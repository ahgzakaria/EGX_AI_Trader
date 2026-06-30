def volume_score(df):

    score = 0

    avg_volume = df["Volume"].tail(20).mean()

    current_volume = df["Volume"].iloc[-1]

    if current_volume > avg_volume * 1.5:
        score += 40

    elif current_volume > avg_volume:
        score += 20

    return score