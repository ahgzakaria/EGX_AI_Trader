"""Turn the scalping and gap probes' lens on the swing strategy itself.

Three questions, in the order they matter:

  1. Does the strategy hold long enough for the per-round-trip toll to be
     amortised, and what shape is its expectancy?
  2. Is the friction it was backtested against the friction it would pay?
  3. Does it read the daily `open` field anywhere -- the field the gap probe
     found to be carried forward rather than observed?

Read-only. Reads reports/backtest_results.csv, data/market_data_cache.sqlite
and data/rubix_live_market.db. Changes nothing.
"""
from __future__ import annotations

import csv
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "reports" / "backtest_results.csv"
CACHE = ROOT / "data" / "market_data_cache.sqlite"
MARKET = ROOT / "data" / "rubix_live_market.db"

BROKER_ROUND_TRIP = 0.3638      # contract note in config/settings.json, both sides
MODELLED_COMMISSION = 0.003     # settings.json -> backtest.commission
MODELLED_SLIPPAGE = 0.0005      # per side


def load_trades():
    return list(csv.DictReader(RESULTS.open(encoding="utf-8-sig")))


def holding_profile(trades) -> None:
    print("=" * 68)
    print("1. Holding profile and the shape of the expectancy")
    print("=" * 68)
    held = sorted(int(float(t["holding_days"])) for t in trades if t["holding_days"])
    print(f"  trades {len(trades)}   mean hold {statistics.mean(held):.2f} sessions"
          f"   median {statistics.median(held):.0f}   max {held[-1]}")
    for thr in (2, 3, 5):
        share = 100.0 * sum(1 for x in held if x <= thr) / len(held)
        print(f"    held <= {thr} sessions: {share:5.1f}%")

    buckets = defaultdict(list)
    for t in trades:
        if not t["holding_days"] or not t["profit_percent"]:
            continue
        h = int(float(t["holding_days"]))
        key = ("1-2" if h <= 2 else "3-5" if h <= 5 else
               "6-10" if h <= 10 else "11-20" if h <= 20 else "20+")
        buckets[key].append(float(t["profit_percent"]))
    print(f"\n  {'hold':>8}{'n':>6}{'mean':>10}{'median':>10}{'win%':>8}")
    for key in ("1-2", "3-5", "6-10", "11-20", "20+"):
        v = buckets.get(key)
        if not v:
            continue
        print(f"  {key:>8}{len(v):>6}{statistics.mean(v):>+9.3f}%"
              f"{statistics.median(v):>+9.3f}%"
              f"{100.0 * sum(1 for x in v if x > 0) / len(v):>7.1f}%")
    print("\n  NOTE: holding period is an outcome, not a decision. 78% of exits are")
    print("  trailing stops, which terminate losers early, so short holds are")
    print("  losing trades by construction. This is not evidence that holding")
    print("  longer would earn more.")

    profits = [float(t["profit_percent"]) for t in trades if t["profit_percent"]]
    print(f"\n  per-trade profit: mean {statistics.mean(profits):+.3f}%  "
          f"median {statistics.median(profits):+.3f}%  "
          f"positive {100.0 * sum(1 for x in profits if x > 0) / len(profits):.1f}%")
    print("  A positive mean over a negative median is a right tail carrying the")
    print("  result -- the same shape the overnight gap showed.")
    print(f"\n  exit reasons: "
          f"{', '.join(f'{k}={v}' for k, v in Counter(t['exit_reason'] for t in trades).most_common())}")


def friction(trades) -> None:
    print("\n" + "=" * 68)
    print("2. Modelled friction versus measured friction")
    print("=" * 68)
    conn = sqlite3.connect(f"file:{MARKET}?mode=ro", uri=True)
    raw = defaultdict(list)
    for tk, b, a in conn.execute(
        "select ticker, bid, ask from quotes "
        "where substr(market_timestamp,1,10) >= '2026-08-02' "
        "and substr(market_timestamp,12,5) between '07:00' and '11:30' "
        "and bid > 0 and ask > 0 and ask >= bid"
    ):
        raw[tk].append(200.0 * (a - b) / (a + b))
    conn.close()
    spread = {k: statistics.median(v) for k, v in raw.items() if len(v) >= 50}

    counts = Counter(t["symbol"].replace(".CA", "") for t in trades)
    matched = [(s, n, spread[s]) for s, n in counts.items() if s in spread]
    weight = sum(n for _s, n, _x in matched)
    weighted = sum(n * x for _s, n, x in matched) / weight
    print(f"  symbols traded {len(counts)}, with a measurable live spread {len(matched)}")
    print(f"  trade-weighted median spread of the traded names: {weighted:.3f}%")

    charged = []
    for t in trades:
        try:
            entry, exit_px = float(t["entry_price"]), float(t["exit_price"])
        except (TypeError, ValueError):
            continue
        charged.append(100.0 * (entry + exit_px) * MODELLED_COMMISSION / entry)
    modelled = statistics.mean(charged) + 100.0 * 2 * MODELLED_SLIPPAGE
    measured = BROKER_ROUND_TRIP + weighted
    gap = measured - modelled
    print(f"  modelled: {statistics.mean(charged):.3f}% commission "
          f"+ {100.0 * 2 * MODELLED_SLIPPAGE:.3f}% slippage = {modelled:.3f}%")
    print(f"  measured: {BROKER_ROUND_TRIP:.3f}% broker + {weighted:.3f}% spread "
          f"= {measured:.3f}%")
    print(f"  understated by {gap:+.3f}% per round trip")

    extra = 0.0
    for t in trades:
        try:
            entry, shares = float(t["entry_price"]), float(t["shares"] or 0)
        except (TypeError, ValueError):
            continue
        if shares > 0:
            extra += entry * shares * gap / 100.0
    profits = [float(t["profit_percent"]) for t in trades if t["profit_percent"]]
    print(f"\n  per-trade edge: {statistics.mean(profits):+.3f}% -> "
          f"{statistics.mean(profits) - gap:+.3f}%")
    print(f"  portfolio: net profit 75,669 -> {75669 - extra:,.0f} EGP, "
          f"total return 75.7% -> {(75669 - extra) / 1000:.1f}%")
    print("  Spread is measured in August 2026 and applied to trades from 2020")
    print("  onward, so this is indicative, not a restatement of the backtest.")


def open_field() -> None:
    print("\n" + "=" * 68)
    print("3. The daily `open` field the strategy is standing on")
    print("=" * 68)
    conn = sqlite3.connect(f"file:{CACHE}?mode=ro", uri=True)
    series = defaultdict(list)
    impossible = total_bars = 0
    for prov, sym, per, ts, o, h, l, c in conn.execute(
        "select provider, symbol, period, timestamp, open, high, low, close "
        "from market_data_candles where interval = '1d' "
        "and open > 0 and close > 0 and high > 0 and low > 0 "
        "order by provider, symbol, period, timestamp"
    ):
        total_bars += 1
        if o > h + 1e-9 or o < l - 1e-9:
            impossible += 1
        series[(prov, sym, per)].append((o, c))
    conn.close()

    agg = defaultdict(lambda: [0, 0])
    for (prov, _s, _p), rows in series.items():
        for (_o0, c0), (o1, _c1) in zip(rows, rows[1:]):
            agg[prov][0] += 1
            if abs(o1 - c0) < 1e-9:
                agg[prov][1] += 1
    for prov, (n, flat) in agg.items():
        print(f"  {prov:8} {n:>8,} transitions   "
              f"open == previous close: {100.0 * flat / n:5.1f}%")
    print(f"  bars whose open lies outside [low, high] -- impossible: "
          f"{impossible:,} of {total_bars:,} ({100.0 * impossible / total_bars:.1f}%)")
    print("\n  The open is carried forward, not observed, and is not even clipped")
    print("  into the bar's own range. Anything reading it is reading a constant.")


def candle_dependence(trades) -> None:
    print("\n" + "=" * 68)
    print("4. What that does to candle confirmation")
    print("=" * 68)
    patterns = ("Bullish Engulfing", "Hammer", "Doji", "Morning Star")
    found = Counter()
    for t in trades:
        for p in patterns:
            if p in (t["reasons"] or ""):
                found[p] += 1
    print(f"  strategy.require_candle_confirmation gates signals on candle_score,")
    print(f"  and strategy/candles.py reads Open in every pattern it tests.")
    print(f"\n  cited across {len(trades)} backtest trades:")
    for p in patterns:
        print(f"    {p:20}{found[p]:>5}  ({100.0 * found[p] / len(trades):.1f}%)")
    print("\n  With Open == previous Close, these stop being candle geometry:")
    print("    Bullish Engulfing needs Open < previous Close -- unreachable when")
    print("      they are equal, and it fires 5 times in 684.")
    print("    Doji becomes 'today closed near yesterday's close', and it is the")
    print("      most-cited confirmation in the whole record.")
    print("    Morning Star reduces to a comparison of lagged returns.")
    print("  The detector still fires. It is not detecting what it is named for.")


def main() -> None:
    trades = load_trades()
    holding_profile(trades)
    friction(trades)
    open_field()
    candle_dependence(trades)


if __name__ == "__main__":
    main()
