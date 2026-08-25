"""Compare two backtest runs trade-for-trade.

Written to measure what removing candle confirmation did, but it is generic:
point it at a saved `backtest_results.csv` and the current one.

Read-only. Prints; writes nothing.
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import Counter
from pathlib import Path


def load(path: Path):
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def number(row, key, default=0.0):
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return default


def summarise(rows, label: str) -> dict:
    profits = [number(r, "profit_percent") for r in rows if r.get("profit_percent")]
    scores = [number(r, "score") for r in rows if r.get("score")]
    holds = [number(r, "holding_days") for r in rows if r.get("holding_days")]
    wins = sum(1 for r in rows if r.get("result") == "WIN")
    portfolio = sum(number(r, "portfolio_profit") for r in rows)
    return {
        "label": label,
        "trades": len(rows),
        "wins": wins,
        "win_rate": 100.0 * wins / len(rows) if rows else 0.0,
        "avg_profit_pct": statistics.mean(profits) if profits else 0.0,
        "median_profit_pct": statistics.median(profits) if profits else 0.0,
        "portfolio_profit": portfolio,
        "avg_score": statistics.mean(scores) if scores else 0.0,
        "avg_hold": statistics.mean(holds) if holds else 0.0,
        "symbols": len({r.get("symbol") for r in rows}),
    }


def key(row):
    return (row.get("symbol"), row.get("entry_date"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    args = ap.parse_args()

    before, after = load(args.before), load(args.after)
    a, b = summarise(before, "before"), summarise(after, "after")

    print(f"{'metric':22}{'before':>14}{'after':>14}{'delta':>14}")
    for field, fmt in (
        ("trades", "{:,.0f}"), ("symbols", "{:,.0f}"), ("win_rate", "{:.2f}%"),
        ("avg_profit_pct", "{:+.3f}%"), ("median_profit_pct", "{:+.3f}%"),
        ("portfolio_profit", "{:,.0f}"), ("avg_score", "{:.2f}"),
        ("avg_hold", "{:.2f}"),
    ):
        delta = b[field] - a[field]
        print(f"{field:22}{fmt.format(a[field]):>14}{fmt.format(b[field]):>14}"
              f"{fmt.format(delta):>14}")

    before_keys = {key(r) for r in before}
    after_keys = {key(r) for r in after}
    kept = before_keys & after_keys
    print(f"\ntrades in both runs: {len(kept):,}")
    print(f"  only before (lost): {len(before_keys - kept):,}")
    print(f"  only after  (new):  {len(after_keys - kept):,}")

    lost = [r for r in before if key(r) in before_keys - kept]
    gained = [r for r in after if key(r) in after_keys - kept]
    for rows, name in ((lost, "lost"), (gained, "new")):
        if not rows:
            continue
        profits = [number(r, "profit_percent") for r in rows if r.get("profit_percent")]
        wins = sum(1 for r in rows if r.get("result") == "WIN")
        print(f"  {name:6} trades: win rate {100.0 * wins / len(rows):5.1f}%  "
              f"avg {statistics.mean(profits):+.3f}%" if profits else f"  {name}: n/a")

    if lost:
        print("\n  reasons cited by lost trades, most common first:")
        cited = Counter()
        for r in lost:
            for reason in (r.get("reasons") or "").split("|"):
                reason = reason.strip()
                if reason:
                    cited[reason] += 1
        for reason, n in cited.most_common(8):
            print(f"    {reason:44}{n:>5}")


if __name__ == "__main__":
    main()
