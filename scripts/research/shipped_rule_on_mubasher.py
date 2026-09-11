r"""Does the shipped rule's evidence survive a change of data source?

Every number quoted for CONFIRMED_VOLUME_BREAKOUT was measured on the Yahoo
snapshot (``shipped_rule_evidence.py``). That snapshot is not complete and not
frozen in practice, and MubasherTrade PRO's record, now frozen in
``data/frozen_mubasher``, is the better-supported source
(docs/audits/providers/MUBASHER_VS_YAHOO.md). Before a backtest reads Mubasher,
this asks whether the rule's conclusions depend on which source it read.

The rule is not changed. Stop, holding period, costs, benchmark and scoring
are the functions ``shipped_rule_evidence.py`` already uses. Only the input
changes, one difference at a time:

* **A. Prices only.** Yahoo's symbols, each Mubasher series cut to that
  symbol's Yahoo span before indicators, so both have the same warm-up and the
  same bars available. Any difference is the prices.
* **C. Plus warm-up.** The same symbols, on Mubasher's full history, with
  trades counted from the panel's first date. Adds the longer indicator
  history the ten-year window cuts off.
* **B. Plus coverage.** Every active symbol in the frozen store, the same way.
  Adds the 33 active symbols the Yahoo snapshot lacks.

Both inputs are cleaned the way the backtest loader cleans
(``core.data_provider._clean_for_engine``): NaN rows and zero-volume bars
dropped. Mubasher's served ``Open`` is the previous close, which the rule never
reads.

    venv\Scripts\python.exe scripts\research\shipped_rule_on_mubasher.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.research.shipped_rule_evidence import (                   # noqa: E402
    HEADER, benchmark_series, panel_with_indicators, score, trades_for)
from scripts.research.signal_scan import SPLIT, costs_by_symbol        # noqa: E402
from core.frozen_mubasher_store import frozen_symbols, load_frozen     # noqa: E402
from core.universe import active_symbols                               # noqa: E402
from indicators.technical import calculate_indicators                  # noqa: E402
from strategy_momentum_breakout.config import load as load_config      # noqa: E402

OHLCV = ["Open", "High", "Low", "Close", "Volume"]
#: `panel_with_indicators` keeps a symbol only with at least this many rows;
#: the Mubasher side is held to the same floor.
MIN_ROWS = 300


def engine_clean(frame):
    kept = frame.dropna(subset=OHLCV)
    return kept[kept["Volume"] > 0]


def with_indicators(frame):
    if frame is None or len(frame) < MIN_ROWS:
        return None
    try:
        return calculate_indicators(frame[OHLCV + ["Adj Close"]].copy())
    except Exception:                                               # noqa: BLE001
        return None


def walk_forward(result):
    """Out-of-sample lift, as `shipped_rule_evidence.py` computes it."""
    if result is None:
        return 0, None
    trades = result["trades"].assign(
        lift=result["lift"], year=pd.to_datetime(result["trades"]["entry_date"]).dt.year)
    traded = []
    for year in sorted(trades["year"].unique())[1:]:
        prior = trades[trades["year"] < year]["lift"]
        this = trades[trades["year"] == year]
        if len(prior) < 20 or this.empty:
            continue
        if prior.mean() > 0:
            traded.append(this["lift"])
    pooled = pd.concat(traded) if traded else pd.Series(dtype=float)
    return len(pooled), (float(pooled.mean()) if len(pooled) else None)


def run(label, frames, cfg, start=None):
    costs = costs_by_symbol(list(frames))
    per_date = benchmark_series(frames, cfg, cfg.holding_bars)
    average_cost = float(np.mean([costs[s] for s in frames]))
    trades = trades_for(frames, costs, cfg)
    if start is not None and len(trades):
        trades = trades[pd.to_datetime(trades["signal_date"]) >= start].reset_index(drop=True)
    result = score(trades, per_date, average_cost, label)
    if result is None:
        return None
    early = pd.to_datetime(result["trades"]["signal_date"]) < pd.Timestamp(SPLIT)
    net = result["trades"]["net"]
    n_wf, wf = walk_forward(result)
    return {
        "label": label, "symbols": len(frames), "trades": len(net),
        "lift_train": float(result["lift"][early].mean()),
        "lift_valid": float(result["lift"][~early].mean()),
        "walk_forward_n": n_wf, "walk_forward_lift": wf,
        "mean_net": float(net.mean()), "median_net": float(net.median()),
        "top10_share": (float(net.nlargest(10).sum() / net.sum() * 100)
                        if net.sum() > 0 else None),
        "average_cost": average_cost, "trades_frame": result["trades"],
    }


def main() -> int:
    cfg = load_config()
    yahoo = panel_with_indicators()
    key_of = {str(k).split(".")[0].upper(): k for k in yahoo}
    start = min(frame.index[0] for frame in yahoo.values())
    held = set(frozen_symbols())
    active = set(active_symbols())
    print(f"Yahoo panel: {len(yahoo)} symbols from {start.date()}. Frozen Mubasher: "
          f"{len(held)} symbols. Active universe: {len(active)}. Split at {SPLIT}.\n")

    same_span, full_same, full_all = {}, {}, {}
    for base, key in key_of.items():
        mub = load_frozen(base)
        if mub is None:
            continue
        mub = engine_clean(mub)
        span = yahoo[key].index
        cut = with_indicators(mub[(mub.index >= span[0]) & (mub.index <= span[-1])])
        if cut is not None:
            same_span[key] = cut
        full = with_indicators(mub)
        if full is not None:
            full_same[key] = full
    for base in sorted(active & held):
        mub = load_frozen(base)
        full = with_indicators(engine_clean(mub)) if mub is not None else None
        if full is not None:
            full_all[key_of.get(base, f"{base}.CA")] = full

    # The Yahoo run is restricted to the symbols A can compare, so A differs
    # from it in prices alone.
    yahoo_matched = {k: v for k, v in yahoo.items() if k in same_span}

    print(HEADER)
    print("-" * 100)
    rows = [
        run("Yahoo, as shipped (all panel)", yahoo, cfg),
        run("Yahoo, symbols A can compare", yahoo_matched, cfg),
        run("A  Mubasher, Yahoo span", same_span, cfg),
        run("C  Mubasher, full history", full_same, cfg, start=start),
        run("B  Mubasher, every active", full_all, cfg, start=start),
    ]

    print(f"\n{'input':<34}{'symbols':>8}{'trades':>8}{'lift tr':>9}{'lift val':>10}"
          f"{'walk-fwd':>10}{'n':>6}{'mean':>8}{'median':>8}{'top10%':>8}")
    print("-" * 109)
    for row in rows:
        if row is None:
            continue
        wf = "n/a" if row["walk_forward_lift"] is None else f"{row['walk_forward_lift']:+.2f}"
        top = "n/a" if row["top10_share"] is None else f"{row['top10_share']:.1f}"
        print(f"{row['label']:<34}{row['symbols']:>8}{row['trades']:>8}"
              f"{row['lift_train']:>+9.2f}{row['lift_valid']:>+10.2f}{wf:>10}"
              f"{row['walk_forward_n']:>6}{row['mean_net']:>+8.2f}{row['median_net']:>+8.2f}{top:>8}")

    matched, a_run = rows[1], rows[2]
    if matched is not None and a_run is not None:
        key = ["symbol", "signal_date"]
        left = matched["trades_frame"].assign(signal_date=pd.to_datetime(matched["trades_frame"]["signal_date"]))
        right = a_run["trades_frame"].assign(signal_date=pd.to_datetime(a_run["trades_frame"]["signal_date"]))
        both = left.merge(right, on=key, suffixes=("_yahoo", "_mubasher"))
        only_yahoo = len(left) - len(both)
        only_mubasher = len(right) - len(both)
        print(f"\nA against Yahoo on the same symbols, trade by trade:")
        print(f"   same symbol and signal day: {len(both):,}   Yahoo only: {only_yahoo:,}   "
              f"Mubasher only: {only_mubasher:,}")
        if len(both):
            gap = (both["net_mubasher"] - both["net_yahoo"]).abs()
            print(f"   on the shared trades: mean net Yahoo {both['net_yahoo'].mean():+.2f}%, "
                  f"Mubasher {both['net_mubasher'].mean():+.2f}%; the same trade differs by "
                  f"more than 1 point on {(gap > 1).mean() * 100:.1f}%")
        for name, frame, other in (("Yahoo only", left, right), ("Mubasher only", right, left)):
            alone = frame.merge(other[key], on=key, how="left", indicator=True)
            alone = alone[alone["_merge"] == "left_only"]
            if len(alone):
                print(f"   {name:<14} mean net {alone['net'].mean():+.2f}%, "
                      f"median {alone['net'].median():+.2f}%")

    extra = sorted(set(full_all) - set(full_same))
    print(f"\nB adds {len(extra)} symbols the Yahoo panel does not carry: "
          + ", ".join(str(s).split('.')[0] for s in extra))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
