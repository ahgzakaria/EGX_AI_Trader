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
    # Symbol Statistics
    # ==================================

    def symbol_statistics(self):

        df = self.dataframe()

        if df.empty:

            return pd.DataFrame()

        rows = []

        for symbol, group in df.groupby("symbol"):

            wins = (group["result"] == "WIN").sum()

            losses = (group["result"] == "LOSS").sum()

            breakeven = (group["result"] == "BREAKEVEN").sum()

            gross_profit = group.loc[
                group["profit"] > 0,
                "profit"
            ].sum()

            gross_loss = abs(

                group.loc[
                    group["profit"] < 0,
                    "profit"
                ].sum()

            )

            if gross_loss == 0:

                profit_factor = round(
                    gross_profit,
                    2
                )

            else:

                profit_factor = round(
                    gross_profit / gross_loss,
                    2
                )

            rows.append({

                "Symbol": symbol,

                "Trades": len(group),

                "Wins": wins,

                "Losses": losses,

                "BreakEven": breakeven,

                "WinRate": round(
                    wins / len(group) * 100,
                    2
                ),

                "NetProfit": round(
                    group["profit"].sum(),
                    2
                ),

                "AverageProfit": round(
                    group["profit"].mean(),
                    2
                ),

                "ProfitFactor": profit_factor,

                "AverageR": round(
                    group["r_multiple"].mean(),
                    2
                ),

                "AverageHoldingDays": round(
                    group["holding_days"].mean(),
                    2
                ),

                "AverageRR": round(
                    group["rr"].mean(),
                    2
                ),

                "BestTrade": round(
                    group["profit"].max(),
                    2
                ),

                "WorstTrade": round(
                    group["profit"].min(),
                    2
                )

            })

        result = pd.DataFrame(rows)

        result = result.sort_values(

            [

                "ProfitFactor",

                "AverageR",

                "WinRate",

                "NetProfit"

            ],

            ascending=False

        )

        return result

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
    # Save Symbol Statistics
    # ==================================

    def save_symbol_statistics(

        self,

        filename="reports/symbol_statistics.csv"

    ):

        df = self.symbol_statistics()

        df.to_csv(

            filename,

            index=False,

            encoding="utf-8-sig"

        )

        return df

    # ==================================
    # Save All
    # ==================================

    def save_all(self):

        self.save_trades()

        self.save_statistics()

        self.save_equity()

        self.save_symbol_statistics()