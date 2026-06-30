def trend_score(df):

    last = df.iloc[-1]

    score = 0

    if last["EMA20"] > last["EMA50"] > last["EMA200"]:
        score += 40

    if last["Close"] > last["EMA20"]:
        score += 20

    if last["Close"] > last["EMA50"]:
        score += 20

    if last["Close"] > last["EMA200"]:
        score += 20

    return score