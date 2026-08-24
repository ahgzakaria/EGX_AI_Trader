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
import sqlite3

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


def test_startup_and_shutdown_both_verify():
    """Startup is where a corrupt database is worth knowing about, and neither
    moment competes with the collector for the disk."""
    source = inspect.getsource(CollectorSupervisor.run)

    startup = source.split("database_startup_check", 1)
    assert len(startup) == 2, "no startup verification at all"
    assert "integrity=True" in startup[1].split("\n\n", 1)[0]

    # The shutdown call takes the default, which verifies.
    shutdown = source.split("finally", 1)[1]
    assert "database_maintenance(self.database)" in shutdown
    assert "integrity=False" not in shutdown


def test_the_next_interval_is_measured_from_the_end_of_the_work():
    """Taken before the work, a slow pass schedules the next one immediately
    and the supervisor checkpoints back to back."""
    source = inspect.getsource(CollectorSupervisor.run)
    body = source.split("maintenance_seconds", 1)[1].split("finally", 1)[0]
    assert body.index("database_maintenance") < body.index("self.last_maintenance = time.monotonic()")
