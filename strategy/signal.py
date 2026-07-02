def signal_engine(result):

    score = result["Score"]
    confidence = result["Confidence"]
    rr = result["RR"]

    trend = result["Trend"]
    volume = result["Volume"]
    momentum = result["Momentum"]

    market_passed = result["MarketPassed"]

    # ==================================
    # BUY
    # ==================================

    if (

        market_passed

        and score >= 65

        and confidence >= 80

        and rr >= 2

        and trend >= 25

        and momentum >= 5

        and volume >= 5

    ):

        signal = "BUY"
        stars = 5

    # ==================================
    # WATCH (Strong)
    # ==================================

    elif (

        score >= 60

        and confidence >= 65

    ):

        signal = "WATCH"
        stars = 4

    # ==================================
    # WATCH (Weak)
    # ==================================

    elif score >= 40:

        signal = "WATCH"
        stars = 3

    # ==================================
    # AVOID
    # ==================================

    else:

        signal = "AVOID"
        stars = 2

    return {

        "Signal": signal,

        "Stars": stars

    }