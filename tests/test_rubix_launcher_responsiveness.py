"""Headless proofs for the Rubix launcher's asynchronous startup contract."""

from __future__ import annotations

import inspect
import queue
import sqlite3
import sys
import threading
import time
import types

import pytest

from services.launcher_startup import (
    AsyncJobRunner,
    StartupState,
    TimeoutStatus,
    WorkerEvent,
    lightweight_rubix_health,
    poll_until,
)
import scripts.launch_rubix_production as launcher


def _event(events, *, kind, job, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = events.get(timeout=max(0.01, deadline - time.monotonic()))
        if item.kind == kind and item.job == job:
            return item
    raise AssertionError(f"missing {kind}/{job}")


def test_slow_worker_never_runs_on_main_thread():
    events = queue.Queue()
    jobs = AsyncJobRunner(events)
    main = threading.get_ident()
    assert jobs.submit("slow", lambda: (time.sleep(0.05), threading.get_ident())[1])
    result = _event(events, kind="result", job="slow")
    assert result.payload != main


def test_duplicate_jobs_are_coalesced_for_start_and_refresh():
    jobs = AsyncJobRunner()
    release = threading.Event()
    for name in ("rubix_start", "status_refresh"):
        assert jobs.submit(name, lambda: release.wait(1)) is True
        assert jobs.submit(name, lambda: None) is False
    release.set()


def test_worker_exception_is_surfaced_as_safe_event():
    jobs = AsyncJobRunner()

    def fail():
        raise RuntimeError("sanitized failure")

    jobs.submit("status_refresh", fail)
    event = _event(jobs.events, kind="error", job="status_refresh")
    assert event.payload["type"] == "RuntimeError"
    assert event.payload["message"] == "sanitized failure"


def test_progress_events_are_thread_safe_and_queue_driven():
    jobs = AsyncJobRunner()
    jobs.publish(
        "state",
        "startup",
        {"state": StartupState.WAITING_FOR_AUTH, "message": "Waiting…"},
    )
    event = jobs.events.get_nowait()
    assert isinstance(event, WorkerEvent)
    assert event.payload["state"] is StartupState.WAITING_FOR_AUTH


def test_cancellation_during_authentication_wait_is_typed_and_fast():
    cancel = threading.Event()
    cancel.set()
    result = poll_until(
        lambda: False,
        timeout_seconds=30,
        cancel_event=cancel,
        interval_seconds=0.01,
    )
    assert isinstance(result, TimeoutStatus)
    assert (result.ok, result.code) == (False, "CANCELLED")
    assert result.elapsed_seconds < 0.25


def test_readiness_timeout_is_typed():
    result = poll_until(
        lambda: False,
        timeout_seconds=0.03,
        cancel_event=threading.Event(),
        interval_seconds=0.005,
        timeout_code="STREAMLIT_TIMEOUT",
    )
    assert (result.ok, result.code) == (False, "STREAMLIT_TIMEOUT")
    assert "timed out" in result.message


def _rubix_db(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE quotes (
            id INTEGER PRIMARY KEY, ticker TEXT, market_timestamp TEXT,
            received_at TEXT
        );
        CREATE TABLE candles_1m (
            ticker TEXT, minute TEXT,
            PRIMARY KEY (ticker, minute)
        );
        CREATE TABLE feed_metrics (
            id INTEGER PRIMARY KEY, observed_at TEXT, event TEXT
        );
        INSERT INTO quotes(ticker,market_timestamp,received_at)
        VALUES ('COMI','2026-07-27T07:00:00+00:00','2026-07-27T07:00:01+00:00');
        INSERT INTO feed_metrics(observed_at,event)
        VALUES ('2026-07-27T06:59:59+00:00','authentication_acknowledged');
        INSERT INTO feed_metrics(observed_at,event)
        VALUES ('2026-07-27T07:00:00+00:00','connected');
        """
    )
    connection.commit()
    connection.close()


def test_lightweight_sqlite_health_uses_bounded_rows_and_targeted_queries(tmp_path):
    database = tmp_path / "rubix.db"
    _rubix_db(database)
    result = lightweight_rubix_health(database)
    assert result["status"] == "RUBIX_FRESH"
    assert result["query_count"] == 3
    assert result["rows_queried"] <= 259
    assert result["symbols_received"] == 1


def test_missing_rubix_database_returns_without_freezing(tmp_path):
    started = time.monotonic()
    result = lightweight_rubix_health(tmp_path / "missing.db")
    assert result["reason"] == "Rubix database is missing"
    assert time.monotonic() - started < 0.25


def test_busy_rubix_database_returns_typed_status(monkeypatch, tmp_path):
    database = tmp_path / "rubix.db"
    database.touch()

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("services.launcher_startup.sqlite3.connect", locked)
    result = lightweight_rubix_health(database)
    assert result["status"] == "RUBIX_BUSY"
    assert result["reason"] == "Rubix database busy"


def test_slow_sqlite_and_ollama_checks_do_not_stall_caller():
    jobs = AsyncJobRunner()
    main = threading.get_ident()

    def slow_check():
        time.sleep(0.1)
        return threading.get_ident()

    started = time.monotonic()
    assert jobs.submit("rubix_health", slow_check)
    assert jobs.submit("ollama_health", slow_check)
    assert time.monotonic() - started < 0.05
    results = {}
    deadline = time.monotonic() + 2
    while len(results) < 2 and time.monotonic() < deadline:
        event = jobs.events.get(timeout=1)
        if event.kind == "result":
            results[event.job] = event.payload
    assert set(results) == {"rubix_health", "ollama_health"}
    assert all(worker != main for worker in results.values())


def test_tk_constructor_finishes_before_preflight_or_status_refresh(monkeypatch):
    callbacks = []
    calls = []

    class Var:
        def __init__(self, value=None):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class Root:
        def title(self, _value):
            pass

        def winfo_screenwidth(self):
            return 1920

        def winfo_screenheight(self):
            return 1080

        def minsize(self, *_args):
            pass

        def geometry(self, value=None):
            return value or "1080x820+0+0"

        def bind(self, *_args, **_kwargs):
            pass

        def protocol(self, *_args):
            pass

        def after(self, *_args):
            pass

        def after_idle(self, callback):
            callbacks.append(callback)

    class Style:
        def theme_names(self):
            return ("default",)

        def theme_use(self, _theme):
            pass

    fake_ttk = types.SimpleNamespace(Style=Style)
    fake_tk = types.ModuleType("tkinter")
    fake_tk.Tk = Root
    fake_tk.StringVar = Var
    fake_tk.BooleanVar = Var
    fake_tk.ttk = fake_ttk
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", fake_ttk)
    monkeypatch.setattr(
        launcher.RubixAuthenticationAssistantUI,
        "_build_initial_shell",
        lambda self: calls.append("ui_built"),
    )
    monkeypatch.setattr(
        launcher.RubixAuthenticationAssistantUI,
        "_apply_auth_inspection",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        launcher.RubixAuthenticationAssistantUI,
        "refresh_status",
        lambda self: calls.append("slow_status"),
    )
    monkeypatch.setattr(launcher.atexit, "register", lambda *_args: None)
    launcher.RubixAuthenticationAssistantUI()
    assert calls == ["ui_built"]
    assert len(callbacks) == 1


def test_ui_callbacks_only_schedule_known_slow_operations():
    slow_tokens = (
        "subprocess.run",
        ".wait(",
        "wait_until_healthy(",
        "wait_until_streamlit_ready(",
        "current_research_status(",
        "check_health(",
        "supervisor.health(",
        "run_preflight(",
    )
    for method in (
        launcher.RubixAuthenticationAssistantUI.start,
        launcher.RubixAuthenticationAssistantUI.start_research_only,
        launcher.RubixAuthenticationAssistantUI.refresh_status,
        launcher.RubixAuthenticationAssistantUI.run_self_check,
        launcher.RubixAuthenticationAssistantUI.stop,
        launcher.RubixAuthenticationAssistantUI._refresh,
        launcher.RubixAuthenticationAssistantUI.close,
    ):
        source = inspect.getsource(method)
        assert not any(token in source for token in slow_tokens), method.__name__


def test_normal_startup_contains_no_recursive_heavy_directory_scan():
    source = inspect.getsource(launcher)
    for token in ("os.walk(", ".rglob(", "glob(\"**", "glob('**"):
        assert token not in source


def test_close_is_cancellation_first_and_never_calls_destroy_immediately():
    source = inspect.getsource(launcher.RubixAuthenticationAssistantUI.close)
    assert "_schedule_stop" in source
    assert "root.destroy" not in source
    assert "jobs.cancel" in inspect.getsource(
        launcher.RubixAuthenticationAssistantUI._schedule_stop
    )
