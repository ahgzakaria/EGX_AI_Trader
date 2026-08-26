def candle_score(df, i):

    # Every pattern below reads Open, so none of them mean what they are named
    # for unless the Open was observed. Where the source carries it forward from
    # the previous close -- 96-98% of bars in every source this project can
    # reach -- Bullish Engulfing needs Open < prev Close and Harami needs
    # Open > prev Close, both reducing to x < x; Doji degenerates into "the
    # close barely moved from yesterday" and fires on 63% of trades; Morning
    # Star becomes a comparison of lagged returns.
    #
    # So when the Open is *known* to be fabricated, this refuses to score and
    # says why, rather than reporting a Morning Star that did not happen. It
    # starts working again on its own the day a real Open arrives.
    integrity = (df.attrs or {}).get("open_integrity")
    verdict = getattr(integrity, "verdict", None)
    open_status = getattr(verdict, "value", None) or "UNKNOWN"

    # Only a positive finding silences it. UNKNOWN means the frame lost its
    # provenance in transit -- pandas `attrs` does not survive every operation
    # -- and absence of evidence is not evidence of fabrication. Condemning on
    # UNKNOWN would silently disable candles wherever attrs were dropped.
    if verdict is not None and open_status in ("CARRIED_FORWARD", "OUT_OF_RANGE"):
        return {
            "score": 0,
            "confidence": 0,
            "reasons": [f"Candle patterns unavailable (Open {open_status})"],
            "open_integrity": open_status
        }

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