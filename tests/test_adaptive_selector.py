"""Phase 11 isolation, chronology, explainability, and regime tests."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from backtesting.config import load as load_backtest_config
from strategy_selector.market_classifier import (
    MarketClassification,
    MarketClassifier,
    MarketSnapshot,
    REGIMES,
)
from strategy_selector.selector import AdaptiveStrategySelector, load_selector_settings
from strategy_selector.selector_backtest import AdaptiveSelectorBacktester
from strategy_selector.selector_score import WalkForwardPerformanceLedger


ROOT = Path(__file__).resolve().parents[1]


def snapshot(**changes):
    values = dict(
        timestamp="2026-07-19", symbols=200, case30_trend="BULL",
        ema_alignment_ratio=0.60, average_adx=30, average_atr_percent=2,
        index_momentum_percent=1, index_volume_ratio=1.2, breadth=0.40,
        advancers=130, decliners=50, unchanged=20, new_highs=20,
        new_lows=2, breakout_frequency=0.10, average_rr=1.8,
        average_volume_ratio=1.3, gap_frequency=0.05,
        average_trend_score=70, average_momentum_score=70,
        average_confidence=75, average_edge_score=70,
        volatility_expansion=1.0, sector_strength=None,
        index_data_available=True,
    )
    values.update(changes)
    return MarketSnapshot(**values)


def classification(regime="BULL"):
    return MarketClassification(regime, 80.0, ("test",), snapshot(), (regime,))


def candidate(symbol, signal, exit_date, profit_percent, strategy):
    entry = (pd.Timestamp(signal) + pd.Timedelta(days=1)).date().isoformat()
    profit = profit_percent
    return SimpleNamespace(
        symbol=symbol, signal_date=str(signal), entry_date=entry,
        exit_date=str(exit_date), entry_price=100.0, exit_price=100.0 + profit,
        stop_loss=95.0, target1=105.0, target2=110.0, rr=2.0,
        result="WIN" if profit > 0 else "LOSS", exit_reason="TEST",
        profit=profit, profit_percent=profit_percent, score=80, confidence=80,
        trend_score=40, momentum_score=15, ai_rank=75.0,
        holding_days=max((pd.Timestamp(exit_date) - pd.Timestamp(entry)).days, 0),
        r_multiple=profit / 5.0, executed=False, shares=0,
        portfolio_profit=0.0, risk_amount=0.0, final_position_size=0,
        portfolio_rejection_reason="", ai_multiplier=1.0, ai_probability=None,
        ai_mode=strategy,
    )


def test_selector_is_disabled_and_decision_support_only():
    settings = load_selector_settings()
    assert settings["enabled_by_default"] is False
    assert settings["decision_support_only"] is True


def test_classifier_emits_declared_regimes_with_explanation():
    settings = load_selector_settings()
    classifier = MarketClassifier(settings)
    strong = classifier.classify(snapshot())
    panic = classifier.classify(snapshot(
        case30_trend="BEAR", breadth=-0.7, volatility_expansion=2.0,
        index_momentum_percent=-5,
    ))
    unknown = classifier.classify(snapshot(symbols=2))
    assert strong.regime == "STRONG_BULL"
    assert panic.regime == "PANIC"
    assert unknown.regime == "UNKNOWN"
    assert strong.reasons and panic.reasons and unknown.reasons
    assert {strong.regime, panic.regime, unknown.regime}.issubset(REGIMES)


def test_preference_is_learned_not_fixed_by_bull_label():
    settings = load_selector_settings()
    ledger = WalkForwardPerformanceLedger(settings)
    for _ in range(30):
        ledger.observe("CLASSIC", "BULL", 1.0)
        ledger.observe("BREAKOUT_SWING", "BULL", -1.0)
    selector = AdaptiveStrategySelector(settings, ledger)
    output = {"Signal": "BUY", "Score": 80, "Confidence": 80, "RR": 2}
    result = selector.select_symbol(output, {**output, "EdgeScore": 75}, classification())
    assert result["PreferredStrategy"] == "CLASSIC"
    assert result["FinalRecommendation"] == "BUY_CLASSIC"

    reverse = WalkForwardPerformanceLedger(settings)
    for _ in range(30):
        reverse.observe("CLASSIC", "BULL", -1.0)
        reverse.observe("BREAKOUT_SWING", "BULL", 1.0)
    result = AdaptiveStrategySelector(settings, reverse).select_symbol(
        output, {**output, "EdgeScore": 75}, classification()
    )
    assert result["PreferredStrategy"] == "BREAKOUT_SWING"
    assert result["FinalRecommendation"] == "BUY_BREAKOUT"


def test_bear_and_panic_never_generate_buy():
    settings = load_selector_settings()
    ledger = WalkForwardPerformanceLedger(settings)
    for _ in range(30):
        ledger.observe("CLASSIC", "BEAR", 1)
        ledger.observe("BREAKOUT_SWING", "BEAR", 1)
    output = {"Signal": "BUY", "Score": 100, "Confidence": 100, "RR": 3}
    result = AdaptiveStrategySelector(settings, ledger).select_symbol(
        output, {**output, "EdgeScore": 100}, classification("BEAR")
    )
    assert result["FinalRecommendation"] == "NO_TRADE"


def test_walk_forward_learning_uses_only_strictly_earlier_exits():
    settings = deepcopy(load_selector_settings())
    settings["minimum_regime_samples"] = 1
    classic = [
        candidate("AAA.CA", "2024-01-01", "2024-01-10", 2, "CLASSIC"),
        candidate("BBB.CA", "2024-01-05", "2024-01-08", -1, "CLASSIC"),
        candidate("CCC.CA", "2024-01-12", "2024-01-20", 2, "CLASSIC"),
    ]
    breakout = [
        candidate("DDD.CA", "2024-01-01", "2024-01-10", -1, "BREAKOUT_SWING"),
        candidate("EEE.CA", "2024-01-05", "2024-01-09", 2, "BREAKOUT_SWING"),
        candidate("FFF.CA", "2024-01-12", "2024-01-18", -1, "BREAKOUT_SWING"),
    ]
    regimes = {
        pd.Timestamp(date): classification("BULL")
        for date in ("2024-01-01", "2024-01-05", "2024-01-12")
    }
    result = AdaptiveSelectorBacktester(settings, load_backtest_config()).run(
        classic, breakout, regimes
    )
    early = [row for row in result["decisions"] if row["Date"] == "2024-01-05"]
    later = [row for row in result["decisions"] if row["Date"] == "2024-01-12"]
    assert early and all(row["LatestTrainingExitDate"] is None for row in early)
    assert later and all(row["LatestTrainingExitDate"] < row["Date"] for row in later)
    assert all(row["ChronologyValid"] for row in result["decisions"])


def test_strategy_edge_and_explanations_are_present():
    settings = load_selector_settings()
    ledger = WalkForwardPerformanceLedger(settings)
    result = AdaptiveStrategySelector(settings, ledger).select_symbol(
        {"Signal": "WATCH", "Score": 70, "Confidence": 70, "RR": 1},
        {"Signal": "BUY", "Score": 80, "Confidence": 80, "RR": 2, "EdgeScore": 75},
        classification("BULL"),
    )
    assert 0 <= result["StrategyEdgeScore"] <= 100
    assert result["WhyPreferred"]
    assert result["WhyNotOther"]
    assert result["SelectorReason"]


def test_frozen_classic_and_breakout_manifest_is_unchanged():
    def normalized_source_hash(path):
        # Git may check text out as CRLF on Windows. Freeze semantics, not checkout bytes.
        data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        return hashlib.sha256(data).hexdigest()

    manifest = json.loads(
        (ROOT / "strategy_selector" / "frozen_strategy_manifest.json").read_text(encoding="utf-8")
    )
    for relative, expected in manifest.items():
        actual = normalized_source_hash(ROOT / relative)
        assert actual == expected, relative


def test_selector_package_does_not_call_frozen_strategies_or_indicators():
    for path in (ROOT / "strategy_selector").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "calculate_indicators" not in source
        assert "TradingDecisionService" not in source
        assert "BreakoutSwingStrategy" not in source

