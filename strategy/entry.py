def entry_signal(df, i):

    last = df.iloc[i]

    start = max(0, i - 19)

    support = float(df["Low"].iloc[start:i + 1].min())

    # المقاومة بدون الشمعة الحالية
    if i > 0:
        resistance = float(df["High"].iloc[start:i].max())
    else:
        resistance = float(df["High"].iloc[0])

    price = float(last["Close"])
    high = float(last["High"])
    low = float(last["Low"])

    atr = float(last["ATR"])

    volume = float(last["Volume"])
    avg_volume = float(df["Volume"].iloc[start:i + 1].mean())

    score = 0
    confidence = 0
    reasons = []

    # ==================================
    # Breakout
    # ==================================

    breakout = (

        price > resistance
        and volume > avg_volume * 1.2

    )

    if breakout:

        score += 8
        confidence += 10

        reasons.append("Confirmed Breakout")

    # ==================================
    # Pullback
    # ==================================

    if abs(price - support) <= atr:

        score += 6
        confidence += 8

        reasons.append("Near Support")

    # ==================================
    # Strong Close
    # ==================================

    candle_range = high - low

    if candle_range > 0:

        close_position = (price - low) / candle_range

        if close_position >= 0.80:

            score += 4
            confidence += 5

            reasons.append("Strong Close")

    # ==================================
    # Buy Zone
    # ==================================

    buy_low = round(

        max(

            support,

            price - atr * 0.30

        ),

        2

    )

    buy_high = round(price, 2)

    # ==================================
    # Stop Loss
    # ==================================

    stop_loss = round(

        support - atr * 0.30,

        2

    )

    # ==================================
    # Risk
    # ==================================

    risk = buy_high - stop_loss

    if risk <= 0:

        return {

            "score": 0,

            "confidence": 0,

            "reasons": ["Invalid Risk"],

            "BuyLow": buy_low,
            "BuyHigh": buy_high,

            "StopLoss": stop_loss,

            "Target1": buy_high,
            "Target2": buy_high,

            "RR": 0

        }

    # ==================================
    # Targets (Risk Based)
    # ==================================

    target1 = round(

        buy_high + risk,

        2

    )

    target2 = round(

        buy_high + risk * 2,

        2

    )

    rr = round(

        (target2 - buy_high) / risk,

        2

    )

    # ==================================
    # RR Score
    # ==================================

    if rr >= 2:

        score += 8
        confidence += 8

        reasons.append("Excellent RR")

    elif rr >= 1.5:

        score += 4
        confidence += 4

        reasons.append("Good RR")

    return {

        "score": score,

        "confidence": confidence,

        "reasons": reasons,

        "BuyLow": buy_low,
        "BuyHigh": buy_high,

        "StopLoss": stop_loss,

        "Target1": target1,
        "Target2": target2,

        "RR": rr

    }