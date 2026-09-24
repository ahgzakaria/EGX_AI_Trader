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
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STORE = ROOT / "data" / "research" / "gap_forward.db"

#: Bumped whenever the selection rule changes. Rows keep the version that made
#: them, so a later analysis can refuse to pool two different rules -- and
#: ``report`` refuses on its own if it finds more than one.
#:
#: v1 ran on the Rubix feed and read a quoted bid and ask. v2 reads
#: MubasherTrade PRO's minute store, which carries no quotes at all, so the
#: spread term is gone from the rule and ``spread_percent`` is NULL on every
#: v2 row. That is a different rule and the version has to say so.
#:
#: What the term was doing is measurable on the 1,178 graded v1 rows: with it,
#: 91 selections at +0.701% gross; without it, 186 at +0.770%. The spread was
#: the cost gate, not the edge -- so the hypothesis survives the source change
#: and the net-of-cost figure does not.
RULE_VERSION = "top20pct-intraday-turnover20M-mubasher-v2"
BROKER_ROUND_TRIP = 0.3638

#: Minutes are read for the session's own Cairo date, so there is no window to
#: state: the store's bars for 2026-09-10 are that session and nothing else.
#: What still needs saying is where the auction is, because a session whose
#: minutes stop before it has a last price and not a close.
AUCTION_MINUTE = "14:25"

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
    -- Roll's effective spread from the session's own trade tape. Recorded
    -- beside the prediction and never read by the rule that made it: against
    -- the quoted spread it ranks well (0.78-0.85) and is biased low, so it can
    -- order symbols and cannot price one. NULL on every v1 row.
    roll_spread_estimate REAL,
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
    # CREATE TABLE IF NOT EXISTS leaves an existing table's columns alone, so a
    # store written before v2 keeps its old shape and rejects every insert with
    # "no column named roll_spread_estimate". The 1,768 standing predictions in
    # it are the experiment; they are migrated, never rebuilt.
    held = {row[1] for row in conn.execute("PRAGMA table_info(gap_predictions)")}
    if "roll_spread_estimate" not in held:
        conn.execute("ALTER TABLE gap_predictions ADD COLUMN roll_spread_estimate REAL")
        conn.commit()
    return conn


def session_shapes(session: str, min_bars: int):
    """One session's shape per symbol, from MubasherTrade PRO's minute store.

    Three of these numbers are better than the feed this used to read. The open
    is a real 10:00 open rather than the first bar a collector happened to
    capture; the close is the price the 14:25 auction crossed at, which the old
    feed never sent; and the turnover is the figure the exchange reported
    rather than ``volume x close`` summed over minutes.

    One is gone. The store carries no bid or ask -- neither does the daily
    record, whose BBP/BAP columns are -1 in every row -- so ``spread`` is None
    for every symbol and the rule that reads it changed with the source. See
    RULE_VERSION.
    """

    from sector_flow.mubasher_local import roll_spread_estimates, session_minutes

    minutes = session_minutes(session)
    rolls = roll_spread_estimates(session)

    shapes = []
    for ticker, shape in minutes.items():
        if shape["bars"] < min_bars:
            continue
        first_open, last_close = shape["open"], shape["close"]
        high, low = shape["high"], shape["low"]
        if not first_open or not last_close or low <= 0:
            continue
        shapes.append({
            "ticker": ticker,
            "close": last_close,
            "intraday": 100.0 * (last_close - first_open) / first_open,
            "range": 100.0 * (high - low) / low,
            "close_position": (last_close - low) / (high - low) if high > low else 0.5,
            "turnover": shape["turnover"] or 0.0,
            "spread": None,                       # no quotes in this source
            "roll": rolls.get(ticker),
            "last_minute": shape["last_minute"],
            "close_confirmed": shape["close_confirmed"],
        })
    return shapes


#: A recorded prediction is never rewritten, so an early run against a
#: half-collected session would be wrong permanently. These make an incomplete
#: session refuse rather than record: the closing data normally lands within
#: seconds of 14:30 Cairo, but collection does fail -- 2026-08-20 arrived eight
#: hours late -- and the difference must not depend on the clock being generous.
MINIMUM_SYMBOLS = 150

#: The old feed wrote a bar for every minute it ticked, so 160 of a session's
#: ~270 minutes meant "traded throughout". Mubasher's store writes a bar only
#: for a minute that actually traded -- median 142 bars a symbol against the
#: feed's 254 -- so the same intent sits at a much lower count.
#:
#: Calibrated on 2026-09-09, where the old threshold kept 194 symbols: at 60
#: this keeps 199, 187 of them the same names, losing 7. At 160 it would keep
#: 110 and drop 90 symbols the rule had always accepted.
DEFAULT_MIN_BARS = 60


def session_is_complete(session: str, shapes) -> tuple[bool, str]:
    """Refuse a session whose auction has not landed in the store yet.

    The old gate asked whether the collector had reached 11:25 UTC. This one
    asks the question that actually matters: did the closing auction print?
    A session recorded from minutes that stop at 14:14 has a last continuous
    trade where its close should be, and on 2026-09-10 that was a different
    price for 163 of 191 symbols.
    """

    if len(shapes) < MINIMUM_SYMBOLS:
        return False, (f"only {len(shapes)} symbols have {{}} bars; "
                       f"{MINIMUM_SYMBOLS} expected")
    confirmed = [s for s in shapes if s["close_confirmed"]]
    if len(confirmed) < MINIMUM_SYMBOLS:
        latest = max((s["last_minute"] for s in shapes), default="absent")
        return False, (f"only {len(confirmed)} symbols have closed on the auction "
                       f"(last bar anywhere is {latest}, auction prints from "
                       f"{AUCTION_MINUTE})")
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
        # No spread term: this source has no quotes, and a filter that reads a
        # None every time selects nothing at all. On the 1,178 graded v1 rows
        # dropping it took 91 selections at +0.701% gross to 186 at +0.770%,
        # so it was gating cost rather than finding the effect. RULE_VERSION
        # records which rule made the row; `report` refuses to pool two.
        selected = int(rank < cutoff and shape["turnover"] >= 20_000_000)
        cursor = conn.execute(
            "INSERT OR IGNORE INTO gap_predictions "
            "(session, ticker, recorded_at, rule_version, selected, "
            " rank_in_session, session_count, intraday_return, session_range, "
            " close_position, turnover, spread_percent, roll_spread_estimate, "
            " close_price, broker_round_trip) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session, shape["ticker"], now, RULE_VERSION, selected, rank,
             len(shapes), shape["intraday"], shape["range"],
             shape["close_position"], shape["turnover"], shape["spread"],
             shape.get("roll"), shape["close"], BROKER_ROUND_TRIP))
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
    # Only rows this rule version made. The two sources do not define an open
    # the same way: on 2026-09-10 the feed's first captured bar and Mubasher's
    # first traded minute agree for 87.6% of symbols and to 0.0000% at the
    # median, but 14 of 217 differ by more than 0.5% and one by 5.4% -- the
    # thin names, where the feed had a bar before the symbol had a trade.
    #
    # A prediction made from one source and settled against the other is a
    # measurement change inside an experiment, on exactly the symbols the rule
    # is least sure about. So v1 rows are closed out from the feed archive that
    # made them, and v2 rows are graded here.
    stale = conn.execute(
        "select count(*) from gap_predictions "
        "where session = ? and graded_at IS NULL and rule_version <> ?",
        (session, RULE_VERSION)).fetchone()[0]
    if stale:
        print(f"session {session}: {stale} ungraded rows were recorded under an "
              f"earlier rule version and are not graded from this source.")
    pending = conn.execute(
        "select ticker, close_price, spread_percent, broker_round_trip "
        "from gap_predictions where session = ? and graded_at IS NULL "
        "and rule_version = ?",
        (session, RULE_VERSION)).fetchall()
    if not pending:
        print(f"session {session}: nothing ungraded "
              "(a graded prediction is never regraded)")
        conn.close()
        return

    from sector_flow.mubasher_local import available_sessions, session_opens

    later = [day for day in available_sessions() if day > session]
    if not later:
        print(f"session {session}: no later session is in the minute store; "
              "nothing can be graded, and nothing is")
        conn.close()
        return
    following = later[0]
    opens = session_opens(following)

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
    from sector_flow.mubasher_local import available_sessions

    held = available_sessions()
    if not held:
        raise SystemExit(
            "the MubasherTrade PRO minute store holds no sessions. It keeps a "
            "rolling fourteen and only while the terminal runs, so a machine "
            "that has not opened it has nothing to record.")

    # This used to record only the newest session and grade the one before it,
    # so a day the daily run was not clicked was lost for good: 2026-09-23 has
    # no predictions although the minute store still held it for weeks after.
    for session in sessions_to_record(held):
        try:
            record(session, min_bars)
        except SystemExit as refusal:     # one incomplete session is not fatal
            print(f"session {session}: not recorded -- {refusal}")

    graded = 0
    for earlier, later in zip(held, held[1:]):
        if needs_grading(earlier):
            print(f"grading {earlier} -- its outcome is {later}'s open")
            grade(earlier)
            graded += 1
    if len(held) < 2:
        print("only one session exists; nothing is gradeable yet")
    elif not graded:
        print(f"nothing ungraded through {held[-2]}")


def recorded_sessions(rule_version=None):
    """Sessions this rule version has recorded, oldest first."""
    rule_version = RULE_VERSION if rule_version is None else rule_version
    conn = connect()
    try:
        return [row[0] for row in conn.execute(
            "select distinct session from gap_predictions where rule_version = ? "
            "order by session", (rule_version,))]
    finally:
        conn.close()


def sessions_to_record(held):
    """The newest held session, and every held session the record skipped.

    Only sessions AFTER the first one this rule version recorded: a gap in the
    forward record is a missed day, while anything before it is history the
    rule never saw live, and writing that in now would be a backtest filed as
    a forward test. Each session is read from its own minutes only, so writing
    it late changes what is known about it by nothing.
    """
    held = sorted(held)
    if not held:
        return []
    done = set(recorded_sessions())
    if not done:
        return [held[-1]]                  # a new record starts today
    first = min(done)
    missing = [s for s in held if s > first and s not in done]
    if held[-1] not in missing:
        missing.append(held[-1])           # idempotent: rewrites nothing
    return missing


def needs_grading(session):
    """True when ``session`` has predictions and none has been graded yet.

    Not "any row still open": a prediction is settled against the very next
    session's open, so a symbol that did not trade that session has no outcome
    and never will. Those stragglers -- one each on 2026-09-10 and 2026-09-21 --
    would otherwise be retried, and reprinted, on every run until the minute
    store rolled them out.
    """
    conn = connect()
    try:
        ungraded, graded = conn.execute(
            "select sum(graded_at IS NULL), sum(graded_at IS NOT NULL) "
            "from gap_predictions where session = ? and rule_version = ?",
            (session, RULE_VERSION)).fetchone()
        return bool(ungraded) and not graded
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=("record", "grade", "report", "daily"))
    ap.add_argument("--session", help="YYYY-MM-DD; defaults to the latest session")
    ap.add_argument("--min-bars", type=int, default=DEFAULT_MIN_BARS)
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
        from sector_flow.mubasher_local import available_sessions

        held = available_sessions()
        if not held:
            raise SystemExit("the MubasherTrade PRO minute store holds no sessions")
        session = held[-1]
    if args.action == "record":
        record(session, args.min_bars, args.force)
    else:
        grade(session)


if __name__ == "__main__":
    main()
