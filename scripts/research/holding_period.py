"""The cost is fixed per round trip. What changes with holding period is the
move you are paying it to capture.

Intraday drift on liquid EGX names is +0.0775% against a 0.80% round trip: the
cost is more than ten times the whole average move. This asks the obvious next
question -- how far out do you have to hold before the average move is large
enough that 0.80% stops being the dominant term?
"""
import csv
import glob
import os
import statistics
from collections import defaultdict

COST = 0.80
TOP_N = 40

data = defaultdict(list)
for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
    symbol = os.path.basename(path)[:-4]
    for r in csv.DictReader(open(path, encoding="utf-8")):
        try:
            o, h, l, c = (float(r["open"]), float(r["high"]),
                          float(r["low"]), float(r["close"]))
            v = float(r["volume"] or 0)
        except Exception:
            continue
        if min(o, h, l, c) <= 0 or v <= 0 or not (l <= o <= h and l <= c <= h):
            continue
        if (h - l) / o * 100.0 > 25.0:
            continue
        data[symbol].append((r["date"], o, h, l, c, v))

ranked = sorted(
    ((sorted(d[4] * d[5] for d in v[-250:])[len(v[-250:]) // 2], k)
     for k, v in data.items() if len(v) >= 400),
    reverse=True,
)
universe = [s for _, s in ranked[:TOP_N]]

print(f"universe: top {TOP_N} by median turnover")
print(f"round-trip cost held constant at {COST}%")
print()
print(f"{'hold':<10}{'trades':>9}{'avg move':>11}{'median':>10}"
      f"{'net of cost':>13}{'cost as % of move':>20}")
print("-" * 73)

for horizon in (0, 1, 2, 3, 5, 10, 20, 60):
    moves = []
    for symbol in universe:
        days = data[symbol]
        for i in range(len(days) - horizon - 1):
            entry = days[i][1] if horizon == 0 else days[i][4]
            exit_price = days[i][4] if horizon == 0 else days[i + horizon][4]
            if entry <= 0:
                continue
            moves.append((exit_price - entry) / entry * 100.0)
    if not moves:
        continue
    average = statistics.fmean(moves)
    label = "intraday" if horizon == 0 else f"{horizon} day" + ("s" if horizon > 1 else "")
    share = abs(COST / average * 100.0) if average else float("inf")
    print(f"{label:<10}{len(moves):>9,}{average:>10.3f}%{statistics.median(moves):>9.3f}%"
          f"{average - COST:>12.3f}%{share:>18.0f}%")

print()
print("Same, but measuring the AVERAGE FAVOURABLE EXCURSION -- the best exit")
print("available inside the window, which is what a target is aiming at.")
print()
print(f"{'hold':<10}{'avg best exit':>15}{'net of cost':>14}")
print("-" * 41)
for horizon in (1, 2, 3, 5, 10, 20):
    best = []
    for symbol in universe:
        days = data[symbol]
        for i in range(len(days) - horizon - 1):
            entry = days[i][4]
            if entry <= 0:
                continue
            peak = max(days[i + k][2] for k in range(1, horizon + 1))
            best.append((peak - entry) / entry * 100.0)
    if best:
        average = statistics.fmean(best)
        print(f"{horizon:<10}{average:>14.3f}%{average - COST:>13.3f}%")
