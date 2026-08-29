r"""The new rule against the one this project already ships beside it.

`services/swing_breakout.py` was derived from the same market and reached a
close relative of the same rule: a twenty-day breakout on 2.5x volume, in a
liquid name above its own 200-day average, in the top third by 12-1 momentum,
held twenty sessions with no stop. That convergence is worth more than either
result on its own -- two independent passes over the same data landing on the
same trigger is evidence the trigger is real.

But it also means a new strategy has to justify itself against *that*, not
against the Daily Dashboard strategy, and only the parts that differ are on
trial:

* a **close-position** gate -- the breakout has to hold into the close;
* a **calm** gate -- ATR% below the name's own past-year median, which is
  self-referential where Swing Breakout's momentum gate is cross-sectional;
* a **stop**, which Swing Breakout does not have at all;
* and the **price-integrity** guard.

Both are run here through the same simulator, the same costs, the same entry
convention and the same benchmark, so the difference is the rule.

    venv\Scripts\python.exe scripts\research\against_swing_breakout.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.research.breakout_exits import atr_stop, base_stop, no_trail, simulate
from scripts.research.panel import load
from scripts.research.signal_scan import (
    SPLIT, add_cross_section, costs_by_symbol, features,
)

HOLD = 20


def prepare():
    panel = load()
    costs = costs_by_symbol(panel["Symbol"].unique())
    data = features(panel, hold=HOLD)
    frames = []
    for _symbol, frame in data.groupby("Symbol", sort=False):
        f = frame.sort_values("Date").copy()
        close = f["Close"]
        f["pool_fwd"] = (close.shift(-HOLD - 1) / close.shift(-1) - 1) * 100
        bar_range = (f["High"] - f["Low"]).replace(0, np.nan)
        f["close_position"] = (close - f["Low"]) / bar_range
        f["calm_own"] = f["atrp"] <= f["atrp"].rolling(250).median().shift(1)
        f["sma200"] = close.rolling(200).mean()
        f["session_move"] = close.pct_change().abs() * 100
        f["clean"] = ~(f["session_move"] >= 30).rolling(20, min_periods=1).max().astype(bool)
        frames.append(f)
    data = pd.concat(frames, ignore_index=True)
    data = add_cross_section(data)
    return data.dropna(subset=["atrp", "VOLUME_RATIO", "ATR", "EMA200",
                               "sma200", "mom_12_1"]), costs


def evaluate(data, costs, per_date, average_cost, mask, label, stop, hold=HOLD):
    rule = dict(stop=stop, trail=no_trail, max_bars=hold, target=None)
    frame = data.assign(signal=mask.fillna(False))
    trades = []
    for symbol, part in frame.groupby("Symbol", sort=False):
        part = part.sort_values("Date").reset_index(drop=True)
        entries = np.flatnonzero(part["signal"].to_numpy())
        if len(entries):
            trades.extend(simulate(part, entries, rule, costs[symbol]))
    if not trades:
        print(f"{label:<40}{'no trades':>10}")
        return None
    t = pd.DataFrame(trades)
    t["lift"] = t["net"] - (t["entry_date"].map(per_date) - average_cost)
    t["year"] = pd.to_datetime(t["entry_date"]).dt.year
    era = pd.to_datetime(t["signal_date"]) < pd.Timestamp(SPLIT)
    years = t.groupby("year").agg(n=("net", "size"), lift=("lift", "mean"))
    counted = years[years["n"] >= 5]
    positive = t.loc[t["net"] > 0, "net"].sum()
    negative = -t.loc[t["net"] <= 0, "net"].sum()
    print(f"{label:<40}{len(t):>7,}{t.loc[era, 'lift'].mean():>+10.2f}"
          f"{t.loc[~era, 'lift'].mean():>+10.2f}{t['net'].mean():>+9.2f}"
          f"{t['net'].median():>+9.2f}{(t['net'] > 0).mean() * 100:>7.0f}"
          f"{positive / negative if negative else float('inf'):>7.2f}"
          f"{t['net'].min():>9.1f}{int((counted['lift'] < 0).sum()):>5}/{len(counted):<3}")
    return t


def main() -> int:
    data, costs = prepare()
    pool = data[data["turnover_20"] >= 2_000_000]
    per_date = pool.groupby("Date")["pool_fwd"].mean()
    average_cost = float(np.mean([costs[s] for s in pool["Symbol"].unique()]))

    shipped = (
        (data["turnover_20"] >= 5_000_000)
        & data["breakout_20"] & (data["VOLUME_RATIO"] >= 2.5)
        & (data["Close"] > data["sma200"])
        & (data["rank_mom_12_1"] >= 0.67)
    )
    new = (
        (data["turnover_20"] >= 2_000_000)
        & data["breakout_20"] & (data["VOLUME_RATIO"] >= 2.5)
        & (data["close_position"] >= 0.70)
        & (data["Close"] > data["EMA200"])
        & data["calm_own"] & data["clean"]
    )

    print(f"{'rule':<40}{'trades':>7}{'lift tr':>10}{'lift val':>10}"
          f"{'mean':>9}{'median':>9}{'win%':>7}{'PF':>7}{'worst':>9}{'bad yrs':>9}")
    print("-" * 118)
    evaluate(data, costs, per_date, average_cost, shipped,
             "Swing Breakout, as it ships (no stop)", stop=atr_stop(99))
    evaluate(data, costs, per_date, average_cost, shipped,
             "  the same, with a stop under the base", stop=base_stop)
    evaluate(data, costs, per_date, average_cost, new,
             "Confirmed Volume Breakout (no stop)", stop=atr_stop(99))
    evaluate(data, costs, per_date, average_cost, new,
             "Confirmed Volume Breakout (as built)", stop=base_stop)

    print("\nAnd the pieces, added to Swing Breakout's rule one at a time:")
    print("-" * 118)
    for label, extra in [
        ("+ close in the top 30% of the bar", data["close_position"] >= 0.70),
        ("+ calm vs its own past year", data["calm_own"]),
        ("+ price-integrity guard", data["clean"]),
        ("+ all three", (data["close_position"] >= 0.70) & data["calm_own"]
         & data["clean"]),
    ]:
        evaluate(data, costs, per_date, average_cost, shipped & extra,
                 label, stop=atr_stop(99))

    print("\n'lift' is over owning an average liquid name on the same day for")
    print("the same 20 sessions, entered at the next close, net of each")
    print("symbol's own round trip. Split at", SPLIT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
