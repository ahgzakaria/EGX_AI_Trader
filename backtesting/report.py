import os
import pandas as pd

from backtesting.statistics import BacktestStatistics
from backtesting.equity import EquityCurve


class BacktestReport:

    def __init__(self, trades):

        self.trades = trades

    # ==================================
    # Trades
    # ==================================

    def dataframe(self):

        return pd.DataFrame(

            [trade.__dict__ for trade in self.trades]

        )

    # ==================================
    # Statistics
    # ==================================

    def statistics(self):

        stats = BacktestStatistics(

            self.trades

        ).summary()

        return pd.DataFrame([stats])

    # ==================================
    # Equity Curve
    # ==================================

    def equity_curve(self):

        equity = EquityCurve(

            self.trades

        ).curve()

        return pd.DataFrame({

            "Trade": list(range(len(equity))),

            "Equity": equity

        })

    # ==================================
    # Save Trades
    # ==================================

    def save_trades(

        self,

        filename="reports/backtest_results.csv"

    ):

        os.makedirs(

            "reports",

            exist_ok=True

        )

        df = self.dataframe()

        df.to_csv(

            filename,

            index=False,

            encoding="utf-8-sig"

        )

        return df

    # ==================================
    # Save Statistics
    # ==================================

    def save_statistics(

        self,

        filename="reports/backtest_statistics.csv"

    ):

        stats = self.statistics()

        stats.to_csv(

            filename,

            index=False,

            encoding="utf-8-sig"

        )

        return stats

    # ==================================
    # Save Equity
    # ==================================

    def save_equity(

        self,

        filename="reports/equity_curve.csv"

    ):

        equity = self.equity_curve()

        equity.to_csv(

            filename,

            index=False,

            encoding="utf-8-sig"

        )

        return equity

    # ==================================
    # Save All
    # ==================================

    def save_all(self):

        self.save_trades()

        self.save_statistics()

        self.save_equity()