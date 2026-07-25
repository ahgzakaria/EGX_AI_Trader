"""Production-launcher tests isolated from trading and UI rendering."""

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts.launch_egx_ai_trader import (
    DatabaseStatus,
    LauncherError,
    ProcessSupervisor,
    _load_adapter_symbols,
    inspect_database,
    load_launcher_config,
    redact_log_text,
    redact_url,
    save_launcher_config,
    validate_adapter_path,
    validate_adapter_runtime,
    validate_database_path,
    validate_project,
    validate_streamer_url,
    wait_for_recent_quote,
)
from core.egx_session import assess_quote_freshness, trading_session_lag


def _adapter(path):
    path.mkdir()
    for name in ("adapter.py", "protocol.py", "storage.py"):
        (path / name).write_text("# fixture\n", encoding="utf-8")
    return path


def _quote_database(path, received):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE quotes (
                ticker TEXT, received_at TEXT, exchange_timestamp TEXT
            );
            INSERT INTO quotes VALUES ('COMI.EGY', '', '');
            """
        )
        exchange = received - timedelta(minutes=15)
        connection.execute(
            "UPDATE quotes SET received_at=?, exchange_timestamp=?",
            (received.isoformat(), exchange.isoformat()),
        )


def test_streamer_url_validation_and_log_redaction():
    valid = "wss://stream-1.delayed.tickerchart.net/ws/?version=1"
    assert validate_streamer_url(valid) == valid
    actual = "wss://delayedkse2.tickerchart.net/streamhubws/"
    assert validate_streamer_url(actual) == actual
    assert redact_url(valid) == "wss://stream-1.delayed.tickerchart.net/ws/"
    assert "version=1" not in redact_log_text(f"Connecting {valid}")

    for invalid in (
        "ws://stream.tickerchart.net/ws/",
        "wss://tickerchart.net.evil.example/ws/",
        "wss://example.com/ws/",
        "wss://user:pass@tickerchart.net/ws/",
        "wss://stream.tickerchart.net/not-websocket",
        "wss://stream.tickerchart.net/nested/streamhubws/",
        "wss://stream.tickerchart.net/ws/?session=secret",
    ):
        with pytest.raises(LauncherError):
            validate_streamer_url(invalid)


def test_path_and_project_validation(tmp_path):
    adapter = _adapter(tmp_path / "adapter")
    assert validate_adapter_path(adapter) == adapter.resolve()
    with pytest.raises(LauncherError):
        validate_adapter_path(tmp_path / "missing")

    db = tmp_path / "data" / "quotes.sqlite3"
    assert validate_database_path(db) == db.resolve()
    with pytest.raises(LauncherError):
        validate_database_path(tmp_path / "quotes.txt")

    project = tmp_path / "project"
    (project / "data").mkdir(parents=True)
    (project / "venv" / "Scripts").mkdir(parents=True)
    for file in (project / "app.py", project / "data" / "symbols.csv",
                 project / "venv" / "Scripts" / "python.exe"):
        file.write_text("", encoding="utf-8")
    assert validate_project(project) == project.resolve()


def test_adapter_runtime_preflight(tmp_path):
    adapter = _adapter(tmp_path / "adapter")

    class Result:
        returncode = 0
        stdout = "usage: adapter.py"
        stderr = ""

    calls = []
    assert validate_adapter_runtime(
        "python.exe", adapter,
        runner=lambda *args, **kwargs: calls.append((args, kwargs)) or Result(),
    ) is True
    assert calls[0][0][0][-1] == "--help"
    assert calls[0][1]["env"]["TICKERCHART_ADAPTER_PATH"] == str(adapter)

    Result.returncode = 1
    Result.stderr = "bad dependency"
    with pytest.raises(LauncherError, match="bad dependency"):
        validate_adapter_runtime("python.exe", adapter, runner=lambda *_a, **_k: Result())


def test_config_persistence_whitelists_non_secret_values(tmp_path):
    path = tmp_path / "launcher.json"
    saved = save_launcher_config({
        "adapter_path": "C:/adapter",
        "database_path": "D:/data/quotes.sqlite3",
        "streamer_url": "wss://stream.tickerchart.net/ws/",
        "streamlit_port": 8600,
        "browser_auto_open": False,
        "password": "must-not-persist",
        "cookie": "must-not-persist",
        "token": "must-not-persist",
    }, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == {
        "adapter_path", "database_path", "streamer_url",
        "streamlit_port", "browser_auto_open",
    }
    assert raw == saved == load_launcher_config(path)
    assert "must-not-persist" not in path.read_text(encoding="utf-8")


def test_database_status_recent_stale_and_unavailable(tmp_path):
    now = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
    missing = inspect_database(tmp_path / "missing.sqlite3", now=now)
    assert missing.state == "TICKERCHART_UNAVAILABLE"
    assert missing.recent is False

    db = tmp_path / "quotes.sqlite3"
    _quote_database(db, now - timedelta(seconds=20))
    active = inspect_database(db, recent_seconds=300, now=now)
    assert active.state == "TICKERCHART_DELAYED"
    assert active.symbol_count == 1
    assert active.recent is True

    stale = inspect_database(db, recent_seconds=5, now=now)
    assert stale.state == "TICKERCHART_STALE"
    assert "during the open session" in stale.reason


def test_closed_session_age_is_not_treated_like_open_session_staleness(tmp_path):
    # Monday 09:30 Cairo is pre-open.  A Sunday quote is the latest completed
    # regular session even though it is many hours old in calendar time.
    now = datetime(2026, 7, 13, 6, 30, tzinfo=timezone.utc)
    sunday_quote = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    db = tmp_path / "quotes.sqlite3"
    _quote_database(db, sunday_quote)
    status = inspect_database(db, recent_seconds=300, now=now)
    assert status.state == "TICKERCHART_DELAYED"
    assert status.session_phase == "PRE_OPEN"
    assert status.session_lag == 0

    # Once Monday's session is underway, the same Sunday receipt is stale.
    open_status = inspect_database(
        db, recent_seconds=300,
        now=datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
    )
    assert open_status.state == "TICKERCHART_STALE"


def test_connected_collector_waits_for_closed_session_without_false_active(tmp_path):
    db = tmp_path / "quotes.sqlite3"
    connected = datetime(2026, 7, 13, 6, 29, tzinfo=timezone.utc)
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE quotes (
                ticker TEXT, received_at TEXT, exchange_timestamp TEXT
            );
            CREATE TABLE metrics (
                recorded_at TEXT, name TEXT, ticker TEXT, value REAL, detail TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO metrics VALUES (?, 'connect_success', NULL, 1, NULL)",
            (connected.isoformat(),),
        )

    pre_open = inspect_database(
        db, now=datetime(2026, 7, 13, 6, 30, tzinfo=timezone.utc)
    )
    assert pre_open.state == "TICKERCHART_WAITING_FOR_SESSION"
    assert pre_open.recent is False  # Never falsely claims usable TickerChart data.
    assert pre_open.waiting is True

    open_market = inspect_database(
        db, now=datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)
    )
    assert open_market.state == "TICKERCHART_UNAVAILABLE"


def test_yahoo_calendar_age_is_reported_as_trading_session_lag():
    now = datetime(2026, 7, 13, 6, 30, tzinfo=timezone.utc)
    assert trading_session_lag(datetime(2026, 7, 9).date(), now) == 1

    freshness = assess_quote_freshness(
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 12, 11, 45, tzinfo=timezone.utc),
        value=now,
    )
    assert freshness.usable is True


def test_wait_requires_quote_from_current_collector_and_times_out():
    started = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
    old = DatabaseStatus(
        "TICKERCHART_DELAYED",
        latest_quote_time=(started - timedelta(seconds=1)).isoformat(),
    )
    new = DatabaseStatus(
        "TICKERCHART_DELAYED",
        latest_quote_time=(started + timedelta(seconds=1)).isoformat(),
    )
    values = iter((old, new))
    result = wait_for_recent_quote(
        "ignored", timeout_seconds=1, checker=lambda *_args, **_kwargs: next(values),
        sleep=lambda _seconds: None, minimum_received_at=started,
    )
    assert result.latest_quote_time == new.latest_quote_time

    unavailable = DatabaseStatus("TICKERCHART_UNAVAILABLE", reason="offline")
    result = wait_for_recent_quote(
        "ignored", timeout_seconds=0,
        checker=lambda *_args, **_kwargs: unavailable,
    )
    assert result.reason == "offline"

    waiting = DatabaseStatus(
        "TICKERCHART_WAITING_FOR_SESSION",
        latest_connection_time=(started + timedelta(seconds=1)).isoformat(),
    )
    result = wait_for_recent_quote(
        "ignored", timeout_seconds=0,
        checker=lambda *_args, **_kwargs: waiting,
        minimum_received_at=started,
        allow_session_wait=True,
    )
    assert result.waiting is True


def test_one_click_launcher_redirects_to_unified_v2_launcher():
    # The old TickerChart+Yahoo-fallback UI is gone; running this launcher opens the
    # unified Rubix Production Launcher V2 (EODHD + Rubix, no Yahoo fallback).
    import scripts.launch_egx_ai_trader as one_click

    assert not hasattr(one_click, "failure_choice")   # Yahoo-fallback choice removed
    assert not hasattr(one_click, "LauncherUI")        # Yahoo-fallback UI removed
    src = __import__("inspect").getsource(one_click.main)
    assert "launch_rubix_production" in src            # redirects to the V2 launcher


class _FakeProcess:
    def __init__(self, pid=1234, terminate_error=False):
        self.pid = pid
        self.returncode = None
        self.terminate_error = terminate_error
        self.stdout = None

    def poll(self):
        return self.returncode

    def terminate(self):
        if self.terminate_error:
            raise RuntimeError("cannot terminate")
        self.returncode = 0

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


def test_child_process_startup_health_and_cleanup(tmp_path):
    created = []

    def popen(command, **kwargs):
        created.append((command, kwargs))
        return _FakeProcess()

    supervisor = ProcessSupervisor(tmp_path, popen_factory=popen)
    process = supervisor.start(
        "collector", ["python", "adapter.py"], {"SAFE": "1"}, tmp_path / "collector.log"
    )
    assert process.pid == 1234
    assert supervisor.alive("collector") is True
    assert supervisor.pid("collector") == 1234
    assert created[0][1]["stdin"] is not None
    supervisor.stop_all()
    assert supervisor.alive("collector") is False
    assert supervisor.log_handles == {}
    supervisor.job.close()


def test_child_output_is_redacted_before_log_persistence(tmp_path):
    process = _FakeProcess()
    process.stdout = io.StringIO(
        "Connected wss://stream.tickerchart.net/ws/?session=do-not-log\n"
    )
    supervisor = ProcessSupervisor(
        tmp_path, popen_factory=lambda *_args, **_kwargs: process
    )
    log_path = tmp_path / "collector.log"
    supervisor.start("collector", ["python"], {}, log_path)
    supervisor.log_threads["collector"].join(timeout=1)
    supervisor.stop("collector")
    content = log_path.read_text(encoding="utf-8")
    assert "do-not-log" not in content
    assert "wss://stream.tickerchart.net/ws/" in content
    supervisor.job.close()


def test_forced_cleanup_uses_process_tree_kill_on_windows(tmp_path):
    calls = []
    process = _FakeProcess(pid=4321, terminate_error=True)
    supervisor = ProcessSupervisor(
        tmp_path,
        popen_factory=lambda *_args, **_kwargs: process,
        run_command=lambda command, **kwargs: calls.append((command, kwargs)),
    )
    supervisor.start("streamlit", ["python", "-m", "streamlit"], {}, tmp_path / "streamlit.log")
    supervisor.stop("streamlit", graceful_timeout=0)
    if sys.platform == "win32":
        assert calls and calls[0][0][:3] == ["taskkill", "/PID", "4321"]
    supervisor.job.close()


def test_symbol_file_maps_to_adapter_convention(tmp_path):
    path = tmp_path / "symbols.csv"
    path.write_text("Ticker\nCOMI.CA\nSWDY.CA\nFWRY.CA\n", encoding="utf-8")
    assert _load_adapter_symbols(path) == ["COMI.EGY", "FWRY.EGY", "SWDY.EGY"]
