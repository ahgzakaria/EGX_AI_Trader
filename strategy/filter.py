def market_filter(df, i, cfg):

    last = df.iloc[i]

    reasons = []

    adx = last["ADX"]

    # ==========================================
    # Market Regime
    # ==========================================

    if adx >= cfg.MARKET_TREND_ADX:

        regime = "TRENDING"

    elif adx >= cfg.MARKET_WEAK_TREND_ADX:

        regime = "WEAK_TREND"

    else:

        regime = "RANGING"

    # ==========================================
    # Trending Market
    # ==========================================

    if regime == "TRENDING":

        if not (

            last["EMA20"] >

            last["EMA50"]

        ):

            reasons.append("EMA20 below EMA50")

            return {

                "passed": False,

                "reasons": reasons,

                "regime": regime

            }

        if last["Close"] < last["EMA20"]:

            reasons.append("Price below EMA20")

            return {

                "passed": False,

                "reasons": reasons,

                "regime": regime

            }

    # ==========================================
    # Weak Trend
    # ==========================================

    elif regime == "WEAK_TREND":

        if last["Close"] < last["EMA50"]:

            reasons.append("Price below EMA50")

            return {

                "passed": False,

                "reasons": reasons,

                "regime": regime

            }

    # ==========================================
    # Ranging Market
    # ==========================================

    else:

        reasons.append("Ranging market rejected")

        return {

            "passed": False,

            "reasons": reasons,

            "regime": regime

        }

    # ==========================================
    # Passed
    # ==========================================

    return {

        "passed": True,

        "reasons": [regime],

        "regime": regime

    }
