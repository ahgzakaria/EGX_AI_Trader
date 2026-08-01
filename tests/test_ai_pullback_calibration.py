"""Calibration, execution and leakage tests for the research-only Pullback engine."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from core.ai_pullback_research import (
    PullbackResearchReport,
    _aggregate,
    _gate_diagnostics,
    evaluate_pullback_history,
    simulate_managed_pullback_trade,
)
from core.ai_pullback_calibration import (
    build_verdict_markdown,
    merge_research_reports,
    write_calibration_reports,
)
from core.ai_pullback_scenario import _confirmed_pivots
from core.universe import lookup
from tests.test_ai_pullback_scenario import _frame


def _trade_frame(rows):
    index = pd.date_range("2026-01-01", periods=len(rows), freq="B", name="Date")
    return pd.DataFrame(rows, index=index, columns=["Open", "High", "Low", "Close"])


def _scenario():
    return SimpleNamespace(stop_loss=9.0, target_1=12.0, target_2=14.0)


def _simulate(rows):
    return simulate_managed_pullback_trade(
        _trade_frame(rows), signal_position=0, scenario=_scenario(), symbol="AAA",
        maximum_holding_bars=len(rows) - 2, transaction_cost_bps=30.0)


def test_stop_touched_before_target_exits_at_stop():
    trade = _simulate([
        (10, 10.5, 9.8, 10), (10, 11.0, 9.5, 10.5),
        (10.5, 11.0, 8.8, 9.2), (9.2, 13.0, 9.1, 12.5)])
    assert trade["outcome"] == "STOP"
    assert trade["exit_date"] == "2026-01-05"
    assert trade["stop_hit"] is True and trade["target_1_hit"] is False


def test_target_touched_before_stop_exits_at_target_one():
    trade = _simulate([
        (10, 10.5, 9.8, 10), (10, 12.2, 9.5, 12.0),
        (12, 13.0, 8.0, 9.0)])
    assert trade["outcome"] == "TARGET_1"
    assert trade["first_touch_outcome"] == "TARGET_1"
    assert trade["target_1_hit"] is True and trade["stop_hit"] is False


def test_same_candle_stop_and_target_uses_conservative_stop_first():
    trade = _simulate([
        (10, 10.5, 9.8, 10), (10, 12.2, 8.8, 11.0),
        (11, 15.0, 10.5, 14.5)])
    assert trade["outcome"] == "STOP_SAME_BAR_AMBIGUOUS"
    assert trade["same_bar_ambiguity"] is True
    assert trade["same_bar_policy"] == "STOP_FIRST_CONSERVATIVE"


def test_trade_stops_accruing_after_exit():
    trade = _simulate([
        (10, 10.5, 9.8, 10), (10, 11.0, 8.8, 9.0),
        (9, 20.0, 9.0, 20.0), (20, 30.0, 19.0, 30.0)])
    assert trade["outcome"] == "STOP"
    assert trade["holding_bars"] == 1
    assert trade["exit_date"] == "2026-01-02"
    assert trade["path_stopped_at_exit"] is True
    assert trade["realized_return_percent"] < 0
    assert trade["mae_to_exit_percent"] == -10.0
    assert trade["mfe_to_exit_percent"] == 0.0


def test_gap_stop_does_not_count_post_exit_intraday_low():
    trade = _simulate([
        (10, 10.5, 9.8, 10), (10.0, 11.0, 9.5, 10.5),
        (8.0, 12.0, 6.0, 7.0), (7.0, 20.0, 6.5, 19.0)])
    assert trade["outcome"] == "STOP_SAME_BAR_AMBIGUOUS"
    assert trade["exit_price"] == 8.0
    assert trade["mae_to_exit_percent"] == -20.0
    assert trade["mfe_to_exit_percent"] == 10.0


def test_unmanaged_and_managed_metrics_are_separate():
    frame = _frame()
    tail = pd.concat([frame.iloc[-1:].copy()] * 25)
    tail.index = pd.date_range(frame.index[-1] + pd.offsets.BDay(), periods=25, freq="B")
    history = pd.concat([frame, tail])
    history.attrs = dict(frame.attrs)
    report = evaluate_pullback_history({"AAA": history})
    assert "unmanaged_forward_returns" in report.summary
    assert "managed_trades" in report.summary
    assert all(row["managed_exit_applied"] is False
               for row in report.unmanaged_forward_returns)
    assert all(row["path_stopped_at_exit"] is True for row in report.managed_trades)


def test_pivot_is_available_only_after_confirmation_bars():
    index = pd.date_range("2026-01-01", periods=7, freq="B")
    series = pd.Series([1.0, 2.0, 5.0, 3.0, 2.0, 2.5, 2.0], index=index)
    pivots = _confirmed_pivots(series, 2, high=True)
    pivot = next(item for item in pivots if item.value == 5.0)
    assert pivot.timestamp == index[2]
    assert pivot.confirmed_at == index[4]
    assert not _confirmed_pivots(series.iloc[:4], 2, high=True)


def test_rejection_funnel_counts_are_deterministic():
    frame = _frame()
    tail = pd.concat([frame.iloc[-1:].copy()] * 25)
    tail.index = pd.date_range(frame.index[-1] + pd.offsets.BDay(), periods=25, freq="B")
    history = pd.concat([frame, tail])
    history.attrs = dict(frame.attrs)
    first = evaluate_pullback_history({"AAA": history})
    second = evaluate_pullback_history({"AAA": history})
    assert first.gate_pass_rates == second.gate_pass_rates
    assert first.rejection_counts == second.rejection_counts


def test_bounded_evaluator_window_matches_full_engine_at_same_cutoff():
    from core import ai_analysis_evidence as evidence
    from core.ai_pullback_config import DEFAULT_PULLBACK_RESEARCH_CONFIG as config
    from core.ai_pullback_scenario import evaluate_pullback_scenario

    base = _frame()
    prefix = pd.concat([base.iloc[:60].copy(), base])
    prefix.index = pd.date_range("2025-10-01", periods=len(prefix), freq="B")
    prefix.attrs = dict(base.attrs)
    ema20 = evidence._ema(prefix["Close"], 20)
    ema50 = evidence._ema(prefix["Close"], 50)
    atr14 = evidence._atr_wilder(prefix["High"], prefix["Low"], prefix["Close"])
    full = evaluate_pullback_scenario(
        prefix, ema20=ema20, ema50=ema50, atr14=atr14, volume_safe=True,
        data_cutoff=prefix.index[-1].date())
    size = (config.structure_lookback_bars + config.maximum_impulse_lookback_bars
            + 2 * config.pivot_radius + config.slope_lookback_bars)
    bounded = evaluate_pullback_scenario(
        prefix.tail(size), ema20=ema20.tail(size), ema50=ema50.tail(size),
        atr14=atr14.tail(size), volume_safe=True,
        data_cutoff=prefix.index[-1].date())
    comparable = (
        "state", "swing_high", "impulse_low", "support_zone_low",
        "support_zone_high", "entry_trigger", "stop_loss", "target_1",
        "reward_risk", "gate_results", "rejection_reasons")
    assert {name: getattr(full, name) for name in comparable} == {
        name: getattr(bounded, name) for name in comparable}


def test_literal_null_ticker_remains_a_valid_universe_record():
    record = lookup("NULL")
    assert record is not None
    assert record.canonical_symbol == "NULL"


def _empty_report(**overrides):
    values = {
        "report_id": "test-report",
        "evaluator_version": "test",
        "pullback_engine_version": "test",
        "universe_snapshot": ("AAA",),
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "data_sources": ("EODHD",),
        "configuration": {},
        "summary": {"scenarios_generated": 100, "state_counts": {}},
        "observations": (),
        "unmanaged_forward_returns": (),
        "managed_trades": (),
        "gate_pass_rates": (),
        "rejection_counts": (),
        "run_metadata": {},
    }
    values.update(overrides)
    return PullbackResearchReport(**values)


def test_verdict_reports_largest_incremental_gate_loss():
    report = _empty_report(gate_pass_rates=(
        {"gate": "first", "cumulative_pass_count": 90},
        {"gate": "second", "cumulative_pass_count": 20},
        {"gate": "third", "cumulative_pass_count": 5},
    ))
    verdict = build_verdict_markdown(report)
    assert "`second` rejects 70 observations" in verdict


def test_calibration_report_writer_creates_all_required_outputs(tmp_path):
    report = merge_research_reports((_empty_report(),), run_metadata={
        "symbols_requested": 1,
        "symbols_with_sufficient_data": 1,
        "symbols_skipped": {},
    })
    paths = write_calibration_reports(report, output_dir=tmp_path)
    assert set(paths) == {
        "funnel", "rejections", "gates", "managed", "unmanaged",
        "sensitivity", "verdict",
    }
    assert all(path.exists() for path in paths.values())
    verdict = paths["verdict"].read_text("utf-8")
    assert "Pullback Calibration Verdict" in verdict
    assert "REJECTED_FOR_OPERATIONAL_USE" in verdict
    assert "RETAINED_AS_RESEARCH_BASELINE" in verdict


def test_maximum_drawdown_uses_chronological_trade_order_and_starting_equity():
    trades = (
        {"entry_date": "2026-01-02", "symbol": "B", "realized_return_percent": -20,
         "r_multiple": -1, "first_touch_outcome": "STOP", "stop_hit": True,
         "target_1_hit": False, "target_2_touched_before_exit": False,
         "holding_bars": 1, "mae_to_exit_percent": -20, "mfe_to_exit_percent": 0},
        {"entry_date": "2026-01-01", "symbol": "A", "realized_return_percent": 50,
         "r_multiple": 2, "first_touch_outcome": "TARGET_1", "stop_hit": False,
         "target_1_hit": True, "target_2_touched_before_exit": False,
         "holding_bars": 1, "mae_to_exit_percent": 0, "mfe_to_exit_percent": 50},
        {"entry_date": "2026-01-03", "symbol": "C", "realized_return_percent": -20,
         "r_multiple": -1, "first_touch_outcome": "STOP", "stop_hit": True,
         "target_1_hit": False, "target_2_touched_before_exit": False,
         "holding_bars": 1, "mae_to_exit_percent": -20, "mfe_to_exit_percent": 0},
    )
    summary = _aggregate((), (), (), trades)
    assert summary["managed_trades"]["maximum_drawdown_percent"] == -36.0


def test_primary_rejection_is_counted_even_when_not_a_contributing_reason():
    gates, reasons = _gate_diagnostics(({
        "gate_results": (("example", "FAIL"),),
        "primary_rejection_reason": "PRIMARY_ONLY",
        "rejection_reasons": ("DETAIL",),
    },))
    indexed = {row["reason"]: row for row in reasons}
    assert gates[0]["fail_count"] == 1
    assert indexed["PRIMARY_ONLY"] == {
        "reason": "PRIMARY_ONLY", "all_contributing_count": 0, "primary_count": 1}
