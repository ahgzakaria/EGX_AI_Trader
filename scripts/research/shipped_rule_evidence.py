r"""Every number quoted for CONFIRMED_VOLUME_BREAKOUT, from the rule as shipped.

The sweeps in `breakout_candidate.py`, `breakout_selfreferential.py` and
`breakout_robustness.py` were each run against the candidate as it stood at that
point in the investigation, which is what a search looks like. None of them is
the final rule: one used cross-sectional ranks, another had no price-integrity
guard, a third gated on EMA50 rather than EMA200.

Quoting those numbers for the shipped rule would be describing a strategy that
was never built. So this re-runs every sweep against `strategy_momentum_breakout`
exactly as it is configured, and everything in
`docs/audits/strategies/CONFIRMED_VOLUME_BREAKOUT.md` and on the dashboard page
comes from here.

The rule is read from the package rather than restated, so a config change moves
these numbers instead of silently invalidating them.

    venv\Scripts\python.exe scripts\research\shipped_rule_evidence.py
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from core.environment import load_project_environment

load_project_environment()

from scripts.research.breakout_exits import (                     # noqa: E402
    atr_stop, base_stop, no_trail, simulate,
)
from scripts.research.panel import load                           # noqa: E402
from scripts.research.signal_scan import (                        # noqa: E402
    SPLIT, costs_by_symbol,
)
from strategy_momentum_breakout.config import load as load_config  # noqa: E402
from strategy_momentum_breakout.signal import measure              # noqa: E402


def panel_with_indicators():
    """The panel, per symbol, in the shape `measure` expects."""
    panel = load().sort_values(["Symbol", "Date"])
    frames = {}
    for symbol, frame in panel.groupby("Symbol", sort=False):
        frame = frame.set_index("Date")
        if len(frame) < 300:
            continue
        frames[symbol] = frame
    return frames


def benchmark_series(frames, cfg, hold):
    """Owning an average name in the tradeable pool, from the next close."""
    rows = []
    for symbol, frame in frames.items():
        close = frame["Close"]
        turnover = (close * frame["Volume"]).rolling(cfg.turnover_window).mean()
        forward = (close.shift(-hold - 1) / close.shift(-1) - 1) * 100
        rows.append(pd.DataFrame({
            "Date": frame.index, "fwd": forward.to_numpy(),
            "eligible": (turnover >= cfg.minimum_turnover_egp).to_numpy(),
        }))
    pool = pd.concat(rows, ignore_index=True)
    pool = pool[pool["eligible"] & pool["fwd"].notna()]
    return pool.groupby("Date")["fwd"].mean()


def trades_for(frames, costs, cfg, stop=None, hold=None):
    stop = stop or base_stop
    hold = hold or cfg.holding_bars
    rule = dict(stop=stop, trail=no_trail, max_bars=hold, target=None)
    out = []
    for symbol, frame in frames.items():
        table = measure(frame, cfg)
        entries = np.flatnonzero(table["Passed"].to_numpy(bool))
        if not len(entries):
            continue
        walkable = frame.reset_index()
        for trade in simulate(walkable, entries, rule, costs[symbol]):
            trade["symbol"] = symbol
            out.append(trade)
    return pd.DataFrame(out)


def score(trades, per_date, average_cost, label):
    if trades.empty:
        print(f"{label:<34}{'no trades':>10}")
        return None
    lift = trades["net"] - (trades["entry_date"].map(per_date) - average_cost)
    era = pd.to_datetime(trades["signal_date"]) < pd.Timestamp(SPLIT)
    years = trades.assign(year=pd.to_datetime(trades["entry_date"]).dt.year,
                          lift=lift).groupby("year").agg(
        n=("net", "size"), lift=("lift", "mean"))
    counted = years[years["n"] >= 5]
    positive = trades.loc[trades["net"] > 0, "net"].sum()
    negative = -trades.loc[trades["net"] <= 0, "net"].sum()
    print(f"{label:<34}{len(trades):>7,}{trades['risk_percent'].mean():>8.1f}"
          f"{lift[era].mean():>+10.2f}{lift[~era].mean():>+10.2f}"
          f"{trades['net'].median():>+9.2f}{(trades['net'] > 0).mean() * 100:>7.0f}"
          f"{positive / negative if negative else float('inf'):>7.2f}"
          f"{int((counted['lift'] < 0).sum()):>5}/{len(counted):<3}")
    return {"lift": lift, "years": years, "trades": trades}


HEADER = (f"{'variant':<34}{'trades':>7}{'risk%':>8}{'lift tr':>10}"
          f"{'lift val':>10}{'median':>9}{'win%':>7}{'PF':>7}{'bad yrs':>9}")


def main() -> int:
    cfg = load_config()
    frames = panel_with_indicators()
    costs = costs_by_symbol(list(frames))
    per_date = benchmark_series(frames, cfg, cfg.holding_bars)
    average_cost = float(np.mean([costs[s] for s in frames]))
    print(f"{len(frames)} symbols, mean round trip {average_cost:.2f}%, "
          f"split at {SPLIT}\n")

    print("THE RULE AS SHIPPED")
    print(HEADER)
    print("-" * 100)
    shipped = trades_for(frames, costs, cfg)
    result = score(shipped, per_date, average_cost, "as configured")

    print("\nSTOP DISTANCE (everything else as shipped)")
    print(HEADER)
    print("-" * 100)
    for label, stop in [("1.5 ATR below entry", atr_stop(1.5)),
                        ("2.5 ATR below entry", atr_stop(2.5)),
                        ("4 ATR below entry", atr_stop(4.0)),
                        ("under the base (shipped)", base_stop),
                        ("no stop at all", atr_stop(99))]:
        score(trades_for(frames, costs, cfg, stop=stop), per_date,
              average_cost, label)

    print("\nHOLDING CAP (everything else as shipped)")
    print("The benchmark is rebuilt at each cap. Comparing a forty-bar trade")
    print("against a twenty-bar benchmark credits the longer hold with the")
    print("market's own extra drift, and makes every longer cap look better")
    print("than it is -- which is exactly what the first version of this sweep")
    print("did.")
    print(HEADER)
    print("-" * 100)
    for cap in (10, 15, 20, 25, 30, 40):
        marker = " <- shipped" if cap == cfg.holding_bars else ""
        score(trades_for(frames, costs, cfg, hold=cap),
              benchmark_series(frames, cfg, cap), average_cost,
              f"cap {cap} bars{marker}")

    print("\nEVERY OTHER THRESHOLD, ONE NOTCH EITHER SIDE")
    print(HEADER)
    print("-" * 100)
    sweeps = {
        "breakout_window": [10, 15, 20, 30, 40],
        "minimum_volume_ratio": [1.5, 2.0, 2.5, 3.0, 4.0],
        "minimum_close_position": [0.5, 0.6, 0.7, 0.8, 0.9],
        "calm_window": [120, 250, 500],
        "minimum_turnover_egp": [1e6, 2e6, 5e6, 1e7],
        "maximum_session_move_percent": [20.0, 30.0, 50.0, 1e9],
    }
    for field, values in sweeps.items():
        for value in values:
            variant = replace(cfg, **{field: value})
            marker = " <-" if value == getattr(cfg, field) else ""
            shown = f"{value:,.0f}" if value >= 1000 else f"{value:g}"
            score(trades_for(frames, costs, variant), per_date, average_cost,
                  f"{field[:22]} = {shown}{marker}")
        print()

    print("WALK FORWARD -- each year judged by a rule that never saw it")
    print(f"  {'year':>6}{'n':>6}{'lift so far':>14}{'traded':>9}"
          f"{'lift that year':>17}")
    trades = result["trades"].assign(
        lift=result["lift"],
        year=pd.to_datetime(result["trades"]["entry_date"]).dt.year)
    traded = []
    for year in sorted(trades["year"].unique())[1:]:
        prior = trades[trades["year"] < year]["lift"]
        this = trades[trades["year"] == year]
        if len(prior) < 20 or this.empty:
            continue
        allowed = prior.mean() > 0
        if allowed:
            traded.append(this["lift"])
        print(f"  {year:>6}{len(this):>6}{prior.mean():>+13.2f}%"
              f"{('yes' if allowed else 'no'):>9}{this['lift'].mean():>+16.2f}%")
    pooled = pd.concat(traded)
    print(f"\n  out-of-sample lift over {len(pooled)} signals: "
          f"{pooled.mean():+.2f}% per trade")

    print("\nTAIL CONCENTRATION")
    net = result["trades"]["net"]
    for n in (1, 5, 10, 20):
        print(f"  top {n:>2} trades: {net.nlargest(n).sum() / net.sum() * 100:>6.1f}% of total net")
    print(f"  mean {net.mean():+.2f}%   median {net.median():+.2f}%   "
          f"worst {net.min():+.1f}%   best {net.max():+.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
