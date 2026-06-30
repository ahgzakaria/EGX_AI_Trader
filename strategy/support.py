def support_resistance(df, window=20):

    support = float(df["Low"].rolling(window).min().iloc[-1])
    resistance = float(df["High"].rolling(window).max().iloc[-1])
    current_price = float(df["Close"].iloc[-1])

    score = 0
    confidence = 0
    reasons = []

    # قريب من الدعم (أقل من 3%)
    if current_price <= support * 1.03:
        score += 20
        confidence += 15
        reasons.append("Near Support")

    # بعيد عن المقاومة (أكثر من 5%)
    if current_price < resistance * 0.95:
        score += 10
        confidence += 10
        reasons.append("Room To Resistance")

    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons,
        "support": support,
        "resistance": resistance
    }