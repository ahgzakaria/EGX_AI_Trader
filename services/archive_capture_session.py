"""One run-scoped, bounded, single-threaded writer for immutable archive capture.

Archiving a universe scan costs roughly 65 ms of pure serialization per artifact —
canonical CSV bytes, deterministic gzip, and a compressed NumPy archive. With two
artifacts per successful symbol that is tens of seconds of CPU sitting directly on the
scanner's critical path, even though none of it is needed before the next symbol is
analysed.

This session moves that work onto ONE writer thread behind a small bounded queue, so
serialization overlaps symbol analysis while the archive itself stays strictly
sequential. It is deliberately a thin wrapper: the writer calls the existing, proven
:meth:`services.dataset_archive.DatasetArchive.capture`, so the bytes, hashes, file
names, manifest fields, atomic replacement and recovery behaviour are produced by
exactly the same code as before.

What this module does NOT do: it never publishes a run, never writes a manifest, never
touches a provider, a database or Streamlit. Publication stays on the scan worker and
happens only after the writer has drained and been joined.

Design constraints that are load-bearing rather than stylistic:

  * **one** writer thread — never a pool, so artifacts are written in capture order;
  * a **bounded** queue (default 2, matching the two stages of one symbol) so memory
    cannot grow with the universe;
  * the producer's ``put`` never blocks forever: it polls and re-checks cancellation,
    writer failure and session closure, so a dead writer can never deadlock the scan;
  * an archive failure is a **job failure**, never a silently degraded scan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

#: Two artifacts (raw + normalized) are produced per successful symbol, so a capacity of
#: two lets one symbol's pair be in flight while the next is analysed. Larger values buy
#: little overlap and retain more ten-year frames in memory.
DEFAULT_QUEUE_CAPACITY = 2

#: Producer poll slice. Short enough that cancellation is noticed promptly, long enough
#: that a waiting producer is not spinning.
_PUT_POLL_SECONDS = 0.05
_GET_POLL_SECONDS = 0.1


class ArchiveWriterFailed(RuntimeError):
    """The archive writer failed; evidence is incomplete and must not be published."""


class ArchiveSessionClosed(RuntimeError):
    """A capture was submitted after the session stopped accepting work."""


@dataclass
class _Task:
    sequence: int
    symbol: str
    stage: str
    frame: object
    metadata: dict = field(default_factory=dict)


@dataclass
class CaptureMetrics:
    """Safe counters only — never frame contents, symbols excepted as plain names."""

    capture_requests: int = 0
    unique_artifacts: int = 0
    equivalent_duplicates: int = 0
    conflicting_duplicates: int = 0
    enqueued: int = 0
    written: int = 0
    max_queue_depth: int = 0
    producer_wait_seconds: float = 0.0
    writer_active_seconds: float = 0.0
    writer_idle_seconds: float = 0.0
    drain_wait_seconds: float = 0.0
    writer_failures: int = 0
    cancelled: bool = False

    def as_dict(self):
        return dict(self.__dict__)


def snapshot_frame(frame):
    """An isolated copy the writer owns; later caller mutation cannot reach it.

    ``deep=True`` copies the value buffers, which is sufficient for the numeric dtypes
    the archive supports — and only those dtypes are supported, so there are no arbitrary
    Python objects for a deep copy to share. ``attrs`` is rebuilt one level down because
    pandas propagates that mapping by reference.
    """
    copy = frame.copy(deep=True)
    copy.attrs = {key: dict(value) if isinstance(value, dict) else value
                  for key, value in frame.attrs.items()}
    return copy


def validate_archivable(frame):
    """Fail fast on the scanner thread for dtypes the archive cannot represent.

    The NumPy writer rejects non-numeric columns anyway; catching it here turns a
    writer-thread failure into an immediate, attributable error at the capture site.
    """
    for column in frame.columns:
        kind = frame[column].to_numpy().dtype.kind
        if kind not in "biufcMm":
            raise TypeError(
                f"Unsupported non-numeric OHLCV dtype: {column}={frame[column].dtype}")


class QueuedArchiveCaptureSession:
    """Accept captures on the scanner thread; serialize and write them on one thread."""

    def __init__(self, archive, capacity=DEFAULT_QUEUE_CAPACITY, *,
                 cancellation_event=None, start=True):
        self._archive = archive
        self._queue = queue.Queue(maxsize=max(1, int(capacity)))
        self._lock = threading.RLock()
        self._reserved: dict[tuple[str, str], str] = {}
        self._sequence = 0
        self._closed = False
        self._cancelled = cancellation_event or threading.Event()
        self._failure = None
        self._writer = None
        self.capacity = max(1, int(capacity))
        self.metrics = CaptureMetrics()
        if start:
            self.start()

    # -- lifecycle ---------------------------------------------------------- #

    def start(self):
        if self._writer is not None:
            return
        # Daemon only so a hung writer can never prevent process shutdown; normal
        # completion and cancellation both join it explicitly.
        self._writer = threading.Thread(target=self._run, name="archive-writer",
                                        daemon=True)
        self._writer.start()

    @property
    def failure(self):
        with self._lock:
            return self._failure

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def accepting(self) -> bool:
        with self._lock:
            return not self._closed and self._failure is None and not self.cancelled

    def cancel(self):
        """Stop accepting captures and unblock any waiting producer."""
        self._cancelled.set()
        with self._lock:
            self._closed = True
            self.metrics.cancelled = True

    def _fail(self, error):
        with self._lock:
            if self._failure is None:
                self._failure = f"{type(error).__name__}: {str(error)[:300]}"
                self.metrics.writer_failures += 1
            self._closed = True

    # -- producer ----------------------------------------------------------- #

    def capture(self, symbol, frame, stage, metadata=None):
        """Reserve the logical key and enqueue an isolated snapshot.

        Mirrors the synchronous contract: an empty frame is ignored, an unknown stage is
        rejected, an equivalent duplicate is accepted without a second write, and a
        conflicting duplicate for the same key fails immediately.
        """
        from services.dataset_archive import frame_hash

        if frame is None or getattr(frame, "empty", False):
            return
        if stage not in {"raw", "normalized"}:
            raise ValueError(f"Unsupported archive stage: {stage}")

        failure = self.failure
        if failure is not None:
            raise ArchiveWriterFailed(failure)
        if self.cancelled or self._closed:
            raise ArchiveSessionClosed("archive session is no longer accepting captures")

        validate_archivable(frame)
        content_hash = frame_hash(frame)
        key = (stage, str(symbol))

        with self._lock:
            self.metrics.capture_requests += 1
            existing = self._reserved.get(key)
            if existing is not None:
                if existing != content_hash:
                    self.metrics.conflicting_duplicates += 1
                    self._closed = True
                    raise RuntimeError(
                        f"Dataset changed inside one run for {symbol} ({stage}); "
                        f"refusing ambiguous archive")
                self.metrics.equivalent_duplicates += 1
                return
            self._reserved[key] = content_hash
            self._sequence += 1
            self.metrics.unique_artifacts += 1
            task = _Task(sequence=self._sequence, symbol=str(symbol), stage=stage,
                         frame=snapshot_frame(frame), metadata=dict(metadata or {}))

        self._put(task)

    def _put(self, task):
        """Bounded put. Never blocks forever: re-checks failure and cancellation."""
        started = time.monotonic()
        while True:
            failure = self.failure
            if failure is not None:
                raise ArchiveWriterFailed(failure)
            if self.cancelled:
                raise ArchiveSessionClosed("archive session cancelled")
            try:
                self._queue.put(task, timeout=_PUT_POLL_SECONDS)
            except queue.Full:
                continue
            with self._lock:
                self.metrics.enqueued += 1
                self.metrics.producer_wait_seconds += time.monotonic() - started
                self.metrics.max_queue_depth = max(self.metrics.max_queue_depth,
                                                   self._queue.qsize())
            return

    # -- writer ------------------------------------------------------------- #

    def _run(self):
        while True:
            idle_started = time.monotonic()
            try:
                task = self._queue.get(timeout=_GET_POLL_SECONDS)
            except queue.Empty:
                with self._lock:
                    self.metrics.writer_idle_seconds += time.monotonic() - idle_started
                if self._closed and self._queue.empty():
                    return
                continue
            with self._lock:
                self.metrics.writer_idle_seconds += time.monotonic() - idle_started
            if task is None:                       # sentinel
                self._queue.task_done()
                return
            started = time.monotonic()
            try:
                # The proven synchronous implementation does the serialization, hashing,
                # atomic publication and manifest record — unchanged.
                self._archive.capture(task.symbol, task.frame, task.stage, task.metadata)
                with self._lock:
                    self.metrics.written += 1
            except Exception as error:             # sanitized; never re-raised here
                logger.exception("Archive writer failed for %s (%s)",
                                 task.symbol, task.stage)
                self._fail(error)
                self._queue.task_done()
                return
            finally:
                with self._lock:
                    self.metrics.writer_active_seconds += time.monotonic() - started
            self._queue.task_done()

    # -- completion --------------------------------------------------------- #

    def close_and_drain(self, timeout=120.0):
        """Stop accepting captures, drain the queue and join the writer.

        Returns the sanitized writer failure, or ``None``. The caller must not publish a
        completed archive when this returns a failure or when the session was cancelled.
        """
        started = time.monotonic()
        with self._lock:
            self._closed = True
        try:
            self._queue.put(None, timeout=_PUT_POLL_SECONDS)
        except queue.Full:
            pass                                   # the writer exits on _closed anyway
        if self._writer is not None:
            self._writer.join(timeout=timeout)
            if self._writer.is_alive():
                self._fail(TimeoutError("archive writer did not finish in time"))
        with self._lock:
            self.metrics.drain_wait_seconds += time.monotonic() - started
        return self.failure

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.cancel()
        self.close_and_drain()
        return False
