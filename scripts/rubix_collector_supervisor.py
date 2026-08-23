"""Production supervisor for the independent Rubix SQLite collector.

The supervisor never authenticates to Rubix itself.  It passes a fresh,
user-supplied temporary frame file to the external adapter, redacts child
output, restarts failures, and publishes non-secret local health evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.symbols import load_symbols
from providers.rubix_subscription import build_rubix_subscription_plan
from providers.rubix_sqlite_provider import RubixSQLiteProvider
from scripts.launcher_process_utils import (
    InstanceAlreadyRunning,
    SingleInstanceLock,
)
from core.universe import UNIVERSE_SOURCE


FEED_URL = "wss://eg-feed3.mubashertrade.com/websocket/price"
STOP_REQUESTED = False
SECRET_PATTERN = re.compile(
    r"(?i)(token|cookie|authorization|auth[_ -]?frame)\s*[:=]\s*\S+"
)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def redact(value: str) -> str:
    value = SECRET_PATTERN.sub(r"\1=[REDACTED]", str(value or ""))
    return re.sub(r"(?i)(--auth-frame-file)\s+\S+", r"\1 [REDACTED]", value)


class StructuredLog:
    def __init__(self, path: Path, max_bytes=5_000_000, backups=5):
        self.path = path
        self.max_bytes = int(max_bytes)
        self.backups = int(backups)
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields) -> None:
        self._rotate()
        payload = {
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
            "event": event,
            **{key: redact(value) if isinstance(value, str) else value for key, value in fields.items()},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def _rotate(self):
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        oldest = self.path.with_suffix(self.path.suffix + f".{self.backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_suffix(self.path.suffix + f".{index}")
            if source.exists():
                os.replace(source, self.path.with_suffix(self.path.suffix + f".{index + 1}"))
        os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))


def validate_auth_file(path: Path, max_age_minutes=15) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file() or not 0 < path.stat().st_size <= 1_000_000:
        raise ValueError("A non-empty temporary authentication-frame file is required")
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age = (datetime.now(timezone.utc) - modified).total_seconds() / 60
    if age < -1 or age > float(max_age_minutes):
        raise ValueError(f"Authentication-frame file is {max(age, 0):.1f} minutes old")
    return path


def validate_adapter(path: Path) -> Path:
    path = path.expanduser().resolve()
    missing = [name for name in ("adapter.py", "cli.py", "storage.py", "protocol.py") if not (path / name).is_file()]
    if missing:
        raise ValueError("Rubix adapter is incomplete: " + ", ".join(missing))
    return path


def database_maintenance(path: Path) -> dict:
    if not path.is_file():
        return {"integrity": "MISSING", "wal_checkpoint": None, "bytes": 0}
    try:
        with sqlite3.connect(path, timeout=15) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            checkpoint = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        return {
            "integrity": integrity,
            "wal_checkpoint": list(checkpoint) if checkpoint else None,
            "bytes": path.stat().st_size,
        }
    except sqlite3.Error as error:
        return {"integrity": "ERROR", "error": str(error), "bytes": path.stat().st_size}


class CollectorSupervisor:
    def __init__(self, args):
        self.args = args
        self.adapter = validate_adapter(Path(args.adapter))
        self.auth_file = validate_auth_file(Path(args.auth_frame_file), args.auth_max_age_minutes)
        self.database = Path(args.database).expanduser().resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.log = StructuredLog(Path(args.log_file), args.log_max_bytes, args.log_backups)
        self.pid_file = Path(args.pid_file)
        self.lock_file = Path(getattr(args, "lock_file", None)
                              or self.pid_file.with_suffix(".lock"))
        self.lock = None
        self.health_file = Path(args.health_file)
        self.child = None
        self.restarts = 0
        self.started_at = time.monotonic()
        # Wall clock, kept separately: started_at is monotonic and measures
        # uptime, so comparing it against a file's mtime compares two different
        # clocks and the staleness test silently always -- or never -- fires.
        self.started_wall = time.time()
        self.stop_file = Path(getattr(args, "stop_file", None)
                              or PROJECT_ROOT / "data" / "runtime" / "stop_requested.flag")
        self.last_maintenance = 0.0
        plan = build_rubix_subscription_plan(args.symbols, args.batch_size)
        if plan.invalid:
            raise ValueError(f"Invalid Rubix symbol mappings: {len(plan.invalid)}")
        self.symbols = list(plan.subscriptions)
        self.requested_symbols = list(plan.requested)

    def child_command(self):
        command = [
            sys.executable, "-m", "rubix_feed.cli", "--url", FEED_URL,
            "--auth-frame-file", str(self.auth_file), "--database", str(self.database),
            "--stale-seconds", str(self.args.stale_seconds),
            "--subscription-batch-size", str(self.args.batch_size),
            "--authenticated-session",
        ]
        for symbol in self.symbols:
            command.extend(("--symbol", symbol))
        return command

    def start_child(self):
        # Never run two collectors under one supervisor: a live child means the
        # existing session already owns the credential.
        if self.child is not None and self.child.poll() is None:
            self.log.emit("collector_start_skipped", reason="child already running",
                          pid=self.child.pid)
            return
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.adapter.parent) + os.pathsep + environment.get("PYTHONPATH", "")
        self.child = subprocess.Popen(
            self.child_command(), cwd=str(self.adapter.parent), env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._drain_child_output, args=(self.child,), daemon=True).start()
        self.log.emit("collector_started", pid=self.child.pid, symbols=len(self.symbols), restart=self.restarts)

    def _drain_child_output(self, child):
        if child.stdout is None:
            return
        for line in child.stdout:
            self.log.emit("collector_output", message=redact(line.rstrip()))

    def health(self):
        provider = RubixSQLiteProvider(
            self.database,
            stale_after_minutes=float(self.args.stale_seconds) / 60,
            bar_stale_after_minutes=float(self.args.bar_stale_seconds) / 60,
            expected_symbols=self.requested_symbols,
        )
        health = provider.health()
        health.update({
            "supervisor_pid": os.getpid(),
            "collector_pid": self.child.pid if self.child and self.child.poll() is None else None,
            "collector_alive": bool(self.child and self.child.poll() is None),
            "restart_count": self.restarts,
            "supervisor_uptime_seconds": round(time.monotonic() - self.started_at, 1),
            "auth_file_persisted": True,
            "auth_contents_logged": False,
        })
        return health

    def stop_child(self):
        if self.child is None or self.child.poll() is not None:
            return
        self.child.terminate()
        try:
            self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=5)
        self.log.emit("collector_stopped", return_code=self.child.returncode)

    def stop_requested_on_disk(self):
        """True when something asked for a clean stop by creating the flag.

        Checked once per heartbeat alongside the signal flag, so a stop takes
        effect within a couple of seconds and leaves through the same ``finally``
        every other exit uses -- child stopped, database checkpointed, health
        file marked, lock released.

        A flag written *before* this run started is ignored and deleted. The
        alternative is a stale flag from yesterday's shutdown stopping tomorrow
        morning's collector a second after it starts, which would be a far
        worse failure than the one this exists to fix.
        """
        try:
            if not self.stop_file.exists():
                return False
            if self.stop_file.stat().st_mtime < self.started_wall:
                self.log.emit("stop_flag_stale_ignored", path=str(self.stop_file))
                self.stop_file.unlink(missing_ok=True)
                return False
            self.log.emit("stop_requested_by_flag", path=str(self.stop_file))
            return True
        except OSError as error:
            # An unreadable flag must never take the collector down mid-session.
            self.log.emit("stop_flag_unreadable", error=str(error))
            return False

    def run(self):
        # Atomic OS-level lock held for the whole lifetime: two supervisors can
        # never both hold it (no start-up race), and the OS drops it on exit or
        # crash so a stale lock never blocks a restart. The PID file is metadata.
        self.lock = SingleInstanceLock(
            self.lock_file, label="Rubix collector supervisor", meta_path=self.pid_file,
            extra_meta={"executable": sys.executable,
                        "project_root": str(PROJECT_ROOT),
                        "database": str(self.database)})
        try:
            self.lock.acquire()
        except InstanceAlreadyRunning as error:
            self.log.emit("supervisor_duplicate_blocked", reason=str(error))
            raise
        backoff = float(self.args.restart_backoff_seconds)
        try:
            self.start_child()
            while not STOP_REQUESTED and not self.stop_requested_on_disk():
                health = self.health()
                atomic_json(self.health_file, health)
                if self.child.poll() is not None:
                    self.log.emit("collector_exit", return_code=self.child.returncode)
                    if self.restarts >= self.args.max_restarts:
                        self.log.emit("supervisor_failed", reason="restart limit reached")
                        return 2
                    self.restarts += 1
                    time.sleep(min(backoff * self.restarts, 60))
                    self.start_child()
                now = time.monotonic()
                if now - self.last_maintenance >= self.args.maintenance_seconds:
                    self.last_maintenance = now
                    self.log.emit("database_maintenance", **database_maintenance(self.database))
                time.sleep(self.args.heartbeat_seconds)
            return 0
        finally:
            self.stop_child()
            maintenance = database_maintenance(self.database)
            self.log.emit("supervisor_shutdown", **maintenance)
            atomic_json(self.health_file, {**self.health(), "shutdown": True, "database": maintenance})
            if self.lock is not None:
                self.lock.release()                      # OS lock + metadata file


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--auth-frame-file", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--symbols", default=str(PROJECT_ROOT / UNIVERSE_SOURCE))
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--stale-seconds", type=int, default=60)
    parser.add_argument("--bar-stale-seconds", type=int, default=120)
    parser.add_argument("--auth-max-age-minutes", type=int, default=15)
    parser.add_argument("--heartbeat-seconds", type=float, default=2)
    parser.add_argument("--maintenance-seconds", type=float, default=300)
    parser.add_argument("--max-restarts", type=int, default=20)
    parser.add_argument("--restart-backoff-seconds", type=float, default=2)
    parser.add_argument("--pid-file", default=str(PROJECT_ROOT / "data" / "rubix_supervisor.pid.json"))
    parser.add_argument("--lock-file", default=str(PROJECT_ROOT / "data" / "rubix_supervisor.lock"))
    parser.add_argument("--health-file", default=str(PROJECT_ROOT / "data" / "rubix_supervisor_health.json"))
    parser.add_argument("--log-file", default=str(PROJECT_ROOT / "logs" / "rubix_supervisor.log"))
    parser.add_argument("--log-max-bytes", type=int, default=5_000_000)
    parser.add_argument("--log-backups", type=int, default=5)
    # A file another process can create to ask for a clean stop. Windows does
    # not deliver SIGTERM, and closing the console window kills the process
    # outright: in 3516 log lines this supervisor had never once reached its
    # own shutdown path, so every stop skipped the final checkpoint and left
    # the health file claiming the collector was still up.
    parser.add_argument("--stop-file",
                        default=str(PROJECT_ROOT / "data" / "runtime" / "stop_requested.flag"))
    return parser


def _request_stop(*_):
    global STOP_REQUESTED
    STOP_REQUESTED = True


def main():
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    args = build_parser().parse_args()
    try:
        return CollectorSupervisor(args).run()
    except Exception as error:
        # pythonw has no console. Persist every fatal startup failure with its
        # stage and traceback so the launcher can show a useful diagnosis.
        StructuredLog(Path(args.log_file)).emit(
            "supervisor_fatal",
            exception_type=type(error).__name__,
            message=str(error),
            traceback=traceback.format_exc(),
            supervisor_pid=os.getpid(),
            working_directory=str(Path.cwd()),
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
