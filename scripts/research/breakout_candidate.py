r"""One candidate at a time, examined the way the shipped one should have been.

`breakout_exits.py` compared exit rules on an aggregate. An aggregate is how
`min_rr 3.0` came to be quoted at +40% when 84% of that came from five trades,
so nothing here is reported as an aggregate alone.

Two corrections from `entry_day_decay.py` are built in and matter more than any
parameter below:

* **The clock starts at the next close.** The signal is a close, so it is known
  only after the close, and the day after a volume breakout returns +1.06%
  against a +0.13% baseline -- a continuation you have to pay for, not a
  give-back you collect. Measuring from the signal's own close overstates the
  lift by roughly 1.3 points.
* **The benchmark is the top half of the universe by turnover**, the same pool
  the strategy is allowed to trade in. Benchmarking a liquid-only rule against a
  universe that includes the illiquid half credits it with an illiquidity
  premium it never took.

For each variant this prints year by year (a mean over ten years hides which
regime paid for it), the share of profit carried by the best five trades (a
lottery and an edge have the same mean), and the lift over owning the same pool
on the same days (in EGP terms this market compounded ~36x over the window, and
beta is not alpha).

Survivorship is not corrected and cannot be: the panel is what the provider
still serves, so delisted names are absent. That inflates the strategy and the
benchmark together, which is exactly why the headline is the *lift*.

    venv\Scripts\python.exe scripts\research\breakout_candidate.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.research.breakout_exits import base_stop, atr_stop, no_trail, simulate
from scripts.research.panel import load
from scripts.research.signal_scan import (
    SPLIT, add_cross_section, costs_by_symbol, features,
)

#: Twenty bars. `entry_day_decay.py` measured the lift at 20 and at 40 bars, from
#: the next close: +1.86%/+1.29% at twenty, +1.60%/+0.14% at forty. The edge
#: decays inside the second month, so the second month is not held.
HOLD_CAP = 20

#: A floor in EGP, on top of the relative turnover rank, so a signal cannot be
#: generated in a name that trades less in a day than one position is worth.
TURNOVER_FLOOR = 2_000_000


def close_position(data):
    bar_range = (data["High"] - data["Low"]).replace(0, np.nan)
    return (data["Close"] - data["Low"]) / bar_range


def variants(data):
    trigger = data["breakout_20"] & (data["VOLUME_RATIO"] >= 2.5)
    liquid = (data["rank_turnover_20"] >= 0.5) & (data["turnover_20"] >= TURNOVER_FLOOR)
    strong = close_position(data) >= 0.70
    regime = data["market_above_200"].fillna(False)
    return {
        "A trigger, liquid pool": trigger & liquid,
        "B + strong close": trigger & liquid & strong,
        "C + market above 200d": trigger & liquid & strong & regime,
        "D + name above EMA50": trigger & liquid & strong & regime
        & (data["Close"] > data["EMA50"]),
        "E + calm half by ATR%": trigger & liquid & strong & regime
        & (data["Close"] > data["EMA50"]) & (data["rank_atrp"] <= 0.5),
        "F + 12-1 momentum top half": trigger & liquid & strong & regime
        & (data["Close"] > data["EMA50"]) & (data["rank_atrp"] <= 0.5)
        & (data["rank_mom_12_1"] >= 0.5),
    }


def run(data, mask, costs, rule):
    data = data.assign(signal=mask.reindex(data.index, fill_value=False).fillna(False))
    trades = []
    for symbol, frame in data.groupby("Symbol", sort=False):
        frame = frame.sort_values("Date").reset_index(drop=True)
        entries = np.flatnonzero(frame["signal"].to_numpy())
        if not len(entries):
            continue
        for trade in simulate(frame, entries, rule, costs[symbol]):
            trade["symbol"] = symbol
            trades.append(trade)
    return pd.DataFrame(trades)


def benchmark_by_date(pool, cap):
    """Owning an average name in the same pool, from the same next close."""
    column = f"pool_fwd_{cap}"
    return pool.groupby("Date")[column].mean()


def describe(trades, label, per_date_benchmark, average_cost):
    if trades.empty:
        print(f"\n{label}: no trades")
        return
    net = trades["net"]
    matched = trades["entry_date"].map(per_date_benchmark) - average_cost
    lift = net - matched
    positive, negative = net[net > 0].sum(), -net[net <= 0].sum()
    top5 = net.sort_values(ascending=False).head(5).sum()
    years = trades.assign(year=pd.to_datetime(trades["entry_date"]).dt.year,
                          lift=lift)
    table = years.groupby("year").agg(
        n=("net", "size"), mean=("net", "mean"), median=("net", "median"),
        lift=("lift", "mean"))
    table["win%"] = years.groupby("year")["net"].apply(lambda s: (s > 0).mean() * 100)

    era = pd.to_datetime(trades["signal_date"]) < pd.Timestamp(SPLIT)
    print(f"\n{label}")
    print(f"  trades {len(trades):,} ({len(trades) / 10:.0f}/yr)   "
          f"median hold {trades['bars'].median():.0f} bars   "
          f"mean risk/share {trades['risk_percent'].mean():.1f}%")
    print(f"  mean net {net.mean():+.2f}%   median {net.median():+.2f}%   "
          f"win {(net > 0).mean() * 100:.0f}%   PF {positive / negative if negative else float('inf'):.2f}")
    print(f"  LIFT over the same pool on the same days:  "
          f"all {lift.mean():+.2f}%   train {lift[era].mean():+.2f}%   "
          f"validation {lift[~era].mean():+.2f}%")
    print(f"  best 5 trades are {top5 / net.sum() * 100:.0f}% of total net")
    print("  " + table.round(2).to_string().replace("\n", "\n  "))
    print(f"  years with >=5 trades whose LIFT was negative: "
          f"{int(((table['n'] >= 5) & (table['lift'] < 0)).sum())} of "
          f"{int((table['n'] >= 5).sum())}")


def main() -> int:
    panel = load()
    costs = costs_by_symbol(panel["Symbol"].unique())
    data = features(panel, hold=20)
    frames = []
    for _symbol, frame in data.groupby("Symbol", sort=False):
        f = frame.sort_values("Date").copy()
        close = f["Close"]
        # From the next close, held HOLD_CAP bars -- what a buyer actually gets.
        f[f"pool_fwd_{HOLD_CAP}"] = (
            close.shift(-HOLD_CAP - 1) / close.shift(-1) - 1) * 100
        frames.append(f)
    data = pd.concat(frames, ignore_index=True)
    data = add_cross_section(data)
    data = data.dropna(subset=["mom_12_1", "atrp", "rank_turnover_20",
                               "VOLUME_RATIO", "ATR", "EMA50"])

    pool = data[(data["rank_turnover_20"] >= 0.5)
                & (data["turnover_20"] >= TURNOVER_FLOOR)]
    per_date = benchmark_by_date(pool, HOLD_CAP)
    average_cost = float(np.mean([costs[s] for s in pool["Symbol"].unique()]))
    print(f"pool: {pool['Symbol'].nunique()} symbols, {len(pool):,} bars, "
          f"mean round trip {average_cost:.2f}%")

    print("\n" + "=" * 78)
    print("STAGE 1 -- which filters earn their place (stop under the base, cap 20)")
    print("=" * 78)
    rule = dict(stop=base_stop, trail=no_trail, max_bars=HOLD_CAP, target=None)
    for label, mask in variants(data).items():
        describe(run(data, mask, costs, rule), label, per_date, average_cost)

    print("\n" + "=" * 78)
    print("STAGE 2 -- the stop, on the filter set chosen in stage 1")
    print("=" * 78)
    chosen = variants(data)["E + calm half by ATR%"]
    for label, stop in [("under the 20-bar base -0.3 ATR", base_stop),
                        ("1.5 ATR below entry", atr_stop(1.5)),
                        ("2.5 ATR below entry", atr_stop(2.5)),
                        ("4 ATR below entry", atr_stop(4.0)),
                        ("no stop at all", atr_stop(99))]:
        rule = dict(stop=stop, trail=no_trail, max_bars=HOLD_CAP, target=None)
        describe(run(data, chosen, costs, rule), label, per_date, average_cost)

    print("\n" + "=" * 78)
    print("STAGE 3 -- a stop decided on the close, which a wick cannot reach")
    print("=" * 78)

    def below(which, buffer=0.0):
        def rule(k, entry, close, ema20, ema50):
            reference = (ema20 if which == 20 else ema50)[k]
            return close[k] < reference * (1 - buffer)
        return rule

    def below_entry(k, entry, close, ema20, ema50):
        return close[k] < entry * 0.90

    for label, close_exit in [("close under EMA20", below(20)),
                              ("close under EMA20 by 1%", below(20, 0.01)),
                              ("close under EMA50", below(50)),
                              ("close 10% under entry", below_entry)]:
        rule = dict(stop=atr_stop(99), trail=no_trail, max_bars=HOLD_CAP,
                    target=None, close_exit=close_exit)
        describe(run(data, chosen, costs, rule), label, per_date, average_cost)

    print("\n" + "=" * 78)
    print("STAGE 4 -- how long to hold, with the close-based stop")
    print("=" * 78)
    print("The benchmark is rebuilt at each cap. Holding a trade for forty bars")
    print("and comparing it against a twenty-bar benchmark credits the longer")
    print("hold with the market's own extra drift; an earlier version of this")
    print("sweep did that and made every longer cap look better than it is.")
    for cap in (10, 15, 20, 30, 40):
        rule = dict(stop=atr_stop(99), trail=no_trail, max_bars=cap,
                    target=None, close_exit=below(20))
        # Shift on the *unfiltered* frame and filter afterwards. Shifting
        # inside an already-filtered pool walks across the gaps the filter left
        # and compares a close to one from months later.
        forward = data.groupby("Symbol", sort=False)["Close"].transform(
            lambda s, cap=cap: (s.shift(-cap - 1) / s.shift(-1) - 1) * 100)
        matched = (data.assign(_f=forward)
                   .loc[(data["rank_turnover_20"] >= 0.5)
                        & (data["turnover_20"] >= TURNOVER_FLOOR)]
                   .groupby("Date")["_f"].mean())
        describe(run(data, chosen, costs, rule), f"cap {cap} bars",
                 matched, average_cost)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
