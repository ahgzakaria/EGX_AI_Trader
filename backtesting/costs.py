from backtesting.config import load as load_backtest_config


class TradingCosts:

    def __init__(
        self,
        commission=None,
        slippage=None
    ):

        """
        Trading Costs Configuration

        commission = 0.003  -> 0.30%
        slippage  = 0.0005 -> 0.05%

        لو محددتش commission/slippage صراحة، بيتقروا Live من
        settings.json وقت إنشاء الكلاس (مش وقت استيراد الملف).
        """

        cfg = load_backtest_config()

        self.commission = (
            commission
            if commission is not None
            else cfg.COMMISSION
        )

        self.slippage = (
            slippage
            if slippage is not None
            else cfg.SLIPPAGE
        )

    # ==================================
    # Entry Execution Price
    # ==================================

    def entry_price(self, price):

        return round(

            price *

            (1 + self.slippage),

            4

        )

    # ==================================
    # Exit Execution Price
    # ==================================

    def exit_price(self, price):

        return round(

            price *

            (1 - self.slippage),

            4

        )

    # ==================================
    # Commission Cost
    # ==================================

    def commission_cost(

        self,

        entry_price,

        exit_price

    ):

        return round(

            (entry_price + exit_price)

            * self.commission,

            4

        )

    # ==================================
    # Gross Profit
    # ==================================

    def gross_profit(

        self,

        entry_price,

        exit_price

    ):

        return round(

            exit_price - entry_price,

            2

        )

    # ==================================
    # Net Profit
    # ==================================

    def net_profit(

        self,

        entry_price,

        exit_price

    ):

        gross = self.gross_profit(

            entry_price,

            exit_price

        )

        commission = self.commission_cost(

            entry_price,

            exit_price

        )

        return round(

            gross - commission,

            2

        )