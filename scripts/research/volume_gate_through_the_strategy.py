"""One clean comparison: vary only the volume gate, with the current code.

The earlier decomposition ran while EMA alignment was scored at zero. Restoring
it changed the score distribution and therefore the baseline, so those numbers
cannot be compared against these. Nothing is patched here -- the scoring module
is used exactly as it ships, and the only thing that moves is the gate.
"""
import glob
import os
import statistics
import sys

import pandas as pd

sys.path.insert(0, ".")
from indicators.technical import calculate_indicators
from strategy_breakout.breakout_strategy import BreakoutConfig, BreakoutSwingStrategy

COST, HOLD, TOP_N, BARS = 0.80, 20, 40, 260
WINDOWS = (0, 400, 700, 1000, 1200)
GATES = (1.5, 2.0, 2.5, 3.0)

frames = {}
for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
    symbol = os.path.basename(path)[:-4]
    f = pd.read_csv(path)
    if len(f) < 400:
        continue
    f = f.rename(columns=str.title)[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna()
    f = f[(f[["Open", "High", "Low", "Close"]] > 0).all(axis=1) & (f["Volume"] > 0)]
    f = f[(f["High"] - f["Low"]) / f["Open"] * 100 <= 25]
    if len(f) >= 400:
        frames[symbol] = f.reset_index(drop=True)

liquidity = {s: (f["Close"] * f["Volume"]).tail(250).median() for s, f in frames.items()}
prepared = {}
for symbol in sorted(liquidity, key=liquidity.get, reverse=True)[:TOP_N]:
    try:
        prepared[symbol] = calculate_indicators(frames[symbol].copy())
    except Exception:
        pass
print(f"{len(prepared)} symbols, {len(WINDOWS)} windows of {BARS} bars, hold {HOLD}d")
print()


def run(gate, offset):
    base = vars(BreakoutConfig())
    strategy = BreakoutSwingStrategy(BreakoutConfig(**{**base, "minimum_volume_ratio": gate}))
    nets = []
    for frame in prepared.values():
        start = max(200, len(frame) - BARS - offset)
        stop = max(start, len(frame) - offset - HOLD - 1)
        for i in range(start, stop):
            try:
                result = strategy.evaluate(frame, i)
            except Exception:
                continue
            if result.get("Signal") != "BUY":
                continue
            entry = float(frame["Close"].iloc[i])
            nets.append((float(frame["Close"].iloc[i + HOLD]) - entry) / entry * 100.0 - COST)
    return nets


print(f"{'gate':>6}" + "".join(f"{'w-' + str(w):>17}" for w in WINDOWS))
print("-" * (6 + 17 * len(WINDOWS)))
totals = {}
for gate in GATES:
    cells, pooled = [], []
    for offset in WINDOWS:
        nets = run(gate, offset)
        pooled += nets
        cells.append(f"{statistics.fmean(nets):+6.2f}% n={len(nets):<4}" if nets else "     none     ")
    totals[gate] = pooled
    print(f"{gate:>6.1f}" + "".join(f"{c:>17}" for c in cells))

print()
print(f"{'gate':>6}{'trades':>9}{'avg net':>10}{'win%':>9}{'median':>10}{'total':>12}")
print("-" * 56)
for gate, nets in totals.items():
    if not nets:
        continue
    print(f"{gate:>6.1f}{len(nets):>9,}{statistics.fmean(nets):>9.3f}%"
          f"{sum(1 for n in nets if n > 0) / len(nets) * 100:>8.1f}%"
          f"{statistics.median(nets):>9.3f}%{sum(nets):>11,.0f}%")
