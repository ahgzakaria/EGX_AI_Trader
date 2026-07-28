"""Read-only Rubix live readiness for immutable historical candidates.

This module is deliberately separate from historical selection and execution.
It reads only the displayed members of a READY frozen watchlist, opens the
collector database read-only, caps all market inputs at the 14:15 Cairo
continuous-session boundary, and returns immutable research-only assessments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
import math
import os
from pathlib import Path
import sqlite3
import time as clock
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

from core.egx_calendar import is_official_holiday
from core.egx_session import TRADING_WEEKDAYS


CAIRO = ZoneInfo("Africa/Cairo")
UTC = timezone.utc
CONTINUOUS_OPEN = time(10, 0)
CONTINUOUS_CLOSE = time(14, 15)
AUCTION_CLOSE = time(14, 25)

LIVE_METRIC_VERSION = "LIVE_ENTRY_READINESS_V1"
LIVE_CONFIG_VERSION = "LIVE_ENTRY_READINESS_CONFIG_V1"

WATCHLIST_NOT_READY = "WATCHLIST_NOT_READY"
HISTORICAL_CANDIDATE_WAITING = "HISTORICAL_CANDIDATE_WAITING"
PRE_OPEN_WAIT = "PRE_OPEN_WAIT"
OPENING_RANGE_FORMING = "OPENING_RANGE_FORMING"
ENTRY_TRIGGER_FORMING = "ENTRY_TRIGGER_FORMING"
ENTRY_READY_RESEARCH_ONLY = "ENTRY_READY_RESEARCH_ONLY"
BREAKOUT_UNCONFIRMED = "BREAKOUT_UNCONFIRMED"
PULLBACK_CONFIRMATION_REQUIRED = "PULLBACK_CONFIRMATION_REQUIRED"
MOVE_EXTENDED_DO_NOT_CHASE = "MOVE_EXTENDED_DO_NOT_CHASE"
TARGET_OUTSIDE_TYPICAL_ZONE = "TARGET_OUTSIDE_TYPICAL_ZONE"
SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
LIQUIDITY_INSUFFICIENT = "LIQUIDITY_INSUFFICIENT"
LIVE_DATA_STALE = "LIVE_DATA_STALE"
LIVE_DATA_UNAVAILABLE = "LIVE_DATA_UNAVAILABLE"
OPPORTUNITY_INVALIDATED = "OPPORTUNITY_INVALIDATED"
CLOSING_AUCTION_NO_NEW_ENTRY = "CLOSING_AUCTION_NO_NEW_ENTRY"
SESSION_CLOSED = "SESSION_CLOSED"

VWAP_AVAILABLE = "VWAP_DERIVED_FROM_MINUTE_OHLCV"
VWAP_UNAVAILABLE = "VWAP_UNAVAILABLE"
OPENING_RANGE_COMPLETE = "OPENING_RANGE_COMPLETE"
OPENING_RANGE_INCOMPLETE = "OPENING_RANGE_INCOMPLETE"
DATA_VALID = "AVAILABLE_AND_VALIDATED"
DATA_PARTIAL = "AVAILABLE_BUT_SPARSE"
DATA_UNAVAILABLE = "UNAVAILABLE"
DATA_STALE = "STALE"
DATA_NOT_QUERIED = "NOT_QUERIED_SESSION_PHASE"


@dataclass(frozen=True)
class LiveReadinessConfig:
    """Centralized, typed research thresholds and explainable score weights."""

    metric_version: str = LIVE_METRIC_VERSION
    config_version: str = LIVE_CONFIG_VERSION
    opening_range_minutes: int = 15
    minimum_opening_bars: int = 8
    minimum_structure_bars: int = 12
    maximum_open_delay_minutes: int = 2
    quote_stale_seconds: float = 120.0
    maximum_spread_percent: float = 0.60
    minimum_cumulative_volume: float = 100_000.0
    take_profit_percent: float = 2.0
    stop_loss_percent: float = 2.0
    estimated_fees_percent: float = 0.10
    breakout_buffer_percent: float = 0.05
    breakout_confirmation_bars: int = 2
    retest_tolerance_percent: float = 0.20
    trigger_near_percent: float = 0.35
    invalidation_below_opening_range_percent: float = 0.50
    severe_upper_excursion_consumption: float = 0.90
    severe_range_consumption: float = 1.00
    maximum_vwap_extension_percent: float = 1.50
    target_zone_tolerance: float = 1.10
    readiness_threshold: float = 70.0
    structure_weight: float = 35.0
    vwap_weight: float = 15.0
    remaining_movement_weight: float = 25.0
    liquidity_weight: float = 15.0
    freshness_weight: float = 10.0
    refresh_seconds: int = 15
    read_busy_timeout_ms: int = 2_000

    def __post_init__(self):
        total = (
            self.structure_weight
            + self.vwap_weight
            + self.remaining_movement_weight
            + self.liquidity_weight
            + self.freshness_weight
        )
        if not math.isclose(total, 100.0, abs_tol=1e-9):
            raise ValueError("live readiness weights must total 100%")
        if self.opening_range_minutes <= 0:
            raise ValueError("opening range must be positive")
        if self.minimum_opening_bars > self.opening_range_minutes:
            raise ValueError("minimum opening bars exceed opening window")
        if self.take_profit_percent <= 0 or self.stop_loss_percent <= 0:
            raise ValueError("target and stop percentages must be positive")


@dataclass(frozen=True)
class HardGateResult:
    name: str
    passed: bool | None
    detail: str


@dataclass(frozen=True)
class RubixQuote:
    symbol: str
    last_price: float | None
    bid: float | None
    ask: float | None
    cumulative_volume: float | None
    change_percent: float | None
    market_timestamp: datetime | None
    received_at: datetime | None


@dataclass(frozen=True)
class RubixMinuteBar:
    symbol: str
    minute: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    updates: int


@dataclass(frozen=True)
class RubixSymbolSnapshot:
    symbol: str
    quote: RubixQuote | None
    continuous_bars: tuple[RubixMinuteBar, ...]


@dataclass(frozen=True)
class RubixBatchSnapshot:
    target_session_date: str
    symbols: tuple[str, ...]
    by_symbol: Mapping[str, RubixSymbolSnapshot]
    data_cutoff: str | None
    connection_count: int
    query_count: int
    query_latency_ms: float
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class LiveEntryReadinessResult:
    # Identity.
    watchlist_id: str
    target_session_date: str
    symbol: str
    historical_rank: int
    evaluated_at: str
    rubix_data_cutoff: str | None
    live_metric_version: str
    live_config_version: str
    # Historical references copied from the immutable member.
    historical_score: float
    median_daily_range: float
    normal_range_lower: float
    normal_range_upper: float
    median_upper_excursion: float
    median_lower_excursion: float
    range_stability: float
    zone_consistency: float
    liquidity_score: float
    # Live inputs.
    current_price: float | None
    previous_close: float | None
    session_open: float | None
    continuous_high: float | None
    continuous_low: float | None
    continuous_range_percent: float | None
    change_from_open_percent: float | None
    session_vwap: float | None
    distance_from_vwap_percent: float | None
    opening_range_high: float | None
    opening_range_low: float | None
    opening_range_width_percent: float | None
    current_spread_percent: float | None
    quote_age_seconds: float | None
    cumulative_volume: float | None
    cumulative_traded_value: float | None
    trade_count: int | None
    session_phase: str
    # Outputs.
    readiness_score: float | None
    live_state: str
    hard_gates: tuple[HardGateResult, ...]
    trigger_results: tuple[str, ...]
    historical_range_consumption: float | None
    indicative_remaining_movement: str
    no_chase_reason: str | None
    entry_zone_low: float | None
    entry_zone_high: float | None
    target_price: float | None
    stop_price: float | None
    gross_reward_percent: float | None
    gross_risk_percent: float | None
    spread_cost_allowance_percent: float | None
    expected_net_reward_percent: float | None
    historical_zone_feasible: bool | None
    invalidation_condition: str
    explanations: tuple[str, ...]
    data_quality_status: str
    opening_range_state: str
    vwap_state: str


@dataclass(frozen=True)
class LiveReadinessBatchResult:
    status: str
    watchlist_id: str | None
    target_session_date: str
    results: tuple[LiveEntryReadinessResult, ...] = ()
    symbols_requested: tuple[str, ...] = ()
    rubix_data_cutoff: str | None = None
    connection_count: int = 0
    query_count: int = 0
    query_latency_ms: float = 0.0
    evaluation_latency_ms: float = 0.0
    detail: str | None = None


def live_session_phase(
    evaluated_at: datetime,
    target_session_date: date | str,
    *,
    holidays: Iterable[date] | None = None,
) -> str:
    """Return the strict Phase 3B session phase in Cairo time."""

    current = _aware(evaluated_at).astimezone(CAIRO)
    target = _as_date(target_session_date)
    holiday_set = set(holidays or ())
    if current.date() != target:
        return PRE_OPEN_WAIT if current.date() < target else SESSION_CLOSED
    if (
        target.weekday() not in TRADING_WEEKDAYS
        or target in holiday_set
        or (holidays is None and is_official_holiday(target))
    ):
        return SESSION_CLOSED
    local = current.timetz().replace(tzinfo=None)
    if local < CONTINUOUS_OPEN:
        return PRE_OPEN_WAIT
    if local < CONTINUOUS_CLOSE:
        return "CONTINUOUS_TRADING"
    if local < AUCTION_CLOSE:
        return CLOSING_AUCTION_NO_NEW_ENTRY
    return SESSION_CLOSED


class RubixLiveBatchReader:
    """Two indexed queries through one read-only SQLite transaction."""

    REQUIRED_QUOTES = {
        "ticker",
        "last_price",
        "bid",
        "ask",
        "volume",
        "market_timestamp",
        "received_at",
        "change_percent",
    }
    REQUIRED_CANDLES = {
        "ticker",
        "minute",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "updates",
    }

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        busy_timeout_ms: int = 2_000,
    ):
        configured = db_path or os.getenv(
            "RUBIX_DB_PATH", "data/rubix_live_market.db"
        )
        self.db_path = Path(configured)
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))

    def load(
        self,
        symbols: Iterable[str],
        *,
        target_session_date: date | str,
        evaluated_at: datetime,
    ) -> RubixBatchSnapshot:
        requested = tuple(dict.fromkeys(_symbol(item) for item in symbols))
        target = _as_date(target_session_date)
        if not requested:
            return RubixBatchSnapshot(
                target.isoformat(), (), {}, None, 0, 0, 0.0
            )
        started = clock.perf_counter()
        try:
            connection = self._connect()
        except (OSError, sqlite3.Error) as error:
            return self._error_snapshot(
                target, requested, started, "RUBIX_DATABASE_UNAVAILABLE", error
            )
        query_count = 0
        try:
            self._validate_schema(connection)
            session_open, continuous_cutoff, receive_cutoff = _query_boundaries(
                target, evaluated_at
            )
            placeholders = ",".join("?" for _ in requested)
            connection.execute("BEGIN")
            quote_rows = connection.execute(
                f"""
                WITH ranked AS (
                  SELECT ticker,last_price,bid,ask,volume,change_percent,
                         market_timestamp,received_at,
                         ROW_NUMBER() OVER (
                           PARTITION BY ticker
                           ORDER BY market_timestamp DESC,id DESC
                         ) AS row_number
                  FROM quotes
                  WHERE ticker IN ({placeholders})
                    AND market_timestamp>=?
                    AND market_timestamp<?
                    AND received_at<=?
                )
                SELECT ticker,last_price,bid,ask,volume,change_percent,
                       market_timestamp,received_at
                FROM ranked WHERE row_number=1
                """,
                (
                    *requested,
                    _iso(session_open),
                    _iso(continuous_cutoff),
                    _iso(receive_cutoff),
                ),
            ).fetchall()
            query_count += 1
            candle_rows = connection.execute(
                f"""
                SELECT ticker,minute,open,high,low,close,volume,updates
                FROM candles_1m
                WHERE ticker IN ({placeholders})
                  AND minute>=? AND minute<?
                ORDER BY ticker,minute
                """,
                (*requested, _iso(session_open), _iso(continuous_cutoff)),
            ).fetchall()
            query_count += 1
            connection.rollback()
        except sqlite3.Error as error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            return self._error_snapshot(
                target,
                requested,
                started,
                (
                    "RUBIX_DATABASE_BUSY"
                    if "locked" in str(error).lower()
                    else "RUBIX_DATABASE_UNAVAILABLE"
                ),
                error,
                query_count=query_count,
            )
        finally:
            connection.close()

        quotes = {
            str(row["ticker"]): _quote_from_row(row)
            for row in quote_rows
        }
        bars: dict[str, list[RubixMinuteBar]] = {
            symbol: [] for symbol in requested
        }
        cutoffs: list[datetime] = []
        for row in candle_rows:
            bar = _bar_from_row(row)
            if bar is not None:
                bars.setdefault(bar.symbol, []).append(bar)
                cutoffs.append(bar.minute)
        for quote in quotes.values():
            if quote.market_timestamp is not None:
                cutoffs.append(quote.market_timestamp)
        by_symbol = {
            symbol: RubixSymbolSnapshot(
                symbol,
                quotes.get(symbol),
                tuple(bars.get(symbol, ())),
            )
            for symbol in requested
        }
        return RubixBatchSnapshot(
            target.isoformat(),
            requested,
            by_symbol,
            _iso(max(cutoffs)) if cutoffs else None,
            1,
            query_count,
            round((clock.perf_counter() - started) * 1_000, 3),
        )

    def _connect(self):
        if not self.db_path.is_file():
            raise OSError("Rubix SQLite database is unavailable")
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return connection

    def _validate_schema(self, connection):
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"quotes", "candles_1m"}.issubset(tables):
            raise sqlite3.OperationalError("Rubix schema is incomplete")
        quote_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(quotes)")
        }
        candle_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(candles_1m)")
        }
        if not self.REQUIRED_QUOTES.issubset(quote_columns):
            raise sqlite3.OperationalError("Rubix quote schema is incomplete")
        if not self.REQUIRED_CANDLES.issubset(candle_columns):
            raise sqlite3.OperationalError("Rubix candle schema is incomplete")

    @staticmethod
    def _error_snapshot(
        target,
        requested,
        started,
        code,
        error,
        *,
        query_count=0,
    ):
        detail = " ".join(str(error).replace("\n", " ").split())[:240]
        return RubixBatchSnapshot(
            target.isoformat(),
            requested,
            {},
            None,
            1,
            query_count,
            round((clock.perf_counter() - started) * 1_000, 3),
            code,
            detail,
        )


class LiveEntryReadinessEngine:
    """Deterministic long-only readiness over displayed frozen candidates."""

    def __init__(
        self,
        reader: RubixLiveBatchReader,
        *,
        config: LiveReadinessConfig | None = None,
    ):
        self.reader = reader
        self.config = config or LiveReadinessConfig()

    def evaluate(
        self,
        record,
        *,
        evaluated_at: datetime | None = None,
        live_snapshot: RubixBatchSnapshot | None = None,
    ) -> LiveReadinessBatchResult:
        started = clock.perf_counter()
        now = _aware(evaluated_at or datetime.now(UTC))
        if (
            record is None
            or record.header.get("status") != "READY"
            or not record.displayed
        ):
            return LiveReadinessBatchResult(
                WATCHLIST_NOT_READY,
                None,
                now.astimezone(CAIRO).date().isoformat(),
                evaluation_latency_ms=_elapsed_ms(started),
                detail=(
                    "No READY frozen historical watchlist; full-market and "
                    "legacy fallbacks are disabled"
                ),
            )
        header = record.header
        target = _as_date(header["target_session_date"])
        phase = live_session_phase(now, target)
        displayed = tuple(
            sorted(
                record.displayed,
                key=lambda row: (int(row["historical_rank"]), row["symbol"]),
            )
        )
        symbols = tuple(_symbol(row["symbol"]) for row in displayed)
        if phase == PRE_OPEN_WAIT:
            results = tuple(
                self._phase_only_result(header, member, now, PRE_OPEN_WAIT)
                for member in displayed
            )
            return LiveReadinessBatchResult(
                PRE_OPEN_WAIT,
                header["watchlist_id"],
                target.isoformat(),
                results,
                symbols,
                evaluation_latency_ms=_elapsed_ms(started),
            )
        snapshot = live_snapshot or self.reader.load(
            symbols,
            target_session_date=target,
            evaluated_at=now,
        )
        if snapshot.error_code:
            results = tuple(
                self._unavailable_result(
                    header,
                    member,
                    now,
                    phase,
                    snapshot.error_detail or snapshot.error_code,
                )
                for member in displayed
            )
        else:
            results = tuple(
                self._evaluate_member(
                    header,
                    member,
                    snapshot.by_symbol.get(_symbol(member["symbol"])),
                    now,
                    phase,
                    snapshot.data_cutoff,
                )
                for member in displayed
            )
        return LiveReadinessBatchResult(
            "LIVE_READINESS_EVALUATED",
            header["watchlist_id"],
            target.isoformat(),
            results,
            symbols,
            snapshot.data_cutoff,
            snapshot.connection_count,
            snapshot.query_count,
            snapshot.query_latency_ms,
            _elapsed_ms(started),
            snapshot.error_detail,
        )

    def _evaluate_member(
        self,
        header,
        member,
        live,
        now,
        phase,
        data_cutoff,
    ):
        if phase in {
            CLOSING_AUCTION_NO_NEW_ENTRY,
            SESSION_CLOSED,
        }:
            result = self._assess_inputs(
                header, member, live, now, phase, data_cutoff
            )
            return _replace_result(
                result,
                live_state=phase,
                readiness_score=None,
                explanations=result.explanations
                + (
                    (
                        "Closing auction: no new scalping entry; continuous "
                        "high/low/range are frozen at 14:15 Cairo"
                    )
                    if phase == CLOSING_AUCTION_NO_NEW_ENTRY
                    else "Session closed: no new entry"
                ,),
            )
        return self._assess_inputs(
            header, member, live, now, phase, data_cutoff
        )

    def _assess_inputs(
        self,
        header,
        member,
        live,
        now,
        phase,
        data_cutoff,
    ):
        cfg = self.config
        base = _base_result(header, member, now, phase, data_cutoff, cfg)
        if live is None or live.quote is None:
            return _replace_result(
                base,
                live_state=LIVE_DATA_UNAVAILABLE,
                data_quality_status=DATA_UNAVAILABLE,
                explanations=base.explanations
                + ("Rubix quote is unavailable for this frozen candidate",),
                hard_gates=(
                    HardGateResult("rubix_quote", False, "quote unavailable"),
                ),
            )
        quote = live.quote
        bars = tuple(
            bar for bar in live.continuous_bars if _valid_bar(bar)
        )
        current = _positive(quote.last_price)
        bid = _positive(quote.bid)
        ask = _positive(quote.ask)
        crossed_quote = (
            bid is not None and ask is not None and ask < bid
        )
        spread = (
            (ask - bid) / bid * 100
            if bid is not None and ask is not None and ask >= bid
            else None
        )
        quote_age = (
            max(0.0, (now.astimezone(UTC) - quote.market_timestamp).total_seconds())
            if quote.market_timestamp is not None
            else None
        )
        session_open = bars[0].open if bars else None
        high = max((bar.high for bar in bars), default=None)
        low = min((bar.low for bar in bars), default=None)
        range_pct = (
            (high - low) / session_open * 100
            if session_open and high is not None and low is not None
            else None
        )
        change_open = (
            (current - session_open) / session_open * 100
            if current and session_open
            else None
        )
        previous_close = _previous_close(current, quote.change_percent)
        opening_end = _session_moment(
            _as_date(header["target_session_date"]),
            time(
                CONTINUOUS_OPEN.hour,
                CONTINUOUS_OPEN.minute + cfg.opening_range_minutes,
            ),
        )
        opening_bars = tuple(bar for bar in bars if bar.minute < opening_end)
        opening_high = max((bar.high for bar in opening_bars), default=None)
        opening_low = min((bar.low for bar in opening_bars), default=None)
        opening_width = (
            (opening_high - opening_low) / session_open * 100
            if session_open and opening_high is not None and opening_low is not None
            else None
        )
        vwap = _minute_vwap(bars)
        vwap_distance = (
            (current - vwap) / vwap * 100 if current and vwap else None
        )
        consumption = (
            range_pct / float(member["median_daily_range"])
            if range_pct is not None and member["median_daily_range"] > 0
            else None
        )
        remaining = _remaining_label(consumption)
        gates = [
            HardGateResult("ready_watchlist", True, header["watchlist_id"]),
            HardGateResult("displayed_candidate", True, "frozen Top-N member"),
            HardGateResult(
                "target_session",
                header["target_session_date"] == now.astimezone(CAIRO).date().isoformat(),
                header["target_session_date"],
            ),
            HardGateResult(
                "continuous_session",
                phase == "CONTINUOUS_TRADING",
                phase,
            ),
            HardGateResult("rubix_quote", current is not None, "positive last price"),
            HardGateResult(
                "quote_freshness",
                quote_age is not None and quote_age <= cfg.quote_stale_seconds,
                f"age={quote_age:.1f}s" if quote_age is not None else "timestamp unavailable",
            ),
            HardGateResult(
                "session_open",
                _valid_session_open(bars, cfg),
                "first observed positive bar must begin within two minutes of 10:00",
            ),
            HardGateResult(
                "continuous_high_low",
                high is not None and low is not None and high >= low > 0,
                "continuous candles only",
            ),
            HardGateResult(
                "minute_structure",
                len(bars) >= cfg.minimum_structure_bars
                and len(opening_bars) >= cfg.minimum_opening_bars,
                f"{len(bars)} total / {len(opening_bars)} opening bars",
            ),
            HardGateResult(
                "spread",
                (
                    False
                    if crossed_quote
                    else (
                        None
                        if spread is None
                        else spread <= cfg.maximum_spread_percent
                    )
                ),
                (
                    "crossed or inverted bid/ask"
                    if crossed_quote
                    else (
                    "bid/ask unavailable"
                    if spread is None
                    else f"{spread:.3f}% <= {cfg.maximum_spread_percent:.3f}%"
                    )
                ),
            ),
            HardGateResult(
                "liquidity",
                quote.cumulative_volume is not None
                and quote.cumulative_volume >= cfg.minimum_cumulative_volume,
                f"cumulative volume={quote.cumulative_volume}",
            ),
        ]
        base = _replace_result(
            base,
            current_price=current,
            previous_close=previous_close,
            session_open=session_open,
            continuous_high=high,
            continuous_low=low,
            continuous_range_percent=range_pct,
            change_from_open_percent=change_open,
            session_vwap=vwap,
            distance_from_vwap_percent=vwap_distance,
            opening_range_high=opening_high,
            opening_range_low=opening_low,
            opening_range_width_percent=opening_width,
            current_spread_percent=spread,
            quote_age_seconds=quote_age,
            cumulative_volume=quote.cumulative_volume,
            historical_range_consumption=consumption,
            indicative_remaining_movement=remaining,
            hard_gates=tuple(gates),
            opening_range_state=(
                OPENING_RANGE_COMPLETE
                if now >= opening_end
                and len(opening_bars) >= cfg.minimum_opening_bars
                else OPENING_RANGE_INCOMPLETE
            ),
            vwap_state=VWAP_AVAILABLE if vwap is not None else VWAP_UNAVAILABLE,
            data_quality_status=(
                DATA_VALID
                if len(bars) >= 200
                else DATA_PARTIAL
            ),
        )
        if phase != "CONTINUOUS_TRADING":
            return base
        if now < opening_end:
            return _replace_result(
                base,
                live_state=OPENING_RANGE_FORMING,
                readiness_score=None,
                explanations=base.explanations
                + (
                    f"Opening range is forming for {cfg.opening_range_minutes} minutes",
                ),
            )
        if current is None:
            return _blocked(base, LIVE_DATA_UNAVAILABLE, "Current price is unavailable")
        if quote_age is None:
            return _blocked(base, LIVE_DATA_UNAVAILABLE, "Market timestamp is unavailable")
        if quote_age > cfg.quote_stale_seconds:
            return _replace_result(
                _blocked(
                    base,
                    LIVE_DATA_STALE,
                    f"Rubix quote is {quote_age:.1f} seconds old",
                ),
                data_quality_status=DATA_STALE,
            )
        if not _valid_session_open(bars, cfg):
            return _blocked(
                base,
                LIVE_DATA_UNAVAILABLE,
                "A trustworthy 10:00 session-open bar is unavailable",
            )
        if (
            len(bars) < cfg.minimum_structure_bars
            or len(opening_bars) < cfg.minimum_opening_bars
        ):
            return _blocked(
                base,
                LIVE_DATA_UNAVAILABLE,
                "Minute structure is too sparse for an opening-range claim",
            )
        if crossed_quote:
            return _blocked(
                base,
                LIVE_DATA_UNAVAILABLE,
                "Rubix bid/ask is crossed or inverted and cannot support a spread gate",
            )
        if spread is not None and spread > cfg.maximum_spread_percent:
            return _blocked(
                base,
                SPREAD_TOO_WIDE,
                f"Spread {spread:.3f}% exceeds {cfg.maximum_spread_percent:.3f}%",
            )
        if (
            quote.cumulative_volume is None
            or quote.cumulative_volume < cfg.minimum_cumulative_volume
        ):
            return _blocked(
                base,
                LIQUIDITY_INSUFFICIENT,
                "Cumulative volume is below the research executability gate",
            )
        invalidation = (
            opening_low is not None
            and current
            < opening_low
            * (1 - cfg.invalidation_below_opening_range_percent / 100)
        )
        if invalidation:
            return _replace_result(
                _blocked(
                    base,
                    OPPORTUNITY_INVALIDATED,
                    "Price broke materially below the opening-range low",
                ),
                invalidation_condition=(
                    f"Long thesis invalid below {opening_low:.4f} "
                    f"minus {cfg.invalidation_below_opening_range_percent:.2f}%"
                ),
            )
        upper_consumption = (
            max(0.0, change_open) / float(member["median_upper_excursion"])
            if change_open is not None and member["median_upper_excursion"] > 0
            else 0.0
        )
        no_chase = _no_chase_reasons(
            base,
            upper_consumption=upper_consumption,
            config=cfg,
        )
        if no_chase:
            return _replace_result(
                _blocked(
                    base,
                    MOVE_EXTENDED_DO_NOT_CHASE,
                    no_chase[0],
                ),
                no_chase_reason="; ".join(no_chase),
            )
        target = current * (1 + cfg.take_profit_percent / 100)
        stop = current * (1 - cfg.stop_loss_percent / 100)
        typical_upper_price = session_open * (
            1
            + float(member["median_upper_excursion"])
            * cfg.target_zone_tolerance
            / 100
        )
        target_feasible = target <= typical_upper_price
        if not target_feasible:
            return _replace_result(
                _blocked(
                    base,
                    TARGET_OUTSIDE_TYPICAL_ZONE,
                    "Projected +2% target lies outside the typical historical upper zone",
                ),
                target_price=target,
                stop_price=stop,
                gross_reward_percent=cfg.take_profit_percent,
                gross_risk_percent=cfg.stop_loss_percent,
                spread_cost_allowance_percent=(
                    (spread or 0.0) + cfg.estimated_fees_percent
                ),
                expected_net_reward_percent=(
                    cfg.take_profit_percent
                    - (spread or 0.0)
                    - cfg.estimated_fees_percent
                ),
                historical_zone_feasible=False,
            )
        triggers = _opening_range_triggers(
            bars,
            opening_bars,
            opening_high,
            current,
            cfg,
        )
        score = _readiness_score(
            base,
            triggers=triggers,
            cumulative_volume=quote.cumulative_volume,
            config=cfg,
        )
        state = _trigger_state(
            triggers,
            current=current,
            opening_high=opening_high,
            score=score,
            config=cfg,
        )
        costs = (spread or 0.0) + cfg.estimated_fees_percent
        ready = state == ENTRY_READY_RESEARCH_ONLY
        return _replace_result(
            base,
            readiness_score=score,
            live_state=state,
            trigger_results=triggers,
            entry_zone_low=current if ready else None,
            entry_zone_high=(ask or current) if ready else None,
            target_price=target if ready else None,
            stop_price=stop if ready else None,
            gross_reward_percent=cfg.take_profit_percent if ready else None,
            gross_risk_percent=cfg.stop_loss_percent if ready else None,
            spread_cost_allowance_percent=costs if ready else None,
            expected_net_reward_percent=(
                cfg.take_profit_percent - costs if ready else None
            ),
            historical_zone_feasible=True,
            invalidation_condition=(
                f"Invalid below opening-range low {opening_low:.4f}"
                if opening_low is not None
                else "Opening-range invalidation unavailable"
            ),
            explanations=base.explanations
            + _assessment_explanations(
                base, triggers, state, score, costs
            ),
        )

    def _phase_only_result(self, header, member, now, state):
        base = _base_result(
            header, member, now, state, None, self.config
        )
        return _replace_result(
            base,
            live_state=state,
            data_quality_status=DATA_NOT_QUERIED,
            explanations=base.explanations
            + ("Waiting for the continuous trading session",),
        )

    def _unavailable_result(self, header, member, now, phase, detail):
        base = _base_result(
            header, member, now, phase, None, self.config
        )
        state = (
            phase
            if phase in {CLOSING_AUCTION_NO_NEW_ENTRY, SESSION_CLOSED}
            else LIVE_DATA_UNAVAILABLE
        )
        return _replace_result(
            base,
            live_state=state,
            data_quality_status=DATA_UNAVAILABLE,
            explanations=base.explanations + (detail,),
        )


def _base_result(header, member, now, phase, data_cutoff, cfg):
    return LiveEntryReadinessResult(
        watchlist_id=header["watchlist_id"],
        target_session_date=header["target_session_date"],
        symbol=_symbol(member["symbol"]),
        historical_rank=int(member["historical_rank"]),
        evaluated_at=_iso(now),
        rubix_data_cutoff=data_cutoff,
        live_metric_version=cfg.metric_version,
        live_config_version=cfg.config_version,
        historical_score=float(member["historical_score"]),
        median_daily_range=float(member["median_daily_range"]),
        normal_range_lower=float(member["normal_range_lower"]),
        normal_range_upper=float(member["normal_range_upper"]),
        median_upper_excursion=float(member["median_upper_excursion"]),
        median_lower_excursion=float(member["median_lower_excursion"]),
        range_stability=float(member["range_stability_score"]),
        zone_consistency=float(member["combined_zone_consistency_score"]),
        liquidity_score=float(member["liquidity_score"]),
        current_price=None,
        previous_close=None,
        session_open=None,
        continuous_high=None,
        continuous_low=None,
        continuous_range_percent=None,
        change_from_open_percent=None,
        session_vwap=None,
        distance_from_vwap_percent=None,
        opening_range_high=None,
        opening_range_low=None,
        opening_range_width_percent=None,
        current_spread_percent=None,
        quote_age_seconds=None,
        cumulative_volume=None,
        cumulative_traded_value=None,
        trade_count=None,
        session_phase=phase,
        readiness_score=None,
        live_state=HISTORICAL_CANDIDATE_WAITING,
        hard_gates=(),
        trigger_results=(),
        historical_range_consumption=None,
        indicative_remaining_movement=(
            "Historical range consumption unavailable"
        ),
        no_chase_reason=None,
        entry_zone_low=None,
        entry_zone_high=None,
        target_price=None,
        stop_price=None,
        gross_reward_percent=None,
        gross_risk_percent=None,
        spread_cost_allowance_percent=None,
        expected_net_reward_percent=None,
        historical_zone_feasible=None,
        invalidation_condition=(
            "Long-only: invalidate on a confirmed break of the opening-range low"
        ),
        explanations=(
            f"Historical rank {int(member['historical_rank'])} is frozen",
            (
                f"Historical median full-session range "
                f"{float(member['median_daily_range']):.2f}%"
            ),
            (
                "Historical range consumption is an indicative proxy; "
                "EODHD Daily may include closing-auction effects"
            ),
        ),
        data_quality_status=DATA_UNAVAILABLE,
        opening_range_state=OPENING_RANGE_INCOMPLETE,
        vwap_state=VWAP_UNAVAILABLE,
    )


def _replace_result(result, **changes):
    values = {
        name: getattr(result, name)
        for name in result.__dataclass_fields__
    }
    values.update(changes)
    return LiveEntryReadinessResult(**values)


def _blocked(base, state, reason):
    return _replace_result(
        base,
        readiness_score=None,
        live_state=state,
        explanations=base.explanations + (reason,),
    )


def _no_chase_reasons(base, *, upper_consumption, config):
    reasons = []
    if upper_consumption >= config.severe_upper_excursion_consumption:
        reasons.append(
            "Move from open has consumed at least 90% of the typical upper excursion"
        )
    if (
        base.historical_range_consumption is not None
        and base.historical_range_consumption >= config.severe_range_consumption
    ):
        reasons.append(
            "Continuous range is at or beyond the historical median full-session range"
        )
    if (
        base.distance_from_vwap_percent is not None
        and base.distance_from_vwap_percent
        > config.maximum_vwap_extension_percent
    ):
        reasons.append("Price is excessively extended above session VWAP")
    return tuple(reasons)


def _opening_range_triggers(
    bars, opening_bars, opening_high, current, config
):
    if not opening_bars or opening_high is None:
        return ()
    later = tuple(bar for bar in bars if bar.minute >= opening_bars[-1].minute)
    threshold = opening_high * (1 + config.breakout_buffer_percent / 100)
    breakout_indexes = [
        index for index, bar in enumerate(later) if bar.close > threshold
    ]
    if not breakout_indexes:
        return ("NO_CANDLE_BREAKOUT",)
    first = breakout_indexes[0]
    confirmations = sum(
        later[index].close > threshold
        for index in range(first, min(len(later), first + 3))
    )
    confirmed = confirmations >= config.breakout_confirmation_bars
    retest = any(
        bar.low
        <= opening_high * (1 + config.retest_tolerance_percent / 100)
        and bar.close
        >= opening_high * (1 - config.retest_tolerance_percent / 100)
        for bar in later[first + 1 :]
    )
    return (
        f"BREAKOUT_AT_{_iso(later[first].minute)}",
        "BREAKOUT_CONFIRMED" if confirmed else "BREAKOUT_UNCONFIRMED",
        "RETEST_CONFIRMED" if retest else "RETEST_NOT_OBSERVED",
    )


def _trigger_state(
    triggers, *, current, opening_high, score, config
):
    if not triggers or triggers == ("NO_CANDLE_BREAKOUT",):
        if (
            current is not None
            and opening_high is not None
            and abs(current - opening_high) / opening_high * 100
            <= config.trigger_near_percent
        ):
            return ENTRY_TRIGGER_FORMING
        return HISTORICAL_CANDIDATE_WAITING
    if "BREAKOUT_UNCONFIRMED" in triggers:
        return BREAKOUT_UNCONFIRMED
    if "RETEST_NOT_OBSERVED" in triggers:
        return PULLBACK_CONFIRMATION_REQUIRED
    if score >= config.readiness_threshold:
        return ENTRY_READY_RESEARCH_ONLY
    return ENTRY_TRIGGER_FORMING


def _readiness_score(
    base, *, triggers, cumulative_volume, config
):
    if "RETEST_CONFIRMED" in triggers:
        structure = 100.0
    elif "BREAKOUT_CONFIRMED" in triggers:
        structure = 70.0
    elif "BREAKOUT_UNCONFIRMED" in triggers:
        structure = 45.0
    else:
        structure = 25.0
    components = [
        (structure, config.structure_weight),
        (
            _remaining_component(base.historical_range_consumption),
            config.remaining_movement_weight,
        ),
        (
            min(
                100.0,
                50.0
                + 50.0
                * max(0.0, float(cumulative_volume or 0))
                / max(1.0, config.minimum_cumulative_volume * 5),
            ),
            config.liquidity_weight,
        ),
        (
            max(
                0.0,
                100.0
                * (
                    1
                    - float(base.quote_age_seconds or 0)
                    / config.quote_stale_seconds
                ),
            ),
            config.freshness_weight,
        ),
    ]
    if base.session_vwap is not None:
        distance = float(base.distance_from_vwap_percent or 0)
        vwap_score = (
            100.0
            if 0 <= distance <= 0.75
            else max(0.0, 80.0 - abs(distance) * 20)
        )
        components.append((vwap_score, config.vwap_weight))
    weight = sum(item[1] for item in components)
    return round(sum(score * part for score, part in components) / weight, 2)


def _remaining_component(consumption):
    if consumption is None:
        return 50.0
    return max(0.0, min(100.0, (1.0 - consumption) * 100))


def _assessment_explanations(base, triggers, state, score, costs):
    items = [
        (
            f"Current continuous range {base.continuous_range_percent:.2f}%"
            if base.continuous_range_percent is not None
            else "Continuous range unavailable"
        ),
        (
            f"Historical full-session range consumption "
            f"{base.historical_range_consumption:.1%}"
            if base.historical_range_consumption is not None
            else "Historical range consumption unavailable"
        ),
        f"Opening-range structure: {', '.join(triggers) if triggers else 'unavailable'}",
        (
            f"VWAP {base.session_vwap:.4f}; distance "
            f"{base.distance_from_vwap_percent:+.2f}%"
            if base.session_vwap is not None
            else "VWAP unavailable; its score weight was reallocated"
        ),
        f"Estimated spread/fee allowance {costs:.3f}%",
        f"Live readiness {score:.2f}/100; state {state}",
    ]
    return tuple(items)


def _remaining_label(consumption):
    if consumption is None:
        return "INDICATIVE_REMAINING_MOVEMENT_UNAVAILABLE"
    if consumption >= 1:
        return "HISTORICAL_FULL_SESSION_RANGE_CONSUMED"
    if consumption >= 0.75:
        return "INDICATIVE_MOVEMENT_LIMITED"
    return "MOVEMENT_CAPACITY_NOT_YET_EXHAUSTED"


def _minute_vwap(bars):
    valid = [
        bar for bar in bars
        if bar.volume > 0 and _valid_bar(bar)
    ]
    total = sum(bar.volume for bar in valid)
    if total <= 0:
        return None
    value = sum(
        ((bar.high + bar.low + bar.close) / 3) * bar.volume
        for bar in valid
    )
    return value / total


def _valid_session_open(bars, config):
    if not bars:
        return False
    local = bars[0].minute.astimezone(CAIRO)
    delay = (local.hour * 60 + local.minute) - (
        CONTINUOUS_OPEN.hour * 60 + CONTINUOUS_OPEN.minute
    )
    return 0 <= delay <= config.maximum_open_delay_minutes and bars[0].open > 0


def _query_boundaries(target, evaluated_at):
    session_open = _session_moment(target, CONTINUOUS_OPEN)
    continuous_close = _session_moment(target, CONTINUOUS_CLOSE)
    evaluated = _aware(evaluated_at).astimezone(UTC)
    # Only completed minute candles and never a 14:15 auction minute.
    minute_floor = evaluated.replace(second=0, microsecond=0)
    continuous_cutoff = min(minute_floor, continuous_close)
    if continuous_cutoff <= session_open:
        continuous_cutoff = session_open
    return session_open, continuous_cutoff, evaluated


def _session_moment(day, local_time):
    return datetime.combine(day, local_time, tzinfo=CAIRO).astimezone(UTC)


def _quote_from_row(row):
    symbol = _symbol(row["ticker"])
    return RubixQuote(
        symbol,
        _positive(row["last_price"]),
        _positive(row["bid"]),
        _positive(row["ask"]),
        _nonnegative(row["volume"]),
        _number(row["change_percent"]),
        _timestamp(row["market_timestamp"]),
        _timestamp(row["received_at"]),
    )


def _bar_from_row(row):
    values = tuple(_number(row[name]) for name in ("open", "high", "low", "close"))
    if any(value is None or value <= 0 for value in values):
        return None
    open_, high, low, close = values
    if high < max(open_, close, low) or low > min(open_, close, high):
        return None
    return RubixMinuteBar(
        _symbol(row["ticker"]),
        _timestamp(row["minute"]),
        open_,
        high,
        low,
        close,
        _nonnegative(row["volume"]) or 0.0,
        int(row["updates"] or 0),
    )


def _valid_bar(bar):
    return (
        bar.open > 0
        and bar.high >= max(bar.open, bar.close, bar.low)
        and bar.low <= min(bar.open, bar.close, bar.high)
        and bar.volume >= 0
    )


def _previous_close(current, change_percent):
    if current is None or change_percent is None or change_percent <= -100:
        return None
    return current / (1 + change_percent / 100)


def _positive(value):
    number = _number(value)
    return number if number is not None and number > 0 else None


def _nonnegative(value):
    number = _number(value)
    return number if number is not None and number >= 0 else None


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _aware(parsed).astimezone(UTC)


def _aware(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso(value):
    return _aware(value).astimezone(UTC).isoformat()


def _as_date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _symbol(value):
    return str(value).strip().upper().split(".")[0]


def _elapsed_ms(started):
    return round((clock.perf_counter() - started) * 1_000, 3)
