from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from ai.risk_overlay import AIRiskOverlay
from portfolio.portfolio_simulator import PortfolioSimulator
from portfolio.sizing import PositionSizer
from strategy.trading_decision import TradingDecisionService


OVERLAY_CONFIG = {
    "position_sizing": {
        "bands": [
            {"min_probability": 75, "multiplier": 1.0},
            {"min_probability": 60, "multiplier": 0.75},
            {"min_probability": 45, "multiplier": 0.5},
        ],
        "below_band_action": "size",
        "below_band_multiplier": 0.25,
    },
    "ranking": {
        "weights": {
            "strategy_quality": 0.40,
            "ai_probability": 0.35,
            "risk_reward": 0.15,
            "confidence": 0.05,
            "market_regime": 0.05,
        },
        "rr_cap": 5.0,
        "regime_scores": {"BULL": 1.0, "SIDEWAYS": 0.5, "BEAR": 0.0},
    },
    "hybrid": {"emergency_min_probability": 20},
}


def technical_buy(_frame, _index):
    return {
        "Signal": "BUY", "Stars": 5, "RR": 2.0, "Score": 80,
        "Confidence": 70, "Regime": "BULL", "Reasons": [],
        "RejectReason": None, "DecisionTrace": {},
    }


class FixedHistoricalFilter:
    def __init__(self, probability):
        self.probability = probability
        self.calls = 0

    def predict(self, _features, _date):
        self.calls += 1
        return {"Probability": self.probability, "AILevel": "Good"}


def settings_value(section):
    return {
        "ai": {"enabled": True, "min_probability": 60},
        "ai_risk_overlay": OVERLAY_CONFIG,
    }.get(section, {})


def portfolio_trade(symbol, rank=0.0, multiplier=1.0):
    return SimpleNamespace(
        symbol=symbol,
        entry_date="2024-01-02",
        exit_date="2024-01-04",
        entry_price=10.0,
        exit_price=11.0,
        stop_loss=9.0,
        profit=1.0,
        ai_rank=rank,
        ai_multiplier=multiplier,
        ai_mode="AI_RANKING_ONLY" if rank else "AI_POSITION_SIZING",
        executed=False,
        shares=0,
        portfolio_profit=0.0,
        final_position_size=0,
        portfolio_rejection_reason="",
    )


class AIRiskOverlayTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame([{"Close": 100.0}])

    def _service(self, mode, probability):
        return TradingDecisionService(
            mode=mode,
            historical_ai_filter=FixedHistoricalFilter(probability),
            strict_historical_predictions=True,
            base_evaluator=technical_buy,
        )

    @patch("strategy.trading_decision.settings.get", side_effect=settings_value)
    def test_hard_filter_preserves_legacy_rejection(self, _settings):
        decision = self._service(TradingDecisionService.AI_HARD_FILTER, 55).evaluate(
            self.frame, 0
        )
        self.assertEqual(decision["Signal"], "WATCH")
        self.assertEqual(decision["AIRejectionReason"], "AIProbability")

    @patch("strategy.trading_decision.settings.get", side_effect=settings_value)
    def test_position_sizing_keeps_technical_buy_and_applies_band(self, _settings):
        decision = self._service(
            TradingDecisionService.AI_POSITION_SIZING, 50
        ).evaluate(self.frame, 0)
        self.assertEqual(decision["Signal"], "BUY")
        self.assertEqual(decision["AIPositionMultiplier"], 0.5)

    @patch("strategy.trading_decision.settings.get", side_effect=settings_value)
    def test_ranking_only_never_rejects_and_sets_rank(self, _settings):
        decision = self._service(
            TradingDecisionService.AI_RANKING_ONLY, 80
        ).evaluate(self.frame, 0)
        self.assertEqual(decision["Signal"], "BUY")
        self.assertEqual(decision["AIPositionMultiplier"], 1.0)
        self.assertGreater(decision["AIRank"], 0)

    @patch("strategy.trading_decision.settings.get", side_effect=settings_value)
    def test_hybrid_rejects_only_emergency_probability(self, _settings):
        decision = self._service(TradingDecisionService.AI_HYBRID, 15).evaluate(
            self.frame, 0
        )
        self.assertEqual(decision["Signal"], "WATCH")
        self.assertEqual(decision["AIRejectionReason"], "AIEmergencyProbability")

    def test_strategy_only_is_unaffected_by_ai_and_never_queries_filter(self):
        filter_ = FixedHistoricalFilter(1)
        service = TradingDecisionService(
            mode=TradingDecisionService.STRATEGY_ONLY,
            historical_ai_filter=filter_,
            base_evaluator=technical_buy,
        )
        decision = service.evaluate(self.frame, 0)
        self.assertEqual(decision["Signal"], "BUY")
        self.assertEqual(decision["AIPositionMultiplier"], 1.0)
        self.assertEqual(filter_.calls, 0)

    def test_position_size_multiplier_is_applied_to_base_shares(self):
        trade = portfolio_trade("SIZE.CA", multiplier=0.5)
        base_shares = PositionSizer(1000, 2).calculate(10.0, 9.0)["Shares"]
        result = PortfolioSimulator(
            [trade], initial_capital=1000, risk_percent=2,
            allow_overlapping_trades=True, max_open_positions=10,
            max_portfolio_risk_percent=10,
        ).run()
        self.assertEqual(len(result["executed_trades"]), 1)
        self.assertEqual(trade.final_position_size, int(base_shares * 0.5))

    def test_ranking_selects_highest_rank_when_capacity_is_limited(self):
        low = portfolio_trade("LOW.CA", rank=10)
        high = portfolio_trade("HIGH.CA", rank=90)
        result = PortfolioSimulator(
            [low, high], initial_capital=1000, risk_percent=2,
            allow_overlapping_trades=True, max_open_positions=1,
            max_portfolio_risk_percent=2,
        ).run()
        self.assertEqual([trade.symbol for trade in result["executed_trades"]], ["HIGH.CA"])
