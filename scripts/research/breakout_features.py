"""What do the breakout features actually predict on EGX?

`strategy_breakout/breakout_scoring.py` assigns each feature a weight by hand:
20 for a previous-resistance breakout, 15 for volume, 10 for EMA continuation,
and so on. Nobody measured them. This measures them, on the same features the
engine computes, over the archived daily history.

Discipline, because the whole point is not to fool ourselves:

* **Split by time.** Everything is reported separately for a training era and
  a later validation era. A feature that only works in one is not a feature.
* **Net of cost.** Every number is after the 0.80% round trip.
* **Against the base rate.** A feature is only interesting to the extent it
  beats doing nothing selective on the same day and horizon.
"""
import csv
import glob
import os
import statistics
from collections import defaultdict

import numpy as np
import pandas as pd

COST = 0.80
HORIZONS = (5, 10, 20)
SPLIT_DATE = "2024-01-01"
TOP_N = 60
MIN_ROWS = 400

frames = {}
for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
    symbol = os.path.basename(path)[:-4]
    frame = pd.read_csv(path)
    if len(frame) < MIN_ROWS:
        continue
    frame = frame.rename(columns=str.title).rename(columns={"Adjusted_close": "Adj"})
    frame = frame[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna()
    frame = frame[(frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
    frame = frame[frame["Volume"] > 0]
    # EGX caps a session at +/-20%; a wider range is a bad print, not a move.
    frame = frame[(frame["High"] - frame["Low"]) / frame["Open"] * 100 <= 25]
    if len(frame) < MIN_ROWS:
        continue
    frames[symbol] = frame.reset_index(drop=True)

liquidity = {
    s: (f["Close"] * f["Volume"]).tail(250).median() for s, f in frames.items()
}
universe = sorted(liquidity, key=liquidity.get, reverse=True)[:TOP_N]
print(f"{len(universe)} symbols, {sum(len(frames[s]) for s in universe):,} stock-days")
print(f"cost {COST}%, split at {SPLIT_DATE}")
print()

records = []
for symbol in universe:
    f = frames[symbol].copy()
    close, high, low, volume = f["Close"], f["High"], f["Low"], f["Volume"]

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    previous_close = close.shift(1)
    true_range = pd.concat([
        high - low, (high - previous_close).abs(), (low - previous_close).abs()
    ], axis=1).max(axis=1)
    atr = true_range.rolling(14).mean()
    volume_ratio = volume / volume.rolling(20).mean()
    resistance = high.rolling(20).max().shift(1)
    consolidation_width = (
        (high.rolling(10).max() - low.rolling(10).min()) / low.rolling(10).min() * 100
    ).shift(1)

    direct_breakout = close > resistance
    f["previous_resistance_breakout"] = direct_breakout
    f["high_volume_breakout"] = direct_breakout & (volume_ratio >= 1.5)
    f["ema20_continuation"] = (close > ema20) & (low <= ema20 * 1.01)
    f["consolidation_breakout"] = direct_breakout & (consolidation_width <= 8)
    f["atr_expansion"] = (high - low) >= atr * 1.2
    f["higher_high_breakout"] = high > resistance
    f["ema_aligned"] = (ema20 > ema50) & (ema50 > ema200)
    f["above_ema20"] = close > ema20
    f["Symbol"] = symbol

    for horizon in HORIZONS:
        f[f"fwd{horizon}"] = (close.shift(-horizon) - close) / close * 100.0 - COST
        # What a target aiming inside the window could have reached.
        f[f"peak{horizon}"] = (
            high.shift(-1).rolling(horizon).max().shift(-(horizon - 1)) - close
        ) / close * 100.0 - COST

    records.append(f)

data = pd.concat(records, ignore_index=True)
data = data.dropna(subset=[f"fwd{h}" for h in HORIZONS])
train = data[data["Date"] < SPLIT_DATE]
test = data[data["Date"] >= SPLIT_DATE]
print(f"train {len(train):,} rows   test {len(test):,} rows")
print()

FEATURES = [
    "previous_resistance_breakout", "high_volume_breakout", "ema20_continuation",
    "consolidation_breakout", "atr_expansion", "higher_high_breakout",
    "ema_aligned", "above_ema20",
]

HAND_WEIGHTS = {
    "previous_resistance_breakout": 20, "high_volume_breakout": 15,
    "ema20_continuation": 10, "consolidation_breakout": 15,
    "atr_expansion": 15, "higher_high_breakout": 10,
    "ema_aligned": 10, "above_ema20": 5,
}

for horizon in HORIZONS:
    column = f"fwd{horizon}"
    print(f"=== {horizon}-day forward return, net of {COST}% cost ===")
    base_train = train[column].mean()
    base_test = test[column].mean()
    print(f"base rate (every day, every name): train {base_train:+.3f}%  "
          f"test {base_test:+.3f}%")
    print(f"{'feature':<32}{'hand':>6}{'train lift':>12}{'test lift':>12}"
          f"{'test n':>10}{'test win%':>11}")
    print("-" * 83)
    rows = []
    for feature in FEATURES:
        tr = train[train[feature]][column]
        te = test[test[feature]][column]
        if len(te) < 200:
            continue
        lift_train = tr.mean() - base_train
        lift_test = te.mean() - base_test
        rows.append((lift_test, feature, HAND_WEIGHTS[feature], lift_train,
                     lift_test, len(te), (te > 0).mean() * 100))
    for _, feature, hand, lt, lte, n, win in sorted(rows, reverse=True):
        print(f"{feature:<32}{hand:>6}{lt:>+11.3f}%{lte:>+11.3f}%{n:>10,}{win:>10.1f}%")
    print()
