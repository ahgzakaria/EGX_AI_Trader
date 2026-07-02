def market_filter(df, i):

    last = df.iloc[i]

    reasons = []

    # ==========================
    # ADX Filter
    # ==========================

    if last["ADX"] < 25:

        reasons.append("Weak Trend")

        return {
            "passed": False,
            "reasons": reasons
        }

    # ==========================
    # EMA Alignment
    # ==========================

    if not (
        last["EMA20"] >
        last["EMA50"] >
        last["EMA200"]
    ):

        reasons.append("EMA Misalignment")

        return {
            "passed": False,
            "reasons": reasons
        }

    # ==========================
    # Price Position
    # ==========================

    if last["Close"] < last["EMA50"]:

        reasons.append("Below EMA50")

        return {
            "passed": False,
            "reasons": reasons
        }

    # ==========================
    # Passed
    # ==========================

    return {
        "passed": True,
        "reasons": ["Trending Market"]
    }