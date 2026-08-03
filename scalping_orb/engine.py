"""The pure deterministic ORB first-pullback research engine.

Research only. The engine takes typed frozen context plus completed bars and
returns a typed evaluation. It reads no global state, opens no database,
contacts no provider and consults no model. The furthest state it can reach is
``ENTRY_READY_RESEARCH`` — a research candidate, never an order.

Strategy model, in one line:

    Opening Range High breakout -> refuse the chase -> wait for the first
    pullback -> defend or reclaim OR High -> deterministic research readiness.

``OPENING_RANGE_HIGH`` is the primary structural reference throughout. EMA and
breakout-zone values are recorded as secondary context and never replace it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from scalping_orb.bars import CompletedBar
from scalping_orb.capabilities import LiveDecisionCapability
from scalping_orb.events import (
    HistoricalReplayStatus,
    LiveFreshnessStatus,
    MarketTimeStatus,
    UniverseMembershipStatus,
    VolumeCapability,
)
from scalping_orb.indicators import (
    IntradayAtr,
    IntradayAtrStatus,
    exponential_moving_average,
    intraday_atr,
)
from scalping_orb.opening_range import OpeningRangeStatus
from scalping_orb.session import CONTINUOUS_PHASES, OrbSessionPhase, require_aware
from scalping_orb.states import (
    TERMINAL_STATES,
    OrbResearchState,
    RejectionReason,
    RuleCode,
    assert_legal_transition,
)
from scalping_orb.strategy_config import (
    ENGINE_VERSION,
    EvaluationMode,
    OpeningRangeVersionMode,
    OrbStrategyConfig,
    ReclaimRule,
    VolumeRequirementMode,
)


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OpeningRangeSnapshot:
    """The exact frozen opening-range version a candidate is bound to."""

    canonical_ticker: str
    session_date: date
    revision: int
    status: OpeningRangeStatus
    high: float | None
    low: float | None
    frozen_at_utc: datetime | None
    source_identity: str | None

    @property
    def ready(self) -> bool:
        return (
            self.status is OpeningRangeStatus.READY
            and self.high is not None
            and self.low is not None
            and self.high > 0
            and self.low > 0
            and self.high >= self.low
        )

    def version_identity(self) -> str:
        payload = {
            "ticker": self.canonical_ticker,
            "session_date": self.session_date.isoformat(),
            "revision": self.revision,
            "high": self.high,
            "low": self.low,
            "frozen_at": self.frozen_at_utc.isoformat() if self.frozen_at_utc else None,
            "source_identity": self.source_identity,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class DailyContext:
    """Completed D-1 EODHD context. Supplied by the caller; never fetched here."""

    available: bool = False
    previous_close: float | None = None
    daily_atr14: float | None = None
    #: Ascending resistance levels above the last close. Never invented.
    resistance_levels: tuple[float, ...] = ()

    def nearest_resistance_above(self, price: float) -> float | None:
        candidates = [level for level in self.resistance_levels if level > price]
        return min(candidates) if candidates else None


@dataclass(frozen=True)
class ORBStrategyContext:
    """Everything the engine may know, frozen at evaluation time."""

    canonical_ticker: str
    session_date: date
    opening_range: OpeningRangeSnapshot
    universe_membership_status: UniverseMembershipStatus = (
        UniverseMembershipStatus.UNKNOWN_UNMAPPED_IDENTIFIER
    )
    operationally_eligible: bool = False
    daily: DailyContext = field(default_factory=DailyContext)
    evaluation_mode: EvaluationMode = EvaluationMode.HISTORICAL_REPLAY
    opening_range_version_mode: OpeningRangeVersionMode = (
        OpeningRangeVersionMode.DECISION_TIME_ORIGINAL_VERSION
    )
    market_time_status: MarketTimeStatus = MarketTimeStatus.MARKET_TIME_VALID
    historical_replay_status: HistoricalReplayStatus = (
        HistoricalReplayStatus.HISTORICAL_REPLAY_ACCEPTED
    )
    live_freshness_status: LiveFreshnessStatus = (
        LiveFreshnessStatus.COLLECTOR_FRESHNESS_UNAVAILABLE
    )
    live_decision_capability: LiveDecisionCapability = (
        LiveDecisionCapability.LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE
    )
    volume_capability: VolumeCapability = VolumeCapability.VOLUME_UNAVAILABLE


@dataclass(frozen=True)
class CompletedBarSequence:
    """Completed bars only, plus the instant the evaluation is made at."""

    one_minute: tuple[CompletedBar, ...]
    five_minute: tuple[CompletedBar, ...]
    as_of_utc: datetime

    def confirmation_bars(self, interval_minutes: int) -> tuple[CompletedBar, ...]:
        source = self.five_minute if interval_minutes == 5 else self.one_minute
        return tuple(
            bar
            for bar in sorted(source, key=lambda item: item.bar_start_utc)
            if bar.completed
            and bar.interval_minutes == interval_minutes
            and bar.bar_end_utc <= self.as_of_utc
            and bar.session_phase in CONTINUOUS_PHASES
        )


# --------------------------------------------------------------------------- #
# Outputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StateTransition:
    transition_id: str
    canonical_ticker: str
    session_date: date
    opening_range_version_identity: str
    opening_range_revision: int
    prior_state: OrbResearchState
    new_state: OrbResearchState
    exchange_timestamp_utc: datetime | None
    as_of_timestamp_utc: datetime
    rule_code: RuleCode
    evidence: tuple[str, ...]
    frozen_references: tuple[tuple[str, float], ...]
    market_time_status: MarketTimeStatus
    historical_replay_status: HistoricalReplayStatus
    live_freshness_status: LiveFreshnessStatus
    live_decision_capability: LiveDecisionCapability
    evaluation_mode: EvaluationMode
    strategy_fingerprint: str
    engine_version: str
    sequence_index: int


@dataclass(frozen=True)
class BreakoutZone:
    """Frozen at breakout. Never widened once the pullback starts."""

    lower: float
    upper: float
    construction_rule: str
    opening_range_version_identity: str
    breakout_identity: str


@dataclass(frozen=True)
class BreakoutAssessment:
    breakout_identity: str
    bar_start_utc: datetime
    bar_end_utc: datetime
    close: float
    high: float
    low: float
    opening_range_high: float
    distance_above_or_high: float
    distance_above_or_high_percent: float
    bar_range: float
    bar_range_percent: float
    bar_range_atr: float | None
    extension_atr: float | None
    update_count: int
    distance_to_daily_resistance: float | None
    distance_to_daily_resistance_percent: float | None
    intraday_atr: IntradayAtr
    zone: BreakoutZone
    accepted: bool
    too_extended: bool
    #: Which anti-chase gate(s) fired. Empty when the breakout is not extended.
    #: Kept separate from ``rejection_reasons``: an extended breakout is not
    #: rejected, it is made to wait for a pullback.
    extension_reasons: tuple[RejectionReason, ...]
    rejection_reasons: tuple[RejectionReason, ...]


@dataclass(frozen=True)
class PullbackAssessment:
    ordinal: int
    start_bar_start_utc: datetime
    low: float
    low_bar_start_utc: datetime
    post_breakout_high: float
    depth_from_high: float
    depth_from_high_percent: float
    depth_atr: float | None
    depth_versus_or_high_percent: float
    bars_since_breakout: int
    duration_bars: int
    closes_below_or_high: int
    structural_breach_bars: int
    touched_or_high: bool
    entered_breakout_zone: bool
    ema9_five_minute: float | None
    ema20_five_minute: float | None
    ema9_one_minute: float | None
    breakout_bar_volume: float | None
    pullback_volume_total: float | None
    volume_assessed: bool
    price_only: bool
    healthy: bool
    rejection_reasons: tuple[RejectionReason, ...]


@dataclass(frozen=True)
class ReclaimAssessment:
    rule: ReclaimRule
    confirmed: bool
    confirmation_bar_start_utc: datetime | None
    confirmation_bar_end_utc: datetime | None
    trigger_price: float | None
    opening_range_high: float
    zone_lower: float
    zone_upper: float
    pullback_low: float
    bars_elapsed_since_pullback_low: int
    rejection_reasons: tuple[RejectionReason, ...]


@dataclass(frozen=True)
class StructuralRiskProposal:
    """A research stop. Never a broker instruction and never sized."""

    proposed_stop: float | None
    stop_basis: str
    raw_pullback_low: float | None
    buffer_applied: float
    buffer_basis: str
    stop_distance_absolute: float | None
    stop_distance_percent: float | None
    stop_distance_atr: float | None
    valid: bool
    rejection_reasons: tuple[RejectionReason, ...]


@dataclass(frozen=True)
class TargetProjection:
    trigger_price: float
    risk_per_share: float
    target_1: float
    target_2: float
    target_1_r_multiple: float
    target_2_r_multiple: float
    nearest_daily_resistance: float | None
    reward_before_resistance: float | None
    usable_target: float
    effective_reward_risk: float
    resistance_before_target_1: bool
    meets_minimum_reward_risk: bool
    rejection_reasons: tuple[RejectionReason, ...]


@dataclass(frozen=True)
class ORBResearchEvaluation:
    canonical_ticker: str
    session_date: date
    final_state: OrbResearchState
    terminal: bool
    opening_range_version_identity: str
    opening_range_revision: int
    transitions: tuple[StateTransition, ...]
    breakout: BreakoutAssessment | None
    pullback: PullbackAssessment | None
    reclaim: ReclaimAssessment | None
    risk: StructuralRiskProposal | None
    targets: TargetProjection | None
    rejection_reasons: tuple[RejectionReason, ...]
    evaluation_mode: EvaluationMode
    opening_range_version_mode: OpeningRangeVersionMode
    strategy_fingerprint: str
    engine_version: str
    evidence_fingerprint: str
    candidate_identity: str

    @property
    def research_ready(self) -> bool:
        return self.final_state is OrbResearchState.ENTRY_READY_RESEARCH


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


def _cairo_time(moment: datetime, zone: ZoneInfo) -> time:
    return moment.astimezone(zone).timetz().replace(tzinfo=None)


def _valid_bar(bar: CompletedBar) -> bool:
    return (
        bar.completed
        and bar.open > 0
        and bar.high > 0
        and bar.low > 0
        and bar.close > 0
        and bar.high >= max(bar.open, bar.low, bar.close)
        and bar.low <= min(bar.open, bar.high, bar.close)
    )


class OrbResearchEngine:
    """Deterministic. Same inputs always produce the same evaluation."""

    def __init__(self, config: OrbStrategyConfig | None = None):
        self.config = config or OrbStrategyConfig()
        self.zone = ZoneInfo(self.config.data.timezone)

    # -- transition plumbing ----------------------------------------------

    def _transition(
        self,
        context: ORBStrategyContext,
        state: OrbResearchState,
        new_state: OrbResearchState,
        *,
        rule: RuleCode,
        exchange_timestamp: datetime | None,
        as_of: datetime,
        evidence: Sequence[str] = (),
        frozen: Sequence[tuple[str, float]] = (),
        index: int,
    ) -> StateTransition:
        assert_legal_transition(state, new_state)
        version_identity = context.opening_range.version_identity()
        payload = {
            "ticker": context.canonical_ticker,
            "session_date": context.session_date.isoformat(),
            "or_version": version_identity,
            "prior": state.value,
            "new": new_state.value,
            "exchange": exchange_timestamp.isoformat() if exchange_timestamp else None,
            "rule": rule.value,
            "evidence": list(evidence),
            "frozen": [[name, value] for name, value in frozen],
            "mode": context.evaluation_mode.value,
            "strategy": self.config.strategy_fingerprint,
            "engine": ENGINE_VERSION,
            "index": index,
        }
        transition_id = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return StateTransition(
            transition_id=transition_id,
            canonical_ticker=context.canonical_ticker,
            session_date=context.session_date,
            opening_range_version_identity=version_identity,
            opening_range_revision=context.opening_range.revision,
            prior_state=state,
            new_state=new_state,
            exchange_timestamp_utc=exchange_timestamp,
            as_of_timestamp_utc=as_of,
            rule_code=rule,
            evidence=tuple(evidence),
            frozen_references=tuple(frozen),
            market_time_status=context.market_time_status,
            historical_replay_status=context.historical_replay_status,
            live_freshness_status=context.live_freshness_status,
            live_decision_capability=context.live_decision_capability,
            evaluation_mode=context.evaluation_mode,
            strategy_fingerprint=self.config.strategy_fingerprint,
            engine_version=ENGINE_VERSION,
            sequence_index=index,
        )

    # -- gates -------------------------------------------------------------

    def _eligibility_reasons(
        self, context: ORBStrategyContext
    ) -> tuple[RejectionReason, ...]:
        reasons: list[RejectionReason] = []
        if context.universe_membership_status is (
            UniverseMembershipStatus.ARCHIVED_INACTIVE_SYMBOL
        ):
            reasons.append(RejectionReason.ARCHIVED_SYMBOL_INELIGIBLE)
        elif not context.operationally_eligible:
            reasons.append(RejectionReason.UNVERIFIED_RUBIX_MAPPING)
        return tuple(reasons)

    def _freshness_reasons(
        self, context: ORBStrategyContext
    ) -> tuple[RejectionReason, ...]:
        """Live evidence fails closed; historical replay may reconstruct.

        Phase 2A deliberately gave ``LiveDecisionCapability`` no enabled member —
        every value is a ``LIVE_DECISION_DISABLED_*`` variant. So a live mode
        cannot reach research readiness by construction, not merely by
        configuration, and this method does not test for an "enabled" value that
        would have to be invented first. Re-enabling live is therefore a
        deliberate Phase 2A capability change, reviewed on its own merits.
        """

        reasons: list[RejectionReason] = []
        if context.market_time_status is MarketTimeStatus.MARKET_TIME_UNRELIABLE:
            reasons.append(RejectionReason.UNRELIABLE_MARKET_TIMESTAMP)
        if context.evaluation_mode is EvaluationMode.HISTORICAL_REPLAY:
            if context.historical_replay_status is (
                HistoricalReplayStatus.HISTORICAL_REPLAY_REJECTED
            ):
                reasons.append(RejectionReason.STALE_LIVE_DATA)
            return tuple(dict.fromkeys(reasons))
        # SHADOW_LIVE and LIVE_DISABLED are both disabled today.
        reasons.append(RejectionReason.LIVE_DECISION_DISABLED_FRESHNESS)
        if context.live_freshness_status is LiveFreshnessStatus.LIVE_FRESHNESS_FAILED:
            reasons.append(RejectionReason.STALE_LIVE_DATA)
        return tuple(dict.fromkeys(reasons))

    # -- breakout ----------------------------------------------------------

    def _assess_breakout(
        self,
        context: ORBStrategyContext,
        bar: CompletedBar,
        history: Sequence[CompletedBar],
    ) -> BreakoutAssessment:
        cfg = self.config
        or_high = float(context.opening_range.high)
        atr = intraday_atr(
            history,
            interval_minutes=cfg.intraday_atr_interval_minutes,
            lookback_bars=cfg.intraday_atr_lookback_bars,
            minimum_bars=cfg.intraday_atr_minimum_bars,
        )
        distance = bar.close - or_high
        distance_percent = distance / or_high if or_high else 0.0
        bar_range = bar.high - bar.low
        bar_range_percent = bar_range / or_high if or_high else 0.0
        bar_range_atr = bar_range / atr.value if atr.available else None
        extension_atr = distance / atr.value if atr.available else None

        resistance = context.daily.nearest_resistance_above(bar.close)
        resistance_distance = resistance - bar.close if resistance is not None else None
        resistance_percent = (
            resistance_distance / bar.close
            if resistance_distance is not None and bar.close
            else None
        )

        reasons: list[RejectionReason] = []
        if not _valid_bar(bar):
            reasons.append(RejectionReason.INVALID_PRICE_BAR)
        if bar.update_count < cfg.minimum_updates_for_breakout_bar:
            reasons.append(RejectionReason.INSUFFICIENT_PRICE_UPDATES)
        if bar.session_phase is OrbSessionPhase.CLOSING_AUCTION:
            reasons.append(RejectionReason.AUCTION_CONTAMINATION)
        if (
            resistance_percent is not None
            and resistance_percent < cfg.maximum_distance_to_daily_resistance_percent
        ):
            reasons.append(RejectionReason.DAILY_RESISTANCE_TOO_CLOSE)

        # Initial reward/risk, using the only structural reference that exists
        # at breakout time: the breakout bar's own low. Skipped entirely when no
        # D-1 resistance is known — a resistance is never invented to fill it.
        initial_risk = bar.close - bar.low
        initial_reward_risk = (
            (resistance - bar.close) / initial_risk
            if resistance is not None and initial_risk > 0
            else None
        )
        if (
            initial_reward_risk is not None
            and initial_reward_risk < cfg.minimum_initial_reward_risk
        ):
            reasons.append(RejectionReason.POOR_INITIAL_REWARD_RISK)

        # Each gate is recorded by name. All three compare like with like:
        # `distance_percent` and the percent threshold are both fractions of OR
        # High; `extension_atr` and `bar_range_atr` are both price/price
        # ratios against the *intraday* ATR, and are skipped entirely when that
        # ATR is unavailable rather than falling back to a daily figure.
        extension_reasons: list[RejectionReason] = []
        if distance_percent > cfg.maximum_breakout_extension_percent:
            extension_reasons.append(RejectionReason.EXTENSION_ABOVE_OR_HIGH_EXCEEDED)
        if extension_atr is not None and extension_atr > cfg.maximum_breakout_extension_atr:
            extension_reasons.append(
                RejectionReason.EXTENSION_ABOVE_OR_HIGH_ATR_EXCEEDED
            )
        if bar_range_atr is not None and bar_range_atr > cfg.maximum_breakout_bar_range_atr:
            extension_reasons.append(RejectionReason.BREAKOUT_BAR_RANGE_ATR_EXCEEDED)
        too_extended = bool(extension_reasons)

        identity_payload = {
            "ticker": context.canonical_ticker,
            "or_version": context.opening_range.version_identity(),
            "bar_start": bar.bar_start_utc.isoformat(),
            "bar_source": bar.source_identity,
        }
        breakout_identity = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

        # The zone is frozen here and never widened afterwards.
        zone = BreakoutZone(
            lower=or_high * (1 - cfg.maximum_close_below_or_high_percent),
            upper=max(bar.close, or_high),
            construction_rule=(
                "lower=or_high*(1-maximum_close_below_or_high_percent); "
                "upper=max(breakout_close, or_high)"
            ),
            opening_range_version_identity=context.opening_range.version_identity(),
            breakout_identity=breakout_identity,
        )

        return BreakoutAssessment(
            breakout_identity=breakout_identity,
            bar_start_utc=bar.bar_start_utc,
            bar_end_utc=bar.bar_end_utc,
            close=bar.close,
            high=bar.high,
            low=bar.low,
            opening_range_high=or_high,
            distance_above_or_high=distance,
            distance_above_or_high_percent=distance_percent,
            bar_range=bar_range,
            bar_range_percent=bar_range_percent,
            bar_range_atr=bar_range_atr,
            extension_atr=extension_atr,
            update_count=bar.update_count,
            distance_to_daily_resistance=resistance_distance,
            distance_to_daily_resistance_percent=resistance_percent,
            intraday_atr=atr,
            zone=zone,
            accepted=not reasons,
            too_extended=too_extended,
            extension_reasons=tuple(dict.fromkeys(extension_reasons)),
            rejection_reasons=tuple(dict.fromkeys(reasons)),
        )

    # -- risk and targets ---------------------------------------------------

    def _structural_risk(
        self,
        trigger: float,
        pullback_low: float | None,
        atr: IntradayAtr,
    ) -> StructuralRiskProposal:
        cfg = self.config
        if pullback_low is None or pullback_low <= 0:
            return StructuralRiskProposal(
                proposed_stop=None,
                stop_basis="PULLBACK_LOW_UNAVAILABLE",
                raw_pullback_low=pullback_low,
                buffer_applied=0.0,
                buffer_basis="NONE",
                stop_distance_absolute=None,
                stop_distance_percent=None,
                stop_distance_atr=None,
                valid=False,
                rejection_reasons=(RejectionReason.STOP_REFERENCE_MISSING,),
            )
        if atr.available:
            buffer = cfg.stop_atr_buffer * float(atr.value)
            basis = "INTRADAY_ATR_BUFFER"
        else:
            buffer = cfg.stop_percent_buffer * pullback_low
            basis = "PERCENT_BUFFER_ATR_UNAVAILABLE"
        stop = pullback_low - buffer
        reasons: list[RejectionReason] = []
        if stop <= 0 or stop >= trigger:
            reasons.append(RejectionReason.STOP_NOT_BELOW_TRIGGER)
        distance = trigger - stop
        distance_percent = distance / trigger if trigger else None
        if distance <= 0:
            reasons.append(RejectionReason.RISK_NOT_CALCULABLE)
        elif distance_percent is not None and (
            distance_percent > cfg.maximum_stop_distance_percent
        ):
            reasons.append(RejectionReason.STOP_DISTANCE_EXCEEDED)
        return StructuralRiskProposal(
            proposed_stop=stop,
            stop_basis="BELOW_FIRST_PULLBACK_LOW",
            raw_pullback_low=pullback_low,
            buffer_applied=buffer,
            buffer_basis=basis,
            stop_distance_absolute=distance,
            stop_distance_percent=distance_percent,
            stop_distance_atr=(distance / atr.value) if atr.available else None,
            valid=not reasons,
            rejection_reasons=tuple(dict.fromkeys(reasons)),
        )

    def _targets(
        self,
        context: ORBStrategyContext,
        trigger: float,
        risk: StructuralRiskProposal,
    ) -> TargetProjection | None:
        cfg = self.config
        if not risk.valid or not risk.stop_distance_absolute:
            return None
        risk_per_share = float(risk.stop_distance_absolute)
        target_1 = trigger + cfg.target_1_r_multiple * risk_per_share
        target_2 = trigger + cfg.target_2_r_multiple * risk_per_share
        resistance = context.daily.nearest_resistance_above(trigger)
        reward_before_resistance = (
            resistance - trigger if resistance is not None else None
        )
        resistance_before_target_1 = bool(
            resistance is not None
            and resistance
            < target_1 * (1 - cfg.maximum_distance_to_target_resistance_percent)
        )
        # The usable target is the furthest projected target still reachable
        # before known D-1 resistance. Measuring reward to target 1 would make
        # the ratio tautologically equal to target_1_r_multiple and turn the
        # minimum-R/R gate into a no-op.
        if resistance is not None and resistance < target_2:
            usable_target = resistance
        else:
            usable_target = target_2
        usable_reward = usable_target - trigger
        effective = usable_reward / risk_per_share if risk_per_share else 0.0
        reasons: list[RejectionReason] = []
        if resistance_before_target_1:
            reasons.append(RejectionReason.DAILY_RESISTANCE_TOO_CLOSE)
        if effective < cfg.minimum_reward_risk:
            reasons.append(RejectionReason.REWARD_RISK_BELOW_MINIMUM)
        return TargetProjection(
            trigger_price=trigger,
            risk_per_share=risk_per_share,
            target_1=target_1,
            target_2=target_2,
            target_1_r_multiple=cfg.target_1_r_multiple,
            target_2_r_multiple=cfg.target_2_r_multiple,
            nearest_daily_resistance=resistance,
            reward_before_resistance=reward_before_resistance,
            usable_target=usable_target,
            effective_reward_risk=effective,
            resistance_before_target_1=resistance_before_target_1,
            meets_minimum_reward_risk=effective >= cfg.minimum_reward_risk,
            rejection_reasons=tuple(dict.fromkeys(reasons)),
        )

    # -- main evaluation ----------------------------------------------------

    def evaluate(
        self, context: ORBStrategyContext, bars: CompletedBarSequence
    ) -> ORBResearchEvaluation:
        cfg = self.config
        as_of = require_aware(bars.as_of_utc, "as_of_utc").astimezone(timezone.utc)
        transitions: list[StateTransition] = []
        rejections: list[RejectionReason] = []
        state = OrbResearchState.WAIT_OPENING_RANGE
        index = 0

        def move(
            new_state: OrbResearchState,
            rule: RuleCode,
            *,
            exchange_timestamp: datetime | None = None,
            evidence: Sequence[str] = (),
            frozen: Sequence[tuple[str, float]] = (),
        ) -> OrbResearchState:
            nonlocal state, index
            transitions.append(
                self._transition(
                    context,
                    state,
                    new_state,
                    rule=rule,
                    exchange_timestamp=exchange_timestamp,
                    as_of=as_of,
                    evidence=evidence,
                    frozen=frozen,
                    index=index,
                )
            )
            index += 1
            state = new_state
            return state

        def finish(
            breakout=None, pullback=None, reclaim=None, risk=None, targets=None
        ) -> ORBResearchEvaluation:
            return self._build(
                context,
                state,
                tuple(transitions),
                tuple(dict.fromkeys(rejections)),
                breakout,
                pullback,
                reclaim,
                risk,
                targets,
                as_of,
            )

        # --- eligibility and opening range -------------------------------
        eligibility = self._eligibility_reasons(context)
        if eligibility:
            rejections.extend(eligibility)
            move(
                OrbResearchState.FAILED,
                RuleCode.BREAKOUT_QUALITY_REJECTED,
                evidence=tuple(reason.value for reason in eligibility),
            )
            return finish()

        if not context.opening_range.ready:
            rejections.append(RejectionReason.OPENING_RANGE_NOT_READY)
            move(
                OrbResearchState.DATA_UNAVAILABLE,
                RuleCode.OPENING_RANGE_NOT_READY,
                evidence=(context.opening_range.status.value,),
            )
            return finish()

        or_high = float(context.opening_range.high)
        move(
            OrbResearchState.WAIT_BREAKOUT,
            RuleCode.OPENING_RANGE_READY,
            evidence=(f"opening_range_revision={context.opening_range.revision}",),
            frozen=(("opening_range_high", or_high),),
        )

        confirmation = bars.confirmation_bars(cfg.breakout_confirmation_interval_minutes)
        if not confirmation:
            rejections.append(RejectionReason.PULLBACK_NOT_STARTED)
            return finish()

        breakout: BreakoutAssessment | None = None
        pullback: PullbackAssessment | None = None
        reclaim: ReclaimAssessment | None = None
        risk: StructuralRiskProposal | None = None
        targets: TargetProjection | None = None

        post_breakout_high: float | None = None
        pullback_low: float | None = None
        pullback_low_bar: CompletedBar | None = None
        pullback_start_bar: CompletedBar | None = None
        pullback_bars: list[CompletedBar] = []
        breakout_index: int | None = None
        #: Breaching closes observed while waiting for reclaim.
        reclaim_breaches = 0

        freshness = self._freshness_reasons(context)

        for position, bar in enumerate(confirmation):
            clock = _cairo_time(bar.bar_start_utc, self.zone)

            # Auction and cutoff dominate everything else.
            if clock >= cfg.auction_start:
                rejections.append(RejectionReason.AUCTION_PHASE_REACHED)
                move(
                    OrbResearchState.AUCTION_PHASE,
                    RuleCode.AUCTION_REACHED,
                    exchange_timestamp=bar.bar_start_utc,
                )
                return finish(breakout, pullback, reclaim, risk, targets)
            if clock >= cfg.expiry_time and state not in (
                OrbResearchState.LATE_SESSION,
            ):
                rejections.append(RejectionReason.ENTRY_WINDOW_EXPIRED)
                move(
                    OrbResearchState.ENTRY_EXPIRED,
                    RuleCode.ENTRY_WINDOW_EXPIRED,
                    exchange_timestamp=bar.bar_start_utc,
                )
                return finish(breakout, pullback, reclaim, risk, targets)
            if clock >= cfg.latest_research_entry_time and state not in (
                OrbResearchState.LATE_SESSION,
            ):
                rejections.append(RejectionReason.LATE_SESSION_CUTOFF)
                move(
                    OrbResearchState.LATE_SESSION,
                    RuleCode.LATE_SESSION_CUTOFF,
                    exchange_timestamp=bar.bar_start_utc,
                )
                continue

            if state is OrbResearchState.WAIT_BREAKOUT:
                if clock < cfg.earliest_breakout_time:
                    continue
                threshold = or_high * (1 + cfg.minimum_close_above_or_high_percent)
                if bar.high > or_high and bar.close <= or_high:
                    rejections.append(RejectionReason.WICK_ONLY_BREAKOUT)
                    continue
                if bar.close <= threshold:
                    continue

                history = confirmation[:position]
                candidate = self._assess_breakout(context, bar, history)
                move(
                    OrbResearchState.BREAKOUT_CANDIDATE,
                    RuleCode.BREAKOUT_CLOSE_ABOVE_OR_HIGH,
                    exchange_timestamp=bar.bar_start_utc,
                    evidence=(
                        f"close={bar.close:.6f}",
                        f"or_high={or_high:.6f}",
                    ),
                    frozen=(
                        ("breakout_close", bar.close),
                        ("opening_range_high", or_high),
                    ),
                )
                breakout = candidate

                if freshness:
                    rejections.extend(freshness)
                    stale = RejectionReason.STALE_LIVE_DATA in freshness or (
                        RejectionReason.LIVE_DECISION_DISABLED_FRESHNESS in freshness
                    )
                    move(
                        OrbResearchState.BREAKOUT_REJECTED_STALE
                        if stale
                        else OrbResearchState.BREAKOUT_REJECTED_POOR_QUALITY,
                        RuleCode.BREAKOUT_QUALITY_REJECTED,
                        exchange_timestamp=bar.bar_start_utc,
                        evidence=tuple(reason.value for reason in freshness),
                    )
                    return finish(breakout, pullback, reclaim, risk, targets)

                if candidate.rejection_reasons:
                    rejections.extend(candidate.rejection_reasons)
                    move(
                        OrbResearchState.BREAKOUT_REJECTED_POOR_QUALITY,
                        RuleCode.BREAKOUT_QUALITY_REJECTED,
                        exchange_timestamp=bar.bar_start_utc,
                        evidence=tuple(
                            reason.value for reason in candidate.rejection_reasons
                        ),
                    )
                    return finish(breakout, pullback, reclaim, risk, targets)

                if candidate.too_extended:
                    # Record which gate fired *and* every measurement, so an
                    # ATR-range rejection is never filed as a percent one.
                    anti_chase_evidence = [
                        reason.value for reason in candidate.extension_reasons
                    ]
                    anti_chase_evidence.append(
                        f"extension_percent={candidate.distance_above_or_high_percent:.6f}"
                    )
                    if candidate.extension_atr is not None:
                        anti_chase_evidence.append(
                            f"extension_atr={candidate.extension_atr:.6f}"
                        )
                    if candidate.bar_range_atr is not None:
                        anti_chase_evidence.append(
                            f"bar_range_atr={candidate.bar_range_atr:.6f}"
                        )
                    anti_chase_evidence.append(
                        f"intraday_atr_status={candidate.intraday_atr.status.value}"
                    )
                    move(
                        OrbResearchState.BREAKOUT_TOO_EXTENDED,
                        RuleCode.ANTI_CHASE_EXTENSION_EXCEEDED,
                        exchange_timestamp=bar.bar_start_utc,
                        evidence=tuple(anti_chase_evidence),
                    )
                else:
                    move(
                        OrbResearchState.MOMENTUM_QUALIFIED_RESEARCH,
                        RuleCode.ANTI_CHASE_ACCEPTABLE,
                        exchange_timestamp=bar.bar_start_utc,
                    )
                # The breakout bar itself can never be an entry.
                move(
                    OrbResearchState.WAIT_FIRST_PULLBACK,
                    RuleCode.BREAKOUT_FROZEN_AWAIT_PULLBACK,
                    exchange_timestamp=bar.bar_end_utc,
                    frozen=(
                        ("breakout_zone_lower", candidate.zone.lower),
                        ("breakout_zone_upper", candidate.zone.upper),
                    ),
                )
                breakout_index = position
                post_breakout_high = bar.high
                continue

            if state is OrbResearchState.WAIT_FIRST_PULLBACK:
                assert breakout is not None and breakout_index is not None
                bars_since = position - breakout_index
                post_breakout_high = max(post_breakout_high or bar.high, bar.high)
                depth = (post_breakout_high - bar.low) / post_breakout_high
                if depth >= cfg.minimum_pullback_depth_percent:
                    # The starting bar is assessed on its own bar, not one bar
                    # later: a pullback that opens straight through the floor
                    # must fail immediately rather than look healthy for a bar.
                    pullback_start_bar = bar
                    pullback_bars = []
                    pullback_low = bar.low
                    pullback_low_bar = bar
                    move(
                        OrbResearchState.FIRST_PULLBACK_IN_PROGRESS,
                        RuleCode.FIRST_PULLBACK_STARTED,
                        exchange_timestamp=bar.bar_start_utc,
                        evidence=(f"bars_since_breakout={bars_since}",),
                    )
                    # fall through to the in-progress assessment below
                elif bars_since >= cfg.maximum_bars_until_first_pullback:
                    rejections.append(RejectionReason.PULLBACK_NOT_STARTED)
                    move(
                        OrbResearchState.ENTRY_EXPIRED,
                        RuleCode.ENTRY_WINDOW_EXPIRED,
                        exchange_timestamp=bar.bar_start_utc,
                    )
                    return finish(breakout, pullback, reclaim, risk, targets)
                if state is OrbResearchState.WAIT_FIRST_PULLBACK:
                    continue

            if state is OrbResearchState.FIRST_PULLBACK_IN_PROGRESS:
                assert breakout is not None and pullback_start_bar is not None
                pullback_bars.append(bar)
                if bar.low < (pullback_low or bar.low):
                    pullback_low = bar.low
                    pullback_low_bar = bar
                pullback = self._assess_pullback(
                    context,
                    breakout,
                    pullback_start_bar,
                    pullback_bars,
                    pullback_low or bar.low,
                    pullback_low_bar or bar,
                    post_breakout_high or breakout.high,
                    bars.one_minute,
                    breakout_index or 0,
                    confirmation,
                )
                if pullback.rejection_reasons:
                    rejections.extend(pullback.rejection_reasons)
                    failure = self._pullback_failure_state(pullback)
                    move(
                        failure,
                        self._pullback_failure_rule(failure),
                        exchange_timestamp=bar.bar_start_utc,
                        evidence=tuple(
                            reason.value for reason in pullback.rejection_reasons
                        ),
                    )
                    return finish(breakout, pullback, reclaim, risk, targets)
                # OR High is the primary structural decision point, so only a
                # return to it resolves the pullback. Breakout-zone entry is
                # recorded as context but must not resolve it — the zone spans
                # everything up to the breakout close, so treating entry as
                # resolution would end every pullback on its first bar and
                # leave `maximum_pullback_bars` unreachable.
                if not pullback.touched_or_high:
                    continue
                healthy_state = (
                    OrbResearchState.PULLBACK_PRICE_ONLY
                    if pullback.price_only
                    else OrbResearchState.PULLBACK_HEALTHY
                )
                move(
                    healthy_state,
                    RuleCode.PULLBACK_QUALITY_ASSESSED,
                    exchange_timestamp=bar.bar_start_utc,
                    frozen=(("pullback_low", pullback.low),),
                )
                move(
                    OrbResearchState.WAIT_RECLAIM,
                    RuleCode.AWAIT_RECLAIM_CONFIRMATION,
                    exchange_timestamp=bar.bar_end_utc,
                )
                continue

            if state is OrbResearchState.WAIT_RECLAIM:
                assert breakout is not None and pullback is not None
                elapsed = position - confirmation.index(pullback_low_bar or bar)
                if elapsed > cfg.confirmation_expiry_bars:
                    rejections.append(RejectionReason.RECLAIM_CONFIRMATION_EXPIRED)
                    move(
                        OrbResearchState.RECLAIM_FAILED,
                        RuleCode.RECLAIM_NOT_CONFIRMED,
                        exchange_timestamp=bar.bar_start_utc,
                    )
                    return finish(breakout, pullback, reclaim, risk, targets)
                # Structure must keep holding while we wait. A completed close
                # decisively under the zone is a failure, not a longer wait.
                # ``maximum_structural_breach_bars`` is how many such closes are
                # tolerated; at the default 0 the first one fails.
                if bar.close < or_high * (1 - cfg.maximum_close_below_or_high_percent):
                    reclaim_breaches += 1
                    if reclaim_breaches > cfg.maximum_structural_breach_bars:
                        rejections.append(
                            RejectionReason.STRUCTURAL_LOWER_LOW_FAILURE
                        )
                        move(
                            OrbResearchState.PULLBACK_STRUCTURE_FAILED,
                            RuleCode.PULLBACK_STRUCTURE_BROKEN,
                            exchange_timestamp=bar.bar_start_utc,
                            evidence=(
                                f"close={bar.close:.6f}",
                                f"or_high={or_high:.6f}",
                                f"breaching_closes={reclaim_breaches}",
                            ),
                        )
                        return finish(breakout, pullback, reclaim, risk, targets)
                    continue
                confirmed, trigger = self._reclaim_confirmed(bar, or_high)
                if not confirmed:
                    continue
                reclaim = ReclaimAssessment(
                    rule=cfg.reclaim_rule,
                    confirmed=True,
                    confirmation_bar_start_utc=bar.bar_start_utc,
                    confirmation_bar_end_utc=bar.bar_end_utc,
                    trigger_price=trigger,
                    opening_range_high=or_high,
                    zone_lower=breakout.zone.lower,
                    zone_upper=breakout.zone.upper,
                    pullback_low=pullback.low,
                    bars_elapsed_since_pullback_low=elapsed,
                    rejection_reasons=(),
                )
                move(
                    OrbResearchState.RECLAIM_CANDIDATE,
                    RuleCode.RECLAIM_CONFIRMED,
                    exchange_timestamp=bar.bar_end_utc,
                    frozen=(("trigger_price", trigger),),
                )
                risk = self._structural_risk(
                    trigger, pullback.low, breakout.intraday_atr
                )
                targets = self._targets(context, trigger, risk)
                blocking = list(risk.rejection_reasons)
                if targets is None:
                    blocking.append(RejectionReason.RISK_NOT_CALCULABLE)
                else:
                    blocking.extend(targets.rejection_reasons)
                if blocking:
                    rejections.extend(blocking)
                    move(
                        OrbResearchState.RECLAIM_FAILED,
                        RuleCode.RESEARCH_READINESS_REJECTED,
                        exchange_timestamp=bar.bar_end_utc,
                        evidence=tuple(reason.value for reason in blocking),
                    )
                    return finish(breakout, pullback, reclaim, risk, targets)
                move(
                    OrbResearchState.ENTRY_READY_RESEARCH,
                    RuleCode.RESEARCH_READINESS_ACCEPTED,
                    exchange_timestamp=bar.bar_end_utc,
                    frozen=(
                        ("trigger_price", trigger),
                        ("proposed_stop", float(risk.proposed_stop)),
                        ("target_1", targets.target_1),
                        ("target_2", targets.target_2),
                    ),
                )
                return finish(breakout, pullback, reclaim, risk, targets)

        if state is OrbResearchState.WAIT_FIRST_PULLBACK:
            rejections.append(RejectionReason.PULLBACK_NOT_STARTED)
        elif state is OrbResearchState.WAIT_RECLAIM:
            rejections.append(RejectionReason.RECLAIM_TRIGGER_NOT_REACHED)

        # Session-phase closure is decided by the evaluation instant, not by a
        # bar. Auction bars never reach this loop at all — Phase 2A excludes
        # them from continuous aggregation — so an auction bar could never
        # carry the activation into AUCTION_PHASE on its own.
        if state not in TERMINAL_STATES:
            clock_now = _cairo_time(as_of, self.zone)
            if clock_now >= cfg.auction_start:
                rejections.append(RejectionReason.AUCTION_PHASE_REACHED)
                move(OrbResearchState.AUCTION_PHASE, RuleCode.AUCTION_REACHED)
            elif clock_now >= cfg.expiry_time:
                rejections.append(RejectionReason.ENTRY_WINDOW_EXPIRED)
                move(OrbResearchState.ENTRY_EXPIRED, RuleCode.ENTRY_WINDOW_EXPIRED)
            elif (
                clock_now >= cfg.latest_research_entry_time
                and state is not OrbResearchState.LATE_SESSION
            ):
                rejections.append(RejectionReason.LATE_SESSION_CUTOFF)
                move(OrbResearchState.LATE_SESSION, RuleCode.LATE_SESSION_CUTOFF)
        return finish(breakout, pullback, reclaim, risk, targets)

    # -- pullback helpers ---------------------------------------------------

    def _assess_pullback(
        self,
        context: ORBStrategyContext,
        breakout: BreakoutAssessment,
        start_bar: CompletedBar,
        members: Sequence[CompletedBar],
        low: float,
        low_bar: CompletedBar,
        post_breakout_high: float,
        one_minute: Sequence[CompletedBar],
        breakout_index: int,
        confirmation: Sequence[CompletedBar],
    ) -> PullbackAssessment:
        cfg = self.config
        or_high = breakout.opening_range_high
        depth = post_breakout_high - low
        depth_percent = depth / post_breakout_high if post_breakout_high else 0.0
        depth_atr = (
            depth / breakout.intraday_atr.value
            if breakout.intraday_atr.available
            else None
        )
        # Recorded evidence, not gates. The pullback resolves on the first bar
        # whose low reaches OR High, and any close below OR High implies such a
        # low, so neither counter can exceed one here. Multi-bar breach
        # tolerance is enforced in WAIT_RECLAIM, the only phase that can
        # observe more than one breaching close.
        breach_threshold = or_high * (1 - cfg.maximum_close_below_or_high_percent)
        closes_below = sum(1 for bar in members if bar.close < or_high)
        breaches = sum(1 for bar in members if bar.close < breach_threshold)
        # A bar has returned to the structure once its low reaches OR High —
        # including a bar trading entirely beneath it.
        touched = any(bar.low <= or_high for bar in members)
        entered_zone = any(
            bar.low <= breakout.zone.upper and bar.high >= breakout.zone.lower
            for bar in members
        )

        # Structural failure during the pullback itself: a low that wicks
        # decisively through the frozen zone floor. Its own named threshold, so
        # it does not silently inherit the close-based breach percentage.
        low_failure_threshold = breakout.zone.lower * (
            1 - cfg.maximum_low_below_zone_lower_percent
        )
        structural_failure = low < low_failure_threshold

        breakout_bar = confirmation[breakout_index] if confirmation else None
        breakout_volume = breakout_bar.volume if breakout_bar else None
        member_volumes = [bar.volume for bar in members]
        volume_valid = (
            context.volume_capability is VolumeCapability.VOLUME_AVAILABLE
            and breakout_volume is not None
            and all(value is not None for value in member_volumes)
        )
        pullback_volume_total = (
            sum(float(value) for value in member_volumes) if volume_valid else None
        )

        reasons: list[RejectionReason] = []
        if depth_percent > cfg.maximum_pullback_depth_percent:
            reasons.append(RejectionReason.PULLBACK_DEPTH_PERCENT_EXCEEDED)
        if depth_atr is not None and depth_atr > cfg.maximum_pullback_depth_atr:
            reasons.append(RejectionReason.PULLBACK_DEPTH_ATR_EXCEEDED)
        if len(members) > cfg.maximum_pullback_bars:
            reasons.append(RejectionReason.PULLBACK_DURATION_EXCEEDED)
        if structural_failure:
            reasons.append(RejectionReason.STRUCTURAL_LOWER_LOW_FAILURE)

        price_only = False
        if volume_valid:
            average_pullback = pullback_volume_total / max(1, len(members))
            if (
                cfg.require_reduced_selling_volume_when_volume_valid
                and breakout_volume
                and average_pullback > float(breakout_volume)
            ):
                reasons.append(RejectionReason.SELLING_VOLUME_EXPANSION)
        else:
            if cfg.volume_requirement_mode is VolumeRequirementMode.REQUIRE_VALID_VOLUME:
                reasons.append(RejectionReason.VOLUME_UNAVAILABLE_AND_REQUIRED)
            else:
                price_only = True

        return PullbackAssessment(
            ordinal=1,
            start_bar_start_utc=start_bar.bar_start_utc,
            low=low,
            low_bar_start_utc=low_bar.bar_start_utc,
            post_breakout_high=post_breakout_high,
            depth_from_high=depth,
            depth_from_high_percent=depth_percent,
            depth_atr=depth_atr,
            depth_versus_or_high_percent=(or_high - low) / or_high if or_high else 0.0,
            bars_since_breakout=max(0, confirmation.index(start_bar) - breakout_index)
            if start_bar in confirmation
            else 0,
            duration_bars=len(members),
            closes_below_or_high=closes_below,
            structural_breach_bars=breaches,
            touched_or_high=touched,
            entered_breakout_zone=entered_zone,
            ema9_five_minute=exponential_moving_average(
                confirmation, period=9, interval_minutes=5
            ),
            ema20_five_minute=exponential_moving_average(
                confirmation, period=20, interval_minutes=5
            ),
            ema9_one_minute=exponential_moving_average(
                one_minute, period=9, interval_minutes=1
            ),
            breakout_bar_volume=breakout_volume,
            pullback_volume_total=pullback_volume_total,
            volume_assessed=volume_valid,
            price_only=price_only,
            healthy=not reasons,
            rejection_reasons=tuple(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _pullback_failure_state(
        pullback: PullbackAssessment,
    ) -> OrbResearchState:
        reasons = set(pullback.rejection_reasons)
        if RejectionReason.SELLING_VOLUME_EXPANSION in reasons:
            return OrbResearchState.PULLBACK_VOLUME_EXPANSION
        if {
            RejectionReason.PULLBACK_DEPTH_PERCENT_EXCEEDED,
            RejectionReason.PULLBACK_DEPTH_ATR_EXCEEDED,
        } & reasons:
            return OrbResearchState.PULLBACK_TOO_DEEP
        return OrbResearchState.PULLBACK_STRUCTURE_FAILED

    @staticmethod
    def _pullback_failure_rule(state: OrbResearchState) -> RuleCode:
        if state is OrbResearchState.PULLBACK_VOLUME_EXPANSION:
            return RuleCode.PULLBACK_VOLUME_EXPANDED
        if state is OrbResearchState.PULLBACK_TOO_DEEP:
            return RuleCode.PULLBACK_DEPTH_EXCEEDED
        return RuleCode.PULLBACK_STRUCTURE_BROKEN

    def _reclaim_confirmed(
        self, bar: CompletedBar, or_high: float
    ) -> tuple[bool, float | None]:
        """The single configured deterministic confirmation rule.

        A touch is never confirmation: every rule requires a *completed* bar.
        """

        cfg = self.config
        if cfg.require_completed_bar and not bar.completed:
            return False, None
        if cfg.reclaim_rule is not ReclaimRule.CLOSE_RECLAIMS_OR_HIGH:
            # Unreachable via configuration — OrbStrategyConfig rejects any
            # unimplemented rule. Kept so a future rule cannot be added to the
            # enum and silently behave like the default one.
            raise ValueError(
                f"{cfg.reclaim_rule.value} is not implemented in Phase 2B Core"
            )
        if bar.close > or_high:
            return True, bar.close
        return False, None

    # -- assembly -----------------------------------------------------------

    def _build(
        self,
        context: ORBStrategyContext,
        state: OrbResearchState,
        transitions: tuple[StateTransition, ...],
        rejections: tuple[RejectionReason, ...],
        breakout,
        pullback,
        reclaim,
        risk,
        targets,
        as_of: datetime,
    ) -> ORBResearchEvaluation:
        from scalping_orb.states import TERMINAL_STATES

        version_identity = context.opening_range.version_identity()
        evidence_payload = {
            "state": state.value,
            "transitions": [item.transition_id for item in transitions],
            "rejections": [reason.value for reason in rejections],
            "breakout": breakout.breakout_identity if breakout else None,
            "pullback_low": pullback.low if pullback else None,
            "trigger": targets.trigger_price if targets else None,
            "stop": risk.proposed_stop if risk else None,
        }
        evidence_fingerprint = hashlib.sha256(
            json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        candidate_identity = hashlib.sha256(
            json.dumps(
                {
                    "ticker": context.canonical_ticker,
                    "session_date": context.session_date.isoformat(),
                    "or_version": version_identity,
                    "mode": context.evaluation_mode.value,
                    "strategy": self.config.strategy_fingerprint,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return ORBResearchEvaluation(
            canonical_ticker=context.canonical_ticker,
            session_date=context.session_date,
            final_state=state,
            terminal=state in TERMINAL_STATES,
            opening_range_version_identity=version_identity,
            opening_range_revision=context.opening_range.revision,
            transitions=transitions,
            breakout=breakout,
            pullback=pullback,
            reclaim=reclaim,
            risk=risk,
            targets=targets,
            rejection_reasons=rejections,
            evaluation_mode=context.evaluation_mode,
            opening_range_version_mode=context.opening_range_version_mode,
            strategy_fingerprint=self.config.strategy_fingerprint,
            engine_version=ENGINE_VERSION,
            evidence_fingerprint=evidence_fingerprint,
            candidate_identity=candidate_identity,
        )


__all__ = [
    "BreakoutAssessment",
    "BreakoutZone",
    "CompletedBarSequence",
    "DailyContext",
    "ORBResearchEvaluation",
    "ORBStrategyContext",
    "OpeningRangeSnapshot",
    "OrbResearchEngine",
    "PullbackAssessment",
    "ReclaimAssessment",
    "StateTransition",
    "StructuralRiskProposal",
    "TargetProjection",
]
