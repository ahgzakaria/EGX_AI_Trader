"""Contract tests for the bounded single-threaded archive writer.

The writer moves serialization off the scanner's critical path. That is only acceptable
if the evidence is unchanged and if no failure mode can deadlock a scan or publish an
archive that should not exist — which is what these pin.

Every test uses a temporary archive root.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pandas as pd
import pytest

from services import dataset_archive as da
from services.archive_capture_session import (
    DEFAULT_QUEUE_CAPACITY,
    ArchiveSessionClosed,
    ArchiveWriterFailed,
    QueuedArchiveCaptureSession,
    snapshot_frame,
    validate_archivable,
)


def _frame(rows=400, seed=1, base=10.0):
    generator = np.random.default_rng(seed)
    index = pd.date_range("2021-01-04", periods=rows, freq="B", name="Date")
    close = base + np.cumsum(generator.normal(0, 0.3, rows))
    frame = pd.DataFrame({
        "Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close,
        "Volume": generator.integers(1_000, 900_000, rows).astype("int64"),
    }, index=index)
    frame.attrs["market_data"] = {"provider": "eodhd"}
    return frame


@pytest.fixture
def archive(tmp_path):
    return da.DatasetArchive(tmp_path / "run", {"probe": True})


# --------------------------------------------------------------------------- #
# Writer shape
# --------------------------------------------------------------------------- #

def test_exactly_one_writer_thread_is_started(archive):
    before = {thread.name for thread in threading.enumerate()}
    session = QueuedArchiveCaptureSession(archive)
    try:
        writers = [thread for thread in threading.enumerate()
                   if thread.name == "archive-writer" and thread.name not in before]
        assert len(writers) == 1
        session.start()                      # idempotent: still one
        assert len([t for t in threading.enumerate()
                    if t.name == "archive-writer"]) == 1
    finally:
        session.close_and_drain()


def test_the_default_queue_capacity_is_two(archive):
    assert DEFAULT_QUEUE_CAPACITY == 2
    session = QueuedArchiveCaptureSession(archive)
    try:
        assert session.capacity == 2
    finally:
        session.close_and_drain()


def test_the_writer_terminates_on_success(archive):
    session = QueuedArchiveCaptureSession(archive)
    session.capture("AAA.CA", _frame(), "normalized")
    assert session.close_and_drain(timeout=30) is None
    assert not session._writer.is_alive()


def test_capture_order_is_deterministic(archive):
    session = QueuedArchiveCaptureSession(archive)
    symbols = [f"S{index:02d}.CA" for index in range(8)]
    for index, symbol in enumerate(symbols):
        session.capture(symbol, _frame(seed=index), "normalized")
    assert session.close_and_drain(timeout=60) is None
    written = [key[1] for key in archive.records if key[0] == "normalized"]
    assert written == symbols


# --------------------------------------------------------------------------- #
# Payload isolation
# --------------------------------------------------------------------------- #

def test_mutating_the_frame_after_capture_cannot_change_the_artifact(archive, tmp_path):
    frame = _frame()
    reference = da._exact_frame_hash(frame)

    session = QueuedArchiveCaptureSession(archive)
    session.capture("MUT.CA", frame, "normalized")
    # Mutate every way a caller plausibly could, while the writer may still be working.
    frame.iloc[0, frame.columns.get_loc("Close")] = 999999.0
    frame.attrs["market_data"]["provider"] = "tampered"
    assert session.close_and_drain(timeout=30) is None

    record = archive.records[("normalized", "MUT.CA")]
    assert record["content_sha256"] == reference
    restored = da._read_exact_frame(archive.staging / record["path"])
    assert da._exact_frame_hash(restored) == reference
    assert restored["Close"].iloc[0] != 999999.0


def test_snapshot_isolates_values_index_columns_and_attrs():
    frame = _frame(rows=20)
    copy = snapshot_frame(frame)

    frame.iloc[0, 0] = -1.0
    frame.attrs["market_data"]["provider"] = "tampered"
    frame.rename(columns={"Open": "Renamed"}, inplace=True)

    assert copy.iloc[0, 0] != -1.0
    assert copy.attrs["market_data"]["provider"] == "eodhd"
    assert list(copy.columns)[0] == "Open"


def test_snapshot_preserves_index_column_order_and_dtypes():
    frame = _frame(rows=30)
    copy = snapshot_frame(frame)
    assert list(copy.columns) == list(frame.columns)
    assert copy.dtypes.to_dict() == frame.dtypes.to_dict()
    assert copy.index.equals(frame.index)
    assert da._exact_frame_hash(copy) == da._exact_frame_hash(frame)


def test_an_object_dtype_column_is_rejected_at_the_capture_site(archive):
    """The archive supports numeric dtypes only; fail where it is attributable."""
    frame = _frame(rows=10)
    frame["Note"] = [{"mutable": True}] * len(frame)
    with pytest.raises(TypeError, match="Unsupported non-numeric"):
        validate_archivable(frame)
    session = QueuedArchiveCaptureSession(archive)
    try:
        with pytest.raises(TypeError):
            session.capture("OBJ.CA", frame, "normalized")
    finally:
        session.close_and_drain()


# --------------------------------------------------------------------------- #
# Deduplication
# --------------------------------------------------------------------------- #

def test_an_equivalent_duplicate_is_written_once(archive):
    frame = _frame()
    session = QueuedArchiveCaptureSession(archive)
    session.capture("DUP.CA", frame, "normalized")
    session.capture("DUP.CA", frame.copy(), "normalized")
    assert session.close_and_drain(timeout=30) is None

    assert session.metrics.unique_artifacts == 1
    assert session.metrics.equivalent_duplicates == 1
    assert session.metrics.written == 1


def test_a_conflicting_duplicate_fails_before_publication(archive):
    session = QueuedArchiveCaptureSession(archive)
    session.capture("CON.CA", _frame(seed=1), "normalized")
    with pytest.raises(RuntimeError, match="refusing ambiguous archive"):
        session.capture("CON.CA", _frame(seed=2), "normalized")
    assert session.metrics.conflicting_duplicates == 1
    assert not session.accepting
    session.close_and_drain()


def test_raw_and_normalized_stay_separate_even_when_identical(archive):
    frame = _frame()
    session = QueuedArchiveCaptureSession(archive)
    session.capture("SEP.CA", frame, "raw")
    session.capture("SEP.CA", frame, "normalized")
    assert session.close_and_drain(timeout=30) is None
    assert ("raw", "SEP.CA") in archive.records
    assert ("normalized", "SEP.CA") in archive.records
    assert session.metrics.written == 2


# --------------------------------------------------------------------------- #
# Backpressure, failure and cancellation
# --------------------------------------------------------------------------- #

class _BlockingArchive:
    """An archive whose writes block until released, to exercise a full queue."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.captured = []

    def capture(self, symbol, frame, stage, metadata=None):
        self.started.set()
        self.release.wait(timeout=30)
        self.captured.append((stage, symbol))


class _FailingArchive:
    def capture(self, symbol, frame, stage, metadata=None):
        raise OSError("disk is on fire")


def test_the_bounded_queue_applies_backpressure(archive):
    blocking = _BlockingArchive()
    session = QueuedArchiveCaptureSession(blocking, capacity=2)
    try:
        for index in range(3):                 # 1 in the writer + 2 queued
            session.capture(f"B{index}.CA", _frame(rows=30, seed=index), "normalized")
        blocking.started.wait(timeout=10)
        assert session._queue.qsize() <= 2
        assert session.metrics.max_queue_depth <= 2
    finally:
        blocking.release.set()
        session.close_and_drain(timeout=30)


def test_a_producer_blocked_on_a_full_queue_unblocks_on_cancellation(archive):
    blocking = _BlockingArchive()
    session = QueuedArchiveCaptureSession(blocking, capacity=1)
    raised = []

    def producer():
        try:
            for index in range(20):
                session.capture(f"C{index}.CA", _frame(rows=30, seed=index), "normalized")
        except ArchiveSessionClosed as error:
            raised.append(error)

    thread = threading.Thread(target=producer)
    thread.start()
    blocking.started.wait(timeout=10)
    time.sleep(0.2)
    started = time.monotonic()
    session.cancel()
    thread.join(timeout=10)
    latency = time.monotonic() - started

    assert not thread.is_alive(), "the producer deadlocked on a full queue"
    assert raised, "cancellation did not surface to the producer"
    assert latency < 5.0
    blocking.release.set()
    session.close_and_drain(timeout=30)


def test_a_producer_unblocks_after_writer_failure(archive):
    session = QueuedArchiveCaptureSession(_FailingArchive(), capacity=1)
    raised = []

    def producer():
        try:
            for index in range(40):
                session.capture(f"F{index}.CA", _frame(rows=30, seed=index), "normalized")
                time.sleep(0.01)
        except (ArchiveWriterFailed, ArchiveSessionClosed) as error:
            raised.append(error)

    thread = threading.Thread(target=producer)
    thread.start()
    thread.join(timeout=15)

    assert not thread.is_alive(), "the producer deadlocked after writer failure"
    assert raised, "the writer failure never reached the producer"
    assert session.failure is not None
    assert "OSError" in session.failure
    assert session.metrics.writer_failures == 1
    session.close_and_drain(timeout=10)


def test_writer_failure_is_surfaced_by_the_drain(archive):
    session = QueuedArchiveCaptureSession(_FailingArchive(), capacity=2)
    try:
        session.capture("X.CA", _frame(rows=30), "normalized")
    except ArchiveWriterFailed:
        pass
    failure = session.close_and_drain(timeout=15)
    assert failure is not None and "OSError" in failure
    assert not session._writer.is_alive()


def test_the_writer_terminates_on_cancellation(archive):
    session = QueuedArchiveCaptureSession(archive)
    session.capture("K.CA", _frame(rows=40), "normalized")
    session.cancel()
    session.close_and_drain(timeout=30)
    assert not session._writer.is_alive()


def test_a_cancelled_session_rejects_further_captures(archive):
    session = QueuedArchiveCaptureSession(archive)
    session.cancel()
    with pytest.raises(ArchiveSessionClosed):
        session.capture("Z.CA", _frame(rows=20), "normalized")
    session.close_and_drain(timeout=10)


def test_the_currently_writing_artifact_finishes_atomically_during_cancellation(archive):
    """Cancellation stops new work; it does not abandon a half-written file."""
    session = QueuedArchiveCaptureSession(archive)
    session.capture("ATOM.CA", _frame(rows=600), "normalized")
    session.cancel()
    session.close_and_drain(timeout=30)

    if ("normalized", "ATOM.CA") in archive.records:
        record = archive.records[("normalized", "ATOM.CA")]
        npz = archive.staging / record["path"]
        csv = archive.staging / record["human_readable_path"]
        assert npz.is_file() and csv.is_file()
        assert da.sha256_file(npz) == record["file_sha256"]
        assert da.sha256_file(csv) == record["human_readable_file_sha256"]
    assert not list(archive.staging.rglob("*.tmp")), "a partial artifact was left behind"


# --------------------------------------------------------------------------- #
# Evidence parity with the synchronous path
# --------------------------------------------------------------------------- #

def test_queued_output_is_byte_identical_to_synchronous_capture(tmp_path):
    frame = _frame(rows=900, seed=42)

    sync_archive = da.DatasetArchive(tmp_path / "sync", {"probe": True})
    sync_archive.capture("PAR.CA", frame, "normalized")

    async_archive = da.DatasetArchive(tmp_path / "async", {"probe": True})
    session = QueuedArchiveCaptureSession(async_archive)
    session.capture("PAR.CA", frame, "normalized")
    assert session.close_and_drain(timeout=60) is None

    sync_record = sync_archive.records[("normalized", "PAR.CA")]
    async_record = async_archive.records[("normalized", "PAR.CA")]

    for field in ("path", "human_readable_path", "format", "rows", "columns", "dtypes",
                  "content_sha256", "file_sha256", "human_readable_file_sha256",
                  "uncompressed_bytes", "start", "end"):
        assert sync_record[field] == async_record[field], field

    for relative in (sync_record["path"], sync_record["human_readable_path"]):
        assert (sync_archive.staging / relative).read_bytes() == (
            async_archive.staging / relative).read_bytes()


def test_the_archive_reader_validates_queued_output(tmp_path):
    frame = _frame(rows=300, seed=9)
    archive = da.DatasetArchive(tmp_path / "run", {"probe": True})
    session = QueuedArchiveCaptureSession(archive)
    session.capture("READ.CA", frame, "normalized")
    assert session.close_and_drain(timeout=30) is None

    record = archive.records[("normalized", "READ.CA")]
    restored = da._read_exact_frame(archive.staging / record["path"])
    pd.testing.assert_frame_equal(restored, frame, check_freq=False)
    assert da._exact_frame_hash(restored) == record["content_sha256"]


def test_the_session_never_publishes_a_run(tmp_path):
    """Publication stays on the scan worker; the writer only stages."""
    import pathlib
    from services import archive_capture_session

    source = pathlib.Path(archive_capture_session.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    body = code.split('"""', 2)[-1]
    for forbidden in ("finalize", "MANIFEST", "promote", "streamlit", "st."):
        assert forbidden not in body, f"the writer references {forbidden}"


def test_metrics_carry_no_frame_contents(archive):
    session = QueuedArchiveCaptureSession(archive)
    session.capture("MET.CA", _frame(rows=50), "normalized")
    session.close_and_drain(timeout=30)
    values = session.metrics.as_dict()
    assert set(values) >= {
        "capture_requests", "unique_artifacts", "equivalent_duplicates",
        "conflicting_duplicates", "enqueued", "written", "max_queue_depth",
        "producer_wait_seconds", "writer_active_seconds", "writer_idle_seconds",
        "drain_wait_seconds", "writer_failures", "cancelled"}
    assert all(isinstance(value, (int, float, bool)) for value in values.values())


def test_no_production_archive_root_is_touched(tmp_path):
    archive = da.DatasetArchive(tmp_path / "run", {"probe": True})
    session = QueuedArchiveCaptureSession(archive)
    session.capture("ISO.CA", _frame(rows=40), "normalized")
    session.close_and_drain(timeout=30)
    assert str(tmp_path) in str(archive.staging)
