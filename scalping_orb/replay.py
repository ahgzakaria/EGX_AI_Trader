"""Deterministic read-only Rubix replay through the Phase 2A pipeline."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from core.universe import lookup
from scalping_orb.bars import CompletedBar
from scalping_orb.config import OrbDataConfig
from scalping_orb.events import NormalizedIntradayEvent, RubixQuoteInput
from scalping_orb.session import OrbSessionClassifier


def _parse_aware(value) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _market_payload_key(row: sqlite3.Row) -> str:
    """Safe identity over verified market fields, excluding replay metadata."""

    payload = {
        "ticker": str(row["ticker"]).strip().upper(),
        "market_timestamp": row["market_timestamp"],
        "sequence": row["sequence"],
        "last_price": row["last_price"],
        "volume": row["volume"],
        "bid": row["bid"],
        "ask": row["ask"],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _market_payload_tuple(row: sqlite3.Row) -> tuple:
    return (
        str(row["ticker"]).strip().upper(),
        row["market_timestamp"],
        row["sequence"],
        row["last_price"],
        row["volume"],
        row["bid"],
        row["ask"],
    )


@dataclass(frozen=True)
class ReplayCategorySelection:
    category: str
    session_date: date
    canonical_ticker: str
    reason: str


@dataclass(frozen=True)
class RubixReplayPlan:
    dense_session_date: date
    dense_tickers: tuple[str, ...]
    full_session_pairs: tuple[tuple[date, str], ...]
    selections: tuple[ReplayCategorySelection, ...]


@dataclass(frozen=True)
class RubixReplayBatch:
    raw_events_by_session: tuple[tuple[date, tuple[RubixQuoteInput, ...]], ...]
    raw_row_count: int
    safe_source_identity: str
    distinct_market_payloads: int
    exact_payload_repeats: int
    same_timestamp_distinct_events: int
    identity_hash_collisions: int


class RubixReadOnlyReplaySource:
    """Read verified quote fields only; never expose or write raw Rubix payloads."""

    def __init__(self, path, config: OrbDataConfig | None = None):
        self.path = Path(path)
        self.config = config or OrbDataConfig()
        self.classifier = OrbSessionClassifier(self.config)
        if not self.path.is_file():
            raise FileNotFoundError(f"Rubix database not found: {self.path}")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(
            f"file:{self.path.resolve().as_posix()}?mode=ro", uri=True, timeout=30
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            yield connection
        finally:
            connection.close()

    def build_plan(self) -> RubixReplayPlan:
        """Select evidence categories with stable data-derived ordering rules."""

        with self.connect() as connection:
            date_rows = connection.execute(
                """SELECT substr(minute,1,10) session_date,count(*) row_count
                   FROM candles_1m GROUP BY 1 ORDER BY row_count DESC,session_date"""
            ).fetchall()
            dates = []
            for row in date_rows:
                try:
                    value = date.fromisoformat(row["session_date"])
                except (TypeError, ValueError):
                    continue
                if self.classifier.classify(
                    self.classifier.window(value).continuous_start_utc
                ).value != "NON_TRADING_DAY":
                    dates.append(value)
            if not dates:
                raise RuntimeError("No regular Rubix candle session is available")
            dense_date = dates[0]
            dense_window = self.classifier.window(dense_date)
            dense_tickers = tuple(
                row[0]
                for row in connection.execute(
                    """SELECT DISTINCT upper(ticker) FROM quotes
                       WHERE market_timestamp>=? AND market_timestamp<? ORDER BY 1""",
                    (
                        dense_window.continuous_start_utc.isoformat(),
                        dense_window.opening_range_end_utc.isoformat(),
                    ),
                )
            )

            selections: list[ReplayCategorySelection] = []
            reset_pairs: set[tuple[date, str]] = set()
            first_exact = first_distinct = first_lag = None
            observed: set[tuple[date, str]] = set()
            for session_date in sorted(dates):
                window = self.classifier.window(session_date)
                cursor = connection.execute(
                    """SELECT id,upper(ticker) ticker,last_price,bid,ask,volume,
                              market_timestamp,received_at,sequence,has_feed_timestamp
                       FROM quotes WHERE market_timestamp>=? AND market_timestamp<?
                       ORDER BY upper(ticker),market_timestamp,id""",
                    (
                        window.continuous_start_utc.isoformat(),
                        window.continuous_end_utc.isoformat(),
                    ),
                )
                last_by_ticker: dict[str, tuple[float | None, datetime, str]] = {}
                for row in cursor:
                    ticker = row["ticker"]
                    market = _parse_aware(row["market_timestamp"])
                    received = _parse_aware(row["received_at"])
                    if market is None:
                        continue
                    observed.add((session_date, ticker))
                    volume = float(row["volume"]) if row["volume"] is not None else None
                    payload = _market_payload_key(row)
                    previous = last_by_ticker.get(ticker)
                    if previous is not None:
                        prior_volume, prior_market, prior_payload = previous
                        if (
                            volume is not None
                            and prior_volume is not None
                            and volume < prior_volume
                        ):
                            reset_pairs.add((session_date, ticker))
                        if first_exact is None and payload == prior_payload:
                            first_exact = (session_date, ticker)
                        if (
                            first_distinct is None
                            and market == prior_market
                            and payload != prior_payload
                        ):
                            first_distinct = (session_date, ticker)
                    if (
                        first_lag is None
                        and received is not None
                        and (received - market).total_seconds()
                        > self.config.maximum_quote_age_seconds
                    ):
                        first_lag = (session_date, ticker)
                    last_by_ticker[ticker] = (volume, market, payload)

            active = sorted(
                pair
                for pair in observed
                if (record := lookup(pair[1])) is not None
                and record.is_active
                and record.has_verified_rubix_mapping
            )
            archived = sorted(
                pair
                for pair in observed
                if (record := lookup(pair[1])) is not None
                and not record.is_active
                and record.has_verified_rubix_mapping
            )
            if active:
                selections.append(
                    ReplayCategorySelection(
                        "ACTIVE_MAPPED", active[0][0], active[0][1],
                        "lexicographically first observed active verified pair",
                    )
                )
            if archived:
                selections.append(
                    ReplayCategorySelection(
                        "ARCHIVED_MAPPED", archived[0][0], archived[0][1],
                        "lexicographically first observed archived verified pair",
                    )
                )
            for category, pair, reason in (
                ("HIGH_RECEIVE_LAG", first_lag,
                 "first market-ordered pair exceeding the configured receive-lag budget"),
                ("SAME_TIMESTAMP_DISTINCT", first_distinct,
                 "first adjacent same-timestamp pair with distinct market identity"),
                ("EXACT_REPLAY_DUPLICATE", first_exact,
                 "first adjacent exact market-payload repeat"),
            ):
                if pair is not None:
                    selections.append(
                        ReplayCategorySelection(category, pair[0], pair[1], reason)
                    )
            for session_date, ticker in sorted(reset_pairs):
                selections.append(
                    ReplayCategorySelection(
                        "CUMULATIVE_VOLUME_DECREASE",
                        session_date,
                        ticker,
                        "every symbol/session containing an ordered cumulative decrease",
                    )
                )
            selections.append(
                ReplayCategorySelection(
                    "DENSE_OPENING_RANGE_SESSION",
                    dense_date,
                    "ALL_OBSERVED",
                    "highest candle-row count; replay all quotes in [10:00,10:15)",
                )
            )
            full_pairs = tuple(
                sorted(
                    {
                        (item.session_date, item.canonical_ticker)
                        for item in selections
                        if item.canonical_ticker != "ALL_OBSERVED"
                    }
                )
            )
            return RubixReplayPlan(
                dense_session_date=dense_date,
                dense_tickers=dense_tickers,
                full_session_pairs=full_pairs,
                selections=tuple(sorted(selections, key=lambda item: (
                    item.category, item.session_date, item.canonical_ticker
                ))),
            )

    def load(self, plan: RubixReplayPlan) -> RubixReplayBatch:
        """Load the plan's rows once, deduplicating only by source row id."""

        rows_by_id: dict[int, sqlite3.Row] = {}
        with self.connect() as connection:
            dense_window = self.classifier.window(plan.dense_session_date)
            for row in connection.execute(
                """SELECT id,upper(ticker) ticker,last_price,bid,ask,volume,
                          market_timestamp,received_at,sequence,has_feed_timestamp
                   FROM quotes WHERE market_timestamp>=? AND market_timestamp<?
                   ORDER BY market_timestamp,upper(ticker),id""",
                (
                    dense_window.continuous_start_utc.isoformat(),
                    dense_window.opening_range_end_utc.isoformat(),
                ),
            ):
                rows_by_id[int(row["id"])] = row
            for session_date, ticker in plan.full_session_pairs:
                window = self.classifier.window(session_date)
                for row in connection.execute(
                    """SELECT id,upper(ticker) ticker,last_price,bid,ask,volume,
                              market_timestamp,received_at,sequence,has_feed_timestamp
                       FROM quotes WHERE upper(ticker)=? AND market_timestamp>=?
                         AND market_timestamp<?
                       ORDER BY market_timestamp,id""",
                    (
                        ticker,
                        window.continuous_start_utc.isoformat(),
                        window.continuous_end_utc.isoformat(),
                    ),
                ):
                    rows_by_id[int(row["id"])] = row

        grouped: dict[date, list[RubixQuoteInput]] = {}
        digest = hashlib.sha256()
        payload_by_hash: dict[str, tuple] = {}
        timestamp_payloads: dict[tuple[str, str], set[str]] = {}
        exact_repeats = same_timestamp_distinct = collisions = 0
        for source_id, row in sorted(
            rows_by_id.items(),
            key=lambda item: (
                str(item[1]["market_timestamp"]), item[1]["ticker"], item[0]
            ),
        ):
            market = _parse_aware(row["market_timestamp"])
            received = _parse_aware(row["received_at"])
            if market is None or received is None:
                continue
            ticker = row["ticker"]
            payload_hash = _market_payload_key(row)
            payload_tuple = _market_payload_tuple(row)
            prior_payload = payload_by_hash.get(payload_hash)
            if prior_payload is not None:
                if prior_payload == payload_tuple:
                    exact_repeats += 1
                else:
                    collisions += 1
            else:
                payload_by_hash[payload_hash] = payload_tuple
            timestamp_key = (ticker, str(row["market_timestamp"]))
            hashes_at_timestamp = timestamp_payloads.setdefault(timestamp_key, set())
            if hashes_at_timestamp and payload_hash not in hashes_at_timestamp:
                same_timestamp_distinct += 1
            hashes_at_timestamp.add(payload_hash)
            record = lookup(ticker)
            rubix_symbol = (
                record.rubix_symbol
                if record is not None and record.has_verified_rubix_mapping
                else f"CASE~{ticker}"
            )
            event = RubixQuoteInput(
                canonical_ticker=ticker,
                verified_rubix_symbol=rubix_symbol,
                market_timestamp=market,
                receive_timestamp=received,
                sequence=row["sequence"],
                last_price=row["last_price"],
                cumulative_volume=row["volume"],
                bid=row["bid"],
                ask=row["ask"],
                has_feed_timestamp=bool(row["has_feed_timestamp"]),
                source_row_id=source_id,
            )
            grouped.setdefault(self.classifier.session_date(market), []).append(event)
            digest.update(payload_hash.encode("ascii"))
            digest.update(b"\n")
        return RubixReplayBatch(
            raw_events_by_session=tuple(
                (session_date, tuple(values))
                for session_date, values in sorted(grouped.items())
            ),
            raw_row_count=sum(len(values) for values in grouped.values()),
            safe_source_identity=digest.hexdigest(),
            distinct_market_payloads=len(payload_by_hash),
            exact_payload_repeats=exact_repeats,
            same_timestamp_distinct_events=same_timestamp_distinct,
            identity_hash_collisions=collisions,
        )

    def arrival_order_metrics(self) -> dict:
        """Measure live arrival-order loss without changing historical ordering."""

        total = out_of_order = delayed = negative_lag = unreliable = 0
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT substr(market_timestamp,1,10) FROM quotes ORDER BY 1"
            ).fetchall()
            for row in rows:
                try:
                    session_date = date.fromisoformat(row[0])
                except (TypeError, ValueError):
                    continue
                window = self.classifier.window(session_date)
                if self.classifier.classify(window.continuous_start_utc).value == "NON_TRADING_DAY":
                    continue
                last_market: dict[str, datetime] = {}
                cursor = connection.execute(
                    """SELECT upper(ticker) ticker,market_timestamp,received_at,
                              has_feed_timestamp FROM quotes
                       WHERE market_timestamp>=? AND market_timestamp<? ORDER BY id""",
                    (
                        window.continuous_start_utc.isoformat(),
                        window.continuous_end_utc.isoformat(),
                    ),
                )
                for quote in cursor:
                    market = _parse_aware(quote["market_timestamp"])
                    received = _parse_aware(quote["received_at"])
                    if market is None:
                        unreliable += 1
                        continue
                    total += 1
                    ticker = quote["ticker"]
                    previous = last_market.get(ticker)
                    if previous is not None and market < previous:
                        out_of_order += 1
                    if previous is None or market > previous:
                        last_market[ticker] = market
                    if not quote["has_feed_timestamp"] or received is None:
                        unreliable += 1
                    elif received < market:
                        negative_lag += 1
                    elif (
                        received - market
                    ).total_seconds() > self.config.maximum_quote_age_seconds:
                        delayed += 1
        return {
            "continuous_rows": total,
            "arrival_order_out_of_order_rows": out_of_order,
            "arrival_order_out_of_order_ratio": out_of_order / total if total else 0.0,
            "receive_delayed_rows": delayed,
            "receive_delayed_ratio": delayed / total if total else 0.0,
            "negative_receive_lag_rows": negative_lag,
            "market_time_unreliable_rows": unreliable,
            "live_out_of_order_policy": "ZERO_TOLERANCE_REJECT_AND_AUDIT",
            "historical_out_of_order_policy": "MARKET_TIME_REORDER_WITH_LATE_CORRECTION_ONLY",
        }

    def candle_rows(
        self, session_date: date, *, opening_range_only: bool = True
    ) -> tuple[sqlite3.Row, ...]:
        window = self.classifier.window(session_date)
        end = (
            window.opening_range_end_utc
            if opening_range_only
            else window.continuous_end_utc
        )
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT upper(ticker) ticker,minute,open,high,low,close,volume,updates
                   FROM candles_1m WHERE minute>=? AND minute<?
                   ORDER BY upper(ticker),minute""",
                (window.continuous_start_utc.isoformat(), end.isoformat()),
            ).fetchall()
        return tuple(rows)


def stable_bar_bytes(bars: Iterable[CompletedBar]) -> bytes:
    payload = [
        {
            "ticker": bar.canonical_ticker,
            "interval": bar.interval_minutes,
            "session_date": bar.session_date.isoformat(),
            "start": bar.bar_start_utc.isoformat(),
            "end": bar.bar_end_utc.isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "updates": bar.update_count,
            "flags": bar.data_quality_flags,
            "completed": bar.completed,
            "phase": bar.session_phase.value,
            "source": bar.source_identity,
        }
        for bar in sorted(
            bars,
            key=lambda value: (
                value.session_date,
                value.canonical_ticker,
                value.interval_minutes,
                value.bar_start_utc,
            ),
        )
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compare_quote_bars_to_candles(
    *,
    quote_bars: Iterable[CompletedBar],
    normalized_events: Iterable[NormalizedIntradayEvent],
    candle_rows: Iterable[sqlite3.Row],
) -> list[dict]:
    """Compare evidence sets without declaring either source authoritative."""

    bars = {
        (bar.canonical_ticker, bar.bar_start_utc): bar
        for bar in quote_bars
        if bar.interval_minutes == 1
    }
    event_times: dict[tuple[str, datetime], list[datetime]] = {}
    for event in normalized_events:
        minute = event.market_timestamp_utc.replace(second=0, microsecond=0)
        event_times.setdefault((event.canonical_ticker, minute), []).append(
            event.market_timestamp_utc
        )
    candles = {
        (row["ticker"], _parse_aware(row["minute"])): row for row in candle_rows
    }
    results = []
    for key in sorted(set(bars) | set(candles), key=lambda item: (item[0], item[1])):
        bar = bars.get(key)
        candle = candles.get(key)
        if bar is None:
            classification = "QUOTE_MINUTE_UNAVAILABLE"
            explanation = "vendor candle exists but no trustworthy quote-derived bar"
        elif candle is None:
            classification = "CANDLE_MINUTE_UNAVAILABLE"
            explanation = "quote-derived bar exists but vendor candle is absent"
        else:
            exact = all(
                float(getattr(bar, field)) == float(candle[field])
                for field in ("open", "high", "low", "close")
            )
            if exact:
                classification = "EXACT_OHLC_MATCH"
                explanation = "all OHLC fields match"
            elif (
                float(bar.high) == float(candle["high"])
                and float(bar.low) == float(candle["low"])
            ) or int(candle["updates"]) != int(bar.update_count):
                classification = "EXPLAINABLE_EVENT_SET_DIFFERENCE"
                explanation = (
                    "same extrema or vendor update count differs after market-payload dedup"
                )
            else:
                classification = "UNEXPLAINED_OHLC_DIFFERENCE"
                explanation = "OHLC differs beyond the evidenced dedup/event-order explanation"
        times = event_times.get(key, [])
        results.append(
            {
                "ticker": key[0],
                "minute_utc": key[1].isoformat() if key[1] else None,
                "classification": classification,
                "explanation": explanation,
                "quote_open": bar.open if bar else None,
                "quote_high": bar.high if bar else None,
                "quote_low": bar.low if bar else None,
                "quote_close": bar.close if bar else None,
                "candle_open": candle["open"] if candle else None,
                "candle_high": candle["high"] if candle else None,
                "candle_low": candle["low"] if candle else None,
                "candle_close": candle["close"] if candle else None,
                "quote_volume_available": bool(bar and bar.volume is not None),
                "candle_volume_available": bool(
                    candle is not None and candle["volume"] is not None
                ),
                "quote_update_count": bar.update_count if bar else None,
                "candle_update_count": candle["updates"] if candle else None,
                "first_market_timestamp_utc": min(times).isoformat() if times else None,
                "last_market_timestamp_utc": max(times).isoformat() if times else None,
                "session_phase": bar.session_phase.value if bar else None,
                "completed": bool(bar and bar.completed),
                "volume_quality": (
                    "VOLUME_AVAILABLE"
                    if bar and bar.volume is not None
                    else "VOLUME_UNAVAILABLE"
                ),
            }
        )
    return results
