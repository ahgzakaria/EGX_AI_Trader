"""Rubix Assisted Start — collector only. No Dashboard, no trading, no secrets.

Opens a small window that watches an auth-frame inbox, validates any frame it
finds with the existing official validator, and — on one click, or an opt-in
countdown — starts the existing headless collector supervisor and nothing else.

The human step is unchanged: perform the normal Rubix authentication/frame
export and save the JSON into the inbox. Everything around that step is what
this removes.

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
from services.rubix_assisted_start import (
    AssistedState,
    CountdownState,
    FrameDisposal,
    FrameSelection,
    build_collector_command,
    countdown_should_abort,
    dispose_frame,
    evaluate_health,
    resolve_inbox,
    scan_inbox,
)
from services.rubix_auth_assistant import DEFAULT_MAX_AGE_MINUTES


BANNER_LINES = (
    "RUBIX COLLECTOR — ASSISTED START",
    "NO DASHBOARD",
    "NO TRADING EXECUTION",
    "NO CREDENTIAL STORAGE",
)

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
        # Frames and their consumed folder belong beside the runtime data, not
        # beside the code, so the morning export target does not move when the
        # runtime worktree is recreated.
        self.runtime_root = Path(getattr(args, "runtime_root", PROJECT_ROOT)).resolve()
        self.inbox = resolve_inbox(self.runtime_root, args.inbox)
        self.consumed_dir = self.runtime_root / args.consumed_dir
        self.state: AssistedState | None = None
        self.detail = ""
        self.process = None
        self.started_at: datetime | None = None
        self.countdown = CountdownState(
            enabled=bool(args.auto_start), seconds=float(args.countdown_seconds)
        )

    # -- detection ---------------------------------------------------------

    def scan(self):
        return scan_inbox(
            self.inbox,
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
        return dispose_frame(
            frame, FrameDisposal(self.args.disposal), consumed_dir=self.consumed_dir
        )


def render_text_status(session: AssistedStartSession, scan) -> str:
    """Console rendering — identical information to the window."""

    lines = ["=" * 60]
    lines.extend(f"  {line}" for line in BANNER_LINES)
    lines.append("=" * 60)
    lines.append(f"  inbox        : {session.inbox}")
    lines.append(f"  selection    : {scan.selection.value}")
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
    ttk.Label(
        frame,
        text=(
            "Perform your normal Rubix authentication export and save the JSON "
            f"into:\n{session.inbox}"
        ),
        justify="left",
    ).pack(anchor="w", pady=(12, 8))

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
        status.set(f"{scan.selection.value} — {scan.detail}")
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    # Code location and runtime-data location are separate concerns. A scheduled
    # task runs this file from a dedicated runtime worktree, but the collector's
    # database, universe, PID file and lock live in ONE canonical place. Deriving
    # them from the code location would point the supervisor at an empty database
    # in the worktree and - far worse - at a PID/lock file the running supervisor
    # does not use, defeating single-instance protection and allowing a second
    # collector alongside the live one.
    parser.add_argument(
        "--runtime-root", default=os.environ.get("RUBIX_RUNTIME_ROOT", str(PROJECT_ROOT)),
        help="canonical root holding data/ and logs/ (defaults to this checkout)",
    )
    known, _ = parser.parse_known_args(argv)
    runtime_root = Path(known.runtime_root).resolve()

    parser.add_argument("--inbox", default=os.environ.get("RUBIX_AUTH_INBOX", DEFAULT_INBOX))
    parser.add_argument("--consumed-dir", default=DEFAULT_CONSUMED)
    parser.add_argument("--preferences", default=DEFAULT_PREFERENCES)
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--adapter-path", default=os.environ.get("RUBIX_ADAPTER_PATH", ""))
    parser.add_argument("--database", default=str(runtime_root / "data" / "rubix_live_market.db"))
    parser.add_argument("--symbols", default=str(runtime_root / "data" / "universe.json"))
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
