import pandas as pd

import strategy.config as config

from backtesting.engine import BacktestEngine
from backtesting.statistics import BacktestStatistics


class StrategyEvaluator:

    def __init__(self):

        self.symbols = (

            pd.read_csv("data/symbols.csv")["Ticker"]

            .dropna()

            .unique()

            .tolist()

        )

    # ==================================

    def evaluate(

        self,

        params

    ):

        # ---------------------------------
        # Apply Parameters
        # ---------------------------------

        config.MIN_SCORE = params.score

        config.MIN_CONFIDENCE = params.confidence

        config.MIN_RR = params.rr

        config.MIN_TREND = params.trend

        config.MIN_MOMENTUM = params.momentum

        config.MIN_VOLUME = params.volume

        # ---------------------------------

        trades = []

        successful = 0

        failed = 0

        # ---------------------------------

        for symbol in self.symbols:

            try:

                engine = BacktestEngine(symbol)

                trades.extend(

                    engine.run()

                )

                successful += 1

            except Exception:

                failed += 1

        # ---------------------------------

        if len(trades) == 0:

            return None

        summary = BacktestStatistics(

            trades

        ).summary()

        # ---------------------------------

        return {

            "Score": params.score,

            "Confidence": params.confidence,

            "RR": params.rr,

            "Trend": params.trend,

            "Momentum": params.momentum,

            "Volume": params.volume,

            "Trades": len(trades),

            "Successful": successful,

            "Failed": failed,

            "WinRate": summary["WinRate"],

            "ProfitFactor": summary["ProfitFactor"],

            "NetProfit": summary["NetProfit"],

            "Expectancy": summary["Expectancy"],

            "MaxDrawdown": summary["MaxDrawdown"]

        }