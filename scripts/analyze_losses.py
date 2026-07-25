import os
import pandas as pd


REPORT = "reports/backtest_results.csv"


def analyze():

    if not os.path.exists(REPORT):

        print("backtest_results.csv not found.")
        return

    df = pd.read_csv(REPORT)

    # =====================================
    # Wins / Losses
    # =====================================

    wins = df[df["result"] == "WIN"].copy()

    losses = df[df["result"] == "LOSS"].copy()

    # =====================================
    # Columns
    # =====================================

    columns = [

        "rsi",
        "adx",
        "atr",
        "macd",

        "trend_score",
        "volume_score",
        "momentum_score",
        "candle_score",
        "breakout_score",

        "score",
        "confidence",

        "rr",

        "holding_days",

        "profit"

    ]

    print("\n==============================")
    print("LOSING TRADES")
    print("==============================")

    print(losses[columns].mean())

    print("\n==============================")
    print("WINNING TRADES")
    print("==============================")

    print(wins[columns].mean())

    # =====================================
    # Reasons
    # =====================================

    print("\n==============================")
    print("TOP LOSS REASONS")
    print("==============================")

    reasons = (

        losses["reasons"]

        .fillna("")

        .str.split("|")

        .explode()

        .str.strip()

    )

    print(

        reasons

        .value_counts()

        .head(20)

    )

    # =====================================
    # Save
    # =====================================

    os.makedirs(

        "reports",

        exist_ok=True

    )

    losses.to_csv(

        "reports/loss_analysis.csv",

        index=False,

        encoding="utf-8-sig"

    )

    wins.to_csv(

        "reports/win_analysis.csv",

        index=False,

        encoding="utf-8-sig"

    )

    print("\nReports Saved.")


if __name__ == "__main__":

    analyze()