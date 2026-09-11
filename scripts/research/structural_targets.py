r"""Two chart-reading targets on CONFIRMED_VOLUME_BREAKOUT, pre-registered.

The strategy program was closed in `INVESTIGATION_SUMMARY.md`. It is reopened
here for exactly this test, at the owner's request, and for nothing else.

**Why it is needed.** `CONFIRMED_VOLUME_BREAKOUT.md` §4 says "targets were tested
and are worse than useless" and quotes 2R and 3R. Those rows come from
`breakout_exits.py`, which ran the *candidate* of the time -- cross-sectional
ranks, a 2 ATR stop, a forty-bar cap -- not the rule as shipped. No target of any
kind has been measured on the shipped rule, and no structural target (one placed
by the chart rather than by the stop distance) has been measured anywhere.

**Pre-registration.** Everything below was fixed before the first run. Nothing is
tuned after it, and no third target is tried if both fail.

The base is the range the breakout just cleared: the `breakout_window` bars
before the signal bar, the same bars `signal.measure` takes `PriorHigh` from.

    height          = PriorHigh - lowest Low of those same bars
    MEASURED_MOVE   = PriorHigh + 1.000 x height
    FIB_161.8       = base low + 1.618 x height   (= PriorHigh + 0.618 x height)

The Fibonacci level is the 161.8% extension drawn from the base low to the base
high, which is how charting packages draw it. It sits *below* the measured move.

Everything else is the shipped rule, walked the way `shipped_rule_evidence.py`
walks it: entry at the close after the signal, the stop under the base, no
trail, `holding_bars` sessions, per-symbol round trip.

* **Stop before target within a bar.** When a bar touches both, the loss is
  taken -- the harness's existing rule, kept.
* **Target fills at the level**, never at a better gap price. The open in this
  data is carried forward from the previous close on 96-98% of bars and is not
  a tradeable price.
* **A target already at or below the entry close** is taken at the close of the
  first bar after entry, unless the stop is hit on that bar first. A limit sell
  below the market fills at the next tradeable price, and the next honest price
  here is a close. These trades are counted separately.
* A trade that exits early frees the symbol for a later signal, so the trade
  count can differ from the baseline. Matched trades are also compared.

**The bar.** A target passes only if its mean lift per trade beats the shipped
no-target rule in **both** eras (split at `SPLIT`). Lift is the house measure:
net return minus the average eligible name's return over the same twenty
sessions, net of the average round trip. Anything less is written down as a
failure and the program closes again.

Descriptive, not part of the bar: exit reasons, median bars held, net per
twenty bars held, tail concentration, and what the baseline earned on the very
trades the target closed.

    venv\Scripts\python.exe scripts\research\structural_targets.py
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from core.environment import load_project_environment

load_project_environment()

from scripts.research.breakout_exits import base_stop, no_trail, simulate  # noqa: E402
from scripts.research.shipped_rule_evidence import (                       # noqa: E402
    HEADER, benchmark_series, panel_with_indicators, score)
from scripts.research.signal_scan import SPLIT, costs_by_symbol            # noqa: E402
from strategy_momentum_breakout.config import load as load_config          # noqa: E402
from strategy_momentum_breakout.signal import measure                      # noqa: E402

#: The Fibonacci extension ratio. A definition, not a parameter: it is the
#: level being tested, fixed before the run, and no other ratio is tried.
FIB_EXTENSION = 1.618
MEASURED_MOVE = 1.0

VARIANTS = ("NO_TARGET (shipped)", "MEASURED_MOVE", "FIB_161.8")


def target_levels(frame: pd.DataFrame, table: pd.DataFrame, cfg) -> dict:
    """Both levels for every bar, from the bars `PriorHigh` itself is read off."""

    prior_high = table["PriorHigh"].to_numpy(float)
    prior_low = (frame["Low"].rolling(cfg.breakout_window).min().shift(1)
                 .to_numpy(float))
    height = prior_high - prior_low
    return {
        "NO_TARGET (shipped)": None,
        "MEASURED_MOVE": prior_high + MEASURED_MOVE * height,
        "FIB_161.8": prior_low + FIB_EXTENSION * height,
    }


def walk(frame, entries, levels, cost, cfg):
    """`breakout_exits.simulate` for the shipped rule, with an absolute target.

    Kept line-for-line with that function wherever no target applies, and
    checked against it on every symbol before any target is read.
    """
    close = frame["Close"].to_numpy(float)
    high = frame["High"].to_numpy(float)
    low = frame["Low"].to_numpy(float)
    atr = frame["ATR"].to_numpy(float)
    dates = frame["Date"].to_numpy()

    trades = []
    busy_until = -1
    for signal_index in entries:
        if signal_index <= busy_until:
            continue
        entry_index = signal_index + 1
        if entry_index >= len(close) - 1:
            continue
        entry = close[entry_index]
        if not np.isfinite(entry) or entry <= 0:
            continue
        initial_stop = base_stop(signal_index, entry_index, close, high, low, atr)
        if not np.isfinite(initial_stop) or initial_stop >= entry:
            continue

        level = None if levels is None else float(levels[signal_index])
        if level is not None and not np.isfinite(level):
            level = None
        stop = initial_stop
        exit_index, exit_price, reason = None, None, None
        last = min(entry_index + cfg.holding_bars, len(close) - 1)
        for k in range(entry_index + 1, last + 1):
            if low[k] <= stop:
                exit_price = low[k] if high[k] < stop else stop
                exit_index, reason = k, "stop"
                break
            if level is not None:
                if level <= entry:
                    exit_price, exit_index, reason = close[k], k, "reached_at_entry"
                    break
                if high[k] >= level:
                    exit_price, exit_index, reason = level, k, "target"
                    break
        if exit_index is None:
            exit_index, exit_price, reason = last, close[last], "timeout"

        gross = (exit_price / entry - 1) * 100
        trades.append({
            "signal_date": dates[signal_index],
            "entry_date": dates[entry_index],
            "bars": exit_index - entry_index,
            "gross": gross,
            "net": gross - cost,
            "reason": reason,
            "risk_percent": (entry - initial_stop) / entry * 100,
            "target_distance_percent": (np.nan if level is None
                                        else (level / entry - 1) * 100),
        })
        busy_until = exit_index
    return trades


def run(frames, costs, cfg):
    out = {name: [] for name in VARIANTS}
    checked = 0
    shipped_rule = dict(stop=base_stop, trail=no_trail,
                        max_bars=cfg.holding_bars, target=None)
    for symbol, frame in frames.items():
        table = measure(frame, cfg)
        entries = np.flatnonzero(table["Passed"].to_numpy(bool))
        if not len(entries):
            continue
        walkable = frame.reset_index()
        levels = target_levels(frame, table, cfg)
        for name in VARIANTS:
            trades = walk(walkable, entries, levels[name], costs[symbol], cfg)
            for trade in trades:
                trade["symbol"] = symbol
            out[name].extend(trades)

        # The walker must be the shipped harness when no target is set.
        reference = simulate(walkable, entries, shipped_rule, costs[symbol])
        mine = [t for t in out[VARIANTS[0]] if t["symbol"] == symbol]
        assert len(reference) == len(mine), symbol
        for a, b in zip(reference, mine):
            assert a["signal_date"] == b["signal_date"], symbol
            assert abs(a["net"] - b["net"]) < 1e-9, symbol
        checked += 1
    print(f"walker reproduces breakout_exits.simulate on all {checked} "
          f"symbols with signals\n")
    return {name: pd.DataFrame(rows) for name, rows in out.items()}


def era_means(result) -> tuple:
    trades, lift = result["trades"], result["lift"]
    early = pd.to_datetime(trades["signal_date"]) < pd.Timestamp(SPLIT)
    return (float(lift[early].mean()), float(lift[~early].mean()),
            float(trades.loc[early, "net"].mean()),
            float(trades.loc[~early, "net"].mean()),
            int(early.sum()), int((~early).sum()))


def main() -> int:
    cfg = load_config()
    frames = panel_with_indicators()
    costs = costs_by_symbol(list(frames))
    per_date = benchmark_series(frames, cfg, cfg.holding_bars)
    average_cost = float(np.mean([costs[s] for s in frames]))
    print(f"{len(frames)} symbols, mean round trip {average_cost:.2f}%, "
          f"split at {SPLIT}, holding {cfg.holding_bars} bars\n")

    trades = run(frames, costs, cfg)

    print(HEADER)
    print("-" * 100)
    scored = {name: score(trades[name], per_date, average_cost, name)
              for name in VARIANTS}

    print(f"\n{'variant':<22}{'n tr':>6}{'n val':>7}{'lift tr':>9}{'lift val':>10}"
          f"{'net tr':>9}{'net val':>9}{'bars med':>10}{'net/20b':>9}{'top10%':>8}")
    print("-" * 99)
    eras = {}
    for name in VARIANTS:
        frame = trades[name]
        lt, lv, nt, nv, ct, cv = era_means(scored[name])
        eras[name] = (lt, lv)
        per_20 = frame["net"].sum() / max(frame["bars"].sum(), 1) * 20
        total = frame["net"].sum()
        # A share of a negative total is not a share; say so instead.
        top10 = (f"{frame['net'].nlargest(10).sum() / total * 100:>8.1f}"
                 if total > 0 else f"{'n/a':>8}")
        print(f"{name:<22}{ct:>6}{cv:>7}{lt:>+9.2f}{lv:>+10.2f}{nt:>+9.2f}"
              f"{nv:>+9.2f}{frame['bars'].median():>10.0f}{per_20:>+9.2f}"
              f"{top10}")

    print("\nEXIT REASONS")
    for name in VARIANTS:
        counts = trades[name]["reason"].value_counts()
        print(f"  {name:<22}" + "  ".join(f"{k} {v}" for k, v in counts.items()))

    base = trades[VARIANTS[0]].set_index(["symbol", "signal_date"])
    for name in VARIANTS[1:]:
        frame = trades[name]
        print(f"\n{name}: target distance above entry, median "
              f"{frame['target_distance_percent'].median():.2f}%  "
              f"(IQR {frame['target_distance_percent'].quantile(.25):.2f}"
              f"..{frame['target_distance_percent'].quantile(.75):.2f})")
        hit = frame[frame["reason"].isin(["target", "reached_at_entry"])]
        matched = hit.set_index(["symbol", "signal_date"]).join(
            base[["net", "reason"]], rsuffix="_base", how="inner")
        if len(matched):
            print(f"  on the {len(matched)} trades the target closed and the "
                  f"baseline also took:")
            print(f"    target net mean {matched['net'].mean():+.2f}%   "
                  f"baseline net mean on the same trades "
                  f"{matched['net_base'].mean():+.2f}%")
            print(f"    baseline outcome on them: "
                  + "  ".join(f"{k} {v}" for k, v in
                              matched["reason_base"].value_counts().items()))
            print(f"    baseline did better on {int((matched['net_base'] > matched['net']).sum())}"
                  f", worse on {int((matched['net_base'] < matched['net']).sum())}")
            # Split by how the target closed them, so the fill rule for a
            # target already passed at entry cannot be what decides the result.
            for reason, part in matched.groupby("reason"):
                print(f"    {reason:<18} n {len(part):>4}   target net "
                      f"{part['net'].mean():+.2f}%   baseline net "
                      f"{part['net_base'].mean():+.2f}%")

    print("\nVERDICT (pre-registered: mean lift beats the shipped rule in both eras)")
    bt, bv = eras[VARIANTS[0]]
    for name in VARIANTS[1:]:
        lt, lv = eras[name]
        passed = lt > bt and lv > bv
        print(f"  {name:<22}train {lt:+.2f} vs {bt:+.2f}   valid {lv:+.2f} vs {bv:+.2f}"
              f"   -> {'PASS' if passed else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
