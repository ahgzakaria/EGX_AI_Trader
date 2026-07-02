class EquityCurve:

    def __init__(self, trades, initial_capital=100000):

        self.trades = trades
        self.initial_capital = initial_capital

    # ==================================
    # Equity Curve
    # ==================================

    def curve(self):

        equity = self.initial_capital

        values = [equity]

        for trade in self.trades:

            equity += trade.profit

            values.append(round(equity, 2))

        return values

    # ==================================
    # Max Drawdown
    # ==================================

    def max_drawdown(self):

        curve = self.curve()

        peak = curve[0]

        max_dd = 0

        for value in curve:

            if value > peak:

                peak = value

            drawdown = ((peak - value) / peak) * 100

            if drawdown > max_dd:

                max_dd = drawdown

        return round(max_dd, 2)

    # ==================================
    # Final Capital
    # ==================================

    def final_capital(self):

        return self.curve()[-1]

    # ==================================
    # Total Return %
    # ==================================

    def total_return(self):

        final = self.final_capital()

        return round(

            (
                (final - self.initial_capital)
                / self.initial_capital
            ) * 100,

            2

        )