"""Focused acceptance tests for the daily Range-Bound Historical Selector."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from scalping_expected_range.config import (
    DailyHistoricalSelectionConfig,
    ExpectedRangeConfig,
    RangeBoundScoreWeights,
)
from scalping_expected_range.daily_historical_selection import (
    CHANNEL_DOWNTREND,
    CHANNEL_BREAKDOWN_RISK,
    CHANNEL_TOO_NARROW,
    CHANNEL_UNSTABLE,
    CHANNEL_UPTREND,
    DATA_UNAVAILABLE,
    EODHD_DAILY,
    RESISTANCE_DRIFTING_DOWN,
    STABLE_RANGE_BOUND_CANDIDATE,
    SUPPORT_DRIFTING_DOWN,
    analyze_daily_history,
    build_frozen_daily_watchlist,
)


CUTOFF = "2026-07-27"
GENERATED = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)


def _frame(lows, highs, *, closes=None, volume=1_000_000):
    lows = np.asarray(lows, dtype=float)
    highs = np.asarray(highs, dtype=float)
    if closes is None:
        closes = (lows + highs) / 2.0
    closes = np.asarray(closes, dtype=float)
    frame = pd.DataFrame(
        {
            "Open": closes,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": float(volume),
        },
        index=pd.bdate_range(end=CUTOFF, periods=len(lows)),
    )
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "volume_safe_for_lookback": True,
        "corporate_action_policy_version": "test",
    }
    return frame


def _horizontal():
    lows = ([18.4, 18.6, 18.5, 18.7] * 15)[:60]
    highs = ([20.2, 20.0, 20.3, 20.1] * 15)[:60]
    closes = ([19.45, 19.30, 19.55, 19.35] * 15)[:60]
    return _frame(lows, highs, closes=closes)


def _trend(start, stop):
    centers = np.linspace(start, stop, 60)
    return _frame(centers - 1.0, centers + 1.0, closes=centers)


def test_horizontal_stable_channel_ranks_highly():
    stable = analyze_daily_history(
        "HORIZONTAL",
        _horizontal(),
        data_cutoff=CUTOFF,
    )

    assert stable.eligible is True
    assert stable.range_bound_status == STABLE_RANGE_BOUND_CANDIDATE
    assert stable.range_bound_profile_60.channel_direction == "HORIZONTAL"
    assert stable.range_bound_tradability_score > 75
    assert stable.range_bound_profile_60.close_containment_frequency > 0.80
    assert "HORIZONTAL_CHANNEL_CONFIRMED" in (
        stable.range_bound_profile_60.selection_reasons
    )


def test_descending_channel_with_same_daily_range_is_rejected():
    horizontal = analyze_daily_history(
        "HORIZONTAL",
        _trend(20.0, 20.0),
        data_cutoff=CUTOFF,
    )
    descending = analyze_daily_history(
        "DESCENDING",
        _trend(21.0, 17.0),
        data_cutoff=CUTOFF,
    )

    assert horizontal.metrics.median_daily_range_percent == pytest.approx(
        descending.metrics.median_daily_range_percent,
        rel=0.25,
    )
    assert horizontal.eligible is True
    assert descending.eligible is False
    assert CHANNEL_DOWNTREND in descending.eligibility_reasons
    assert descending.range_bound_profile_60.channel_center_slope < 0


def test_strong_ascending_trend_is_not_range_bound():
    result = analyze_daily_history(
        "ASCENDING",
        _trend(17.0, 21.0),
        data_cutoff=CUTOFF,
    )

    assert result.eligible is False
    assert CHANNEL_UPTREND in result.eligibility_reasons
    assert result.range_bound_profile_60.channel_direction == "ASCENDING"


def test_one_extreme_session_does_not_redefine_support_or_resistance():
    baseline = analyze_daily_history(
        "BASE",
        _horizontal(),
        data_cutoff=CUTOFF,
    )
    changed_frame = _horizontal()
    changed_frame.iloc[-1, changed_frame.columns.get_loc("Low")] = 5.0
    changed_frame.iloc[-1, changed_frame.columns.get_loc("High")] = 40.0
    changed = analyze_daily_history(
        "OUTLIER",
        changed_frame,
        data_cutoff=CUTOFF,
    )

    base = baseline.range_bound_profile_60
    outlier = changed.range_bound_profile_60
    assert outlier.support_center == pytest.approx(
        base.support_center, abs=0.10
    )
    assert outlier.resistance_center == pytest.approx(
        base.resistance_center, abs=0.10
    )
    assert outlier.event_dominated_outlier_count == 1


def test_stable_but_extremely_narrow_channel_is_rejected():
    result = analyze_daily_history(
        "NARROW",
        _frame([99.0] * 60, [101.0] * 60, closes=[100.0] * 60),
        data_cutoff=CUTOFF,
    )

    assert result.eligible is False
    assert CHANNEL_TOO_NARROW in result.eligibility_reasons


def test_wide_but_random_channel_is_rejected():
    lows = [80.0, 98.0, 84.0, 96.0, 82.0, 99.0] * 10
    highs = [102.0, 120.0, 118.0, 104.0, 119.0, 103.0] * 10
    result = analyze_daily_history(
        "RANDOM",
        _frame(lows, highs),
        data_cutoff=CUTOFF,
    )

    assert result.range_bound_profile_60.channel_width_percent > 5
    assert result.eligible is False
    assert CHANNEL_UNSTABLE in result.eligibility_reasons


def test_support_and_resistance_drift_are_measured_separately():
    support_result = analyze_daily_history(
        "SUPPORT",
        _frame(
            np.linspace(18.7, 17.0, 60),
            [20.3] * 60,
            closes=[19.5] * 60,
        ),
        data_cutoff=CUTOFF,
    )
    support_drift = support_result.range_bound_profile_60
    resistance_result = analyze_daily_history(
        "RESISTANCE",
        _frame(
            [18.4] * 60,
            np.linspace(20.3, 18.8, 60),
            closes=[19.0] * 60,
        ),
        data_cutoff=CUTOFF,
    )
    resistance_drift = resistance_result.range_bound_profile_60

    assert support_drift.support_zone_slope < 0
    assert abs(support_drift.support_zone_slope) > abs(
        support_drift.resistance_zone_slope
    )
    assert resistance_drift.resistance_zone_slope < 0
    assert abs(resistance_drift.resistance_zone_slope) > abs(
        resistance_drift.support_zone_slope
    )
    assert SUPPORT_DRIFTING_DOWN in support_result.eligibility_reasons
    assert (
        RESISTANCE_DRIFTING_DOWN
        in resistance_result.eligibility_reasons
    )


def test_repeated_channel_breakdowns_have_a_typed_rejection():
    frame = _horizontal()
    frame.iloc[::10, frame.columns.get_loc("Low")] = 15.0
    result = analyze_daily_history(
        "BREAKDOWN",
        frame,
        data_cutoff=CUTOFF,
    )

    assert result.eligible is False
    assert CHANNEL_BREAKDOWN_RISK in result.eligibility_reasons


def test_current_rubix_state_cannot_change_ranking_or_membership():
    histories = {
        "A": _horizontal(),
        "B": _frame([48.0] * 60, [52.0] * 60, closes=[50.0] * 60),
    }
    first = build_frozen_daily_watchlist(
        histories,
        data_cutoff=CUTOFF,
        generated_at=GENERATED,
    )
    changed = {symbol: frame.copy() for symbol, frame in histories.items()}
    for frame in changed.values():
        frame.attrs = dict(histories["A"].attrs)
        frame.attrs["rubix_live"] = {
            "price_change_percent": 50,
            "rvol": 100,
        }
    second = build_frozen_daily_watchlist(
        changed,
        data_cutoff=CUTOFF,
        generated_at=GENERATED,
    )

    assert second.snapshot_id == first.snapshot_id
    assert second.candidate_symbols == first.candidate_symbols


def test_session_d_cannot_enter_watchlist_prepared_for_d():
    base = _horizontal()
    future = pd.DataFrame(
        {
            "Open": [50.0],
            "High": [100.0],
            "Low": [1.0],
            "Close": [90.0],
            "Volume": [1e9],
        },
        index=[pd.Timestamp("2026-07-28")],
    )
    changed = pd.concat([base, future])
    changed.attrs = dict(base.attrs)

    before = build_frozen_daily_watchlist(
        {"A": base},
        data_cutoff=CUTOFF,
        generated_at=GENERATED,
    )
    after = build_frozen_daily_watchlist(
        {"A": changed},
        data_cutoff=CUTOFF,
        generated_at=GENERATED,
    )

    assert after.snapshot_id == before.snapshot_id
    assert after.results[0].data_cutoff == CUTOFF


@pytest.mark.parametrize("provider", ["yahoo", "unknown"])
def test_yahoo_and_unknown_provenance_are_rejected(provider):
    frame = _horizontal()
    frame.attrs["market_data"]["provider"] = provider
    result = analyze_daily_history(
        "BAD",
        frame,
        data_cutoff=CUTOFF,
        source_provider=EODHD_DAILY,
    )

    assert result.eligible is False
    assert result.historical_scalping_potential is None
    assert result.range_bound_status == DATA_UNAVAILABLE


def test_range_bound_weights_are_typed_and_volatility_is_not_a_weight():
    weights = RangeBoundScoreWeights()
    weight_fields = {item.name for item in fields(RangeBoundScoreWeights)}
    config = DailyHistoricalSelectionConfig()

    assert sum(getattr(weights, name) for name in weight_fields) == pytest.approx(
        1.0
    )
    assert "movement_potential" not in weight_fields
    assert config.range_bound.weights == weights


def test_range_bound_score_matches_the_typed_formula():
    result = analyze_daily_history(
        "FORMULA",
        _horizontal(),
        data_cutoff=CUTOFF,
    )
    profile = result.range_bound_profile_60
    weights = DailyHistoricalSelectionConfig().range_bound.weights
    expected = (
        profile.horizontal_channel_stability_score
        * weights.horizontal_channel_stability
        + profile.support_resistance_repeatability_score
        * weights.support_resistance_repeatability
        + profile.tradable_channel_width_score
        * weights.tradable_channel_width
        + profile.channel_containment_score
        * weights.channel_containment
        + result.metrics.daily_liquidity_score * weights.liquidity
    )

    assert profile.range_bound_tradability_score == pytest.approx(
        expected,
        abs=0.0002,
    )


def test_production_broker_and_automatic_execution_remain_disabled():
    config = ExpectedRangeConfig()

    assert config.production_enabled is False
    assert config.automatic_execution is False
    assert config.broker_orders_enabled is False
    assert config.decision_support_only is True
