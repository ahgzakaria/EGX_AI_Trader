def candle_score(df, i):

    # Every pattern below reads Open. Where the source supplies a carried-forward
    # Open, two of them are structurally unreachable -- Bullish Engulfing needs
    # Open < prev Close and Harami needs Open > prev Close, and both reduce to
    # x < x when Open equals prev Close -- while Doji degenerates into "the close
    # barely moved". The verdict is attached by providers.base_provider once per
    # frame; it is reported here, and deliberately does NOT change the score.
    # Gating on it would change which signals fire and is a separate decision.
    integrity = (df.attrs or {}).get("open_integrity")
    open_status = getattr(integrity, "verdict", None)
    open_status = getattr(open_status, "value", None) or "UNKNOWN"

    if i < 2:
        return {
            "score": 0,
            "confidence": 0,
            "reasons": [],
            "open_integrity": open_status
        }

    last = df.iloc[i]
    prev = df.iloc[i - 1]
    prev2 = df.iloc[i - 2]

    score = 0
    confidence = 0
    reasons = []

    # Bullish Engulfing
    if (
        prev["Close"] < prev["Open"]
        and last["Close"] > last["Open"]
        and last["Open"] < prev["Close"]
        and last["Close"] > prev["Open"]
    ):
        score += 15
        confidence += 15
        reasons.append("Bullish Engulfing")

    # Hammer
    body = abs(last["Close"] - last["Open"])
    lower = min(last["Close"], last["Open"]) - last["Low"]
    upper = last["High"] - max(last["Close"], last["Open"])

    if body > 0:
        if (
            lower > body * 2
            and upper < body
        ):
            score += 8
            confidence += 8
            reasons.append("Hammer")

    # Doji
    candle_range = last["High"] - last["Low"]

    if candle_range > 0:
        if body <= candle_range * 0.1:
            score += 5
            confidence += 5
            reasons.append("Doji")

    # Morning Star
    if (
        prev2["Close"] < prev2["Open"]
        and abs(prev["Close"] - prev["Open"])
            < abs(prev2["Close"] - prev2["Open"]) * 0.4
        and last["Close"] > last["Open"]
        and last["Close"]
            > (prev2["Open"] + prev2["Close"]) / 2
    ):
        score += 15
        confidence += 15
        reasons.append("Morning Star")

    # Bullish Harami
    if (
        prev["Close"] < prev["Open"]
        and last["Close"] > last["Open"]
        and last["Open"] > prev["Close"]
        and last["Close"] < prev["Open"]
    ):
        score += 10
        confidence += 10
        reasons.append("Bullish Harami")

    return {
        "score": score,
        "confidence": confidence,
        "reasons": reasons,
        "open_integrity": open_status
    }