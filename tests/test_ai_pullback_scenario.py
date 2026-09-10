"""Focused synthetic coverage for the daily AI Pullback research scenario."""

from __future__ import annotations

from dataclasses import asdict, replace
import numpy as np
import pandas as pd

from core import ai_analysis_evidence as evidence
from core.ai_pullback_config import DEFAULT_PULLBACK_RESEARCH_CONFIG
from core.ai_pullback_research import evaluate_pullback_history
from core.ai_pullback_scenario import evaluate_pullback_scenario
from core.ai_stock_analysis_contract import PullbackScenarioResult, PullbackState


def _frame(*, confirmed=True, correction_volume=600_000.0, final_close=None):
    closes = list(np.linspace(10.0, 14.0, 80))
    closes += [13.70, 14.10, 14.50, 15.00, 15.50, 16.00,
               15.70, 15.40, 15.10, 14.85, 14.75,
               15.30 if confirmed else 14.78]
    if final_close is not None:
        closes[-1] = final_close
    index = pd.date_range("2026-01-01", periods=len(closes), freq="B", name="Date")
    close = pd.Series(closes, index=index, dtype=float)
    opened = close.shift(1).fillna(close.iloc[0])
    high = pd.concat((opened, close), axis=1).max(axis=1) + 0.08
    low = pd.concat((opened, close), axis=1).min(axis=1) - 0.12
    high.iloc[85] = 16.15
    low.iloc[80] = 13.55
    if confirmed:
        low.iloc[-1] = 14.42
        high.iloc[-1] = max(high.iloc[-1], close.iloc[-1] + 0.08)
    volume = np.full(len(close), 1_000_000.0)
    volume[86:] = correction_volume
    volume[-1] = correction_volume
    frame = pd.DataFrame({
        "Open": opened, "High": high, "Low": low, "Close": close,
        "Adj Close": close, "Volume": volume,
    }, index=index)
    frame.attrs["market_data"] = {
        "provider": "eodhd", "data_domain": "CURRENT_RESEARCH_V2",
        "volume_safe_for_lookback": True,
        "latest_completed_session": index[-1].date().isoformat(),
        "history_sufficient": True, "yahoo_network_used": False,
    }
    return frame


def _evaluate(frame, *, config=DEFAULT_PULLBACK_RESEARCH_CONFIG, cutoff=None,
              ema50_override=None):
    close = frame["Close"].astype(float)
    ema20 = evidence._ema(close, 20)
    ema50 = evidence._ema(close, 50) if ema50_override is None else ema50_override
    atr14 = evidence._atr_wilder(
        frame["High"].astype(float), frame["Low"].astype(float), close)
    return evaluate_pullback_scenario(
        frame, ema20=ema20, ema50=ema50, atr14=atr14,
        volume_safe=bool(frame.attrs["market_data"]["volume_safe_for_lookback"]),
        data_cutoff=cutoff or frame.index[-1].date(), config=config,
    )


def test_clean_uptrend_with_shallow_healthy_correction():
    result = _evaluate(_frame())
    assert result.prior_trend_status == "VALID_UPTREND"
    assert result.depth_classification == "HEALTHY"
    assert result.pullback_percent is not None and result.pullback_atr is not None
    assert result.impulse_retracement_percent is not None


def test_pullback_to_ema20_with_declining_volume():
    result = _evaluate(_frame(
        confirmed=False, correction_volume=500_000.0, final_close=14.62))
    assert result.volume_behaviour == "SELLING_VOLUME_CONTRACTING"
    assert result.correction_average_volume < result.impulse_average_volume
    assert "EMA20" in result.support_confluence


def test_deep_pullback_to_major_support():
    frame = _frame(confirmed=False, final_close=14.38)
    result = _evaluate(frame)
    assert result.depth_classification in {"DEEP", "FAILED_DEPTH"}
    assert result.state in {PullbackState.DEEP_PULLBACK, PullbackState.FAILED_PULLBACK}
    assert any(source in result.support_confluence for source in (
        "EMA50", "PREVIOUS_SWING_LOW", "HORIZONTAL_SUPPORT", "FIB_61_8"))


def test_deep_pullback_at_ema50_does_not_require_ema20_reclaim():
    frame = _frame(confirmed=False, final_close=13.60)
    previous = frame.index[-1]
    frame.loc[previous, ["Open", "High", "Low", "Close", "Adj Close", "Volume"]] = [
        13.70, 13.75, 13.50, 13.60, 13.60, 500_000.0]
    current = previous + pd.offsets.BDay()
    frame.loc[current] = [13.60, 14.00, 13.50, 13.90, 13.90, 500_000.0]
    frame = frame.sort_index()
    frame.attrs["market_data"] = dict(_frame().attrs["market_data"])
    frame.attrs["market_data"]["latest_completed_session"] = current.date().isoformat()
    config = replace(
        DEFAULT_PULLBACK_RESEARCH_CONFIG,
        failed_retracement_percent=95.0,
        failed_pullback_atr=10.0,
        minimum_reward_risk=0.10,
    )
    result = _evaluate(frame, config=config)
    assert result.depth_classification == "DEEP"
    assert "EMA50" in result.support_confluence
    assert result.current_price < result.ema20
    assert "EMA20_RECLAIM" not in result.confirmation_reasons
    assert result.entry_trigger < result.ema20
    assert result.state == PullbackState.CONFIRMED_PULLBACK_ENTRY


def test_support_touch_without_reversal_confirmation_waits():
    frame = _frame(confirmed=False)
    frame.loc[frame.index[-1], "Open"] = frame["Close"].iloc[-1]
    frame.loc[frame.index[-1], "High"] = frame["Close"].iloc[-1] + 0.02
    result = _evaluate(frame)
    assert result.support_reached is True
    assert result.confirmation_status in {"WAITING", "REVERSAL_CANDLE_TRIGGER_PENDING"}
    assert result.state != PullbackState.CONFIRMED_PULLBACK_ENTRY


def test_confirmed_reversal_with_acceptable_reward_risk():
    result = _evaluate(_frame(confirmed=True))
    assert result.confirmation_status == "CONFIRMED"
    assert result.reward_risk is not None
    assert result.reward_risk >= DEFAULT_PULLBACK_RESEARCH_CONFIG.minimum_reward_risk
    assert result.state == PullbackState.CONFIRMED_PULLBACK_ENTRY


def test_expanding_selling_volume_is_not_bullish():
    result = _evaluate(_frame(confirmed=True, correction_volume=1_800_000.0))
    assert result.volume_behaviour == "AGGRESSIVE_SELLING_EXPANSION"
    assert result.state == PullbackState.FAILED_PULLBACK


def test_descending_channel_is_not_misclassified_as_pullback():
    closes = np.linspace(20.0, 10.0, 92)
    frame = _frame()
    frame = frame.copy()
    frame["Close"] = closes
    frame["Open"] = frame["Close"].shift(1).fillna(frame["Close"].iloc[0])
    frame["High"] = frame[["Open", "Close"]].max(axis=1) + 0.10
    frame["Low"] = frame[["Open", "Close"]].min(axis=1) - 0.10
    frame.attrs["market_data"] = _frame().attrs["market_data"]
    result = _evaluate(frame)
    assert result.state in {PullbackState.NOT_APPLICABLE, PullbackState.FAILED_PULLBACK}


def test_materially_declining_ema50_fails_existing_pullback():
    frame = _frame()
    ema50 = evidence._ema(frame["Close"].astype(float), 50).copy()
    start = float(ema50.iloc[85])
    ema50.iloc[86:] = np.linspace(start, start * 0.90, len(ema50) - 86)
    result = _evaluate(frame, ema50_override=ema50)
    assert result.state == PullbackState.FAILED_PULLBACK
    assert result.invalidation_reason == "EMA50_DECLINING_MATERIALLY"


def test_structural_support_failure_is_explicit():
    first = _evaluate(_frame())
    frame = _frame(confirmed=False, final_close=float(first.impulse_low) - 0.75)
    result = _evaluate(frame)
    assert result.state == PullbackState.FAILED_PULLBACK
    assert result.invalidation_reason == "CLOSE_BELOW_STRUCTURAL_SUPPORT"


def test_nearby_resistance_and_poor_rr_prevent_confirmed_entry():
    strict = replace(DEFAULT_PULLBACK_RESEARCH_CONFIG, minimum_reward_risk=5.0)
    result = _evaluate(_frame(), config=strict)
    assert result.reward_risk is not None and result.reward_risk < 5.0
    assert result.state != PullbackState.CONFIRMED_PULLBACK_ENTRY
    assert result.invalidation_reason == "REWARD_RISK_BELOW_RESEARCH_THRESHOLD"


def test_missing_swing_or_history_is_reported_not_zero_filled():
    result = _evaluate(_frame().iloc[:40].copy())
    assert result.state == PullbackState.NOT_APPLICABLE
    assert result.swing_high is None and result.impulse_low is None
    assert result.missing_measurements


def test_d_minus_one_freeze_ignores_future_bars():
    base = _frame()
    cutoff = base.index[-1].date()
    future = pd.concat([base.iloc[-1:].copy()] * 3)
    future.index = pd.date_range(base.index[-1] + pd.offsets.BDay(), periods=3, freq="B")
    future.loc[:, ["Open", "High", "Low", "Close", "Adj Close"]] *= 5.0
    extended = pd.concat([base, future])
    extended.attrs = dict(base.attrs)
    frozen = _evaluate(extended, cutoff=cutoff)
    reference = _evaluate(base, cutoff=cutoff)
    assert asdict(frozen) == asdict(reference)


def test_signal_date_is_not_before_swing_pivot_confirmation_date():
    result = _evaluate(_frame())
    assert result.swing_high_confirmation_date is not None
    assert pd.Timestamp(result.historical_data_cutoff) >= pd.Timestamp(
        result.swing_high_confirmation_date)


def test_target_selection_cannot_use_future_unconfirmed_pivot():
    base = _frame()
    cutoff = base.index[-1].date()
    future = pd.concat([base.iloc[-1:].copy()] * 5)
    future.index = pd.date_range(base.index[-1] + pd.offsets.BDay(), periods=5, freq="B")
    future.loc[:, "High"] = [18.0, 30.0, 18.0, 17.0, 16.0]
    extended = pd.concat([base, future])
    extended.attrs = dict(base.attrs)
    frozen = _evaluate(extended, cutoff=cutoff)
    reference = _evaluate(base, cutoff=cutoff)
    assert (frozen.target_1, frozen.target_2, frozen.major_resistance) == (
        reference.target_1, reference.target_2, reference.major_resistance)


def test_non_eodhd_history_is_not_used_for_pullback_structure():
    frame = _frame()
    frame.attrs["market_data"]["provider"] = "local_plus_mubasher"
    result = _evaluate(frame)
    assert result.state == PullbackState.NOT_APPLICABLE
    assert result.invalidation_reason == "EODHD_COMPLETED_DAILY_REQUIRED"


def test_unsafe_volume_cannot_become_confirmed_entry():
    frame = _frame()
    frame.attrs["market_data"]["volume_safe_for_lookback"] = False
    result = _evaluate(frame)
    assert result.volume_behaviour == "UNAVAILABLE"
    assert result.state != PullbackState.CONFIRMED_PULLBACK_ENTRY
    assert "current_volume_ratio" in result.missing_measurements


def test_existing_breakout_and_breakdown_scenarios_are_unchanged():
    scenarios = evidence._scenarios(
        last_close=100.0, channel_high=102.0, channel_low=95.0, atr_val=2.0,
        trend="UPTREND", momentum="POSITIVE", volume_safe=True,
        volume_ratio=1.1, usable=True)
    assert [scenario.scenario_id for scenario in scenarios] == [
        "breakout_continuation", "support_breakdown"]
    assert (scenarios[0].trigger, scenarios[0].entry_high, scenarios[0].target,
            scenarios[0].stop) == (102.0, 103.0, 107.0, 96.5)


def test_research_evaluator_uses_one_bar_delay_and_costs():
    base = _frame()
    tail_index = pd.date_range(base.index[-1] + pd.offsets.BDay(), periods=25, freq="B")
    last = float(base["Close"].iloc[-1])
    tail_close = pd.Series(np.linspace(last, last * 1.10, 25), index=tail_index)
    tail = pd.DataFrame({
        "Open": tail_close.shift(1).fillna(last),
        "High": tail_close * 1.01,
        "Low": tail_close * 0.99,
        "Close": tail_close,
        "Adj Close": tail_close,
        "Volume": 700_000.0,
    }, index=tail_index)
    history = pd.concat([base, tail])
    history.attrs = dict(base.attrs)
    config = replace(DEFAULT_PULLBACK_RESEARCH_CONFIG, minimum_history_bars=80)
    report = evaluate_pullback_history({"AAA": history}, config=config)
    assert report.summary["scenarios_generated"] > 0
    for row in report.observations:
        assert row["execution_delay_bars"] == 1
        assert row["transaction_cost_bps"] == config.transaction_cost_bps


def test_research_report_writes_reproducible_json_and_csv(tmp_path):
    from core.ai_pullback_research import save_pullback_research_report

    base = _frame()
    tail = pd.concat([base.iloc[-1:].copy()] * 25)
    tail.index = pd.date_range(base.index[-1] + pd.offsets.BDay(), periods=25, freq="B")
    history = pd.concat([base, tail])
    history.attrs = dict(base.attrs)
    report = evaluate_pullback_history({"AAA": history})
    json_path, csv_path = save_pullback_research_report(report, tmp_path)
    assert json_path.parent == tmp_path and csv_path.parent == tmp_path
    assert report.report_id in json_path.name and report.report_id in csv_path.name
    assert json_path.exists() and csv_path.exists()
