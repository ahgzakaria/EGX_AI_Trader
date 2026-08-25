r"""Twenty sessions is a measurement window, not an exit rule. What should be?

The strategy was validated on a fixed twenty-day hold because that is what the
original sweep measured, and a fixed window is the right instrument for asking
"does this signal predict anything". It is a poor instrument for running money:
it sells a name that is still climbing on day twenty and holds one that broke
down on day three.

What makes an exit worth changing here is not only its return. At a 4M book
with a 5% drawdown tolerance, the deployable fraction is 5 divided by the
strategy's worst fall -- so an exit that cuts the drawdown lets more capital
work at the same tolerance, and can beat a higher-returning rule on the money
that actually ends up invested. Both columns are reported, and so is the
fraction each one implies.

**Exits are evaluated on closes.** With daily bars, a stop touched intraday
cannot be distinguished from one that was not, and resolving that ambiguity in
either direction is a choice that decides the answer -- the intraday version of
this question (``exit_rules.py``) had to report two bounds for exactly that
reason. A close-based rule has no ambiguity: you look at the close, and you act.
It is also what a person actually does. The cost is that a violent intraday
reversal is exited at that day's close rather than at the level, which makes
every stop below slightly worse than its label, not better.

    venv\Scripts\python.exe scripts\research\swing_exit_rules.py
"""

from __future__ import annotations

import glob
import os
from pathlib import Path
import random
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.research.breakout_filters import COST, SPLIT, build

TURNOVER_FLOOR = 5_000_000.0
#: Nothing is held past this, whatever the rule says. A trend-following exit
#: without a backstop is an open-ended position, not a strategy.
MAX_HOLD = 60
SLOTS = 40
SEEDS = 6


def liquid_universe():
    frames, turnover = {}, {}
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
            turnover[symbol] = float((frame["Close"] * frame["Volume"]).tail(250).median())
    return {s: f for s, f in frames.items() if turnover[s] >= TURNOVER_FLOOR}


def walk(closes, means_20, entry_index: int, rule: str, parameter: float):
    """Days held and gross percent, following one position forward on closes."""
    entry = closes[entry_index]
    peak = entry
    limit = min(entry_index + MAX_HOLD, len(closes) - 1)
    for offset in range(1, limit - entry_index + 1):
        index = entry_index + offset
        price = closes[index]
        peak = max(peak, price)

        if rule == "fixed" and offset >= parameter:
            return offset, (price - entry) / entry * 100
        if rule == "trail" and price <= peak * (1 - parameter / 100):
            return offset, (price - entry) / entry * 100
        if rule == "stop" and price <= entry * (1 - parameter / 100):
            return offset, (price - entry) / entry * 100
        if rule == "mean" and not np.isnan(means_20[index]) and price < means_20[index]:
            return offset, (price - entry) / entry * 100
        if rule == "stop_then_time":
            if price <= entry * (1 - parameter / 100):
                return offset, (price - entry) / entry * 100
            if offset >= 20:
                return offset, (price - entry) / entry * 100
    held = limit - entry_index
    return held, (closes[limit] - entry) / entry * 100


def portfolio(trades: pd.DataFrame, slots: int, seed: int):
    """Equity curve with `slots` equal-weight positions, random when crowded."""
    rng = random.Random(seed)
    held, equity = [], [1.0]
    for _, group in trades.groupby("Date"):
        held = [(days - 1) for days in held if days > 1]
        free = slots - len(held)
        rows = list(group.itertuples())
        rng.shuffle(rows)
        for row in rows[:max(free, 0)]:
            held.append(row.days)
            equity.append(equity[-1] * (1 + row.net / 100 / slots))
    return np.array(equity)


RULES = [
    ("fixed 20 sessions (shipped)", "fixed", 20),
    ("fixed 40 sessions", "fixed", 40),
    ("trailing 8% from the peak", "trail", 8),
    ("trailing 12% from the peak", "trail", 12),
    ("trailing 20% from the peak", "trail", 20),
    ("hard stop 8%, else 20 sessions", "stop_then_time", 8),
    ("hard stop 12%, else 20 sessions", "stop_then_time", 12),
    ("close below the 20-day mean", "mean", 0),
]


def main() -> int:
    frames = liquid_universe()
    panel = build(frames)
    panel["Date"] = pd.to_datetime(panel["Date"])
    signals = panel[panel["breakout"] & (panel["volume_ratio"] >= 2.5)
                    & (panel["momentum_rank"] >= 0.67)][["Date", "Symbol"]]

    series = {}
    for symbol, frame in frames.items():
        frame = frame.copy()
        frame["Date"] = pd.to_datetime(frame["Date"])
        frame = frame.sort_values("Date").reset_index(drop=True)
        series[symbol] = (
            frame["Close"].to_numpy(),
            frame["Close"].rolling(20).mean().to_numpy(),
            {d: i for i, d in enumerate(frame["Date"])},
        )

    print(f"{len(frames)} names, {len(signals)} signals, "
          f"max hold {MAX_HOLD}, {SLOTS} slots, cost {COST:.2f}%\n")
    print(f"  {'exit rule':<34} {'days':>5} {'net':>7} {'win':>6} "
          f"{'annual':>8} {'fall':>7} {'deploy':>7} {'on 4M':>7}")
    print(f"  {'-' * 88}")

    for label, rule, parameter in RULES:
        rows = []
        for date, symbol in signals.itertuples(index=False):
            closes, means, lookup = series[symbol]
            index = lookup.get(date)
            if index is None or index + 2 >= len(closes):
                continue
            days, gross = walk(closes, means, index, rule, parameter)
            rows.append({"Date": date, "days": days, "net": gross - COST})
        trades = pd.DataFrame(rows).sort_values("Date")
        if trades.empty:
            continue

        recent = trades[trades["Date"] >= SPLIT]
        years = (trades["Date"].max() - trades["Date"].min()).days / 365.25
        curves = [portfolio(trades, SLOTS, seed) for seed in range(SEEDS)]
        annual = np.mean([(c[-1] ** (1 / years) - 1) * 100 for c in curves])
        falls = []
        for curve in curves:
            peak = np.maximum.accumulate(curve)
            falls.append(((curve - peak) / peak).min() * 100)
        fall = float(np.mean(falls))
        # The tolerance is 5% of the whole book, so this is the share of it
        # that can be at work without breaching that.
        deploy = min(5.0 / abs(fall), 1.0) if fall else 1.0
        print(f"  {label:<34} {trades['days'].mean():>5.0f} "
              f"{trades['net'].mean():>+6.2f}% "
              f"{(trades['net'] > 0).mean() * 100:>5.1f}% "
              f"{annual:>+7.1f}% {fall:>+6.1f}% {deploy * 100:>6.0f}% "
              f"{annual * deploy:>+6.1f}%")

    print("\nThe last column is the one to read: return on the whole 4M after "
          "sizing the\ndeployment down to a 5% worst fall. A rule that returns "
          "less per trade but\nfalls less can put more money to work and beat "
          "one that returns more.")
    print("\nMeasured over the full 25 years, not the bull market. Exits are "
          "evaluated on\ncloses, so every stop here is slightly worse than its "
          "label rather than better.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
