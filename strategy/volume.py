def volume_score(df, i):

    last = df.iloc[i]

    score = 0
    confidence = 0
    reasons = []

    # متوسط آخر 20 جلسة حتى الشمعة الحالية
    start = max(0, i - 19)
    avg_volume = df["Volume"].iloc[start:i + 1].mean()

    # Volume
    if last["Volume"] > avg_volume * 1.5:
        score += 10
        confidence += 10
        reasons.append("High Volume")

    elif last["Volume"] > avg_volume:
        score += 5
        confidence += 5
        reasons.append("Volume Above Average")

    else:
        reasons.append("Normal Volume")

    # OBV Trend
    if i >= 5:

        obv_now = df["OBV"].iloc[i]
        obv_prev = df["OBV"].iloc[i - 5]

        if obv_now > obv_prev:
            score += 10
            confidence += 10
            reasons.append("OBV Rising")

        elif obv_now < obv_prev:
            reasons.append("OBV Falling")

    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons
    }