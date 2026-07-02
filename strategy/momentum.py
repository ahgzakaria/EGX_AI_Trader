def momentum_score(df, i):

    last = df.iloc[i]

    score = 0
    confidence = 0
    reasons = []

    # ==========================
    # RSI
    # ==========================

    if 45 <= last["RSI"] <= 65:
        score += 5
        confidence += 5
        reasons.append("Healthy RSI")

    elif 35 <= last["RSI"] < 45:
        score += 3
        confidence += 3
        reasons.append("RSI Recovering")

    # ==========================
    # MACD
    # ==========================

    if (
        last["MACD"] > last["MACD_Signal"]
        and last["MACD"] > 0
    ):
        score += 10
        confidence += 10
        reasons.append("Strong Bullish MACD")

    elif last["MACD"] > last["MACD_Signal"]:
        score += 5
        confidence += 5
        reasons.append("Bullish MACD")

    # ==========================
    # ADX
    # ==========================

    if last["ADX"] >= 30:
        score += 5
        confidence += 10
        reasons.append("Strong Trend (ADX)")

    elif last["ADX"] >= 25:
        score += 3
        confidence += 5
        reasons.append("Trending Market")

    # ==========================
    # Bollinger Bands
    # ==========================

    if (
        last["Close"] > last["BB_MIDDLE"]
        and last["Close"] < last["BB_UPPER"]
    ):
        score += 5
        confidence += 5
        reasons.append("Inside Upper Bollinger")

    elif last["Close"] <= last["BB_LOWER"]:
        score += 3
        confidence += 3
        reasons.append("Near Lower Bollinger")

    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons
    }