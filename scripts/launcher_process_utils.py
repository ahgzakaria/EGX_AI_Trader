"""Windows-safe process and window helpers for the Rubix launcher.

This module is deliberately isolated from market-data and trading code.  It
contains only launcher lifecycle primitives that are easy to unit test.
"""

from __future__ import annotations

from datetime import datetime, timezone
import ctypes
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from urllib import error as urlerror
from urllib import request as urlrequest


# --- atomic OS-level single-instance lock primitives ------------------------
#
# The legacy `claim_pid_file` guard ("O_EXCL create, then read-back the PID")
# has a start-up race: a second process can observe the freshly-created but
# not-yet-written lock file, judge it stale, delete it, and claim its own — so
# two supervisors both start. `SingleInstanceLock` instead takes an exclusive,
# non-blocking OS lock on a dedicated lock file and holds it for the entire
# process lifetime. The OS releases the lock automatically on exit or crash, so
# two processes can never both hold it and a stale lock can never block a
# restart. The PID metadata file is diagnostic only, written atomically.

if os.name == "nt":                                     # pragma: no cover - platform
    import msvcrt

    def _lock_exclusive(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:                                                    # pragma: no cover - platform
    import fcntl

    def _lock_exclusive(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def write_json_atomic(path: Path, record: dict) -> None:
    """Write JSON via a temp file + os.replace so a reader never sees a partial file."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


class SingleInstanceLock:
    """Atomic, crash-safe single-instance guard backed by an OS file lock.

    The exclusive lock is held for the whole process lifetime; the OS drops it on
    exit or crash. `acquire()` raises `InstanceAlreadyRunning` immediately when
    another live process holds the lock. The metadata file (pid, process start
    time, executable, project root) is for diagnostics/status only — never the
    primary guard — and is written atomically.
    """

    def __init__(self, lock_path, *, label: str, meta_path=None, extra_meta=None):
        self.lock_path = Path(lock_path)
        self.meta_path = Path(meta_path) if meta_path else self.lock_path.with_suffix(".pid.json")
        self.label = label
        self.extra_meta = dict(extra_meta or {})
        self._fd = None
        self._locked = False

    def acquire(self) -> "SingleInstanceLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR)
        try:
            _lock_exclusive(fd)
        except OSError:
            os.close(fd)
            holder = read_pid_record(self.meta_path)
            pid = holder.get("pid")
            raise InstanceAlreadyRunning(
                f"{self.label} is already running"
                + (f" (PID {pid})." if pid else " (lock held).")
            )
        self._fd = fd
        self._locked = True
        self._write_meta()
        return self

    def _write_meta(self) -> None:
        record = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "process_created_at": process_creation_time(os.getpid()),
            "executable": sys.executable,
            "label": self.label,
            **self.extra_meta,
        }
        try:
            write_json_atomic(self.meta_path, record)
        except OSError:
            pass                                         # diagnostics only — never fatal

    def release(self) -> None:
        if self._fd is not None:
            try:
                if self._locked:
                    _unlock(self._fd)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            self._locked = False
        # Remove the metadata file only when it still records this process.
        record = read_pid_record(self.meta_path)
        try:
            owned = int(record.get("pid")) == os.getpid()
        except (TypeError, ValueError):
            owned = False
        if owned:
            self.meta_path.unlink(missing_ok=True)

    @property
    def locked(self) -> bool:
        return self._locked

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()
        return False


# Command-line fragments that uniquely identify a Rubix collector/supervisor
# process. Recovery/duplicate-detection matches ONLY these — never a bare
# "python.exe" — so Streamlit, pytest, training or any other Python is never hit.
_RUBIX_PROC_PATTERNS = ("rubix_collector_supervisor.py", "rubix_feed.cli")


def is_rubix_collector_process(command_line) -> bool:
    """True only for a Rubix supervisor or collector process command line."""

    text = str(command_line or "")
    return any(pattern in text for pattern in _RUBIX_PROC_PATTERNS)


def unhealthy_is_only_stale(health: dict) -> bool:
    """True when the ONLY problem is stale quotes on an otherwise-good collector.

    i.e. authenticated + connected + schema valid, but no fresh quotes. This is the
    expected state on a non-trading day (no live ticks exist) — distinct from an
    auth failure, a missing database, or a dashboard error.
    """
    if not isinstance(health, dict):
        return False
    connected = str(health.get("collector_status")) == "CONNECTED"
    authed = str(health.get("authentication_status")) in ("ACKNOWLEDGED", "VALID")
    stale = str(health.get("freshness")) == "STALE" or not health.get("symbols_received")
    dashboard_ok = health.get("dashboard_ready") is not False
    schema_ok = health.get("schema_valid") is not False
    return connected and authed and stale and dashboard_ok and schema_ok


def should_suppress_retry(health: dict, is_trading_day: bool) -> bool:
    """On a non-trading day (weekend/holiday) a connected+authenticated+stale
    collector is EXPECTED, so the launcher must not prompt a pointless Rubix retry."""
    return (not is_trading_day) and unhealthy_is_only_stale(health)


def supervisor_status(meta_path) -> dict:
    """Non-destructive status for the launcher: is a live supervisor recorded?

    Reads the diagnostic metadata and verifies the PID is alive AND matches the
    recorded process start time (so a reused PID is never mistaken for the
    supervisor). Never acquires the lock, so it cannot disturb a running instance.
    """

    record = read_pid_record(meta_path)
    if not record:
        return {"running": False, "pid": None, "record": {}}
    running = pid_record_is_current(record)
    return {"running": running, "pid": record.get("pid") if running else None,
            "record": record}


DEFAULT_WINDOW_SIZE = (1080, 820)
MIN_WINDOW_SIZE = (900, 650)
_GEOMETRY = re.compile(
    r"^\s*(?P<width>\d+)x(?P<height>\d+)(?:(?P<x>[+-]\d+)(?P<y>[+-]\d+))?\s*$"
)


class InstanceAlreadyRunning(RuntimeError):
    """Raised only when a PID file still identifies the same live process."""


def enable_windows_dpi_awareness() -> None:
    """Prevent Windows display scaling from shrinking or offsetting Tk."""

    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def safe_window_geometry(
    value: str | None,
    screen_width: int,
    screen_height: int,
    *,
    default_size: tuple[int, int] = DEFAULT_WINDOW_SIZE,
    minimum_size: tuple[int, int] = MIN_WINDOW_SIZE,
) -> str:
    """Return an on-screen geometry, replacing minimized/off-screen sentinels."""

    match = _GEOMETRY.match(str(value or ""))
    if match:
        width = int(match.group("width"))
        height = int(match.group("height"))
        x_text, y_text = match.group("x"), match.group("y")
    else:
        width, height = default_size
        x_text = y_text = None

    max_width = max(1, int(screen_width) - 40)
    max_height = max(1, int(screen_height) - 80)
    invalid_size = width < minimum_size[0] or height < minimum_size[1]
    if invalid_size:
        width, height = default_size
        x_text = y_text = None
    width = min(max(width, min(minimum_size[0], max_width)), max_width)
    height = min(max(height, min(minimum_size[1], max_height)), max_height)

    if x_text is not None and y_text is not None:
        x, y = int(x_text), int(y_text)
        visible = (
            x >= 0
            and y >= 0
            and x + 120 <= int(screen_width)
            and y + 80 <= int(screen_height)
        )
    else:
        visible = False
        x = y = 0
    if not visible:
        x = max(0, (int(screen_width) - width) // 2)
        y = max(0, (int(screen_height) - height) // 2)
    return f"{width}x{height}+{x}+{y}"


def process_creation_time(pid: int) -> float | None:
    """Return process creation epoch using Win32, without external packages."""

    if int(pid) <= 0:
        return None
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
            return None
        except OSError:
            return None
    query = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    handle = ctypes.windll.kernel32.OpenProcess(query, False, int(pid))
    if not handle:
        return None
    try:
        creation = ctypes.c_ulonglong()
        exit_time = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        ok = ctypes.windll.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        return creation.value / 10_000_000 - 11_644_473_600
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def pid_record_is_current(record: dict) -> bool:
    """Reject PID reuse by comparing the recorded and actual creation times."""

    try:
        pid = int(record.get("pid"))
    except (TypeError, ValueError):
        return False
    actual = process_creation_time(pid)
    if actual is None:
        return False
    recorded = record.get("process_created_at")
    if recorded is None:
        started = record.get("started_at")
        try:
            recorded = datetime.fromisoformat(str(started).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return False
    try:
        return abs(actual - float(recorded)) <= 30
    except (TypeError, ValueError):
        return False


def read_pid_record(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def claim_pid_file(path: Path, *, label: str) -> dict:
    """Atomically claim a PID file, removing only a proven stale record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            record = read_pid_record(path)
            if pid_record_is_current(record):
                raise InstanceAlreadyRunning(
                    f"{label} is already running (PID {record.get('pid')})."
                )
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        record = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "process_created_at": process_creation_time(os.getpid()),
            "label": label,
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
        return record
    raise InstanceAlreadyRunning(f"Could not acquire the {label} instance lock.")


def release_pid_file(path: Path, pid: int | None = None) -> None:
    """Remove a PID file only when it still belongs to this process."""

    record = read_pid_record(path)
    expected = int(pid or os.getpid())
    try:
        owned = int(record.get("pid")) == expected
    except (TypeError, ValueError):
        owned = False
    if owned:
        path.unlink(missing_ok=True)


def port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def streamlit_is_ready(port: int, timeout: float = 0.75) -> bool:
    """Use Streamlit's local health endpoint; no external network is touched."""

    try:
        with urlrequest.urlopen(
            f"http://127.0.0.1:{int(port)}/_stcore/health", timeout=timeout
        ) as response:
            return response.status == 200 and response.read(32).strip().lower() == b"ok"
    except (OSError, urlerror.URLError, ValueError):
        return False


def wait_for_streamlit(
    port: int,
    process,
    *,
    timeout: float = 45,
    probe=streamlit_is_ready,
    sleep=time.sleep,
) -> tuple[bool, str]:
    """Bounded readiness wait that fails immediately if the child exits."""

    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False, f"Streamlit exited with code {process.returncode}."
        if probe(int(port)):
            return True, f"Dashboard is ready on http://127.0.0.1:{int(port)}"
        sleep(0.25)
    return False, f"Streamlit did not become ready within {float(timeout):.0f} seconds."
