import os
import joblib
import pandas as pd

from ai.dataset import DatasetBuilder


class AIPredictor:

    def __init__(self):

        model_path = "ai/models/trading_model.pkl"

        if not os.path.exists(model_path):

            raise FileNotFoundError(
                f"Model not found -> {model_path}"
            )

        self.model = joblib.load(model_path)

        self.features = DatasetBuilder.FEATURES

    # ==================================
    # Normalize Column Names
    # ==================================

    def _normalize(self, candle):

        return {

            str(k).lower(): v

            for k, v in candle.items()

        }

    # ==================================
    # Build Feature Vector
    # ==================================

    def _build_features(self, candle):

        candle = self._normalize(candle)

        missing = []

        data = {}

        for feature in self.features:

            if feature in candle:

                data[feature] = candle[feature]

            else:

                data[feature] = 0

                missing.append(feature)

        if missing:

            print(

                "AI Warning - Missing Features:",

                ", ".join(missing)

            )

        return pd.DataFrame(

            [data],

            columns=self.features,

            dtype="float32"

        )

    # ==================================
    # AI Level
    # ==================================

    def _get_level(self, probability):

        if probability >= 90:

            return "Very High"

        elif probability >= 80:

            return "High"

        elif probability >= 70:

            return "Good"

        elif probability >= 60:

            return "Moderate"

        return "Weak"

    # ==================================
    # Predict
    # ==================================

    def predict(self, candle):

        X = self._build_features(candle)

        # لا نفترض أن الفئة الرابحة موجودة دائماً أو أن ترتيبها هو 1.
        # ذلك يحصل مثلاً مع مجموعة تدريب كلها خسائر/أرباح.
        classes = list(self.model.classes_)
        win_index = classes.index(1) if 1 in classes else None

        probability = (
            float(self.model.predict_proba(X)[0][win_index])
            if win_index is not None
            else 0.0
        ) * 100

        probability = round(probability, 1)

        return {

            "Probability": probability,

            "AILevel": self._get_level(probability)

        }
