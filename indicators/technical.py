from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volume import OnBalanceVolumeIndicator
from ta.volatility import AverageTrueRange, BollingerBands


def calculate_indicators(df):

    # ==========================
    # RSI
    # ==========================

    df["RSI"] = RSIIndicator(
        close=df["Close"],
        window=14
    ).rsi()

    # ==========================
    # EMA
    # ==========================

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

    # ==========================
    # MACD
    # ==========================

    macd = MACD(close=df["Close"])

    df["MACD"] = macd.macd()

    # نحافظ على الاسم القديم حتى لا نكسر باقى المشروع
    df["MACD_Signal"] = macd.macd_signal()

    df["MACD_HIST"] = macd.macd_diff()

    # ==========================
    # ADX
    # ==========================

    adx = ADXIndicator(
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
        window=14
    )

    df["ADX"] = adx.adx()

    # ==========================
    # Bollinger Bands
    # ==========================

    bb = BollingerBands(
        close=df["Close"],
        window=20,
        window_dev=2
    )

    df["BB_UPPER"] = bb.bollinger_hband()
    df["BB_MIDDLE"] = bb.bollinger_mavg()
    df["BB_LOWER"] = bb.bollinger_lband()

    # ==========================
    # OBV
    # ==========================

    df["OBV"] = OnBalanceVolumeIndicator(
        close=df["Close"],
        volume=df["Volume"]
    ).on_balance_volume()

    # ==========================
    # ATR
    # ==========================

    atr = AverageTrueRange(
        high=df["High"],
        low=df["Low"],
        close=df["Close"]
    )

    df["ATR"] = atr.average_true_range()

    # ==================================================
    # AI FEATURES
    # ==================================================

    df["VOLUME_AVG20"] = (
        df["Volume"]
        .rolling(20)
        .mean()
    )

    df["VOLUME_RATIO"] = (
        df["Volume"] /
        df["VOLUME_AVG20"]
    )

    df["ATR_PERCENT"] = (
        df["ATR"] /
        df["Close"]
    ) * 100

    df["EMA20_DIST"] = (
        (df["Close"] - df["EMA20"]) /
        df["EMA20"]
    ) * 100

    df["EMA50_DIST"] = (
        (df["Close"] - df["EMA50"]) /
        df["EMA50"]
    ) * 100

    df["EMA200_DIST"] = (
        (df["Close"] - df["EMA200"]) /
        df["EMA200"]
    ) * 100

    df["BB_POSITION"] = (
        (df["Close"] - df["BB_LOWER"]) /
        (df["BB_UPPER"] - df["BB_LOWER"])
    )

    # ==========================
    # Clean Data
    # ==========================

    df.replace(
        [float("inf"), float("-inf")],
        0,
        inplace=True
    )

    df.fillna(0, inplace=True)

    return df