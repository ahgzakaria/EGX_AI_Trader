from strategy.trend import trend_score
from strategy.volume import volume_score
from strategy.support import support_resistance
from strategy.entry import entry_signal
from strategy.momentum import momentum_score
from strategy.candles import candle_score
from strategy.breakout import breakout_score
from strategy.signal import signal_engine

from core.strategy_result import StrategyResult


class StrategyEngine:

    def __init__(self, data):

        self.data = data

    def score(self, i):

        result = StrategyResult()

        trend = trend_score(self.data.df, i)
        volume = volume_score(self.data.df, i)
        support = support_resistance(self.data.df, i)
        entry = entry_signal(self.data.df, i)
        momentum = momentum_score(self.data.df, i)
        candles = candle_score(self.data.df, i)
        breakout = breakout_score(self.data.df, i)

        # ----------------------------
        # Total Score
        # ----------------------------

        result.score = (
            trend["score"]
            + volume["score"]
            + support["score"]
            + entry["score"]
            + momentum["score"]
            + candles["score"]
            + breakout["score"]
        )

        result.confidence = min(

            trend["confidence"]
            + volume["confidence"]
            + support["confidence"]
            + entry["confidence"]
            + momentum["confidence"]
            + candles["confidence"]
            + breakout["confidence"],

            100

        )

        result.reasons = (

            trend["reasons"]
            + volume["reasons"]
            + support["reasons"]
            + entry["reasons"]
            + momentum["reasons"]
            + candles["reasons"]
            + breakout["reasons"]

        )

        result.trend = trend["score"]
        result.volume = volume["score"]
        result.momentum = momentum["score"]
        result.candles = candles["score"]
        result.breakout = breakout["score"]

        result.support = support["support"]
        result.resistance = support["resistance"]

        result.buy_low = entry["BuyLow"]
        result.buy_high = entry["BuyHigh"]

        result.stop_loss = entry["StopLoss"]

        result.target1 = entry["Target1"]
        result.target2 = entry["Target2"]

        result.rr = entry["RR"]

        signal = signal_engine({

            "Score": result.score,

            "Confidence": result.confidence,

            "RR": result.rr,

            "Trend": result.trend,

            "Volume": result.volume,

            "Momentum": result.momentum

        })

        result.signal = signal["Signal"]
        result.stars = signal["Stars"]

        return result