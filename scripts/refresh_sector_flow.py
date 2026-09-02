"""Daily refresh of the sector liquidity history.

    venv\\Scripts\\python.exe scripts\\refresh_sector_flow.py

Why this exists: `scripts/build_sector_flow.py` is a manual, ~20-minute full
rebuild, and nothing ran it. A feature that needs a manual rebuild to stay
current goes stale silently -- the coverage guard refuses to rank a half-observed
session, but it has nothing to say about a build that is itself two days old.

What makes a daily run cheap is not splitting the work but skipping it. On every
weekend, every holiday, and every run after the day's first, no EGX session has
completed since the last build, so there is nothing to derive and the script
exits in under a second. Only a genuinely missing session pays the twenty
minutes.

The rebuild is deliberately whole rather than incremental. Every sector's share
is a fraction of that session's market total, and RVOL scores a session against
its own trailing median, so appending sessions measured on a different source
basis than the ones before them would put a discontinuity inside the very
comparison those numbers exist to make. The saving comes from not running, not
from running partially.

Run daily after the EGX close (Sun-Thu, ~15:30 Cairo) via Task Scheduler.
Records only: it places no order and changes no strategy parameter.
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

from core.environment import load_project_environment  # noqa: E402
from sector_flow.builder import (  # noqa: E402
    DEFAULT_DATABASE,
    _expected_session,
    build,
    latest_build_metadata,
    save,
    sessions_behind,
    stored_latest_session,
)

LOG_DIR = ROOT / "logs"
LOG_NAME = "sector_flow_refresh.log"
COVERAGE_REPORT = "reports/sector_flow_coverage.csv"


def _log(handle, message):
    line = f"{datetime.now(timezone.utc).astimezone().isoformat()}  {message}"
    print(line)
    handle.write(line + "\n")
    handle.flush()


def refresh(*, force=False, database=DEFAULT_DATABASE, coverage_report=COVERAGE_REPORT):
    LOG_DIR.mkdir(exist_ok=True)
    with (LOG_DIR / LOG_NAME).open("a", encoding="utf-8") as handle:
        stored = stored_latest_session(database)
        behind = sessions_behind(database)
        _log(handle, f"stored latest complete session: {stored}; sessions behind: {behind}")

        if behind == 0 and not force:
            _log(handle, "UP_TO_DATE -- no completed session since the last build")
            return {"status": "UP_TO_DATE", "stored_latest_session": str(stored),
                    "sessions_behind": 0, "rebuilt": False}

        # A build can be correct and still not advance: EODHD publishes a
        # session a day late and sometimes two. Without this the scheduler
        # would pay a full rebuild on every run until the provider caught up.
        expected = _expected_session()
        attempted = (latest_build_metadata(database) or {}).get("attempted_for_session")
        if not force and expected is not None and attempted == str(expected):
            _log(handle, f"WAITING_ON_PROVIDER -- already rebuilt for {expected}; "
                         f"the provider has not published it yet")
            return {"status": "WAITING_ON_PROVIDER", "stored_latest_session": str(stored),
                    "expected_session": str(expected), "sessions_behind": behind,
                    "rebuilt": False}

        reason = "forced" if force else (
            "no history stored" if behind is None else f"{behind} session(s) missing")
        _log(handle, f"rebuilding ({reason})")
        load_project_environment()
        # Everything through the save is inside this, not just the build. On
        # 2026-09-02 the 16:00 run logged "rebuilding" and then nothing at all,
        # exited 1, and left the store two sessions behind. The build is the
        # part that takes eight minutes, so it is the part that looks worth
        # guarding -- but writing the coverage CSV and saving to SQLite are the
        # steps that contend with a reader, and they sat outside. A failure
        # there escaped as a traceback into a log that did not capture it, and
        # the only evidence left was a missing line.
        #
        # A run that fails must say so in its own log, which is the one place
        # that is written and flushed as it goes.
        try:
            history, outcomes, metadata = build()

            Path(coverage_report).parent.mkdir(parents=True, exist_ok=True)
            outcomes.to_csv(coverage_report, index=False)
            if history.empty:
                _log(handle, f"EMPTY -- no sector history produced; see {coverage_report}")
                return {"status": "EMPTY", "rebuilt": False}

            save(history, metadata, database)
        except Exception as error:
            _log(handle, f"FAILED -- {type(error).__name__}: {error}")
            return {"status": "FAILED", "error": f"{type(error).__name__}: {error}",
                    "rebuilt": False}

        _log(handle,
             f"OK -- {metadata['loaded_symbols']}/{metadata['classified_symbols']} symbols, "
             f"{metadata['held_symbols']} held, latest complete "
             f"{metadata['last_complete_session']}")
        return {
            "status": "OK", "rebuilt": True,
            "previous_latest_session": str(stored),
            "latest_complete_session": metadata["last_complete_session"],
            "loaded_symbols": metadata["loaded_symbols"],
            "held_symbols": metadata["held_symbols"],
            "unavailable_symbols": metadata["unavailable_symbols"],
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refresh the sector liquidity history.")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild even when no session is missing.")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    args = parser.parse_args(argv)

    result = refresh(force=args.force, database=args.database)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] in ("OK", "UP_TO_DATE", "WAITING_ON_PROVIDER") else 1


if __name__ == "__main__":
    raise SystemExit(main())
