from backtesting.config import load as load_backtest_config


class EntryManager:

    def __init__(

        self,

        costs,

        execution_delay_bars=0

    ):

        self.costs = costs
        self.execution_delay_bars = max(0, int(execution_delay_bars))

        # بنقرا الإعدادات Live وقت إنشاء الـ Manager (بداية كل
        # سهم فى الـ backtest)، مش مجمّدة وقت استيراد الملف.

        self.entry_wait_days = load_backtest_config().ENTRY_WAIT_DAYS

    # ==================================
    # Find Entry
    # ==================================

    def find_entry(

        self,

        context

    ):

        data = context.data

        start = context.signal_index + 1

        end = min(

            context.signal_index +

            self.entry_wait_days + 1,

            data.length

        )

        buy_low = context.signal["BuyLow"]
        buy_high = context.signal["BuyHigh"]

        for j in range(start, end):

            if (

                data.low[j] <= buy_high

                and

                data.high[j] >= buy_low

            ):

                execution_index = j + self.execution_delay_bars
                if execution_index >= data.length:
                    return False

                # The default remains the existing buy-zone execution. The
                # optional delayed path is used only by a Phase 5 stress test.
                execution_price = (
                    buy_high if self.execution_delay_bars == 0
                    else float(data.open[execution_index])
                )
                context.entry_price = self.costs.entry_price(execution_price)

                context.entry_date = str(

                    data.index[execution_index].date()

                )

                # أول شمعة بعد الدخول
                context.entry_index = execution_index + 1

                return True

        return False
