r"""Grade the gap predictions the Rubix feed made, from the Rubix feed.

    venv\Scripts\python.exe scripts\research\close_out_gap_forward_v1.py --dry-run
    venv\Scripts\python.exe scripts\research\close_out_gap_forward_v1.py

One-off, and it has to exist rather than being done by hand, because what it
does is a judgement about the experiment rather than a chore.

``record_gap_forward.py`` now reads MubasherTrade PRO's minute store. Its v1
rows were made from the Rubix feed, which stopped on 2026-09-10, and 590 of
them were never graded -- the recorder's own scheduled task had been failing.
Those outcomes are still obtainable: ``data/rubix_live_market.db`` is a static
8 GB archive that still holds every candle through 2026-09-10.

They are not graded by the v2 path, and the guard in ``grade`` refuses to.
The two sources do not define an opening price the same way. On 2026-09-10 the
feed's first captured bar and Mubasher's first traded minute agree for 87.6% of
symbols and to 0.0000% at the median -- but 14 of 217 differ by more than 0.5%
and one by 5.4%, all of them thin names where the feed had a bar before the
symbol had a trade. Settling a prediction against a different definition of the
number it predicted is a measurement change inside an experiment, landing on
exactly the symbols the rule is least confident about.

So: v1 is closed out here, against the archive that made it. v2 begins at
2026-09-10 and is graded from Mubasher. No row ever mixes the two.

Writes only ``graded_at``, ``next_session``, ``next_open``, ``gap_percent`` and
``net_percent``, and only into rows that already exist with ``graded_at IS
NULL``. It cannot create a prediction and it cannot regrade one.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

ARCHIVE = ROOT / "data" / "rubix_live_market.db"
STORE = ROOT / "data" / "research" / "gap_forward.db"

#: The window v1 always read. Kept verbatim so a row graded today is graded the
#: way the same row would have been graded on the evening it was recorded.
SESSION_START = "07:00"
SESSION_END = "11:30"

V1_RULE = "top20pct-intraday-spread0.3-turnover20M-v1"


def archive_opens(market, session):
    """v1's own definition of an opening price: the first captured bar."""
    return dict(market.execute(
        "select ticker, min(case when ra = 1 then open end) from "
        "(select ticker, minute, open, row_number() over "
        "  (partition by ticker order by minute) ra from candles_1m "
        " where substr(minute,1,10) = ? "
        "   and substr(minute,12,5) between ? and ? and open > 0) "
        "group by ticker", (session, SESSION_START, SESSION_END)))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be graded and write nothing")
    args = parser.parse_args(argv)

    if not ARCHIVE.exists():
        print(f"The feed archive is gone ({ARCHIVE}). These rows can no longer "
              f"be graded, and must not be graded from another source.",
              file=sys.stderr)
        return 2

    store = sqlite3.connect(STORE)
    market = sqlite3.connect(f"file:{ARCHIVE.as_posix()}?mode=ro", uri=True)

    sessions = [row[0] for row in store.execute(
        "select distinct session from gap_predictions "
        "where graded_at IS NULL and rule_version = ? order by session", (V1_RULE,))]
    if not sessions:
        print("Nothing ungraded is left under the v1 rule.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    total = 0
    for session in sessions:
        following = market.execute(
            "select min(substr(minute,1,10)) from candles_1m "
            "where substr(minute,1,10) > ?", (session,)).fetchone()[0]
        if not following:
            print(f"  {session}: the archive holds no later session. "
                  f"Its outcome was never captured and never will be.")
            continue

        opens = archive_opens(market, following)
        pending = store.execute(
            "select ticker, close_price, spread_percent, broker_round_trip "
            "from gap_predictions where session = ? and graded_at IS NULL "
            "and rule_version = ?", (session, V1_RULE)).fetchall()

        graded = 0
        for ticker, close_price, spread, broker in pending:
            nxt = opens.get(ticker)
            if not nxt or not close_price:
                continue
            gap = 100.0 * (nxt - close_price) / close_price
            net = None if spread is None else gap - broker - spread
            if not args.dry_run:
                store.execute(
                    "UPDATE gap_predictions SET graded_at=?, next_session=?, "
                    "next_open=?, gap_percent=?, net_percent=? "
                    "WHERE session=? AND ticker=? AND graded_at IS NULL",
                    (now, following, nxt, gap, net, session, ticker))
            graded += 1
        total += graded
        print(f"  {session}: {graded} of {len(pending)} graded against {following}"
              + ("  (dry run)" if args.dry_run else ""))

    if not args.dry_run:
        store.commit()
    store.close()
    market.close()
    print()
    print(f"{'Would grade' if args.dry_run else 'Graded'} {total} v1 outcomes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
