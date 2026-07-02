def support_resistance(df, i, window=20):

    start = max(0, i - window + 1)

    support = float(df["Low"].iloc[start:i + 1].min())
    resistance = float(df["High"].iloc[start:i + 1].max())

    current_price = float(df["Close"].iloc[i])

    score = 0
    confidence = 0
    reasons = []

    distance_to_support = (
        (current_price - support) / support
    ) * 100

    distance_to_resistance = (
        (resistance - current_price) / current_price
    ) * 100

    # قريب من الدعم
    if distance_to_support <= 3:
        score += 10
        confidence += 10
        reasons.append("Near Support")

    # مساحة صعود جيدة
    if distance_to_resistance >= 5:
        score += 5
        confidence += 5
        reasons.append("Good Upside Potential")

    return {

        "score": score,

        "confidence": confidence,

        "reasons": reasons,

        "support": round(support, 2),

        "resistance": round(resistance, 2)

    }