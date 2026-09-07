r"""Rubix Assisted Start — collector only. No Dashboard, no trading, no secrets.

Opens a small window that watches the user's existing auth-frame file, validates
it in place with the existing official validator, and — on one click, or an
opt-in countdown — starts the existing headless collector supervisor and nothing
else.

The human step is unchanged: perform the normal Rubix authentication/frame
export, which already writes to C:\secure-temp\rubix-price-auth-frame.txt.
Everything around that step is what this removes — including any copying. The
frame is read where it already lives and is never copied, moved, renamed or
deleted.

An inbox-directory mode remains available for testing and future use, but the
normal workflow needs no inbox at all.

It never authenticates, never drives a browser, never opens a websocket, never
stores a credential, and never launches Streamlit. All decisions live in
``services.rubix_assisted_start`` so they are testable without a desktop.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.launcher_process_utils import supervisor_status
from core.universe import UNIVERSE_SOURCE  # noqa: E402
from services.rubix_assisted_start import (
    DEFAULT_AUTH_FRAME_PATH,
    AssistedState,
    CountdownState,
    FrameDisposal,
    FrameSelection,
    build_collector_command,
    countdown_should_abort,
    dispose_frame,
    evaluate_health,
    resolve_inbox,
    scan_frame_file,
    scan_inbox,
)
from services.rubix_auth_assistant import DEFAULT_MAX_AGE_MINUTES


BANNER_LINES = (
    "RUBIX COLLECTOR — ASSISTED START",
    "NO DASHBOARD",
    "NO TRADING EXECUTION",
    "NO CREDENTIAL STORAGE",
)

#: Watching the user's real export path is the production mode; the inbox is an
#: optional advanced mode kept for testing and future use.
WATCH_AUTH_FRAME = "auth-frame"
WATCH_INBOX = "inbox"

DEFAULT_INBOX = "data/local/rubix_auth_inbox"
DEFAULT_CONSUMED = "data/local/rubix_auth_consumed"
#: Preferences hold paths and the auto-start opt-in. Never a secret.
DEFAULT_PREFERENCES = "data/local/rubix_assisted_start_prefs.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_preferences(path: Path) -> dict:
    """Paths and the auto-start flag only — never a credential."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    allowed = {"inbox", "auto_start_enabled", "disposal", "adapter_path"}
    return {key: value for key, value in payload.items() if key in allowed}


def save_preferences(path: Path, values: dict) -> None:
    forbidden = {"password", "username", "cookie", "session", "token", "frame"}
    for key in values:
        if any(word in key.lower() for word in forbidden):
            raise ValueError(f"refusing to persist a credential-like key: {key}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(values, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class AssistedStartSession:
    """Headless core of the assisted flow. The UI renders this."""

    def __init__(self, args, *, clock=None, runner=None):
        self.args = args
        self._clock = clock or _utc_now
        # Injected so tests never spawn a real collector.
        self._runner = runner or self._spawn_supervisor
        self.runtime_root = Path(getattr(args, "runtime_root", PROJECT_ROOT)).resolve()
        self.watch_mode = getattr(args, "watch_mode", WATCH_AUTH_FRAME)
        self.auth_frame = Path(getattr(args, "auth_frame", DEFAULT_AUTH_FRAME_PATH))
        # The inbox is only resolved in the advanced mode. Resolving it here
        # would recreate the very requirement this mode exists to remove.
        self.inbox = (
            resolve_inbox(self.runtime_root, args.inbox)
            if self.watch_mode == WATCH_INBOX
            else None
        )
        self.consumed_dir = self.runtime_root / args.consumed_dir
        self.state: AssistedState | None = None
        self.detail = ""
        self.process = None
        self.started_at: datetime | None = None
        self.countdown = CountdownState(
            enabled=bool(args.auto_start), seconds=float(args.countdown_seconds)
        )

    # -- detection ---------------------------------------------------------

    @property
    def watched(self) -> Path:
        """What the user is told to look at — one file, or one directory."""

        return self.inbox if self.watch_mode == WATCH_INBOX else self.auth_frame

    def scan(self):
        if self.watch_mode == WATCH_INBOX:
            return scan_inbox(
                self.inbox,
                now=self._clock(),
                max_age_minutes=float(self.args.max_age_minutes),
            )
        return scan_frame_file(
            self.auth_frame,
            now=self._clock(),
            max_age_minutes=float(self.args.max_age_minutes),
        )

    def frame_summary(self, scan) -> dict | None:
        if scan.selected is None:
            return None
        return scan.selected.summary(self._clock(), float(self.args.max_age_minutes))

    # -- start -------------------------------------------------------------

    def _spawn_supervisor(self, command):  # pragma: no cover - real process
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        return subprocess.Popen(
            command.as_list(), cwd=command.working_directory,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )

    def start(self, scan) -> AssistedState:
        """Start the collector, or explain why not. Never starts a second one."""

        if scan.selection is not FrameSelection.FRAME_SELECTED or scan.selected is None:
            self.state = AssistedState.AUTH_FRAME_REJECTED
            self.detail = scan.detail
            return self.state

        # The existing non-destructive check, before anything is spawned.
        status = supervisor_status(Path(self.args.pid_file))
        if status["running"]:
            self.state = AssistedState.INSTANCE_ALREADY_RUNNING
            self.detail = f"a supervisor already holds the lock (PID {status['pid']})"
            return self.state

        command = build_collector_command(
            python_executable=self.args.python_exe,
            # The supervisor script lives beside this file; the data it
            # touches is addressed by the absolute paths below.
            project_root=PROJECT_ROOT,
            adapter_path=Path(self.args.adapter_path),
            auth_frame=scan.selected.path,
            database=Path(self.args.database),
            symbols_path=Path(self.args.symbols),
            pid_file=Path(self.args.pid_file),
            lock_file=Path(self.args.lock_file),
            log_file=Path(self.args.log_file),
            batch_size=int(self.args.batch_size),
        )
        try:
            self.process = self._runner(command)
        except Exception as error:  # noqa: BLE001 - surfaced, never swallowed
            self.state = AssistedState.START_FAILED
            self.detail = f"{type(error).__name__}: {error}"
            return self.state
        self.started_at = self._clock()
        self.state = AssistedState.STARTING
        self.detail = "supervisor launched; waiting for the source to advance"
        return self.state

    # -- health ------------------------------------------------------------

    def check_health(self, readiness: dict) -> AssistedState:
        elapsed = (
            (self._clock() - self.started_at).total_seconds()
            if self.started_at
            else 0.0
        )
        status = supervisor_status(Path(self.args.pid_file))
        outcome = evaluate_health(
            readiness,
            supervisor_running=bool(status["running"]),
            elapsed_seconds=elapsed,
            timeout_seconds=float(self.args.health_timeout_seconds),
        )
        self.state = outcome.state
        self.detail = outcome.detail
        return self.state

    def dispose(self, frame: Path) -> str:
        """In the production mode the frame is the user's file. It is not ours.

        Watching a file in place means disposal would act on the user's own
        long-lived export path rather than on a copy this code made, so moving
        or deleting it is refused outright here - not merely defaulted off.
        """

        if self.watch_mode != WATCH_INBOX:
            return "frame left untouched (watched in place; never copied or moved)"
        return dispose_frame(
            frame, FrameDisposal(self.args.disposal), consumed_dir=self.consumed_dir
        )


def render_text_status(session: AssistedStartSession, scan) -> str:
    """Console rendering — identical information to the window."""

    lines = ["=" * 60]
    lines.extend(f"  {line}" for line in BANNER_LINES)
    lines.append("=" * 60)
    lines.append("  Auth frame:")
    lines.append(f"    {session.watched}")
    lines.append("")
    lines.append(f"  Status       : {scan.status.value}")
    lines.append(f"  detail       : {scan.detail}")
    summary = session.frame_summary(scan)
    if summary:
        lines.append("")
        lines.append(f"  file         : {summary['filename']}")
        lines.append(f"  validated at : {summary['validated_timestamp_utc']}")
        lines.append(f"  timestamp    : {summary['timestamp_source']}")
        lines.append(f"  age          : {summary['age_seconds']}s")
        lines.append(f"  remaining    : {summary['remaining_seconds']}s")
        lines.append(f"  validation   : {summary['validation_result']}")
    for candidate in scan.rejected:
        detail = candidate.summary(session._clock(), float(session.args.max_age_minutes))
        lines.append(
            f"  rejected     : {detail['filename']} -> "
            f"{detail['validation_result']}: {detail['rejection_reason']}"
        )
    lines.append("")
    lines.append(
        "  auto-start   : "
        + ("ENABLED (opt-in)" if session.countdown.enabled else "disabled (default)")
    )
    return "\n".join(lines)


def run_headless(args) -> int:
    """One detection pass, printed. Starts nothing unless --start is given."""

    session = AssistedStartSession(args)
    scan = session.scan()
    print(render_text_status(session, scan))
    if not args.start:
        print()
        print("  (detection only; pass --start to launch the collector)")
        return 0 if scan.ready else 1
    state = session.start(scan)
    print()
    print(f"  state        : {state.value}")
    print(f"  detail       : {session.detail}")
    return 0 if state in (AssistedState.STARTING, AssistedState.INSTANCE_ALREADY_RUNNING) else 1


def run_ui(args) -> int:  # pragma: no cover - requires a desktop session
    """Thin tkinter shell over AssistedStartSession. Renders, decides nothing."""

    import tkinter as tk
    from tkinter import ttk

    session = AssistedStartSession(args)
    root = tk.Tk()
    root.title("Rubix Collector — Assisted Start")
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text=BANNER_LINES[0], font=("Segoe UI", 16, "bold")).pack(anchor="w")
    for line in BANNER_LINES[1:]:
        ttk.Label(frame, text=line, foreground="#a00").pack(anchor="w")
    # The user is never asked to browse for or copy the file: the path they
    # already export to is shown, and it is read exactly there.
    ttk.Label(
        frame, text="Auth frame:", font=("Segoe UI", 10, "bold"),
    ).pack(anchor="w", pady=(12, 0))
    ttk.Label(frame, text=str(session.watched), justify="left").pack(anchor="w")
    ttk.Label(
        frame,
        text=(
            "Perform your normal Rubix authentication export. This file is read "
            "where it is — never copied, moved or deleted."
        ),
        justify="left",
    ).pack(anchor="w", pady=(4, 8))

    status = tk.StringVar(value="Scanning…")
    ttk.Label(frame, textvariable=status, justify="left").pack(anchor="w")
    detail = tk.StringVar(value="")
    ttk.Label(frame, textvariable=detail, justify="left").pack(anchor="w", pady=(4, 12))

    start_button = ttk.Button(frame, text="START RUBIX COLLECTOR", state="disabled")
    start_button.pack(anchor="w", ipadx=20, ipady=8)
    cancel_button = ttk.Button(frame, text="Cancel countdown", state="disabled")
    cancel_button.pack(anchor="w", pady=(6, 0))

    def refresh():
        scan = session.scan()
        summary = session.frame_summary(scan)
        status.set(f"{scan.status.value} — {scan.detail}")
        if summary:
            detail.set(
                f"{summary['filename']}   validated {summary['validated_timestamp_utc']}"
                f"   age {summary['age_seconds']}s"
                f"   remaining {summary['remaining_seconds']}s"
                f"   [{summary['timestamp_source']}]"
            )
            start_button.configure(state="normal", command=lambda: do_start(scan))
            if session.countdown.enabled and not session.countdown.started:
                abort = countdown_should_abort(scan)
                if abort:
                    session.countdown.cancel()
                else:
                    cancel_button.configure(
                        state="normal", command=session.countdown.cancel
                    )
                    if session.countdown.tick(1.0):
                        do_start(scan)
                    else:
                        status.set(
                            f"Auto-start in {session.countdown.remaining:.0f}s — "
                            "press Cancel to stop"
                        )
        else:
            detail.set("")
            start_button.configure(state="disabled")
        root.after(1000, refresh)

    def do_start(scan):
        state = session.start(scan)
        status.set(state.value)
        detail.set(session.detail)
        start_button.configure(state="disabled")
        cancel_button.configure(state="disabled")
        if state is AssistedState.STARTING:
            root.after(3000, lambda: root.iconify())

    root.after(200, refresh)
    root.mainloop()
    return 0


#: Code location and runtime-data location are separate concerns. A scheduled
#: task runs this file from a dedicated runtime worktree, but the collector's
#: database, universe, PID file and lock live in ONE canonical place. Deriving
#: them from the code location would point the supervisor at an empty database
#: in the worktree and - far worse - at a PID/lock file the running supervisor
#: does not use, defeating single-instance protection and allowing a second
#: collector alongside the live one.
_RUNTIME_ROOT_HELP = "canonical root holding data/ and logs/ (defaults to this checkout)"


def _runtime_root_from(argv):
    """Read --runtime-root first, because other defaults are derived from it.

    A pre-parser rather than ``parse_known_args`` on the real parser: the real
    parser owns ``-h``, and letting it answer help here would print a usage
    line listing this one option and exit before the rest are even declared.
    """

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument(
        "--runtime-root",
        default=os.environ.get("RUBIX_RUNTIME_ROOT", str(PROJECT_ROOT)),
    )
    known, _ = pre.parse_known_args(argv)
    return Path(known.runtime_root).resolve()


def parse_args(argv=None):
    runtime_root = _runtime_root_from(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-root", default=str(runtime_root), help=_RUNTIME_ROOT_HELP,
    )

    # The production default: the file the Rubix export has always written.
    # Nothing needs to be copied anywhere for this to work.
    parser.add_argument(
        "--auth-frame",
        default=os.environ.get("RUBIX_AUTH_FRAME", str(DEFAULT_AUTH_FRAME_PATH)),
        help="auth frame to watch in place (production default)",
    )
    parser.add_argument(
        "--watch-mode", choices=(WATCH_AUTH_FRAME, WATCH_INBOX), default=WATCH_AUTH_FRAME,
        help="watch one known file (default) or, as an advanced mode, a directory",
    )
    parser.add_argument("--inbox", default=os.environ.get("RUBIX_AUTH_INBOX", DEFAULT_INBOX))
    parser.add_argument("--consumed-dir", default=DEFAULT_CONSUMED)
    parser.add_argument("--preferences", default=DEFAULT_PREFERENCES)
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--adapter-path", default=os.environ.get("RUBIX_ADAPTER_PATH", ""))
    parser.add_argument("--database", default=str(runtime_root / "data" / "rubix_live_market.db"))
    # UNIVERSE_SOURCE, not data/universe.json. That file has never existed:
    # the project's universe is data/universe/egx_universe.csv, which is what
    # launch_rubix_production.py and every other entry point passes.
    #
    # The supervisor refuses to start without a readable symbols source, so this
    # default produced supervisor_fatal every time the window was used without
    # an explicit --symbols. On 2026-09-07 it fired twice at 09:36:36 before
    # something started the collector correctly at 09:36:59, and two fatal
    # tracebacks in the log every morning is how a real one stops being read.
    parser.add_argument("--symbols",
                        default=str(runtime_root / UNIVERSE_SOURCE))
    parser.add_argument("--pid-file", default=str(runtime_root / "data" / "rubix_supervisor.pid.json"))
    parser.add_argument("--lock-file", default=str(runtime_root / "data" / "rubix_supervisor.lock"))
    parser.add_argument("--log-file", default=str(runtime_root / "logs" / "rubix_supervisor.log"))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-age-minutes", type=float, default=DEFAULT_MAX_AGE_MINUTES)
    parser.add_argument("--health-timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--auto-start", action="store_true",
        help="opt in to a visible countdown after one unambiguous valid frame",
    )
    parser.add_argument("--countdown-seconds", type=float, default=5.0)
    parser.add_argument(
        "--disposal", default=FrameDisposal.LEAVE_UNTOUCHED.value,
        choices=[item.value for item in FrameDisposal],
    )
    parser.add_argument("--headless", action="store_true", help="text mode, no window")
    parser.add_argument("--start", action="store_true", help="headless mode: actually start")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.headless:
        return run_headless(args)
    return run_ui(args)  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
