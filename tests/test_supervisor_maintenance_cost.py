"""The periodic maintenance must not read the whole database.

``wal_checkpoint(PASSIVE)`` writes back only what the WAL holds and is
proportional to recent activity. ``integrity_check`` reads and verifies every
page and is proportional to the whole file.

Measured on the real 5.84 GB database on 2026-08-24, with no collector running
so nothing competed for the disk: the checkpoint took 0.00 s, and even
``quick_check`` -- the cheaper of the two verification pragmas -- took 147.9 s.
From the supervisor's own log, during the session with the collector writing,
the full check took 233-635 s against a 300 s schedule. One took ten and a half
minutes. Roughly seventy percent of every trading session went into it, at over
100 MB/s and 65,000 disk operations a second, and nothing consumed the verdict.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import time as _time
from types import SimpleNamespace

import pytest

from scripts.rubix_collector_supervisor import CollectorSupervisor, database_maintenance


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "market.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE quotes (symbol TEXT, price REAL)")
        connection.executemany("INSERT INTO quotes VALUES (?, ?)",
                               [(f"S{i}", i * 1.5) for i in range(500)])
    return path


def test_the_periodic_pass_skips_verification_and_says_so(database):
    """Not silently: "NOT_CHECKED" must never be mistaken for "ok"."""
    result = database_maintenance(database, integrity=False)
    assert result["integrity"] == "NOT_CHECKED"
    assert result["wal_checkpoint"] is not None, "the checkpoint still has to run"
    assert result["bytes"] > 0


def test_verification_still_happens_when_asked(database):
    assert database_maintenance(database, integrity=True)["integrity"] == "ok"


def test_verification_defaults_to_on(database):
    """Callers that do not think about it get the safe, slow behaviour."""
    assert database_maintenance(database)["integrity"] == "ok"


def test_a_missing_database_is_reported_not_raised(tmp_path):
    result = database_maintenance(tmp_path / "gone.db", integrity=False)
    assert result["integrity"] == "MISSING"


def test_the_loop_never_runs_the_full_check(tmp_path):
    """The whole point. A regression here silently returns the session to
    spending most of itself reading a six-gigabyte file."""
    source = inspect.getsource(CollectorSupervisor.run)
    periodic = source.split("maintenance_seconds", 1)[1]
    assert "integrity=False" in periodic.split("finally", 1)[0]


def test_the_collector_starts_before_anything_verifies():
    """Verification on the startup path is the collector not collecting.

    A first attempt ran the check before start_child. Measured on the real
    database it held the collector for 156 seconds and counting; at 09:10 that
    is the opening auction missed, which is far worse than learning about
    corruption two minutes later.
    """
    source = inspect.getsource(CollectorSupervisor.run)
    assert source.index("self.start_child()") < source.index("verify_database_in_background")


def test_verification_runs_off_the_main_thread():
    source = inspect.getsource(CollectorSupervisor.verify_database_in_background)
    assert "threading.Thread" in source
    assert "daemon=True" in source
    assert "integrity=True" in source


def test_shutdown_still_verifies():
    """The one moment nothing is waiting on the collector."""
    shutdown = inspect.getsource(CollectorSupervisor.run).split("finally", 1)[1]
    assert "database_maintenance(self.database, truncate=True)" in shutdown
    assert "integrity=False" not in shutdown


def test_a_clean_previous_shutdown_skips_the_check_entirely(tmp_path):
    """It verified the same file on its way out, and nothing consumes the
    verdict beyond a log line, so the cost has to be earned."""
    supervisor = _bare_supervisor(tmp_path, health={"shutdown": True})
    assert supervisor.previous_run_shut_down_cleanly() is True

    supervisor.verify_database_in_background(skip=True)
    assert supervisor.log.names() == ["database_startup_check_skipped"]


def test_an_unclean_or_unknown_history_always_verifies(tmp_path):
    """A kill, a power cut, or no file at all. Unknown is a reason to check."""
    assert _bare_supervisor(tmp_path, health={}).previous_run_shut_down_cleanly() is False
    assert _bare_supervisor(tmp_path, health=None).previous_run_shut_down_cleanly() is False
    assert _bare_supervisor(tmp_path, health="not json").previous_run_shut_down_cleanly() is False


def test_a_failing_check_never_takes_the_collector_down(tmp_path, monkeypatch):
    import scripts.rubix_collector_supervisor as module

    supervisor = _bare_supervisor(tmp_path, health={})
    monkeypatch.setattr(module, "database_maintenance",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk gone")))
    supervisor.verify_database_in_background(skip=False)
    for _ in range(50):
        if supervisor.log.names():
            break
        _time.sleep(0.05)
    assert supervisor.log.names() == ["database_startup_check_failed"]


def test_the_next_interval_is_measured_from_the_end_of_the_work():
    """Taken before the work, a slow pass schedules the next one immediately
    and the supervisor checkpoints back to back."""
    source = inspect.getsource(CollectorSupervisor.run)
    body = source.split("maintenance_seconds", 1)[1].split("finally", 1)[0]
    assert body.index("database_maintenance") < body.index("self.last_maintenance = time.monotonic()")


class _Log:
    def __init__(self):
        self.events = []

    def emit(self, event, **fields):
        self.events.append((event, fields))

    def names(self):
        return [name for name, _ in self.events]


def _bare_supervisor(tmp_path, *, health):
    """The real methods bound to the two attributes they read."""
    health_file = tmp_path / "health.json"
    if health is not None:
        health_file.write_text(
            health if isinstance(health, str) else json.dumps(health), encoding="utf-8"
        )
    stub = SimpleNamespace(health_file=health_file, log=_Log(),
                           database=tmp_path / "market.db")
    for name in ("previous_run_shut_down_cleanly", "verify_database_in_background"):
        setattr(stub, name, getattr(CollectorSupervisor, name).__get__(stub))
    return stub
