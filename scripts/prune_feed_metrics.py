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
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import time

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


def wait_for_idle(minutes: float, *, poll_seconds: float = 20.0) -> bool:
    """Block until no supervisor is recorded as live. True when it is idle.

    Used to arm the big pass against the close of the session rather than
    against the clock: the collector writes continuously from 10:00, and 1,690
    batched deletes competing for the write lock is not something to run beside
    a live feed.

    ``supervisor_status`` verifies the PID *and* its recorded start time, so a
    reused PID never reads as the collector still being up.
    """
    deadline = time.monotonic() + minutes * 60
    announced = False
    while time.monotonic() < deadline:
        if not supervisor_status(SUPERVISOR_PID_FILE).get("running"):
            # A clean shutdown checkpoints on its way out. Give it a moment to
            # finish rather than opening the file underneath it.
            time.sleep(15)
            if not supervisor_status(SUPERVISOR_PID_FILE).get("running"):
                print(f"Collector is down at "
                      f"{datetime.now().strftime('%H:%M:%S')}. Starting.", flush=True)
                return True
        if not announced:
            print(f"Waiting for the collector to stop "
                  f"(up to {minutes:.0f} min)...", flush=True)
            announced = True
        time.sleep(poll_seconds)
    return False


def vacuum(database: Path) -> tuple[bool, str]:
    """Rebuild the file so the freed pages actually leave it.

    Deleting rows moves their pages to the free list; the file keeps its size
    and new rows reuse the space. That is enough to stop the growth, and it is
    all the retention pass does. VACUUM is what gives the gigabytes back, and
    it costs a full rewrite with the database locked throughout -- so it is
    opt-in and runs once, here, not on any shutdown.
    """
    before = database.stat().st_size
    started = time.monotonic()
    try:
        connection = sqlite3.connect(database, timeout=60)
        try:
            connection.execute("VACUUM")
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return False, str(error)
    after = database.stat().st_size
    return True, (f"{before/1024**3:.2f} GB -> {after/1024**3:.2f} GB "
                  f"in {time.monotonic() - started:.0f}s")


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
    parser.add_argument("--wait-for-idle-minutes", type=float, default=0.0,
                        help="wait up to this long for the collector to stop, "
                             "then prune. 0 means do not wait.")
    parser.add_argument("--vacuum", action="store_true",
                        help="rebuild the file afterwards to give the space "
                             "back. Locks the database for its whole duration.")
    args = parser.parse_args()

    if args.wait_for_idle_minutes > 0 and not wait_for_idle(args.wait_for_idle_minutes):
        print(f"\nThe collector was still running after "
              f"{args.wait_for_idle_minutes:.0f} minutes. Nothing was pruned.")
        return 1

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
        print("\nCaught up.")
    else:
        print(f"\nNot finished: {pending - result.deleted:,} rows still eligible. "
              f"Run it again, or let the next clean shutdown continue.")

    if args.vacuum:
        if not result.finished:
            # Rebuilding a file that still holds rows due to be deleted means
            # doing the expensive part twice.
            print("Skipping VACUUM: finish the pruning first.")
            return 1
        # Re-check rather than trust the earlier one: the wait may have been
        # long, and a rebuild underneath a restarted collector is the one thing
        # this must never do.
        if supervisor_status(SUPERVISOR_PID_FILE).get("running") and not args.force:
            print("Skipping VACUUM: the collector came back up.")
            return 1
        print("\nRebuilding the file (locked throughout)...", flush=True)
        ok, detail = vacuum(database)
        print(f"VACUUM   : {detail}" if ok else f"VACUUM failed: {detail}")
        if not ok:
            return 1
    else:
        print("The space is on the free list, so the file does not shrink -- "
              "future rows reuse it instead of growing it. Pass --vacuum to "
              "give the gigabytes back.")
    return 0 if not result.error else 1


if __name__ == "__main__":
    raise SystemExit(main())
