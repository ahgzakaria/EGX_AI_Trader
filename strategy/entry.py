def entry_signal(df, i):

    last = df.iloc[i]

    start = max(0, i - 19)

    support = float(df["Low"].iloc[start:i + 1].min())

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

        and

        volume > avg_volume * 1.2

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

        close_position = (

            price - low

        ) / candle_range

        if close_position >= 0.80:

            score += 4
            confidence += 5

            reasons.append("Strong Close")

    # ==================================
    # Entry
    # ==================================

    buy_low = round(

        max(

            support,

            price - atr * 0.30

        ),

        2

    )

    buy_high = round(price, 2)

    stop_loss = round(

        support - atr * 0.30,

        2

    )

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
    # Dynamic Targets
    # ==================================

    target1 = round(

        resistance,

        2

    )

    target2 = round(

        resistance + atr * 2,

        2

    )

    reward = target2 - buy_high

    # Both targets are anchored to `resistance`, the highest high of the
    # previous twenty bars excluding today. When today's close has already
    # passed that level, target1 sits *below* the entry and the reward is
    # negative before anything has happened: OFH.CA on 2026-08-26 closed at
    # 1.10 against a 1.00 resistance and reported RR -0.05.
    #
    # The same condition is rewarded eight points and the reason "Confirmed
    # Breakout" forty lines above, so the stronger the breakout, the more
    # negative the reward. That contradiction is not resolvable here by
    # inventing a target: this strategy's quality filter demands at least 3%
    # of room *below* resistance, and dissolving the contradiction the other
    # way was measured and is worse -- profit factor 0.90 against 0.94, return
    # -23.79% against -14.27%. A stock trading above its resistance is simply
    # not a setup this strategy takes.
    #
    # So it says so, the way `risk <= 0` already does, instead of emitting a
    # negative number that reaches the dashboard with nothing to explain it.
    # See docs/audits/strategies/SCORE_DIAGNOSIS.md.
    if reward <= 0:

        return {

            "score": 0,
            "confidence": 0,
            "reasons": ["No Reward Above Entry"],

            "BuyLow": buy_low,
            "BuyHigh": buy_high,

            "StopLoss": stop_loss,

            "Target1": target1,
            "Target2": target2,

            "RR": 0

        }

    rr = round(

        reward / risk,

        2

    )

    # ==================================
    # RR Score
    # ==================================

    if rr >= 3:

        score += 10
        confidence += 10

        reasons.append("Excellent RR")

    elif rr >= 2:

        score += 8
        confidence += 8

        reasons.append("Very Good RR")

    elif rr >= 1.5:

        score += 5
        confidence += 5

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