r"""Does the breakout's lift survive the day it takes to act on it?

`breakout_study.py` measured a lift of +3.13% (train) and +1.36% (validation)
for a twenty-day breakout on 2.5x volume. `breakout_candidate.py` simulated the
same trigger and found a *negative* lift. Two measurements of the same rule
cannot both be right, so this isolates the three things that differ between
them:

1. **When the clock starts.** The scan measures forward return from the signal's
   own close. You cannot buy at a close you have only just seen, so the trade
   starts one bar later.
2. **What it is compared against.** The scan benchmarks against the top half of
   the universe by turnover; the simulation benchmarked against the whole
   universe, and the illiquid half of an EGX-like market carries its own premium.
3. **How long it is held.** Twenty bars against forty.

Each is varied on its own here. The answer decides whether there is a strategy
to build at all.

    venv\Scripts\python.exe scripts\research\entry_day_decay.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.research.panel import load
from scripts.research.signal_scan import (
    SPLIT, add_cross_section, costs_by_symbol, features,
)


def build(hold_lengths=(20, 40)):
    panel = load()
    data = features(panel, hold=20)
    frames = []
    for _symbol, frame in data.groupby("Symbol", sort=False):
        f = frame.sort_values("Date").copy()
        close = f["Close"]
        for hold in hold_lengths:
            # From today's close, which is the day the signal is known.
            f[f"same_day_{hold}"] = (close.shift(-hold) / close - 1) * 100
            # From tomorrow's close, which is the first price you could pay.
            f[f"next_day_{hold}"] = (close.shift(-hold - 1) / close.shift(-1) - 1) * 100
        f["day_after"] = (close.shift(-1) / close - 1) * 100
        frames.append(f)
    data = pd.concat(frames, ignore_index=True)
    return add_cross_section(data)


def main() -> int:
    data = build()
    costs = costs_by_symbol(data["Symbol"].unique())
    data = data.dropna(subset=["mom_12_1", "atrp", "rank_turnover_20",
                               "VOLUME_RATIO", "ATR"])
    data["cost"] = data["Symbol"].map(costs)

    trigger = data["breakout_20"] & (data["VOLUME_RATIO"] >= 2.5)

    print("1. The breakout day is followed by a give-back.")
    print(f"{'group':<34}{'n':>9}{'next-day return':>18}")
    print("-" * 62)
    for name, mask in [("every bar", pd.Series(True, index=data.index)),
                       ("20d breakout", data["breakout_20"]),
                       ("20d breakout + vol 2.5x", trigger),
                       ("20d breakout + vol 4x",
                        data["breakout_20"] & (data["VOLUME_RATIO"] >= 4))]:
        part = data[mask]["day_after"].dropna()
        print(f"{name:<34}{len(part):>9,}{part.mean():>17.3f}%")

    for universe_name, universe in [
            ("top-half turnover", data[data["rank_turnover_20"] >= 0.5]),
            ("whole universe", data)]:
        print(f"\n2. Lift over '{universe_name}', by when the clock starts.")
        print(f"{'measurement':<34}{'train n':>9}{'lift':>9}"
              f"{'valid n':>9}{'lift':>9}")
        print("-" * 71)
        selected = universe[trigger.reindex(universe.index, fill_value=False)]
        for hold in (20, 40):
            for start in ("same_day", "next_day"):
                column = f"{start}_{hold}"
                net_all = (universe[column] - universe["cost"]).dropna()
                net_sel = (selected[column] - selected["cost"]).dropna()
                dates_all = universe.loc[net_all.index, "Date"]
                dates_sel = selected.loc[net_sel.index, "Date"]
                base_tr = net_all[dates_all < SPLIT].mean()
                base_te = net_all[dates_all >= SPLIT].mean()
                tr = net_sel[dates_sel < SPLIT]
                te = net_sel[dates_sel >= SPLIT]
                label = ("from the signal close" if start == "same_day"
                         else "from the NEXT close")
                print(f"{f'hold {hold}, {label}':<34}{len(tr):>9,}"
                      f"{tr.mean() - base_tr:>+9.2f}{len(te):>9,}"
                      f"{te.mean() - base_te:>+9.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
