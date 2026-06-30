class ConfidenceEngine:

    def calculate(self, result):

        confidence = 50
        reasons = []

        # Trend
        if result["Trend"] >= 80:
            confidence += 20
            reasons.append("Strong Trend")

        elif result["Trend"] >= 60:
            confidence += 10
            reasons.append("Good Trend")

        # Volume
        if result["Volume"] >= 40:
            confidence += 15
            reasons.append("High Volume")

        elif result["Volume"] >= 20:
            confidence += 10
            reasons.append("Average Volume")

        # Risk / Reward
        if result["RR"] >= 3:
            confidence += 15
            reasons.append("Excellent Risk/Reward")

        elif result["RR"] >= 2:
            confidence += 10
            reasons.append("Good Risk/Reward")

        # Signal
        if result["Signal"] == "BUY":
            confidence += 10
            reasons.append("BUY Signal")

        elif result["Signal"] == "WATCH":
            confidence += 5
            reasons.append("WATCH Signal")

        confidence = max(0, min(confidence, 100))

        return {
            "Confidence": confidence,
            "Reasons": reasons
        }