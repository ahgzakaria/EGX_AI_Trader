r"""The one rule that survived both eras, taken apart.

`signal_scan.py` put twenty conditions through the same test -- lift over owning
the same universe on the same days, net of each symbol's own round trip, in two
eras split at 2023-01-01. Almost everything that looked strong in 2016-2022 was
zero or negative in 2023-2026, including the shipped strategy's own gate stack
(+1.91% then -0.22%).

Two things did not fall over: a twenty-day breakout, and volume confirming it.

This module asks how much of that is real. It sweeps the two parameters that
define the rule, adds one filter at a time on top (never a stack, so a result
belongs to an idea rather than to a combination nobody can interpret), and then
stops using forward returns altogether and simulates the actual trade -- entry
at the *next* close, a stop, a cap, and the symbol's own costs.

Entry is modelled at the next bar's close, never at the open: the open in this
data is carried forward from the previous close on 96-98% of bars and is not a
price anybody could trade at.

    venv\Scripts\python.exe scripts\research\breakout_study.py
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


def eras(selected, base, column="net"):
    tr = selected[selected["Date"] < SPLIT][column]
    te = selected[selected["Date"] >= SPLIT][column]
    btr = base[base["Date"] < SPLIT][column]
    bte = base[base["Date"] >= SPLIT][column]
    return (len(tr), tr.mean() - btr.mean(), len(te), te.mean() - bte.mean())


def sweep(data):
    base = data[data["rank_turnover_20"] >= 0.5]
    print("\n1. Volume confirmation, swept. Lift over owning everything, 20-bar hold.")
    print(f"{'volume ratio at least':<26}{'train n':>9}{'lift':>8}"
          f"{'valid n':>9}{'lift':>8}{'per year':>10}")
    print("-" * 70)
    for threshold in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0):
        selected = base[base["breakout_20"] & (base["VOLUME_RATIO"] >= threshold)]
        n_tr, lift_tr, n_te, lift_te = eras(selected, base)
        years = selected["Date"].dt.year.nunique()
        print(f"{threshold:<26.1f}{n_tr:>9,}{lift_tr:>+8.2f}{n_te:>9,}"
              f"{lift_te:>+8.2f}{len(selected) / max(years, 1):>10,.0f}")

    print("\n2. Breakout base length, at volume >= 2.5x.")
    print(f"{'base length (bars)':<26}{'train n':>9}{'lift':>8}"
          f"{'valid n':>9}{'lift':>8}")
    print("-" * 60)
    for window in (10, 20, 40, 60, 120, 252):
        column = f"bo_{window}"
        selected = base[base[column] & (base["VOLUME_RATIO"] >= 2.5)]
        n_tr, lift_tr, n_te, lift_te = eras(selected, base)
        print(f"{window:<26}{n_tr:>9,}{lift_tr:>+8.2f}{n_te:>9,}{lift_te:>+8.2f}")

    print("\n3. One filter at a time on top of `20d breakout + volume >= 2.5x`.")
    trigger = base["breakout_20"] & (base["VOLUME_RATIO"] >= 2.5)
    extras = {
        "(nothing added)": lambda d: pd.Series(True, index=d.index),
        "market above its 200d": lambda d: d["market_above_200"].fillna(False),
        "market above its 50d": lambda d: d["market_above_50"].fillna(False),
        "price above its 200d": lambda d: d["above_200"],
        "EMA20 > EMA50": lambda d: d["EMA20"] > d["EMA50"],
        "12-1 momentum top half": lambda d: d["rank_mom_12_1"] >= 0.5,
        "12-1 momentum top third": lambda d: d["rank_mom_12_1"] >= 0.67,
        "1-0 momentum NOT top third": lambda d: d["rank_mom_1_0"] < 0.67,
        "within 10% of 52w high": lambda d: d["pct_of_52w_high"] >= 90,
        "ADX >= 21": lambda d: d["ADX"] >= 21,
        "ATR% below its median": lambda d: d["rank_atrp"] <= 0.5,
        "ATR% >= 1.5 (shipped gate)": lambda d: d["ATR_PERCENT"] >= 1.5,
        "top quartile turnover": lambda d: d["rank_turnover_20"] >= 0.75,
        "close in top 25% of bar": lambda d: (
            (d["Close"] - d["Low"]) / (d["High"] - d["Low"]).replace(0, np.nan) >= 0.75),
        "not already extended (<8% over EMA20)": lambda d: d["EMA20_DIST"] < 8,
    }
    print(f"{'added filter':<40}{'train n':>9}{'lift':>8}{'valid n':>9}"
          f"{'lift':>8}{'badyr':>8}")
    print("-" * 84)
    for name, extra in extras.items():
        selected = base[trigger & extra(base)]
        if len(selected) < 200:
            print(f"{name:<40}{'too few':>9}")
            continue
        n_tr, lift_tr, n_te, lift_te = eras(selected, base)
        years = selected.assign(year=selected["Date"].dt.year).groupby("year")["net"]
        counted = years.agg(["mean", "count"])
        counted = counted[counted["count"] >= 15]
        bad = int((counted["mean"] < 0).sum())
        print(f"{name:<40}{n_tr:>9,}{lift_tr:>+8.2f}{n_te:>9,}{lift_te:>+8.2f}"
              f"{bad:>4}/{len(counted):<3}")


def main() -> int:
    panel = load()
    costs = costs_by_symbol(panel["Symbol"].unique())
    data = features(panel, hold=20)

    # Extra base lengths, all shifted so today cannot break out of itself.
    frames = []
    for _symbol, frame in data.groupby("Symbol", sort=False):
        f = frame.sort_values("Date").copy()
        for window in (10, 40, 120, 252):
            f[f"bo_{window}"] = f["Close"] > f["High"].rolling(window).max().shift(1)
        f["bo_20"] = f["breakout_20"]
        f["bo_60"] = f["breakout_60"]
        frames.append(f)
    data = pd.concat(frames, ignore_index=True)

    data = add_cross_section(data)
    data = data.dropna(subset=["fwd", "mom_12_1", "above_200", "atrp",
                               "rank_turnover_20", "VOLUME_RATIO"])
    data["net"] = data["fwd"] - data["Symbol"].map(costs)

    print(f"{data['Symbol'].nunique()} symbols, {len(data):,} usable bars, "
          f"split at {SPLIT}")
    sweep(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
