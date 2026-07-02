from core.data_loader import load_data
from core.market_data import MarketData

from indicators.technical import calculate_indicators
from core.scoring import score_stock

from backtesting.trade import Trade
from backtesting.costs import TradingCosts

import gc


class BacktestEngine:

    def __init__(self, symbol):

        self.symbol = symbol
        self.trades = []

    def load(self):

        df = load_data(self.symbol)
        df = calculate_indicators(df)

        self.data = MarketData(df)

    def run(self):

        self.load()

        costs = TradingCosts()

        # ==================================
        # يمنع تداخل الصفقات
        # ==================================

        next_available_index = 200

        i = 200

        while i < self.data.length - 20:

            # لو لسه فيه صفقة مفتوحة
            if i < next_available_index:
                i += 1
                continue

            result = score_stock(self.data.df, i)

            if result["Signal"] != "BUY":
                i += 1
                continue

            buy_low = result["BuyLow"]
            buy_high = result["BuyHigh"]

            entry = None
            entry_date = None
            start = None

            # ==================================
            # انتظار الدخول (5 جلسات)
            # ==================================

            for j in range(i + 1, min(i + 6, self.data.length)):

                if (
                    self.data.low[j] <= buy_high
                    and self.data.high[j] >= buy_low
                ):

                    entry = costs.entry_price(buy_high)

                    entry_date = str(
                        self.data.index[j].date()
                    )

                    start = j + 1

                    break

            if entry is None:
                i += 1
                continue

            stop = result["StopLoss"]

            exit_price = None
            exit_date = None

            trade_result = None
            exit_reason = None

            break_even = False

            exit_index = start

            # ==================================
            # إدارة الصفقة
            # ==================================

            for k in range(start, min(start + 20, self.data.length)):

                exit_index = k

                if (
                    not break_even
                    and self.data.high[k] >= result["Target1"]
                ):

                    break_even = True
                    stop = entry

                if self.data.low[k] <= stop:

                    exit_price = costs.exit_price(stop)

                    exit_date = str(
                        self.data.index[k].date()
                    )

                    if break_even:

                        trade_result = "BREAKEVEN"
                        exit_reason = "BreakEven"

                    else:

                        trade_result = "LOSS"
                        exit_reason = "StopLoss"

                    break

                if self.data.high[k] >= result["Target2"]:

                    exit_price = costs.exit_price(
                        result["Target2"]
                    )

                    exit_date = str(
                        self.data.index[k].date()
                    )

                    trade_result = "WIN"
                    exit_reason = "Target2"

                    break

            # ==================================
            # Timeout
            # ==================================

            if trade_result is None:

                exit_index = min(
                    start + 19,
                    self.data.length - 1
                )

                exit_price = costs.exit_price(
                    float(self.data.close[exit_index])
                )

                exit_date = str(
                    self.data.index[exit_index].date()
                )

                if exit_price > entry:

                    trade_result = "WIN"

                else:

                    trade_result = "LOSS"

                exit_reason = "Timeout"

            profit = costs.net_profit(
                entry,
                exit_price
            )

            candle = self.data.df.iloc[i]

            trade = Trade(

                symbol=self.symbol,

                entry_date=entry_date,
                exit_date=exit_date,

                entry_price=entry,
                exit_price=exit_price,

                stop_loss=result["StopLoss"],

                target1=result["Target1"],
                target2=result["Target2"],

                rr=result["RR"],

                result=trade_result,
                exit_reason=exit_reason,

                profit=profit,

                score=result["Score"],
                confidence=result["Confidence"],

                trend_score=result["Trend"],
                volume_score=result["Volume"],
                momentum_score=result["Momentum"],
                candle_score=result["Candles"],
                breakout_score=result["Breakout"],

                rsi=round(float(candle["RSI"]), 2),
                adx=round(float(candle["ADX"]), 2),
                atr=round(float(candle["ATR"]), 2),
                macd=round(float(candle["MACD"]), 4),

                reasons=" | ".join(result["Reasons"])

            )

            self.trades.append(trade)

            # ==================================
            # لا يسمح بصفقة جديدة قبل انتهاء الحالية
            # ==================================

            next_available_index = exit_index + 1

            i = next_available_index

        self.data = None

        gc.collect()

        return self.trades