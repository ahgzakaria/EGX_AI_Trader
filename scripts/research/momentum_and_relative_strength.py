"""Do inputs the engine does not have carry anything the ones it has do not?

Three attempts to reweight the seven existing breakout features all failed to
beat weights assigned by judgement, which says the weighting is not the binding
constraint. Seven binary flags derived from one daily bar carry a certain
amount of information and the current score already extracts most of it.

This asks a different question. Momentum over months, strength relative to the
rest of the market, and where a name sits in its own volatility regime are not
computable from a single bar and are absent from the score entirely. Momentum
in particular is the most replicated cross-sectional effect in the literature,
and the only EGX studies that found anything worked on 5, 50 and 150-day
moving averages -- multi-month horizons, not intraday ones.

The discipline that caught the last three failures, applied from the start:

* **The out-of-sample column is the only one that counts.** Everything is
  reported for the training era and the validation era separately, and the
  pooled figure is not shown at all, because a pooled average over mostly
  in-sample data is how the last candidate looked decisive while being worth
  nothing.
* **Deciles, not thresholds.** A threshold picked after seeing the data is a
  free parameter. Sorting into ten buckets and reading the shape says whether
  there is a monotone relationship before anyone chooses a cut.
* **Net of cost.** 0.80% a round trip, every figure.

Read-only, ships nothing.

    venv/Scripts/python.exe scripts/research/momentum_and_relative_strength.py
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

COST = 0.80
HOLD = 20
TOP_N = 60
SPLIT_DATE = "2024-01-01"
MIN_ROWS = 500
MAX_PLAUSIBLE_RANGE = 25.0

MONTH = 21          # trading days


def load() -> dict[str, pd.DataFrame]:
    frames = {}
    for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
        symbol = os.path.basename(path)[:-4]
        frame = pd.read_csv(path)
        if len(frame) < MIN_ROWS:
            continue
        frame = frame.rename(columns=str.title)
        frame = frame[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna()
        frame = frame[(frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
        frame = frame[frame["Volume"] > 0]
        frame = frame[
            (frame["High"] - frame["Low"]) / frame["Open"] * 100 <= MAX_PLAUSIBLE_RANGE
        ]
        if len(frame) >= MIN_ROWS:
            frames[symbol] = frame.reset_index(drop=True)
    return frames


def build(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    liquidity = {s: (f["Close"] * f["Volume"]).tail(250).median() for s, f in frames.items()}
    universe = sorted(liquidity, key=liquidity.get, reverse=True)[:TOP_N]

    records = []
    for symbol in universe:
        f = frames[symbol].copy()
        close, high, low = f["Close"], f["High"], f["Low"]

        f["Symbol"] = symbol
        f["momentum_3m"] = close.pct_change(3 * MONTH) * 100
        f["momentum_6m"] = close.pct_change(6 * MONTH) * 100
        f["momentum_12m"] = close.pct_change(12 * MONTH) * 100

        # 12-1: a year of momentum excluding the most recent month, which is
        # the standard construction because the last month tends to reverse.
        year_ago = close.shift(12 * MONTH)
        month_ago = close.shift(MONTH)
        f["momentum_12_1"] = (month_ago / year_ago - 1) * 100

        # How far below its own 52-week high, a scale-free trend measure.
        f["below_52w_high"] = (close / high.rolling(252).max() - 1) * 100

        previous_close = close.shift(1)
        true_range = pd.concat(
            [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = true_range.rolling(14).mean() / close * 100
        # Above 1 the name is more volatile than its own recent norm.
        f["volatility_regime"] = atr / atr.rolling(126).mean()

        f["forward"] = (close.shift(-HOLD) - close) / close * 100.0 - COST
        records.append(f)

    panel = pd.concat(records, ignore_index=True)

    # Relative strength: the same momentum measured against what every other
    # name did on that date. A 20% run means one thing when the market ran 25%
    # and another when it ran 2%.
    for horizon in ("3m", "6m", "12m"):
        market = panel.groupby("Date")[f"momentum_{horizon}"].transform("mean")
        panel[f"relative_{horizon}"] = panel[f"momentum_{horizon}"] - market

    return panel.dropna(subset=["forward"])


FEATURES = (
    "momentum_3m", "momentum_6m", "momentum_12m", "momentum_12_1",
    "relative_3m", "relative_6m", "relative_12m",
    "below_52w_high", "volatility_regime",
)


def deciles(frame: pd.DataFrame, feature: str) -> list:
    subset = frame.dropna(subset=[feature])
    if len(subset) < 2000:
        return []
    try:
        buckets = pd.qcut(subset[feature], 10, labels=False, duplicates="drop")
    except ValueError:
        return []
    return [
        subset.loc[buckets == b, "forward"].mean()
        for b in sorted(pd.Series(buckets).dropna().unique())
    ]


def main() -> int:
    panel = build(load())
    train = panel[panel["Date"] < SPLIT_DATE]
    test = panel[panel["Date"] >= SPLIT_DATE]
    print(f"{panel['Symbol'].nunique()} symbols   "
          f"train {len(train):,}   validation {len(test):,}")
    print(f"{HOLD}-day forward return, net of {COST}% cost")
    print(f"base rate: train {train['forward'].mean():+.3f}%   "
          f"validation {test['forward'].mean():+.3f}%")
    print()
    print("Decile 1 is the lowest value of the feature, decile 10 the highest.")
    print("A feature worth having is monotone AND keeps its shape in validation.")
    print()

    for feature in FEATURES:
        train_deciles = deciles(train, feature)
        test_deciles = deciles(test, feature)
        if not train_deciles or not test_deciles:
            continue

        spread_train = train_deciles[-1] - train_deciles[0]
        spread_test = test_deciles[-1] - test_deciles[0]
        # Rank correlation of decile index against decile mean: +1 is
        # perfectly increasing, -1 perfectly decreasing, 0 is noise.
        monotone = np.corrcoef(range(len(test_deciles)), test_deciles)[0, 1]

        print(f"{feature:<20} top-bottom  train {spread_train:+7.2f}%   "
              f"validation {spread_test:+7.2f}%   monotone {monotone:+.2f}")
        print(f"{'':<20} validation deciles  "
              + " ".join(f"{v:+5.1f}" for v in test_deciles))

    print()
    print("Read the validation column and the monotone score together. A large")
    print("top-bottom spread with a monotone near zero is one lucky decile, not")
    print("a relationship, and will not survive being turned into a rule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
