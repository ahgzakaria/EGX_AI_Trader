"""Measure the support/resistance range-scalp idea on real EGX intraday minutes.

Read-only research probe. It touches no strategy, no config and no live path:
it reads data/rubix_live_market.db and writes CSVs under reports/.

The idea under test: in a stock that oscillates between two levels, buy near the
lower level and sell near the upper one, repeatedly, inside a single session.

Rules held deliberately pessimistic, so a surviving edge is a real one:
  * The range comes from the opening window only; the trading window is disjoint
    from it, so no level is ever built from a bar the trade could not have seen.
  * A signal on minute t is filled at minute t+1's open, never at t's close.
  * Buys pay half the spread up, sells pay half the spread down.
  * A minute that touches both target and stop is scored as the stop.
  * Every fill pays the real broker schedule from config/settings.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKET_DB = ROOT / "data" / "rubix_live_market.db"
SETTINGS = ROOT / "config" / "settings.json"
REPORTS = ROOT / "reports"

SESSION_START = "07:00"   # 10:00 Cairo, UTC+3
SESSION_END = "11:30"     # 14:30 Cairo
SESSION_MINUTES = 270


def _fee_schedule() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8"))["scalping"]["fee_schedule"]


def fee_rate() -> float:
    """Per-side percentage cost from the broker contract note in settings."""
    sched = _fee_schedule()
    return sum(v for k, v in sched.items()
               if isinstance(v, (int, float)) and k.endswith("_percent")) / 100.0


def order_fee() -> float:
    return float(_fee_schedule().get("order_fee_egp", 0.0))


@dataclass
class Bar:
    minute: str
    o: float
    h: float
    l: float
    c: float
    v: float

    @property
    def hhmm(self) -> str:
        return self.minute[11:16]


def load(conn: sqlite3.Connection):
    """Return {(date, ticker): [Bar]} and {(date, ticker): median spread fraction}."""
    bars = defaultdict(list)
    for tk, mn, o, h, l, c, v in conn.execute(
        "select ticker, minute, open, high, low, close, volume from candles_1m "
        "where substr(minute,12,5) between ? and ? order by ticker, minute",
        (SESSION_START, SESSION_END),
    ):
        if not all(x and x > 0 for x in (o, h, l, c)):
            continue
        bars[(mn[:10], tk)].append(Bar(mn, o, h, l, c, v or 0.0))

    raw = defaultdict(list)
    for tk, ts, b, a in conn.execute(
        "select ticker, market_timestamp, bid, ask from quotes "
        "where substr(market_timestamp,12,5) between ? and ? "
        "and bid > 0 and ask > 0 and ask >= bid",
        (SESSION_START, SESSION_END),
    ):
        raw[(ts[:10], tk)].append(2.0 * (a - b) / (a + b))
    spreads = {k: statistics.median(v) for k, v in raw.items() if len(v) >= 20}
    return bars, spreads


def dense_sessions(bars, min_cover: float, min_symbols: int):
    per_day = defaultdict(list)
    for (day, _tk), rows in bars.items():
        per_day[day].append(len(rows))
    out = []
    for day, counts in per_day.items():
        ok = sum(1 for n in counts if n >= min_cover * SESSION_MINUTES)
        if ok >= min_symbols:
            out.append(day)
    return sorted(out)


@dataclass
class Params:
    open_minutes: int = 60
    min_width_pct: float = 1.5
    max_width_pct: float = 12.0
    min_mid_crosses: int = 2
    entry_zone: float = 0.20     # fraction of range width above support
    target_zone: float = 0.15    # fraction of range width below resistance
    stop_frac: float = 0.40      # stop distance below support, in range widths
    max_trades: int = 3
    notional: float = 20000.0
    exit_buffer: int = 5         # minutes before close, forced flat


def _plus(hhmm: str, minutes: int) -> str:
    total = int(hhmm[:2]) * 60 + int(hhmm[3:5]) + minutes
    return f"{total // 60:02d}:{total % 60:02d}"


def simulate(rows, spread: float, p: Params, fee: float, ofee: float):
    """One symbol, one session. Returns (trades, range_info) or (None, reason)."""
    if len(rows) < 0.6 * SESSION_MINUTES:
        return None, "SPARSE"
    cut = _plus(SESSION_START, p.open_minutes)
    opening = [b for b in rows if b.hhmm < cut]
    trading = [b for b in rows if b.hhmm >= cut]
    if len(opening) < 0.6 * p.open_minutes or len(trading) < 60:
        return None, "SPARSE_WINDOW"

    support = min(b.l for b in opening)
    resistance = max(b.h for b in opening)
    width = resistance - support
    width_pct = 100.0 * width / support
    if width_pct < p.min_width_pct:
        return None, "RANGE_TOO_NARROW"
    if width_pct > p.max_width_pct:
        return None, "RANGE_TOO_WIDE"

    mid = (support + resistance) / 2.0
    crosses, side = 0, None
    for b in opening:
        now = "up" if b.c > mid else "down"
        if side and now != side:
            crosses += 1
        side = now
    if crosses < p.min_mid_crosses:
        return None, "NOT_OSCILLATING"

    buy_zone = support + p.entry_zone * width
    target = resistance - p.target_zone * width
    stop = support - p.stop_frac * width
    half = spread / 2.0

    trades, pos, armed = [], None, False
    last = len(trading) - 1
    forced = last - p.exit_buffer
    for i, b in enumerate(trading):
        if pos is None:
            if len(trades) >= p.max_trades or i >= forced:
                continue
            if armed and i + 1 <= last:
                nxt = trading[i + 1]
                entry = nxt.o * (1 + half)
                if entry < target:      # never buy above the exit
                    pos = {"entry": entry, "i": i + 1, "minute": nxt.minute}
                armed = False
                continue
            if b.l <= buy_zone:
                armed = True
            continue

        hit_stop = b.l <= stop
        hit_target = b.h >= target
        if hit_stop:                    # ambiguous minutes resolve against us
            exit_px, why = stop * (1 - half), "STOP"
        elif hit_target:
            exit_px, why = target * (1 - half), "TARGET"
        elif i >= forced:
            exit_px, why = b.c * (1 - half), "TIME"
        else:
            continue
        qty = p.notional / pos["entry"]
        gross = (exit_px - pos["entry"]) * qty
        costs = (pos["entry"] + exit_px) * qty * fee + 2 * ofee
        trades.append({
            "entry_minute": pos["minute"], "exit_minute": b.minute,
            "entry": pos["entry"], "exit": exit_px, "reason": why,
            "gross": gross, "costs": costs, "net": gross - costs,
            "net_pct": 100.0 * (gross - costs) / p.notional,
            "held_minutes": i - pos["i"],
        })
        pos = None
    return trades, {
        "support": support, "resistance": resistance, "width_pct": width_pct,
        "crosses": crosses, "spread_pct": 100.0 * spread,
    }


def opening_hour_move(rows, p: Params):
    """Return% of the opening window itself: open of the first bar to its close.

    Known the instant the trading window starts, so it is legitimate as a filter.
    """
    cut = _plus(SESSION_START, p.open_minutes)
    opening = [b for b in rows if b.hhmm < cut]
    if len(opening) < 0.6 * p.open_minutes or opening[0].o <= 0:
        return None
    return 100.0 * (opening[-1].c - opening[0].o) / opening[0].o


def unconditional_control(trading, hold: int, spread: float, p: Params,
                          fee: float, ofee: float):
    """Net% of buying at *every* minute of this session and holding `hold` minutes.

    This is the null the idea has to beat. If buying at support is worth
    anything, its trades must do better than entering at an arbitrary minute of
    the same symbol on the same day, paying the same spread and the same fees.
    """
    if hold <= 0 or len(trading) <= hold:
        return None
    half = spread / 2.0
    out = []
    for i in range(len(trading) - hold):
        entry = trading[i].o * (1 + half)
        exit_px = trading[i + hold].c * (1 - half)
        qty = p.notional / entry
        gross = (exit_px - entry) * qty
        costs = (entry + exit_px) * qty * fee + 2 * ofee
        out.append(100.0 * (gross - costs) / p.notional)
    return statistics.mean(out) if out else None


def benchmark(rows, spread: float, p: Params, fee: float, ofee: float):
    """Hold the same symbol from the trading-window open to the forced exit."""
    trading = [b for b in rows if b.hhmm >= _plus(SESSION_START, p.open_minutes)]
    if len(trading) < 60:
        return None
    entry = trading[0].o * (1 + spread / 2.0)
    exit_px = trading[-1 - p.exit_buffer].c * (1 - spread / 2.0)
    qty = p.notional / entry
    gross = (exit_px - entry) * qty
    costs = (entry + exit_px) * qty * fee + 2 * ofee
    return 100.0 * (gross - costs) / p.notional


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-turnover", type=float, default=5_000_000.0)
    ap.add_argument("--max-spread-pct", type=float, default=0.5)
    ap.add_argument("--open-minutes", type=int, default=60)
    ap.add_argument("--min-width", type=float, default=1.5)
    ap.add_argument("--entry-zone", type=float, default=0.20)
    ap.add_argument("--target-zone", type=float, default=0.15)
    ap.add_argument("--stop-frac", type=float, default=0.40)
    ap.add_argument("--max-trades", type=int, default=3)
    ap.add_argument("--tag", default="base")
    ap.add_argument("--regime", choices=("all", "up", "down"), default="all",
                    help="keep sessions by opening-hour breadth (known before the "
                         "first trade; no look-ahead)")
    ap.add_argument("--breadth-threshold", type=float, default=50.0)
    ap.add_argument("--oracle", choices=("none", "up", "down"), default="none",
                    help="keep sessions by the REALISED trading-window drift. This "
                         "is look-ahead and is a diagnostic upper bound only.")
    ap.add_argument("--symbol-regime", choices=("all", "up", "down"), default="all",
                    help="keep symbols by their own opening-hour direction")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    p = Params(open_minutes=args.open_minutes, min_width_pct=args.min_width,
               entry_zone=args.entry_zone, target_zone=args.target_zone,
               stop_frac=args.stop_frac, max_trades=args.max_trades)
    fee, ofee = fee_rate(), order_fee()

    conn = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    bars, spreads = load(conn)
    conn.close()

    days = dense_sessions(bars, 0.6, 50)
    if not days:
        raise SystemExit("no session has enough intraday coverage to measure")

    turn, spr = defaultdict(list), defaultdict(list)
    for (day, tk), rows in bars.items():
        if day not in days:
            continue
        turn[tk].append(sum(b.v * b.c for b in rows))
        if (day, tk) in spreads:
            spr[tk].append(spreads[(day, tk)])
    universe = {
        tk for tk in turn
        if statistics.median(turn[tk]) >= args.min_turnover
        and spr.get(tk) and 100.0 * statistics.median(spr[tk]) <= args.max_spread_pct
    }

    # Pre-pass: session regime. Breadth is known at the end of the opening hour;
    # realised drift is not, and is only ever used as a labelled oracle.
    breadth, drift = {}, {}
    for day in days:
        ups, seen, moves = 0, 0, []
        for tk in universe:
            rows = bars.get((day, tk))
            spread = spreads.get((day, tk))
            if not rows or spread is None:
                continue
            m = opening_hour_move(rows, p)
            if m is not None:
                seen += 1
                ups += 1 if m > 0 else 0
            b = benchmark(rows, spread, p, fee, ofee)
            if b is not None:
                moves.append(b)
        breadth[day] = 100.0 * ups / seen if seen else 0.0
        drift[day] = statistics.median(moves) if moves else 0.0

    kept = []
    for day in days:
        if args.regime == "up" and breadth[day] < args.breadth_threshold:
            continue
        if args.regime == "down" and breadth[day] >= args.breadth_threshold:
            continue
        if args.oracle == "up" and drift[day] <= 0:
            continue
        if args.oracle == "down" and drift[day] > 0:
            continue
        kept.append(day)
    if not args.quiet:
        print(f"\n=== {args.tag} ===")
        print(f"session regime filter: regime={args.regime} oracle={args.oracle} "
              f"symbol={args.symbol_regime} -> {len(kept)}/{len(days)} sessions")
        print(f"  {'date':12}{'breadth':>9}{'drift':>9}{'kept':>6}")
        for day in days:
            print(f"  {day:12}{breadth[day]:>8.1f}%{drift[day]:>+8.3f}%"
                  f"{'  yes' if day in kept else '   no':>6}")
    if not kept:
        raise SystemExit("no session survives the regime filter")

    all_trades, rejects, per_day = [], defaultdict(int), defaultdict(list)
    per_day_bench, per_day_edge = defaultdict(list), defaultdict(list)
    bench, considered = [], 0
    for day in kept:
        for tk in sorted(universe):
            rows = bars.get((day, tk))
            if not rows:
                continue
            spread = spreads.get((day, tk))
            if spread is None:
                rejects["NO_SPREAD"] += 1
                continue
            if args.symbol_regime != "all":
                m = opening_hour_move(rows, p)
                if m is None:
                    rejects["NO_OPENING_MOVE"] += 1
                    continue
                if (args.symbol_regime == "up") != (m > 0):
                    rejects["SYMBOL_REGIME"] += 1
                    continue
            considered += 1
            trades, info = simulate(rows, spread, p, fee, ofee)
            if trades is None:
                rejects[info] += 1
                continue
            b = benchmark(rows, spread, p, fee, ofee)
            if b is not None:
                bench.append(b)
                per_day_bench[day].append(b)
            if not trades:
                rejects["NO_TOUCH"] += 1
            trading = [x for x in rows
                       if x.hhmm >= _plus(SESSION_START, p.open_minutes)]
            for t in trades:
                ctrl = unconditional_control(trading, t["held_minutes"], spread,
                                             p, fee, ofee)
                t.update(date=day, ticker=tk, width_pct=info["width_pct"],
                         spread_pct=info["spread_pct"],
                         control_net_pct=ctrl,
                         edge_pct=None if ctrl is None else t["net_pct"] - ctrl)
                all_trades.append(t)
                per_day[day].append(t["net_pct"])
                if t["edge_pct"] is not None:
                    per_day_edge[day].append(t["edge_pct"])

    REPORTS.mkdir(exist_ok=True)
    if all_trades:
        out = REPORTS / f"range_scalp_probe_{args.tag}_trades.csv"
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(all_trades[0].keys()))
            w.writeheader()
            w.writerows(all_trades)

    nets = [t["net_pct"] for t in all_trades]
    wins = [n for n in nets if n > 0]
    gross = [100.0 * t["gross"] / p.notional for t in all_trades]
    summary = {
        "tag": args.tag, "regime": args.regime, "oracle": args.oracle,
        "symbol_regime": args.symbol_regime,
        "sessions": len(kept), "sessions_available": len(days),
        "universe": len(universe),
        "symbol_sessions_considered": considered, "trades": len(nets),
        "win_rate": 100.0 * len(wins) / len(nets) if nets else 0.0,
        "avg_gross_pct": statistics.mean(gross) if gross else 0.0,
        "avg_net_pct": statistics.mean(nets) if nets else 0.0,
        "total_net_pct": sum(nets),
        "median_net_pct": statistics.median(nets) if nets else 0.0,
        "profitable_sessions": sum(1 for d in per_day if sum(per_day[d]) > 0),
        "measured_sessions": len(per_day),
        "bench_avg_net_pct": statistics.mean(bench) if bench else 0.0,
    }
    edges = [t["edge_pct"] for t in all_trades if t.get("edge_pct") is not None]
    summary["avg_edge_vs_random_entry_pct"] = statistics.mean(edges) if edges else 0.0
    summary["edge_positive_share"] = (
        100.0 * sum(1 for e in edges if e > 0) / len(edges) if edges else 0.0)
    if edges and len(edges) > 1:
        se = statistics.stdev(edges) / (len(edges) ** 0.5)
        summary["edge_t_stat"] = statistics.mean(edges) / se if se else 0.0
    if not args.quiet:
        print(f"sessions {summary['sessions']}  universe {summary['universe']}  "
              f"symbol-sessions {considered}")
        print("rejections: " + ", ".join(f"{k}={v}" for k, v in
                                         sorted(rejects.items(), key=lambda x: -x[1])))
        print(f"trades {len(nets)}  win% {summary['win_rate']:.1f}  "
              f"avg gross {summary['avg_gross_pct']:+.3f}%  "
              f"avg net {summary['avg_net_pct']:+.3f}%  "
              f"sum net {summary['total_net_pct']:+.1f}%")
        print("exit mix: " + ", ".join(
            f"{r}={sum(1 for t in all_trades if t['reason'] == r)}"
            for r in ("TARGET", "STOP", "TIME")))
        print(f"intraday buy-and-hold benchmark avg net "
              f"{summary['bench_avg_net_pct']:+.3f}%")
        print(f"vs random entry, same symbol/day/hold: edge "
              f"{summary['avg_edge_vs_random_entry_pct']:+.3f}% "
              f"(positive on {summary['edge_positive_share']:.1f}% of trades, "
              f"t={summary.get('edge_t_stat', 0.0):+.2f})")
        print(f"sessions net-positive: {summary['profitable_sessions']}/{len(per_day)}")
        print(f"  {'date':12}{'trades':>7}{'avg net':>9}{'drift':>9}{'edge':>9}")
        for d in sorted(per_day):
            v = per_day[d]
            drift = statistics.mean(per_day_bench[d]) if per_day_bench[d] else 0.0
            edge = statistics.mean(per_day_edge[d]) if per_day_edge[d] else 0.0
            print(f"  {d:12}{len(v):>7}{statistics.mean(v):>+8.3f}%"
                  f"{drift:>+8.3f}%{edge:>+8.3f}%")
    print("SUMMARY " + json.dumps(summary))


if __name__ == "__main__":
    main()
