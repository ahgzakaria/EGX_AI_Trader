def signal_engine(result):

    score = result["Score"]
    rr = result["RR"]

    if score >= 80 and rr >= 2:
        signal = "BUY"
        stars = 5

    elif score >= 60:
        signal = "WATCH"
        stars = 4

    elif score >= 40:
        signal = "WATCH"
        stars = 3

    else:
        signal = "AVOID"
        stars = 2

    return {
        "Signal": signal,
        "Stars": stars
    }