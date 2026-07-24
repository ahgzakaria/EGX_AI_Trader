"""One-click Windows launcher for EGX AI Trader and TickerChart collector.

The launcher stores only explicitly allowed local configuration.  It never
reads browser state, cookies, credentials, tokens, or authentication frames.
Trading and analytical modules are started as-is in child processes.
"""

from __future__ import annotations

import atexit
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qsl, urlsplit, urlunsplit
import webbrowser


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from providers.symbol_mapping import to_tickerchart_symbol  # noqa: E402
from core.egx_session import (  # noqa: E402
    assess_quote_freshness,
    egx_session_phase,
)


CONFIG_PATH = PROJECT_ROOT / "config" / "launcher_settings.json"
LOG_DIR = PROJECT_ROOT / "logs"
ALLOWED_CONFIG_KEYS = {
    "adapter_path",
    "database_path",
    "streamer_url",
    "streamlit_port",
    "browser_auto_open",
}
DEFAULT_CONFIG = {
    "adapter_path": "",
    "database_path": str(PROJECT_ROOT / "data" / "tickerchart_quotes.sqlite3"),
    "streamer_url": "",
    "streamlit_port": 8501,
    "browser_auto_open": True,
}
COLLECTOR_START_TIMEOUT_SECONDS = 45
RECENT_QUOTE_SECONDS = 300
URL_PATTERN = re.compile(r"\b(?:wss?|https?)://[^\s\"']+", re.IGNORECASE)


class LauncherError(RuntimeError):
    """User-correctable launcher configuration or startup failure."""


@dataclass(frozen=True)
class DatabaseStatus:
    state: str
    latest_quote_time: str | None = None
    latest_exchange_time: str | None = None
    age_seconds: float | None = None
    symbol_count: int = 0
    reason: str | None = None
    latest_connection_time: str | None = None
    session_phase: str | None = None
    session_lag: int | None = None

    @property
    def recent(self):
        return self.state == "TICKERCHART_DELAYED"

    @property
    def waiting(self):
        return self.state == "TICKERCHART_WAITING_FOR_SESSION"


def validate_streamer_url(value):
    """Accept only encrypted TickerChart WebSocket endpoints."""

    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
    except ValueError as error:
        raise LauncherError(f"Invalid streamer URL: {error}") from error
    if parsed.scheme.lower() != "wss":
        raise LauncherError("Streamer URL must use wss://")
    if parsed.username or parsed.password:
        raise LauncherError("Streamer URL must not contain embedded credentials")
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = any(
        host == domain or host.endswith("." + domain)
        for domain in ("tickerchart.net", "tickerchart.com")
    )
    if not allowed:
        raise LauncherError("Streamer host must belong to tickerchart.net or tickerchart.com")
    normalized_path = parsed.path.rstrip("/").lower()
    if normalized_path not in {"/ws", "/streamhubws"}:
        raise LauncherError(
            "TickerChart streamer URL path must be /ws/ or /streamhubws/"
        )
    sensitive_keys = {
        "access_token", "auth", "authorization", "cookie", "key",
        "password", "secret", "session", "sessionid", "sig", "signature", "token",
    }
    present = {key.lower() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    forbidden = sorted(present & sensitive_keys)
    if forbidden:
        raise LauncherError(
            "Streamer URL query contains a prohibited credential/token field: "
            + ", ".join(forbidden)
        )
    return text


def redact_url(value):
    """Remove query and fragment values before a URL reaches launcher logs."""

    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return "<redacted-url>"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def redact_log_text(text):
    return URL_PATTERN.sub(lambda match: redact_url(match.group(0)), str(text))


def validate_project(project_root=PROJECT_ROOT):
    root = Path(project_root).resolve()
    required = (
        root / "app.py",
        root / "data" / "symbols.csv",
        root / "venv" / "Scripts" / "python.exe",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise LauncherError("Project is incomplete; missing: " + ", ".join(missing))
    return root


def validate_adapter_path(value):
    path = Path(str(value or "").strip()).expanduser()
    if not str(value or "").strip() or not path.is_dir():
        raise LauncherError("Adapter path does not exist")
    required = ("adapter.py", "protocol.py", "storage.py")
    missing = [name for name in required if not (path / name).is_file()]
    if missing:
        raise LauncherError("Adapter path is missing: " + ", ".join(missing))
    return path.resolve()


def validate_database_path(value):
    text = str(value or "").strip()
    if not text:
        raise LauncherError("Database path is required")
    path = Path(text).expanduser()
    if path.exists() and path.is_dir():
        raise LauncherError("Database path points to a directory")
    if path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
        raise LauncherError("Database file must end in .sqlite, .sqlite3, or .db")
    return path.resolve()


def validate_adapter_runtime(python_executable, adapter_path, runner=None):
    """Prove the selected venv can import and start the collector CLI."""

    runner = runner or subprocess.run
    try:
        environment = os.environ.copy()
        environment["TICKERCHART_ADAPTER_PATH"] = str(adapter_path)
        result = runner(
            [
                str(python_executable),
                str(PROJECT_ROOT / "scripts" / "run_tickerchart_collector.py"),
                "--help",
            ],
            cwd=str(PROJECT_ROOT), env=environment, capture_output=True, text=True,
            timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise LauncherError(f"Collector runtime preflight failed: {error}") from error
    if result.returncode != 0:
        detail = redact_log_text((result.stderr or result.stdout or "unknown error").strip())
        raise LauncherError(f"Collector runtime is not usable: {detail}")
    return True


def normalize_config(values):
    """Whitelist persisted values so accidental secret fields are discarded."""

    source = values if isinstance(values, dict) else {}
    result = dict(DEFAULT_CONFIG)
    for key in ALLOWED_CONFIG_KEYS:
        if key in source:
            result[key] = source[key]
    result["adapter_path"] = str(result.get("adapter_path") or "")
    result["database_path"] = str(result.get("database_path") or "")
    result["streamer_url"] = str(result.get("streamer_url") or "")
    try:
        port = int(result.get("streamlit_port", 8501))
    except (TypeError, ValueError):
        port = 8501
    result["streamlit_port"] = port if 1024 <= port <= 65535 else 8501
    result["browser_auto_open"] = bool(result.get("browser_auto_open", True))
    return result


def load_launcher_config(path=CONFIG_PATH):
    path = Path(path)
    if not path.is_file():
        return dict(DEFAULT_CONFIG)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return normalize_config(json.load(handle))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def save_launcher_config(values, path=CONFIG_PATH):
    """Atomically persist only the allowed non-secret launcher settings."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_config(values)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(normalized, handle, indent=4)
    temporary.replace(path)
    return normalized


def inspect_database(path, recent_seconds=300, now=None):
    """Inspect adapter state using EGX-session time, not raw calendar age."""

    database = Path(path)
    if not database.is_file():
        return DatabaseStatus("TICKERCHART_UNAVAILABLE", reason="database not created")
    try:
        uri = f"file:{database.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "quotes" not in tables:
                return DatabaseStatus(
                    "TICKERCHART_UNAVAILABLE", reason="quotes table missing"
                )
            row = connection.execute(
                """SELECT MAX(received_at), MAX(exchange_timestamp),
                          COUNT(DISTINCT ticker), COUNT(*) FROM quotes"""
            ).fetchone()
            connection_row = None
            if "metrics" in tables:
                connection_row = connection.execute(
                    """SELECT MAX(recorded_at) FROM metrics
                       WHERE name IN ('connect_success', 'reconnect_success')"""
                ).fetchone()
    except sqlite3.Error as error:
        return DatabaseStatus("TICKERCHART_UNAVAILABLE", reason=f"database error: {error}")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    phase = egx_session_phase(current)
    latest_connection = connection_row[0] if connection_row and connection_row[0] else None
    if not row or not row[0] or not row[3]:
        # An authenticated WebSocket can be healthy while a closed EGX session
        # legitimately emits no quote frames.  This is readiness, not data.
        if latest_connection and phase != "OPEN":
            return DatabaseStatus(
                "TICKERCHART_WAITING_FOR_SESSION",
                reason=f"collector connected; EGX phase is {phase}; waiting for first quote",
                latest_connection_time=_parse_timestamp(latest_connection).isoformat(),
                session_phase=phase,
            )
        return DatabaseStatus(
            "TICKERCHART_UNAVAILABLE",
            reason=(
                "collector connected but no quotes arrived during the open EGX session"
                if latest_connection else "database contains no quotes"
            ),
            latest_connection_time=(
                _parse_timestamp(latest_connection).isoformat() if latest_connection else None
            ),
            session_phase=phase,
        )
    try:
        received = _parse_timestamp(row[0])
        age = max(0.0, (current - received).total_seconds())
        exchange = _parse_timestamp(row[1] or row[0])
        freshness = assess_quote_freshness(
            received,
            exchange,
            value=current,
            open_stale_after_minutes=max(1 / 60, recent_seconds / 60),
        )
    except (TypeError, ValueError) as error:
        return DatabaseStatus("TICKERCHART_UNAVAILABLE", reason=f"invalid quote timestamp: {error}")
    state = "TICKERCHART_DELAYED" if freshness.usable else "TICKERCHART_STALE"
    return DatabaseStatus(
        state,
        latest_quote_time=received.astimezone().isoformat(),
        latest_exchange_time=str(row[1]) if row[1] else None,
        age_seconds=age,
        symbol_count=int(row[2] or 0),
        reason=freshness.reason,
        latest_connection_time=(
            _parse_timestamp(latest_connection).isoformat() if latest_connection else None
        ),
        session_phase=freshness.phase,
        session_lag=freshness.session_lag,
    )


def wait_for_recent_quote(
    path, timeout_seconds=45, recent_seconds=300, checker=None, sleep=None,
    minimum_received_at=None, allow_session_wait=False,
):
    checker = checker or inspect_database
    sleeper = sleep or time.sleep
    deadline = time.monotonic() + max(0, timeout_seconds)
    last = checker(path, recent_seconds=recent_seconds)
    while not _qualifying_startup_status(
        last, minimum_received_at, allow_session_wait
    ) and time.monotonic() < deadline:
        sleeper(min(0.25, max(0.0, deadline - time.monotonic())))
        last = checker(path, recent_seconds=recent_seconds)
    return last


def _qualifying_quote(status, minimum_received_at=None):
    if not status.recent:
        return False
    if minimum_received_at is None:
        return True
    try:
        return _parse_timestamp(status.latest_quote_time) >= minimum_received_at.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return False


def _qualifying_startup_status(status, minimum_started_at=None, allow_session_wait=False):
    if _qualifying_quote(status, minimum_started_at):
        return True
    if not (allow_session_wait and status.waiting and status.latest_connection_time):
        return False
    try:
        return _parse_timestamp(status.latest_connection_time) >= minimum_started_at.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return False


def _parse_timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class WindowsKillOnCloseJob:
    """Keep child processes tied to the launcher, including crash/forced close."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
                )]

            class BASIC_LIMIT(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class EXTENDED_LIMIT(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", BASIC_LIMIT),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return
            info = EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = 0x00002000
            ok = kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            )
            if not ok:
                kernel32.CloseHandle(handle)
                return
            self._kernel32 = kernel32
            self.handle = handle
        except Exception:
            self.handle = None

    def assign(self, process):
        if self.handle and getattr(process, "_handle", None):
            self._kernel32.AssignProcessToJobObject(self.handle, process._handle)

    def close(self):
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


class ProcessSupervisor:
    """Start, monitor, and reliably clean up collector/Streamlit children."""

    def __init__(self, project_root=PROJECT_ROOT, popen_factory=None, run_command=None):
        self.project_root = Path(project_root).resolve()
        self.popen_factory = popen_factory or subprocess.Popen
        self.run_command = run_command or subprocess.run
        self.processes = {"collector": None, "streamlit": None}
        self.log_handles = {}
        self.log_paths = {}
        self.log_threads = {}
        self.job = WindowsKillOnCloseJob()

    def start(self, name, command, env, log_path):
        current = self.processes.get(name)
        if current is not None and current.poll() is None:
            raise LauncherError(f"{name} is already running")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "a", encoding="utf-8", buffering=1)
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            process = self.popen_factory(
                command,
                cwd=str(self.project_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )
        except Exception:
            handle.close()
            raise
        self.processes[name] = process
        self.log_handles[name] = handle
        self.log_paths[name] = Path(log_path)
        output = getattr(process, "stdout", None)
        if output is not None:
            thread = threading.Thread(
                target=self._pump_output,
                args=(output, handle),
                name=f"{name}-log-pump",
                daemon=True,
            )
            self.log_threads[name] = thread
            thread.start()
        self.job.assign(process)
        return process

    @staticmethod
    def _pump_output(output, handle):
        try:
            for line in output:
                handle.write(redact_log_text(line))
        except (OSError, ValueError):
            pass

    def alive(self, name):
        process = self.processes.get(name)
        return bool(process is not None and process.poll() is None)

    def pid(self, name):
        process = self.processes.get(name)
        return getattr(process, "pid", None) if process is not None else None

    def stop(self, name, graceful_timeout=5):
        process = self.processes.get(name)
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=graceful_timeout)
            except Exception:
                if os.name == "nt" and getattr(process, "pid", None):
                    self.run_command(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                else:
                    try:
                        process.kill()
                        process.wait(timeout=2)
                    except Exception:
                        pass
        output = getattr(process, "stdout", None) if process is not None else None
        if output is not None:
            try:
                output.close()
            except (OSError, ValueError):
                pass
        thread = self.log_threads.pop(name, None)
        if thread and thread.is_alive():
            thread.join(timeout=1)
        handle = self.log_handles.pop(name, None)
        if handle:
            handle.close()
        self.processes[name] = None

    def stop_all(self):
        # Stop the UI server first, then its market-data source.
        self.stop("streamlit")
        self.stop("collector")

    def close(self):
        self.stop_all()
        self.job.close()


def read_log_tail(paths, max_lines=80):
    lines = []
    for path in paths:
        if not path or not Path(path).is_file():
            continue
        try:
            with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
                lines.extend(handle.readlines()[-max_lines:])
        except OSError:
            continue
    return redact_log_text("".join(lines[-max_lines:]))


def wait_for_port(port, timeout_seconds=30):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# The interactive TickerChart+Yahoo-fallback UI (LauncherUI) was removed when the
# launchers were unified. This module now redirects to the Rubix Production Launcher
# V2 (EODHD current research + Rubix live; frozen Yahoo is backtest-only). The
# TickerChart collector utilities above are kept for run_tickerchart_collector.py
# and tickerchart_health.py; there is no operational Yahoo path here.


def _load_adapter_symbols(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        symbols = []
        for row in reader:
            value = row.get("Ticker") or row.get("Symbol") or next(iter(row.values()), "")
            if value:
                symbols.append(to_tickerchart_symbol(value))
    unique = sorted(set(symbols))
    if not unique:
        raise LauncherError("No symbols found in data/symbols.csv")
    return unique


def main(argv=None):
    """Deprecated entry point — redirects to the unified Rubix Production Launcher V2.

    Both start_egx_ai_trader.bat and start_rubix_production.bat now open the same V2
    launcher: EODHD current research, Rubix live intraday, frozen Yahoo snapshot for
    legacy backtests only. There is no Yahoo operational startup or fallback here.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if "--smoke-test" in args:
        args = ["--check" if a == "--smoke-test" else a for a in args]
    from scripts.launch_rubix_production import main as v2_main
    return v2_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
