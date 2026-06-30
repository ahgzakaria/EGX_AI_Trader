def volume_score(df):

    avg_volume = df["Volume"].tail(20).mean()
    current_volume = df["Volume"].iloc[-1]

    score = 0
    confidence = 0
    reasons = []

    # حجم تداول مرتفع جدًا
    if current_volume >= avg_volume * 1.5:
        score += 40
        confidence += 20
        reasons.append("High Volume")

    # حجم تداول أعلى من المتوسط
    elif current_volume >= avg_volume:
        score += 20
        confidence += 10
        reasons.append("Volume Above Average")

    else:
        reasons.append("Normal Volume")

    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons
    }