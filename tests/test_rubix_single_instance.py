"""Deterministic tests for the Rubix single-instance guard, lock and recovery.

These lock in the fix for the duplicate-supervisor race (two supervisors passing
the old PID-file check simultaneously). No live feed, GUI, or market DB is touched.
"""

from __future__ import annotations

import json
import os

import pytest

from scripts.launcher_process_utils import (
    InstanceAlreadyRunning,
    SingleInstanceLock,
    is_rubix_collector_process,
    process_creation_time,
    read_pid_record,
    should_suppress_retry,
    supervisor_status,
    unhealthy_is_only_stale,
    write_json_atomic,
)


# --- atomic lock: two simultaneous starts, exactly one wins -----------------

def test_two_simultaneous_starts_exactly_one_wins(tmp_path):
    lock = tmp_path / "sup.lock"
    meta = tmp_path / "sup.pid.json"
    first = SingleInstanceLock(lock, label="Rubix collector supervisor", meta_path=meta)
    first.acquire()
    try:
        second = SingleInstanceLock(lock, label="Rubix collector supervisor", meta_path=meta)
        with pytest.raises(InstanceAlreadyRunning):
            second.acquire()                      # the OS lock rejects the racer immediately
    finally:
        first.release()
    # exactly one metadata record existed, naming the winner (this process)
    assert read_pid_record(meta) == {}            # released → removed


def test_metadata_written_atomically_names_holder(tmp_path):
    lock = tmp_path / "sup.lock"
    meta = tmp_path / "sup.pid.json"
    holder = SingleInstanceLock(lock, label="sup", meta_path=meta,
                                extra_meta={"project_root": "X", "executable": "py"})
    holder.acquire()
    try:
        rec = read_pid_record(meta)
        assert rec["pid"] == os.getpid()
        assert rec["project_root"] == "X" and rec["executable"] == "py"
        assert "process_created_at" in rec and "started_at" in rec
    finally:
        holder.release()


# --- stale vs live PID ------------------------------------------------------

def test_stale_pid_file_does_not_block_new_lock(tmp_path):
    lock = tmp_path / "sup.lock"
    meta = tmp_path / "sup.pid.json"
    # a stale metadata file from a dead PID (never a live lock)
    write_json_atomic(meta, {"pid": 999999, "process_created_at": 1.0, "label": "sup"})
    assert supervisor_status(meta)["running"] is False
    fresh = SingleInstanceLock(lock, label="sup", meta_path=meta)
    fresh.acquire()                               # must not be blocked by stale metadata
    try:
        assert supervisor_status(meta)["running"] is True
        assert supervisor_status(meta)["pid"] == os.getpid()
    finally:
        fresh.release()


def test_live_pid_of_unrelated_process_is_not_treated_as_supervisor(tmp_path):
    meta = tmp_path / "sup.pid.json"
    # a real, live PID (this process) but with the WRONG recorded start time →
    # a reused PID must never be mistaken for the supervisor.
    write_json_atomic(meta, {"pid": os.getpid(), "process_created_at": 1.0, "label": "sup"})
    assert supervisor_status(meta)["running"] is False


def test_supervisor_status_recognizes_current_process(tmp_path):
    meta = tmp_path / "sup.pid.json"
    write_json_atomic(meta, {"pid": os.getpid(),
                             "process_created_at": process_creation_time(os.getpid()),
                             "label": "sup"})
    status = supervisor_status(meta)
    assert status["running"] is True and status["pid"] == os.getpid()


# --- restart after crash + clean release ------------------------------------

def test_lock_reacquired_after_holder_releases(tmp_path):
    lock = tmp_path / "sup.lock"
    meta = tmp_path / "sup.pid.json"
    a = SingleInstanceLock(lock, label="sup", meta_path=meta)
    a.acquire()
    a.release()                                   # graceful shutdown
    assert not meta.exists()                      # metadata cleaned up
    b = SingleInstanceLock(lock, label="sup", meta_path=meta)
    b.acquire()                                   # a fresh supervisor can start again
    try:
        assert b.locked
    finally:
        b.release()


def test_crash_releases_lock_via_fd_close(tmp_path):
    lock = tmp_path / "sup.lock"
    meta = tmp_path / "sup.pid.json"
    victim = SingleInstanceLock(lock, label="sup", meta_path=meta)
    victim.acquire()
    # simulate a crash: the OS drops the lock when the fd/handle closes, without
    # any graceful release call.
    os.close(victim._fd)
    victim._fd = None
    survivor = SingleInstanceLock(lock, label="sup", meta_path=meta)
    survivor.acquire()                            # must succeed after the "crash"
    survivor.release()


def test_release_only_removes_own_metadata(tmp_path):
    lock = tmp_path / "sup.lock"
    meta = tmp_path / "sup.pid.json"
    holder = SingleInstanceLock(lock, label="sup", meta_path=meta)
    holder.acquire()
    # someone else's metadata overwrote the file; release must NOT delete it
    write_json_atomic(meta, {"pid": 424242, "label": "other"})
    holder.release()
    assert meta.exists() and read_pid_record(meta)["pid"] == 424242


# --- only one collector child under a supervisor ----------------------------

def test_start_child_skips_when_a_child_is_already_running():
    import scripts.rubix_collector_supervisor as sup

    class _Child:
        pid = 4321
        def poll(self):
            return None                           # still running

    class _Log:
        events = []
        def emit(self, event, **fields):
            self.events.append((event, fields))

    obj = object.__new__(sup.CollectorSupervisor)  # bypass __init__ (no real feed)
    obj.child = _Child()
    obj.log = _Log()
    obj.start_child()                              # must NOT spawn a second collector
    assert obj.child.pid == 4321                   # unchanged
    assert any(e[0] == "collector_start_skipped" for e in obj.log.events)


# --- recovery safety: never touch the DB, never kill unrelated python -------

def test_lock_operations_do_not_touch_market_database(tmp_path):
    db = tmp_path / "rubix_live_market.db"
    db.write_bytes(b"SQLITE_FORMAT_3_PLACEHOLDER")
    before = db.read_bytes()
    lock = tmp_path / "sup.lock"
    meta = tmp_path / "sup.pid.json"
    holder = SingleInstanceLock(lock, label="sup", meta_path=meta)
    holder.acquire()
    holder.release()
    assert db.read_bytes() == before              # database bytes untouched


DEF_STALE_HEALTH = {
    "collector_status": "CONNECTED", "authentication_status": "ACKNOWLEDGED",
    "freshness": "STALE", "schema_valid": True, "symbols_received": 265,
}


def test_holiday_stale_is_recognized_as_only_stale():
    assert unhealthy_is_only_stale(DEF_STALE_HEALTH) is True
    # an auth failure is NOT "only stale" (a real problem to surface)
    bad = dict(DEF_STALE_HEALTH, authentication_status="EXPIRED")
    assert unhealthy_is_only_stale(bad) is False
    # a missing database is NOT "only stale"
    assert unhealthy_is_only_stale(dict(DEF_STALE_HEALTH, schema_valid=False)) is False
    # a dashboard failure is NOT "only stale"
    assert unhealthy_is_only_stale(dict(DEF_STALE_HEALTH, dashboard_ready=False)) is False


def test_retry_suppressed_on_non_trading_day_only():
    # closed market (holiday/weekend) + only-stale → suppress the pointless retry
    assert should_suppress_retry(DEF_STALE_HEALTH, is_trading_day=False) is True
    # a real trading day → never suppress; the operator sees the normal prompt
    assert should_suppress_retry(DEF_STALE_HEALTH, is_trading_day=True) is False
    # closed market but a genuine auth failure → still surface it (not suppressed)
    bad = dict(DEF_STALE_HEALTH, authentication_status="EXPIRED")
    assert should_suppress_retry(bad, is_trading_day=False) is False


def test_configured_holiday_makes_today_non_trading():
    # the 2026-07-23 Revolution Day entry added to config makes it a non-trading day
    import datetime
    from core.egx_session import is_regular_trading_day
    assert is_regular_trading_day(datetime.date(2026, 7, 23), ["2026-07-23"]) is False
    # a normal Thursday without the holiday is a trading day
    assert is_regular_trading_day(datetime.date(2026, 7, 23), []) is True


def test_duplicate_detection_matches_only_rubix_processes():
    assert is_rubix_collector_process(
        r"D:\EGX_AI_Trader\venv\Scripts\python.exe D:\EGX_AI_Trader\scripts\rubix_collector_supervisor.py --adapter X")
    assert is_rubix_collector_process(
        "python -m rubix_feed.cli --url wss://eg-feed3.mubashertrade.com/websocket/price")
    # unrelated Python must NEVER match (so recovery can't kill them)
    assert not is_rubix_collector_process("python -m streamlit run app.py")
    assert not is_rubix_collector_process("python -m pytest tests/")
    assert not is_rubix_collector_process(r"C:\Python\python.exe train.py")
    assert not is_rubix_collector_process("")
    assert not is_rubix_collector_process(None)
