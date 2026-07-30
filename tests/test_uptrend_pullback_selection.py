"""Acceptance tests for the UPTREND_PULLBACK_SCALPING historical selector.

Every fixture is a completed daily OHLCV frame with EODHD provenance. No test
uses Yahoo data, a live quote, a current incomplete candle, or Rubix movement.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from scalping_uptrend_pullback.config import (
    PullbackProximityConfig,
    ShortTermTrendConfig,
    SupportZoneConfig,
    UptrendPullbackScoreWeights,
    UptrendPullbackSelectionConfig,
)
from scalping_uptrend_pullback.selection import (
    _resolve_state,
    analyze_uptrend_pullback,
    build_frozen_uptrend_watchlist,
)
from scalping_uptrend_pullback.states import (
    DATA_UNAVAILABLE,
    EMA5_SLOPE_FAILED,
    EMA_ALIGNMENT_FAILED,
    INSUFFICIENT_HISTORY,
    INSUFFICIENT_LIQUIDITY,
    PROVENANCE_REJECTED,
    REASON_LAST_CLOSE_BELOW_EMA10,
    REASON_PULLBACK_TOO_DEEP,
    REASON_SUPPORT_INVALIDATED,
    REASON_SUPPORT_NO_SOURCES,
    REASON_TREND_TOO_YOUNG,
    SUPPORT_BROKEN,
    SUPPORT_NOT_CONFIRMED,
    SUPPORT_SOURCE_EMA10,
    TREND_STRUCTURE_FAILED,
    UPTREND_EXTENDED_NO_CHASE,
    UPTREND_NEAR_SUPPORT,
    UPTREND_PULLBACK_TOO_DEEP,
    UPTREND_WAIT_FOR_PULLBACK,
)


CUTOFF = "2026-07-27"
GENERATED = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)
CONFIG = UptrendPullbackSelectionConfig()


def _frame(closes, *, wick=0.01, volume=500_000, provider="eodhd", end=CUTOFF):
    closes = np.asarray(closes, dtype=float)
    frame = pd.DataFrame(
        {
            "Open": closes,
            "High": closes * (1.0 + wick),
            "Low": closes * (1.0 - wick),
            "Close": closes,
            "Volume": float(volume),
        },
        index=pd.bdate_range(end=end, periods=len(closes)),
    )
    frame.attrs["market_data"] = {
        "provider": provider,
        "volume_safe_for_lookback": True,
        "corporate_action_policy_version": "test",
    }
    return frame


def _shelf(sessions=22, level=20.0):
    return list(level + 0.12 * np.sin(np.arange(sessions)))


def _pullback_to_support():
    """Consolidation, breakout, controlled pullback back onto the confluence."""

    return (
        _shelf()
        + list(np.linspace(20.25, 21.60, 18))
        + list(np.linspace(21.55, 20.62, 12))
        + list(np.linspace(20.66, 20.95, 8))
    )


def _extended():
    """Same setup, one violent session that leaves price far above support."""

    return _pullback_to_support() + [23.45]


def _waiting():
    return _pullback_to_support() + [21.80, 22.67]


def _dipped_below_ema10():
    return _pullback_to_support() + [23.45, 20.90]


def _young_trend():
    return list(np.linspace(20.0, 19.6, 54)) + list(np.linspace(19.75, 21.50, 6))


# --- The intended candidate --------------------------------------------------


def test_pullback_onto_confluence_is_the_eligible_candidate():
    result = analyze_uptrend_pullback(
        "AAA", _frame(_pullback_to_support()), data_cutoff=CUTOFF
    )

    assert result.candidate_state == UPTREND_NEAR_SUPPORT
    assert result.eligible is True
    assert result.strategy_identity == "UPTREND_PULLBACK_SCALPING_V1"
    assert result.trend.fast_ema > result.trend.slow_ema
    assert result.trend.fast_ema_slope_percent_per_session > 0
    assert result.trend.slow_ema_slope_percent_per_session > 0
    assert 0 <= result.distance_from_support_percent <= (
        CONFIG.pullback.near_support_maximum_percent
    )
    assert result.support.confirmed is True
    assert len(result.support.source_types) >= (
        CONFIG.support.minimum_support_sources
    )
    assert result.support.lower <= result.support.centre <= result.support.upper
    assert result.invalidation_level < result.support.lower
    assert result.last_close > result.invalidation_level
    assert 0 < result.total_score <= 100


def test_candidate_output_exposes_the_full_research_contract():
    result = analyze_uptrend_pullback(
        "AAA", _frame(_pullback_to_support()), data_cutoff=CUTOFF
    )
    row = result.as_dict()

    for field in (
        "symbol",
        "eligible",
        "candidate_state",
        "total_score",
        "ema5",
        "ema10",
        "ema5_slope",
        "ema10_slope",
        "support_zone_lower",
        "support_zone_upper",
        "support_zone_centre",
        "distance_from_support_percent",
        "support_strength",
        "support_sources",
        "first_research_target",
        "invalidation_level",
        "pullback_depth_percent",
        "liquidity_score",
        "data_cutoff",
        "deterministic_reasons",
        "source_provider",
        "valid_session_count",
    ):
        assert field in row, field
    assert row["data_cutoff"] == CUTOFF
    assert row["source_provider"] == "EODHD_DAILY"
    assert row["valid_session_count"] == result.readiness.valid_sessions


def test_no_order_or_first_touch_claim_is_ever_made():
    result = analyze_uptrend_pullback(
        "AAA", _frame(_pullback_to_support()), data_cutoff=CUTOFF
    )

    assert result.first_touch_order_available is False
    assert result.support.first_touch_order_available is False
    assert "proxies" in result.support.support_touch_disclosure.lower()
    assert "first" in result.path_disclosure.lower()
    assert CONFIG.automatic_execution is False
    assert CONFIG.broker_orders_enabled is False
    assert CONFIG.decision_support_only is True


# --- Proximity states --------------------------------------------------------


def test_waiting_for_pullback_state_sits_in_the_middle_band():
    result = analyze_uptrend_pullback("BBB", _frame(_waiting()), data_cutoff=CUTOFF)

    assert result.candidate_state == UPTREND_WAIT_FOR_PULLBACK
    assert result.eligible is False
    assert (
        CONFIG.pullback.near_support_maximum_percent
        < result.distance_from_support_percent
        <= CONFIG.pullback.wait_for_pullback_maximum_percent
    )


def test_extended_price_is_no_chase_even_with_a_strong_trend():
    result = analyze_uptrend_pullback("CCC", _frame(_extended()), data_cutoff=CUTOFF)

    assert result.candidate_state == UPTREND_EXTENDED_NO_CHASE
    assert result.eligible is False
    assert result.distance_from_support_percent > (
        CONFIG.pullback.wait_for_pullback_maximum_percent
    )
    assert result.trend.alignment_ok is True
    assert result.trend.trend_quality_score > 50


def test_proximity_bands_are_configurable_not_hardcoded():
    frame = _frame(_waiting())
    widened = replace(
        CONFIG,
        pullback=PullbackProximityConfig(
            near_support_maximum_percent=8.0,
            wait_for_pullback_maximum_percent=12.0,
        ),
    )

    default = analyze_uptrend_pullback("BBB", frame, data_cutoff=CUTOFF)
    retuned = analyze_uptrend_pullback(
        "BBB", frame, data_cutoff=CUTOFF, config=widened
    )

    assert default.candidate_state == UPTREND_WAIT_FOR_PULLBACK
    assert retuned.candidate_state == UPTREND_NEAR_SUPPORT
    assert retuned.eligible is True
    assert (
        default.distance_from_support_percent
        == retuned.distance_from_support_percent
    )


def test_deep_pullback_is_rejected_even_when_price_sits_on_support():
    frame = _frame(_pullback_to_support())
    shallow_limit = replace(
        CONFIG,
        pullback=PullbackProximityConfig(
            ideal_pullback_depth_minimum_percent=1.0,
            ideal_pullback_depth_maximum_percent=2.5,
            maximum_pullback_depth_percent=3.0,
        ),
    )

    default = analyze_uptrend_pullback("DDD", frame, data_cutoff=CUTOFF)
    strict = analyze_uptrend_pullback(
        "DDD", frame, data_cutoff=CUTOFF, config=shallow_limit
    )

    assert default.candidate_state == UPTREND_NEAR_SUPPORT
    assert strict.candidate_state == UPTREND_PULLBACK_TOO_DEEP
    assert strict.eligible is False
    assert REASON_PULLBACK_TOO_DEEP in strict.deterministic_reasons
    assert strict.pullback.pullback_depth_percent > 3.0


# --- Trend gates -------------------------------------------------------------


def test_downtrend_fails_ema_alignment():
    result = analyze_uptrend_pullback(
        "EEE", _frame(list(np.linspace(26.0, 19.0, 60))), data_cutoff=CUTOFF
    )

    assert result.candidate_state == EMA_ALIGNMENT_FAILED
    assert result.eligible is False
    assert result.trend.fast_ema < result.trend.slow_ema


def test_flat_market_fails_the_ema5_slope_gate():
    result = analyze_uptrend_pullback(
        "FFF",
        _frame([20.0 + 0.002 * (index % 2) for index in range(60)]),
        data_cutoff=CUTOFF,
    )

    assert result.candidate_state in {EMA5_SLOPE_FAILED, EMA_ALIGNMENT_FAILED}
    assert result.eligible is False


def test_ema20_style_trend_definition_is_rejected_by_configuration():
    with pytest.raises(ValueError, match="EMA20/EMA50"):
        ShortTermTrendConfig(fast_ema_period=20, slow_ema_period=50)


def test_young_trend_fails_the_structure_gate():
    result = analyze_uptrend_pullback(
        "GGG", _frame(_young_trend()), data_cutoff=CUTOFF
    )

    assert result.candidate_state == TREND_STRUCTURE_FAILED
    assert REASON_TREND_TOO_YOUNG in result.deterministic_reasons
    assert result.trend.aligned_sessions < CONFIG.trend.minimum_trend_sessions


def test_last_close_structurally_below_ema10_fails_the_structure_gate():
    result = analyze_uptrend_pullback(
        "HHH", _frame(_dipped_below_ema10()), data_cutoff=CUTOFF
    )

    assert result.candidate_state == TREND_STRUCTURE_FAILED
    assert REASON_LAST_CLOSE_BELOW_EMA10 in result.deterministic_reasons
    assert result.trend.alignment_ok is True
    assert result.trend.last_close_below_slow_ema is True


def test_descending_swing_structure_is_detected():
    steps = []
    for level in (24.0, 23.0, 22.0, 21.0):
        steps.extend([level, level + 0.6, level - 0.4, level + 0.2, level - 0.5])
    closes = (steps * 3)[:60]

    result = analyze_uptrend_pullback("III", _frame(closes), data_cutoff=CUTOFF)

    assert result.eligible is False
    assert result.trend.swing_highs_descending or result.trend.swing_lows_descending


# --- Support gates -----------------------------------------------------------


def test_single_source_support_is_not_confirmed():
    lonely = _shelf() + list(np.linspace(20.25, 26.0, 38))
    result = analyze_uptrend_pullback("JJJ", _frame(lonely), data_cutoff=CUTOFF)

    assert result.candidate_state == SUPPORT_NOT_CONFIRMED
    assert result.support.source_types == (SUPPORT_SOURCE_EMA10,)
    assert REASON_SUPPORT_NO_SOURCES in result.deterministic_reasons


def test_close_below_the_invalidation_level_is_the_support_broken_state():
    candidate = analyze_uptrend_pullback(
        "AAA", _frame(_pullback_to_support()), data_cutoff=CUTOFF
    )
    assert candidate.candidate_state == UPTREND_NEAR_SUPPORT

    state, reasons = _resolve_state(
        trend=replace(
            candidate.trend, last_close=candidate.invalidation_level * 0.99
        ),
        support=candidate.support,
        pullback=candidate.pullback,
        liquidity=candidate.liquidity,
        upside=candidate.upside,
        distance_percent=-5.0,
        cfg=CONFIG,
    )

    assert state == SUPPORT_BROKEN
    assert REASON_SUPPORT_INVALIDATED in reasons


def test_a_collapsed_close_is_never_an_eligible_candidate():
    collapsed = _pullback_to_support()[:-1] + [19.90]

    result = analyze_uptrend_pullback("KKK", _frame(collapsed), data_cutoff=CUTOFF)

    assert result.eligible is False
    assert REASON_SUPPORT_INVALIDATED in result.deterministic_reasons
    assert result.last_close < result.invalidation_level


def test_support_touch_and_reaction_are_named_as_proxies():
    result = analyze_uptrend_pullback(
        "AAA", _frame(_pullback_to_support()), data_cutoff=CUTOFF
    )
    zone = result.support

    assert zone.support_touch_proxy_count >= CONFIG.support.minimum_touch_count
    assert 0.0 <= zone.support_reaction_proxy_frequency <= 1.0
    assert zone.support_reaction_proxy_count <= zone.support_touch_proxy_count


def test_support_zone_width_cap_is_configurable():
    frame = _frame(_pullback_to_support())
    narrow = replace(
        CONFIG,
        support=SupportZoneConfig(maximum_zone_width_percent=0.6),
    )

    result = analyze_uptrend_pullback(
        "AAA", frame, data_cutoff=CUTOFF, config=narrow
    )

    assert result.candidate_state == SUPPORT_NOT_CONFIRMED


# --- Data policy -------------------------------------------------------------


def test_missing_history_is_data_unavailable_not_a_zero_score():
    result = analyze_uptrend_pullback("LLL", None, data_cutoff=CUTOFF)

    assert result.candidate_state == DATA_UNAVAILABLE
    assert result.total_score is None
    assert result.eligible is False


def test_non_eodhd_provenance_is_rejected_without_fallback():
    yahoo = _frame(_pullback_to_support(), provider="yahoo")

    result = analyze_uptrend_pullback("MMM", yahoo, data_cutoff=CUTOFF)

    assert result.candidate_state == PROVENANCE_REJECTED
    assert result.total_score is None


def test_short_history_is_insufficient_history():
    result = analyze_uptrend_pullback(
        "NNN", _frame(_pullback_to_support()[:25]), data_cutoff=CUTOFF
    )

    assert result.candidate_state == INSUFFICIENT_HISTORY
    assert result.total_score is None
    assert result.readiness.valid_sessions < CONFIG.minimum_valid_sessions


def test_illiquid_symbol_is_gated_before_the_trend_is_credited():
    result = analyze_uptrend_pullback(
        "OOO", _frame(_pullback_to_support(), volume=900), data_cutoff=CUTOFF
    )

    assert result.candidate_state == INSUFFICIENT_LIQUIDITY
    assert result.eligible is False
    assert result.liquidity.meets_minimum is False


def test_rows_after_the_cutoff_are_never_used():
    closes = _pullback_to_support()
    full = _frame(closes + [26.0, 27.0], end="2026-07-29")

    at_cutoff = analyze_uptrend_pullback("AAA", full, data_cutoff=CUTOFF)
    reference = analyze_uptrend_pullback(
        "AAA", _frame(closes), data_cutoff=CUTOFF
    )

    assert at_cutoff.latest_session <= CUTOFF
    assert at_cutoff.last_close == reference.last_close
    assert at_cutoff.total_score == reference.total_score


def test_incomplete_daily_candles_are_dropped():
    closes = _pullback_to_support()
    frame = _frame(closes)
    frame["Complete"] = True
    frame.iloc[-1, frame.columns.get_loc("Complete")] = False

    result = analyze_uptrend_pullback("AAA", frame, data_cutoff=CUTOFF)
    reference = analyze_uptrend_pullback(
        "AAA", _frame(closes[:-1]), data_cutoff=CUTOFF
    )

    assert result.last_close == reference.last_close


# --- Scoring and ranking -----------------------------------------------------


def test_score_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1.0"):
        UptrendPullbackScoreWeights(short_term_trend_quality=0.55)


def test_hard_gates_override_a_high_score():
    extended = analyze_uptrend_pullback(
        "CCC", _frame(_extended()), data_cutoff=CUTOFF
    )
    candidate = analyze_uptrend_pullback(
        "AAA", _frame(_pullback_to_support()), data_cutoff=CUTOFF
    )

    assert extended.trend.trend_quality_score > candidate.trend.trend_quality_score
    assert extended.eligible is False
    assert candidate.eligible is True


def test_selection_is_deterministic_for_identical_input():
    first = analyze_uptrend_pullback(
        "AAA", _frame(_pullback_to_support()), data_cutoff=CUTOFF
    )
    second = analyze_uptrend_pullback(
        "AAA", _frame(_pullback_to_support()), data_cutoff=CUTOFF
    )

    assert first.total_score == second.total_score
    assert first.candidate_state == second.candidate_state
    assert first.source_data_fingerprint == second.source_data_fingerprint
    assert first.deterministic_reasons == second.deterministic_reasons


def test_frozen_snapshot_ranks_only_scoreable_symbols():
    histories = {
        "AAA": _frame(_pullback_to_support()),
        "CCC": _frame(_extended()),
        "EEE": _frame(list(np.linspace(26.0, 19.0, 60))),
        "LLL": None,
    }

    snapshot = build_frozen_uptrend_watchlist(
        histories,
        data_cutoff=CUTOFF,
        unavailable_symbols={"LLL"},
        generated_at=GENERATED,
    )

    ranks = {
        result.symbol: result.historical_rank for result in snapshot.results
    }
    assert ranks["LLL"] is None
    assert snapshot.candidate_symbols == ("AAA",)
    assert snapshot.strategy_identity == "UPTREND_PULLBACK_SCALPING_V1"
    assert snapshot.data_cutoff == CUTOFF


def test_top_twenty_is_a_maximum_not_a_quota():
    histories = {
        f"SYM{index:02d}": _frame(list(np.linspace(26.0, 19.0, 60)))
        for index in range(25)
    }
    histories["AAA"] = _frame(_pullback_to_support())

    snapshot = build_frozen_uptrend_watchlist(
        histories, data_cutoff=CUTOFF, generated_at=GENERATED
    )

    assert len(snapshot.candidate_symbols) == 1
    assert snapshot.candidate_limit == 20


def test_snapshot_identity_is_stable_across_rebuilds():
    histories = {"AAA": _frame(_pullback_to_support())}

    first = build_frozen_uptrend_watchlist(
        histories, data_cutoff=CUTOFF, generated_at=GENERATED
    )
    second = build_frozen_uptrend_watchlist(
        {"AAA": _frame(_pullback_to_support())},
        data_cutoff=CUTOFF,
        generated_at=GENERATED,
    )

    assert first.snapshot_id == second.snapshot_id


def test_eligible_state_set_is_configurable():
    frame = _frame(_waiting())
    permissive = replace(
        CONFIG,
        eligible_states=(UPTREND_NEAR_SUPPORT, UPTREND_WAIT_FOR_PULLBACK),
    )

    result = analyze_uptrend_pullback(
        "BBB", frame, data_cutoff=CUTOFF, config=permissive
    )

    assert result.candidate_state == UPTREND_WAIT_FOR_PULLBACK
    assert result.eligible is True
