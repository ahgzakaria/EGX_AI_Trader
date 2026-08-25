r"""Age out the collector's per-tick telemetry, deliberately and in the open.

The supervisor does this on every clean shutdown within a sixty-second budget,
which keeps a normal day tidy. It will not catch up on a forty-million-row
backlog in one evening, and it should not try -- so the first big pass is run
here, by hand, when the collector is stopped and nothing is waiting on it.

Never deletes a quote or a candle, and never deletes the rare operational
events -- stalls, heartbeats, reconnects, subscription batches -- at any age.
Those are 439,000 rows against 43.8 million of per-tick telemetry, and they are
the record of how the feed behaved on the day something went wrong.

    venv\Scripts\python.exe scripts\prune_feed_metrics.py --dry-run
    venv\Scripts\python.exe scripts\prune_feed_metrics.py --budget-seconds 600
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.launcher_process_utils import supervisor_status
from services.feed_metrics_retention import (
    DEFAULT_RETENTION_DAYS,
    HIGH_VOLUME_EVENTS,
    estimate_pending,
    prune,
)

DEFAULT_DATABASE = PROJECT_ROOT / "data" / "rubix_live_market.db"
SUPERVISOR_PID_FILE = PROJECT_ROOT / "data" / "rubix_supervisor.pid.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--budget-seconds", type=float, default=600.0)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what is eligible and delete nothing")
    parser.add_argument("--force", action="store_true",
                        help="run even while the collector is live")
    args = parser.parse_args()

    database = Path(args.database)
    print(f"Database : {database}")
    print(f"Keeping  : {args.retention_days} days of {', '.join(HIGH_VOLUME_EVENTS)}")
    print("Keeping  : every other event, at every age")

    # Counting is itself slow on an unpruned table, which is the symptom.
    print("\nCounting eligible rows (slow on an unpruned table)...", flush=True)
    pending = estimate_pending(database, retention_days=args.retention_days)
    print(f"Eligible : {pending:,} rows")

    if pending == 0:
        print("\nNothing to do.")
        return 0

    if args.dry_run:
        print("\n--dry-run: nothing was deleted.")
        return 0

    status = supervisor_status(SUPERVISOR_PID_FILE)
    if status.get("running") and not args.force:
        # Not a hard refusal on principle -- the deletes are batched and
        # committed, so they are safe beside a live collector. But a big
        # backlog is work the collector should not be competing with, and
        # doing it by accident during a session is the wrong surprise.
        print(f"\nThe collector is running (PID {status.get('pid')}). "
              f"Stop it first, or pass --force.")
        return 1

    print(f"\nPruning, up to {args.budget_seconds:.0f}s...", flush=True)
    result = prune(database, retention_days=args.retention_days,
                   budget_seconds=args.budget_seconds)

    print(f"Deleted  : {result.deleted:,} rows in {result.batches} batches, "
          f"{result.seconds:.0f}s")
    if result.error:
        print(f"Error    : {result.error}")
    if result.finished:
        print("\nCaught up. The space is on the free list, so the file does not "
              "shrink -- future rows reuse it instead of growing the file.")
    else:
        print(f"\nNot finished: {pending - result.deleted:,} rows still eligible. "
              f"Run it again, or let the next clean shutdown continue.")
    return 0 if not result.error else 1


if __name__ == "__main__":
    raise SystemExit(main())
