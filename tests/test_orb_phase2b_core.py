"""Phase 2B Core — deterministic synthetic matrix for the research engine.

Research only. No test starts Rubix, opens a provider, reaches the network, or
touches a production database. Every bar is hand-built with fixed Cairo
timestamps so each assertion pins one rule.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from scalping_orb.bars import CompletedBar
from scalping_orb.capabilities import LiveDecisionCapability
from scalping_orb.engine import (
    CompletedBarSequence,
    DailyContext,
    ORBStrategyContext,
    OpeningRangeSnapshot,
    OrbResearchEngine,
)
from scalping_orb.events import (
    HistoricalReplayStatus,
    LiveFreshnessStatus,
    MarketTimeStatus,
    UniverseMembershipStatus,
    VolumeCapability,
)
from scalping_orb.indicators import (
    IntradayAtrStatus,
    exponential_moving_average,
    intraday_atr,
)
from scalping_orb.opening_range import OpeningRangeStatus
from scalping_orb.repository import SCHEMA_VERSION, OrbResearchRepository
from scalping_orb.session import OrbSessionPhase
from scalping_orb.states import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    OrbResearchState,
    RejectionReason,
    assert_legal_transition,
    state_label,
)
from scalping_orb.strategy_config import (
    ENGINE_VERSION,
    IMPLEMENTED_RECLAIM_RULES,
    EvaluationMode,
    OpeningRangeVersionMode,
    OrbStrategyConfig,
    ReclaimRule,
    VolumeRequirementMode,
)
from scalping_orb.strategy_service import OrbResearchService


CAIRO = ZoneInfo("Africa/Cairo")
DAY = date(2026, 8, 2)
OR_HIGH = 10.0
OR_LOW = 9.5


def at(hour, minute, second=0):
    return datetime.combine(
        DAY, time(hour, minute, second), tzinfo=CAIRO
    ).astimezone(timezone.utc)


def bar(
    hour,
    minute,
    open_,
    high,
    low,
    close,
    *,
    volume=1000.0,
    updates=5,
    interval=5,
    phase=OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE,
    completed=True,
):
    start = at(hour, 0) + timedelta(minutes=minute)
    return CompletedBar(
        canonical_ticker="TEST",
        interval_minutes=interval,
        session_date=DAY,
        bar_start_utc=start,
        bar_end_utc=start + timedelta(minutes=interval),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        update_count=updates,
        first_sequence=None,
        last_sequence=None,
        data_quality_flags=(),
        completed=completed,
        session_phase=phase,
        source_identity=f"{hour:02d}{minute:03d}-{interval}",
        component_bar_count=interval,
    )


def warmup():
    """Seven quiet bars so intraday ATR is available at breakout time."""

    return [bar(10, minute, 9.80, 9.95, 9.75, 9.90) for minute in range(15, 50, 5)]


BREAKOUT = bar(10, 50, 9.90, 10.12, 9.88, 10.10, volume=3000)
PULLBACK_A = bar(10, 55, 10.10, 10.11, 9.99, 10.00, volume=1200)
PULLBACK_B = bar(11, 0, 10.00, 10.04, 9.995, 9.999, volume=900)
RECLAIM = bar(11, 5, 10.02, 10.20, 10.01, 10.15, volume=1500)


def healthy_bars():
    return warmup() + [BREAKOUT, PULLBACK_A, PULLBACK_B, RECLAIM]


def opening_range(**kwargs):
    base = dict(
        canonical_ticker="TEST",
        session_date=DAY,
        revision=0,
        status=OpeningRangeStatus.READY,
        high=OR_HIGH,
        low=OR_LOW,
        frozen_at_utc=at(10, 15),
        source_identity="or-source",
    )
    base.update(kwargs)
    return OpeningRangeSnapshot(**base)


def context(**kwargs):
    base = dict(
        canonical_ticker="TEST",
        session_date=DAY,
        opening_range=opening_range(),
        universe_membership_status=(
            UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX
        ),
        operationally_eligible=True,
        daily=DailyContext(available=True, resistance_levels=(11.5,)),
        volume_capability=VolumeCapability.VOLUME_AVAILABLE,
    )
    base.update(kwargs)
    return ORBStrategyContext(**base)


def run(bars=None, *, config=None, as_of=None, **context_kwargs):
    engine = OrbResearchEngine(config or OrbStrategyConfig())
    sequence = CompletedBarSequence(
        one_minute=(),
        five_minute=tuple(bars if bars is not None else healthy_bars()),
        as_of_utc=as_of or at(12, 0),
    )
    return engine.evaluate(context(**context_kwargs), sequence)


def states(evaluation):
    return [item.new_state for item in evaluation.transitions]


def _code_only(source: str) -> str:
    """Strip docstrings and comments so a prohibition scan reads real code.

    The modules document what they must never do, so a naive text search would
    match their own safety prose instead of an actual call.
    """

    import io
    import tokenize

    kept: list[str] = []
    previous = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.NEWLINE,
            tokenize.NL,
        ):
            continue  # a docstring
        kept.append(token.string)
        if token.type not in (tokenize.NL, tokenize.COMMENT):
            previous = token.type
    return " ".join(kept)


# =========================================================================== #
# Baseline
# =========================================================================== #


def test_the_healthy_setup_reaches_research_readiness():
    result = run()
    assert result.final_state is OrbResearchState.ENTRY_READY_RESEARCH
    assert result.terminal
    assert not result.rejection_reasons
    assert result.targets.effective_reward_risk >= 1.5


def test_the_only_ready_state_carries_the_research_qualifier():
    """No state may be a bare BUY or ENTRY_READY."""

    for state in OrbResearchState:
        assert state.value not in {"BUY", "ENTRY_READY", "ENTRY"}
        if "ENTRY_READY" in state.value:
            assert state.value.endswith("_RESEARCH")
    assert (
        state_label(OrbResearchState.ENTRY_READY_RESEARCH, "AR")
        == "جاهز بحثيًا بعد تأكيد إعادة الاختبار"
    )


def test_no_trade_management_state_exists():
    names = {state.value for state in OrbResearchState}
    assert not names & {"TRADE_ACTIVE", "PARTIAL_EXIT", "TRAILING", "EXITED"}


# =========================================================================== #
# BREAKOUT
# =========================================================================== #


def test_valid_completed_close_above_or_high_is_a_breakout():
    result = run()
    assert result.breakout is not None
    assert result.breakout.close > OR_HIGH
    assert OrbResearchState.BREAKOUT_CANDIDATE in states(result)


def test_wick_only_breakout_is_rejected():
    bars = warmup() + [bar(10, 50, 9.90, 10.30, 9.88, 9.95)]
    result = run(bars)
    assert result.breakout is None
    assert RejectionReason.WICK_ONLY_BREAKOUT in result.rejection_reasons
    assert result.final_state is OrbResearchState.WAIT_BREAKOUT


def test_close_exactly_at_or_high_is_not_a_breakout():
    bars = warmup() + [bar(10, 50, 9.90, 10.05, 9.88, OR_HIGH)]
    result = run(bars)
    assert result.breakout is None
    assert result.final_state is OrbResearchState.WAIT_BREAKOUT


def test_incomplete_confirmation_bar_never_confirms():
    incomplete = replace(BREAKOUT, completed=False)
    result = run(warmup() + [incomplete])
    assert result.breakout is None
    assert result.final_state is OrbResearchState.WAIT_BREAKOUT


def test_a_bar_ending_after_the_evaluation_instant_is_never_used():
    """No future leakage: the breakout bar closes one microsecond too late."""

    result = run(as_of=BREAKOUT.bar_end_utc - timedelta(microseconds=1))
    assert result.breakout is None
    result_at_close = run(as_of=BREAKOUT.bar_end_utc)
    assert result_at_close.breakout is not None


def test_auction_bar_cannot_produce_a_breakout():
    auction = bar(
        14, 15, 9.90, 10.40, 9.88, 10.30, phase=OrbSessionPhase.CLOSING_AUCTION
    )
    result = run(warmup() + [auction], as_of=at(14, 30))
    assert result.breakout is None
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH


def test_archived_symbol_is_rejected_before_any_evaluation():
    result = run(
        universe_membership_status=UniverseMembershipStatus.ARCHIVED_INACTIVE_SYMBOL,
        operationally_eligible=False,
    )
    assert result.final_state is OrbResearchState.FAILED
    assert RejectionReason.ARCHIVED_SYMBOL_INELIGIBLE in result.rejection_reasons
    assert result.breakout is None


def test_active_but_unmapped_symbol_is_rejected():
    result = run(
        universe_membership_status=(
            UniverseMembershipStatus.ACTIVE_UNIVERSE_UNVERIFIED_RUBIX
        ),
        operationally_eligible=False,
    )
    assert result.final_state is OrbResearchState.FAILED
    assert RejectionReason.UNVERIFIED_RUBIX_MAPPING in result.rejection_reasons


def test_stale_live_breakout_is_rejected_and_never_becomes_ready():
    result = run(
        evaluation_mode=EvaluationMode.SHADOW_LIVE,
        live_freshness_status=LiveFreshnessStatus.LIVE_FRESHNESS_FAILED,
    )
    assert result.final_state is OrbResearchState.BREAKOUT_REJECTED_STALE
    assert RejectionReason.STALE_LIVE_DATA in result.rejection_reasons
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH


def test_delayed_historical_evidence_is_accepted_for_reconstruction():
    result = run(
        evaluation_mode=EvaluationMode.HISTORICAL_REPLAY,
        market_time_status=MarketTimeStatus.MARKET_TIME_VALID_RECEIVE_DELAYED,
        historical_replay_status=HistoricalReplayStatus.HISTORICAL_REPLAY_ACCEPTED,
    )
    assert result.final_state is OrbResearchState.ENTRY_READY_RESEARCH


def test_unreliable_market_timestamp_is_rejected_even_in_replay():
    result = run(market_time_status=MarketTimeStatus.MARKET_TIME_UNRELIABLE)
    assert RejectionReason.UNRELIABLE_MARKET_TIMESTAMP in result.rejection_reasons
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH


def test_nearby_daily_resistance_rejects_the_breakout():
    result = run(daily=DailyContext(available=True, resistance_levels=(10.12,)))
    assert RejectionReason.DAILY_RESISTANCE_TOO_CLOSE in result.rejection_reasons
    assert result.final_state is OrbResearchState.BREAKOUT_REJECTED_POOR_QUALITY


def test_poor_initial_reward_risk_rejects_the_breakout():
    # Resistance 0.14 above the close against a 0.22 bar risk => 0.6R.
    result = run(daily=DailyContext(available=True, resistance_levels=(10.24,)))
    assert RejectionReason.POOR_INITIAL_REWARD_RISK in result.rejection_reasons


def test_insufficient_price_updates_rejects_the_breakout():
    thin = replace(BREAKOUT, update_count=1)
    result = run(warmup() + [thin, PULLBACK_A, PULLBACK_B, RECLAIM])
    assert RejectionReason.INSUFFICIENT_PRICE_UPDATES in result.rejection_reasons
    assert result.final_state is OrbResearchState.BREAKOUT_REJECTED_POOR_QUALITY


def test_invalid_price_bar_rejects_the_breakout():
    broken = replace(BREAKOUT, high=9.0)
    result = run(warmup() + [broken, PULLBACK_A])
    assert RejectionReason.INVALID_PRICE_BAR in result.rejection_reasons


def test_opening_range_not_ready_yields_data_unavailable():
    result = run(
        opening_range=opening_range(
            status=OpeningRangeStatus.INSUFFICIENT_COVERAGE, high=None, low=None
        )
    )
    assert result.final_state is OrbResearchState.DATA_UNAVAILABLE
    assert RejectionReason.OPENING_RANGE_NOT_READY in result.rejection_reasons


def test_breakout_before_the_earliest_configured_time_is_ignored():
    config = OrbStrategyConfig(earliest_breakout_time=time(11, 30))
    result = run(config=config)
    assert result.breakout is None


# =========================================================================== #
# ANTI-CHASE
# =========================================================================== #


def test_normal_breakout_is_momentum_qualified_not_extended():
    result = run()
    assert OrbResearchState.MOMENTUM_QUALIFIED_RESEARCH in states(result)
    assert not result.breakout.too_extended


FAR_RESISTANCE = DailyContext(available=True, resistance_levels=(20.0,))


def test_excessive_distance_above_or_high_is_too_extended():
    stretched = bar(10, 50, 9.90, 10.90, 9.88, 10.85, volume=3000)
    result = run(warmup() + [stretched], daily=FAR_RESISTANCE)
    assert result.breakout.too_extended
    assert OrbResearchState.BREAKOUT_TOO_EXTENDED in states(result)


def test_excessive_bar_range_in_atr_is_too_extended():
    wide = bar(10, 50, 9.90, 10.15, 8.90, 10.05, volume=3000)
    result = run(warmup() + [wide], daily=FAR_RESISTANCE)
    assert result.breakout.bar_range_atr is not None
    assert result.breakout.too_extended


def test_a_too_extended_breakout_may_still_wait_for_a_pullback():
    stretched = bar(10, 50, 9.90, 10.90, 9.88, 10.85, volume=3000)
    result = run(
        warmup() + [stretched, bar(11, 0, 10.85, 10.86, 10.40, 10.50)],
        daily=FAR_RESISTANCE,
    )
    sequence = states(result)
    assert OrbResearchState.BREAKOUT_TOO_EXTENDED in sequence
    assert OrbResearchState.WAIT_FIRST_PULLBACK in sequence


def test_a_too_extended_breakout_never_becomes_ready_on_the_breakout_bar():
    stretched = bar(10, 50, 9.90, 10.90, 9.88, 10.85, volume=3000)
    result = run(warmup() + [stretched], daily=FAR_RESISTANCE)
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH


def test_extension_gates_are_skipped_when_atr_is_unavailable():
    """Without warm-up there is no ATR, and no ATR value is invented."""

    result = run([BREAKOUT])
    assert result.breakout.intraday_atr.status is not IntradayAtrStatus.AVAILABLE
    assert result.breakout.bar_range_atr is None
    assert result.breakout.extension_atr is None


def _anti_chase_evidence(result):
    for transition in result.transitions:
        if transition.new_state is OrbResearchState.BREAKOUT_TOO_EXTENDED:
            return transition.evidence
    raise AssertionError("no BREAKOUT_TOO_EXTENDED transition was recorded")


def test_the_anti_chase_gate_that_fired_is_named_in_the_evidence():
    """A range-in-ATR rejection must not be filed as a percent rejection.

    The bar below is only 1.5% above OR High — well inside the 2.0% percent
    gate — so the only gate that can fire is the ATR one. If the evidence said
    `extension_percent` alone, the stored reason for refusing to chase would
    contradict the stored measurement.
    """

    wide = bar(10, 50, 9.90, 10.15, 8.90, 10.05, volume=3000)
    result = run(warmup() + [wide], daily=FAR_RESISTANCE)
    assert result.breakout.distance_above_or_high_percent < (
        OrbStrategyConfig().maximum_breakout_extension_percent
    )
    assert result.breakout.extension_reasons == (
        RejectionReason.BREAKOUT_BAR_RANGE_ATR_EXCEEDED,
    )
    evidence = _anti_chase_evidence(result)
    assert RejectionReason.BREAKOUT_BAR_RANGE_ATR_EXCEEDED.value in evidence
    assert RejectionReason.EXTENSION_ABOVE_OR_HIGH_EXCEEDED.value not in evidence
    # Every measurement is still recorded alongside the reason.
    assert any(item.startswith("bar_range_atr=") for item in evidence)
    assert any(item.startswith("extension_percent=") for item in evidence)


def test_the_percent_extension_gate_is_named_separately_from_the_atr_gate():
    stretched = bar(10, 50, 9.90, 10.90, 9.88, 10.85, volume=3000)
    result = run(warmup() + [stretched], daily=FAR_RESISTANCE)
    reasons = set(result.breakout.extension_reasons)
    assert RejectionReason.EXTENSION_ABOVE_OR_HIGH_EXCEEDED in reasons
    assert RejectionReason.EXTENSION_ABOVE_OR_HIGH_ATR_EXCEEDED in reasons
    assert RejectionReason.EXTENSION_ABOVE_OR_HIGH_EXCEEDED.value in (
        _anti_chase_evidence(result)
    )


def test_an_unextended_breakout_records_no_extension_reason():
    assert run().breakout.extension_reasons == ()


def test_extension_percent_and_its_threshold_are_the_same_unit():
    """Both sides of the gate are fractions of OR High, never one in points."""

    config = OrbStrategyConfig(maximum_breakout_extension_percent=0.02)
    on_gate = bar(10, 50, 9.90, 10.25, 9.88, 10.20, volume=3000)  # exactly +2.0%
    result = run(warmup() + [on_gate], config=config, daily=FAR_RESISTANCE)
    assert result.breakout.distance_above_or_high_percent == pytest.approx(0.02)
    # Strictly greater-than: sitting exactly on the threshold is not a chase.
    assert RejectionReason.EXTENSION_ABOVE_OR_HIGH_EXCEEDED not in (
        result.breakout.extension_reasons
    )
    just_over = bar(10, 50, 9.90, 10.26, 9.88, 10.21, volume=3000)
    over = run(warmup() + [just_over], config=config, daily=FAR_RESISTANCE)
    assert RejectionReason.EXTENSION_ABOVE_OR_HIGH_EXCEEDED in (
        over.breakout.extension_reasons
    )


# =========================================================================== #
# FIRST PULLBACK
# =========================================================================== #


def test_the_first_controlled_pullback_is_measured():
    result = run()
    assert result.pullback is not None
    assert result.pullback.ordinal == 1
    assert result.pullback.low == pytest.approx(9.99)
    assert result.pullback.healthy


def test_no_pullback_expires_the_activation():
    drifting = [
        bar(10, 55 + 5 * index, 10.10 + index * 0.05, 10.16 + index * 0.05,
            10.15 + index * 0.05, 10.15 + index * 0.05)
        for index in range(7)
    ]
    result = run(warmup() + [BREAKOUT] + drifting)
    assert RejectionReason.PULLBACK_NOT_STARTED in result.rejection_reasons
    assert result.final_state in {
        OrbResearchState.ENTRY_EXPIRED,
        OrbResearchState.WAIT_FIRST_PULLBACK,
    }


def test_pullback_too_deep_is_rejected():
    deep = bar(10, 55, 10.10, 10.11, 9.40, 9.45)
    result = run(warmup() + [BREAKOUT, deep])
    assert result.final_state in {
        OrbResearchState.PULLBACK_TOO_DEEP,
        OrbResearchState.PULLBACK_STRUCTURE_FAILED,
    }
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH


def test_repeated_closes_below_or_high_fail_the_structure():
    below = [
        bar(10, 55, 10.10, 10.11, 9.90, 9.93),
        bar(11, 0, 9.93, 9.96, 9.88, 9.90),
        bar(11, 5, 9.90, 9.94, 9.86, 9.89),
    ]
    result = run(warmup() + [BREAKOUT] + below)
    assert result.final_state in {
        OrbResearchState.PULLBACK_STRUCTURE_FAILED,
        OrbResearchState.PULLBACK_TOO_DEEP,
    }
    assert result.final_state in TERMINAL_STATES
    assert result.reclaim is None


def test_pullback_breach_counters_are_evidence_not_gates():
    """They cannot exceed one, so they must never be written as thresholds.

    A pullback resolves on the first bar whose low reaches OR High, and any
    close below OR High implies such a low. A gate of the form
    `closes_below > n` for n >= 1 is therefore unreachable by construction.
    """

    result = run(
        warmup()
        + [BREAKOUT]
        + [
            bar(10, 55, 10.10, 10.11, 9.98, 9.99),
            bar(11, 0, 9.99, 10.00, 9.98, 9.99),
            bar(11, 5, 9.99, 10.00, 9.98, 9.99),
        ]
    )
    assert result.pullback.duration_bars == 1
    assert result.pullback.closes_below_or_high <= 1
    assert result.pullback.structural_breach_bars <= 1
    assert RejectionReason.EXCESSIVE_CLOSES_BELOW_OR_HIGH not in (
        result.rejection_reasons
    )


def _breaching_wait():
    """Reach WAIT_RECLAIM, then close decisively under the breach threshold."""

    # Resolves the pullback on its first bar (low touches OR High) without
    # breaching, so the breach below is observed in WAIT_RECLAIM.
    return warmup() + [
        BREAKOUT,
        bar(10, 55, 10.10, 10.11, 9.99, 10.01),
        bar(11, 0, 10.01, 10.02, 9.94, 9.95),
        bar(11, 5, 9.95, 10.15, 9.94, 10.10),
    ]


def test_the_first_decisive_close_under_the_zone_fails_by_default():
    result = run(_breaching_wait())
    assert result.final_state is OrbResearchState.PULLBACK_STRUCTURE_FAILED
    assert RejectionReason.STRUCTURAL_LOWER_LOW_FAILURE in result.rejection_reasons
    assert result.reclaim is None


def test_the_structural_breach_tolerance_is_live_during_wait_reclaim():
    """The knob must actually change behaviour, in the one phase that sees it."""

    tolerant = run(
        _breaching_wait(), config=OrbStrategyConfig(maximum_structural_breach_bars=1)
    )
    assert tolerant.final_state is OrbResearchState.ENTRY_READY_RESEARCH
    assert OrbStrategyConfig().maximum_structural_breach_bars == 0


def test_the_wick_allowance_is_tunable_without_moving_the_zone_floor():
    """The wick allowance is its own threshold, not a reused close percent.

    The zone floor is deliberately built from
    `maximum_close_below_or_high_percent`; how far the *low* may wick beneath
    that floor is a separate decision. Before this was named, the same percent
    was applied twice, so one could not be tuned without moving the other.
    """

    deep_wick = warmup() + [BREAKOUT, bar(10, 55, 10.10, 10.11, 9.93, 10.05)]
    strict = run(deep_wick)
    assert strict.final_state is OrbResearchState.PULLBACK_STRUCTURE_FAILED

    loosened = run(
        deep_wick, config=OrbStrategyConfig(maximum_low_below_zone_lower_percent=0.02)
    )
    assert loosened.final_state is not OrbResearchState.PULLBACK_STRUCTURE_FAILED
    # The frozen zone floor is untouched by that change.
    assert loosened.breakout.zone.lower == pytest.approx(strict.breakout.zone.lower)
    assert loosened.breakout.zone.lower == pytest.approx(
        OR_HIGH * (1 - OrbStrategyConfig().maximum_close_below_or_high_percent)
    )


def test_structural_lower_low_failure_is_terminal():
    collapse = bar(10, 55, 10.10, 10.11, 9.60, 9.62)
    result = run(warmup() + [BREAKOUT, collapse])
    assert RejectionReason.STRUCTURAL_LOWER_LOW_FAILURE in result.rejection_reasons


def test_a_pullback_touching_or_high_is_recorded_as_touching():
    result = run()
    assert result.pullback.touched_or_high


def test_a_pullback_entering_the_breakout_zone_is_recorded():
    result = run()
    assert result.pullback.entered_breakout_zone


def test_maximum_pullback_duration_is_enforced():
    config = OrbStrategyConfig(maximum_pullback_bars=1)
    drift = [
        bar(10, 55, 10.10, 10.11, 10.05, 10.08),
        bar(11, 0, 10.08, 10.09, 10.04, 10.06),
        bar(11, 5, 10.06, 10.07, 10.03, 10.05),
    ]
    result = run(warmup() + [BREAKOUT] + drift, config=config)
    assert RejectionReason.PULLBACK_DURATION_EXCEEDED in result.rejection_reasons


def test_only_the_first_pullback_is_eligible():
    """A second retracement cannot restart the sequence."""

    result = run()
    assert result.pullback.ordinal == 1
    starts = [
        item
        for item in result.transitions
        if item.new_state is OrbResearchState.FIRST_PULLBACK_IN_PROGRESS
    ]
    assert len(starts) == 1


def test_the_breakout_zone_is_frozen_and_never_widened():
    result = run()
    zone = result.breakout.zone
    assert zone.lower < OR_HIGH <= zone.upper
    assert zone.breakout_identity
    assert zone.opening_range_version_identity == (
        result.opening_range_version_identity
    )
    # Re-running cannot move it.
    assert run().breakout.zone == zone


def test_ema_context_is_recorded_but_never_replaces_or_high():
    result = run()
    assert result.pullback.ema9_five_minute is not None
    # The structural decision point stays OR High.
    assert result.reclaim.opening_range_high == OR_HIGH
    assert result.risk.stop_basis == "BELOW_FIRST_PULLBACK_LOW"


# =========================================================================== #
# VOLUME
# =========================================================================== #


def test_lower_selling_volume_is_accepted():
    result = run()
    assert result.pullback.volume_assessed
    assert not result.pullback.price_only
    assert result.pullback.healthy


def test_aggressive_pullback_volume_expansion_is_rejected():
    heavy = replace(PULLBACK_A, volume=9000.0)
    result = run(warmup() + [BREAKOUT, heavy, PULLBACK_B, RECLAIM])
    assert result.final_state is OrbResearchState.PULLBACK_VOLUME_EXPANSION
    assert RejectionReason.SELLING_VOLUME_EXPANSION in result.rejection_reasons


def test_unavailable_volume_becomes_price_only_research_not_a_pass():
    novol = [replace(item, volume=None) for item in healthy_bars()]
    result = run(novol, volume_capability=VolumeCapability.VOLUME_UNAVAILABLE)
    assert result.pullback.price_only
    assert not result.pullback.volume_assessed
    assert result.pullback.pullback_volume_total is None
    assert OrbResearchState.PULLBACK_PRICE_ONLY in states(result)


def test_no_zero_volume_is_ever_fabricated():
    novol = [replace(item, volume=None) for item in healthy_bars()]
    result = run(novol, volume_capability=VolumeCapability.VOLUME_UNAVAILABLE)
    assert result.pullback.pullback_volume_total is None
    assert result.pullback.breakout_bar_volume is None


def test_strict_volume_mode_refuses_to_assess_without_volume():
    config = OrbStrategyConfig(
        volume_requirement_mode=VolumeRequirementMode.REQUIRE_VALID_VOLUME
    )
    novol = [replace(item, volume=None) for item in healthy_bars()]
    result = run(
        novol, config=config, volume_capability=VolumeCapability.VOLUME_UNAVAILABLE
    )
    assert RejectionReason.VOLUME_UNAVAILABLE_AND_REQUIRED in result.rejection_reasons
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH


def test_volume_capability_unavailable_forces_price_only_even_with_numbers():
    result = run(volume_capability=VolumeCapability.VOLUME_UNAVAILABLE)
    assert result.pullback.price_only


# =========================================================================== #
# RECLAIM
# =========================================================================== #


def test_completed_close_reclaim_confirms():
    result = run()
    assert result.reclaim.confirmed
    assert result.reclaim.rule is ReclaimRule.CLOSE_RECLAIMS_OR_HIGH
    assert result.reclaim.trigger_price == pytest.approx(RECLAIM.close)
    assert result.reclaim.confirmation_bar_start_utc == RECLAIM.bar_start_utc


def test_a_touch_alone_is_never_confirmation():
    touch = bar(11, 5, 10.02, 10.05, 9.99, 9.99)
    result = run(warmup() + [BREAKOUT, PULLBACK_A, PULLBACK_B, touch])
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH
    assert RejectionReason.RECLAIM_TRIGGER_NOT_REACHED in result.rejection_reasons


def test_confirmation_expiry_terminates_the_wait():
    config = OrbStrategyConfig(confirmation_expiry_bars=1)
    late = [
        bar(11, 5, 10.00, 10.01, 9.99, 9.995),
        bar(11, 10, 9.995, 10.00, 9.99, 9.995),
        bar(11, 15, 9.995, 10.30, 9.99, 10.25),
    ]
    result = run(warmup() + [BREAKOUT, PULLBACK_A, PULLBACK_B] + late, config=config)
    assert result.final_state is OrbResearchState.RECLAIM_FAILED
    assert RejectionReason.RECLAIM_CONFIRMATION_EXPIRED in result.rejection_reasons


def test_reclaim_after_structural_failure_is_impossible():
    collapse = bar(10, 55, 10.10, 10.11, 9.60, 9.62)
    later_reclaim = bar(11, 30, 9.62, 10.40, 9.60, 10.35)
    result = run(warmup() + [BREAKOUT, collapse, later_reclaim])
    assert result.final_state in {
        OrbResearchState.PULLBACK_STRUCTURE_FAILED,
        OrbResearchState.PULLBACK_TOO_DEEP,
    }
    assert result.final_state in TERMINAL_STATES
    assert result.reclaim is None


@pytest.mark.parametrize(
    "rule",
    [
        ReclaimRule.CLOSE_ABOVE_THEN_PREVIOUS_BAR_HIGH,
        ReclaimRule.REJECTION_BAR_HIGH_BREAK,
    ],
)
def test_an_unimplemented_reclaim_rule_is_refused_not_silently_defaulted(rule):
    """A declared-but-unbuilt rule must fail loudly.

    Both of these once evaluated to `close > OR High` — exactly the default
    rule — so selecting them changed the recorded rule name without changing a
    single decision.
    """

    with pytest.raises(ValueError, match="not implemented"):
        OrbStrategyConfig(reclaim_rule=rule)


def test_only_one_reclaim_rule_is_implemented():
    assert IMPLEMENTED_RECLAIM_RULES == {ReclaimRule.CLOSE_RECLAIMS_OR_HIGH}
    assert OrbStrategyConfig().reclaim_rule is ReclaimRule.CLOSE_RECLAIMS_OR_HIGH


def test_the_engine_refuses_an_unimplemented_rule_even_if_config_is_bypassed():
    engine = OrbResearchEngine()
    object.__setattr__(
        engine.config, "reclaim_rule", ReclaimRule.REJECTION_BAR_HIGH_BREAK
    )
    with pytest.raises(ValueError, match="not implemented"):
        engine.evaluate(
            context(),
            CompletedBarSequence(
                one_minute=(), five_minute=tuple(healthy_bars()), as_of_utc=at(12, 0)
            ),
        )


def test_a_touch_is_never_confirmation_by_configuration_either():
    with pytest.raises(ValueError, match="touch is never confirmation"):
        OrbStrategyConfig(require_completed_bar=False)


def test_no_intra_bar_repainting_is_exposed_as_confirmation():
    """Only a bar already closed at as_of may confirm."""

    result = run(as_of=RECLAIM.bar_end_utc - timedelta(microseconds=1))
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH
    assert run(as_of=RECLAIM.bar_end_utc).final_state is (
        OrbResearchState.ENTRY_READY_RESEARCH
    )


# =========================================================================== #
# RISK AND TARGETS
# =========================================================================== #


def test_structural_stop_sits_below_the_pullback_low():
    result = run()
    assert result.risk.proposed_stop < result.pullback.low
    assert result.risk.raw_pullback_low == pytest.approx(result.pullback.low)
    assert result.risk.stop_basis == "BELOW_FIRST_PULLBACK_LOW"


def test_atr_buffer_is_used_when_atr_is_available():
    result = run()
    assert result.risk.buffer_basis == "INTRADAY_ATR_BUFFER"
    assert result.risk.buffer_applied > 0


def test_percentage_buffer_is_used_and_labelled_when_atr_is_missing():
    bars = [BREAKOUT, PULLBACK_A, PULLBACK_B, RECLAIM]
    result = run(bars)
    assert result.breakout.intraday_atr.status is not IntradayAtrStatus.AVAILABLE
    assert result.risk.buffer_basis == "PERCENT_BUFFER_ATR_UNAVAILABLE"
    assert result.risk.stop_distance_atr is None


def test_daily_atr_is_never_substituted_for_intraday_atr():
    result = run(
        [BREAKOUT, PULLBACK_A, PULLBACK_B, RECLAIM],
        daily=DailyContext(available=True, daily_atr14=5.0, resistance_levels=(11.5,)),
    )
    assert result.breakout.intraday_atr.value is None
    assert result.risk.buffer_basis == "PERCENT_BUFFER_ATR_UNAVAILABLE"


def test_stop_at_or_above_trigger_is_rejected():
    from scalping_orb.indicators import IntradayAtr

    engine = OrbResearchEngine(
        OrbStrategyConfig(stop_atr_buffer=0.0, stop_percent_buffer=0.0)
    )
    unavailable = IntradayAtr(IntradayAtrStatus.UNAVAILABLE, None, 5, 14, 0)
    risk = engine._structural_risk(10.00, 10.50, unavailable)
    assert not risk.valid
    assert RejectionReason.STOP_NOT_BELOW_TRIGGER in risk.rejection_reasons


def test_a_missing_structural_reference_is_rejected():
    from scalping_orb.indicators import IntradayAtr

    engine = OrbResearchEngine()
    risk = engine._structural_risk(
        10.0, None, IntradayAtr(IntradayAtrStatus.UNAVAILABLE, None, 5, 14, 0)
    )
    assert not risk.valid
    assert RejectionReason.STOP_REFERENCE_MISSING in risk.rejection_reasons


def test_excessive_stop_distance_is_rejected():
    config = OrbStrategyConfig(maximum_stop_distance_percent=0.001)
    result = run(config=config)
    assert RejectionReason.STOP_DISTANCE_EXCEEDED in result.rejection_reasons
    assert result.final_state is OrbResearchState.RECLAIM_FAILED


def test_one_r_and_two_r_targets_are_exact():
    result = run()
    risk = result.targets.risk_per_share
    trigger = result.targets.trigger_price
    assert result.targets.target_1 == pytest.approx(trigger + risk)
    assert result.targets.target_2 == pytest.approx(trigger + 2 * risk)


def test_resistance_before_target_one_degrades_the_setup():
    # minimum_initial_reward_risk is relaxed so only the target-stage gate can fire.
    result = run(
        config=OrbStrategyConfig(minimum_initial_reward_risk=0.1),
        daily=DailyContext(available=True, resistance_levels=(10.20,)),
    )
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH
    assert RejectionReason.DAILY_RESISTANCE_TOO_CLOSE in result.rejection_reasons


def test_minimum_reward_risk_is_enforced():
    config = OrbStrategyConfig(minimum_reward_risk=5.0)
    result = run(config=config)
    assert RejectionReason.REWARD_RISK_BELOW_MINIMUM in result.rejection_reasons
    assert result.final_state is OrbResearchState.RECLAIM_FAILED


def test_reward_risk_is_not_tautologically_one():
    """Measuring reward to target 1 would make the gate a no-op."""

    result = run()
    assert result.targets.effective_reward_risk == pytest.approx(2.0)


def _rr_at(resistance):
    """Effective R/R for the healthy setup with one D-1 resistance level."""

    return run(
        config=OrbStrategyConfig(minimum_initial_reward_risk=0.1),
        daily=DailyContext(available=True, resistance_levels=(resistance,)),
    )


def test_effective_reward_risk_is_measured_to_the_reachable_target():
    """Resistance between 1R and 2R must produce a value strictly between."""

    base = run()
    trigger, risk = base.targets.trigger_price, base.targets.risk_per_share
    midway = _rr_at(trigger + 1.7 * risk)
    assert midway.targets.effective_reward_risk == pytest.approx(1.7)
    assert midway.targets.usable_target == pytest.approx(trigger + 1.7 * risk)
    assert midway.targets.target_1 == pytest.approx(trigger + risk)
    assert midway.targets.target_2 == pytest.approx(trigger + 2 * risk)


def test_the_minimum_reward_risk_gate_is_reachable_at_its_exact_boundary():
    """1.5 exactly passes; a hair under it fails. The gate is not decorative."""

    base = run()
    trigger, risk = base.targets.trigger_price, base.targets.risk_per_share
    minimum = OrbStrategyConfig().minimum_reward_risk

    on_boundary = _rr_at(trigger + minimum * risk)
    assert on_boundary.targets.effective_reward_risk == pytest.approx(minimum)
    assert on_boundary.targets.meets_minimum_reward_risk
    assert RejectionReason.REWARD_RISK_BELOW_MINIMUM not in (
        on_boundary.rejection_reasons
    )

    under = _rr_at(trigger + (minimum - 0.05) * risk)
    assert not under.targets.meets_minimum_reward_risk
    assert RejectionReason.REWARD_RISK_BELOW_MINIMUM in under.rejection_reasons
    assert under.final_state is OrbResearchState.RECLAIM_FAILED


def test_without_known_resistance_the_ratio_is_exactly_the_target_2_multiple():
    """An honest limit of the model, pinned so it cannot drift unnoticed.

    With no D-1 resistance there is nothing to cap the reward, so the ratio is
    always `target_2_r_multiple`. The gate can then only bite through a
    configured minimum above that multiple — never through market structure.
    """

    result = run(daily=DailyContext(available=True, resistance_levels=()))
    assert result.targets.nearest_daily_resistance is None
    assert result.targets.effective_reward_risk == pytest.approx(
        OrbStrategyConfig().target_2_r_multiple
    )
    assert OrbStrategyConfig().minimum_reward_risk < (
        OrbStrategyConfig().target_2_r_multiple
    )


def test_no_resistance_is_invented_when_d1_context_is_absent():
    result = run(daily=DailyContext(available=False))
    assert result.targets.nearest_daily_resistance is None
    assert result.targets.reward_before_resistance is None
    assert result.breakout.distance_to_daily_resistance is None


def test_no_position_size_is_ever_produced():
    result = run()
    for field in ("quantity", "size", "shares", "position_size", "notional"):
        assert not hasattr(result.targets, field)
        assert not hasattr(result.risk, field)


# =========================================================================== #
# STATE MACHINE
# =========================================================================== #


def test_the_exact_transition_order_of_a_healthy_setup():
    assert states(run()) == [
        OrbResearchState.WAIT_BREAKOUT,
        OrbResearchState.BREAKOUT_CANDIDATE,
        OrbResearchState.MOMENTUM_QUALIFIED_RESEARCH,
        OrbResearchState.WAIT_FIRST_PULLBACK,
        OrbResearchState.FIRST_PULLBACK_IN_PROGRESS,
        OrbResearchState.PULLBACK_HEALTHY,
        OrbResearchState.WAIT_RECLAIM,
        OrbResearchState.RECLAIM_CANDIDATE,
        OrbResearchState.ENTRY_READY_RESEARCH,
    ]


def test_no_required_state_is_skipped_before_readiness():
    sequence = states(run())
    for required in (
        OrbResearchState.BREAKOUT_CANDIDATE,
        OrbResearchState.WAIT_FIRST_PULLBACK,
        OrbResearchState.FIRST_PULLBACK_IN_PROGRESS,
        OrbResearchState.WAIT_RECLAIM,
        OrbResearchState.RECLAIM_CANDIDATE,
    ):
        assert required in sequence
        assert sequence.index(required) < sequence.index(
            OrbResearchState.ENTRY_READY_RESEARCH
        )


def test_transition_identity_is_deterministic_and_unique():
    first, second = run(), run()
    assert [t.transition_id for t in first.transitions] == [
        t.transition_id for t in second.transitions
    ]
    ids = [t.transition_id for t in first.transitions]
    assert len(set(ids)) == len(ids)


def test_every_transition_records_the_full_required_evidence():
    for transition in run().transitions:
        assert transition.transition_id
        assert transition.canonical_ticker == "TEST"
        assert transition.session_date == DAY
        assert transition.opening_range_version_identity
        assert transition.opening_range_revision == 0
        assert transition.as_of_timestamp_utc.tzinfo is timezone.utc
        assert transition.rule_code
        assert transition.market_time_status
        assert transition.historical_replay_status
        assert transition.live_freshness_status
        assert transition.live_decision_capability
        assert transition.evaluation_mode
        assert transition.strategy_fingerprint
        assert transition.engine_version == ENGINE_VERSION


def test_an_illegal_transition_raises_rather_than_persisting():
    with pytest.raises(ValueError, match="illegal ORB transition"):
        assert_legal_transition(
            OrbResearchState.WAIT_BREAKOUT, OrbResearchState.ENTRY_READY_RESEARCH
        )


def test_no_transition_may_leave_a_terminal_state():
    for state in TERMINAL_STATES:
        assert state not in LEGAL_TRANSITIONS
        with pytest.raises(ValueError, match="terminal"):
            assert_legal_transition(state, OrbResearchState.WAIT_BREAKOUT)


def test_the_evaluation_binds_to_the_exact_opening_range_version():
    original = run()
    corrected = run(opening_range=opening_range(revision=1, high=10.4))
    assert original.opening_range_version_identity != (
        corrected.opening_range_version_identity
    )
    assert original.opening_range_revision == 0
    assert corrected.opening_range_revision == 1


def test_a_corrected_range_does_not_mutate_the_earlier_evaluation():
    original = run()
    fingerprint = original.evidence_fingerprint
    run(opening_range=opening_range(revision=1, high=10.4))
    assert original.evidence_fingerprint == fingerprint
    assert run().evidence_fingerprint == fingerprint


def test_default_version_mode_is_decision_time_original():
    assert run().opening_range_version_mode is (
        OpeningRangeVersionMode.DECISION_TIME_ORIGINAL_VERSION
    )


# =========================================================================== #
# TIME
# =========================================================================== #


def test_no_evaluation_advances_before_the_opening_range_is_ready():
    result = run(
        opening_range=opening_range(status=OpeningRangeStatus.BUILDING, high=None)
    )
    assert result.final_state is OrbResearchState.DATA_UNAVAILABLE
    assert result.breakout is None


def test_the_late_research_entry_cutoff_stops_new_candidates():
    config = OrbStrategyConfig(latest_research_entry_time=time(10, 45))
    result = run(config=config)
    assert OrbResearchState.LATE_SESSION in states(result)
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH


def test_the_auction_transition_happens_at_the_configured_start():
    auction_bar = bar(
        14, 15, 10.0, 10.5, 9.9, 10.4, phase=OrbSessionPhase.CLOSING_AUCTION
    )
    result = run(warmup() + [auction_bar], as_of=at(14, 30))
    assert result.final_state is OrbResearchState.AUCTION_PHASE
    assert RejectionReason.AUCTION_PHASE_REACHED in result.rejection_reasons


def test_an_auction_bar_cannot_contribute_to_any_continuous_state():
    """Appending an auction bar must change nothing at all."""

    auction_bar = bar(
        14, 15, 10.0, 10.5, 9.9, 10.4, phase=OrbSessionPhase.CLOSING_AUCTION
    )
    without = run(healthy_bars(), as_of=at(14, 30))
    with_auction = run(healthy_bars() + [auction_bar], as_of=at(14, 30))
    assert with_auction.evidence_fingerprint == without.evidence_fingerprint
    assert with_auction.breakout.close == BREAKOUT.close
    assert all(
        transition.exchange_timestamp_utc is None
        or transition.exchange_timestamp_utc < at(14, 15)
        for transition in with_auction.transitions
    )


def test_the_expiry_time_terminates_the_activation():
    config = OrbStrategyConfig(
        latest_research_entry_time=time(11, 0), expiry_time=time(11, 5)
    )
    result = run(config=config)
    assert result.final_state in {
        OrbResearchState.ENTRY_EXPIRED,
        OrbResearchState.LATE_SESSION,
    }


def test_auction_start_is_the_continuous_session_end():
    config = OrbStrategyConfig()
    assert config.auction_start == config.data.continuous_end
    assert config.continuous_session_end == config.data.continuous_end


# =========================================================================== #
# LIVE VERSUS HISTORICAL
# =========================================================================== #


def test_live_modes_fail_closed_unless_the_symbol_is_demonstrably_fresh():
    """The default is still closed. Only an explicit capability opens it.

    This replaces an assertion that live could *never* reach readiness. It could
    not, because the rejection was appended without reading a quote age at all —
    which is how 2026-08-05 rejected 6,847 live breakouts whose median receive
    lag was 0.818 s against a 60 s budget.
    """

    for mode in (EvaluationMode.SHADOW_LIVE, EvaluationMode.LIVE_DISABLED):
        result = run(evaluation_mode=mode)
        assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH
        assert (
            RejectionReason.LIVE_DECISION_DISABLED_FRESHNESS
            in result.rejection_reasons
        )


def test_fresh_live_evidence_can_reach_research_readiness():
    result = run(
        evaluation_mode=EvaluationMode.SHADOW_LIVE,
        live_freshness_status=LiveFreshnessStatus.LIVE_FRESHNESS_PASSED,
        live_decision_capability=(
            LiveDecisionCapability.LIVE_DECISION_ENABLED_RESEARCH_ONLY
        ),
    )
    assert result.final_state is OrbResearchState.ENTRY_READY_RESEARCH
    assert (
        RejectionReason.LIVE_DECISION_DISABLED_FRESHNESS
        not in result.rejection_reasons
    )


def test_stale_live_evidence_still_cannot_reach_readiness():
    """Every disabled capability still refuses, exactly as before."""

    for capability in LiveDecisionCapability:
        if capability is LiveDecisionCapability.LIVE_DECISION_ENABLED_RESEARCH_ONLY:
            continue
        result = run(
            evaluation_mode=EvaluationMode.SHADOW_LIVE,
            live_freshness_status=LiveFreshnessStatus.LIVE_FRESHNESS_FAILED,
            live_decision_capability=capability,
        )
        assert result.final_state is OrbResearchState.BREAKOUT_REJECTED_STALE
        assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH


def test_live_disabled_mode_stays_closed_even_when_the_symbol_is_fresh():
    """`LIVE_DISABLED` means "do not decide". Fresh data does not override it."""

    result = run(
        evaluation_mode=EvaluationMode.LIVE_DISABLED,
        live_freshness_status=LiveFreshnessStatus.LIVE_FRESHNESS_PASSED,
        live_decision_capability=(
            LiveDecisionCapability.LIVE_DECISION_ENABLED_RESEARCH_ONLY
        ),
    )
    assert result.final_state is not OrbResearchState.ENTRY_READY_RESEARCH
    assert (
        RejectionReason.LIVE_DECISION_DISABLED_FRESHNESS in result.rejection_reasons
    )


def test_the_only_enabled_capability_is_research_only():
    """Exactly one enabled member, and it authorises research readiness only."""

    enabled = [
        member
        for member in LiveDecisionCapability
        if not member.value.startswith("LIVE_DECISION_DISABLED")
    ]
    assert enabled == [LiveDecisionCapability.LIVE_DECISION_ENABLED_RESEARCH_ONLY]
    assert enabled[0].value.endswith("RESEARCH_ONLY")
    for member in LiveDecisionCapability:
        for forbidden in ("ORDER", "EXECUTE", "EXECUTION", "TRADE", "BUY", "SELL"):
            assert forbidden not in member.value.upper()


def test_historical_replay_is_the_default_mode():
    assert run().evaluation_mode is EvaluationMode.HISTORICAL_REPLAY


# =========================================================================== #
# CONFIGURATION
# =========================================================================== #


def test_config_round_trips_and_is_fingerprinted():
    config = OrbStrategyConfig()
    restored = OrbStrategyConfig.from_mapping(config.as_dict())
    assert restored.strategy_fingerprint == config.strategy_fingerprint
    assert restored.reclaim_rule is config.reclaim_rule
    assert restored.data.fingerprint == config.data.fingerprint


def test_defaults_are_labelled_as_initial_research_defaults():
    assert OrbStrategyConfig().defaults_provenance == "INITIAL_RESEARCH_DEFAULTS"


def test_changing_a_threshold_changes_the_fingerprint():
    assert (
        OrbStrategyConfig(minimum_reward_risk=2.0).strategy_fingerprint
        != OrbStrategyConfig().strategy_fingerprint
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum_reward_risk": 0},
        {"target_1_r_multiple": 3.0},
        {"maximum_pullback_depth_percent": 0.0005},
        {"stop_atr_buffer": -1},
        {"maximum_stop_distance_percent": 2.0},
        {"earliest_breakout_time": time(9, 0)},
        {"latest_research_entry_time": time(14, 20)},
        {"breakout_confirmation_interval_minutes": 3},
        {"intraday_atr_minimum_bars": 99},
    ],
)
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        OrbStrategyConfig(**kwargs)


def test_phase_2a_session_identity_is_not_disturbed():
    """Phase 2B config must not change the Phase 2A fingerprint."""

    from scalping_orb.config import OrbDataConfig

    assert OrbStrategyConfig().data.fingerprint == OrbDataConfig().fingerprint


# =========================================================================== #
# INDICATORS
# =========================================================================== #


def test_intraday_atr_warms_up_before_it_reports():
    short = intraday_atr(
        warmup()[:2], interval_minutes=5, lookback_bars=14, minimum_bars=6
    )
    assert short.status is IntradayAtrStatus.WARMING_UP
    assert short.value is None


def test_intraday_atr_is_available_after_warm_up():
    ready = intraday_atr(
        warmup(), interval_minutes=5, lookback_bars=14, minimum_bars=6
    )
    assert ready.status is IntradayAtrStatus.AVAILABLE
    assert ready.value > 0


def test_ema_returns_none_before_its_period_fills():
    assert exponential_moving_average(warmup()[:3], period=9, interval_minutes=5) is None


def test_indicators_never_consult_a_later_bar():
    early = intraday_atr(
        warmup()[:6], interval_minutes=5, lookback_bars=14, minimum_bars=6
    )
    late = intraday_atr(
        warmup(), interval_minutes=5, lookback_bars=14, minimum_bars=6
    )
    assert early.observed_bars == 6 and late.observed_bars == 7


# =========================================================================== #
# PERSISTENCE
# =========================================================================== #


def _session(repository):
    repository.ensure_session("session", DAY, "config-hash")
    return "session"


def test_migration_four_creates_every_phase_2b_table(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    # Later phases add their own migrations, so pin what this test is about:
    # migration 4 has been applied and its tables exist.
    assert repository.database_status()["user_version"] >= 4
    with repository.connect() as connection:
        applied = {
            row[0]
            for row in connection.execute("SELECT version FROM orb_schema_meta")
        }
    assert 4 in applied
    for table in (
        "orb_candidates",
        "orb_state_transitions",
        "orb_breakouts",
        "orb_pullbacks",
        "orb_reclaims",
        "orb_research_setups",
    ):
        assert repository.table_count(table) == 0


def test_migration_three_to_four_is_in_place(tmp_path):
    path = tmp_path / "orb.db"
    old = OrbResearchRepository(path, target_schema_version=3)
    _session(old)
    assert old.database_status()["user_version"] == 3
    # Pinned to 4 so this keeps testing the v3 -> v4 hop specifically, rather
    # than whatever the newest schema version happens to be.
    upgraded = OrbResearchRepository(path, target_schema_version=4)
    assert upgraded.database_status()["user_version"] == 4
    assert upgraded.table_count("orb_sessions") == 1
    assert upgraded.table_count("orb_candidates") == 0


# --- real v3 -> v4 upgrade over representative Phase 2A rows ---------------

PHASE_2A_TABLES = (
    "orb_sessions",
    "orb_normalized_events",
    "orb_bars",
    "orb_opening_ranges",
    "orb_data_quality_events",
    "orb_capabilities",
    "orb_collection_runs",
)


def _seed_phase_2a(path):
    """A v3 database carrying real ingested Phase 2A rows, not an empty shell."""

    from scalping_orb.config import OrbDataConfig
    from scalping_orb.events import RubixQuoteInput
    from scalping_orb.shadow import OrbShadowIngestionService

    repository = OrbResearchRepository(path, target_schema_version=3)
    config = OrbDataConfig(research_database_path=str(path))
    service = OrbShadowIngestionService(
        repository, config, holidays=(), mapping_validator=lambda canonical, rubix: True
    )
    quotes = []
    sequence = 0
    for ticker in ("TEST", "OTHER"):
        for minute in range(0, 30):
            sequence += 1
            moment = at(10, 0) + timedelta(minutes=minute)
            quotes.append(
                RubixQuoteInput(
                    canonical_ticker=ticker,
                    verified_rubix_symbol=f"CASE~{ticker}",
                    market_timestamp=moment,
                    receive_timestamp=moment + timedelta(milliseconds=10),
                    sequence=sequence,
                    last_price=10.0 + minute * 0.01,
                    cumulative_volume=100.0 * (minute + 1),
                    bid=9.99,
                    ask=10.01,
                    has_feed_timestamp=True,
                    source_row_id=sequence,
                )
            )
    service.ingest(quotes, evaluated_at=at(14, 15))
    return repository


def _table_snapshot(repository):
    with repository.connect() as connection:
        return {
            table: (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0],
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0],
            )
            for table in PHASE_2A_TABLES
        }


def test_upgrading_a_populated_v3_database_loses_no_phase_2a_row(tmp_path):
    path = tmp_path / "orb.db"
    old = _seed_phase_2a(path)
    assert old.database_status()["user_version"] == 3
    before = _table_snapshot(old)
    assert before["orb_normalized_events"][0] > 0
    assert before["orb_bars"][0] > 0
    assert before["orb_opening_ranges"][0] > 0

    upgraded = OrbResearchRepository(path, target_schema_version=4)
    assert upgraded.database_status()["user_version"] == 4
    after = _table_snapshot(upgraded)

    # Identical row counts *and* identical CREATE TABLE text: additive only.
    assert after == before
    for table in ("orb_candidates", "orb_state_transitions", "orb_breakouts",
                  "orb_pullbacks", "orb_reclaims", "orb_research_setups"):
        assert upgraded.table_count(table) == 0


def test_the_upgraded_database_keeps_wal_and_the_busy_timeout(tmp_path):
    path = tmp_path / "orb.db"
    _seed_phase_2a(path)
    upgraded = OrbResearchRepository(path)
    status = upgraded.database_status()
    assert status["journal_mode"] == "WAL"
    assert status["foreign_keys"] is True
    with upgraded.connect() as connection:
        assert int(connection.execute("PRAGMA busy_timeout").fetchone()[0]) == 30000
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repeating_the_migration_changes_nothing(tmp_path):
    path = tmp_path / "orb.db"
    _seed_phase_2a(path)
    first = OrbResearchRepository(path)
    snapshot = _table_snapshot(first)
    with first.connect() as connection:
        meta = connection.execute(
            "SELECT version,migration_name,checksum FROM orb_schema_meta ORDER BY version"
        ).fetchall()
    for _ in range(3):
        again = OrbResearchRepository(path)
        again.migrate()
    final = OrbResearchRepository(path)
    assert final.database_status()["user_version"] == SCHEMA_VERSION
    assert _table_snapshot(final) == snapshot
    with final.connect() as connection:
        assert [tuple(row) for row in connection.execute(
            "SELECT version,migration_name,checksum FROM orb_schema_meta ORDER BY version"
        )] == [tuple(row) for row in meta]


def test_a_failing_migration_rolls_back_atomically(tmp_path, monkeypatch):
    """An injected failure must leave the v3 database exactly as it was."""

    import scalping_orb.repository as repository_module

    path = tmp_path / "orb.db"
    old = _seed_phase_2a(path)
    before = _table_snapshot(old)

    broken = dict(repository_module.MIGRATIONS)
    broken[4] = ("phase2b_core_research_evidence", "CREATE TABLE not valid sql (;")
    monkeypatch.setattr(repository_module, "MIGRATIONS", broken)

    with pytest.raises(sqlite3.Error):
        OrbResearchRepository(path)

    monkeypatch.undo()
    recovered = OrbResearchRepository(path, target_schema_version=3)
    assert recovered.database_status()["user_version"] == 3
    assert _table_snapshot(recovered) == before
    with recovered.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "orb_candidates" not in names


def test_phase_2b_tables_hold_research_evidence_only(tmp_path):
    """No order, execution, position, trade, P&L or broker column anywhere."""

    repository = OrbResearchRepository(tmp_path / "orb.db")
    forbidden = (
        "order", "execution", "position", "trade", "pnl", "profit", "loss",
        "broker", "fill", "quantity", "shares", "commission", "equity",
    )
    with repository.connect() as connection:
        for table in ("orb_candidates", "orb_state_transitions", "orb_breakouts",
                      "orb_pullbacks", "orb_reclaims", "orb_research_setups"):
            columns = [
                row[1].lower()
                for row in connection.execute(f"PRAGMA table_info({table})")
            ]
            for column in columns:
                assert not any(word in column for word in forbidden), (
                    f"{table}.{column} is not research evidence"
                )


def test_no_execution_table_exists(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    with repository.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    for forbidden in ("orders", "executions", "positions", "trades", "pnl", "fills"):
        assert not any(forbidden in name.lower() for name in names)


def test_persisting_a_research_evaluation_stores_all_evidence(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    session_id = _session(repository)
    evaluation = run()
    candidate_id = repository.persist_research_evaluation(session_id, evaluation)
    assert candidate_id == evaluation.candidate_identity
    assert repository.table_count("orb_candidates") == 1
    assert repository.table_count("orb_state_transitions") == len(evaluation.transitions)
    assert repository.table_count("orb_breakouts") == 1
    assert repository.table_count("orb_pullbacks") == 1
    assert repository.table_count("orb_reclaims") == 1
    assert repository.table_count("orb_research_setups") == 1


def test_replaying_the_same_evaluation_is_idempotent(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    session_id = _session(repository)
    evaluation = run()
    repository.persist_research_evaluation(session_id, evaluation)
    counts = {
        table: repository.table_count(table)
        for table in (
            "orb_candidates",
            "orb_state_transitions",
            "orb_breakouts",
            "orb_pullbacks",
            "orb_reclaims",
            "orb_research_setups",
        )
    }
    for _ in range(3):
        repository.persist_research_evaluation(session_id, run())
    assert {
        table: repository.table_count(table) for table in counts
    } == counts


def test_the_service_loads_one_symbol_not_the_whole_session(tmp_path):
    """Regression: a full-session reload per symbol is the replay bottleneck.

    Filtering in Python after `load_events(session_id)` re-reads every row once
    per symbol — quadratic in a 230-symbol session. The query must be filtered
    in SQL, where the (session_id, canonical_ticker, market_timestamp_utc)
    index serves it.
    """

    path = tmp_path / "orb.db"
    _seed_phase_2a(path)
    repository = OrbResearchRepository(path)
    with repository.connect() as connection:
        session_id, total_events = connection.execute(
            "SELECT session_id, count(*) FROM orb_normalized_events GROUP BY session_id"
        ).fetchone()

    calls: list[tuple] = []
    original = OrbResearchRepository.load_events

    def counting(self, session, canonical_ticker=None):
        rows = original(self, session, canonical_ticker)
        calls.append((canonical_ticker, len(rows)))
        return rows

    service = OrbResearchService(repository, OrbStrategyConfig())
    OrbResearchRepository.load_events = counting
    try:
        for ticker in ("TEST", "OTHER"):
            service.evaluate_symbol(session_id, ticker, as_of=at(14, 15))
    finally:
        OrbResearchRepository.load_events = original

    assert calls, "the service never loaded any event"
    # Every load is scoped to one symbol...
    assert all(ticker is not None for ticker, _ in calls)
    # ...so the whole session is read once in total, not once per symbol.
    assert sum(count for _, count in calls) <= total_events


def test_state_history_is_preserved_in_order(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    session_id = _session(repository)
    evaluation = run()
    candidate_id = repository.persist_research_evaluation(session_id, evaluation)
    stored = repository.load_state_transitions(candidate_id)
    assert [row["new_state"] for row in stored] == [
        item.new_state.value for item in evaluation.transitions
    ]
    assert [row["sequence_index"] for row in stored] == list(range(len(stored)))


def test_a_research_setup_is_only_stored_for_a_ready_candidate(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    session_id = _session(repository)
    rejected = run(config=OrbStrategyConfig(minimum_reward_risk=9.0))
    assert rejected.final_state is not OrbResearchState.ENTRY_READY_RESEARCH
    repository.persist_research_evaluation(session_id, rejected)
    assert repository.table_count("orb_research_setups") == 0


def test_a_different_opening_range_version_is_a_different_candidate(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    session_id = _session(repository)
    repository.persist_research_evaluation(session_id, run())
    repository.persist_research_evaluation(
        session_id, run(opening_range=opening_range(revision=1, high=10.05))
    )
    assert repository.table_count("orb_candidates") == 2


def test_research_setups_are_flagged_research_only(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    session_id = _session(repository)
    repository.persist_research_evaluation(session_id, run())
    with repository.connect() as connection:
        assert connection.execute(
            "SELECT research_only FROM orb_research_setups"
        ).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE orb_research_setups SET research_only=0"
            )


def test_the_read_only_repository_refuses_to_write(tmp_path):
    path = tmp_path / "orb.db"
    OrbResearchRepository(path)
    reader = OrbResearchRepository(path, read_only=True)
    assert reader.table_count("orb_candidates") == 0
    with pytest.raises(Exception):
        reader.persist_research_evaluation("session", run())


# =========================================================================== #
# SERVICE BOUNDARY
# =========================================================================== #


def test_the_service_binds_to_the_original_version_by_default(tmp_path):
    from scalping_orb.opening_range import OpeningRangeResult

    repository = OrbResearchRepository(tmp_path / "orb.db")
    session_id = _session(repository)
    frozen = OpeningRangeResult(
        canonical_ticker="TEST",
        session_date=DAY,
        status=OpeningRangeStatus.READY,
        opening_range_high=OR_HIGH,
        opening_range_low=OR_LOW,
        opening_range_mid=9.75,
        width_absolute=0.5,
        width_percent=5.0,
        completed_one_minute_bar_count=15,
        expected_one_minute_bar_count=15,
        coverage_ratio=1.0,
        valid_volume_total=1000.0,
        first_valid_timestamp_utc=at(10, 0),
        last_valid_timestamp_utc=at(10, 15),
        data_quality_flags=(),
        frozen_at_utc=at(10, 15),
        source_identity="or-source",
    )
    repository.insert_opening_range(session_id, frozen)
    service = OrbResearchService(repository)
    evaluation = service.evaluate_and_persist(
        session_id,
        "TEST",
        as_of=at(12, 0),
        one_minute=(),
        five_minute=tuple(healthy_bars()),
        daily=DailyContext(available=True, resistance_levels=(11.5,)),
    )
    assert evaluation is not None
    assert evaluation.opening_range_revision == 0
    assert evaluation.final_state is OrbResearchState.ENTRY_READY_RESEARCH
    assert repository.table_count("orb_candidates") == 1


def test_the_service_carries_no_collector_alert_or_order_surface():
    import inspect

    from scalping_orb import engine, strategy_service

    for module in (engine, strategy_service):
        source = _code_only(inspect.getsource(module)).lower()
        for forbidden in (
            "websocket",
            "authenticate",
            "api_key",
            "place_order",
            "submit_order",
            "send_alert",
            "paper_trade",
            "yfinance",
            "yahoo",
            "requests.post",
            "urllib",
        ):
            assert forbidden not in source, f"{module.__name__}: {forbidden}"


def test_the_engine_reaches_no_provider_or_database():
    import inspect

    from scalping_orb import engine

    source = _code_only(inspect.getsource(engine))
    for forbidden in ("sqlite3", "OrbResearchRepository", "load_history", "open("):
        assert forbidden not in source, forbidden
