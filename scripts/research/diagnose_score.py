"""Find out why a higher score means a worse trade.

Net profit by score decile, measured on every run so far, slopes downward:
+1.015% at 50 falling to -0.575% at 90 on the sealed baseline. A ranking system
that ranks backwards is a larger defect than any exit parameter, so this asks
which part of it is responsible.

`score` is the sum of seven independent evaluators. Five are recorded per trade
-- trend, volume, momentum, candle and breakout -- and the support and entry
contributions are not, so they are measured here only through the residual.

Read-only. Reads a run directory produced by `isolated_backtest.py` and prints.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

COMPONENTS = (
    "trend_score", "volume_score", "momentum_score",
    "candle_score", "breakout_score",
)

#: Model inputs recorded alongside, worth the same question.
INDICATORS = (
    "rr", "rsi", "adx", "atr_percent", "volume_ratio", "bb_position",
    "ema20_dist", "ema50_dist", "ema200_dist", "dist_high20", "dist_low20",
    "rsi7", "bb_width", "macd_cross_age",
)


def load(path: Path):
    rows = []
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                row["_profit"] = float(row["profit_percent"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append(row)
    return rows


def number(row, key):
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def correlation(pairs):
    """Pearson r and a t statistic, or None when there is nothing to correlate."""
    if len(pairs) < 10:
        return None, None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    if len(set(xs)) < 2:
        return None, None
    r = statistics.correlation(xs, ys)
    n = len(pairs)
    if abs(r) >= 1.0:
        return r, None
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return r, t


def buckets(rows, key, count=5):
    """Split on the field's own quantiles, so每 bucket carries real trades."""
    valued = [(number(r, key), r["_profit"]) for r in rows]
    valued = [(v, p) for v, p in valued if v is not None]
    if len(valued) < count * 5:
        return []
    valued.sort(key=lambda pair: pair[0])
    size = len(valued) // count
    out = []
    for index in range(count):
        start = index * size
        end = len(valued) if index == count - 1 else (index + 1) * size
        chunk = valued[start:end]
        if not chunk:
            continue
        profits = [p for _v, p in chunk]
        out.append({
            "range": (chunk[0][0], chunk[-1][0]),
            "n": len(chunk),
            "avg": statistics.mean(profits),
            "win": 100.0 * sum(1 for p in profits if p > 0) / len(profits),
        })
    return out


def report(rows, key, label=None):
    pairs = [(number(r, key), r["_profit"]) for r in rows]
    pairs = [(v, p) for v, p in pairs if v is not None]
    r, t = correlation(pairs)
    rows_out = buckets(rows, key)
    if not rows_out:
        return None
    lowest, highest = rows_out[0]["avg"], rows_out[-1]["avg"]
    verdict = "predictive" if r and r > 0.08 else (
        "INVERTED" if r and r < -0.08 else "no signal")
    print(f"\n{label or key:18} r={r:+.3f}" if r is not None
          else f"\n{label or key:18} r=n/a", end="")
    if t is not None:
        print(f"  t={t:+.2f}", end="")
    print(f"  {verdict}   top-minus-bottom {highest - lowest:+.3f}%")
    for bucket in rows_out:
        low, high = bucket["range"]
        print(f"    {low:>8.2f}..{high:<8.2f} n={bucket['n']:>4} "
              f"avg {bucket['avg']:>+7.3f}%  win {bucket['win']:>5.1f}%")
    return {"key": key, "r": r, "t": t, "spread": highest - lowest}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args()

    rows = load(args.run_dir / "backtest_results.csv")
    profits = [r["_profit"] for r in rows]
    print(f"{len(rows)} trades   avg {statistics.mean(profits):+.4f}%   "
          f"median {statistics.median(profits):+.4f}%")

    print("\n" + "=" * 66)
    print("THE TOTAL")
    print("=" * 66)
    findings = [report(rows, "score", "score (total)")]

    print("\n" + "=" * 66)
    print("ITS COMPONENTS")
    print("=" * 66)
    for key in COMPONENTS:
        findings.append(report(rows, key))

    # What the score does not see: the part of the total not explained by the
    # five recorded components. Support and entry live in here.
    for row in rows:
        parts = [number(row, key) for key in COMPONENTS]
        total = number(row, "score")
        if total is not None and all(p is not None for p in parts):
            row["unrecorded"] = total - sum(parts)
    findings.append(report(
        [r for r in rows if "unrecorded" in r], "unrecorded",
        "unrecorded (support+entry)"))

    print("\n" + "=" * 66)
    print("INDICATORS RECORDED ALONGSIDE")
    print("=" * 66)
    for key in INDICATORS:
        findings.append(report(rows, key))

    print("\n" + "=" * 66)
    print("RANKED BY SIGNAL STRENGTH")
    print("=" * 66)
    usable = [f for f in findings if f and f["r"] is not None]
    usable.sort(key=lambda f: f["r"])
    print(f"  {'field':28}{'r':>8}{'t':>8}{'verdict':>12}")
    for finding in usable:
        verdict = ("INVERTED" if finding["r"] < -0.08 else
                   "predictive" if finding["r"] > 0.08 else "noise")
        t = f"{finding['t']:+.2f}" if finding["t"] is not None else "n/a"
        print(f"  {finding['key']:28}{finding['r']:>+8.3f}{t:>8}{verdict:>12}")


if __name__ == "__main__":
    main()
