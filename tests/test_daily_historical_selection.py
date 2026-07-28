"""Daily-only historical selection and independent intraday readiness tests."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from scalping_expected_range.config import (
    DailyHistoricalScoreWeights,
    DailyHistoricalSelectionConfig,
    ExpectedRangeConfig,
)
from scalping_expected_range.daily_historical_selection import (
    DAILY_AUCTION_DISCLOSURE,
    DAILY_PATH_DISCLOSURE,
    DAILY_SELECTION_INSUFFICIENT,
    DAILY_SELECTION_READY,
    DAILY_SELECTION_UNAVAILABLE,
    EODHD_DAILY,
    INTRADAY_ENRICHMENT_NOT_READY,
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
    start="2026-04-01",
):
    ranges = list(ranges)
    index = pd.bdate_range(start=start, periods=len(ranges))
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


class _FakeEODHDClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def eod(self, symbol, **kwargs):
        self.calls.append(("eod", symbol, kwargs))
        return self.rows

    def get_json(self, path, **kwargs):
        self.calls.append(("get_json", path, kwargs))
        return []


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
        [{"Open": 100.0, "High": 150.0, "Low": 100.0, "Close": 149.0, "Volume": 1e9}],
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


def test_production_and_broker_execution_remain_disabled():
    existing = ExpectedRangeConfig.load()

    assert existing.production_enabled is False
    assert existing.automatic_execution is False
    assert existing.broker_orders_enabled is False
