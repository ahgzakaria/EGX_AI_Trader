import unittest
from unittest.mock import patch

import pandas as pd

from strategy.trading_decision import TradingDecisionService


def _base_buy(_frame, _index):
    return {
        "Signal": "BUY",
        "Stars": 5,
        "RR": 2.0,
        "Reasons": ["Technical rules passed"],
        "RejectReason": None,
        "DecisionTrace": {"Trend": "PASS"},
    }


class FakePredictor:
    def __init__(self, probability):
        self.probability = probability

    def predict(self, _features):
        return {"Probability": self.probability, "AILevel": "Good"}


class TradingDecisionTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame([{"Close": 100.0}])

    def test_ai_gate_turns_technical_buy_into_watch_everywhere(self):
        with patch(
            "strategy.trading_decision.settings.get",
            return_value={"enabled": True, "min_probability": 70},
        ):
            service = TradingDecisionService(
                predictor=FakePredictor(65),
                base_evaluator=_base_buy,
            )

        decision = service.evaluate(self.frame, 0)

        self.assertEqual(decision["Signal"], "WATCH")
        self.assertEqual(decision["RejectReason"], "AIProbability")
        self.assertEqual(decision["DecisionTrace"]["AIProbability"], "FAIL")

    def test_ai_gate_preserves_approved_buy(self):
        with patch(
            "strategy.trading_decision.settings.get",
            return_value={"enabled": True, "min_probability": 70},
        ):
            service = TradingDecisionService(
                predictor=FakePredictor(80),
                base_evaluator=_base_buy,
            )

        decision = service.evaluate(self.frame, 0)

        self.assertEqual(decision["Signal"], "BUY")
        self.assertTrue(decision["AIApproved"])
        self.assertEqual(decision["DecisionTrace"]["AIProbability"], "PASS")

    def test_live_advisory_never_demotes_strategy_buy(self):
        """Low live probability is evidence, not an entry-rule override."""
        with patch(
            "strategy.trading_decision.settings.get",
            return_value={"enabled": True, "min_probability": 70},
        ):
            service = TradingDecisionService(
                predictor=FakePredictor(25),
                base_evaluator=_base_buy,
                mode=TradingDecisionService.LIVE_ADVISORY,
            )

        decision = service.evaluate(self.frame, 0)

        self.assertEqual(decision["Signal"], "BUY")
        self.assertEqual(decision["Reasons"], ["Technical rules passed"])
        self.assertIsNone(decision["RejectReason"])
        self.assertEqual(decision["AIProbability"], 25)
        self.assertFalse(decision["AIApproved"])
        self.assertEqual(
            decision["DecisionTrace"]["AIProbability"], "ADVISORY_LOW"
        )
