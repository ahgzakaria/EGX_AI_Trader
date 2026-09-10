"""The single-instance lock: exactly one holder, and a dead one blocks nobody.

Written for the duplicate-supervisor race -- two supervisors passing the old
PID-file check at the same moment. That supervisor was retired with the Rubix
feed on 2026-09-10 and the lock outlived it: the dashboard launcher holds it
now, for the same reason. Two launcher windows meant two Streamlit processes
contending for port 8501, and the loser's failure looked like a broken app.

No GUI, no market database, no live feed is touched.
"""

from __future__ import annotations

import json
import os

import pytest

from scripts.launcher_process_utils import (
    InstanceAlreadyRunning,
    SingleInstanceLock,
    pid_record_is_current,
    process_creation_time,
    read_pid_record,
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
    assert pid_record_is_current(read_pid_record(meta)) is False
    fresh = SingleInstanceLock(lock, label="sup", meta_path=meta)
    fresh.acquire()                               # must not be blocked by stale metadata
    try:
        record = read_pid_record(meta)
        assert pid_record_is_current(record) is True
        assert record["pid"] == os.getpid()
    finally:
        fresh.release()


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


