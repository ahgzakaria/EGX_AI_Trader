def trend_score(df):

    last = df.iloc[-1]

    score = 0
    confidence = 0
    reasons = []

    # ترتيب المتوسطات
    if last["EMA20"] > last["EMA50"] > last["EMA200"]:
        score += 40
        confidence += 30
        reasons.append("EMA20 > EMA50 > EMA200")

    # السعر فوق EMA20
    if last["Close"] > last["EMA20"]:
        score += 20
        confidence += 10
        reasons.append("Price Above EMA20")

    # السعر فوق EMA50
    if last["Close"] > last["EMA50"]:
        score += 20
        confidence += 10
        reasons.append("Price Above EMA50")

    # السعر فوق EMA200
    if last["Close"] > last["EMA200"]:
        score += 20
        confidence += 10
        reasons.append("Price Above EMA200")

    confidence = min(confidence, 60)

    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons
    }