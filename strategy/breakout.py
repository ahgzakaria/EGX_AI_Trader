def breakout_score(df, i):

    last = df.iloc[i]

    score = 0
    confidence = 0
    reasons = []

    # لا يمكن حساب الاختراق قبل وجود بيانات كافية
    if i < 20:
        return {
            "score": score,
            "confidence": confidence,
            "reasons": reasons
        }

    start = i - 19

    # المقاومة من آخر 20 شمعة السابقة (بدون الشمعة الحالية)
    resistance = float(df["High"].iloc[start:i].max())

    # متوسط الحجم حتى الشمعة الحالية
    avg_volume = float(df["Volume"].iloc[start:i + 1].mean())

    # اختراق مقاومة
    if last["Close"] > resistance:

        score += 10
        confidence += 10
        reasons.append("Resistance Breakout")

        # تأكيد بالحجم
        if last["Volume"] > avg_volume * 1.5:
            score += 10
            confidence += 10
            reasons.append("Confirmed By Volume")

    # قريب جداً من الاختراق
    elif last["Close"] >= resistance * 0.98:

        score += 5
        confidence += 5
        reasons.append("Near Breakout")

    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons
    }