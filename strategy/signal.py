from strategy.config import (
    MIN_SCORE,
    MIN_CONFIDENCE,
    MIN_RR,
    MIN_TREND,
    MIN_MOMENTUM,
    MIN_VOLUME
)


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

        and score >= MIN_SCORE

        and confidence >= MIN_CONFIDENCE

        and rr >= MIN_RR

        and trend >= MIN_TREND

        and momentum >= MIN_MOMENTUM

        and volume >= MIN_VOLUME

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