from datetime import timedelta
import unittest
from unittest.mock import patch

import pandas as pd

from ai.dataset import DatasetBuilder
from ai.walk_forward import WalkForwardValidator
from strategy.trading_decision import TradingDecisionService


def technical_buy(_frame, _index):
    return {
        "Signal": "BUY", "Stars": 5, "RR": 2.0,
        "Reasons": [], "RejectReason": None, "DecisionTrace": {},
    }


def sample_frame(rows=18):
    start = pd.Timestamp("2024-01-01")
    records = []
    for index in range(rows):
        record = {feature: float(index + 1) for feature in DatasetBuilder.FEATURES}
        record.update({
            "entry_date": start + timedelta(days=index),
            "exit_date": start + timedelta(days=index + 1),
            "result": index % 2,
        })
        records.append(record)
    return pd.DataFrame(records)


def sparse_calendar_frame(rows=24):
    start = pd.Timestamp("2024-01-01")
    records = []
    for index in range(rows):
        entry_date = start + timedelta(days=index * 3)
        record = {feature: float(index + 1) for feature in DatasetBuilder.FEATURES}
        record.update({
            "entry_date": entry_date,
            "exit_date": entry_date + timedelta(days=1),
            "result": index % 2,
        })
        records.append(record)
    return pd.DataFrame(records)


class WalkForwardTests(unittest.TestCase):
    def test_folds_only_train_on_labels_known_before_predictions(self):
        result = WalkForwardValidator(n_splits=3).validate(sample_frame(), persist=False)
        evaluated = result.folds[result.folds["status"] == "evaluated"]

        self.assertFalse(evaluated.empty)
        for _, fold in evaluated.iterrows():
            self.assertLess(fold["train_end"], fold["test_start"])

    def test_imputer_is_fit_from_train_rows_not_future_test_rows(self):
        frame = sample_frame()
        frame.loc[frame.index[-1], "rsi"] = 1000000.0
        result = WalkForwardValidator(n_splits=3).validate(frame, persist=False)
        deployed = result.historical_filter.folds

        self.assertTrue(deployed)
        for fold in deployed:
            median = fold["model"].named_steps["imputer"].statistics_[0]
            self.assertLess(median, 1000000.0)

    def test_historical_filter_covers_calendar_gaps_between_folds(self):
        result = WalkForwardValidator(n_splits=3).validate(sparse_calendar_frame(), persist=False)
        deployed = result.historical_filter.folds
        gap_date = deployed[0]["test_end"] + timedelta(days=1)

        self.assertLess(gap_date, deployed[1]["test_start"])
        prediction = result.historical_filter.predict(
            {feature: 1.0 for feature in DatasetBuilder.FEATURES},
            gap_date,
        )

        self.assertIsNotNone(prediction)
        self.assertIn("Probability", prediction)

    def test_historical_filter_normalizes_raw_indicator_column_names(self):
        result = WalkForwardValidator(n_splits=3).validate(sample_frame(), persist=False)
        raw_candle = {
            "RSI": 55,
            "ADX": 25,
            "ATR": 1.2,
            "MACD": 0.1,
            "EMA20_DIST": 2.5,
            "EMA50_DIST": 3.0,
            "EMA200_DIST": 4.0,
            "VOLUME_RATIO": 1.4,
            "ATR_PERCENT": 2.2,
            "BB_POSITION": 0.8,
            "OBV": 1000,
            "RR": 2.0,
            "RSI7": 60,
            "EMA20_SLOPE": 0.2,
            "EMA50_SLOPE": 0.1,
            "RSI_SLOPE": 0.3,
            "ADX_RISING": 1,
            "BB_WIDTH": 0.5,
            "OBV_SLOPE": 10,
            "DIST_HIGH20": 1.5,
            "DIST_LOW20": 2.5,
            "MACD_CROSS_AGE": 3,
        }

        prediction = result.historical_filter.predict(
            raw_candle,
            result.historical_filter.folds[0]["test_start"],
        )

        self.assertIsNotNone(prediction)
        self.assertIn("Probability", prediction)

    def test_strategy_only_never_constructs_global_model(self):
        frame = pd.DataFrame([{"Close": 100.0}])
        with patch("strategy.trading_decision.AIPredictor", side_effect=AssertionError):
            service = TradingDecisionService(
                mode=TradingDecisionService.STRATEGY_ONLY,
                base_evaluator=technical_buy,
            )

        decision = service.evaluate(frame, 0)
        self.assertEqual(decision["Signal"], "BUY")
        self.assertEqual(decision["DecisionTrace"]["AIProbability"], "DISABLED")

    def test_strict_walk_forward_fails_when_prediction_is_missing(self):
        class EmptyHistoricalFilter:
            def predict(self, _features, _date):
                return None

        frame = pd.DataFrame([{"Close": 100.0}])
        with patch(
            "strategy.trading_decision.settings.get",
            return_value={"enabled": True, "min_probability": 70},
        ):
            service = TradingDecisionService(
                mode=TradingDecisionService.WALK_FORWARD_AI,
                historical_ai_filter=EmptyHistoricalFilter(),
                strict_historical_predictions=True,
                base_evaluator=technical_buy,
            )

        with self.assertRaisesRegex(RuntimeError, "Missing out-of-sample"):
            service.evaluate(frame, 0)
