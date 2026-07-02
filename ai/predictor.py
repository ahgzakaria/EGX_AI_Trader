class AIPredictor:

    def predict(self, result):

        score = result["Score"]
        confidence = result["Confidence"]
        rr = result["RR"]

        trend = result["Trend"]
        volume = result["Volume"]
        momentum = result["Momentum"]

        probability = (
            score * 0.40 +
            confidence * 0.30 +
            rr * 10 +
            trend * 0.10 +
            volume * 0.05 +
            momentum * 0.05
        )

        probability = max(0, min(99, probability))

        if probability >= 90:
            level = "Very High"

        elif probability >= 80:
            level = "High"

        elif probability >= 70:
            level = "Good"

        elif probability >= 60:
            level = "Moderate"

        else:
            level = "Weak"

        return {

            "Probability": round(probability, 1),

            "AILevel": level

        }