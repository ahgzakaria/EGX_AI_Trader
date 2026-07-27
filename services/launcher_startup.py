"""Responsive startup primitives for the Rubix desktop launcher.

This module deliberately contains no Tk calls. Slow functions run in daemon
workers and publish immutable events to a queue which the Tk thread drains with
``root.after``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable


class StartupState(str, Enum):
    UI_READY = "UI_READY"
    PREFLIGHT_RUNNING = "PREFLIGHT_RUNNING"
    PREFLIGHT_READY = "PREFLIGHT_READY"
    STARTING_RUBIX = "STARTING_RUBIX"
    WAITING_FOR_AUTH = "WAITING_FOR_AUTH"
    STARTING_COLLECTOR = "STARTING_COLLECTOR"
    STARTING_STREAMLIT = "STARTING_STREAMLIT"
    WAITING_FOR_READINESS = "WAITING_FOR_READINESS"
    READY = "READY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class WorkerEvent:
    kind: str
    job: str
    payload: Any = None


@dataclass(frozen=True)
class TimeoutStatus:
    ok: bool
    code: str
    message: str
    elapsed_seconds: float


@dataclass(frozen=True)
class PhaseRecord:
    name: str
    started_monotonic: float
    ended_monotonic: float
    duration_seconds: float
    thread: str
    before_mainloop: bool = False
    filesystem_traversal: bool = False
    launches_subprocess: bool = False
    subprocess_timeout_seconds: float | None = None
    paths: tuple[str, ...] = ()
    rows_queried: int | None = None
    endpoint: str | None = None
    detail: str = ""


class PhaseTimeline:
    """Thread-safe, secret-free monotonic startup timing collector."""

    def __init__(self, sink: Callable[[PhaseRecord], None] | None = None):
        self._records: list[PhaseRecord] = []
        self._lock = threading.Lock()
        self._sink = sink

    def measure(self, name: str, function: Callable[[], Any], **metadata):
        started = time.monotonic()
        try:
            return function()
        finally:
            ended = time.monotonic()
            record = PhaseRecord(
                name=name,
                started_monotonic=started,
                ended_monotonic=ended,
                duration_seconds=ended - started,
                thread=threading.current_thread().name,
                **metadata,
            )
            with self._lock:
                self._records.append(record)
            if self._sink is not None:
                self._sink(record)

    def records(self) -> tuple[PhaseRecord, ...]:
        with self._lock:
            return tuple(self._records)


class AsyncJobRunner:
    """Coalescing daemon-worker runner with queue-only result delivery."""

    def __init__(self, events: queue.Queue | None = None):
        self.events = events or queue.Queue()
        self.cancel_event = threading.Event()
        self._active: set[str] = set()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def submit(self, name: str, function: Callable[[], Any]) -> bool:
        with self._lock:
            if name in self._active:
                return False
            self._active.add(name)

        def run():
            try:
                result = function()
                self.events.put(WorkerEvent("result", name, result))
            except Exception as error:  # UI receives a sanitized summary.
                self.events.put(
                    WorkerEvent(
                        "error",
                        name,
                        {
                            "type": type(error).__name__,
                            "message": str(error),
                            "exception": error,
                        },
                    )
                )
            finally:
                with self._lock:
                    self._active.discard(name)
                    self._threads.pop(name, None)
                self.events.put(WorkerEvent("finished", name))

        thread = threading.Thread(
            target=run,
            name=f"rubix-launcher-{name}",
            daemon=True,
        )
        with self._lock:
            self._threads[name] = thread
        thread.start()
        return True

    def publish(self, kind: str, job: str, payload: Any = None) -> None:
        self.events.put(WorkerEvent(kind, job, payload))

    def is_active(self, name: str) -> bool:
        with self._lock:
            return name in self._active

    def active_jobs(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._active))

    def cancel(self) -> None:
        self.cancel_event.set()

    def reset_cancel(self) -> None:
        self.cancel_event.clear()


@dataclass
class CachedHealth:
    value: Any = None
    checked_monotonic: float = 0.0
    error: str = ""


class HealthCache:
    """Short-lived cache which preserves the last good value after a failure."""

    def __init__(self):
        self._values: dict[str, CachedHealth] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl_seconds: float) -> tuple[Any, bool] | None:
        with self._lock:
            item = self._values.get(key)
            if item is None or item.value is None:
                return None
            stale = time.monotonic() - item.checked_monotonic > float(ttl_seconds)
            return item.value, stale

    def success(self, key: str, value: Any) -> None:
        with self._lock:
            self._values[key] = CachedHealth(
                value=value, checked_monotonic=time.monotonic()
            )

    def failure(self, key: str, error: str) -> None:
        with self._lock:
            old = self._values.get(key, CachedHealth())
            old.error = str(error)
            self._values[key] = old


def poll_until(
    probe: Callable[[], bool],
    *,
    timeout_seconds: float,
    cancel_event: threading.Event,
    interval_seconds: float = 0.75,
    progress: Callable[[float], None] | None = None,
    timeout_code: str = "TIMEOUT",
) -> TimeoutStatus:
    """Poll without involving Tk and return a typed cancellation/timeout result."""

    started = time.monotonic()
    deadline = started + float(timeout_seconds)
    while time.monotonic() < deadline:
        elapsed = time.monotonic() - started
        if cancel_event.is_set():
            return TimeoutStatus(False, "CANCELLED", "Startup cancelled.", elapsed)
        if probe():
            return TimeoutStatus(True, "READY", "Ready.", time.monotonic() - started)
        if progress is not None:
            progress(elapsed)
        cancel_event.wait(max(0.01, float(interval_seconds)))
    elapsed = time.monotonic() - started
    return TimeoutStatus(
        False,
        timeout_code,
        f"Status check timed out after {elapsed:.1f} seconds.",
        elapsed,
    )


def lightweight_rubix_health(
    database, *, busy_timeout_ms: int = 750, quote_after_rowid: int | None = None
) -> dict:
    """Return targeted Rubix startup health without table scans.

    The connection is read-only, waits at most ``busy_timeout_ms``, reads schema
    metadata, the newest quote by integer primary key/rowid, and only a bounded
    tail of collector events. It never runs ``integrity_check`` or aggregates
    quote/tick history.
    """

    path = Path(database).expanduser()
    if not path.is_file():
        return {
            "status": "RUBIX_UNAVAILABLE",
            "schema_valid": False,
            "collector_status": "DISCONNECTED",
            "authentication_status": "NOT_CONFIRMED",
            "symbols_received": 0,
            "reason": "Rubix database is missing",
            "query_count": 0,
            "rows_queried": 0,
        }
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    started = time.monotonic()
    query_count = 0
    rows_queried = 0
    try:
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=max(0.05, float(busy_timeout_ms) / 1000),
        )
        connection.execute(f"PRAGMA busy_timeout={max(1, int(busy_timeout_ms))}")
        connection.execute("PRAGMA query_only=ON")
        query_count += 1
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('quotes','candles_1m','feed_metrics')"
            )
        }
        rows_queried += len(tables)
        if tables != {"quotes", "candles_1m", "feed_metrics"}:
            return {
                "status": "RUBIX_UNAVAILABLE",
                "schema_valid": False,
                "collector_status": "DISCONNECTED",
                "authentication_status": "NOT_CONFIRMED",
                "symbols_received": 0,
                "reason": "Rubix database schema is incomplete",
                "query_count": query_count,
                "rows_queried": rows_queried,
                "query_duration_seconds": time.monotonic() - started,
            }
        query_count += 1
        quote = connection.execute(
            "SELECT rowid,ticker,market_timestamp,received_at FROM quotes "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        rows_queried += int(quote is not None)
        query_count += 1
        events = connection.execute(
            "SELECT observed_at,event FROM feed_metrics ORDER BY id DESC LIMIT 256"
        ).fetchall()
        rows_queried += len(events)
    except sqlite3.OperationalError as error:
        text = str(error).lower()
        reason = "Rubix database busy" if "locked" in text or "busy" in text else "Rubix database unavailable"
        return {
            "status": "RUBIX_BUSY" if "busy" in reason.lower() else "RUBIX_UNAVAILABLE",
            "schema_valid": None,
            "collector_status": "UNKNOWN",
            "authentication_status": "UNKNOWN",
            "symbols_received": 0,
            "reason": reason,
            "query_count": query_count,
            "rows_queried": rows_queried,
            "query_duration_seconds": time.monotonic() - started,
        }
    finally:
        if "connection" in locals():
            connection.close()

    latest_by_event: dict[str, str] = {}
    for observed_at, event in events:
        latest_by_event.setdefault(str(event), str(observed_at))
    connected_at = max(
        (
            latest_by_event[name]
            for name in ("connected", "reconnect_success")
            if name in latest_by_event
        ),
        default="",
    )
    disconnected_at = latest_by_event.get("disconnect", "")
    new_quote = bool(
        quote
        and (
            quote_after_rowid is None
            or int(quote[0]) > int(quote_after_rowid)
        )
    )
    # A quote received after this launch's baseline can only arrive after the
    # collector connected and Rubix acknowledged authentication. This avoids a
    # history scan for an old authentication event.
    connected = bool(
        (connected_at and connected_at > disconnected_at) or new_quote
    )
    authenticated = bool(
        "authentication_acknowledged" in latest_by_event or new_quote
    )
    ready = bool(new_quote and connected and authenticated)
    return {
        "status": "RUBIX_FRESH" if ready else "RUBIX_STARTING",
        "schema_valid": True,
        "collector_status": "CONNECTED" if connected else "DISCONNECTED",
        "authentication_status": "ACKNOWLEDGED" if authenticated else "NOT_CONFIRMED",
        # Startup needs only proof that at least one quote arrived. Full coverage
        # remains an optional background health result.
        "symbols_received": 1 if new_quote else 0,
        "symbols_requested": 0,
        "latest_quote_rowid": int(quote[0]) if quote else None,
        "latest_exchange_timestamp": quote[2] if quote else None,
        "latest_received_timestamp": quote[3] if quote else None,
        "freshness": "FRESH" if ready else "WAITING",
        "reason": "" if ready else "Waiting for Rubix authentication and first quote",
        "query_count": query_count,
        "rows_queried": rows_queried,
        "query_duration_seconds": time.monotonic() - started,
    }


def phase_record_json(record: PhaseRecord) -> str:
    """Serialize a timing record for durable logs without arbitrary payloads."""

    return json.dumps(
        {
            "name": record.name,
            "start": round(record.started_monotonic, 6),
            "end": round(record.ended_monotonic, 6),
            "duration_s": round(record.duration_seconds, 6),
            "thread": record.thread,
            "filesystem_traversal": record.filesystem_traversal,
            "launches_subprocess": record.launches_subprocess,
            "subprocess_timeout_s": record.subprocess_timeout_seconds,
            "paths": list(record.paths),
            "rows_queried": record.rows_queried,
            "endpoint": record.endpoint,
            "before_mainloop": record.before_mainloop,
            "detail": record.detail,
        },
        sort_keys=True,
    )
