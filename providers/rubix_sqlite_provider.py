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

import pandas as pd

from core.egx_session import assess_quote_freshness, egx_session_phase
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
                    FROM quotes WHERE UPPER(ticker)=?
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

    def _snapshot(self):
        self._validate_database()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """SELECT MAX(market_timestamp), MAX(received_at),
                              COUNT(DISTINCT ticker), COUNT(*) FROM quotes"""
                ).fetchone()
                candle = connection.execute(
                    "SELECT MAX(minute), COUNT(*) FROM candles_1m"
                ).fetchone()
                coverage = self._coverage_snapshot(connection)
                collector = self._collector_snapshot(connection)
        except sqlite3.Error as error:
            raise ProviderConnectionError(f"cannot read Rubix SQLite: {error}") from error
        if not row or not row[0] or not row[1] or not row[3]:
            raise ProviderConnectionError("Rubix SQLite contains no quotes")
        received = _utc_timestamp(row[1])
        exchange = _utc_timestamp(row[0])
        age_seconds = max(0.0, (self._utc_now() - received).total_seconds())
        latest_candle = _utc_timestamp(candle[0]) if candle and candle[0] else None
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
            "symbol_count": int(row[2] or 0),
            "quote_count": int(row[3] or 0),
            "minute_bar_count": int(candle[1] or 0) if candle else 0,
        }
        result.update(coverage)
        result.update(collector)
        return result

    def _symbol_snapshot(self, mapped):
        self._validate_database()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """SELECT MAX(market_timestamp), MAX(received_at), COUNT(*)
                       FROM quotes WHERE UPPER(ticker)=?""",
                    (mapped.upper(),),
                ).fetchone()
                candle = connection.execute(
                    "SELECT MAX(minute), COUNT(*) FROM candles_1m WHERE UPPER(ticker)=?",
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

    def _coverage_snapshot(self, connection):
        """Report every omitted/stale symbol instead of silently hiding it."""

        rows = connection.execute(
            "SELECT UPPER(ticker), MAX(received_at) FROM quotes GROUP BY UPPER(ticker)"
        ).fetchall()
        latest = {str(row[0]): _utc_timestamp(row[1]) for row in rows if row[0] and row[1]}
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
            updating = sorted(
                symbol for symbol in received_symbols
                if assess_quote_freshness(
                    latest[symbol], latest[symbol], value=current,
                    open_stale_after_minutes=self.stale_after_minutes,
                ).session_lag == 0
            )
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

    def _collector_snapshot(self, connection):
        """Summarize adapter metrics without reading auth data or network state."""

        events = connection.execute(
            "SELECT observed_at,event,ticker,value,detail FROM feed_metrics ORDER BY id DESC LIMIT 20000"
        ).fetchall()
        event_counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT event,COUNT(*) FROM feed_metrics GROUP BY event"
            ).fetchall()
        }
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
        cutoff = (self._utc_now() - pd.Timedelta(minutes=1)).isoformat()
        updates_last_minute = connection.execute(
            "SELECT COUNT(*) FROM quotes WHERE received_at>=?", (cutoff,)
        ).fetchone()[0]
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
                   FROM candles_1m WHERE UPPER(ticker)=? ORDER BY minute""",
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

    def _validate_database(self):
        if self.db_path is None:
            raise ProviderConfigurationError("RUBIX_DB_PATH is not configured")
        if not self.db_path.is_file():
            raise ProviderConnectionError(f"Rubix SQLite not found: {self.db_path}")
        try:
            with self._connect() as connection:
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
        except sqlite3.Error as error:
            raise ProviderConnectionError(f"cannot validate Rubix SQLite: {error}") from error
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
