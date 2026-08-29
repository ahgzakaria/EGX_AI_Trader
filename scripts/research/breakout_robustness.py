r"""Is the candidate a plateau or a spike?

The candidate in `breakout_candidate.py` was chosen after roughly fifteen filters,
five stops and five holding caps were compared, in two eras that were both
visible while choosing. At that many comparisons, a rule that wins on one exact
set of thresholds and falls apart one notch either side is a coincidence with
good manners.

So every threshold is moved on its own, one notch up and one notch down, and the
lift is re-measured in both eras. A real effect degrades smoothly. A fitted one
falls off a cliff.

The second half walks the rule forward: it is evaluated only on data after the
period used to check it was working, in one-year steps, so no year is judged by
a rule that had seen it.

    venv\Scripts\python.exe scripts\research\breakout_robustness.py
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

#: The candidate, as chosen.
DEFAULTS = dict(base=20, volume=2.5, close_position=0.70, atr_rank=0.5,
                regime=200, hold=20)


def prepare():
    panel = load()
    costs = costs_by_symbol(panel["Symbol"].unique())
    data = features(panel, hold=20)
    frames = []
    for _symbol, frame in data.groupby("Symbol", sort=False):
        f = frame.sort_values("Date").copy()
        close = f["Close"]
        f["pool_fwd"] = (close.shift(-HOLD_CAP - 1) / close.shift(-1) - 1) * 100
        for window in (10, 15, 20, 30, 40):
            f[f"bo_{window}"] = close > f["High"].rolling(window).max().shift(1)
        bar_range = (f["High"] - f["Low"]).replace(0, np.nan)
        f["close_position"] = (close - f["Low"]) / bar_range
        frames.append(f)
    data = pd.concat(frames, ignore_index=True)
    data = add_cross_section(data)

    # The regime series, at two lengths, from the equal-weighted return index.
    level = data.groupby("Date")["Close"].mean()  # placeholder, replaced below
    from scripts.research.signal_scan import market_index
    index = market_index(panel)
    for window in (100, 150, 200, 250):
        index[f"regime_{window}"] = (
            index["market_level"] > index["market_level"].rolling(window).mean())
    data = data.merge(index[[f"regime_{w}" for w in (100, 150, 200, 250)]],
                      left_on="Date", right_index=True, how="left")
    return data.dropna(subset=["mom_12_1", "atrp", "rank_turnover_20",
                               "VOLUME_RATIO", "ATR", "EMA50"]), costs


def mask_for(data, **kwargs):
    p = dict(DEFAULTS, **kwargs)
    return (
        data[f"bo_{p['base']}"]
        & (data["VOLUME_RATIO"] >= p["volume"])
        & (data["close_position"] >= p["close_position"])
        & data[f"regime_{p['regime']}"].fillna(False)
        & (data["Close"] > data["EMA50"])
        & (data["rank_atrp"] <= p["atr_rank"])
        & (data["rank_turnover_20"] >= 0.5)
        & (data["turnover_20"] >= TURNOVER_FLOOR)
    )


def evaluate(data, costs, per_date, average_cost, hold=HOLD_CAP, **kwargs):
    mask = mask_for(data, **kwargs)
    rule = dict(stop=base_stop, trail=no_trail, max_bars=hold, target=None)
    frame = data.assign(signal=mask.fillna(False))
    trades = []
    for symbol, part in frame.groupby("Symbol", sort=False):
        part = part.sort_values("Date").reset_index(drop=True)
        entries = np.flatnonzero(part["signal"].to_numpy())
        if len(entries):
            trades.extend(simulate(part, entries, rule, costs[symbol]))
    if not trades:
        return None
    trades = pd.DataFrame(trades)
    lift = trades["net"] - (trades["entry_date"].map(per_date) - average_cost)
    era = pd.to_datetime(trades["signal_date"]) < pd.Timestamp(SPLIT)
    years = trades.assign(year=pd.to_datetime(trades["entry_date"]).dt.year,
                          lift=lift).groupby("year").agg(
        n=("net", "size"), lift=("lift", "mean"))
    counted = years[years["n"] >= 5]
    return {
        "n": len(trades),
        "lift_train": lift[era].mean(),
        "lift_valid": lift[~era].mean(),
        "bad": int((counted["lift"] < 0).sum()),
        "years": len(counted),
        "win": (trades["net"] > 0).mean() * 100,
    }


def main() -> int:
    data, costs = prepare()
    pool = data[(data["rank_turnover_20"] >= 0.5)
                & (data["turnover_20"] >= TURNOVER_FLOOR)]
    per_date = pool.groupby("Date")["pool_fwd"].mean()
    average_cost = float(np.mean([costs[s] for s in pool["Symbol"].unique()]))

    sweeps = {
        "breakout base (bars)": ("base", [10, 15, 20, 30, 40]),
        "volume ratio at least": ("volume", [1.5, 2.0, 2.5, 3.0, 4.0]),
        "close in top of bar": ("close_position", [0.5, 0.6, 0.7, 0.8, 0.9]),
        "ATR% rank at most": ("atr_rank", [0.3, 0.4, 0.5, 0.6, 0.7]),
        "regime average (bars)": ("regime", [100, 150, 200, 250]),
        "holding cap (bars)": ("hold", [10, 15, 20, 25, 30]),
    }

    print("Each threshold moved on its own; everything else at the candidate.")
    print("A real effect degrades smoothly across a sweep. A fitted one spikes.\n")
    for title, (key, values) in sweeps.items():
        print(f"{title}")
        print(f"  {'value':>8}{'trades':>9}{'lift train':>12}{'lift valid':>12}"
              f"{'win%':>7}{'bad yrs':>10}")
        for value in values:
            # The holding cap changes what the trade is, so it has to change
            # what it is compared against too. Holding forty bars and
            # benchmarking against twenty credits the longer hold with the
            # market's own extra drift; the shipped rule's cap sweep in
            # `shipped_rule_evidence.py` peaks at twenty once this is matched
            # and rises monotonically when it is not.
            matched = per_date
            if key == "hold":
                matched = data.assign(_f=data.groupby("Symbol", sort=False)[
                    "Close"].transform(
                        lambda s, cap=value: (s.shift(-cap - 1) / s.shift(-1) - 1) * 100)
                ).pipe(lambda d: d[(d["rank_turnover_20"] >= 0.5)
                                   & (d["turnover_20"] >= TURNOVER_FLOOR)]
                       ).groupby("Date")["_f"].mean()
            result = evaluate(data, costs, matched, average_cost, **{key: value})
            if result is None:
                print(f"  {value:>8}{'no trades':>9}")
                continue
            marker = "  <- candidate" if value == DEFAULTS[key] else ""
            print(f"  {value:>8}{result['n']:>9,}{result['lift_train']:>+12.2f}"
                  f"{result['lift_valid']:>+12.2f}{result['win']:>7.0f}"
                  f"{result['bad']:>6}/{result['years']:<3}{marker}")
        print()

    print("=" * 78)
    print("WALK FORWARD -- each year judged by a rule that never saw it")
    print("=" * 78)
    print("The rule has no fitted coefficients, so 'training' here means only:")
    print("was its lift positive over everything up to the end of the prior year?")
    print("If it was, the next year is traded. If not, that year is stood out of.\n")
    result = evaluate(data, costs, per_date, average_cost)
    mask = mask_for(data)
    rule = dict(stop=base_stop, trail=no_trail, max_bars=HOLD_CAP, target=None)
    frame = data.assign(signal=mask.fillna(False))
    trades = []
    for symbol, part in frame.groupby("Symbol", sort=False):
        part = part.sort_values("Date").reset_index(drop=True)
        entries = np.flatnonzero(part["signal"].to_numpy())
        if len(entries):
            trades.extend(simulate(part, entries, rule, costs[symbol]))
    trades = pd.DataFrame(trades)
    trades["lift"] = trades["net"] - (trades["entry_date"].map(per_date) - average_cost)
    trades["year"] = pd.to_datetime(trades["entry_date"]).dt.year

    print(f"  {'year':>6}{'n':>6}{'lift so far':>14}{'traded?':>10}"
          f"{'lift that year':>17}")
    traded, skipped = [], []
    for year in sorted(trades["year"].unique())[1:]:
        prior = trades[trades["year"] < year]["lift"]
        this = trades[trades["year"] == year]
        if len(prior) < 20:
            continue
        allowed = prior.mean() > 0
        (traded if allowed else skipped).append(this["lift"])
        print(f"  {year:>6}{len(this):>6}{prior.mean():>+13.2f}%"
              f"{'yes' if allowed else 'no':>10}{this['lift'].mean():>+16.2f}%")
    if traded:
        pooled = pd.concat(traded)
        print(f"\n  out-of-sample lift over {len(pooled)} traded signals: "
              f"{pooled.mean():+.2f}% per trade")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
