from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volume import OnBalanceVolumeIndicator
from ta.volatility import AverageTrueRange, BollingerBands


def calculate_indicators(df):

    # ==================================
    # RSI
    # ==================================

    df["RSI"] = RSIIndicator(
        close=df["Close"],
        window=14
    ).rsi()

    df["RSI7"] = RSIIndicator(
        close=df["Close"],
        window=7
    ).rsi()

    # ==================================
    # EMA
    # ==================================

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

    # ==================================
    # MACD
    # ==================================

    macd = MACD(close=df["Close"])

    df["MACD"] = macd.macd()
    df["MACD_Signal"] = macd.macd_signal()
    df["MACD_HIST"] = macd.macd_diff()

    # ==================================
    # MACD Cross Age
    # ==================================

    cross = (
        (df["MACD"] > df["MACD_Signal"])
        !=
        (df["MACD"].shift(1) > df["MACD_Signal"].shift(1))
    )

    age = 0
    ages = []

    for is_cross in cross:

        if is_cross:
            age = 0
        else:
            age += 1

        ages.append(age)

    df["MACD_CROSS_AGE"] = ages

    # ==================================
    # ADX
    # ==================================

    adx = ADXIndicator(
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
        window=14
    )

    df["ADX"] = adx.adx()

    # ==================================
    # Bollinger
    # ==================================

    bb = BollingerBands(
        close=df["Close"],
        window=20,
        window_dev=2
    )

    df["BB_UPPER"] = bb.bollinger_hband()
    df["BB_MIDDLE"] = bb.bollinger_mavg()
    df["BB_LOWER"] = bb.bollinger_lband()

    # ==================================
    # OBV
    # ==================================

    df["OBV"] = OnBalanceVolumeIndicator(
        close=df["Close"],
        volume=df["Volume"]
    ).on_balance_volume()

    # ==================================
    # ATR
    # ==================================

    atr = AverageTrueRange(
        high=df["High"],
        low=df["Low"],
        close=df["Close"]
    )

    df["ATR"] = atr.average_true_range()

    # ==================================
    # AI FEATURES
    # ==================================

    df["EMA20_DIST"] = (
        (df["Close"] - df["EMA20"])
        / df["EMA20"]
    ) * 100

    df["EMA50_DIST"] = (
        (df["Close"] - df["EMA50"])
        / df["EMA50"]
    ) * 100

    df["EMA200_DIST"] = (
        (df["Close"] - df["EMA200"])
        / df["EMA200"]
    ) * 100

    df["EMA20_SLOPE"] = df["EMA20"].pct_change(5) * 100
    df["EMA50_SLOPE"] = df["EMA50"].pct_change(5) * 100

    df["RSI_SLOPE"] = df["RSI"].diff()

    df["ADX_RISING"] = df["ADX"].diff()

    df["VOLUME_RATIO"] = (
        df["Volume"]
        / df["Volume"].rolling(20).mean()
    )

    df["ATR_PERCENT"] = (
        df["ATR"]
        / df["Close"]
    ) * 100

    df["BB_WIDTH"] = (
        (df["BB_UPPER"] - df["BB_LOWER"])
        / df["BB_MIDDLE"]
    ) * 100

    df["BB_POSITION"] = (
        (df["Close"] - df["BB_LOWER"])
        /
        (df["BB_UPPER"] - df["BB_LOWER"])
    )

    df["OBV_SLOPE"] = df["OBV"].pct_change(5)

    high20 = df["High"].rolling(20).max()
    low20 = df["Low"].rolling(20).min()

    df["DIST_HIGH20"] = (
        (high20 - df["Close"])
        / df["Close"]
    ) * 100

    df["DIST_LOW20"] = (
        (df["Close"] - low20)
        / df["Close"]
    ) * 100

    df = df.replace(
        [float("inf"), float("-inf")],
        0
    )

    df = df.fillna(0)

    return df