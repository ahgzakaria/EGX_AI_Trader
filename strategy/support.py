def support_resistance(df, window=20):

    support = df["Low"].rolling(window).min().iloc[-1]

    resistance = df["High"].rolling(window).max().iloc[-1]

    return {
        "support": float(support),
        "resistance": float(resistance)
    }