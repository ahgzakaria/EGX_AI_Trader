"""Measure the EGX overnight gap: is the move that happens while the market is
closed capturable, and does it survive costs?

Read-only. Reads data/frozen_eodhd_seed (official daily bars, corporate-action
adjusted) and writes reports/overnight_gap_*.csv. Touches no strategy.

The daily return of a share splits cleanly in two:

    close[t]/close[t-1] - 1  =  (open[t]/close[t-1] - 1)  +  (close[t]/open[t] - 1)
                                 ------- gap -------          ----- intraday -----

The gap is only reachable by holding a position across the close. The intraday
leg is the only part a same-session strategy can touch. This probe measures the
size, the persistence and the cost-sensitivity of each leg.
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "frozen_eodhd_seed"
REPORTS = ROOT / "reports"


def load_symbol(path: Path, start: str):
    """Adjusted (open, close, turnover) per date, corporate-action consistent."""
    out = []
    for r in csv.DictReader(path.open(encoding="utf-8")):
        d = r["date"]
        if d < start:
            continue
        try:
            o, c = float(r["open"]), float(r["close"])
            ac, v = float(r["adjusted_close"]), float(r["volume"] or 0)
        except (TypeError, ValueError):
            continue
        if min(o, c, ac) <= 0:
            continue
        ratio = ac / c                      # same factor applies to the open
        out.append((d, o * ratio, ac, v * c))
    return out


def minute_mode(args, friction_broker: float) -> None:
    """Measure the gap from real traded minutes instead of the EOD open field.

    Buy the last traded minute of session T, sell the first traded minute of
    session T+1 (and, for the settlement-constrained variant, of T+2). Spreads
    are the per-symbol per-session median from live quotes, charged half on each
    side, so the trade crosses the book in both directions.
    """
    import sqlite3

    db = ROOT / "data" / "rubix_live_market.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    edges: dict[tuple[str, str], tuple[float, float]] = {}
    for tk, day, first_open, last_close, n in conn.execute(
        "select ticker, substr(minute,1,10) d, "
        "  min(case when rn_asc = 1 then open end), "
        "  min(case when rn_desc = 1 then close end), count(*) "
        "from (select ticker, minute, open, close, "
        "        row_number() over (partition by ticker, substr(minute,1,10) "
        "                           order by minute) rn_asc, "
        "        row_number() over (partition by ticker, substr(minute,1,10) "
        "                           order by minute desc) rn_desc "
        "      from candles_1m "
        "      where substr(minute,12,5) between '07:00' and '11:30' "
        "        and open > 0 and close > 0) "
        "group by ticker, d"
    ):
        if n >= 160 and first_open and last_close:
            edges[(tk, day)] = (first_open, last_close)

    spread: dict[tuple[str, str], float] = {}
    raw = defaultdict(list)
    for tk, ts, b, a in conn.execute(
        "select ticker, market_timestamp, bid, ask from quotes "
        "where substr(market_timestamp,12,5) between '07:00' and '11:30' "
        "and bid > 0 and ask > 0 and ask >= bid"
    ):
        raw[(tk, ts[:10])].append(100.0 * (a - b) / (a + b))
    for k, v in raw.items():
        if len(v) >= 20:
            spread[k] = statistics.median(v)
    conn.close()

    days = sorted({d for _tk, d in edges})
    dense = [d for d in days
             if sum(1 for (_t, dd) in edges if dd == d) >= 50]
    print(f"dense sessions: {len(dense)}  {dense[0]} -> {dense[-1]}")

    rows_out = []
    legs = {"gap_t1": [], "intraday": [], "hold_t2": [], "gap_net": [], "t2_net": []}
    for i, d0 in enumerate(dense[:-1]):
        d1 = dense[i + 1]
        d2 = dense[i + 2] if i + 2 < len(dense) else None
        for tk in {t for (t, dd) in edges if dd == d0}:
            a, b = edges.get((tk, d0)), edges.get((tk, d1))
            sp = spread.get((tk, d0))
            if not a or not b or sp is None:
                continue
            close0, open1, close1 = a[1], b[0], b[1]
            gap = 100.0 * (open1 - close0) / close0
            intra = 100.0 * (close1 - open1) / open1
            cost = friction_broker + sp          # half spread each side = one sp
            legs["gap_t1"].append(gap)
            legs["intraday"].append(intra)
            legs["gap_net"].append(gap - cost)
            row = {"ticker": tk, "from": d0, "to": d1, "gap_pct": gap,
                   "next_intraday_pct": intra, "spread_pct": sp,
                   "gap_net_pct": gap - cost, "t2_net_pct": None}
            if d2 and (tk, d2) in edges:
                open2 = edges[(tk, d2)][0]
                held = 100.0 * (open2 - close0) / close0
                legs["hold_t2"].append(held)
                legs["t2_net"].append(held - cost)
                row["t2_net_pct"] = held - cost
            rows_out.append(row)

    def show(name, vals, cost=None):
        if not vals:
            return
        s = sorted(vals)
        se = statistics.stdev(vals) / len(vals) ** 0.5
        print(f"  {name:26} n {len(vals):>6,}  mean {statistics.mean(vals):+.4f}%  "
              f"median {statistics.median(vals):+.4f}%  "
              f"pos {100.0 * sum(1 for x in vals if x > 0) / len(vals):>5.1f}%  "
              f"t {statistics.mean(vals) / se:+.1f}  "
              f"p10 {s[len(s) // 10]:+.2f}%  p90 {s[9 * len(s) // 10]:+.2f}%")

    print("\nlegs of the move, from real traded minutes:")
    show("overnight gap (T->T+1)", legs["gap_t1"])
    show("next session intraday", legs["intraday"])
    show("hold close T -> open T+2", legs["hold_t2"])
    print("\nafter costs:")
    show("gap, sell at open T+1", legs["gap_net"])
    show("gap, sell at open T+2", legs["t2_net"])

    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / "overnight_gap_minute_legs.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)

    # Friction is a fixed toll per round trip; the gap accrues once per session.
    # So the toll is divided by however many sessions the position is held.
    print("\nhold from the close of T to the open of T+N, one round trip of cost:")
    print(f"  {'N':>3}{'n':>7}{'raw mean':>11}{'raw med':>10}"
          f"{'net mean':>11}{'net med':>10}{'net>0':>8}")
    for horizon in range(1, 9):
        vals = []
        for i, d0 in enumerate(dense):
            if i + horizon >= len(dense):
                break
            dn = dense[i + horizon]
            for tk in {t for (t, dd) in edges if dd == d0}:
                if (tk, dn) not in edges:
                    continue
                sp = spread.get((tk, d0))
                if sp is None:
                    continue
                close0 = edges[(tk, d0)][1]
                r = 100.0 * (edges[(tk, dn)][0] - close0) / close0
                vals.append((r, r - friction_broker - sp))
        if not vals:
            continue
        raws = [x[0] for x in vals]
        nets = [x[1] for x in vals]
        print(f"  {horizon:>3}{len(vals):>7}{statistics.mean(raws):>+10.3f}%"
              f"{statistics.median(raws):>+9.3f}%{statistics.mean(nets):>+10.3f}%"
              f"{statistics.median(nets):>+9.3f}%"
              f"{100.0 * sum(1 for x in nets if x > 0) / len(nets):>7.1f}%")
    print("  (windows overlap, so these are not independent observations)")

    per = defaultdict(list)
    for r in rows_out:
        per[r["ticker"]].append(r["gap_net_pct"])
    ranked = sorted(((t, len(v), statistics.mean(v)) for t, v in per.items()
                     if len(v) >= 10), key=lambda x: -x[2])
    print(f"\nsymbols with >=10 transitions: {len(ranked)}; "
          f"net-positive gap capture for {sum(1 for r in ranked if r[2] > 0)}")
    for t, n, m in ranked[:8]:
        print(f"  {t:8}{n:>4}{m:>+9.3f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("daily", "minute"), default="daily")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--min-turnover", type=float, default=1_000_000.0,
                    help="previous-session turnover required to be tradable")
    ap.add_argument("--cost-per-side", type=float, default=0.1819,
                    help="broker schedule, percent per side")
    ap.add_argument("--spread-pct", type=float, default=0.40,
                    help="round-trip spread cost, percent")
    args = ap.parse_args()

    friction = 2 * args.cost_per_side + args.spread_pct
    if args.source == "minute":
        minute_mode(args, 2 * args.cost_per_side)
        return

    gaps = defaultdict(list)       # date -> [gap%]
    intras = defaultdict(list)     # date -> [intraday%]
    per_symbol = defaultdict(lambda: [[], []])
    flat_open = total = 0

    for f in sorted(SEED.glob("*.csv")):
        rows = load_symbol(f, args.start)
        for (d0, _o0, c0, t0), (d1, o1, c1, _t1) in zip(rows, rows[1:]):
            if t0 < args.min_turnover:      # liquidity known at the decision point
                continue
            gap = 100.0 * (o1 - c0) / c0
            intra = 100.0 * (c1 - o1) / o1
            if abs(gap) > 25 or abs(intra) > 25:   # limit-move / bad print guard
                continue
            total += 1
            if abs(gap) < 1e-9:
                flat_open += 1
            gaps[d1].append(gap)
            intras[d1].append(intra)
            per_symbol[f.stem][0].append(gap)
            per_symbol[f.stem][1].append(intra)

    if not total:
        raise SystemExit("no symbol-sessions passed the filters")

    all_gap = [g for v in gaps.values() for g in v]
    all_intra = [g for v in intras.values() for g in v]
    print(f"symbol-sessions: {total:,}  from {min(gaps)} to {max(gaps)}")
    print(f"opens exactly equal to the previous close: "
          f"{100.0 * flat_open / total:.1f}%\n")

    def describe(name, vals):
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        print(f"{name:12} mean {statistics.mean(vals):+.4f}%  "
              f"median {statistics.median(vals):+.4f}%  "
              f"sd {statistics.stdev(vals):.3f}%  "
              f"p10 {vals_sorted[n // 10]:+.2f}%  p90 {vals_sorted[9 * n // 10]:+.2f}%  "
              f"positive {100.0 * sum(1 for x in vals if x > 0) / n:.1f}%")

    describe("gap", all_gap)
    describe("intraday", all_intra)
    print(f"{'sum':12} mean {statistics.mean(all_gap) + statistics.mean(all_intra):+.4f}%")

    se = statistics.stdev(all_gap) / len(all_gap) ** 0.5
    print(f"\ngap mean t-stat: {statistics.mean(all_gap) / se:+.1f}")
    print(f"friction assumed: {friction:.3f}% round trip "
          f"({2 * args.cost_per_side:.3f}% broker + {args.spread_pct:.2f}% spread)")
    print(f"gap net of friction: {statistics.mean(all_gap) - friction:+.4f}% "
          "per symbol-session held overnight")

    print("\nby year:")
    print(f"  {'year':6}{'sessions':>10}{'gap mean':>11}{'intraday':>11}"
          f"{'gap>0':>9}{'gap net':>10}")
    by_year = defaultdict(lambda: [[], []])
    for d, v in gaps.items():
        by_year[d[:4]][0].extend(v)
    for d, v in intras.items():
        by_year[d[:4]][1].extend(v)
    for y in sorted(by_year):
        g, i = by_year[y]
        pos = 100.0 * sum(1 for x in g if x > 0) / len(g)
        print(f"  {y:6}{len(g):>10,}{statistics.mean(g):>+10.4f}%"
              f"{statistics.mean(i):>+10.4f}%{pos:>8.1f}%"
              f"{statistics.mean(g) - friction:>+9.4f}%")

    REPORTS.mkdir(exist_ok=True)
    with (REPORTS / "overnight_gap_by_symbol.csv").open(
            "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "sessions", "gap_mean_pct", "gap_median_pct",
                    "gap_positive_pct", "intraday_mean_pct", "gap_net_of_friction_pct"])
        rows = []
        for tk, (g, i) in per_symbol.items():
            if len(g) < 100:
                continue
            rows.append([tk, len(g), statistics.mean(g), statistics.median(g),
                         100.0 * sum(1 for x in g if x > 0) / len(g),
                         statistics.mean(i), statistics.mean(g) - friction])
        rows.sort(key=lambda r: -r[2])
        w.writerows(rows)
    print(f"\nsymbols with >=100 sessions: {len(rows)}; "
          f"gap mean positive for {sum(1 for r in rows if r[2] > 0)}; "
          f"beats friction for {sum(1 for r in rows if r[6] > 0)}")
    print(f"{'sym':8}{'n':>7}{'gap':>10}{'intraday':>11}{'net':>10}")
    for r in rows[:10]:
        print(f"{r[0]:8}{r[1]:>7}{r[2]:>+9.3f}%{r[5]:>+10.3f}%{r[6]:>+9.3f}%")


if __name__ == "__main__":
    main()
