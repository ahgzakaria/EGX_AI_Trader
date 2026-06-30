def entry_signal(df):

    last = df.iloc[-1]

    support = df["Low"].rolling(20).min().iloc[-1]

    resistance = df["High"].rolling(20).max().iloc[-1]

    price = last["Close"]

    atr = last["ATR"]

    buy_low = max(price - atr * 0.5, support)

    buy_high = price

    stop_loss = support - atr * 0.5

    target1 = resistance

    target2 = resistance + atr * 2

    rr = round((target2 - price) / (price - stop_loss), 2)

    return {
        "BuyLow": round(buy_low, 2),
        "BuyHigh": round(buy_high, 2),
        "StopLoss": round(stop_loss, 2),
        "Target1": round(target1, 2),
        "Target2": round(target2, 2),
        "RR": rr
    }