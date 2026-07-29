"""Daily-only historical selection and independent intraday readiness tests."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from scalping_expected_range.config import (
    DailyHistoricalScoreWeights,
    DailyHistoricalSelectionConfig,
    DailyZoneConsistencyConfig,
    ExpectedRangeConfig,
)
from scalping_expected_range.daily_historical_selection import (
    DAILY_AUCTION_DISCLOSURE,
    DAILY_PATH_DISCLOSURE,
    DAILY_SELECTION_INSUFFICIENT,
    DAILY_SELECTION_READY,
    DAILY_SELECTION_STALE,
    DAILY_SELECTION_UNAVAILABLE,
    EODHD_DAILY,
    INTRADAY_ENRICHMENT_NOT_READY,
    VERY_STABLE_ZONE,
    VOLUME_HISTORY_READY,
    VOLUME_HISTORY_UNRESOLVED,
    ZONE_SAFETY_READY,
    analyze_daily_history,
    assess_intraday_enrichment,
    build_frozen_daily_watchlist,
    load_eodhd_daily_history,
)


CUTOFF = "2026-07-27"
GENERATED = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)


def _history(
    ranges,
    *,
    upper_share=0.5,
    volume=1_000_000,
    start=None,
):
    ranges = list(ranges)
    index = (
        pd.bdate_range(start=start, periods=len(ranges))
        if start is not None
        else pd.bdate_range(end=CUTOFF, periods=len(ranges))
    )
    rows = []
    for i, value in enumerate(ranges):
        share = upper_share[i] if isinstance(upper_share, (list, tuple, np.ndarray)) else upper_share
        upper = float(value) * float(share)
        lower = float(value) - upper
        rows.append(
            {
                "Open": 100.0,
                "High": 100.0 + upper,
                "Low": 100.0 - lower,
                "Close": 100.0,
                "Volume": float(volume),
            }
        )
    frame = pd.DataFrame(rows, index=index)
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "volume_safe_for_lookback": True,
        "corporate_action_policy_version": "test",
    }
    return frame


def _stable(n=60, value=3.0):
    values = [value - 0.2, value - 0.1, value, value + 0.1, value + 0.2]
    return _history((values * ((n + 4) // 5))[:n])


def _zone_history(upper_values, lower_values, *, volume=1_000_000):
    upper_values = list(upper_values)
    lower_values = list(lower_values)
    assert len(upper_values) == len(lower_values)
    frame = pd.DataFrame(
        {
            "Open": 100.0,
            "High": [100.0 + float(value) for value in upper_values],
            "Low": [100.0 - float(value) for value in lower_values],
            "Close": 100.0,
            "Volume": float(volume),
        },
        index=pd.bdate_range(end=CUTOFF, periods=len(upper_values)),
    )
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "volume_safe_for_lookback": True,
        "corporate_action_policy_version": "test",
    }
    return frame


class _FakeEODHDClient:
    def __init__(self, rows, splits=None):
        self.rows = rows
        self.splits = splits or []
        self.calls = []

    def eod(self, symbol, **kwargs):
        self.calls.append(("eod", symbol, kwargs))
        return self.rows

    def get_json(self, path, **kwargs):
        self.calls.append(("get_json", path, kwargs))
        return self.splits


def test_daily_loader_uses_direct_eodhd_and_corporate_action_pipeline_only():
    dates = pd.bdate_range("2026-06-01", periods=35)
    rows = [
        {
            "date": day.strftime("%Y-%m-%d"),
            "open": 100.0,
            "high": 103.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 1_000_000,
        }
        for day in dates
    ]
    client = _FakeEODHDClient(rows)

    loaded = load_eodhd_daily_history("comi", client=client)

    assert loaded.status == "OK"
    assert loaded.frame is not None
    assert [call[:2] for call in client.calls] == [
        ("eod", "COMI.EGX"),
        ("get_json", "splits/COMI.EGX"),
    ]
    metadata = loaded.frame.attrs["market_data"]
    assert metadata["provider"] == "eodhd"
    assert metadata["price_series"] == "SPLIT_ADJUSTED"
    assert metadata["corporate_action_policy_version"]
    assert metadata["fallback_used"] is False
    assert metadata["yahoo_network_used"] is False


def test_split_adjustment_does_not_create_false_daily_volatility():
    dates = pd.bdate_range("2026-05-01", periods=35)
    split_date = dates[20]
    rows = []
    for day in dates:
        scale = 100.0 if day < split_date else 10.0
        rows.append(
            {
                "date": day.strftime("%Y-%m-%d"),
                "open": scale,
                "high": scale * 1.03,
                "low": scale * 0.99,
                "close": scale * 1.01,
                "adjusted_close": scale * 1.01,
                "volume": 1_000_000,
            }
        )
    client = _FakeEODHDClient(
        rows,
        splits=[{"date": split_date.strftime("%Y-%m-%d"), "split": "10/1"}],
    )

    loaded = load_eodhd_daily_history("TEST", client=client)
    result = analyze_daily_history("TEST", loaded.frame, data_cutoff=CUTOFF)

    assert result.metrics.maximum_daily_range_percent == pytest.approx(4.0)
    assert result.metrics.median_daily_range_percent == pytest.approx(4.0)


def test_dividend_adjusted_close_cannot_create_artificial_daily_range():
    dates = pd.bdate_range("2026-05-01", periods=35)
    rows = [
        {
            "date": day.strftime("%Y-%m-%d"),
            "open": 100.0,
            "high": 103.0,
            "low": 99.0,
            "close": 101.0,
            "adjusted_close": 40.0 + index,
            "volume": 1_000_000,
        }
        for index, day in enumerate(dates)
    ]

    loaded = load_eodhd_daily_history("TEST", client=_FakeEODHDClient(rows))
    result = analyze_daily_history("TEST", loaded.frame, data_cutoff=CUTOFF)

    assert result.metrics.median_daily_range_percent == pytest.approx(4.0)
    assert result.metrics.maximum_daily_range_percent == pytest.approx(4.0)


def test_loader_cutoff_excludes_future_bar_and_future_corporate_action():
    dates = pd.bdate_range(end="2026-07-28", periods=35)
    rows = [
        {
            "date": day.strftime("%Y-%m-%d"),
            "open": 100.0,
            "high": 103.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 1_000_000,
        }
        for day in dates
    ]
    client = _FakeEODHDClient(
        rows,
        splits=[{"date": "2026-07-28", "split": "10/1"}],
    )

    loaded = load_eodhd_daily_history(
        "TEST", client=client, data_cutoff="2026-07-27"
    )
    metadata = loaded.frame.attrs["market_data"]

    assert loaded.frame.index.max().date().isoformat() == "2026-07-27"
    assert metadata["normalization_data_cutoff"] == "2026-07-27"
    assert metadata["true_split_events"] == []


def test_empty_eodhd_response_is_unavailable_without_fallback():
    client = _FakeEODHDClient([])

    loaded = load_eodhd_daily_history("missing", client=client)

    assert loaded.status == DAILY_SELECTION_UNAVAILABLE
    assert loaded.frame is None
    assert loaded.source_provider == EODHD_DAILY
    assert "no daily OHLC" in loaded.detail
    assert [call[:2] for call in client.calls] == [
        ("eod", "MISSING.EGX"),
        ("get_json", "splits/MISSING.EGX"),
    ]


def test_eodhd_daily_history_generates_required_baseline_metrics():
    result = analyze_daily_history("COMI", _stable(), data_cutoff=CUTOFF)
    metrics = result.metrics

    assert result.source_provider == EODHD_DAILY
    assert result.readiness.status == DAILY_SELECTION_READY
    assert result.historical_scalping_potential is not None
    assert metrics is not None
    assert metrics.valid_session_count == 60
    assert metrics.median_daily_range_percent == pytest.approx(3.0)
    assert metrics.range_hit_2pct_frequency == 1.0
    assert metrics.daily_range_stability_score > 70
    assert metrics.daily_volatility_zone_consistency_score > 70
    assert metrics.daily_liquidity_score is not None


def test_fewer_than_30_daily_sessions_is_insufficient_not_zero_scored():
    result = analyze_daily_history("X", _stable(29), data_cutoff=CUTOFF)

    assert result.readiness.status == DAILY_SELECTION_INSUFFICIENT
    assert result.historical_scalping_potential is None
    assert result.eligible is False


def test_30_daily_sessions_allows_baseline_scoring():
    result = analyze_daily_history("X", _stable(30), data_cutoff=CUTOFF)

    assert result.readiness.status == DAILY_SELECTION_READY
    assert result.readiness.preferred_ready is False
    assert result.historical_scalping_potential is not None


def test_only_completed_sessions_through_d_minus_one_are_used():
    base = _stable(30)
    future = pd.DataFrame(
        [{"Open": 100.0, "High": 150.0, "Low": 50.0, "Close": 140.0, "Volume": 1e9}],
        index=[pd.Timestamp("2026-07-28")],
    )
    with_today = pd.concat([base, future])
    with_today.attrs = dict(base.attrs)

    reference = analyze_daily_history("X", base, data_cutoff=CUTOFF)
    result = analyze_daily_history("X", with_today, data_cutoff=CUTOFF)

    assert result.metrics.valid_session_count == 30
    assert result.source_data_fingerprint == reference.source_data_fingerprint
    assert result.historical_scalping_potential == reference.historical_scalping_potential


def test_stable_wide_range_beats_equal_mean_erratic_range():
    stable_values = [2.6, 2.8, 3.0, 3.1, 3.3] * 6
    # Same 2.96 arithmetic mean as the stable five-session pattern.
    erratic_values = [0.4, 0.6, 0.8, 0.7, 12.3] * 6

    stable = analyze_daily_history("STABLE", _history(stable_values), data_cutoff=CUTOFF)
    erratic = analyze_daily_history("ERRATIC", _history(erratic_values), data_cutoff=CUTOFF)

    assert np.mean(stable_values) == pytest.approx(np.mean(erratic_values))
    assert (
        stable.metrics.daily_range_stability_score
        > erratic.metrics.daily_range_stability_score
    )


def test_zone_consistency_is_separate_from_range_stability():
    ranges = [3.0] * 60
    consistent = analyze_daily_history(
        "CONSISTENT", _history(ranges, upper_share=0.5), data_cutoff=CUTOFF
    )
    shares = [0.03, 0.97] * 30
    unstable_zone = analyze_daily_history(
        "SHIFTING", _history(ranges, upper_share=shares), data_cutoff=CUTOFF
    )

    assert (
        consistent.metrics.daily_range_stability_score
        == unstable_zone.metrics.daily_range_stability_score
    )
    assert (
        consistent.metrics.daily_volatility_zone_consistency_score
        > unstable_zone.metrics.daily_volatility_zone_consistency_score
    )


def test_stable_upper_and_lower_zones_score_highly_and_disclose_profiles():
    result = analyze_daily_history(
        "BALANCED",
        _zone_history([1.5] * 60, [1.5] * 60),
        data_cutoff=CUTOFF,
    )
    profile = result.zone_profile_60

    assert profile.upper.consistency_score > 90
    assert profile.lower.consistency_score > 90
    assert profile.combined_score > 90
    assert profile.confidence_label == VERY_STABLE_ZONE
    assert profile.safety_status == ZONE_SAFETY_READY
    assert profile.upper.normal_zone_lower_percent <= 1.5
    assert profile.upper.normal_zone_upper_percent >= 1.5


def test_one_stable_side_cannot_hide_a_highly_erratic_side():
    stable = analyze_daily_history(
        "STABLE",
        _zone_history([1.5] * 60, [1.5] * 60),
        data_cutoff=CUTOFF,
    )
    erratic_lower = [0.0, 0.1, 0.2, 1.0, 3.0, 6.0] * 10
    asymmetric = analyze_daily_history(
        "ASYMMETRIC",
        _zone_history([1.5] * 60, erratic_lower),
        data_cutoff=CUTOFF,
    )

    assert asymmetric.zone_profile_60.upper.consistency_score > 90
    assert asymmetric.zone_profile_60.lower.consistency_score < 60
    assert (
        asymmetric.zone_profile_60.combined_score
        < asymmetric.zone_profile_60.upper.consistency_score
    )
    assert (
        asymmetric.zone_profile_60.combined_score
        < stable.zone_profile_60.combined_score - 20
    )


@pytest.mark.parametrize("side", ["upper", "lower"])
def test_one_extreme_zone_outlier_does_not_dominate(side):
    upper = [1.5] * 60
    lower = [1.5] * 60
    if side == "upper":
        upper[-1] = 40.0
    else:
        lower[-1] = 40.0
    baseline = analyze_daily_history(
        "BASE",
        _zone_history([1.5] * 60, [1.5] * 60),
        data_cutoff=CUTOFF,
    )
    changed = analyze_daily_history(
        "OUTLIER",
        _zone_history(upper, lower),
        data_cutoff=CUTOFF,
    )

    assert changed.zone_profile_60.safety_status == ZONE_SAFETY_READY
    assert (
        baseline.zone_profile_60.combined_score
        - changed.zone_profile_60.combined_score
        < 5
    )


def test_two_event_sessions_do_not_dominate_sixty_normal_sessions():
    upper = [1.5] * 58 + [20.0, 1.5]
    lower = [1.5] * 59 + [20.0]
    result = analyze_daily_history(
        "TWO_EVENTS",
        _zone_history(upper, lower),
        data_cutoff=CUTOFF,
    )

    assert result.zone_profile_60.combined_score > 85
    assert result.zone_profile_60.safety_status == ZONE_SAFETY_READY


def test_event_dominated_quantized_zone_is_typed_and_hard_excluded():
    result = analyze_daily_history(
        "EVENT_DOMINATED",
        _zone_history(
            [0.0] * 45 + [10.0] * 15,
            [0.0] * 45 + [25.0] * 15,
        ),
        data_cutoff=CUTOFF,
    )

    assert result.zone_profile_60.safety_status != ZONE_SAFETY_READY
    assert "ZONE_EVENT_DOMINATED" in result.zone_profile_60.safety_reasons
    assert result.eligible is False
    assert "ZONE_EVENT_DOMINATED" in result.eligibility_reasons


def test_zone_side_combination_is_monotonic():
    from scalping_expected_range.daily_historical_selection import (
        _combine_zone_sides,
    )

    cfg = DailyHistoricalSelectionConfig()

    assert _combine_zone_sides(60, 70, cfg) > _combine_zone_sides(50, 70, cfg)
    assert _combine_zone_sides(60, 80, cfg) > _combine_zone_sides(60, 70, cfg)


def test_ordinary_completed_session_moves_zone_score_gradually():
    upper = [1.3, 1.5, 1.7, 1.4, 1.6] * 12
    lower = [1.2, 1.4, 1.6, 1.3, 1.5] * 12
    history = _zone_history(upper, lower)
    ordinary = pd.DataFrame(
        [
            {
                "Open": 100.0,
                "High": 101.5,
                "Low": 98.6,
                "Close": 100.0,
                "Volume": 1_000_000,
            }
        ],
        index=[pd.Timestamp("2026-07-28")],
    )
    extended = pd.concat([history, ordinary])
    extended.attrs = dict(history.attrs)
    before = analyze_daily_history("X", history, data_cutoff=CUTOFF)
    after = analyze_daily_history("X", extended, data_cutoff="2026-07-28")

    assert (
        abs(
            after.zone_profile_60.combined_score
            - before.zone_profile_60.combined_score
        )
        < 2
    )


def test_recent_improvement_cannot_overwrite_long_term_profile():
    unstable_upper = [0.1, 0.3, 1.5, 4.0, 8.0] * 6
    unstable_lower = [8.0, 4.0, 1.5, 0.3, 0.1] * 6
    frame = _zone_history(
        unstable_upper + [1.5] * 30,
        unstable_lower + [1.5] * 30,
    )
    result = analyze_daily_history("X", frame, data_cutoff=CUTOFF)

    assert result.zone_profile_30.combined_score > result.zone_profile_60.combined_score
    assert (
        result.metrics.daily_volatility_zone_consistency_score
        == result.zone_profile_60.combined_score
    )
    assert (
        result.confirmed_historical_scalping_potential
        <= result.historical_scalping_potential
    )
    assert result.zone_confidence_penalty == 0


def test_hard_eligibility_and_top_n_selection_are_separate():
    histories = {
        f"S{index:02d}": _stable(value=4.0 + index / 100.0)
        for index in range(25)
    }
    snapshot = build_frozen_daily_watchlist(
        histories,
        data_cutoff=CUTOFF,
        generated_at=GENERATED,
    )

    assert sum(result.eligible for result in snapshot.results) == 25
    assert sum(result.selected_candidate for result in snapshot.results) == 20
    assert len(snapshot.candidate_symbols) == 20
    assert all(
        result.eligible_rank <= 20
        for result in snapshot.results
        if result.selected_candidate
    )


def test_small_severe_floor_change_does_not_reshuffle_normal_candidates():
    histories = {
        f"S{index:02d}": _stable(value=4.0 + index / 100.0)
        for index in range(25)
    }
    base_cfg = DailyHistoricalSelectionConfig()
    stricter_cfg = replace(
        base_cfg,
        zone=replace(
            base_cfg.zone,
            severe_combined_floor=base_cfg.zone.severe_combined_floor + 1,
            severe_side_floor=base_cfg.zone.severe_side_floor + 1,
        ),
    )
    base = build_frozen_daily_watchlist(
        histories, data_cutoff=CUTOFF, generated_at=GENERATED, config=base_cfg
    )
    stricter = build_frozen_daily_watchlist(
        histories,
        data_cutoff=CUTOFF,
        generated_at=GENERATED,
        config=stricter_cfg,
    )

    assert stricter.candidate_symbols == base.candidate_symbols


def test_zone_score_and_snapshot_are_deterministic():
    histories = {"A": _stable(), "B": _history([2.2, 3.1, 2.7] * 20)}

    first = build_frozen_daily_watchlist(
        histories, data_cutoff=CUTOFF, generated_at=GENERATED
    )
    second = build_frozen_daily_watchlist(
        histories, data_cutoff=CUTOFF, generated_at=GENERATED
    )

    assert first.as_dict() == second.as_dict()


def test_nine_unresolved_volume_histories_remain_hard_excluded():
    histories = {}
    for index in range(9):
        frame = _stable()
        frame.attrs["market_data"]["volume_safe_for_lookback"] = False
        histories[f"V{index}"] = frame

    snapshot = build_frozen_daily_watchlist(
        histories, data_cutoff=CUTOFF, generated_at=GENERATED
    )

    assert snapshot.candidate_symbols == ()
    assert all(not result.eligible for result in snapshot.results)
    assert all(
        result.volume_history_status == VOLUME_HISTORY_UNRESOLVED
        for result in snapshot.results
    )


def test_zone_config_rejects_non_monotonic_default_weights():
    with pytest.raises(ValueError, match="combination weights"):
        DailyZoneConsistencyConfig(
            conservative_minimum_weight=0.50,
            balanced_geometric_weight=0.40,
        )


def test_live_rubix_state_and_today_move_cannot_change_snapshot():
    histories = {"A": _stable(), "B": _stable(value=1.0)}
    before = build_frozen_daily_watchlist(
        histories, data_cutoff=CUTOFF, generated_at=GENERATED
    )

    changed = {symbol: frame.copy() for symbol, frame in histories.items()}
    for frame in changed.values():
        frame.attrs["rubix_live"] = {
            "price_change_percent": 25.0,
            "spread_percent": 0.01,
            "rvol": 50.0,
        }
    today = pd.DataFrame(
        [{"Open": 100.0, "High": 180.0, "Low": 99.0, "Close": 170.0, "Volume": 1e9}],
        index=[pd.Timestamp("2026-07-28")],
    )
    changed["B"] = pd.concat([changed["B"], today])
    changed["B"].attrs = dict(histories["B"].attrs)

    after = build_frozen_daily_watchlist(
        changed, data_cutoff=CUTOFF, generated_at=GENERATED
    )

    assert after.snapshot_id == before.snapshot_id
    assert after.candidate_symbols == before.candidate_symbols
    assert [
        (result.symbol, result.historical_rank, result.historical_scalping_potential)
        for result in after.results
    ] == [
        (result.symbol, result.historical_rank, result.historical_scalping_potential)
        for result in before.results
    ]


def test_stock_rising_strongly_today_cannot_enter_frozen_list():
    weak = _stable(value=0.8)
    snapshot = build_frozen_daily_watchlist(
        {"WEAK": weak}, data_cutoff=CUTOFF, generated_at=GENERATED
    )
    assert "WEAK" not in snapshot.candidate_symbols

    today = pd.DataFrame(
        [{"Open": 100.0, "High": 105.0, "Low": 100.0, "Close": 105.0, "Volume": 1e9}],
        index=[pd.Timestamp("2026-07-28")],
    )
    moved = pd.concat([weak, today])
    moved.attrs = dict(weak.attrs)
    rebuilt = build_frozen_daily_watchlist(
        {"WEAK": moved}, data_cutoff=CUTOFF, generated_at=GENERATED
    )
    assert "WEAK" not in rebuilt.candidate_symbols
    assert rebuilt.snapshot_id == snapshot.snapshot_id


def test_missing_eodhd_symbol_is_unavailable_not_zero():
    snapshot = build_frozen_daily_watchlist(
        {},
        data_cutoff=CUTOFF,
        unavailable_symbols={"MISSING"},
        generated_at=GENERATED,
    )
    result = snapshot.results[0]

    assert result.readiness.status == DAILY_SELECTION_UNAVAILABLE
    assert result.historical_scalping_potential is None
    assert result.historical_rank is None


def test_non_eodhd_source_is_rejected_without_fallback():
    result = analyze_daily_history(
        "X",
        _stable(),
        data_cutoff=CUTOFF,
        source_provider="YAHOO",
    )

    assert result.readiness.status == DAILY_SELECTION_UNAVAILABLE
    assert result.historical_scalping_potential is None
    assert result.source_provider == EODHD_DAILY


def test_yahoo_tagged_frame_is_rejected_even_with_default_source_argument():
    frame = _stable()
    frame.attrs["market_data"]["provider"] = "yahoo"

    result = analyze_daily_history("X", frame, data_cutoff=CUTOFF)

    assert result.readiness.status == DAILY_SELECTION_UNAVAILABLE
    assert result.historical_scalping_potential is None
    assert "provider provenance" in result.eligibility_reasons[-1]


def test_missing_provider_metadata_is_rejected():
    frame = _stable()
    frame.attrs.clear()

    result = analyze_daily_history("X", frame, data_cutoff=CUTOFF)

    assert result.readiness.status == DAILY_SELECTION_UNAVAILABLE
    assert result.source_data_fingerprint is None
    assert result.historical_scalping_potential is None


def test_frame_normalized_after_historical_cutoff_cannot_be_scored():
    frame = _stable()
    frame.attrs["market_data"]["normalization_data_cutoff"] = "2026-07-28"

    result = analyze_daily_history("X", frame, data_cutoff="2026-07-27")

    assert result.volume_history_status == VOLUME_HISTORY_UNRESOLVED
    assert result.historical_scalping_potential is None
    assert result.metrics.median_daily_range_percent is not None


def test_result_fingerprint_is_derived_not_trusted_from_input_metadata():
    frame = _stable()
    assert "source_data_fingerprint" not in frame.attrs["market_data"]

    result = analyze_daily_history("X", frame, data_cutoff=CUTOFF)

    assert result.source_data_fingerprint.startswith("sha256:")


def test_daily_ohlc_makes_no_first_touch_claim():
    result = analyze_daily_history("X", _stable(), data_cutoff=CUTOFF)

    assert result.first_touch_available is False
    assert "does not reveal" in result.path_disclosure
    assert "first-touch" in DAILY_PATH_DISCLOSURE


def test_daily_auction_limitation_is_disclosed_honestly():
    result = analyze_daily_history("X", _stable(), data_cutoff=CUTOFF)

    assert result.auction_disclosure == DAILY_AUCTION_DISCLOSURE
    assert "closing-auction effects" in result.auction_disclosure
    assert "Continuous-session-only" in result.auction_disclosure


def test_intraday_enrichment_not_ready_at_seven_but_daily_can_be_ready():
    intraday = assess_intraday_enrichment(7)
    daily = analyze_daily_history("X", _stable(), data_cutoff=CUTOFF)

    assert intraday.status == INTRADAY_ENRICHMENT_NOT_READY
    assert intraday.required_sessions == 20
    assert len(intraday.unavailable_metrics) == 5
    assert daily.readiness.status == DAILY_SELECTION_READY


def test_weights_are_typed_daily_only_and_sum_to_one():
    weights = DailyHistoricalScoreWeights()
    config_fields = {item.name for item in fields(DailyHistoricalSelectionConfig)}

    assert sum(
        (
            weights.movement_potential,
            weights.range_stability,
            weights.zone_consistency,
            weights.liquidity,
        )
    ) == pytest.approx(1.0)
    assert {
        "live_spread",
        "current_price_change",
        "live_momentum",
        "live_rvol",
    }.isdisjoint(config_fields)


def test_configuration_cannot_relabel_selector_as_yahoo():
    with pytest.raises(ValueError, match="EODHD_DAILY"):
        DailyHistoricalSelectionConfig(source_provider="YAHOO")


def test_frozen_snapshot_has_required_provenance():
    snapshot = build_frozen_daily_watchlist(
        {"X": _stable()}, data_cutoff=CUTOFF, generated_at=GENERATED
    )
    result = snapshot.results[0]

    assert snapshot.source_provider == EODHD_DAILY
    assert snapshot.data_cutoff == CUTOFF
    assert len(snapshot.snapshot_id) == 64
    assert result.interval == "1d"
    assert result.raw_adjusted_mode
    assert result.source_data_fingerprint.startswith("sha256:")
    assert result.metric_version == snapshot.metric_version


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_quote", 99.5),
        ("current_price_change", 4.9),
        ("current_high", 105.0),
        ("current_low", 98.0),
        ("current_volume", 9_999_999),
        ("live_rvol", 8.2),
        ("spread", 0.12),
        ("vwap_state", "ABOVE"),
        ("breakout_state", "BREAKOUT"),
        ("momentum_state", "STRONG"),
        ("rubix_freshness", "FRESH"),
    ],
)
def test_each_live_field_is_independently_invariant(field, value):
    histories = {"A": _stable(value=3.0), "B": _stable(value=1.0)}
    reference = build_frozen_daily_watchlist(
        histories, data_cutoff=CUTOFF, generated_at=GENERATED
    )
    changed = {}
    for symbol, frame in histories.items():
        mutated = frame.copy()
        mutated.attrs["rubix_live"] = {field: value}
        changed[symbol] = mutated

    result = build_frozen_daily_watchlist(
        changed, data_cutoff=CUTOFF, generated_at=GENERATED
    )

    assert result.as_dict() == reference.as_dict()


def test_session_d_is_excluded_then_becomes_available_for_d_plus_one():
    history = _stable(30)
    session_d = pd.DataFrame(
        [{"Open": 100.0, "High": 108.0, "Low": 99.0, "Close": 107.0, "Volume": 2e6}],
        index=[pd.Timestamp("2026-07-28")],
    )
    extended = pd.concat([history, session_d])
    extended.attrs = dict(history.attrs)

    prepared_for_d = analyze_daily_history("X", extended, data_cutoff="2026-07-27")
    reference = analyze_daily_history("X", history, data_cutoff="2026-07-27")
    prepared_for_d_plus_one = analyze_daily_history(
        "X", extended, data_cutoff="2026-07-28"
    )

    assert prepared_for_d.source_data_fingerprint == reference.source_data_fingerprint
    assert prepared_for_d.historical_scalping_potential == reference.historical_scalping_potential
    assert prepared_for_d_plus_one.metrics.valid_session_count == 31
    assert prepared_for_d_plus_one.source_data_fingerprint != reference.source_data_fingerprint


def test_weekend_gap_does_not_admit_next_session_early():
    history = _stable(30)
    history.index = pd.bdate_range(end="2026-07-23", periods=30)
    monday = pd.DataFrame(
        [{"Open": 100.0, "High": 110.0, "Low": 99.0, "Close": 109.0, "Volume": 2e6}],
        index=[pd.Timestamp("2026-07-27")],
    )
    extended = pd.concat([history, monday])
    extended.attrs = dict(history.attrs)

    weekend_snapshot = analyze_daily_history(
        "X", extended, data_cutoff="2026-07-26"
    )
    reference = analyze_daily_history("X", history, data_cutoff="2026-07-26")

    assert weekend_snapshot.source_data_fingerprint == reference.source_data_fingerprint
    assert weekend_snapshot.metrics.valid_session_count == 30


def test_cairo_timezone_boundary_excludes_cairo_next_day():
    history = _stable(30)
    history.index = pd.bdate_range(end="2026-07-27", periods=30, tz="UTC") + pd.Timedelta(
        hours=12
    )
    cairo_next_day = pd.DataFrame(
        [{"Open": 100.0, "High": 110.0, "Low": 99.0, "Close": 109.0, "Volume": 2e6}],
        index=[pd.Timestamp("2026-07-27 22:30:00", tz="UTC")],
    )
    extended = pd.concat([history, cairo_next_day])
    extended.attrs = dict(history.attrs)

    result = analyze_daily_history("X", extended, data_cutoff="2026-07-27")
    reference = analyze_daily_history("X", history, data_cutoff="2026-07-27")

    assert result.source_data_fingerprint == reference.source_data_fingerprint
    assert result.metrics.valid_session_count == 30


def test_stale_cache_remains_lookahead_free():
    stale = _stable(30)
    stale.index = pd.bdate_range(end="2026-07-09", periods=30)
    current = pd.DataFrame(
        [{"Open": 100.0, "High": 120.0, "Low": 95.0, "Close": 118.0, "Volume": 2e6}],
        index=[pd.Timestamp("2026-07-28")],
    )
    extended = pd.concat([stale, current])
    extended.attrs = dict(stale.attrs)

    result = analyze_daily_history("X", extended, data_cutoff="2026-07-27")
    reference = analyze_daily_history("X", stale, data_cutoff="2026-07-27")

    assert result.latest_session == "2026-07-09"
    assert result.source_data_fingerprint == reference.source_data_fingerprint
    assert result.readiness.status == DAILY_SELECTION_STALE
    assert result.historical_scalping_potential is None


def test_incomplete_daily_candle_is_rejected_even_inside_cutoff():
    history = _stable(30)
    history.index = pd.bdate_range(end="2026-07-24", periods=30)
    history["Complete"] = True
    incomplete = pd.DataFrame(
        [
            {
                "Open": 100.0,
                "High": 120.0,
                "Low": 95.0,
                "Close": 118.0,
                "Volume": 2e6,
                "Complete": False,
            }
        ],
        index=[pd.Timestamp("2026-07-27")],
    )
    extended = pd.concat([history, incomplete])
    extended.attrs = dict(history.attrs)

    result = analyze_daily_history("X", extended, data_cutoff="2026-07-27")

    assert result.metrics.valid_session_count == 30
    assert result.latest_session != "2026-07-27"


def test_invalid_zero_negative_and_malformed_rows_are_rejected():
    history = _stable(30)
    invalid = pd.DataFrame(
        [
            {"Open": 0, "High": 5, "Low": 1, "Close": 2, "Volume": 10},
            {"Open": 5, "High": 6, "Low": 4, "Close": 5, "Volume": 0},
            {"Open": 5, "High": 6, "Low": 4, "Close": 5, "Volume": -1},
            {"Open": 5, "High": 4, "Low": 3, "Close": 5, "Volume": 10},
        ],
        index=pd.date_range("2026-07-20", periods=4),
    )
    extended = pd.concat([history, invalid])
    extended.attrs = dict(history.attrs)

    result = analyze_daily_history("X", extended, data_cutoff=CUTOFF)

    assert result.metrics.valid_session_count == 30
    assert result.metrics.zero_volume_session_count == 0


def test_duplicate_session_dates_are_rejected_not_last_write_wins():
    history = _stable(30)
    history.index = pd.bdate_range(end="2026-07-24", periods=30)
    duplicates = pd.DataFrame(
        [
            {"Open": 100, "High": 103, "Low": 99, "Close": 101, "Volume": 1e6},
            {"Open": 100, "High": 120, "Low": 98, "Close": 119, "Volume": 1e6},
        ],
        index=[
            pd.Timestamp("2026-07-27 10:00"),
            pd.Timestamp("2026-07-27 14:00"),
        ],
    )
    extended = pd.concat([history, duplicates])
    extended.attrs = dict(history.attrs)

    result = analyze_daily_history("X", extended, data_cutoff=CUTOFF)

    assert result.metrics.valid_session_count == 30
    assert result.latest_session != "2026-07-27"


def test_abnormal_open_gap_is_flagged_but_not_added_to_score_formula():
    history = _stable(30)
    history.index = pd.bdate_range(end="2026-07-24", periods=30)
    gap = pd.DataFrame(
        [{"Open": 120.0, "High": 123.0, "Low": 119.0, "Close": 121.0, "Volume": 1e6}],
        index=[pd.Timestamp("2026-07-27")],
    )
    extended = pd.concat([history, gap])
    extended.attrs = dict(history.attrs)

    result = analyze_daily_history("X", extended, data_cutoff=CUTOFF)

    assert result.metrics.abnormal_gap_session_count == 1
    assert result.metrics.maximum_absolute_open_gap_percent == pytest.approx(20.0)


def test_single_extreme_candle_cannot_dominate_robust_score():
    stable = analyze_daily_history(
        "STABLE", _history([2.5] * 60), data_cutoff=CUTOFF
    )
    one_extreme = analyze_daily_history(
        "EXTREME", _history([2.5] * 59 + [50.0]), data_cutoff=CUTOFF
    )

    assert one_extreme.metrics.mean_daily_range_percent > stable.metrics.mean_daily_range_percent
    assert one_extreme.metrics.median_daily_range_percent == stable.metrics.median_daily_range_percent
    assert (
        one_extreme.historical_scalping_potential
        <= stable.historical_scalping_potential
    )


def test_unresolved_volume_has_typed_status_while_price_metrics_remain_usable():
    frame = _stable()
    frame.attrs["market_data"]["volume_safe_for_lookback"] = False

    result = analyze_daily_history("X", frame, data_cutoff=CUTOFF)

    assert result.volume_history_status == VOLUME_HISTORY_UNRESOLVED
    assert result.metrics.median_daily_range_percent is not None
    assert result.metrics.daily_liquidity_score is None
    assert result.historical_scalping_potential is None
    assert VOLUME_HISTORY_UNRESOLVED in result.eligibility_reasons


def test_safe_volume_has_typed_ready_status():
    result = analyze_daily_history("X", _stable(), data_cutoff=CUTOFF)

    assert result.volume_history_status == VOLUME_HISTORY_READY


def test_production_and_broker_execution_remain_disabled():
    existing = ExpectedRangeConfig.load()

    assert existing.production_enabled is False
    assert existing.automatic_execution is False
    assert existing.broker_orders_enabled is False
