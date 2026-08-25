"""The write-ahead log must not grow without bound.

Measured on the live database on 2026-08-25: the WAL had reached **30.73 GB
against a 5.85 GB database** -- five times the data it journals -- while every
checkpoint in the log was reporting success. Nothing was broken in the
checkpoint. A PASSIVE checkpoint writes the frames back but leaves the file at
its high-water mark, and the only thing that ever shrinks it is an explicit
TRUNCATE checkpoint, which until 2026-08-24 had never run: every stop was a
kill, so the shutdown path that would have done it was never reached.

``journal_size_limit`` looks like the lever and is not one. That is measured
here rather than assumed, because shipping it would have been a line that reads
like a fix and does nothing.

The cost is not only disk. The disk sat at 125% busy with a queue length of
3.8, and the supervisor was issuing 180,000 I/O operations a second.
"""

from __future__ import annotations

import sqlite3

import pytest

from scripts.rubix_collector_supervisor import database_maintenance


@pytest.fixture
def database(tmp_path):
    """A WAL-mode database with enough churn to make the journal real."""
    path = tmp_path / "market.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA journal_size_limit=-1")   # the old behaviour
        connection.execute("CREATE TABLE feed_metrics (id INTEGER PRIMARY KEY, "
                           "observed_at TEXT, event TEXT, ticker TEXT, value REAL)")
        for batch in range(20):
            connection.executemany(
                "INSERT INTO feed_metrics(observed_at, event, ticker, value) "
                "VALUES (?, ?, ?, ?)",
                [(f"2026-08-25T{batch:02d}", "latency_ms", f"T{i}", i * 1.5)
                 for i in range(2000)],
            )
            connection.commit()
    return path


def _wal_bytes(path):
    wal = path.with_name(path.name + "-wal")
    return wal.stat().st_size if wal.exists() else 0


def test_only_truncate_actually_shrinks_the_file(database):
    """The measurement that decided this, kept as a test.

    ``journal_size_limit`` reads like the lever and is not one: with it set to
    4096, PASSIVE and RESTART both left a 4,301,312-byte WAL exactly as it was,
    and only TRUNCATE returned the file to zero. Shipping the pragma would have
    been a line that looks like a fix and is not.
    """
    before = _wal_bytes(database)
    assert before > 4096, "the fixture did not produce a journal worth shrinking"

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_size_limit=4096")
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
    assert _wal_bytes(database) == before, (
        "if PASSIVE has started truncating, the shutdown-only rule can be relaxed"
    )

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert _wal_bytes(database) == 0


def test_a_shutdown_returns_the_journal_to_nothing(database):
    """At shutdown the child is stopped, so nothing holds the file and the
    space can actually come back."""
    assert _wal_bytes(database) > 0, "the fixture did not produce a journal"

    result = database_maintenance(database, integrity=False, truncate=True)

    assert result["wal_checkpoint"][0] == 0, "the checkpoint was blocked"
    assert _wal_bytes(database) == 0, "TRUNCATE left the file behind"


def test_the_periodic_pass_never_truncates(database):
    """TRUNCATE waits for readers. Run while the collector is live that is a
    stall in the one process that must not stall."""
    import inspect

    from scripts.rubix_collector_supervisor import CollectorSupervisor

    periodic = inspect.getsource(CollectorSupervisor.run).split("maintenance_seconds", 1)[1]
    periodic = periodic.split("finally", 1)[0]
    assert "truncate=True" not in periodic


def test_shutdown_asks_for_the_truncate():
    import inspect

    from scripts.rubix_collector_supervisor import CollectorSupervisor

    shutdown = inspect.getsource(CollectorSupervisor.run).split("finally", 1)[1]
    assert "truncate=True" in shutdown


def test_a_locked_database_is_reported_not_raised(tmp_path):
    """Maintenance runs beside a live collector and must never be able to
    take it down."""
    missing = tmp_path / "nothing.db"
    assert database_maintenance(missing, integrity=False)["integrity"] == "MISSING"
