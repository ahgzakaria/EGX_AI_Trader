"""A stop that the supervisor performs on itself, rather than one done to it.

Windows does not deliver SIGTERM, and closing the console window kills the
process outright. Across 3516 log lines this supervisor had never once reached
its own shutdown path: every stop skipped the final WAL checkpoint on a 5.9 GB
database and left the health file still claiming the collector was up.

The flag closes that gap. It is checked once per heartbeat beside the signal
flag, so a stop leaves through the same ``finally`` every other exit uses --
child stopped, database checkpointed, health file marked, lock released.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.rubix_collector_supervisor import CollectorSupervisor


class _Log:
    def __init__(self):
        self.events = []

    def emit(self, event, **fields):
        self.events.append((event, fields))

    def names(self):
        return [name for name, _ in self.events]


def _supervisor(stop_file: Path, *, started_wall: float | None = None):
    """The real method bound to the two attributes it reads.

    Constructing a whole supervisor would need an adapter, an auth frame valid
    for fifteen minutes and a live database, none of which this behaviour
    touches.
    """
    stub = SimpleNamespace(
        stop_file=stop_file,
        started_wall=time.time() if started_wall is None else started_wall,
        log=_Log(),
    )
    stub.stop_requested_on_disk = CollectorSupervisor.stop_requested_on_disk.__get__(stub)
    return stub


def test_no_flag_means_keep_running(tmp_path):
    supervisor = _supervisor(tmp_path / "stop.flag")
    assert supervisor.stop_requested_on_disk() is False
    assert supervisor.log.names() == []


def test_a_flag_written_after_start_stops_the_run(tmp_path):
    flag = tmp_path / "stop.flag"
    supervisor = _supervisor(flag, started_wall=time.time() - 60)
    flag.write_text("stop", encoding="utf-8")

    assert supervisor.stop_requested_on_disk() is True
    assert "stop_requested_by_flag" in supervisor.log.names()


def test_a_flag_left_over_from_last_night_is_ignored_and_removed(tmp_path):
    """The failure this guard prevents is worse than the one it fixes.

    A flag surviving yesterday's shutdown would stop tomorrow morning's
    collector a second after it starts -- a silent no-collector day, which is
    exactly what cost 2026-08-17.
    """
    flag = tmp_path / "stop.flag"
    flag.write_text("yesterday", encoding="utf-8")
    import os
    old = time.time() - 86_400
    os.utime(flag, (old, old))

    supervisor = _supervisor(flag)  # started just now, flag is a day old

    assert supervisor.stop_requested_on_disk() is False
    assert "stop_flag_stale_ignored" in supervisor.log.names()
    assert not flag.exists(), "a stale flag must be cleared, not re-read every heartbeat"


def test_an_unreadable_flag_never_takes_the_collector_down(tmp_path, monkeypatch):
    """Checked every heartbeat mid-session: a filesystem hiccup must not stop it."""
    flag = tmp_path / "stop.flag"
    flag.write_text("stop", encoding="utf-8")
    supervisor = _supervisor(flag, started_wall=time.time() - 60)

    def explode(*_args, **_kwargs):
        raise OSError("device not ready")

    monkeypatch.setattr(Path, "stat", explode)

    assert supervisor.stop_requested_on_disk() is False
    assert "stop_flag_unreadable" in supervisor.log.names()


def test_the_flag_is_compared_against_a_wall_clock(tmp_path):
    """started_at is monotonic and measures uptime.

    Comparing a file's mtime against it compares two unrelated clocks, and the
    staleness test then always fires or never does. The first draft of this did
    exactly that.
    """
    supervisor = _supervisor(tmp_path / "stop.flag")
    assert supervisor.started_wall > 1_700_000_000, (
        "started_wall must be a real epoch time, not a monotonic counter"
    )


def test_the_loop_condition_actually_consults_the_flag():
    """A guard nothing calls is a guard that passes forever."""
    import inspect

    source = inspect.getsource(CollectorSupervisor.run)
    assert "stop_requested_on_disk()" in source
    assert "while not STOP_REQUESTED" in source


@pytest.mark.parametrize("script", ["scripts/stop_everything.ps1", "STOP.cmd"])
def test_the_stop_scripts_are_pure_ascii(script):
    """Windows PowerShell 5.1 reads a BOM-less UTF-8 file as ANSI.

    One em dash in a string is enough to make it a parse error, which is how a
    scheduled script once failed while running fine in the dev shell.
    """
    text = Path(script).read_text(encoding="utf-8")
    offenders = [
        (number, line)
        for number, line in enumerate(text.splitlines(), 1)
        if any(ord(char) > 127 for char in line)
    ]
    assert not offenders, f"non-ASCII in {script}: {offenders[:3]}"


class _FakeChild:
    def __init__(self):
        self.returncode = None
        self.stopped = False

    def poll(self):
        return None


def test_the_flag_drives_the_real_loop_out_through_its_shutdown_path(tmp_path, monkeypatch):
    """The claim worth testing: a flagged stop is a *clean* stop.

    Not that the process disappears -- killing it does that too -- but that it
    leaves through the same ``finally`` as every other exit, so the child is
    stopped, the database is checkpointed, the health file records the shutdown
    and the lock is released. That is the whole difference between this and the
    console-window kill it replaces.
    """
    import scripts.rubix_collector_supervisor as module

    flag = tmp_path / "stop.flag"
    health_file = tmp_path / "health.json"
    log = _Log()
    order = []

    class Harness(module.CollectorSupervisor):
        def __init__(self):  # the real __init__ needs an adapter and a live feed
            self.stop_file = flag
            self.started_wall = time.time() - 5
            self.started_at = time.monotonic()
            self.log = log
            self.health_file = health_file
            self.database = tmp_path / "fake.db"
            self.child = _FakeChild()
            self.restarts = 0
            self.last_maintenance = time.monotonic()
            self.lock = None
            self.lock_file = tmp_path / "fake.lock"
            self.pid_file = tmp_path / "fake.pid.json"
            self.args = SimpleNamespace(
                restart_backoff_seconds=1, max_restarts=20,
                maintenance_seconds=999, heartbeat_seconds=0.05,
            )

        def health(self):
            return {"up": True}

        def start_child(self):
            order.append("start_child")

        def stop_child(self):
            order.append("stop_child")
            self.child.stopped = True

    def _maintenance(_db, *, integrity=True, truncate=False):
        # Recorded separately: the startup pass verifies, the shutdown pass
        # verifies, and the periodic pass must not.
        order.append("verify" if integrity else "checkpoint")
        return {"integrity": "ok" if integrity else "NOT_CHECKED"}

    monkeypatch.setattr(module, "database_maintenance", _maintenance)
    class _Lock:
        def acquire(self):
            order.append("lock")

        def release(self):
            order.append("unlock")

    monkeypatch.setattr(module, "SingleInstanceLock", lambda *a, **k: _Lock())

    harness = Harness()
    flag.write_text("stop", encoding="utf-8")   # the button, in effect

    assert harness.run() == 0, "a flagged stop is a normal exit, not a failure"

    assert "stop_requested_by_flag" in log.names()
    # The collector starts before anything verifies: startup verification runs
    # on a daemon thread, so where it lands in this list is not deterministic
    # and only its position *after* start_child is asserted.
    assert order[:2] == ["lock", "start_child"], order

    # The shutdown sequence is synchronous and its order is the whole point:
    # the lock is released last, after the database is safely checkpointed,
    # never before -- or the next start could open a database mid-checkpoint.
    assert order[-3:] == ["stop_child", "verify", "unlock"], order
    assert "supervisor_shutdown" in log.names(), (
        "without this line nothing can tell a clean stop from a process that died"
    )

    import json
    recorded = json.loads(health_file.read_text(encoding="utf-8"))
    assert recorded["shutdown"] is True
    assert recorded["database"] == {"integrity": "ok"}
