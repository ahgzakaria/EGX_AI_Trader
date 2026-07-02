import numpy as np


class MarketData:

    def __init__(self, df):

        self.df = df

        # Price
        self.open = df["Open"].to_numpy(dtype=np.float32)
        self.high = df["High"].to_numpy(dtype=np.float32)
        self.low = df["Low"].to_numpy(dtype=np.float32)
        self.close = df["Close"].to_numpy(dtype=np.float32)
        self.volume = df["Volume"].to_numpy(dtype=np.float32)

        # Moving Averages
        self.ema20 = df["EMA20"].to_numpy(dtype=np.float32)
        self.ema50 = df["EMA50"].to_numpy(dtype=np.float32)
        self.ema200 = df["EMA200"].to_numpy(dtype=np.float32)

        # Momentum
        self.rsi = df["RSI"].to_numpy(dtype=np.float32)
        self.macd = df["MACD"].to_numpy(dtype=np.float32)
        self.macd_signal = df["MACD_Signal"].to_numpy(dtype=np.float32)
        self.adx = df["ADX"].to_numpy(dtype=np.float32)

        # Volatility
        self.atr = df["ATR"].to_numpy(dtype=np.float32)

        # Bollinger
        self.bb_upper = df["BB_UPPER"].to_numpy(dtype=np.float32)
        self.bb_middle = df["BB_MIDDLE"].to_numpy(dtype=np.float32)
        self.bb_lower = df["BB_LOWER"].to_numpy(dtype=np.float32)

        # Volume
        self.obv = df["OBV"].to_numpy(dtype=np.float32)

        self.index = df.index
        self.length = len(df)