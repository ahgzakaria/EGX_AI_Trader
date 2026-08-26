"""Is the overnight gap predictable, or only present?

The gap is the strongest reading in this project by a wide margin: +0.663% mean
at t = +15.7, positive on 72.6% of symbol-transitions and on every one of the
thirteen measurable session boundaries. But a single night nets +0.095% on the
mean and **-0.150% on the median** after the round trip, so capturing it
indiscriminately is not a strategy -- most nights lose a little.

That leaves the question nobody has asked: does the gap have cross-sectional
structure? If something observable **at the close** says which symbols will gap
further, the losing median stops being the relevant statistic, because you would
not be holding the median name.

Every candidate here is computed from the session that has already finished. No
predictor uses a price from the session it is predicting.

Multiple comparisons are the obvious hazard -- six predictors against one
outcome, on 16 sessions -- so the report prints the count and the threshold
alongside the results rather than leaving a reader to remember.

Read-only over data/rubix_live_market.db.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKET_DB = ROOT / "data" / "rubix_live_market.db"
SESSION_START = "07:00"
SESSION_END = "11:30"


def load_sessions(min_bars: int):
    """{(ticker, date): dict of that session's shape} from traded minutes."""
    conn = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "select ticker, substr(minute,1,10) d, "
        "  min(case when ra = 1 then open end)  as first_open, "
        "  min(case when rd = 1 then close end) as last_close, "
        "  max(high) as high, min(low) as low, "
        "  sum(volume) as volume, sum(volume * close) as turnover, count(*) as bars "
        "from (select ticker, minute, open, high, low, close, volume, "
        "        row_number() over (partition by ticker, substr(minute,1,10) "
        "                           order by minute) ra, "
        "        row_number() over (partition by ticker, substr(minute,1,10) "
        "                           order by minute desc) rd "
        "      from candles_1m "
        "      where substr(minute,12,5) between ? and ? "
        "        and open > 0 and high > 0 and low > 0 and close > 0) "
        "group by ticker, d", (SESSION_START, SESSION_END)
    ).fetchall()

    spreads = defaultdict(list)
    for ticker, stamp, bid, ask in conn.execute(
        "select ticker, market_timestamp, bid, ask from quotes "
        "where substr(market_timestamp,12,5) between ? and ? "
        "and bid > 0 and ask > 0 and ask >= bid", (SESSION_START, SESSION_END)
    ):
        spreads[(ticker, stamp[:10])].append(200.0 * (ask - bid) / (ask + bid))
    conn.close()

    sessions = {}
    for tk, day, first_open, last_close, high, low, volume, turnover, bars in rows:
        if bars < min_bars or not first_open or not last_close or low <= 0:
            continue
        quoted = spreads.get((tk, day))
        sessions[(tk, day)] = {
            "open": first_open, "close": last_close, "high": high, "low": low,
            "volume": volume or 0.0, "turnover": turnover or 0.0,
            "spread": statistics.median(quoted) if quoted and len(quoted) >= 20 else None,
            "intraday": 100.0 * (last_close - first_open) / first_open,
            "range": 100.0 * (high - low) / low,
            # Where the close sits in the day's range: 1.0 = closed on the high.
            "close_position": (last_close - low) / (high - low) if high > low else 0.5,
        }
    return sessions


def significance(pairs):
    if len(pairs) < 20:
        return None, None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    if len(set(xs)) < 2:
        return None, None
    r = statistics.correlation(xs, ys)
    if abs(r) >= 1.0:
        return r, None
    return r, r * math.sqrt((len(pairs) - 2) / (1 - r * r))


def quintiles(pairs, cost_of=None):
    pairs = sorted(pairs)
    size = len(pairs) // 5
    out = []
    for index in range(5):
        start = index * size
        end = len(pairs) if index == 4 else (index + 1) * size
        chunk = pairs[start:end]
        gaps = [g for _v, g in chunk]
        row = {"lo": chunk[0][0], "hi": chunk[-1][0], "n": len(chunk),
               "gap": statistics.mean(gaps),
               "positive": 100.0 * sum(1 for g in gaps if g > 0) / len(gaps)}
        out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-bars", type=int, default=160)
    ap.add_argument("--broker", type=float, default=0.3638,
                    help="round-trip commission, percent")
    args = ap.parse_args()

    sessions = load_sessions(args.min_bars)
    days = sorted({d for _tk, d in sessions})
    dense = [d for d in days if sum(1 for (_t, dd) in sessions if dd == d) >= 50]

    # Outcome: the gap from this session's close to the next session's open,
    # and the same net of this symbol's own round-trip cost.
    observations = []
    for index, day in enumerate(dense[:-1]):
        nxt = dense[index + 1]
        for (ticker, dd), shape in sessions.items():
            if dd != day:
                continue
            following = sessions.get((ticker, nxt))
            if not following or shape["spread"] is None:
                continue
            gap = 100.0 * (following["open"] - shape["close"]) / shape["close"]
            observations.append({
                **shape,
                "ticker": ticker, "date": day,
                "gap": gap,
                "net": gap - args.broker - shape["spread"],
            })

    if not observations:
        raise SystemExit("no usable transitions")

    gaps = [o["gap"] for o in observations]
    nets = [o["net"] for o in observations]
    print(f"transitions: {len(observations):,} over {len(dense)} sessions\n")
    print(f"  gap            mean {statistics.mean(gaps):+.4f}%  "
          f"median {statistics.median(gaps):+.4f}%  "
          f"positive {100.0 * sum(1 for g in gaps if g > 0) / len(gaps):.1f}%")
    print(f"  net of costs   mean {statistics.mean(nets):+.4f}%  "
          f"median {statistics.median(nets):+.4f}%  "
          f"positive {100.0 * sum(1 for n in nets if n > 0) / len(nets):.1f}%")

    predictors = {
        "intraday return": "intraday",
        "session range %": "range",
        "close position in range": "close_position",
        "turnover (EGP)": "turnover",
        "quoted spread %": "spread",
        "volume": "volume",
    }

    print(f"\n{'=' * 70}\nPREDICTORS, all known at the close\n{'=' * 70}")
    findings = []
    for label, key in predictors.items():
        pairs = [(o[key], o["gap"]) for o in observations if o[key] is not None]
        r, t = significance(pairs)
        if r is None:
            continue
        findings.append((label, key, r, t))
        print(f"\n{label:26} r = {r:+.3f}   t = {t:+.2f}")
        for row in quintiles(pairs):
            print(f"    {row['lo']:>12.3f}..{row['hi']:<12.3f} n={row['n']:>4} "
                  f"gap {row['gap']:>+7.3f}%  up {row['positive']:>5.1f}%")

    tested = len(findings)
    threshold = 2.0 + 0.6 * math.log(max(tested, 1))
    print(f"\n{'=' * 70}\n{tested} predictors tested against one outcome.")
    print(f"A single t of 2 is expected by chance about once in {tested} tries,")
    print(f"so treat |t| below ~{threshold:.1f} as noise.\n")
    print(f"  {'predictor':26}{'r':>8}{'t':>8}   verdict")
    for label, _key, r, t in sorted(findings, key=lambda f: -abs(f[3])):
        verdict = "worth pursuing" if abs(t) >= threshold else "noise"
        print(f"  {label:26}{r:>+8.3f}{t:>+8.2f}   {verdict}")


if __name__ == "__main__":
    main()
