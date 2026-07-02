def trend_score(df, i):

    last = df.iloc[i]

    score = 0
    confidence = 0
    reasons = []

    ema20 = last["EMA20"]
    ema50 = last["EMA50"]
    ema200 = last["EMA200"]
    close = last["Close"]

    # ترتيب المتوسطات
    if ema20 > ema50 > ema200:
        score += 20
        confidence += 20
        reasons.append("Perfect EMA Alignment")

    elif ema20 > ema50:
        score += 12
        confidence += 12
        reasons.append("Bullish EMA Alignment")

    # السعر فوق المتوسطات
    if close > ema20:
        score += 3
        confidence += 5
        reasons.append("Above EMA20")

    if close > ema50:
        score += 3
        confidence += 5
        reasons.append("Above EMA50")

    if close > ema200:
        score += 4
        confidence += 10
        reasons.append("Above EMA200")

    # قوة الاتجاه
    trend_strength = ((ema20 - ema50) / ema50) * 100

    if trend_strength >= 3:
        confidence += 10
        reasons.append("Strong Trend")

    elif trend_strength >= 1:
        confidence += 5
        reasons.append("Moderate Trend")

    return {
        "score": score,
        "confidence": min(confidence, 60),
        "reasons": reasons
    }