r"""Daily forward-testing run for CONFIRMED_VOLUME_BREAKOUT.

    venv\Scripts\python.exe scripts\record_confirmed_breakout_forward.py

Polled hourly after the EGX close (Sun-Thu, 15:00-22:00 Cairo) via Task
Scheduler. It records only: it places no order, holds no position and changes no
strategy parameter.

**Why a scheduled run is the whole point.** Every number this strategy has is
in-sample -- built on a decade of history, with thresholds chosen while both
eras were visible. The one kind of evidence that can never be that is a signal
written down before anyone knows the answer, and that only exists if something
writes it down *every session*, including the many that produce nothing. A
forward record assembled by hand, when someone remembers, is a record of the
days someone remembered.

**It is polled, not scheduled to a time.** The provider's publication delay is
not known -- EODHD has no stated release time, and the Rubix daily bridge is
disabled -- and a fixed hour is therefore a guess. Two were tried and both were
wrong: 16:30 was arbitrary padding, and 15:30 fired on 2026-08-30 into a router
whose newest bar was still 2026-08-26.

So this runs hourly and asks. What makes that affordable is the fast path: a
cheap probe of a few symbols answers "is there a session newer than the ones
already recorded", and when the answer is no the run exits in seconds without
scanning 214 symbols. The first poll that sees a new completed session does the
real work; the rest cost almost nothing.

Exit codes: 0 when the session was recorded or was already present, 1 when the
scan could not read a session at all.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

# Runnable from any working directory (Task Scheduler safe).
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

STATUS_DIRECTORY = ROOT / "data" / "automation_status"


def _status_path():
    day = datetime.now(timezone.utc).astimezone().date().isoformat()
    return STATUS_DIRECTORY / f"confirmed_breakout_forward_{day}.json"


def _write_status(payload):
    """A per-day file, so a silent failure is visible without reading a log.

    Every failure this project has actually suffered was a silence -- a
    collector that died and stayed dead for sixteen hours. A scheduled task
    that stops running looks exactly like a market with no signals, which is
    the normal case here, so the absence of today's file is the alarm.
    """
    STATUS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = _status_path()
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def _record_summary(summary):
    return {
        "sessions": summary["sessions"],
        "first_session": summary["first_session"],
        "signals": summary["signals"],
        "closed": summary["closed"],
        "mean_lift": summary["mean_lift"],
        "configurations": len(summary["configs"]),
    }


def run(database=None, force=False):
    from core.environment import load_project_environment

    load_project_environment()

    from strategy_momentum_breakout.forward import ForwardStore, ForwardTest

    store = ForwardStore(database) if database else ForwardStore()
    test = ForwardTest(store=store)

    already = {row["session_date"] for row in
               store.rows("SELECT session_date FROM sessions")}

    recorded = test.record()
    if recorded["session"] is None:
        return {"status": "NO_SESSION", "note": recorded.get("note", "")}

    if recorded.get("skipped"):
        # The probe found nothing newer than what is already stored, so no scan
        # ran. Resolution is skipped too: a signal matures because new bars
        # appeared, and no new bars appeared.
        return {"status": "UP_TO_DATE", "session_date": recorded["session"],
                "note": recorded.get("note", ""),
                "record": _record_summary(test.report())}

    resolved = test.resolve()
    summary = test.report()

    return {
        "status": "OK",
        "session_date": recorded["session"],
        "already_recorded": recorded["session"] in already and not force,
        "signals_today": recorded["signals"],
        "signals_written": recorded["written"],
        "symbols_scanned": recorded["scanned"],
        "funnel": recorded["funnel"],
        "resolved_now": resolved["resolved"],
        "still_running": resolved["still_running"],
        "stale_config": resolved.get("stale_config", 0),
        "record": _record_summary(summary),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record and resolve CONFIRMED_VOLUME_BREAKOUT forward signals.")
    parser.add_argument("--database", default=None)
    parser.add_argument("--force", action="store_true",
                        help="Re-record even if the session is already stored. "
                             "Harmless: the store refuses to change a signal.")
    args = parser.parse_args(argv)

    try:
        result = run(database=args.database, force=args.force)
    except Exception as error:                          # noqa: BLE001 - recorded
        result = {"status": "FAILED",
                  "error": f"{type(error).__name__}: {error}"}

    # An up-to-date poll must not overwrite the status file that recorded the
    # session, or the day's evidence would be replaced by "nothing to do".
    path = None
    if result["status"] != "UP_TO_DATE" or not _status_path().exists():
        path = _write_status(result)
    if path is not None:
        result["status_file"] = str(path.relative_to(ROOT))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] in ("OK", "UP_TO_DATE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
