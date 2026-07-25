class EquityCurve:

    def __init__(
        self,
        trades,
        initial_capital=100000,
        profit_field="portfolio_profit"
    ):

        # ==================================
        # ترتيب زمني إجباري حسب تاريخ الخروج
        # ==================================
        # لازم نرتب الصفقات بتاريخ الخروج (مش بترتيب الرموز
        # كما كانت مكتوبة فى الملف الأصلي) عشان منحنى رأس
        # المال يمثل تاريخ محفظة حقيقي.

        self.trades = sorted(
            trades,
            key=lambda t: t.exit_date
        )

        self.initial_capital = initial_capital

        self.profit_field = profit_field

    # ==================================
    # Equity Curve
    # ==================================

    def curve(self):

        equity = self.initial_capital

        values = [equity]

        for trade in self.trades:

            equity += getattr(trade, self.profit_field)

            values.append(round(equity, 2))

        return values

    # ==================================
    # Max Drawdown (%)
    # ==================================

    def max_drawdown(self):

        curve = self.curve()

        peak = curve[0]
        max_dd = 0

        for value in curve:

            if value > peak:
                peak = value

            if peak > 0:
                drawdown = ((peak - value) / peak) * 100
            else:
                drawdown = 0

            if drawdown > max_dd:
                max_dd = drawdown

        return round(max_dd, 2)

    # ==================================
    # Max Drawdown (بالجنيه - مطلوب لحساب Recovery Factor)
    # ==================================

    def max_drawdown_amount(self):

        curve = self.curve()

        peak = curve[0]
        max_dd_amount = 0

        for value in curve:

            if value > peak:
                peak = value

            drawdown_amount = peak - value

            if drawdown_amount > max_dd_amount:
                max_dd_amount = drawdown_amount

        return round(max_dd_amount, 2)

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