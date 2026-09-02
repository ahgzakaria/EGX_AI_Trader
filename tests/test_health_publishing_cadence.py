"""The heartbeat and the health file are two different questions.

The supervisor loop runs every two seconds because a dead collector should be
restarted quickly and a stop should take effect quickly. It used to call
``health()`` on every one of those passes, purely to write a telemetry file --
and nothing in the loop branches on what it returns. The restart test asks the
process, not the database.

A warm ``health()`` costs about 220 ms during a session and about 520 ms
outside one, and this supervisor runs all day. Paying it thirty times a minute
held a read snapshot roughly a tenth of the time, against the database the
collector is writing to, for a file that System Health embeds for display and
whose age nothing anywhere measures.

The line these tests hold is not "call it less". It is that *cheap state checks
stay on the fast path and only the expensive evidence waits*: a collector that
dies is still published at once, and so is a restart and a shutdown. The file
is never late about something that happened -- only about how things stayed.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from scripts.rubix_collector_supervisor import CollectorSupervisor, build_parser


class _Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def _publisher(tmp_path, clock, *, health_seconds=30.0):
    """The real publish_health bound to the attributes it reads."""

    stub = SimpleNamespace(
        health_file=tmp_path / "health.json",
        health_seconds=health_seconds,
        last_health_write=0.0,
        calls=[],
    )
    def _health():
        stub.calls.append(clock.value)
        return {"status": "RUBIX_FRESH"}

    stub.health = _health
    stub.publish_health = CollectorSupervisor.publish_health.__get__(stub)
    return stub


# --- the timer ---------------------------------------------------------------

def test_the_health_file_is_written_on_its_own_interval(monkeypatch):
    """Not the heartbeat. Two seconds against thirty is fifteen times the reads."""

    body = inspect.getsource(CollectorSupervisor.run).partition("while not STOP_REQUESTED")[2]
    gate = body.partition("if self.child.poll()")[0]
    assert "self.health_seconds" in gate, (
        "the periodic publish must be gated on its own interval")
    assert "heartbeat_seconds" not in gate, (
        "the heartbeat must not be what schedules the health file")


def test_the_default_interval_is_not_the_heartbeat():
    parser = build_parser()
    defaults = {action.dest: action.default for action in parser._actions}
    assert defaults["heartbeat_seconds"] == 2
    assert defaults["health_seconds"] > defaults["heartbeat_seconds"]


def test_the_interval_survives_an_args_namespace_without_the_flag():
    """Callers build their own args; one missing the new flag must not crash."""

    line = next(row for row in inspect.getsource(CollectorSupervisor.__init__).splitlines()
                if "self.health_seconds" in row)
    assert 'getattr(args, "health_seconds", None)' in line and "or 30" in line


# --- what does not wait for it -----------------------------------------------

def test_publishing_writes_the_file_and_resets_the_timer(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr("scripts.rubix_collector_supervisor.time.monotonic", clock)
    stub = _publisher(tmp_path, clock)

    stub.publish_health()
    assert json.loads(stub.health_file.read_text(encoding="utf-8"))["status"] == "RUBIX_FRESH"
    assert stub.last_health_write == 1000.0

    clock.advance(7.0)
    stub.publish_health()
    assert stub.last_health_write == 1007.0
    assert stub.calls == [1000.0, 1007.0], "each publish reads health once"


def test_extra_fields_are_merged_into_the_published_record(tmp_path, monkeypatch):
    """The shutdown path carries `shutdown: true`, and startup reads it back."""

    clock = _Clock()
    monkeypatch.setattr("scripts.rubix_collector_supervisor.time.monotonic", clock)
    stub = _publisher(tmp_path, clock)

    stub.publish_health(shutdown=True, database={"checkpoint": "TRUNCATE"})
    record = json.loads(stub.health_file.read_text(encoding="utf-8"))
    assert record["shutdown"] is True
    assert record["database"] == {"checkpoint": "TRUNCATE"}
    assert record["status"] == "RUBIX_FRESH", "the health fields must survive the merge"


def test_a_clean_shutdown_still_writes_the_flag_the_next_startup_reads(tmp_path, monkeypatch):
    """publish_health replaced a hand-built dict here; the contract is the flag."""

    clock = _Clock()
    monkeypatch.setattr("scripts.rubix_collector_supervisor.time.monotonic", clock)
    stub = _publisher(tmp_path, clock)
    stub.publish_health(shutdown=True, database={})

    reader = SimpleNamespace(health_file=stub.health_file)
    reader.previous_run_shut_down_cleanly = (
        CollectorSupervisor.previous_run_shut_down_cleanly.__get__(reader))
    assert reader.previous_run_shut_down_cleanly() is True


def test_a_collector_exit_and_a_restart_are_published_at_once():
    """A state change must not wait up to thirty seconds to reach the file."""

    body = inspect.getsource(CollectorSupervisor.run).partition("while not STOP_REQUESTED")[2]
    exit_branch = body.partition("if self.child.poll() is not None:")[2].partition("now = time.monotonic()")[0]
    assert exit_branch.count("self.publish_health()") == 2, (
        "both the restart-limit exit and the restart itself must publish immediately")


def test_the_shutdown_path_publishes_through_the_same_method():
    source = inspect.getsource(CollectorSupervisor.run)
    tail = source.partition("\n        finally:")[2]
    assert "self.publish_health(shutdown=True" in tail
    assert "atomic_json(self.health_file" not in tail, (
        "one way to write the file, so the timer cannot be bypassed by accident")


# --- the launcher windows ----------------------------------------------------
#
# Two windows show the same handful of fields and each picked its own interval.
# One used fifteen seconds on a worker thread; the other used two on the Tk main
# thread, where a half-second call is a window that stops repainting.

def test_both_launcher_windows_share_one_interval():
    from scripts import launch_rubix_production as launcher

    source = inspect.getsource(launcher)
    assert launcher.HEALTH_POLL_SECONDS >= 15
    assert source.count("_last_health_check >= HEALTH_POLL_SECONDS") == 2, (
        "both windows must read the same constant, or they drift apart again")


def test_a_stopped_collector_is_not_hidden_behind_the_health_timer():
    """poll() is free; putting it behind the timer costs fifteen seconds of silence."""

    from scripts import launch_rubix_production as launcher

    body = inspect.getsource(launcher.LauncherUI._refresh)
    block = body.partition("if self.supervisor.collector is not None:")[2]
    stopped = block.index("Collector stopped unexpectedly")
    gated = block.index("HEALTH_POLL_SECONDS")
    assert stopped < gated, (
        "the liveness check must run before the interval gate, not inside it")
