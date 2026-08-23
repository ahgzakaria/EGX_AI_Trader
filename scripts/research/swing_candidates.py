"""Four structurally different swing strategies, measured the same way.

Intraday is closed. The round trip costs about 0.80% while the ordinary
adverse move after any entry is -3.19% and a signalled symbol drifts +1.38% by
the close: a stop wide enough to survive the noise leaves a reward/risk below
one, and no threshold rescues that. The literature on illiquid emerging
markets says the same thing in general terms -- minimise how often a strategy
crosses the spread.

So these candidates all hold for weeks rather than hours, and differ in what
they ask of the market rather than in how they are tuned:

* **volume breakout** -- a close above the 20-day high on heavy volume. The
  one thing measured earlier that survived validation on raw daily bars:
  +5.73% over twenty days at a 59% win rate above 2.5x average volume.
* **52-week high** -- the classic long-horizon breakout, on the theory that a
  name at a yearly high has no overhead supply.
* **pullback in an uptrend** -- buy weakness rather than strength: price above
  a rising 50-day average, touching the 20-day.
* **oversold reversal** -- the direction the momentum study actually pointed:
  three and six month price change is *negatively* related to the next twenty
  days on this market.

Every figure is net of the round trip and reported for the training and
validation eras separately, against the return of simply being in every name
on the same days. A candidate has to beat that, in validation, to mean
anything at all.

    venv/Scripts/python.exe scripts/research/swing_candidates.py
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
MIN_ROWS = 500
MAX_RANGE = 25.0
MONTH = 21


def load():
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
        frame = frame[(frame["High"] - frame["Low"]) / frame["Open"] * 100 <= MAX_RANGE]
        if len(frame) >= MIN_ROWS:
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
        f["resistance20"] = high.rolling(20).max().shift(1)
        f["high52"] = high.rolling(252).max().shift(1)
        f["volume_ratio"] = volume / volume.rolling(20).mean()
        f["ema20"] = close.ewm(span=20, adjust=False).mean()
        f["ema50"] = close.ewm(span=50, adjust=False).mean()
        f["ema50_rising"] = f["ema50"] > f["ema50"].shift(10)
        f["momentum_3m"] = close.pct_change(3 * MONTH) * 100
        f["forward"] = (close.shift(-HOLD) - close) / close * 100.0 - COST
        rows.append(f)
    panel = pd.concat(rows, ignore_index=True).dropna(subset=["forward"])
    # Cross-sectional rank of three-month change, so "oversold" means weak
    # against the market that day rather than weak in absolute terms.
    panel["momentum_rank"] = panel.groupby("Date")["momentum_3m"].rank(pct=True)
    return panel


CANDIDATES = {
    "volume breakout (>=2.5x)":
        lambda d: (d["Close"] > d["resistance20"]) & (d["volume_ratio"] >= 2.5),
    "52-week high breakout":
        lambda d: (d["Close"] > d["high52"]) & (d["volume_ratio"] >= 1.5),
    "pullback in an uptrend":
        lambda d: (d["Close"] > d["ema50"]) & d["ema50_rising"]
                  & (d["Low"] <= d["ema20"] * 1.01) & (d["Close"] > d["ema20"]),
    "oversold, weakest decile":
        lambda d: (d["momentum_rank"] <= 0.10) & (d["Close"] > d["ema20"]),
}


def main() -> int:
    panel = build(load())
    train = panel[panel["Date"] < SPLIT]
    test = panel[panel["Date"] >= SPLIT]
    print(f"{panel['Symbol'].nunique()} symbols   train {len(train):,}   "
          f"validation {len(test):,}")
    print(f"{HOLD}-day hold, net of {COST}% a round trip\n")

    base_train, base_test = train["forward"].mean(), test["forward"].mean()
    print(f"{'candidate':<30}{'train n':>9}{'train%':>9}"
          f"{'valid n':>9}{'valid%':>9}{'win%':>7}{'lift':>8}")
    print("-" * 81)
    print(f"{'every name, every day':<30}{len(train):>9,}{base_train:>9.2f}"
          f"{len(test):>9,}{base_test:>9.2f}"
          f"{(test['forward'] > 0).mean() * 100:>7.0f}{'--':>8}")

    for name, rule in CANDIDATES.items():
        tr = train[rule(train)]["forward"]
        te = test[rule(test)]["forward"]
        if len(te) < 50:
            print(f"{name:<30}{len(tr):>9,}{'--':>9}{len(te):>9,}"
                  f"{'too few':>9}")
            continue
        print(f"{name:<30}{len(tr):>9,}{tr.mean():>9.2f}{len(te):>9,}"
              f"{te.mean():>9.2f}{(te > 0).mean() * 100:>7.0f}"
              f"{te.mean() - base_test:>+8.2f}")

    print()
    print("'lift' is the validation return above simply holding every name on")
    print("the same days. A candidate that does not clear it is a more")
    print("expensive way to own the market.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
