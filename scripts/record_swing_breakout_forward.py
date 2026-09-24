"""Record what Swing Breakout and Breakout Watch named today, and score what matured.

    venv\\Scripts\\python.exe scripts\\record_swing_breakout_forward.py

Run it after the close, the way `record_confirmed_breakout_forward.py` is run.
Re-running it on the same session writes nothing new: the store is append-only
and its triggers refuse a rewrite, so a second run is a no-op rather than a
duplicate.

Both pages render and forget. Until this existed neither rule could be scored
on its live record: a candidate named on a Tuesday left no trace by Wednesday.
This places no order and changes no rule — it calls the same entry points the
pages call and writes down what they said.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STATUS_DIRECTORY = PROJECT_ROOT / "data" / "automation_status"


def _status_path(day=None):
    day = day or datetime.now(timezone.utc).astimezone().date().isoformat()
    return STATUS_DIRECTORY / f"swing_breakout_forward_{day}.json"


def _write_status(result):
    STATUS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = _status_path()
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return path


def run(database=None):
    from core.environment import load_project_environment

    load_project_environment()

    from services.swing_breakout_forward import SwingForwardStore, SwingForwardTest

    from services.swing_breakout import load_universe_histories

    store = SwingForwardStore(database) if database else SwingForwardStore()
    test = SwingForwardTest(store=store)

    # Read once, then replay any session a missed daily run skipped before
    # recording the newest. Both use the same histories, each cut at its day.
    failures = {}
    histories = load_universe_histories(
        on_error=lambda symbol, reason: failures.setdefault(symbol, reason))
    caught_up = test.record_missed(histories) if histories else []

    recorded = test.record(histories=histories, failures=failures)
    if recorded["session"] is None:
        return {"status": "NO_SESSION", "note": recorded.get("note", ""),
                "caught_up": [r.get("session") for r in caught_up]}

    resolved = test.resolve()
    return {
        "status": "OK",
        "session_date": recorded["session"],
        "caught_up": [r.get("session") for r in caught_up],
        "swing_candidates": recorded["candidates"],
        "swing_written": recorded["candidates_written"],
        "watch_candidates": recorded["watch"],
        "watch_written": recorded["watch_written"],
        "symbols_considered": recorded["symbols_considered"],
        "unreadable": recorded["unreadable"],
        **{key: resolved[key] for key in sorted(resolved)},
        "record": test.report(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", default=None)
    args = parser.parse_args(argv)

    try:
        result = run(database=args.database)
    except Exception as error:                          # noqa: BLE001 - recorded
        result = {"status": "FAILED",
                  "error": f"{type(error).__name__}: {error}"}

    path = _write_status(result)
    print(json.dumps(result, indent=2, default=str))
    print(f"\nstatus written to {path}")
    return 0 if result["status"] in {"OK", "NO_SESSION"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
