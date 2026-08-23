"""What, added to the volume breakout, actually improves it?

The volume breakout is the only thing in this data with a lift over simply
owning the market that survives both eras: +3.17% in training and +2.79% in
validation, 59% of trades positive. Its weakness is visible year by year --
thirteen years positive, five negative, and the bad ones reach -7% a trade.

The value factor, which the frontier-market literature rates the strongest,
cannot be tested here: fundamentals are not in the data subscription. That
leaves conditions computable from price and volume, and the obvious suspects
are whether the market itself was rising and whether the name had already been
going up.

Each filter is added to the breakout on its own, never stacked, so a result
belongs to one idea rather than to a combination nobody can interpret. Every
figure is net of the round trip, reported for both eras and then year by year,
because an average that only works in one regime is that regime.
"""
from __future__ import annotations

import glob
import os
import statistics

import pandas as pd

COST = 0.80
HOLD = 20
TOP_N = 60
SPLIT = "2024-01-01"
MONTH = 21


def load():
    frames = {}
    for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
        symbol = os.path.basename(path)[:-4]
        frame = pd.read_csv(path)
        if len(frame) < 500:
            continue
        frame = frame.rename(columns=str.title)
        frame = frame[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna()
        frame = frame[(frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
        frame = frame[frame["Volume"] > 0]
        frame = frame[(frame["High"] - frame["Low"]) / frame["Open"] * 100 <= 25]
        if len(frame) >= 500:
            frames[symbol] = frame.reset_index(drop=True)
    liquidity = {s: (f["Close"] * f["Volume"]).tail(250).median()
                 for s, f in frames.items()}
    return {s: frames[s] for s in
            sorted(liquidity, key=liquidity.get, reverse=True)[:TOP_N]}


def build(frames):
    rows = []
    for symbol, frame in frames.items():
        f = frame.copy()
        close, high, low, volume = f["Close"], f["High"], f["Low"], f["Volume"]
        f["Symbol"] = symbol
        f["breakout"] = close > high.rolling(20).max().shift(1)
        f["volume_ratio"] = volume / volume.rolling(20).mean()
        f["above_200"] = close > close.rolling(200).mean()
        f["momentum_12_1"] = (close.shift(MONTH) / close.shift(12 * MONTH) - 1) * 100
        previous = close.shift(1)
        true_range = pd.concat(
            [high - low, (high - previous).abs(), (low - previous).abs()], axis=1
        ).max(axis=1)
        f["atr_percent"] = true_range.rolling(14).mean() / close * 100
        f["forward"] = (close.shift(-HOLD) - close) / close * 100.0 - COST
        rows.append(f)

    panel = pd.concat(rows, ignore_index=True).dropna(subset=["forward"])

    # An equal-weighted market proxy built from the universe itself, and
    # whether it is above its own 50-day average on that date. There is no
    # index series in this data, so the cross-section is the index.
    daily = panel.groupby("Date")["Close"].mean().sort_index()
    regime = (daily > daily.rolling(50).mean()).rename("market_up")
    panel = panel.merge(regime, left_on="Date", right_index=True, how="left")
    panel["momentum_rank"] = panel.groupby("Date")["momentum_12_1"].rank(pct=True)
    panel["atr_rank"] = panel.groupby("Date")["atr_percent"].rank(pct=True)
    return panel


FILTERS = {
    "(none)": lambda d: d["breakout"] & (d["volume_ratio"] >= 2.5),
    "+ market above its 50d": lambda d: d["breakout"] & (d["volume_ratio"] >= 2.5)
                                        & d["market_up"].fillna(False),
    "+ price above its 200d": lambda d: d["breakout"] & (d["volume_ratio"] >= 2.5)
                                        & d["above_200"],
    "+ 12-1 momentum top half": lambda d: d["breakout"] & (d["volume_ratio"] >= 2.5)
                                          & (d["momentum_rank"] >= 0.5),
    "+ calmer half by ATR": lambda d: d["breakout"] & (d["volume_ratio"] >= 2.5)
                                      & (d["atr_rank"] <= 0.5),
    "+ volume >= 4x": lambda d: d["breakout"] & (d["volume_ratio"] >= 4.0),
}


def main() -> int:
    panel = build(load())
    train = panel[panel["Date"] < SPLIT]
    test = panel[panel["Date"] >= SPLIT]
    base_train, base_test = train["forward"].mean(), test["forward"].mean()

    print(f"{panel['Symbol'].nunique()} symbols, {HOLD}-day hold, net of {COST}%")
    print(f"owning everything: train {base_train:.2f}%  validation {base_test:.2f}%\n")
    print(f"{'filter on top of the breakout':<30}{'train n':>9}{'lift':>8}"
          f"{'valid n':>9}{'lift':>8}{'win%':>7}{'bad yrs':>9}")
    print("-" * 80)

    for name, rule in FILTERS.items():
        selected = panel[rule(panel)]
        tr = selected[selected["Date"] < SPLIT]["forward"]
        te = selected[selected["Date"] >= SPLIT]["forward"]
        if len(te) < 40:
            print(f"{name:<30}{len(tr):>9,}{'--':>8}{len(te):>9,}{'too few':>8}")
            continue
        years = selected.assign(year=selected["Date"].str[:4]).groupby("year")["forward"]
        counted = years.agg(["mean", "count"])
        counted = counted[counted["count"] >= 10]
        bad = int((counted["mean"] < 0).sum())
        print(f"{name:<30}{len(tr):>9,}{tr.mean() - base_train:>+8.2f}"
              f"{len(te):>9,}{te.mean() - base_test:>+8.2f}"
              f"{(te > 0).mean() * 100:>7.0f}{bad:>4}/{len(counted):<4}")

    print()
    print("'lift' is the return above owning every name on the same days.")
    print("'bad yrs' counts years with at least ten trades that lost money.")
    print("A filter that raises the lift and cuts the bad years is worth having;")
    print("one that only raises the lift bought that with fewer, luckier trades.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
