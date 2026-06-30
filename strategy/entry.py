def entry_signal(df):

    last = df.iloc[-1]

    support = float(df["Low"].rolling(20).min().iloc[-1])
    resistance = float(df["High"].rolling(20).max().iloc[-1])

    price = float(last["Close"])
    atr = float(last["ATR"])

    buy_low = max(price - atr * 0.5, support)
    buy_high = price

    stop_loss = support - atr * 0.5

    target1 = resistance
    target2 = resistance + atr * 2

    risk = price - stop_loss
    reward = target2 - price

    rr = round(reward / risk, 2) if risk > 0 else 0

    score = 0
    confidence = 0
    reasons = []

    if rr >= 3:
        score += 20
        confidence += 20
        reasons.append("Excellent Risk/Reward")

    elif rr >= 2:
        score += 15
        confidence += 15
        reasons.append("Good Risk/Reward")

    elif rr >= 1.5:
        score += 10
        confidence += 10
        reasons.append("Acceptable Risk/Reward")

    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons,

        "BuyLow": round(buy_low, 2),
        "BuyHigh": round(buy_high, 2),
        "StopLoss": round(stop_loss, 2),

        "Target1": round(target1, 2),
        "Target2": round(target2, 2),

        "RR": rr
    }