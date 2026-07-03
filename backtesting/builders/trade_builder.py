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
            # Strategy
            # ==================================

            score=signal["Score"],
            confidence=signal["Confidence"],

            trend_score=signal["Trend"],
            volume_score=signal["Volume"],
            momentum_score=signal["Momentum"],
            candle_score=signal["Candles"],
            breakout_score=signal["Breakout"],

            # ==================================
            # Original Indicators
            # ==================================

            rsi=round(float(candle["RSI"]), 2),

            adx=round(float(candle["ADX"]), 2),

            atr=round(float(candle["ATR"]), 4),

            macd=round(float(candle["MACD"]), 4),

            # ==================================
            # AI Features V1
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
            # AI Features V2
            # ==================================

            rsi7=round(
                float(candle["RSI7"]),
                2
            ),

            ema20_slope=round(
                float(candle["EMA20_SLOPE"]),
                4
            ),

            ema50_slope=round(
                float(candle["EMA50_SLOPE"]),
                4
            ),

            rsi_slope=round(
                float(candle["RSI_SLOPE"]),
                4
            ),

            adx_rising=round(
                float(candle["ADX_RISING"]),
                4
            ),

            bb_width=round(
                float(candle["BB_WIDTH"]),
                4
            ),

            obv_slope=round(
                float(candle["OBV_SLOPE"]),
                6
            ),

            dist_high20=round(
                float(candle["DIST_HIGH20"]),
                4
            ),

            dist_low20=round(
                float(candle["DIST_LOW20"]),
                4
            ),

            # ==================================
            # AI Features V3
            # ==================================

            macd_cross_age=int(
                candle["MACD_CROSS_AGE"]
            ),

            # ==================================

            reasons=" | ".join(
                signal["Reasons"]
            )

        )