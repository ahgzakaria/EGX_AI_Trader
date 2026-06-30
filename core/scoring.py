from strategy.trend import trend_score
from strategy.volume import volume_score
from strategy.support import support_resistance
from strategy.entry import entry_signal
from strategy.signal import signal_engine


def score_stock(df):

    trend = trend_score(df)
    volume = volume_score(df)
    support = support_resistance(df)
    entry = entry_signal(df)

    score = (
        trend["score"]
        + volume["score"]
        + support["score"]
        + entry["score"]
    )

    confidence = (
        trend["confidence"]
        + volume["confidence"]
        + support["confidence"]
        + entry["confidence"]
    )

    confidence = min(confidence, 100)

    reasons = (
        trend["reasons"]
        + volume["reasons"]
        + support["reasons"]
        + entry["reasons"]
    )

    result = {

        "Score": score,

        "Confidence": confidence,

        "Reasons": reasons,

        "Trend": trend["score"],

        "Volume": volume["score"],

        "Support": support["support"],

        "Resistance": support["resistance"],

        "BuyLow": entry["BuyLow"],

        "BuyHigh": entry["BuyHigh"],

        "StopLoss": entry["StopLoss"],

        "Target1": entry["Target1"],

        "Target2": entry["Target2"],

        "RR": entry["RR"]

    }

    signal = signal_engine(result)

    result["Signal"] = signal["Signal"]
    result["Stars"] = signal["Stars"]

    return result