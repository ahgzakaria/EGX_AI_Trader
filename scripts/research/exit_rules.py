"""Which exit rule survives EGX costs? Corrected.

Two corrections over the first attempt, both of which changed the answer:

1. One bad row (ZMID 2022-10-04, high and close printed as 1,000,000 against a
   6.81 open) produced a 14.7-million-percent gain and single-handedly made
   "hold to close" average +129% per trade. Rows are now rejected when the
   day's range exceeds what the exchange's own price limit permits: EGX caps a
   session at +/-20% from the reference, so an intraday range above 25% is not
   a move, it is a bad print.

2. Assuming the stop is always taken before the target made every target rule
   look terrible by construction. On a stock that ranges 3.5% in a day, a
   -1.5% stop and a +1% target are usually both touched, and always resolving
   that against the strategy is not a conservative estimate, it is a different
   simulation. Both bounds are now reported. The truth is between them, and
   the gap between them is itself the finding: where it is wide, daily bars
   cannot answer the question and minute data is required.

Remaining limits, unchanged: entry at the open stands in for "a trigger
fired", so this compares exit rules holding entry constant. It is not a
backtest of the ORB engine.
"""
import csv
import glob
import os
import statistics
from collections import defaultdict

COST = 0.80
STOP = 1.5
TOP_N = 40
MIN_DAYS = 400
MAX_PLAUSIBLE_RANGE = 25.0      # EGX price limit is +/-20%

data = defaultdict(list)
rejected = 0
for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
    symbol = os.path.basename(path)[:-4]
    with open(path, encoding="utf-8") as handle:
        for r in csv.DictReader(handle):
            try:
                o, h, l, c = (float(r["open"]), float(r["high"]),
                              float(r["low"]), float(r["close"]))
                v = float(r["volume"] or 0)
            except (TypeError, ValueError, KeyError):
                continue
            if min(o, h, l, c) <= 0 or v <= 0 or not (l <= o <= h and l <= c <= h):
                rejected += 1
                continue
            if (h - l) / o * 100.0 > MAX_PLAUSIBLE_RANGE:
                rejected += 1
                continue
            data[symbol].append((r["date"], o, h, l, c, v))

ranked = []
for symbol, days in data.items():
    if len(days) < MIN_DAYS:
        continue
    recent = days[-250:]
    turnovers = sorted(d[4] * d[5] for d in recent)
    ranked.append((turnovers[len(turnovers) // 2], symbol))
ranked.sort(reverse=True)
universe = [s for _, s in ranked[:TOP_N]]

print(f"rejected {rejected:,} implausible rows")
print(f"universe: top {len(universe)} by median daily turnover, "
      f"{sum(len(data[s]) for s in universe):,} stock-days")
print(f"stop {STOP}%, round-trip cost {COST}%")
print()


def atr_percent(days, index, window=14):
    if index < window + 1:
        return None
    trs = []
    for i in range(index - window, index):
        _, o, h, l, c, _ = days[i]
        previous_close = days[i - 1][4]
        trs.append(max(h - l, abs(h - previous_close), abs(l - previous_close))
                   / previous_close * 100.0)
    return statistics.fmean(trs)


def simulate(target_for, stop_first, gap_min=None):
    results = []
    for symbol in universe:
        days = data[symbol]
        for i in range(15, len(days)):
            _, o, h, l, c, _ = days[i]
            if gap_min is not None:
                gap = (o - days[i - 1][4]) / days[i - 1][4] * 100.0
                if gap < gap_min:
                    continue
            target = target_for(days, i, o)
            if target is None:
                continue
            stop_price = o * (1 - STOP / 100.0)
            hit_stop = l <= stop_price
            hit_target = target is not False and h >= target

            if hit_stop and hit_target:
                gross = -STOP if stop_first else (target - o) / o * 100.0
            elif hit_stop:
                gross = -STOP
            elif hit_target:
                gross = (target - o) / o * 100.0
            else:
                gross = (c - o) / o * 100.0
            results.append(gross - COST)
    return results


RULES = [
    ("engine-like: fixed +0.86%", lambda d, i, o: o * 1.0086),
    ("fixed +1.5%", lambda d, i, o: o * 1.015),
    ("fixed +3.0%", lambda d, i, o: o * 1.030),
    ("hold to close (no target)", lambda d, i, o: False),
    ("target = 1 x ATR(14)",
     lambda d, i, o: (lambda a: None if a is None else o * (1 + a / 100.0))(atr_percent(d, i))),
    ("target = 2 x ATR(14)",
     lambda d, i, o: (lambda a: None if a is None else o * (1 + 2 * a / 100.0))(atr_percent(d, i))),
]


def run(header, gap_min=None):
    print(header)
    print(f"{'exit rule':<30}{'trades':>8}{'net if stop first':>19}{'net if target first':>21}")
    print("-" * 78)
    for name, rule in RULES:
        pessimistic = simulate(rule, stop_first=True, gap_min=gap_min)
        optimistic = simulate(rule, stop_first=False, gap_min=gap_min)
        if not pessimistic:
            continue
        print(f"{name:<30}{len(pessimistic):>8,}"
              f"{statistics.fmean(pessimistic):>18.3f}%"
              f"{statistics.fmean(optimistic):>20.3f}%")
    print()


run("ALL DAYS")
run("ONLY DAYS THAT GAPPED UP MORE THAN 1% AT THE OPEN", gap_min=1.0)
run("ONLY DAYS THAT GAPPED UP MORE THAN 2% AT THE OPEN", gap_min=2.0)
