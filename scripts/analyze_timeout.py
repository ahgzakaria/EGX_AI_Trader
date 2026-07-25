import os
import pandas as pd


REPORT = "reports/backtest_results.csv"


def analyze():

    if not os.path.exists(REPORT):

        print("backtest_results.csv not found.")
        return

    df = pd.read_csv(REPORT)

    timeout = df[df["exit_reason"] == "Timeout"].copy()

    if timeout.empty:

        print("No Timeout Trades Found.")
        return

    print("=" * 60)
    print("TIMEOUT ANALYSIS")
    print("=" * 60)

    print(f"Total Timeout Trades : {len(timeout)}")

    wins = timeout[timeout["result"] == "WIN"]
    losses = timeout[timeout["result"] == "LOSS"]
    breakeven = timeout[timeout["result"] == "BREAKEVEN"]

    print(f"Winning Timeout      : {len(wins)}")
    print(f"Losing Timeout       : {len(losses)}")
    print(f"BreakEven Timeout    : {len(breakeven)}")

    print()

    print("Average Profit       :", round(timeout["profit"].mean(), 2))
    print("Average Holding Days :", round(timeout["holding_days"].mean(), 2))
    print("Average RR           :", round(timeout["rr"].mean(), 2))

    print()
    print("=" * 60)
    print("AVERAGE INDICATORS")
    print("=" * 60)

    indicators = [

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
        "confidence"

    ]

    for col in indicators:

        if col in timeout.columns:

            print(f"{col:20} : {round(timeout[col].mean(),2)}")

    print()
    print("=" * 60)
    print("TOP SYMBOLS")
    print("=" * 60)

    print(timeout["symbol"].value_counts().head(20))

    print()
    print("=" * 60)
    print("SAVE REPORT")
    print("=" * 60)

    timeout.sort_values(

        "profit",

        inplace=True

    )

    timeout.to_csv(

        "reports/timeout_analysis.csv",

        index=False,

        encoding="utf-8-sig"

    )

    print("Saved -> reports/timeout_analysis.csv")


if __name__ == "__main__":

    analyze()