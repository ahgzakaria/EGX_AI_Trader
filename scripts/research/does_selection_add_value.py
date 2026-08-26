"""Does the strategy's entry date carry information, or is it a costly sampler?

Every throttle tested so far -- the trailing stop, the candle gate -- turned out
to reduce losses by reducing exposure rather than by choosing better. That
raises a question no amount of parameter work can answer: does the strategy pick
*when* to be long any better than a coin would?

The test isolates date selection and nothing else. For every trade taken, it
compares:

    strategy : close[entry_bar] -> close[exit_bar]
    control  : close[random] -> close[random + bars], same symbol, same length

`holding_days` is CALENDAR days (`backtesting/trade.py` uses
`(exit - entry).days`), so indexing forward by it measures a span 49% longer
than the trade ran and manufactures a drift that was never captured. The bar
count is taken from the two dates' positions in the series instead.

Same instrument, same holding length, same era. The exit rule, the limit entry
and the position sizing are all stripped out of both sides, so what remains is
the choice of day. Costs are identical on both sides and cancel, so the
comparison is gross.

If the strategy has an edge in timing, its trades beat their own controls. If
they do not, the entry model is noise and the machinery around it is expensive
sampling.

Read-only. Reads a run directory and the daily cache.
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "market_data_cache.sqlite"


def load_series():
    """{symbol: [(date, close)]} from the provider the backtest actually reads."""
    conn = sqlite3.connect(f"file:{CACHE}?mode=ro", uri=True)
    series = defaultdict(list)
    for symbol, timestamp, close in conn.execute(
        "select symbol, timestamp, close from market_data_candles "
        "where provider = 'yahoo' and interval = '1d' and close > 0 "
        "order by symbol, timestamp"
    ):
        series[symbol].append((timestamp[:10], float(close)))
    conn.close()
    return series


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--draws", type=int, default=200,
                    help="random entry dates sampled per trade")
    ap.add_argument("--window", type=int, default=250,
                    help="trading days either side of the real entry to sample from")
    ap.add_argument("--seed", type=int, default=20260826)
    args = ap.parse_args()

    random.seed(args.seed)
    series = load_series()
    index = {symbol: {date: position for position, (date, _c) in enumerate(rows)}
             for symbol, rows in series.items()}

    paired, skipped = [], defaultdict(int)
    with (args.run_dir / "backtest_results.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            symbol = row["symbol"]
            rows = series.get(symbol)
            if not rows:
                skipped["no_series"] += 1
                continue
            start = index[symbol].get(row["entry_date"])
            if start is None:
                skipped["entry_not_in_series"] += 1
                continue
            end = index[symbol].get(row["exit_date"])
            if end is None or end <= start:
                skipped["exit_not_in_series"] += 1
                continue
            held = end - start          # trading bars, never holding_days
            if start + held >= len(rows):
                skipped["runs_off_the_end"] += 1
                continue

            actual = 100.0 * (rows[end][1] / rows[start][1] - 1.0)

            low = max(0, start - args.window)
            high = min(len(rows) - held - 1, start + args.window)
            if high <= low:
                skipped["window_too_small"] += 1
                continue
            draws = []
            for _ in range(args.draws):
                pick = random.randint(low, high)
                draws.append(100.0 * (rows[pick + held][1] / rows[pick][1] - 1.0))
            paired.append((actual, statistics.mean(draws), held, symbol))

    if not paired:
        raise SystemExit(f"nothing comparable: {dict(skipped)}")

    actuals = [p[0] for p in paired]
    controls = [p[1] for p in paired]
    edges = [a - c for a, c in zip(actuals, controls)]
    mean_edge = statistics.mean(edges)
    stderr = statistics.stdev(edges) / math.sqrt(len(edges))
    t = mean_edge / stderr if stderr else 0.0

    print(f"trades compared: {len(paired):,}"
          + (f"   skipped: {dict(skipped)}" if skipped else ""))
    print(f"median holding length: {statistics.median([p[2] for p in paired]):.0f} sessions")
    print()
    print(f"  strategy entry, close to close : {statistics.mean(actuals):+.4f}%")
    print(f"  random entry, same symbol/length: {statistics.mean(controls):+.4f}%")
    print(f"  edge from choosing the day      : {mean_edge:+.4f}%")
    print(f"  t = {t:+.2f}   beat their control on {100.0 * sum(1 for e in edges if e > 0) / len(edges):.1f}% of trades")
    print()
    verdict = ("the entry date carries information" if t > 2
               else "the entry date carries no information" if abs(t) <= 2
               else "the entry date is actively bad")
    print(f"  verdict: {verdict}")

    by_year = defaultdict(list)
    with (args.run_dir / "backtest_results.csv").open(encoding="utf-8-sig") as fh:
        for row, (actual, control, _h, _s) in zip(csv.DictReader(fh), paired):
            by_year[row["entry_date"][:4]].append(actual - control)
    print(f"\n  {'year':>6}{'n':>6}{'edge':>10}")
    for year in sorted(by_year):
        values = by_year[year]
        print(f"  {year:>6}{len(values):>6}{statistics.mean(values):>+9.3f}%")


if __name__ == "__main__":
    main()
