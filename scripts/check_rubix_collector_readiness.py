"""Read-only readiness check for the existing Rubix collector. Research Only.

Answers one operator question before 09:40: **will the ORB Shadow run find a
healthy collector, or will it fail closed?**

It starts nothing, stops nothing, authenticates to nothing, opens no websocket
and never acquires the supervisor lock. It reads the existing launcher's own
status helpers and the source database read-only.

This exists because the collector cannot start unattended: the supervisor needs
an authentication frame at most 15 minutes old, and nothing in this repository
can produce one (see RUBIX_AUTOSTART_ARCHITECTURE_AUDIT.md). The next best
thing is telling the operator, early and precisely, whether today's session is
going to work.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.launcher_process_utils import supervisor_status


BANNER = """
================================================================
  RUBIX COLLECTOR READINESS — READ ONLY
----------------------------------------------------------------
  Starts nothing. Stops nothing. Authenticates to nothing.
  Opens no websocket and never takes the supervisor lock.
================================================================
""".strip()

#: Mirrors the supervisor's own default (`--auth-max-age-minutes`).
AUTH_MAX_AGE_MINUTES = 15.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Readiness:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))

    @property
    def ready(self) -> bool:
        return all(check.ok for check in self.checks)

    def as_dict(self) -> dict:
        return {
            "read_only": True,
            "ready": self.ready,
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks
            ],
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def check_supervisor(pid_file: Path, readiness: Readiness) -> None:
    """Non-destructive: reads metadata only, never touches the lock."""

    status = supervisor_status(pid_file)
    if status["running"]:
        readiness.add(
            "supervisor_running", True,
            f"a live supervisor holds the lock (PID {status['pid']})",
        )
    elif status["record"]:
        readiness.add(
            "supervisor_running", False,
            "a stale supervisor record exists but the process is not alive; "
            "the launcher clears it on the next start",
        )
    else:
        readiness.add(
            "supervisor_running", False,
            "no supervisor is running — start it from the official launcher",
        )


def check_auth_frame(path: Path | None, readiness: Readiness, *, now=None) -> None:
    """The blocker in one check: is a usable auth frame present and fresh?"""

    moment = now or _utc_now()
    if path is None:
        readiness.add(
            "auth_frame", False,
            "no --auth-frame-file supplied; the supervisor cannot start without one",
        )
        return
    if not path.is_file():
        readiness.add("auth_frame", False, f"auth frame not found: {path}")
        return
    age_minutes = (
        moment - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    ).total_seconds() / 60.0
    fresh = age_minutes <= AUTH_MAX_AGE_MINUTES
    readiness.add(
        "auth_frame", fresh,
        f"auth frame is {age_minutes:.1f} min old "
        f"(limit {AUTH_MAX_AGE_MINUTES:g}); "
        + ("fresh" if fresh else "TOO OLD — recapture it before starting"),
    )


def check_source(database: Path, readiness: Readiness, *, now=None) -> None:
    """Open the source strictly read-only and ask whether it is progressing."""

    moment = now or _utc_now()
    if not database.is_file():
        readiness.add("source_database", False, f"source not found: {database}")
        return
    try:
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro", uri=True, timeout=10
        )
        connection.execute("PRAGMA query_only=ON")
        try:
            row = connection.execute(
                "SELECT max(id), max(received_at) FROM quotes"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        readiness.add("source_database", False, f"unreadable: {error}")
        return

    max_id, latest = row if row else (None, None)
    if not max_id:
        readiness.add("source_database", False, "source contains no quote rows")
        return
    readiness.add("source_database", True, f"readable, max source id {max_id:,}")

    if not latest:
        readiness.add("source_progressing", False, "no receive timestamp recorded")
        return
    try:
        parsed = datetime.fromisoformat(str(latest))
    except ValueError:
        readiness.add("source_progressing", False, f"unparsable receive time: {latest}")
        return
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_minutes = (moment - parsed).total_seconds() / 60.0
    progressing = age_minutes <= 5.0
    readiness.add(
        "source_progressing", progressing,
        f"last row received {age_minutes:.1f} min ago; "
        + ("progressing" if progressing else "FROZEN — the feed is not writing"),
    )


def run(args) -> dict:
    readiness = Readiness()
    check_supervisor(Path(args.supervisor_pid_file), readiness)
    check_auth_frame(
        Path(args.auth_frame_file) if args.auth_frame_file else None, readiness
    )
    check_source(Path(args.rubix_db_path), readiness)
    return readiness.as_dict()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rubix-db-path",
        default=str(PROJECT_ROOT / "data" / "rubix_live_market.db"),
    )
    parser.add_argument(
        "--supervisor-pid-file",
        default=str(PROJECT_ROOT / "data" / "rubix_supervisor.pid.json"),
    )
    parser.add_argument(
        "--auth-frame-file",
        default=None,
        help="the manually captured authentication frame, if you have one",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(BANNER)
        print()
        for check in payload["checks"]:
            mark = "OK  " if check["ok"] else "FAIL"
            print(f"  [{mark}] {check['name']:20s} {check['detail']}")
        print()
        print(f"  READY: {payload['ready']}")
        if not payload["ready"]:
            print()
            print("  The ORB Shadow run will fail closed unless these are resolved.")
            print("  The collector cannot start unattended — see")
            print("  docs/audits/providers/rubix/RUBIX_AUTOSTART_ARCHITECTURE_AUDIT.md")
    # Non-zero when not ready, so a wrapper can act on it. Nothing is started.
    return 0 if payload["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
