"""Operational-boundary tests for diagnostic-only Pullback Health Analysis."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

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
