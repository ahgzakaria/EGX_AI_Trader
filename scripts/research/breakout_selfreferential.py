r"""Can the rule be stated without the cross-section, and does it survive that?

Two of the candidate's filters are cross-sectional -- "in the calmer half of the
universe by ATR% today" and "in the top half by turnover today". A rank against
the whole universe is a real thing to compute in research and an awkward thing
to compute in production: `BacktestEngine` evaluates one symbol at a time and
never sees the others, so a rank would have to be precomputed and passed in,
which is one more artifact that can go stale or leak a future date into a past
bar.

If a *self-referential* form of each filter -- the name against its own history
rather than against its peers -- measures the same, then the rule loses a
dependency for nothing, and each signal becomes explainable to a reader from
that symbol's own chart.

The market regime is a different case and stays external: it is genuinely about
the market, and `strategy/market_analyzer.py` already loads EGX30 with a
process-level cache for exactly this.

    venv\Scripts\python.exe scripts\research\breakout_selfreferential.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.research.breakout_exits import base_stop, no_trail, simulate
from scripts.research.panel import load
from scripts.research.signal_scan import (
    SPLIT, add_cross_section, costs_by_symbol, features,
)

HOLD_CAP = 20
TURNOVER_FLOOR = 2_000_000


def prepare():
    panel = load()
    costs = costs_by_symbol(panel["Symbol"].unique())
    data = features(panel, hold=20)
    frames = []
    for _symbol, frame in data.groupby("Symbol", sort=False):
        f = frame.sort_values("Date").copy()
        close = f["Close"]
        f["pool_fwd"] = (close.shift(-HOLD_CAP - 1) / close.shift(-1) - 1) * 100
        bar_range = (f["High"] - f["Low"]).replace(0, np.nan)
        f["close_position"] = (close - f["Low"]) / bar_range
        # The name against its own past year, shifted so today is not in the
        # median it is being compared to.
        f["atrp_own_median"] = f["atrp"].rolling(250).median().shift(1)
        f["calm_for_itself"] = f["atrp"] <= f["atrp_own_median"]
        f["turnover_own_median"] = f["turnover_20"].rolling(250).median().shift(1)
        frames.append(f)
    data = pd.concat(frames, ignore_index=True)
    data = add_cross_section(data)
    return data.dropna(subset=["atrp", "rank_turnover_20", "VOLUME_RATIO",
                               "ATR", "EMA50", "atrp_own_median"]), costs


def evaluate(data, costs, per_date, average_cost, mask, label):
    frame = data.assign(signal=mask.fillna(False))
    rule = dict(stop=base_stop, trail=no_trail, max_bars=HOLD_CAP, target=None)
    trades = []
    for symbol, part in frame.groupby("Symbol", sort=False):
        part = part.sort_values("Date").reset_index(drop=True)
        entries = np.flatnonzero(part["signal"].to_numpy())
        if len(entries):
            trades.extend(simulate(part, entries, rule, costs[symbol]))
    if not trades:
        print(f"{label:<44}{'no trades':>9}")
        return
    trades = pd.DataFrame(trades)
    lift = trades["net"] - (trades["entry_date"].map(per_date) - average_cost)
    era = pd.to_datetime(trades["signal_date"]) < pd.Timestamp(SPLIT)
    years = trades.assign(year=pd.to_datetime(trades["entry_date"]).dt.year,
                          lift=lift).groupby("year").agg(
        n=("net", "size"), lift=("lift", "mean"))
    counted = years[years["n"] >= 5]
    print(f"{label:<44}{len(trades):>7,}{lift[era].mean():>+11.2f}"
          f"{lift[~era].mean():>+11.2f}{(trades['net'] > 0).mean() * 100:>7.0f}"
          f"{int((counted['lift'] < 0).sum()):>5}/{len(counted):<3}")


def main() -> int:
    data, costs = prepare()
    pool = data[(data["rank_turnover_20"] >= 0.5)
                & (data["turnover_20"] >= TURNOVER_FLOOR)]
    per_date = pool.groupby("Date")["pool_fwd"].mean()
    average_cost = float(np.mean([costs[s] for s in pool["Symbol"].unique()]))

    core = (data["breakout_20"] & (data["VOLUME_RATIO"] >= 2.5)
            & (data["close_position"] >= 0.70)
            & data["market_above_200"].fillna(False)
            & (data["Close"] > data["EMA50"]))

    cross_liquid = (data["rank_turnover_20"] >= 0.5) & (data["turnover_20"] >= TURNOVER_FLOOR)
    cross_calm = data["rank_atrp"] <= 0.5

    print(f"{'variant':<44}{'trades':>7}{'lift train':>11}{'lift valid':>11}"
          f"{'win%':>7}{'bad yrs':>9}")
    print("-" * 88)
    evaluate(data, costs, per_date, average_cost,
             core & cross_liquid & cross_calm,
             "cross-sectional (the candidate)")
    evaluate(data, costs, per_date, average_cost,
             core & cross_liquid & data["calm_for_itself"],
             "calm vs its own year, turnover cross-sec")
    evaluate(data, costs, per_date, average_cost,
             core & (data["turnover_20"] >= 5_000_000) & cross_calm,
             "calm cross-sec, turnover >= 5M EGP")
    for floor in (2, 5, 10, 20):
        evaluate(data, costs, per_date, average_cost,
                 core & (data["turnover_20"] >= floor * 1_000_000)
                 & data["calm_for_itself"],
                 f"fully self-referential, turnover >= {floor}M EGP")
    for absolute in (2.0, 2.5, 3.0, 4.0):
        evaluate(data, costs, per_date, average_cost,
                 core & (data["turnover_20"] >= 5_000_000)
                 & (data["ATR_PERCENT"] <= absolute),
                 f"turnover >= 5M, ATR% <= {absolute} flat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
