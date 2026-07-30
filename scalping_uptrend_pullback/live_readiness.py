"""Read-only Rubix overlay for frozen Uptrend Pullback candidates.

Historical membership, rank, score, zones, target and strategy identity are
copied from the immutable watchlist.  This module only assesses current-session
proximity, quote quality, spread, no-chase and session-phase blocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math

from scalping_expected_range.live_readiness import (
    CLOSING_AUCTION,
    CLOSING_AUCTION_NO_NEW_ENTRY,
    ENTRY_READY_RESEARCH_ONLY,
    ENTRY_TRIGGER_FORMING,
    LIVE_DATA_STALE,
    LIVE_DATA_UNAVAILABLE,
    MOVE_EXTENDED_DO_NOT_CHASE,
    POST_CLOSE,
    PRE_OPEN,
    PRE_OPEN_WAIT,
    SESSION_CLOSED,
    SPREAD_TOO_WIDE,
    LiveReadinessConfig,
    RubixBatchSnapshot,
    RubixLiveBatchReader,
    live_session_phase,
)
from scalping_uptrend_pullback.config import UptrendPullbackSelectionConfig
from scalping_uptrend_pullback.states import STRATEGY_IDENTITY


UPTREND_LIVE_METRIC_VERSION = "UPTREND_PULLBACK_LIVE_READINESS_V1"
UPTREND_LIVE_EVALUATED = "UPTREND_LIVE_READINESS_EVALUATED"
UPTREND_WATCHLIST_NOT_READY = "UPTREND_WATCHLIST_NOT_READY"
UPTREND_WAIT_FOR_PULLBACK_LIVE = "UPTREND_WAIT_FOR_PULLBACK_LIVE"
UPTREND_SUPPORT_BROKEN_LIVE = "UPTREND_SUPPORT_BROKEN_LIVE"


@dataclass(frozen=True)
class UptrendLiveReadinessResult:
    watchlist_id: str
    target_session_date: str
    symbol: str
    strategy_identity: str
    historical_rank: int
    historical_score: float
    historical_state: str
    support_zone_lower: float
    support_zone_upper: float
    first_research_target: float
    invalidation_level: float
    current_price: float | None
    distance_from_support_percent: float | None
    current_spread_percent: float | None
    quote_age_seconds: float | None
    session_phase: str
    live_state: str
    no_chase_reason: str | None
    invalidation_condition: str | None
    explanations: tuple[str, ...]
    data_quality_status: str
    rubix_data_cutoff: str | None
    evaluated_at: str


@dataclass(frozen=True)
class UptrendLiveReadinessBatch:
    status: str
    watchlist_id: str | None
    target_session_date: str
    results: tuple[UptrendLiveReadinessResult, ...] = ()
    symbols_requested: tuple[str, ...] = ()
    rubix_data_cutoff: str | None = None
    connection_count: int = 0
    query_count: int = 0
    query_latency_ms: float = 0.0
    detail: str | None = None


class UptrendLiveReadinessEngine:
    """Evaluate live proximity without changing any historical field."""

    def __init__(
        self,
        reader: RubixLiveBatchReader,
        *,
        selection_config: UptrendPullbackSelectionConfig | None = None,
        live_config: LiveReadinessConfig | None = None,
    ):
        self.reader = reader
        self.selection_config = (
            selection_config or UptrendPullbackSelectionConfig()
        )
        self.live_config = live_config or LiveReadinessConfig()

    def evaluate(
        self,
        record,
        *,
        evaluated_at: datetime | None = None,
        live_snapshot: RubixBatchSnapshot | None = None,
    ) -> UptrendLiveReadinessBatch:
        now = _aware(evaluated_at or datetime.now(timezone.utc))
        if (
            record is None
            or record.header.get("status") != "READY"
            or record.header.get("strategy_identity") != STRATEGY_IDENTITY
            or not record.displayed
        ):
            return UptrendLiveReadinessBatch(
                UPTREND_WATCHLIST_NOT_READY,
                None,
                now.date().isoformat(),
                detail="No READY frozen Uptrend Pullback watchlist",
            )
        header = record.header
        target = header["target_session_date"]
        phase = live_session_phase(now, target)
        displayed = tuple(
            sorted(
                record.displayed,
                key=lambda row: (int(row["eligible_rank"]), row["symbol"]),
            )
        )
        symbols = tuple(member["symbol"] for member in displayed)
        if phase == PRE_OPEN:
            results = tuple(
                self._phase_result(
                    header,
                    member,
                    now,
                    phase,
                    PRE_OPEN_WAIT,
                )
                for member in displayed
            )
            return UptrendLiveReadinessBatch(
                PRE_OPEN_WAIT,
                header["watchlist_id"],
                target,
                results,
                symbols,
            )
        if phase in (CLOSING_AUCTION, POST_CLOSE):
            state = (
                CLOSING_AUCTION_NO_NEW_ENTRY
                if phase == CLOSING_AUCTION
                else SESSION_CLOSED
            )
            results = tuple(
                self._phase_result(
                    header,
                    member,
                    now,
                    phase,
                    state,
                )
                for member in displayed
            )
            return UptrendLiveReadinessBatch(
                state,
                header["watchlist_id"],
                target,
                results,
                symbols,
            )
        snapshot = live_snapshot or self.reader.load(
            symbols,
            target_session_date=target,
            evaluated_at=now,
        )
        results = tuple(
            self._evaluate_member(
                header,
                member,
                snapshot.by_symbol.get(member["symbol"]),
                now,
                phase,
                snapshot,
            )
            for member in displayed
        )
        return UptrendLiveReadinessBatch(
            UPTREND_LIVE_EVALUATED,
            header["watchlist_id"],
            target,
            results,
            symbols,
            snapshot.data_cutoff,
            snapshot.connection_count,
            snapshot.query_count,
            snapshot.query_latency_ms,
            snapshot.error_detail,
        )

    def _evaluate_member(
        self,
        header,
        member,
        snapshot,
        now,
        phase,
        batch,
    ):
        if phase == CLOSING_AUCTION:
            return self._phase_result(
                header,
                member,
                now,
                phase,
                CLOSING_AUCTION_NO_NEW_ENTRY,
                snapshot=snapshot,
                rubix_data_cutoff=batch.data_cutoff,
            )
        if phase == POST_CLOSE:
            return self._phase_result(
                header,
                member,
                now,
                phase,
                SESSION_CLOSED,
                snapshot=snapshot,
                rubix_data_cutoff=batch.data_cutoff,
            )
        if batch.error_code or snapshot is None or snapshot.quote is None:
            return self._result(
                header,
                member,
                now,
                phase,
                LIVE_DATA_UNAVAILABLE,
                snapshot=snapshot,
                data_quality_status="UNAVAILABLE",
                explanation=batch.error_detail or batch.error_code,
                rubix_data_cutoff=batch.data_cutoff,
            )

        quote = snapshot.quote
        current = _positive(quote.last_price)
        quote_age = _quote_age_seconds(quote, now)
        spread = _spread_percent(quote.bid, quote.ask)
        if current is None:
            state = LIVE_DATA_UNAVAILABLE
            quality = "UNAVAILABLE"
        elif quote_age is None or quote_age > self.live_config.quote_stale_seconds:
            state = LIVE_DATA_STALE
            quality = "STALE"
        elif (
            spread is not None
            and spread > self.live_config.maximum_spread_percent
        ):
            state = SPREAD_TOO_WIDE
            quality = "AVAILABLE_AND_VALIDATED"
        else:
            state = self._proximity_state(member, current)
            quality = "AVAILABLE_AND_VALIDATED"
        distance = _distance_from_zone(
            current,
            member["support_zone_lower"],
            member["support_zone_upper"],
        )
        no_chase = (
            "Live price is beyond the backend-owned pullback proximity band"
            if state == MOVE_EXTENDED_DO_NOT_CHASE
            else None
        )
        invalidation = (
            "Live price is below the frozen invalidation level"
            if state == UPTREND_SUPPORT_BROKEN_LIVE
            else None
        )
        return self._result(
            header,
            member,
            now,
            phase,
            state,
            snapshot=snapshot,
            current_price=current,
            distance=distance,
            spread=spread,
            quote_age=quote_age,
            no_chase=no_chase,
            invalidation=invalidation,
            data_quality_status=quality,
            rubix_data_cutoff=batch.data_cutoff,
        )

    def _proximity_state(self, member, current):
        if current < float(member["invalidation_level"]):
            return UPTREND_SUPPORT_BROKEN_LIVE
        distance = _distance_from_zone(
            current,
            member["support_zone_lower"],
            member["support_zone_upper"],
        )
        if distance is None:
            return LIVE_DATA_UNAVAILABLE
        proximity = self.selection_config.pullback
        if distance > proximity.wait_for_pullback_maximum_percent:
            return MOVE_EXTENDED_DO_NOT_CHASE
        if distance > proximity.near_support_maximum_percent:
            return UPTREND_WAIT_FOR_PULLBACK_LIVE
        if distance > 0:
            return ENTRY_TRIGGER_FORMING
        return ENTRY_READY_RESEARCH_ONLY

    def _phase_result(
        self,
        header,
        member,
        now,
        phase,
        state,
        *,
        snapshot=None,
        rubix_data_cutoff=None,
    ):
        return self._result(
            header,
            member,
            now,
            phase,
            state,
            snapshot=snapshot,
            data_quality_status="NOT_QUERIED_SESSION_PHASE",
            rubix_data_cutoff=rubix_data_cutoff,
        )

    @staticmethod
    def _result(
        header,
        member,
        now,
        phase,
        state,
        *,
        snapshot=None,
        current_price=None,
        distance=None,
        spread=None,
        quote_age=None,
        no_chase=None,
        invalidation=None,
        data_quality_status,
        explanation=None,
        rubix_data_cutoff=None,
    ):
        return UptrendLiveReadinessResult(
            header["watchlist_id"],
            header["target_session_date"],
            member["symbol"],
            member["strategy_identity"],
            int(member["eligible_rank"]),
            float(member["total_score"]),
            member["candidate_state"],
            float(member["support_zone_lower"]),
            float(member["support_zone_upper"]),
            float(member["first_research_target"]),
            float(member["invalidation_level"]),
            current_price,
            distance,
            spread,
            quote_age,
            phase,
            state,
            no_chase,
            invalidation,
            tuple(item for item in (explanation,) if item),
            data_quality_status,
            rubix_data_cutoff,
            now.astimezone(timezone.utc).isoformat(),
        )


def _distance_from_zone(current, lower, upper):
    if current is None:
        return None
    current = float(current)
    lower = float(lower)
    upper = float(upper)
    if lower <= current <= upper:
        return 0.0
    reference = upper if current > upper else lower
    return (current - reference) / reference * 100.0


def _spread_percent(bid, ask):
    bid = _positive(bid)
    ask = _positive(ask)
    if bid is None or ask is None or ask < bid:
        return None
    midpoint = (ask + bid) / 2.0
    return (ask - bid) / midpoint * 100.0 if midpoint else None


def _quote_age_seconds(quote, now):
    timestamp = quote.market_timestamp or quote.received_at
    if timestamp is None:
        return None
    return max(0.0, (now - _aware(timestamp)).total_seconds())


def _positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _aware(value):
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
