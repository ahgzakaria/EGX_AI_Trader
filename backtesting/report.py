import os
import pandas as pd

from backtesting.statistics import BacktestStatistics
from backtesting.equity import EquityCurve


class BacktestReport:

    def __init__(
        self,
        trades,
        profit_field="portfolio_profit",
        prices=None,
    ):

        # هنا برضه بنستبعد أي صفقة اترفضت (executed=False)
        # عشان التقارير تعكس بس الصفقات اللي حصلت فعلاً.

        self.trades = [
            t for t in trades
            if getattr(t, "executed", True)
        ]

        self.profit_field = profit_field

        #: Daily closes, so the saved statistics and the saved equity curve
        #: both mark open positions to market. `None` reproduces the
        #: pre-2026-08-29 closed-trade curve exactly.
        self.prices = prices

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
            self.trades,
            profit_field=self.profit_field,
            prices=self.prices,
        ).summary()

        return pd.DataFrame([stats])

    # ==================================
    # Equity Curve
    # ==================================

    def equity_curve(self):
        """The saved curve, daily and marked to market where prices allow.

        The `Equity` column keeps its name and meaning, so the dashboard chart
        that reads it is unaffected. What changes is the row: one per session
        rather than one per exit, with a `Date` to plot against, and a value
        that includes open positions. Without prices this returns the original
        per-trade curve unchanged.
        """

        curve = EquityCurve(
            self.trades,
            profit_field=self.profit_field,
            prices=self.prices,
        )

        daily = curve.daily_curve()
        if len(daily):
            return pd.DataFrame({
                "Date": [str(day.date()) for day in daily.index],
                "Trade": list(range(len(daily))),
                "Equity": [round(float(value), 2) for value in daily],
            })

        equity = curve.curve()
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

        profit_col = self.profit_field

        rows = []

        for symbol, group in df.groupby("symbol"):

            wins = (group["result"] == "WIN").sum()
            losses = (group["result"] == "LOSS").sum()
            breakeven = (group["result"] == "BREAKEVEN").sum()

            gross_profit = group.loc[
                group[profit_col] > 0,
                profit_col
            ].sum()

            gross_loss = abs(
                group.loc[
                    group[profit_col] < 0,
                    profit_col
                ].sum()
            )

            if gross_loss == 0:
                profit_factor = round(gross_profit, 2)
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
                    group[profit_col].sum(),
                    2
                ),

                "AverageProfit": round(
                    group[profit_col].mean(),
                    2
                ),

                "AverageProfitPercent": round(
                    group["profit_percent"].mean(),
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

                "AverageShares": round(
                    group["shares"].mean(),
                    2
                ),

                "BestTrade": round(
                    group[profit_col].max(),
                    2
                ),

                "WorstTrade": round(
                    group[profit_col].min(),
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

        os.makedirs("reports", exist_ok=True)

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