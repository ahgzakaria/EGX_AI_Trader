"""Read-only Rubix SQLite provider for live Scanner/Forward data.

The independent Rubix adapter is the sole writer and the sole component that
communicates with Rubix.  This module opens SQLite with ``mode=ro`` and never
authenticates, connects to a network, creates tables, or writes adapter rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import threading
import time

import pandas as pd

from core.egx_session import CAIRO, assess_quote_freshness, egx_session_phase
from providers.base_provider import (
    MarketDataProvider,
    ProviderError,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderDataError,
    ProviderSchemaError,
    normalize_history,
)
from providers.symbol_mapping import to_rubix_symbol

#: Per-ticker reads for the batched overlay load. Each one is answered through
#: ``idx_quotes_ticker_time (ticker, market_timestamp)``; the whole-table
#: ``GROUP BY ticker`` aggregates these replaced could not be, and hung a scan.
_LATEST_QUOTE = (
    "SELECT last_price, bid, ask, volume, market_timestamp, received_at "
    "FROM quotes WHERE ticker=? ORDER BY market_timestamp DESC LIMIT 1"
)
_QUOTE_COUNT = "SELECT COUNT(*) FROM quotes WHERE ticker=?"
_MINUTE_SUMMARY = "SELECT MAX(minute), COUNT(*) FROM candles_1m WHERE ticker=?"

#: The newest row the collector wrote, for either the whole table or one ticker.
#: ``id`` is the primary key and the collector inserts as it receives, so the
#: highest id is the most recent arrival. This is deliberately not
#: ``MAX(received_at)``: that column is TEXT and in no index, so SQLite both
#: scans the whole table for it AND compares it lexically rather than
#: chronologically -- measured on 2026-09-01, the lexical maximum disagreed with
#: the newest actual arrival on 4 of 265 tickers, once by 12.4 hours.
_NEWEST_ROW = ("SELECT market_timestamp, received_at FROM quotes "
               "ORDER BY id DESC LIMIT 1")
_NEWEST_PER_TICKER = "SELECT ticker, MAX(id) FROM quotes GROUP BY ticker"
_ROW_BY_ID = "SELECT market_timestamp, received_at FROM quotes WHERE id=?"
_NEWEST_ID_FOR_TICKER = "SELECT MAX(id) FROM quotes WHERE ticker=?"
#: The newest completed minute, without scanning for it.
#:
#: ``SELECT MAX(minute) FROM candles_1m`` cannot use the adapter's primary key,
#: which is ``(ticker, minute)``: minute is the second column, so SQLite walks
#: all 1,377,726 rows -- 163.7 ms, on the live path, because bar_age_seconds
#: is what decides RUBIX_STALE.
#:
#: The index does order minutes *within* each ticker, so the maximum is the
#: largest of 265 seeks to the end of a range. This walks the tickers through
#: that same index rather than being handed a list, so it depends on no cache
#: and no other table: 0.45 ms, 364x faster, byte-identical on the live
#: database. Every step of its plan is a SEARCH; the old one was a SCAN.
#:
#: The alternative was an index on ``candles_1m(minute)``, which would have
#: meant writing to a schema this provider opens read-only and does not own.
#: Nothing needed adding -- the ordering was already there, and the query was
#: asking in a way that could not reach it.
_NEWEST_CANDLE = """
WITH RECURSIVE tickers(ticker) AS (
    SELECT MIN(ticker) FROM candles_1m
    UNION ALL
    SELECT (SELECT MIN(ticker) FROM candles_1m WHERE ticker > tickers.ticker)
    FROM tickers WHERE tickers.ticker IS NOT NULL
)
SELECT MAX((SELECT MAX(minute) FROM candles_1m WHERE ticker = tickers.ticker))
FROM tickers WHERE ticker IS NOT NULL
"""

#: How long a census may be reused. The census is the expensive half of
#: ``health()`` -- last-seen per ticker plus the row counts -- and it cannot be
#: made cheap: every exact route has to walk the index for all 23.5 million
#: rows, and the best measured 59 seconds against 115 for the original.
#:
#: The supervisor calls ``health()`` every ``--heartbeat-seconds`` (default 2)
#: purely to write a telemetry file, so it never actually reached its sleep: on
#: 2026-09-01 it had burned 8,512 CPU-seconds over 369 minutes, 38% of a core
#: sustained, scanning 7.3 GB of the database the collector was writing to.
#:
#: So the census is read at most once a minute and reused in between. What is
#: NOT cached is the verdict: staleness is recomputed from the cached last-seen
#: times against the current clock on every call, so a symbol still goes stale
#: on time. Only the answer to "when did we last see each ticker" may be up to
#: this many seconds old.
CENSUS_TTL_SECONDS = 60.0

#: A ceiling on how much of the clock the census is allowed to occupy.
#:
#: The TTL above bounds how old an observation may be. It does not bound how
#: often the database is read, and those come apart precisely when it matters:
#: a census that takes longer than its own TTL produces an entry that is
#: already expired when it is stored, so the next call re-reads immediately and
#: the cache stops serving anything at all. Measured: with a 3-second read
#: behind a 2-second TTL, four calls performed four reads.
#:
#: That is the failure loop this cache exists to prevent, arriving through the
#: cache. A slow database makes health() a continuous reader; a continuous
#: reader stops SQLite reusing the WAL; the growing WAL is what made the
#: database slow. Each turn tightens the next.
#:
#: So an entry lives for at least as long as the read that produced it cost,
#: times this factor: the slower the database, the less often it is asked. At
#: four, health() can never spend more than a quarter of the clock reading, and
#: on a healthy database (a 16-second census against a 60-second TTL) the TTL
#: still governs and nothing changes.
CENSUS_DUTY_FACTOR = 4.0

_CENSUS_CACHE = {}
_CENSUS_LOCK = threading.Lock()


def _cached_read(store, key, ttl, read):
    """Serve ``read`` from ``store`` behind a self-limiting TTL.

    Shared by the census and the ticker list because they had the same bug
    written out twice, and the second copy was two seconds from the same
    disengagement the first one already suffered.

    The entry is stamped when the value became available, not when the read
    began. Stamping at the start charges the entry for its own retrieval, which
    is what makes a slow read expire before it is ever stored.
    """

    if ttl > 0:
        with _CENSUS_LOCK:
            entry = store.get(key)
            if entry and (time.monotonic() - entry["at"]) < entry["ttl"]:
                return entry["value"]
    started = time.monotonic()
    value = read()
    finished = time.monotonic()
    with _CENSUS_LOCK:
        store[key] = {
            "at": finished,
            "ttl": max(ttl, (finished - started) * CENSUS_DUTY_FACTOR),
            "value": value,
        }
    return value


def reset_census_cache():
    """Drop every cached census. For tests, and for a forced refresh."""

    with _CENSUS_LOCK:
        _CENSUS_CACHE.clear()
        _TICKER_CACHE.clear()


#: Every ticker the collector has written, cached on the same terms as the
#: census. It backs one thing: the check that the adapter is writing upper-case
#: tickers. That is a contract on the writer, it changes about never, and
#: reading it costs a covering-index scan of the whole table -- 3.3 seconds
#: today and growing.
#:
#: Paid once per overlay load that was fine while only the daily scan called it.
#: The portfolio page then began refreshing every 45 seconds through the same
#: method, which turned a once-a-day scan into a permanent one.
_TICKER_CACHE = {}


class RubixSQLiteProvider(MarketDataProvider):
    """Consume adapter-generated quotes/candles without touching its schema."""

    name = "rubix"
    delayed = False
    delay_minutes = 0
    prefer_only_if_newer = True

    FRESH = "RUBIX_FRESH"
    DELAYED = "RUBIX_DELAYED"
    STALE = "RUBIX_STALE"
    UNAVAILABLE = "RUBIX_UNAVAILABLE"
    SYMBOL_MISSING = "SYMBOL_MISSING"

    REQUIRED_TABLES = {"quotes", "candles_1m", "feed_metrics"}
    REQUIRED_QUOTE_COLUMNS = {
        "ticker", "last_price", "bid", "ask", "volume",
        "market_timestamp", "received_at",
    }
    REQUIRED_CANDLE_COLUMNS = {
        "ticker", "minute", "open", "high", "low", "close", "volume",
    }

    def __init__(
        self,
        db_path=None,
        stale_after_minutes=5,
        bar_stale_after_minutes=2,
        history_loader=None,
        now=None,
        expected_symbols=None,
        census_ttl_seconds=None,
    ):
        configured = db_path or os.getenv("RUBIX_DB_PATH", "")
        self.db_path = Path(configured).expanduser() if configured else None
        # Quote and bar thresholds are operational safeguards only. They never
        # enter indicator or strategy calculations.
        self.stale_after_minutes = max(1 / 60, float(stale_after_minutes))
        self.bar_stale_after_minutes = max(1.0, float(bar_stale_after_minutes))
        self._history_loader = history_loader
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.expected_symbols = tuple(to_rubix_symbol(item) for item in (expected_symbols or ()))
        # Bounded wait for the collector's writer lock. A busy database must degrade to a
        # typed read failure, never to an unbounded stall inside a scan.
        self.read_timeout_ms = int(os.getenv("RUBIX_READ_TIMEOUT_MS", "5000") or 5000)
        # Zero forces every call to re-read; see CENSUS_TTL_SECONDS.
        self.census_ttl_seconds = (CENSUS_TTL_SECONDS if census_ttl_seconds is None
                                   else float(census_ttl_seconds))

    @staticmethod
    def map_symbol(symbol):
        return to_rubix_symbol(symbol)

    def health(self):
        """Return database/schema/freshness state from read-only queries only."""

        base = {
            "provider": self.name,
            "database_path": str(self.db_path) if self.db_path else None,
            "database_status": "NOT_CONFIGURED" if self.db_path is None else "READ_ONLY",
            "delayed": False,
            "delay_minutes": 0,
            # Stable health keys keep CLI/UI failure reporting complete even
            # before the collector has created the production database.
            "collector_status": "UNAVAILABLE",
            "authentication_status": "NOT_CONFIRMED",
            "collector_session": None,
            "latest_exchange_timestamp": None,
            "latest_received_timestamp": None,
            "age_seconds": None,
            "symbols_requested": len(self.expected_symbols),
            "symbols_received": 0,
            "symbols_updating": 0,
            "symbols_stale": 0,
            "symbols_missing": len(self.expected_symbols),
            "updating_symbols": [],
            "stale_symbols": [],
            "missing_symbols": sorted(self.expected_symbols),
            "symbol_coverage_pct": 0.0 if self.expected_symbols else None,
            "updates_per_minute": 0.0,
            "heartbeat_status": "NOT_OBSERVED",
            "reconnect_count": 0,
            "live_scanning_safe": False,
        }
        try:
            snapshot = self._snapshot()
        except (
            ProviderConfigurationError,
            ProviderConnectionError,
            ProviderSchemaError,
        ) as error:
            base.update({
                "status": self.UNAVAILABLE,
                "operational_state": self.UNAVAILABLE,
                "connection_state": self.UNAVAILABLE,
                "schema_valid": False,
                "reason": str(error),
            })
            return base

        freshness = assess_quote_freshness(
            snapshot["latest_received_timestamp"],
            snapshot["latest_exchange_timestamp"],
            value=self._now(),
            open_stale_after_minutes=self.stale_after_minutes,
        )
        state, state_reason = self._state_from_snapshot(snapshot, freshness)
        base.update(snapshot)
        base.update({
            "status": state,
            "operational_state": state,
            "connection_state": state,
            "database_status": "READ_ONLY_OK",
            "schema_valid": True,
            "session_phase": freshness.phase,
            "session_lag": freshness.session_lag,
            "freshness": state.replace("RUBIX_", ""),
            "reason": state_reason,
            "live_scanning_safe": state == self.FRESH and freshness.phase == "OPEN",
        })
        return base

    def load_history(self, symbol, period, interval):
        """Return adapter candles for intraday consumers.

        Daily aggregation remains available for compatibility diagnostics, but
        Swing/Daily routing no longer calls this method.  That route loads its
        immutable Yahoo-backed daily cache and uses :meth:`quote_overlay` only.
        """

        mapped = self.map_symbol(symbol)
        snapshot = self._symbol_snapshot(mapped)
        freshness = assess_quote_freshness(
            snapshot["latest_received_timestamp"],
            snapshot["latest_exchange_timestamp"],
            value=self._now(),
            open_stale_after_minutes=self.stale_after_minutes,
        )
        # Staleness is disclosed, not silently converted into a worse source.
        # ProviderManager still compares this timestamp with Yahoo and uses
        # Rubix only when it is actually newer.
        state, state_reason = self._state_from_snapshot(snapshot, freshness)

        interval_key = str(interval).strip().lower()
        if interval_key in {"1d", "1day", "day"}:
            rubix = self._daily_candles(mapped)
            frame = self._merge_historical_seed(symbol, period, interval, rubix)
        elif interval_key in {"1m", "1min"}:
            frame = self._minute_candles(mapped)
        else:
            raise ProviderDataError(
                f"Rubix SQLite stores 1-minute candles; unsupported interval {interval!r}"
            )

        normalized = normalize_history(frame, symbol, self.name)
        metadata = dict(normalized.attrs.get("market_data", {}))
        metadata.update({
            "mapped_symbol": mapped,
            "status": state,
            "operational_state": state,
            "connection_state": state,
            "freshness": state.replace("RUBIX_", ""),
            "freshness_warning": state_reason,
            "database_status": "READ_ONLY_OK",
            "schema_valid": True,
            "adapter_interface": "sqlite_read_only",
            "source_latest_timestamp": snapshot["latest_exchange_timestamp"],
            "latest_exchange_timestamp": snapshot["latest_exchange_timestamp"],
            "received_timestamp": snapshot["latest_received_timestamp"],
            "session_phase": freshness.phase,
            "session_lag": freshness.session_lag,
            "quote_count": snapshot["quote_count"],
            "age_seconds": snapshot.get("age_seconds"),
            "bar_age_seconds": snapshot.get("bar_age_seconds"),
        })
        if self._history_loader and interval_key in {"1d", "1day", "day"}:
            metadata.update({
                "historical_seed_provider": "yahoo",
                "rubix_overlay_active": True,
            })
        normalized.attrs["market_data"] = metadata
        return normalized

    # -- batched overlay --------------------------------------------------- #

    # The adapter is the sole writer and stores normalized uppercase tickers, so the
    # hot predicates compare the column directly and stay on ``idx_quotes_ticker_time``.
    # Wrapping the column in ``UPPER()`` makes SQLite unable to use that index and turns
    # every lookup into a full scan of the quotes table. Unexpected non-normalized rows
    # are reported as a data-quality warning instead of silently broadening every query.
    QUOTE_TICKER_NOT_NORMALIZED = "RUBIX_TICKER_NOT_NORMALIZED"

    def load_latest_quote_overlays(self, symbols):
        """Return ``{symbol: overlay}`` for a whole universe in one bounded read.

        This is the scan-time replacement for calling :meth:`quote_overlay` once per
        symbol. It opens ONE read-only connection, runs ONE bounded read transaction,
        and performs no write of any kind — no schema change, no index creation, no
        checkpoint, no vacuum, no WAL operation.

        **Every read here goes through ``idx_quotes_ticker_time``.** It once did
        not, and the cost was not a performance complaint. The three aggregates
        it used to run -- ``MAX(market_timestamp)``, ``MAX(received_at)`` and
        ``COUNT(*)``, each ``GROUP BY ticker`` -- scanned the whole table, and
        this table now holds 22 million rows across 7.3 GB. On 2026-08-31 a
        Daily Dashboard scan sat in ``PREPARING_RUBIX`` at 0/241 for a quarter
        of an hour before it was killed.

        ``received_at`` is the column that cannot be rescued. It is not in any
        index, so even narrowed to a single ticker ``MAX(received_at)`` measured
        **8.7 seconds per symbol** -- 35 minutes for a 241-name universe --
        because it must fetch that column from every one of the ticker's rows.
        Measured, not assumed.

        So the receipt time is taken **from the newest row by exchange time**
        rather than as an independent maximum, which is the one substantive
        difference from :meth:`quote_overlay` and is the same trade-off
        ``holdings/quotes.py`` documents. It can only err toward calling a quote
        *stale*: if a row had arrived later out of order, this makes the feed
        look older than it is, never fresher. A quote wrongly called stale
        withholds a recommendation; the opposite error would act on a price that
        no longer exists.

        Everything else is unchanged and exact -- the counts are real counts,
        and every judgement is still made by this provider's own composers.

        A symbol with no quote yields a typed missing overlay so one absent
        ticker can never fail the universe.
        """
        requested = [str(symbol) for symbol in symbols or ()]
        mapped_by_symbol = {symbol: self.map_symbol(symbol) for symbol in requested}
        wanted = sorted({mapped.upper() for mapped in mapped_by_symbol.values()})
        if not wanted:
            return {}

        self._validate_path()
        connection = self._connect()
        try:
            connection.execute(f"PRAGMA busy_timeout={int(self.read_timeout_ms)}")
            self._validate_schema(connection)
            # One bounded read transaction: every aggregate and every point lookup below
            # observes the same consistent snapshot of the collector's database.
            connection.execute("BEGIN")
            try:
                all_tickers = self._known_tickers(connection)
                quote_times, received, candles, latest = {}, {}, {}, {}
                for ticker in wanted:
                    row = connection.execute(_LATEST_QUOTE, (ticker,)).fetchone()
                    if row is None:
                        continue
                    latest[ticker] = row
                    # Both taken from the row the index just found: the exchange
                    # time is its own, and the receipt time is that row's rather
                    # than a separate maximum. See the docstring.
                    quote_times[ticker] = row[4]
                    quote_count = connection.execute(
                        _QUOTE_COUNT, (ticker,)).fetchone()[0]
                    received[ticker] = (row[5], quote_count)
                    candles[ticker] = (
                        connection.execute(_MINUTE_SUMMARY, (ticker,)).fetchone()
                        or (None, 0))
            finally:
                connection.rollback()          # read-only: never leave a write intent
        except sqlite3.Error as error:
            raise ProviderConnectionError(
                f"cannot batch-read Rubix quotes: {error}") from error
        finally:
            connection.close()

        # Defensive contract check — surfaced, never worked around silently.
        # Read from every ticker the collector wrote, not only the ones asked
        # for: the requested tickers were upper-cased on the way in, so checking
        # those would be checking our own normalization rather than the
        # collector's.
        unnormalized = sorted(
            key for key in all_tickers if key != key.upper())[:5]

        overlays = {}
        for symbol, mapped in mapped_by_symbol.items():
            ticker = mapped.upper()
            row = latest.get(ticker)
            exchange_time = quote_times.get(ticker)
            received_time, quote_count = received.get(ticker, (None, 0))
            candle_time, bar_count = candles.get(ticker, (None, 0))
            if row is None or not exchange_time or not received_time or not quote_count:
                overlays[symbol] = self._missing_overlay(
                    mapped, f"{self.SYMBOL_MISSING}: Rubix SQLite has no quote for {mapped}")
                continue
            snapshot = self._snapshot_from_batch(
                exchange_time, received_time, quote_count, candle_time, bar_count)
            overlays[symbol] = self._overlay_from_row(mapped, row, snapshot)
            if unnormalized:
                overlays[symbol]["data_quality_warning"] = (
                    f"{self.QUOTE_TICKER_NOT_NORMALIZED}: {','.join(unnormalized)}")
        return overlays

    def _snapshot_from_batch(self, exchange_time, received_time, quote_count,
                             candle_time, bar_count):
        """Build the exact snapshot shape ``_symbol_snapshot`` returns, from batch rows."""
        received_ts = _utc_timestamp(received_time)
        latest_candle = _utc_timestamp(candle_time) if candle_time else None
        return {
            "latest_exchange_timestamp": _utc_timestamp(exchange_time).isoformat(),
            "latest_received_timestamp": received_ts.isoformat(),
            "quote_count": int(quote_count or 0),
            "latest_candle_timestamp": (
                latest_candle.isoformat() if latest_candle is not None else None),
            "minute_bar_count": int(bar_count or 0),
            "age_seconds": max(0.0, (self._utc_now() - received_ts).total_seconds()),
            "bar_age_seconds": (
                max(0.0, (self._utc_now() - latest_candle).total_seconds())
                if latest_candle is not None else None),
        }

    def _missing_overlay(self, mapped, reason):
        """A typed absent-quote result. One missing symbol never fails the universe."""
        return {
            "provider": self.name,
            "mapped_symbol": mapped,
            "available": False,
            "freshness": "UNAVAILABLE",
            "operational_state": self.UNAVAILABLE,
            "freshness_warning": reason,
            "session_phase": egx_session_phase(),
            "quote_count": 0,
            "minute_bars_available": False,
            "minute_bar_count": 0,
            "latest_minute_bar": None,
        }

    def _overlay_from_row(self, mapped, row, snapshot):
        """Compose one overlay from a latest-quote row and its snapshot."""
        freshness = assess_quote_freshness(
            snapshot["latest_received_timestamp"],
            snapshot["latest_exchange_timestamp"],
            value=self._now(),
            open_stale_after_minutes=self.stale_after_minutes,
        )
        state, reason = self._state_from_snapshot(snapshot, freshness)
        bid = _optional_number(row[1])
        ask = _optional_number(row[2])
        spread = None
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            spread = (ask - bid) / bid * 100
        return {
            "provider": self.name,
            "mapped_symbol": mapped,
            "available": True,
            "last": _optional_number(row[0]),
            "bid": bid,
            "ask": ask,
            "volume": _optional_number(row[3]),
            "quote_timestamp": _utc_timestamp(row[4]).isoformat(),
            "received_timestamp": _utc_timestamp(row[5]).isoformat(),
            "spread_percent": spread,
            "freshness": state.replace("RUBIX_", ""),
            "operational_state": state,
            "freshness_warning": reason,
            "session_phase": freshness.phase,
            "session_lag": freshness.session_lag,
            "age_seconds": snapshot.get("age_seconds"),
            "quote_count": snapshot.get("quote_count", 0),
            "minute_bars_available": bool(snapshot.get("latest_candle_timestamp")),
            "minute_bar_count": snapshot.get("minute_bar_count", 0),
            "latest_minute_bar": snapshot.get("latest_candle_timestamp"),
        }

    def quote_overlay(self, symbol):
        """Read the latest quote as metadata; never manufacture a daily candle."""

        mapped = self.map_symbol(symbol)
        snapshot = self._symbol_snapshot(mapped)
        freshness = assess_quote_freshness(
            snapshot["latest_received_timestamp"],
            snapshot["latest_exchange_timestamp"],
            value=self._now(),
            open_stale_after_minutes=self.stale_after_minutes,
        )
        state, reason = self._state_from_snapshot(snapshot, freshness)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT last_price, bid, ask, volume, market_timestamp, received_at
                    FROM quotes WHERE ticker=?
                    ORDER BY received_at DESC LIMIT 1
                    """,
                    (mapped.upper(),),
                ).fetchone()
        except sqlite3.Error as error:
            raise ProviderConnectionError(
                f"cannot read latest Rubix quote for {mapped}: {error}"
            ) from error
        if row is None:
            raise ProviderDataError(
                f"{self.SYMBOL_MISSING}: Rubix SQLite has no quote for {mapped}"
            )
        bid = _optional_number(row[1])
        ask = _optional_number(row[2])
        spread = None
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            spread = (ask - bid) / bid * 100
        return {
            "provider": self.name,
            "mapped_symbol": mapped,
            "available": True,
            "last": _optional_number(row[0]),
            "bid": bid,
            "ask": ask,
            "volume": _optional_number(row[3]),
            "quote_timestamp": _utc_timestamp(row[4]).isoformat(),
            "received_timestamp": _utc_timestamp(row[5]).isoformat(),
            "spread_percent": spread,
            "freshness": state.replace("RUBIX_", ""),
            "operational_state": state,
            "freshness_warning": reason,
            "session_phase": freshness.phase,
            "session_lag": freshness.session_lag,
            "age_seconds": snapshot.get("age_seconds"),
            "quote_count": snapshot.get("quote_count", 0),
            "minute_bars_available": bool(snapshot.get("latest_candle_timestamp")),
            "minute_bar_count": snapshot.get("minute_bar_count", 0),
            "latest_minute_bar": snapshot.get("latest_candle_timestamp"),
        }

    def symbol_availability(self, symbol):
        """Return quote/minute availability for the coverage audit."""

        try:
            overlay = self.quote_overlay(symbol)
            return {
                "rubix_quote_available": True,
                "rubix_minute_bars_available": bool(
                    overlay.get("minute_bars_available")
                ),
                "rubix_quote_count": int(overlay.get("quote_count") or 0),
                "rubix_minute_bar_count": int(
                    overlay.get("minute_bar_count") or 0
                ),
                "rubix_quote_timestamp": overlay.get("quote_timestamp"),
                "rubix_status": overlay.get("operational_state"),
                "rubix_error": None,
            }
        except ProviderError as error:
            return {
                "rubix_quote_available": False,
                "rubix_minute_bars_available": False,
                "rubix_quote_count": 0,
                "rubix_minute_bar_count": 0,
                "rubix_quote_timestamp": None,
                "rubix_status": self.UNAVAILABLE,
                "rubix_error": str(error),
            }

    def _known_tickers(self, connection):
        """Every ticker the collector has written, behind the census TTL.

        `GROUP BY ticker` selecting only `ticker` is answered from the covering
        index in about three seconds; `SELECT DISTINCT ticker` takes
        twenty-five, for the same 265 rows. Neither is cheap enough to run on
        every call once a page refreshes every 45 seconds through this method.

        What it feeds is a contract check on the adapter -- are the tickers it
        writes upper-case -- which is not a per-call question.
        """

        return _cached_read(
            _TICKER_CACHE, str(self.db_path), self.census_ttl_seconds,
            lambda: [str(row[0]) for row in connection.execute(
                "SELECT ticker FROM quotes GROUP BY ticker") if row[0]],
        )

    def _read_census(self, connection):
        """The expensive half: last-seen per ticker, and the row counts.

        Every read here is exact. ``MAX(id) GROUP BY ticker`` is answered from
        the covering index and gives the most recently written row for each
        ticker, which is what "when did we last see this symbol" means; the
        rowid lookups that follow are O(1). Checked against the old lexical
        ``MAX(received_at)`` on the live database: identical for every ticker,
        including the four it had been getting wrong.
        """

        newest_ids = {
            str(ticker): int(rowid)
            for ticker, rowid in connection.execute(_NEWEST_PER_TICKER)
            if ticker and rowid is not None
        }
        last_seen = {}
        for ticker, rowid in newest_ids.items():
            row = connection.execute(_ROW_BY_ID, (rowid,)).fetchone()
            if row and row[1]:
                last_seen[ticker] = _utc_timestamp(row[1])
        # feed_metrics is the larger table of the two -- 55.7 million rows on
        # 2026-09-01 against the quotes table's 23.5 -- and this histogram over
        # it measured 150 seconds, the single largest cost in health(). It
        # feeds three lifetime counters that nothing branches on.
        event_counts = {
            str(event): int(count)
            for event, count in connection.execute(
                "SELECT event,COUNT(*) FROM feed_metrics GROUP BY event")
        }
        # Also an unindexed scan of quotes, and also a rate rather than a fact:
        # a figure describing the last minute does not become wrong by being a
        # minute old.
        cutoff = (self._utc_now() - pd.Timedelta(minutes=1)).isoformat()
        updates_last_minute = connection.execute(
            "SELECT COUNT(*) FROM quotes WHERE received_at>=?", (cutoff,)
        ).fetchone()[0]
        return {
            "last_seen": last_seen,
            "symbol_count": len(newest_ids),
            "quote_count": int(connection.execute(
                "SELECT COUNT(*) FROM quotes").fetchone()[0] or 0),
            # A displayed total, not an input to any verdict. Counting the
            # candles costs 74 ms, which is worth caching now that finding the
            # newest one costs 0.45.
            "minute_bar_count": int(connection.execute(
                "SELECT COUNT(*) FROM candles_1m").fetchone()[0] or 0),
            "event_counts": event_counts,
            "updates_last_minute": int(updates_last_minute or 0),
        }

    def _census(self, connection):
        """``_read_census`` behind a TTL. See ``CENSUS_TTL_SECONDS``."""

        return _cached_read(_CENSUS_CACHE, str(self.db_path), self.census_ttl_seconds,
                            lambda: self._read_census(connection))

    def _snapshot(self):
        self._validate_database()
        try:
            with self._connect() as connection:
                # The tip is always read fresh: it is one row through the
                # primary key, and it is what "is the feed alive right now"
                # depends on. Only the census below is allowed to be a minute
                # old.
                tip = connection.execute(_NEWEST_ROW).fetchone()
                # So is the newest completed minute, and for the same reason:
                # bar_age_seconds is the only cached number _state_from_snapshot
                # branches on, so leaving it in the census meant a database slow
                # enough to need the cache would age its own candle past
                # bar_stale_after_minutes and report RUBIX_STALE -- turning off
                # live scanning as a side effect of protecting the WAL. It reads
                # in under a millisecond; there was never a reason to pay
                # for it with the verdict.
                candle = connection.execute(_NEWEST_CANDLE).fetchone()
                census = self._census(connection)
                coverage = self._coverage_snapshot(census["last_seen"])
                collector = self._collector_snapshot(connection, census)
        except sqlite3.Error as error:
            raise ProviderConnectionError(f"cannot read Rubix SQLite: {error}") from error
        if not tip or not tip[0] or not tip[1] or not census["quote_count"]:
            raise ProviderConnectionError("Rubix SQLite contains no quotes")
        received = _utc_timestamp(tip[1])
        exchange = _utc_timestamp(tip[0])
        age_seconds = max(0.0, (self._utc_now() - received).total_seconds())
        latest_candle = (_utc_timestamp(candle[0])
                         if candle and candle[0] else None)
        bar_age_seconds = (
            max(0.0, (self._utc_now() - latest_candle).total_seconds())
            if latest_candle is not None else None
        )
        result = {
            "latest_exchange_timestamp": exchange.isoformat(),
            "latest_received_timestamp": received.isoformat(),
            "latest_candle_timestamp": latest_candle.isoformat() if latest_candle is not None else None,
            "age_seconds": age_seconds,
            "age_minutes": age_seconds / 60,
            "bar_age_seconds": bar_age_seconds,
            "symbol_count": census["symbol_count"],
            "quote_count": census["quote_count"],
            "minute_bar_count": census["minute_bar_count"],
        }
        result.update(coverage)
        result.update(collector)
        return result

    def _symbol_snapshot(self, mapped):
        self._validate_database()
        try:
            with self._connect() as connection:
                # Was MAX(market_timestamp), MAX(received_at), COUNT(*) in one
                # query. The two maxima cost 8.7 seconds per symbol between
                # them, because received_at is in no index and the filter only
                # narrows the scan to that ticker's rows. The newest row by id
                # is the same answer through the covering index, and the count
                # is a separate, cheap, index-only read.
                newest = connection.execute(
                    _NEWEST_ID_FOR_TICKER, (mapped.upper(),)).fetchone()
                row = None
                if newest and newest[0] is not None:
                    stamps = connection.execute(_ROW_BY_ID, (newest[0],)).fetchone()
                    count = connection.execute(
                        _QUOTE_COUNT, (mapped.upper(),)).fetchone()
                    if stamps:
                        row = (stamps[0], stamps[1], count[0] if count else 0)
                candle = connection.execute(
                    "SELECT MAX(minute), COUNT(*) FROM candles_1m WHERE ticker=?",
                    (mapped.upper(),),
                ).fetchone()
        except sqlite3.Error as error:
            raise ProviderConnectionError(f"cannot read Rubix symbol {mapped}: {error}") from error
        if not row or not row[0] or not row[1] or not row[2]:
            raise ProviderDataError(f"{self.SYMBOL_MISSING}: Rubix SQLite has no data for {mapped}")
        received = _utc_timestamp(row[1])
        latest_candle = _utc_timestamp(candle[0]) if candle and candle[0] else None
        return {
            "latest_exchange_timestamp": _utc_timestamp(row[0]).isoformat(),
            "latest_received_timestamp": received.isoformat(),
            "quote_count": int(row[2]),
            "latest_candle_timestamp": latest_candle.isoformat() if latest_candle is not None else None,
            "minute_bar_count": int(candle[1] or 0) if candle else 0,
            "age_seconds": max(0.0, (self._utc_now() - received).total_seconds()),
            "bar_age_seconds": (
                max(0.0, (self._utc_now() - latest_candle).total_seconds())
                if latest_candle is not None else None
            ),
        }

    def _state_from_snapshot(self, snapshot, freshness):
        """Combine quote receipt and completed-minute freshness evidence."""

        if not freshness.usable:
            return self.STALE, freshness.reason
        if freshness.phase == "OPEN":
            bar_age = snapshot.get("bar_age_seconds")
            if bar_age is None:
                return self.STALE, "Rubix has quotes but no generated 1-minute candle"
            if bar_age > self.bar_stale_after_minutes * 60:
                return self.STALE, f"latest 1-minute bar is {bar_age / 60:.1f} minutes old"
            exchange_age = max(
                0.0,
                (self._utc_now() - _utc_timestamp(snapshot["latest_exchange_timestamp"])).total_seconds(),
            )
            if exchange_age > self.stale_after_minutes * 60:
                return self.DELAYED, f"collector is receiving data but exchange timestamp is {exchange_age:.0f}s old"
        return self.FRESH, freshness.reason

    def _coverage_snapshot(self, latest):
        """Report every omitted/stale symbol instead of silently hiding it.

        ``latest`` is the census's last-seen map, which may be up to
        ``CENSUS_TTL_SECONDS`` old. The classification below is not: it is
        recomputed against the current clock on every call, so a symbol that
        stops updating still crosses into ``stale`` on time. What ages is only
        the observation, never the verdict drawn from it.
        """

        expected = tuple(dict.fromkeys(self.expected_symbols))
        universe = expected or tuple(sorted(latest))
        received_symbols = sorted(symbol for symbol in universe if symbol in latest)
        current = self._utc_now()
        if egx_session_phase(current) == "OPEN":
            updating = sorted(
                symbol for symbol in received_symbols
                if (current - latest[symbol]).total_seconds() <= self.stale_after_minutes * 60
            )
            stale = sorted(symbol for symbol in expected if symbol in latest and symbol not in updating)
        else:
            # Once per distinct Cairo date, not once per symbol.
            #
            # session_lag is trading_session_lag(the timestamp's Cairo date,
            # current), and both halves are fixed inside one call -- so two
            # symbols last seen on the same day cannot get different answers.
            # The live database holds 265 symbols across 3 such dates, and this
            # was calling assess_quote_freshness 265 times to learn 3 things.
            #
            # It cost 1,065 us a call, or 282 ms: 512 us re-resolving the
            # holiday calendar to recompute a session phase identical for every
            # symbol, and 546 us on the lag itself. Pure CPU, no database, and
            # it only appears outside session hours -- where this supervisor
            # spends most of its day.
            lag_by_date = {}

            def _current_session(stamp):
                key = stamp.astimezone(CAIRO).date()
                if key not in lag_by_date:
                    lag_by_date[key] = assess_quote_freshness(
                        stamp, stamp, value=current,
                        open_stale_after_minutes=self.stale_after_minutes,
                    ).session_lag == 0
                return lag_by_date[key]

            updating = sorted(symbol for symbol in received_symbols
                              if _current_session(latest[symbol]))
            stale = sorted(symbol for symbol in expected if symbol in latest and symbol not in updating)
        missing = sorted(symbol for symbol in expected if symbol not in latest)
        requested_count = len(expected)
        return {
            "symbols_requested": requested_count,
            "symbols_received": len(received_symbols),
            "symbols_updating": len(updating),
            "symbols_stale": len(stale),
            "symbols_missing": len(missing),
            "updating_symbols": updating,
            "stale_symbols": stale,
            "missing_symbols": missing,
            "symbol_coverage_pct": round(len(received_symbols) / requested_count * 100, 2) if requested_count else None,
        }

    def _collector_snapshot(self, connection, census):
        """Summarize adapter metrics without reading auth data or network state.

        The recent-events read below is bounded and costs 0.03 seconds, so it
        stays live: connection state, authentication and heartbeat are what
        "is the collector working right now" means. The lifetime counters and
        the update rate come from the census, which may be a minute old.
        """

        events = connection.execute(
            "SELECT observed_at,event,ticker,value,detail FROM feed_metrics ORDER BY id DESC LIMIT 20000"
        ).fetchall()
        event_counts = census["event_counts"]
        latest_by_event = {}
        for row in events:
            latest_by_event.setdefault(str(row[1]), row)
        connected_candidates = [
            row for name in ("connected", "reconnect_success")
            if (row := latest_by_event.get(name)) is not None
        ]
        connected = max(
            connected_candidates, key=lambda row: _utc_timestamp(row[0])
        ) if connected_candidates else None
        disconnected = latest_by_event.get("disconnect")
        connected_at = _utc_timestamp(connected[0]) if connected else None
        disconnected_at = _utc_timestamp(disconnected[0]) if disconnected else None
        collector_status = "CONNECTED" if connected_at and (
            disconnected_at is None or connected_at > disconnected_at
        ) else "DISCONNECTED"
        updates_last_minute = census["updates_last_minute"]
        heartbeat = latest_by_event.get("heartbeat_received") or latest_by_event.get("heartbeat_sent")
        heartbeat_stamp = _utc_timestamp(heartbeat[0]) if heartbeat else None
        heartbeat_age = (
            max(0.0, (self._utc_now() - heartbeat_stamp).total_seconds())
            if heartbeat_stamp is not None else None
        )
        heartbeat_at = heartbeat_stamp.isoformat() if heartbeat_stamp is not None else None
        rejected = [
            {"ticker": row[2], "reason": row[4]}
            for row in events if row[1] == "subscription_rejected"
        ][:100]
        return {
            "collector_status": collector_status,
            "authentication_status": (
                "ACKNOWLEDGED" if "authentication_acknowledged" in latest_by_event else "NOT_CONFIRMED"
            ),
            "collector_session": connected_at.isoformat() if connected_at else None,
            "last_heartbeat": heartbeat_at,
            "heartbeat_status": (
                "HEALTHY" if heartbeat_age is not None and heartbeat_age <= 60
                else "STALE" if heartbeat_age is not None else "NOT_OBSERVED"
            ),
            "heartbeat_age_seconds": heartbeat_age,
            "reconnect_count": event_counts.get("reconnect_success", 0),
            "disconnect_count": event_counts.get("disconnect", 0),
            "updates_last_minute": int(updates_last_minute or 0),
            "updates_per_minute": float(updates_last_minute or 0),
            "subscription_rejections": rejected,
            "rejected_subscriptions": event_counts.get("subscription_rejected", 0),
            "subscription_batches_sent": event_counts.get("subscription_batch_sent", 0),
        }

    def _minute_candles(self, mapped):
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT minute AS Date, open AS Open, high AS High,
                          low AS Low, close AS Close, volume AS Volume
                   FROM candles_1m WHERE ticker=? ORDER BY minute""",
                (mapped.upper(),),
            ).fetchall()
        if not rows:
            raise ProviderDataError(f"Rubix SQLite has no candles for {mapped}")
        return pd.DataFrame([dict(row) for row in rows])

    def _daily_candles(self, mapped):
        minute = normalize_history(self._minute_candles(mapped), mapped, self.name)
        grouped = minute.groupby(minute.index.date, sort=True)
        daily = grouped.agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        })
        daily.index = pd.DatetimeIndex(pd.to_datetime(daily.index), name="Date")
        return daily

    def _merge_historical_seed(self, symbol, period, interval, rubix):
        if self._history_loader is None:
            return rubix
        seed = normalize_history(
            self._history_loader(symbol, period, interval), symbol, "yahoo"
        )
        overlay = normalize_history(rubix, symbol, self.name)
        if "Adj Close" in seed.columns and "Adj Close" not in overlay.columns:
            overlay["Adj Close"] = overlay["Close"]
        combined = pd.concat([seed, overlay])
        return combined[~combined.index.duplicated(keep="last")].sort_index()

    def _validate_path(self):
        if self.db_path is None:
            raise ProviderConfigurationError("RUBIX_DB_PATH is not configured")
        if not self.db_path.is_file():
            raise ProviderConnectionError(f"Rubix SQLite not found: {self.db_path}")

    def _validate_database(self):
        self._validate_path()
        try:
            with self._connect() as connection:
                self._validate_schema(connection)
        except sqlite3.Error as error:
            raise ProviderConnectionError(f"cannot validate Rubix SQLite: {error}") from error

    def _validate_schema(self, connection):
        """Schema check on an EXISTING connection, so a batch read opens only one."""
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing_tables = self.REQUIRED_TABLES - tables
        if missing_tables:
            raise ProviderSchemaError(
                "Rubix SQLite missing tables: " + ", ".join(sorted(missing_tables))
            )
        quote_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(quotes)")
        }
        candle_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(candles_1m)")
        }
        missing_quotes = self.REQUIRED_QUOTE_COLUMNS - quote_columns
        missing_candles = self.REQUIRED_CANDLE_COLUMNS - candle_columns
        if missing_quotes or missing_candles:
            missing = sorted(missing_quotes | missing_candles)
            raise ProviderSchemaError("Rubix SQLite missing columns: " + ", ".join(missing))

    def _connect(self):
        try:
            uri = f"file:{self.db_path.resolve().as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            return connection
        except (OSError, sqlite3.Error) as error:
            raise ProviderConnectionError(f"cannot open Rubix SQLite read-only: {error}") from error

    def _utc_now(self):
        current = self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)


def _utc_timestamp(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp


def _optional_number(value):
    """Normalize nullable SQLite numeric values without inventing zeroes."""

    if value is None or pd.isna(value):
        return None
    return float(value)
