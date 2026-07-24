import numpy as np
import unittest

from ai.predictor import AIPredictor


class OneClassModel:
    classes_ = np.array([0])

    def predict_proba(self, _features):
        return np.array([[1.0]])


class AIPredictorTests(unittest.TestCase):

 def test_predictor_handles_model_without_win_class(self):
    predictor = AIPredictor.__new__(AIPredictor)
    predictor.model = OneClassModel()
    predictor.features = ["rsi"]

    result = predictor.predict({"RSI": 50})

    self.assertEqual(result["Probability"], 0.0)
    self.assertEqual(result["AILevel"], "Weak")
