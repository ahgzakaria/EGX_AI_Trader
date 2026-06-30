from strategy.trend import trend_score
from strategy.volume import volume_score
from strategy.support import support_resistance
from strategy.entry import entry_signal
from strategy.signal import signal_engine


def score_stock(df):

    trend = trend_score(df)

    volume = volume_score(df)

    levels = support_resistance(df)

    entry = entry_signal(df)

    total = trend + volume

    result = {
        "Score": total,
        "Trend": trend,
        "Volume": volume,

        "Support": levels["support"],
        "Resistance": levels["resistance"],

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