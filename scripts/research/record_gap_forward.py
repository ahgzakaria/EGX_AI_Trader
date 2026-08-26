"""Record tonight's gap prediction before tonight happens.

GAP_PREDICTABILITY found the first effect in this project that clears its costs
by a margin: selecting the top fifth of symbols by the session's own return
turns a -0.107% net into +0.913%, cross-sectional at t = +11.93. It rests on
sixteen sessions in one month, and it cannot be checked against history because
the daily `open` field is fabricated in every source available.

So it has to be checked forward. The gap yields about 38 observations a session
-- a month of recording exceeds the entire nine-year swing backtest -- which
makes it the one hypothesis here settleable on a timescale worth waiting for.

The design has one job: make it impossible to grade a prediction that was not
committed to first.

* Predictions are written after the close, from that session only, and the
  outcome column is left NULL. Nothing in this script can fill it.
* Grading is a separate invocation that reads the *next* session's opening
  price and writes outcomes into rows that already exist. It refuses to create
  a row it is grading.
* Rows are keyed on (session, ticker) and inserted with `INSERT OR IGNORE`, so
  re-running never rewrites a standing prediction. A prediction may be recorded
  once and only once.
* Every row carries the rule version and the cost basis used, because a
  prediction whose rule nobody recorded is the artifact this project has already
  been burned by.

Read-only over the market DB; writes only data/research/gap_forward.db.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKET_DB = ROOT / "data" / "rubix_live_market.db"
STORE = ROOT / "data" / "research" / "gap_forward.db"

SESSION_START = "07:00"
SESSION_END = "11:30"

#: Bumped whenever the selection rule changes. Rows keep the version that made
#: them, so a later analysis can refuse to pool two different rules.
RULE_VERSION = "top20pct-intraday-spread0.3-turnover20M-v1"
BROKER_ROUND_TRIP = 0.3638

SCHEMA = """
CREATE TABLE IF NOT EXISTS gap_predictions (
    session TEXT NOT NULL,
    ticker TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    selected INTEGER NOT NULL,
    rank_in_session INTEGER NOT NULL,
    session_count INTEGER NOT NULL,
    intraday_return REAL NOT NULL,
    session_range REAL NOT NULL,
    close_position REAL NOT NULL,
    turnover REAL NOT NULL,
    spread_percent REAL,
    close_price REAL NOT NULL,
    broker_round_trip REAL NOT NULL,
    -- graded later, by a separate invocation, never by the recorder
    graded_at TEXT,
    next_session TEXT,
    next_open REAL,
    gap_percent REAL,
    net_percent REAL,
    PRIMARY KEY (session, ticker)
);
"""


def connect():
    STORE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STORE)
    conn.executescript(SCHEMA)
    return conn


def session_shapes(session: str, min_bars: int):
    conn = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "select ticker, "
        "  min(case when ra = 1 then open end), min(case when rd = 1 then close end), "
        "  max(high), min(low), sum(volume * close), count(*) "
        "from (select ticker, minute, open, high, low, close, volume, "
        "        row_number() over (partition by ticker order by minute) ra, "
        "        row_number() over (partition by ticker order by minute desc) rd "
        "      from candles_1m "
        "      where substr(minute,1,10) = ? "
        "        and substr(minute,12,5) between ? and ? "
        "        and open > 0 and high > 0 and low > 0 and close > 0) "
        "group by ticker", (session, SESSION_START, SESSION_END)
    ).fetchall()
    spreads = {}
    raw = {}
    for ticker, bid, ask in conn.execute(
        "select ticker, bid, ask from quotes "
        "where substr(market_timestamp,1,10) = ? "
        "and substr(market_timestamp,12,5) between ? and ? "
        "and bid > 0 and ask > 0 and ask >= bid",
        (session, SESSION_START, SESSION_END)
    ):
        raw.setdefault(ticker, []).append(200.0 * (ask - bid) / (ask + bid))
    for ticker, values in raw.items():
        if len(values) >= 20:
            spreads[ticker] = statistics.median(values)
    conn.close()

    shapes = []
    for ticker, first_open, last_close, high, low, turnover, bars in rows:
        if bars < min_bars or not first_open or not last_close or low <= 0:
            continue
        shapes.append({
            "ticker": ticker,
            "close": last_close,
            "intraday": 100.0 * (last_close - first_open) / first_open,
            "range": 100.0 * (high - low) / low,
            "close_position": (last_close - low) / (high - low) if high > low else 0.5,
            "turnover": turnover or 0.0,
            "spread": spreads.get(ticker),
        })
    return shapes


#: A recorded prediction is never rewritten, so an early run against a
#: half-collected session would be wrong permanently. These make an incomplete
#: session refuse rather than record: the closing data normally lands within
#: seconds of 14:30 Cairo, but collection does fail -- 2026-08-20 arrived eight
#: hours late -- and the difference must not depend on the clock being generous.
MINIMUM_SYMBOLS = 150
LATEST_BAR_REQUIRED = "11:25"


def session_is_complete(session: str, shapes) -> tuple[bool, str]:
    if len(shapes) < MINIMUM_SYMBOLS:
        return False, (f"only {len(shapes)} symbols have {{}} bars; "
                       f"{MINIMUM_SYMBOLS} expected")
    conn = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    last = conn.execute(
        "select max(substr(minute,12,5)) from candles_1m "
        "where substr(minute,1,10) = ? and substr(minute,12,5) <= ?",
        (session, SESSION_END)).fetchone()[0]
    conn.close()
    if not last or last < LATEST_BAR_REQUIRED:
        return False, f"last bar is {last or 'absent'}, before {LATEST_BAR_REQUIRED}"
    return True, ""


def record(session: str, min_bars: int, force: bool = False) -> None:
    shapes = session_shapes(session, min_bars)
    if not shapes:
        raise SystemExit(f"no usable minute data for {session}")

    complete, why = session_is_complete(session, shapes)
    if not complete and not force:
        raise SystemExit(
            f"session {session} looks incomplete: {why.format(min_bars)}.\n"
            "Nothing was recorded. A prediction is written once and never "
            "rewritten, so recording a half-collected session would be wrong "
            "permanently. Re-run once collection has finished, or pass --force "
            "if you have checked and disagree.")
    shapes.sort(key=lambda s: -s["intraday"])
    cutoff = max(1, len(shapes) // 5)
    now = datetime.now(timezone.utc).isoformat()

    conn = connect()
    written = 0
    for rank, shape in enumerate(shapes):
        selected = int(
            rank < cutoff
            and shape["spread"] is not None and shape["spread"] <= 0.3
            and shape["turnover"] >= 20_000_000
        )
        cursor = conn.execute(
            "INSERT OR IGNORE INTO gap_predictions "
            "(session, ticker, recorded_at, rule_version, selected, "
            " rank_in_session, session_count, intraday_return, session_range, "
            " close_position, turnover, spread_percent, close_price, "
            " broker_round_trip) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session, shape["ticker"], now, RULE_VERSION, selected, rank,
             len(shapes), shape["intraday"], shape["range"],
             shape["close_position"], shape["turnover"], shape["spread"],
             shape["close"], BROKER_ROUND_TRIP))
        written += cursor.rowcount
    conn.commit()

    total, chosen = conn.execute(
        "select count(*), sum(selected) from gap_predictions where session = ?",
        (session,)).fetchone()
    conn.close()
    print(f"session {session}: {written} new rows "
          f"({total} recorded, {chosen or 0} selected by the rule)")
    if written == 0:
        print("  nothing new -- this session was already recorded, and a "
              "standing prediction is never rewritten")


def grade(session: str) -> None:
    """Fill outcomes for a session already recorded, from the NEXT open."""
    conn = connect()
    pending = conn.execute(
        "select ticker, close_price, spread_percent, broker_round_trip "
        "from gap_predictions where session = ? and graded_at IS NULL",
        (session,)).fetchall()
    if not pending:
        print(f"session {session}: nothing ungraded "
              "(a graded prediction is never regraded)")
        conn.close()
        return

    market = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    following = market.execute(
        "select min(substr(minute,1,10)) from candles_1m "
        "where substr(minute,1,10) > ?", (session,)).fetchone()[0]
    if not following:
        print(f"session {session}: no later session exists yet; "
              "nothing can be graded, and nothing is")
        market.close(); conn.close()
        return
    opens = dict(market.execute(
        "select ticker, min(case when ra = 1 then open end) from "
        "(select ticker, minute, open, row_number() over "
        "  (partition by ticker order by minute) ra from candles_1m "
        " where substr(minute,1,10) = ? "
        "   and substr(minute,12,5) between ? and ? and open > 0) "
        "group by ticker", (following, SESSION_START, SESSION_END)))
    market.close()

    now = datetime.now(timezone.utc).isoformat()
    graded = 0
    for ticker, close_price, spread, broker in pending:
        nxt = opens.get(ticker)
        if not nxt or not close_price:
            continue
        gap = 100.0 * (nxt - close_price) / close_price
        net = None if spread is None else gap - broker - spread
        conn.execute(
            "UPDATE gap_predictions SET graded_at=?, next_session=?, "
            "next_open=?, gap_percent=?, net_percent=? "
            "WHERE session=? AND ticker=? AND graded_at IS NULL",
            (now, following, nxt, gap, net, session, ticker))
        graded += 1
    conn.commit()

    rows = conn.execute(
        "select selected, gap_percent, net_percent from gap_predictions "
        "where session = ? and graded_at IS NOT NULL", (session,)).fetchall()
    conn.close()
    print(f"session {session} graded against {following}: {graded} outcomes")
    for flag, label in ((1, "selected by the rule"), (0, "not selected")):
        nets = [r[2] for r in rows if r[0] == flag and r[2] is not None]
        gaps = [r[1] for r in rows if r[0] == flag]
        if not gaps:
            continue
        print(f"  {label:22} n={len(gaps):>4}  gap {statistics.mean(gaps):+.3f}%"
              + (f"  net {statistics.mean(nets):+.3f}%" if nets else ""))


def report() -> None:
    conn = connect()
    rows = conn.execute(
        "select rule_version, selected, gap_percent, net_percent, session "
        "from gap_predictions where graded_at IS NOT NULL").fetchall()
    if not rows:
        print("nothing graded yet")
        conn.close()
        return
    versions = {r[0] for r in rows}
    if len(versions) > 1:
        print(f"WARNING: {len(versions)} rule versions present. Not pooled.")
    for version in sorted(versions):
        subset = [r for r in rows if r[0] == version]
        sessions = {r[4] for r in subset}
        print(f"\nrule {version}   {len(subset):,} graded over {len(sessions)} sessions")
        for flag, label in ((1, "selected"), (0, "not selected")):
            nets = [r[3] for r in subset if r[1] == flag and r[3] is not None]
            if not nets:
                continue
            mean = statistics.mean(nets)
            se = (statistics.stdev(nets) / len(nets) ** 0.5) if len(nets) > 1 else 0
            print(f"  {label:14} n={len(nets):>5}  net {mean:+.4f}%"
                  + (f"  t={mean / se:+.2f}" if se else "")
                  + f"  positive {100.0 * sum(1 for n in nets if n > 0) / len(nets):.1f}%")
    conn.close()


def daily(min_bars: int) -> None:
    """One invocation for a scheduler: record today, grade what today settles.

    Grading session S needs S+1's opening price, so after today's close the
    session that becomes gradeable is the previous one -- today's open is its
    outcome. Recording today and grading yesterday is therefore the complete
    daily unit, and doing both here means the scheduled task is a single
    command with no ordering for anyone to get wrong.

    Every step is idempotent, so a task that fires twice, or catches up after a
    missed day, changes nothing it should not.
    """
    conn = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    sessions = [r[0] for r in conn.execute(
        "select distinct substr(minute,1,10) d from candles_1m "
        "order by d desc limit 2")]
    conn.close()
    if not sessions:
        raise SystemExit("no sessions in the market database")

    record(sessions[0], min_bars)
    if len(sessions) > 1:
        print(f"grading {sessions[1]} -- its outcome is {sessions[0]}'s open")
        grade(sessions[1])
    else:
        print("only one session exists; nothing is gradeable yet")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=("record", "grade", "report", "daily"))
    ap.add_argument("--session", help="YYYY-MM-DD; defaults to the latest session")
    ap.add_argument("--min-bars", type=int, default=160)
    ap.add_argument("--force", action="store_true",
                    help="record even if the session looks incomplete")
    args = ap.parse_args()

    if args.action == "report":
        report()
        return
    if args.action == "daily":
        daily(args.min_bars)
        return

    session = args.session
    if not session:
        conn = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
        session = conn.execute(
            "select max(substr(minute,1,10)) from candles_1m").fetchone()[0]
        conn.close()
    if args.action == "record":
        record(session, args.min_bars, args.force)
    else:
        grade(session)


if __name__ == "__main__":
    main()
