from backtesting.trade import Trade


class TradeBuilder:

    @staticmethod
    def build(context):

        candle = context.data.df.iloc[
            context.signal_index
        ]

        signal = context.signal

        return Trade(

            # ==================================
            # Basic
            # ==================================

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

            # ==================================
            # Strategy Scores
            # ==================================

            score=signal["Score"],
            confidence=signal["Confidence"],

            trend_score=signal["Trend"],
            volume_score=signal["Volume"],
            momentum_score=signal["Momentum"],
            candle_score=signal["Candles"],
            breakout_score=signal["Breakout"],

            # ==================================
            # Raw Indicators
            # ==================================

            rsi=round(float(candle["RSI"]), 2),

            adx=round(float(candle["ADX"]), 2),

            atr=round(float(candle["ATR"]), 4),

            macd=round(float(candle["MACD"]), 4),

            # ==================================
            # AI Features
            # ==================================

            ema20_dist=round(
                float(candle["EMA20_DIST"]),
                4
            ),

            ema50_dist=round(
                float(candle["EMA50_DIST"]),
                4
            ),

            ema200_dist=round(
                float(candle["EMA200_DIST"]),
                4
            ),

            volume_ratio=round(
                float(candle["VOLUME_RATIO"]),
                4
            ),

            atr_percent=round(
                float(candle["ATR_PERCENT"]),
                4
            ),

            bb_position=round(
                float(candle["BB_POSITION"]),
                4
            ),

            obv=float(candle["OBV"]),

            # ==================================

            reasons=" | ".join(
                signal["Reasons"]
            )

        )