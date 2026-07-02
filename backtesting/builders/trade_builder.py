from backtesting.trade import Trade


class TradeBuilder:

    @staticmethod
    def build(context):

        candle = context.data.df.iloc[
            context.signal_index
        ]

        signal = context.signal

        return Trade(

            symbol=context.symbol,

            entry_date=context.entry_date,
            exit_date=context.exit_date,

            entry_price=context.entry_price,
            exit_price=context.exit_price,

            stop_loss=signal["StopLoss"],

            target1=signal["Target1"],
            target2=signal["Target2"],

            rr=signal["RR"],

            result=context.result,
            exit_reason=context.exit_reason,

            profit=context.profit,

            score=signal["Score"],
            confidence=signal["Confidence"],

            trend_score=signal["Trend"],
            volume_score=signal["Volume"],
            momentum_score=signal["Momentum"],
            candle_score=signal["Candles"],
            breakout_score=signal["Breakout"],

            rsi=round(float(candle["RSI"]), 2),
            adx=round(float(candle["ADX"]), 2),
            atr=round(float(candle["ATR"]), 2),
            macd=round(float(candle["MACD"]), 4),

            reasons=" | ".join(signal["Reasons"])

        )