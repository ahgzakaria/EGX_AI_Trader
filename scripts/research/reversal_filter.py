"""Does declining the most extended names improve the swing signal?

Sorting EGX liquid names by three and six month price change and reading the
next twenty days found the relationship runs the wrong way for momentum:
the highest decile of three-month change returned +1.5% against +4.7% for the
lowest, monotone -0.68, and the sign held in both eras. That is short-term
reversal, which is a documented effect, but it is the opposite of what the
momentum literature would predict and the opposite of what this was looking
for.

A decile study is not a rule. This turns it into one -- decline a signal when
the name is already in the top N% of trailing change -- and runs it through
BreakoutSwingStrategy, reporting the genuinely out-of-sample window on its own
because a pooled figure over mostly in-sample data is how three earlier
candidates looked good and were not.

Read-only, ships nothing.
"""
from __future__ import annotations

import glob
import os
import statistics
import sys

import pandas as pd

sys.path.insert(0, ".")

COST, HOLD, TOP_N, BARS = 0.80, 20, 40, 260
MONTH = 21
#: w-0 is the only window that postdates the 2024-01-01 fit boundary.
WINDOWS = (0, 400, 700, 1000, 1200)
ERAS = {0: "OUT-OF-SAMPLE", 400: "mixed", 700: "in-era", 1000: "in-era", 1200: "in-era"}


def prepare():
    from indicators.technical import calculate_indicators

    frames = {}
    for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
        symbol = os.path.basename(path)[:-4]
        f = pd.read_csv(path)
        if len(f) < 500:
            continue
        f = f.rename(columns=str.title)[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna()
        f = f[(f[["Open", "High", "Low", "Close"]] > 0).all(axis=1) & (f["Volume"] > 0)]
        f = f[(f["High"] - f["Low"]) / f["Open"] * 100 <= 25]
        if len(f) >= 500:
            frames[symbol] = f.reset_index(drop=True)

    liquidity = {s: (f["Close"] * f["Volume"]).tail(250).median() for s, f in frames.items()}
    universe = sorted(liquidity, key=liquidity.get, reverse=True)[:TOP_N]

    prepared = {}
    for symbol in universe:
        try:
            frame = calculate_indicators(frames[symbol].copy())
        except Exception:
            continue
        frame["momentum_3m"] = frame["Close"].pct_change(3 * MONTH) * 100
        frame["momentum_6m"] = frame["Close"].pct_change(6 * MONTH) * 100
        prepared[symbol] = frame
    return prepared


def run(prepared, *, feature=None, cutoff=None):
    """Backtest, optionally declining names above a trailing-change cutoff."""

    from strategy_breakout.breakout_strategy import BreakoutSwingStrategy

    strategy = BreakoutSwingStrategy()
    per_window = {}
    for offset in WINDOWS:
        nets = []
        for frame in prepared.values():
            start = max(200, len(frame) - BARS - offset)
            stop = max(start, len(frame) - offset - HOLD - 1)
            for i in range(start, stop):
                if feature is not None:
                    value = frame[feature].iloc[i]
                    if pd.notna(value) and value > cutoff:
                        continue
                try:
                    result = strategy.evaluate(frame, i)
                except Exception:
                    continue
                if result.get("Signal") != "BUY":
                    continue
                entry = float(frame["Close"].iloc[i])
                nets.append(
                    (float(frame["Close"].iloc[i + HOLD]) - entry) / entry * 100.0 - COST
                )
        per_window[offset] = nets
    return per_window


def report(label, per_window, baseline=None):
    cells = []
    for offset in WINDOWS:
        nets = per_window[offset]
        cells.append(f"{statistics.fmean(nets):+6.2f}%/{len(nets):<4}" if nets else "   --      ")
    total = [n for nets in per_window.values() for n in nets]
    if not total:
        print(f"{label:<28} no signals")
        return
    won = ""
    if baseline:
        won = "  " + ("BETTER" if statistics.fmean(per_window[0]) >
                      statistics.fmean(baseline[0]) else "worse") + " out-of-sample"
    print(f"{label:<28}" + "".join(f"{c:>13}" for c in cells)
          + f"{sum(1 for n in total if n > 0) / len(total) * 100:>7.1f}%" + won)


def main() -> int:
    prepared = prepare()
    print(f"{len(prepared)} symbols, {HOLD}-day hold, net of {COST}%")
    print()
    header = "".join(f"{'w-' + str(w):>13}" for w in WINDOWS)
    print(f"{'filter':<28}{header}{'win%':>7}")
    print(f"{'':<28}" + "".join(f"{ERAS[w]:>13}" for w in WINDOWS))
    print("-" * (28 + 13 * len(WINDOWS) + 7))

    baseline = run(prepared)
    report("none (shipping)", baseline)

    for feature in ("momentum_3m", "momentum_6m"):
        for cutoff in (20, 40, 60):
            report(f"decline {feature} > {cutoff}%",
                   run(prepared, feature=feature, cutoff=cutoff), baseline)

    print()
    print("w-0 is the only column that is out of sample for the decile study")
    print("that suggested this filter. A candidate that improves the in-era")
    print("columns and not w-0 has told us nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
