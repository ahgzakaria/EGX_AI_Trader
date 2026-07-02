from core.data_loader import load_data
from core.market_data import MarketData
from indicators.technical import calculate_indicators
from core.scoring import score_stock

from backtesting.costs import TradingCosts
from backtesting.context import BacktestContext

from backtesting.managers.entry_manager import EntryManager
from backtesting.managers.exit_manager import ExitManager

from backtesting.builders.trade_builder import TradeBuilder

import gc


class BacktestEngine:

    def __init__(self, symbol):

        self.symbol = symbol
        self.trades = []

    # ==================================
    # Load Data
    # ==================================

    def load(self):

        df = load_data(self.symbol)

        df = calculate_indicators(df)

        self.data = MarketData(df)

    # ==================================
    # Run
    # ==================================

    def run(self):

        self.load()

        costs = TradingCosts()

        entry_manager = EntryManager(costs)

        exit_manager = ExitManager(costs)

        next_available_index = 200

        i = 200

        while i < self.data.length - 20:

            if i < next_available_index:

                i += 1
                continue

            signal = score_stock(

                self.data.df,

                i

            )

            if signal["Signal"] != "BUY":

                i += 1

                continue

            context = BacktestContext(

                symbol=self.symbol,

                data=self.data,

                signal_index=i,

                signal=signal

            )

            # ==========================
            # Entry
            # ==========================

            if not entry_manager.find_entry(context):

                i += 1

                continue

            # ==========================
            # Exit
            # ==========================

            exit_manager.manage(context)

            # ==========================
            # Profit
            # ==========================

            context.profit = costs.net_profit(

                context.entry_price,

                context.exit_price

            )

            # ==========================
            # Trade
            # ==========================

            context.trade = TradeBuilder.build(

                context

            )

            self.trades.append(

                context.trade

            )

            # ==========================
            # Next Trade
            # ==========================

            next_available_index = (

                context.exit_index + 1

            )

            i = next_available_index

        self.data = None

        gc.collect()

        return self.trades