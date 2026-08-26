"""Measure each symbol's quoted spread once, so the cost model can charge it.

`TradingCosts` charges every symbol the same 0.500%, the trade-weighted median
of the traded universe. Real spreads run from 0.04% on COMI to over 1% on the
thin names, so the flat rate is simultaneously too harsh on the liquid ones and
too generous on the illiquid ones. No universe filter can be evaluated while
every symbol costs the same to trade.

Written as a table rather than computed per run: it takes a full pass over 19M
quote rows, it is a property of the instrument rather than of a backtest, and a
committed file can be reviewed and dated where a recomputation cannot.

The measurement window is what the quote history covers, currently 2026-08-02
onward. Applying it to trades from 2017 assumes a symbol's spread is a stable
characteristic. That is an assumption, it is stated in the file's own header,
and it is a far better one than charging every symbol the median.

Read-only over the market DB; writes data/universe/egx_spread_table.csv.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKET_DB = ROOT / "data" / "rubix_live_market.db"
OUT = ROOT / "data" / "universe" / "egx_spread_table.csv"

SESSION_START = "07:00"   # 10:00 Cairo
SESSION_END = "11:30"     # 14:30 Cairo


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2026-08-02",
                    help="first session to measure; the dense-capture era begins here")
    ap.add_argument("--min-quotes", type=int, default=50,
                    help="below this a symbol is left out rather than guessed at")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    samples = defaultdict(list)
    for ticker, bid, ask in conn.execute(
        "select ticker, bid, ask from quotes "
        "where substr(market_timestamp,1,10) >= ? "
        "and substr(market_timestamp,12,5) between ? and ? "
        "and bid > 0 and ask > 0 and ask >= bid",
        (args.from_date, SESSION_START, SESSION_END),
    ):
        samples[ticker].append(200.0 * (ask - bid) / (ask + bid))
    last_session = conn.execute(
        "select max(substr(market_timestamp,1,10)) from quotes"
    ).fetchone()[0]
    conn.close()

    rows = []
    for ticker, values in samples.items():
        if len(values) < args.min_quotes:
            continue
        values.sort()
        rows.append({
            "ticker": ticker,
            "median_spread_percent": round(statistics.median(values), 4),
            "p25_spread_percent": round(values[len(values) // 4], 4),
            "p75_spread_percent": round(values[3 * len(values) // 4], 4),
            "quotes": len(values),
        })
    rows.sort(key=lambda r: r["median_spread_percent"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        fh.write(
            f"# EGX quoted spreads, median of bid/ask over the session.\n"
            f"# measured_from={args.from_date} measured_to={last_session} "
            f"generated={datetime.now(timezone.utc).date().isoformat()}\n"
            f"# Symbols with fewer than {args.min_quotes} quotes are omitted rather\n"
            f"# than estimated; a caller finding no row must fall back, not guess.\n"
            f"# Applying these to older trades assumes spread is a stable property\n"
            f"# of the symbol. It is an assumption, and a better one than a flat rate.\n"
        )
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    spreads = [r["median_spread_percent"] for r in rows]
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(rows)} symbols)")
    print(f"  window     : {args.from_date} .. {last_session}")
    print(f"  median     : {statistics.median(spreads):.3f}%")
    print(f"  tightest   : {rows[0]['ticker']} {rows[0]['median_spread_percent']:.3f}%")
    print(f"  widest     : {rows[-1]['ticker']} {rows[-1]['median_spread_percent']:.3f}%")
    for threshold in (0.2, 0.3, 0.5, 1.0):
        n = sum(1 for s in spreads if s <= threshold)
        print(f"  <= {threshold:.1f}%    : {n:>3} symbols ({100.0 * n / len(rows):.0f}%)")


if __name__ == "__main__":
    main()
