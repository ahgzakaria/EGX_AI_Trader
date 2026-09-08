r"""Session-hours watchdog for the Rubix collector — alert only, starts nothing.

The morning start is already automatic: the 09:10 `EGX Rubix Assisted Start`
task watches the auth frame and starts the supervisor ten seconds after a valid
one lands. What has never been covered is the supervisor dying *after* that. The
assisted-start window disables its own Start button once it has started the
collector, so nothing is watching it for the rest of the day.

This fills that gap and nothing more. It reads the supervisor's diagnostic PID
record and the Rubix database strictly read-only, and when the collector is gone
or mute it says so, loudly, once. It never authenticates, never opens a
websocket, never takes the supervisor lock, and never starts or stops a process:
restarting mid-session would need an auth frame the repository cannot produce.

    python -m scripts.watch_rubix_supervisor            # until 14:30 Cairo
    python -m scripts.watch_rubix_supervisor --once     # one poll, for checks
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.launcher_process_utils import supervisor_status          # noqa: E402
from services.rubix_supervisor_watchdog import (                      # noqa: E402
    FEED_STALL_SECONDS,
    SESSION_CLOSE,
    WatchState,
    WatchVerdict,
    alert_message,
    assess,
    in_session,
)

CAIRO = ZoneInfo("Africa/Cairo")
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "rubix_live_market.db"
DEFAULT_PID_FILE = PROJECT_ROOT / "data" / "rubix_supervisor.pid.json"
DEFAULT_LOG_FILE = PROJECT_ROOT / "logs" / "rubix_supervisor_watchdog.log"


def feed_age_seconds(database: Path) -> float | None:
    """Seconds since the newest stored quote was received, or None if unreadable.

    Read off the newest row by rowid rather than as `MAX(received_at)`:
    `received_at` is in no index, so aggregating it scans the whole 22M-row
    table and takes seconds per call, while `ORDER BY id DESC LIMIT 1` is a
    fraction of a millisecond. The connection is `mode=ro` because the Rubix
    adapter is the database's sole writer.
    """

    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro", uri=True, timeout=10
        )
        try:
            row = connection.execute(
                "SELECT received_at FROM quotes ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    try:
        received = datetime.fromisoformat(row[0])
    except ValueError:
        return None
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - received).total_seconds()


def record(log_file: Path, **fields) -> None:
    """Append one JSON line. The log is evidence; it is never read back."""

    log_file.parent.mkdir(parents=True, exist_ok=True)
    fields["timestamp"] = datetime.now(CAIRO).isoformat(timespec="seconds")
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(fields, sort_keys=True) + "\n")


def raise_alarm(message: str) -> None:
    """Make the failure impossible to miss on the operator's own desktop.

    Sound first, because it survives a minimised window and a second monitor.
    The dialog goes on a daemon thread so an unacknowledged box never stops the
    watchdog from continuing to poll -- an operator who missed one alert still
    needs the next one.
    """

    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONHAND)
    except Exception:                                     # not Windows, or no audio
        print("\a", end="", flush=True)

    def show() -> None:
        try:
            import ctypes

            #: MB_ICONERROR | MB_SETFOREGROUND | MB_SYSTEMMODAL
            ctypes.windll.user32.MessageBoxW(
                None, message, "EGX — Rubix collector", 0x10 | 0x10000 | 0x1000
            )
        except Exception:
            pass

    threading.Thread(target=show, daemon=True).start()
    print(message, flush=True)


def poll(args, state: WatchState, log_file: Path) -> WatchVerdict:
    """One observation: look, decide, and announce only a new condition."""

    moment = datetime.now(CAIRO)
    status = supervisor_status(Path(args.supervisor_pid_file))
    age = feed_age_seconds(Path(args.rubix_db_path))
    verdict = assess(
        moment=moment,
        supervisor_running=bool(status.get("running")),
        feed_age_seconds=age,
        ever_seen_alive=state.ever_seen_alive,
        stall_seconds=args.stall_seconds,
    )
    if state.observe(verdict):
        message = alert_message(verdict, feed_age_seconds=age)
        record(
            log_file, event="alert", verdict=verdict.value, message=message,
            feed_age_seconds=None if age is None else round(age, 1),
            supervisor_pid=status.get("pid"),
        )
        if not args.quiet:
            raise_alarm(message)
    return verdict


def main(argv=None) -> int:
    args = parse_args(argv)
    log_file = Path(args.log_file)
    state = WatchState()

    if args.once:
        verdict = poll(args, state, log_file)
        print(verdict.value)
        return 0 if verdict is not WatchVerdict.SUPERVISOR_LOST else 1

    record(log_file, event="watch_started", poll_seconds=args.poll_seconds)
    try:
        while True:
            verdict = poll(args, state, log_file)
            moment = datetime.now(CAIRO)
            if moment.time() > SESSION_CLOSE and not in_session(moment):
                record(log_file, event="watch_finished", reason="session closed")
                return 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        record(log_file, event="watch_finished", reason="interrupted")
        return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubix-db-path", default=str(DEFAULT_DATABASE))
    parser.add_argument("--supervisor-pid-file", default=str(DEFAULT_PID_FILE))
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE))
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--stall-seconds", type=float, default=FEED_STALL_SECONDS)
    parser.add_argument("--once", action="store_true", help="one poll, then exit")
    parser.add_argument(
        "--quiet", action="store_true",
        help="log alerts without the sound and dialog (for tests and checks)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
