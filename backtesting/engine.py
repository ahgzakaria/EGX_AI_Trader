from core.data_provider import load_history, provider_purpose
from core.market_data import MarketData
from indicators.technical import calculate_indicators
from strategy.trading_decision import (
    REJECTION_REASONS,
    TradingDecisionService,
)

from backtesting.costs import TradingCosts
from backtesting.context import BacktestContext

from backtesting.managers.entry_manager import EntryManager
from backtesting.managers.exit_manager import ExitManager

from backtesting.builders.trade_builder import TradeBuilder

import gc


class BacktestEngine:

    def __init__(self, symbol, decision_service=None, execution_delay_bars=0):

        self.symbol = symbol
        self.trades = []
        self.decision_service = decision_service or TradingDecisionService(
            mode=TradingDecisionService.STRATEGY_ONLY
        )
        self.execution_delay_bars = max(0, int(execution_delay_bars))

        # ==================================
        # تتبع أسباب رفض كل يوم "قبل" ما يوصل حتى لمرحلة
        # الدخول (Signal-Level Rejections)، منفصل عن رفض
        # المحفظة (Capital/Heat) اللي بيحصل بعد كده.
        # ==================================

        self.signal_rejections = {reason: 0 for reason in REJECTION_REASONS}
        # Audits retain technical BUYs even when an AI overlay rejects them
        # before an entry can be constructed.
        self.signal_audits = []

    # ==================================
    # Load Data
    # ==================================

    def load(self):

        # Historical execution is pinned to backtest_provider (Yahoo by default).
        df = load_history(self.symbol, purpose="backtest")

        df = calculate_indicators(df)

        # ==================================
        # Validate Indicators
        # ==================================

        REQUIRED_COLUMNS = [

            "MACD",
            "MACD_Signal",
            "MACD_HIST",
            "MACD_CROSS_AGE",
            "RSI",
            "RSI7",
            "ADX",
            "ATR",
            "EMA20",
            "EMA50",
            "EMA200",

        ]

        missing = [

            col

            for col in REQUIRED_COLUMNS

            if col not in df.columns

        ]

        if missing:

            raise ValueError(

                f"{self.symbol}: Missing indicators -> "

                + ", ".join(missing)

            )

        self.data = MarketData(df)

    # ==================================
    # Run
    # ==================================

    def run(self, start_date=None, end_date=None):

        self.load()

        costs = TradingCosts()

        entry_manager = EntryManager(
            costs, execution_delay_bars=self.execution_delay_bars
        )

        exit_manager = ExitManager(costs)

        next_available_index = 200
        i = 200

        if start_date is not None:
            start_timestamp = self.data.index.searchsorted(start_date, side="left")
            i = max(i, int(start_timestamp))
            next_available_index = i

        while i < self.data.length - 20:

            if end_date is not None and self.data.index[i] > end_date:
                break

            if i < next_available_index:

                i += 1
                continue

            with provider_purpose("backtest"):
                signal = self.decision_service.evaluate(self.data.df, i)

            if signal.get("TechnicalSignal") == "BUY":
                self.signal_audits.append({
                    "symbol": self.symbol,
                    "signal_date": str(self.data.index[i].date()),
                    "final_signal": signal.get("Signal"),
                    "ai_mode": signal.get("AIMode", "STRATEGY_ONLY"),
                    "ai_probability": signal.get("AIProbability"),
                    "ai_multiplier": signal.get("AIPositionMultiplier", 1.0),
                    "ai_rank": signal.get("AIRank", 0.0),
                    "ai_rejection_reason": signal.get("AIRejectionReason", ""),
                })

            if signal["Signal"] != "BUY":

                reason = signal["RejectReason"]

                if reason in self.signal_rejections:
                    self.signal_rejections[reason] += 1

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

                self.signal_rejections["EntryTimeout"] += 1

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
