r"""What is up right now, as JSON, for the start and stop buttons.

Every verdict here comes from the code that already owns it -- the supervisor's
own PID record, the official auth-frame validator -- rather than from a second
opinion written for a launcher. A button that decides "the collector is running"
by a different rule than the collector uses will eventually disagree with it,
and the disagreement will surface as a duplicate collector or a morning where
nothing starts.

Reads only. It opens no database, acquires no lock, and starts nothing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.launcher_process_utils import supervisor_status
from services.rubix_assisted_start import DEFAULT_AUTH_FRAME_PATH, scan_frame_file


def collector_state(pid_file: Path) -> dict:
    """Whether a supervisor is genuinely alive.

    ``supervisor_status`` checks the PID *and* the recorded process start time,
    so a reused PID is never mistaken for the collector. It never touches the
    lock, so asking cannot disturb a running instance.
    """
    try:
        status = supervisor_status(pid_file)
        return {"running": bool(status.get("running")), "pid": status.get("pid")}
    except Exception as error:  # noqa: BLE001 - a probe must always answer
        return {"running": None, "pid": None, "error": str(error)}


def auth_frame_state(frame_path: Path) -> dict:
    """Whether this morning's Rubix export is present and still fresh.

    ``running is None`` and ``usable is None`` mean "could not tell", which the
    caller must not read as "no". The collector cannot start without a fresh
    frame, and a launcher that treats an unreadable answer as a green light
    starts a supervisor that dies on its first validation.
    """
    try:
        scan = scan_frame_file(frame_path)
        # ``ready`` is the scan's own verdict. Re-deriving it here by comparing
        # the selection enum would be a second copy of the rule, free to drift.
        return {
            "usable": scan.ready,
            "reason": scan.detail,
            "status": str(getattr(scan.status, "name", scan.status)),
            "path": str(frame_path),
        }
    except Exception as error:  # noqa: BLE001
        return {"usable": None, "reason": f"could not check: {error}",
                "path": str(frame_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid-file",
                        default=str(PROJECT_ROOT / "data" / "rubix_supervisor.pid.json"))
    parser.add_argument("--auth-frame", default=str(DEFAULT_AUTH_FRAME_PATH))
    args = parser.parse_args()

    print(json.dumps({
        "collector": collector_state(Path(args.pid_file)),
        "auth_frame": auth_frame_state(Path(args.auth_frame)),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
