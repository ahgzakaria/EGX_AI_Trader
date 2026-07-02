from backtesting.config import ENTRY_WAIT_DAYS


class EntryManager:

    def __init__(

        self,

        costs

    ):

        self.costs = costs

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

            ENTRY_WAIT_DAYS + 1,

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

                context.entry_price = self.costs.entry_price(

                    buy_high

                )

                context.entry_date = str(

                    data.index[j].date()

                )

                # أول شمعة بعد الدخول
                context.entry_index = j + 1

                return True

        return False