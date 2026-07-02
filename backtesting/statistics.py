from backtesting.equity import EquityCurve


class BacktestStatistics:

    def __init__(self, trades, initial_capital=100000):

        self.trades = trades
        self.initial_capital = initial_capital

    def summary(self):

        total_trades = len(self.trades)

        if total_trades == 0:

            return {
                "Trades": 0,
                "Wins": 0,
                "Losses": 0,
                "BreakEven": 0,
                "WinRate": 0,
                "NetProfit": 0,
                "GrossProfit": 0,
                "GrossLoss": 0,
                "ProfitFactor": 0,
                "AverageWin": 0,
                "AverageLoss": 0,
                "Expectancy": 0,
                "AverageHoldingDays": 0,
                "AverageR": 0,
                "BestTrade": 0,
                "WorstTrade": 0,
                "LargestWinner": "",
                "LargestLoser": "",
                "InitialCapital": self.initial_capital,
                "FinalCapital": self.initial_capital,
                "TotalReturn": 0,
                "MaxDrawdown": 0
            }

        wins = sum(1 for t in self.trades if t.result == "WIN")
        losses = sum(1 for t in self.trades if t.result == "LOSS")
        breakeven = sum(1 for t in self.trades if t.result == "BREAKEVEN")

        win_rate = round(wins / total_trades * 100, 2)

        net_profit = round(sum(t.profit for t in self.trades), 2)

        gross_profit = round(sum(t.profit for t in self.trades if t.profit > 0), 2)

        gross_loss = round(abs(sum(t.profit for t in self.trades if t.profit < 0)), 2)

        profit_factor = round(
            gross_profit / gross_loss,
            2
        ) if gross_loss else 0

        average_win = round(
            gross_profit / wins,
            2
        ) if wins else 0

        average_loss = round(
            gross_loss / losses,
            2
        ) if losses else 0

        expectancy = round(
            net_profit / total_trades,
            2
        )

        average_holding = round(
            sum(t.holding_days for t in self.trades) / total_trades,
            2
        )

        average_r = round(
            sum(t.r_multiple for t in self.trades) / total_trades,
            2
        )

        best_trade = max(self.trades, key=lambda t: t.profit)
        worst_trade = min(self.trades, key=lambda t: t.profit)

        equity = EquityCurve(
            self.trades,
            self.initial_capital
        )

        return {

            "Trades": total_trades,

            "Wins": wins,

            "Losses": losses,

            "BreakEven": breakeven,

            "WinRate": win_rate,

            "NetProfit": net_profit,

            "GrossProfit": gross_profit,

            "GrossLoss": gross_loss,

            "ProfitFactor": profit_factor,

            "AverageWin": average_win,

            "AverageLoss": average_loss,

            "Expectancy": expectancy,

            "AverageHoldingDays": average_holding,

            "AverageR": average_r,

            "BestTrade": best_trade.profit,

            "WorstTrade": worst_trade.profit,

            "LargestWinner": best_trade.symbol,

            "LargestLoser": worst_trade.symbol,

            "InitialCapital": self.initial_capital,

            "FinalCapital": equity.final_capital(),

            "TotalReturn": equity.total_return(),

            "MaxDrawdown": equity.max_drawdown()

        }