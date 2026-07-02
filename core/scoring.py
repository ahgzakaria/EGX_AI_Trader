from strategy.filter import market_filter

from strategy.trend import trend_score
from strategy.volume import volume_score
from strategy.support import support_resistance
from strategy.entry import entry_signal
from strategy.momentum import momentum_score
from strategy.candles import candle_score
from strategy.breakout import breakout_score
from strategy.signal import signal_engine


def score_stock(df, i):

    # ==================================
    # Market Filter
    # ==================================

    market = market_filter(df, i)

    trend = trend_score(df, i)
    volume = volume_score(df, i)
    support = support_resistance(df, i)
    entry = entry_signal(df, i)
    momentum = momentum_score(df, i)
    candles = candle_score(df, i)
    breakout = breakout_score(df, i)

    score = (
        trend["score"]
        + volume["score"]
        + support["score"]
        + entry["score"]
        + momentum["score"]
        + candles["score"]
        + breakout["score"]
    )

    confidence = min(
        trend["confidence"]
        + volume["confidence"]
        + support["confidence"]
        + entry["confidence"]
        + momentum["confidence"]
        + candles["confidence"]
        + breakout["confidence"],
        100
    )

    reasons = (
        market["reasons"]
        + trend["reasons"]
        + volume["reasons"]
        + support["reasons"]
        + entry["reasons"]
        + momentum["reasons"]
        + candles["reasons"]
        + breakout["reasons"]
    )

    result = {

        "MarketPassed": market["passed"],

        "Score": score,
        "Confidence": confidence,
        "Reasons": reasons,

        "Trend": trend["score"],
        "Volume": volume["score"],
        "Momentum": momentum["score"],
        "Candles": candles["score"],
        "Breakout": breakout["score"],

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