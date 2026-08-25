r"""How many positions, and how small can each one be?

Two limits meet here and they pull against each other.

**The profit is in a thin tail.** Over the 5M-turnover universe the median
trade returns +1.00% and the mean +5.74%, because the best ten percent of
trades produce 112% of the total profit -- the other ninety percent lose money
together. A portfolio that holds few positions usually misses the trades that
pay for it, so breadth is not a comfort here, it is the mechanism.

**Breadth costs capital, and the flat fee punishes small positions.** The
broker's schedule is 0.1819% a side plus 4 EGP an order. The percentage does
not care about size; the 4 EGP does. Round trip:

     2,000 EGP  0.764%      20,000 EGP  0.404%
     5,000 EGP  0.524%      50,000 EGP  0.380%
    10,000 EGP  0.444%     100,000 EGP  0.372%

Above about ten thousand the flat fee stops mattering -- from there to a
hundred thousand the cost falls only 0.07 points. Below five thousand it starts
eating the median trade.

The simulation holds each position for twenty sessions, weights every position
equally at 1/slots of capital, and picks at random when more signals arrive
than there are free slots. Random is the honest stand-in: any rule for choosing
between simultaneous signals is a rule that has not been measured.

    venv\Scripts\python.exe scripts\research\position_sizing.py
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

from scripts.research.breakout_filters import HOLD, SPLIT, build

#: The shipped floor. Below it the fills stop being real.
TURNOVER_FLOOR = 5_000_000.0
SEEDS = 5


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
        if len(frame) < 500:
            continue
        frames[symbol] = frame.reset_index(drop=True)
        turnover[symbol] = float((frame["Close"] * frame["Volume"]).tail(250).median())
    return {s: f for s, f in frames.items() if turnover[s] >= TURNOVER_FLOOR}


def simulate(signals: pd.DataFrame, slots: int, seed: int):
    """One pass. Returns the equity curve and how many signals were taken."""
    rng = random.Random(seed)
    held, equity, taken = [], [1.0], 0
    for _, group in signals.groupby("Date"):
        held = [(value, days - 1) for value, days in held if days > 1]
        free = slots - len(held)
        candidates = list(group.itertuples())
        rng.shuffle(candidates)
        for candidate in candidates[:max(free, 0)]:
            held.append((candidate.forward, HOLD))
            taken += 1
            # Equal weight: one position moves the book by its return over the
            # number of slots, never by its full size.
            equity.append(equity[-1] * (1 + candidate.forward / 100 / slots))
    return taken, np.array(equity)


def main() -> int:
    frames = liquid_universe()
    panel = build(frames)
    panel["Date"] = pd.to_datetime(panel["Date"])
    signals = panel[panel["breakout"] & (panel["volume_ratio"] >= 2.5)
                    & (panel["momentum_rank"] >= 0.67)]
    signals = signals[signals["Date"] >= SPLIT].sort_values("Date")
    years = (signals["Date"].max() - signals["Date"].min()).days / 365.25

    print(f"{len(frames)} names above {TURNOVER_FLOOR/1e6:.0f}M EGP turnover")
    print(f"{len(signals)} signals over {years:.1f} validation years "
          f"({len(signals)/years:.0f} a year)\n")

    print(f"  {'slots':>6} {'taken':>7} {'of all':>7} {'annual':>8} "
          f"{'worst fall':>11} {'capital at 10k':>15}")
    print(f"  {'-' * 60}")
    for slots in (5, 10, 18, 25, 40):
        runs = [simulate(signals, slots, seed) for seed in range(SEEDS)]
        taken = int(np.mean([r[0] for r in runs]))
        annual = np.mean([(r[1][-1] ** (1 / years) - 1) * 100 for r in runs])
        falls = []
        for _, curve in runs:
            peak = np.maximum.accumulate(curve)
            falls.append(((curve - peak) / peak).min() * 100)
        print(f"  {slots:>6} {taken:>7} {taken/len(signals)*100:>6.0f}% "
              f"{annual:>+7.1f}% {np.mean(falls):>+10.1f}% "
              f"{slots * 10_000:>14,}")

    print("\nFive and ten slots are dominated: the same return as eighteen, a "
          "similar fall,\nand only a fifth of the signals. That is the thin "
          "tail escaping -- with few slots\nthe trade that pays for the year "
          "is usually one you had no room for.\n")
    print("Read the annual figures as a comparison between rows, not as a "
          "forecast. They\ncome from 2.5 years that were a strong bull market, "
          "on five random draws, with\nsurvivorship in the universe. What "
          "carries across is the shape: too few slots\nloses the tail, too "
          "many dilutes it, and the natural load sits near eighteen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
