from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volume import OnBalanceVolumeIndicator
from ta.volatility import AverageTrueRange


def calculate_indicators(df):

    # RSI
    df["RSI"] = RSIIndicator(
        close=df["Close"],
        window=14
    ).rsi()

    # EMA
    df["EMA20"] = EMAIndicator(
        close=df["Close"],
        window=20
    ).ema_indicator()

    df["EMA50"] = EMAIndicator(
        close=df["Close"],
        window=50
    ).ema_indicator()

    df["EMA200"] = EMAIndicator(
        close=df["Close"],
        window=200
    ).ema_indicator()

    # MACD
    macd = MACD(close=df["Close"])

    df["MACD"] = macd.macd()
    df["MACD_SIGNAL"] = macd.macd_signal()
    df["MACD_HIST"] = macd.macd_diff()

    # OBV
    df["OBV"] = OnBalanceVolumeIndicator(
        close=df["Close"],
        volume=df["Volume"]
    ).on_balance_volume()

    # ATR
    atr = AverageTrueRange(
        high=df["High"],
        low=df["Low"],
        close=df["Close"]
    )

    df["ATR"] = atr.average_true_range()

    return df