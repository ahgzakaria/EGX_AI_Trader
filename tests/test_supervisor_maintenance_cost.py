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


# --- reclaiming the write-ahead log ------------------------------------------
#
# Only TRUNCATE returns the WAL file to zero; the periodic PASSIVE pass flushes
# its contents and leaves the file at its high-water mark. Truncating lived only
# in the shutdown path, which a killed supervisor never reaches, so the file only
# ever grew: 30.73 GB against a 5.85 GB database once, and 43,074 MB against
# 7,655 MB on 2026-09-01. Readers traverse the WAL, so the whole application
# slows with it -- one GROUP BY went from 2.9 seconds to over 280.

def _reclaimer(tmp_path, database_path):
    stub = SimpleNamespace(log=_Log(), database=database_path)
    for name in ("reclaim_wal", "wal_bytes"):
        setattr(stub, name, getattr(CollectorSupervisor, name).__get__(stub))
    return stub


def _grow_wal(path):
    """Leave real frames in the -wal sidecar."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")   # do not flush behind us
    connection.executemany("INSERT INTO quotes VALUES (?, ?)",
                           [(f"W{i}", i * 2.0) for i in range(5000)])
    connection.commit()
    return connection            # held open so the WAL survives to be measured


def test_the_wal_is_truncated_before_the_collector_starts(database, tmp_path):
    # The connection stays open across the call. Closing the last one makes
    # SQLite checkpoint and remove the WAL by itself, which would leave nothing
    # for this to reclaim and the test asserting on its own cleanup.
    keep_open = _grow_wal(database)
    try:
        supervisor = _reclaimer(tmp_path, database)
        assert supervisor.wal_bytes() > 0, "the fixture must leave a WAL to reclaim"
        supervisor.reclaim_wal()
        assert supervisor.wal_bytes() == 0
        assert "wal_reclaimed" in supervisor.log.names()
    finally:
        keep_open.close()


def test_it_reports_what_it_reclaimed(database, tmp_path):
    keep_open = _grow_wal(database)
    try:
        supervisor = _reclaimer(tmp_path, database)
        before = supervisor.wal_bytes()
        assert before > 0
        supervisor.reclaim_wal()
        event, fields = supervisor.log.events[-1]
        assert event == "wal_reclaimed"
        assert fields["wal_bytes_before"] == before
        assert fields["wal_bytes_after"] == 0
        assert fields["seconds"] >= 0
    finally:
        keep_open.close()


def test_a_missing_database_is_not_an_error(tmp_path):
    supervisor = _reclaimer(tmp_path, tmp_path / "absent.db")
    supervisor.reclaim_wal()
    assert supervisor.log.names() == []
    assert supervisor.wal_bytes() == 0


def test_a_failure_to_reclaim_never_stops_the_collector_starting(database, tmp_path, monkeypatch):
    """Tidying up is not a precondition for collecting the market."""

    import scripts.rubix_collector_supervisor as module

    def _explode(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(module, "database_maintenance", _explode)
    supervisor = _reclaimer(tmp_path, database)
    supervisor.reclaim_wal()                      # must not raise
    assert supervisor.log.names() == ["wal_reclaim_failed"]


def test_reclaiming_happens_before_the_child_is_started():
    """After start_child there is a writer, and TRUNCATE waits for writers --
    which is the one thing this must never do to the collector."""

    source = inspect.getsource(CollectorSupervisor.run)
    body = source.partition("\n        finally:")[0]
    assert body.index("self.reclaim_wal()") < body.index("self.start_child()")


# --- one refusal is not the answer -------------------------------------------
#
# The first live run proved the "nothing to wait for before start_child"
# assumption wrong: the launcher reads this database too, on a health() timer,
# and one of its calls spanned the whole attempt. The truncate found the file
# locked, gave up after 15 seconds, and logged 45,165,903,792 bytes before and
# after -- honest, and useless.

def test_it_retries_while_a_reader_still_holds_the_file(database, tmp_path, monkeypatch):
    import scripts.rubix_collector_supervisor as module

    supervisor = _reclaimer(tmp_path, database)
    calls = {"n": 0}

    def _busy_then_free(path, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"integrity": "ERROR", "error": "database is locked", "bytes": 0}
        return {"integrity": "NOT_CHECKED", "wal_checkpoint": [0, 0, 0], "bytes": 0}

    # The WAL stays at its size until the third attempt succeeds. Bound to the
    # instance, not the type: SimpleNamespace attributes are per-object.
    sizes = iter([5000, 5000, 5000, 0, 0, 0])
    supervisor.wal_bytes = lambda: next(sizes)
    monkeypatch.setattr(module, "database_maintenance", _busy_then_free)
    monkeypatch.setattr(module.time, "sleep", lambda _s: None)

    supervisor.reclaim_wal(budget_seconds=60.0)
    assert calls["n"] >= 3
    event, fields = supervisor.log.events[-1]
    assert event == "wal_reclaimed" and fields["attempts"] >= 3


def test_the_budget_stops_it_retrying_forever(database, tmp_path, monkeypatch):
    """A supervisor that will not start because it cannot tidy up is worse than
    the untidiness."""

    import scripts.rubix_collector_supervisor as module

    supervisor = _reclaimer(tmp_path, database)
    monkeypatch.setattr(module, "database_maintenance",
                        lambda path, **kw: {"integrity": "ERROR",
                                            "error": "database is locked", "bytes": 0})
    monkeypatch.setattr(module.time, "sleep", lambda _s: None)

    supervisor.reclaim_wal(budget_seconds=0.0)     # one attempt, then give up
    event, fields = supervisor.log.events[-1]
    assert event == "wal_reclaimed"
    assert fields["wal_bytes_after"] == fields["wal_bytes_before"]


def test_an_oversized_wal_is_also_attempted_mid_session():
    """Waiting for the next startup means carrying it for the rest of the day,
    which is exactly what made every read slow on 2026-09-02."""

    source = inspect.getsource(CollectorSupervisor.run)
    loop = source.partition("\n        finally:")[0]
    maintenance = loop.split("self.args.maintenance_seconds", 1)[1]
    assert "WAL_RECLAIM_THRESHOLD_BYTES" in maintenance
    assert "reclaim_wal(" in maintenance


def test_the_mid_session_attempt_is_bounded_so_it_cannot_stall_the_loop():
    """TRUNCATE waits for readers. Mid-session that wait must be a short refusal,
    never a stall in the one process that must not stall."""

    source = inspect.getsource(CollectorSupervisor.run)
    maintenance = source.partition("\n        finally:")[0].split(
        "self.args.maintenance_seconds", 1)[1]
    call = maintenance.split("reclaim_wal(", 1)[1].split(")", 1)[0]
    assert "budget_seconds=0" in call
    assert "attempt_timeout=" in call
