"""How often does an EGX stock-day even move far enough to pay for a round trip?

Round-trip cost, from the broker contract note plus the measured spread, is
about 0.80% of price. A strategy that enters and exits inside one session has
to clear that before it has made anything at all.
"""
import csv
import glob
import os
from collections import defaultdict

COST = 0.80

per_symbol = defaultdict(list)
rows = 0
for path in glob.glob("data/frozen_eodhd_seed/*.csv"):
    symbol = os.path.basename(path)[:-4]
    with open(path, encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            try:
                o = float(record["open"]); h = float(record["high"])
                l = float(record["low"]); c = float(record["close"])
                v = float(record["volume"] or 0)
            except (TypeError, ValueError, KeyError):
                continue
            if o <= 0 or h <= 0 or v <= 0:
                continue
            rows += 1
            per_symbol[symbol].append({
                "range_pct": (h - l) / o * 100.0,
                "open_to_close_pct": (c - o) / o * 100.0,
                "open_to_high_pct": (h - o) / o * 100.0,
                "turnover": v * c,
                "close": c,
            })

print(f"{len(per_symbol)} symbols, {rows:,} stock-days")
print()

ranges = sorted(d["range_pct"] for v in per_symbol.values() for d in v)


def q(p):
    return ranges[int(len(ranges) * p)]


print("Daily range (high-low)/open across all EGX history:")
for p, label in ((.25, "p25"), (.50, "median"), (.75, "p75"), (.90, "p90"), (.95, "p95")):
    print(f"  {label:<8} {q(p):>6.2f}%")
print()

for threshold in (COST, 1.5, 2 * COST, 3.0):
    n = sum(1 for r in ranges if r > threshold)
    print(f"  days with range > {threshold:>4.2f}% : {n:>7,}  ({n / len(ranges) * 100:5.1f}%)")

print()
print("The range is the best case: it assumes buying the low and selling the high.")
print("What a directional entry actually captures is open-to-high, or less.")
print()
oh = sorted(d["open_to_high_pct"] for v in per_symbol.values() for d in v)
for threshold in (COST, 1.5, 2 * COST):
    n = sum(1 for r in oh if r > threshold)
    print(f"  days with open-to-high > {threshold:>4.2f}% : {n:>7,}  ({n / len(oh) * 100:5.1f}%)")

print()
print("=" * 66)
print("By liquidity: only names you could actually trade size in")
print("=" * 66)

# Rank symbols by median daily turnover; a scalper cannot trade the tail.
liquid = []
for symbol, days in per_symbol.items():
    recent = days[-250:]
    if len(recent) < 100:
        continue
    turnovers = sorted(d["turnover"] for d in recent)
    median_turnover = turnovers[len(turnovers) // 2]
    med_range = sorted(d["range_pct"] for d in recent)[len(recent) // 2]
    over = sum(1 for d in recent if d["range_pct"] > 2 * COST) / len(recent) * 100
    liquid.append((median_turnover, symbol, med_range, over, len(recent)))

liquid.sort(reverse=True)
print(f"{'symbol':<8}{'med turnover EGP':>18}{'med range':>11}{'days>1.6%':>11}")
print("-" * 48)
for turnover, symbol, med_range, over, n in liquid[:25]:
    print(f"{symbol:<8}{turnover:>18,.0f}{med_range:>10.2f}%{over:>10.0f}%")

print()
top = liquid[:30]
print(f"Top 30 by turnover: median daily range "
      f"{sorted(t[2] for t in top)[15]:.2f}%, "
      f"share of days clearing 1.6%: {sum(t[3] for t in top) / len(top):.0f}%")
