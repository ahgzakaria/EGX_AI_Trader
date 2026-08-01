"""Operational-boundary tests for diagnostic-only Pullback Health Analysis."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from core import ai_analysis_evidence as evidence
from core.ai_stock_analysis_contract import (
    AnalysisRequest,
    MarketPhase,
    PullbackScenarioResult,
    PullbackState,
)
from tests.test_ai_pullback_scenario import _frame


ROOT = Path(__file__).resolve().parents[1]


def _request():
    return AnalysisRequest(
        symbol="AAA",
        request_id="pullback-isolation",
        as_of="2026-07-22T14:30:00+03:00",
        market_phase=MarketPhase.CLOSED,
        include_live=False,
    )


def _build(monkeypatch, pullback):
    monkeypatch.setattr(
        evidence, "evaluate_pullback_scenario", lambda *args, **kwargs: pullback)
    return evidence.build_evidence(
        _request(), _frame(), market_phase=MarketPhase.CLOSED,
        generated_at="2026-07-22T14:30:00+03:00",
    )


def _operational_decision_snapshot(result):
    return {
        "recommendation": result.recommendation,
        "recommendation_reasons": result.recommendation_reasons,
        "confidence": asdict(result.confidence),
        "scenarios": tuple(asdict(item) for item in result.scenarios),
        "key_levels": tuple(asdict(item) for item in result.key_levels),
    }


def test_pullback_changes_cannot_change_decision_confidence_or_breakout_scenarios(
        monkeypatch):
    rejected = PullbackScenarioResult(
        state=PullbackState.FAILED_PULLBACK,
        research_score=0.0,
        entry_trigger=1.0,
        stop_loss=0.5,
        target_1=2.0,
    )
    research_confirmation = PullbackScenarioResult(
        state=PullbackState.CONFIRMED_PULLBACK_ENTRY,
        research_score=100.0,
        entry_trigger=1_000.0,
        stop_loss=900.0,
        target_1=2_000.0,
    )
    first = _build(monkeypatch, rejected)
    second = _build(monkeypatch, research_confirmation)

    assert first.pullback_scenario != second.pullback_scenario
    assert _operational_decision_snapshot(first) == _operational_decision_snapshot(second)


def test_pullback_has_no_live_trigger_or_rubix_subscription_path():
    service = (ROOT / "core" / "ai_stock_analysis_service.py").read_text("utf-8")
    contract = (ROOT / "core" / "ai_stock_analysis_contract.py").read_text("utf-8")
    page = (ROOT / "dashboard" / "ai_stock_analysis.py").read_text("utf-8")

    forbidden = (
        "confirm_pullback_with_rubix",
        "pullback_live_confirmation",
        "RubixMappingResolver",
        "LIVE_TRIGGER_RECLAIMED",
    )
    combined = service + contract + page
    assert all(token not in combined for token in forbidden)
    assert not (ROOT / "core" / "ai_pullback_live.py").exists()


def test_operational_consumers_do_not_reference_pullback_result():
    operational_roots = (
        "portfolio",
        "strategy",
        "strategy_breakout",
        "strategy_selector",
        "decision_support",
        "scalping",
        "scalping_expected_range",
        "scalping_uptrend_pullback",
        "services",
        "providers",
    )
    forbidden_tokens = ("pullback_scenario", "PullbackScenarioResult")
    offenders = []
    for root_name in operational_roots:
        for path in (ROOT / root_name).rglob("*.py"):
            source = path.read_text("utf-8", errors="replace")
            if any(token in source for token in forbidden_tokens):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_pullback_modules_do_not_write_or_plan_operational_actions():
    forbidden = (
        "paper_trades",
        "position_size",
        "broker execution",
        "watchlist inclusion",
        "subscription planning",
        "create_alert",
        "send_alert",
    )
    sources = "\n".join(
        path.read_text("utf-8", errors="replace").lower()
        for path in (ROOT / "core").glob("ai_pullback_*.py")
    )
    assert all(token not in sources for token in forbidden)


def test_ai_analysis_service_runs_pullback_automatically(monkeypatch):
    from core.ai_stock_analysis_service import analyze_symbol

    calls = []
    real_evaluator = evidence.evaluate_pullback_scenario

    def recording_evaluator(frame, **kwargs):
        calls.append((float(frame["Close"].iloc[-1]), kwargs["data_cutoff"]))
        return real_evaluator(frame, **kwargs)

    monkeypatch.setattr(evidence, "evaluate_pullback_scenario", recording_evaluator)
    frame = _frame()
    response = analyze_symbol(
        "AAA",
        now=datetime(2026, 7, 22, 15, 0, tzinfo=ZoneInfo("Africa/Cairo")),
        include_live=False,
        history_loader=lambda symbol: frame,
    )

    assert len(calls) == 1
    assert calls[0][1] == frame.attrs["market_data"]["latest_completed_session"]
    assert response.result.pullback_scenario is not None
    assert response.result.pullback_scenario.historical_data_cutoff == calls[0][1]


def test_new_symbol_and_analysis_date_recalculate_without_reusing_previous_result(
        monkeypatch):
    from core.ai_stock_analysis_service import analyze_symbol

    calls = []

    def diagnostic(frame, **kwargs):
        current = float(frame["Close"].iloc[-1])
        cutoff = str(kwargs["data_cutoff"])
        calls.append((current, cutoff))
        return PullbackScenarioResult(
            state=PullbackState.DEVELOPING_PULLBACK,
            current_price=current,
            historical_data_cutoff=cutoff,
        )

    monkeypatch.setattr(evidence, "evaluate_pullback_scenario", diagnostic)
    first_frame = _frame(final_close=14.80)
    second_frame = _frame(final_close=15.40)
    second_frame.index = second_frame.index + pd.offsets.BDay(1)
    second_frame.attrs["market_data"]["latest_completed_session"] = (
        second_frame.index[-1].date().isoformat())

    first = analyze_symbol(
        "AAA", as_of="2026-07-22T15:00:00+03:00", include_live=False,
        history_loader=lambda symbol: first_frame)
    second = analyze_symbol(
        "BBB", as_of="2026-07-23T15:00:00+03:00", include_live=False,
        history_loader=lambda symbol: second_frame)

    assert len(calls) == 2
    assert first.result.request.symbol == "AAA"
    assert second.result.request.symbol == "BBB"
    assert first.result.request.as_of != second.result.request.as_of
    assert first.result.pullback_scenario.current_price != second.result.pullback_scenario.current_price
    assert first.result.pullback_scenario.historical_data_cutoff != (
        second.result.pullback_scenario.historical_data_cutoff)


def test_pullback_failure_preserves_analysis_decision_confidence_and_scenarios(monkeypatch):
    baseline = evidence.build_evidence(
        _request(), _frame(), market_phase=MarketPhase.CLOSED,
        generated_at="2026-07-22T14:30:00+03:00")

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic pullback failure")

    monkeypatch.setattr(evidence, "evaluate_pullback_scenario", fail)
    protected = evidence.build_evidence(
        _request(), _frame(), market_phase=MarketPhase.CLOSED,
        generated_at="2026-07-22T14:30:00+03:00")

    assert protected.pullback_scenario.state == PullbackState.NOT_APPLICABLE
    assert protected.pullback_scenario.invalidation_reason == (
        "PULLBACK_DIAGNOSTIC_ERROR:RuntimeError")
    assert "synthetic pullback failure" not in protected.pullback_scenario.invalidation_reason
    assert _operational_decision_snapshot(protected) == _operational_decision_snapshot(baseline)


def test_insufficient_analysis_still_contains_visible_pullback_reason():
    result = evidence.build_insufficient_evidence(
        _request(), market_phase=MarketPhase.CLOSED,
        generated_at="2026-07-22T14:30:00+03:00")
    assert result.pullback_scenario is not None
    assert result.pullback_scenario.state == PullbackState.NOT_APPLICABLE
    assert result.pullback_scenario.invalidation_reason == (
        "INSUFFICIENT_COMPLETED_DAILY_HISTORY")


def test_narrative_contains_correction_quality_without_upgrading_recommendation():
    from core.ai_analysis_narrative import build_fallback_narrative

    result = evidence.build_evidence(
        _request(), _frame(), market_phase=MarketPhase.CLOSED,
        generated_at="2026-07-22T14:30:00+03:00")
    before = result.recommendation
    narrative = build_fallback_narrative(result)

    assert "تحليل جودة التصحيح" in narrative.rationale
    assert "لا تغيّر التوصية العامة" in narrative.rationale
    assert result.recommendation == before
