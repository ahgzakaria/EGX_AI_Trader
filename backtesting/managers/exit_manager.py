from backtesting.config import (
    EXIT_MODE,
    MAX_HOLDING_DAYS,
    MOVE_TO_BREAKEVEN
)


class ExitManager:

    def __init__(

        self,

        costs

    ):

        self.costs = costs

    # ==================================
    # Manage Trade
    # ==================================

    def manage(

        self,

        context

    ):

        data = context.data

        signal = context.signal

        stop = signal["StopLoss"]

        break_even = False

        last_index = min(

            context.entry_index +

            MAX_HOLDING_DAYS - 1,

            data.length - 1

        )

        for k in range(

            context.entry_index,

            last_index + 1

        ):

            # ==================================
            # Target 1
            # ==================================

            if data.high[k] >= signal["Target1"]:

                if EXIT_MODE == "TARGET1":

                    context.exit_price = self.costs.exit_price(

                        signal["Target1"]

                    )

                    context.exit_date = str(

                        data.index[k].date()

                    )

                    context.exit_index = k

                    context.exit_reason = "Target1"

                    context.result = "WIN"

                    return

                elif MOVE_TO_BREAKEVEN:

                    break_even = True

                    stop = context.entry_price

            # ==================================
            # Stop Loss
            # ==================================

            if data.low[k] <= stop:

                context.exit_price = self.costs.exit_price(

                    stop

                )

                context.exit_date = str(

                    data.index[k].date()

                )

                context.exit_index = k

                if break_even:

                    context.result = "BREAKEVEN"

                    context.exit_reason = "BreakEven"

                else:

                    context.result = "LOSS"

                    context.exit_reason = "StopLoss"

                return

            # ==================================
            # Target 2
            # ==================================

            if (

                EXIT_MODE == "TARGET2"

                and

                data.high[k] >= signal["Target2"]

            ):

                context.exit_price = self.costs.exit_price(

                    signal["Target2"]

                )

                context.exit_date = str(

                    data.index[k].date()

                )

                context.exit_index = k

                context.result = "WIN"

                context.exit_reason = "Target2"

                return

        # ==================================
        # Timeout
        # ==================================

        context.exit_price = self.costs.exit_price(

            float(

                data.close[last_index]

            )

        )

        context.exit_date = str(

            data.index[last_index].date()

        )

        context.exit_index = last_index

        context.exit_reason = "Timeout"

        if context.exit_price > context.entry_price:

            context.result = "WIN"

        else:

            context.result = "LOSS"