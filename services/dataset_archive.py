"""Immutable, deterministic input-data archives for experiment runs.

This module observes data at the provider boundary.  It never transforms a
trading input and therefore cannot influence strategy, AI, or portfolio logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from typing import Any

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:                  # pragma: no cover - import guard
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.launcher_process_utils import (  # noqa: E402 - project root above
    _lock_exclusive,
    _unlock,
)

ARCHIVE_SCHEMA_VERSION = "1.0"
DATASET_FORMAT = "npz"
_ACTIVE_ARCHIVE: ContextVar["DatasetArchive | None"] = ContextVar(
    "active_dataset_archive", default=None
)
_REPLAY_FRAMES: ContextVar[dict[str, pd.DataFrame] | None] = ContextVar(
    "replay_dataset_frames", default=None
)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, pd.Timestamp)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except (TypeError, ValueError):
            pass
    try:
        return None if pd.isna(value) else str(value)
    except (TypeError, ValueError):
        return str(value)


def atomic_json(path: Path, payload: dict) -> None:
    """Write JSON atomically, closing and flushing the handle to disk first.

    Windows will not rename a directory while any file inside it is still open, so
    every writer in this module must release its handle deterministically rather
    than waiting for garbage collection.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(_json_value(payload), indent=2, ensure_ascii=False)
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_symbol(symbol: str) -> str:
    value = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(symbol))
    if not value or value in {".", ".."}:
        raise ValueError("Invalid symbol for dataset archive")
    return value


def canonical_frame_bytes(frame: pd.DataFrame) -> bytes:
    """Serialize candles deterministically without reducing float precision."""

    normalized = frame.copy()
    normalized.index = pd.to_datetime(normalized.index, errors="raise")
    normalized.index.name = normalized.index.name or "Date"
    buffer = io.StringIO(newline="")
    normalized.to_csv(
        buffer,
        index=True,
        lineterminator="\n",
        date_format="%Y-%m-%dT%H:%M:%S.%f%z",
        float_format="%.17g",
        na_rep="",
    )
    return buffer.getvalue().encode("utf-8")


def frame_hash(frame: pd.DataFrame) -> str:
    return _exact_frame_hash(frame)


def _exact_frame_hash(frame: pd.DataFrame) -> str:
    """Hash exact ndarray bytes, dtypes, columns, and datetime index."""
    digest = hashlib.sha256()
    digest.update(json.dumps([str(column) for column in frame.columns]).encode("utf-8"))
    index = pd.DatetimeIndex(frame.index)
    digest.update(index.asi8.tobytes(order="C"))
    digest.update(str(getattr(index.dtype, "unit", np.datetime_data(index.dtype)[0])).encode("ascii"))
    digest.update(str(index.tz or "").encode("utf-8"))
    digest.update(str(index.name or "Date").encode("utf-8"))
    for column in frame.columns:
        values = frame[column].to_numpy()
        digest.update(str(values.dtype).encode("ascii"))
        digest.update(np.ascontiguousarray(values).tobytes(order="C"))
    return digest.hexdigest()


def _publish_bytes(path: Path, blob: bytes) -> str:
    """Write ``blob`` atomically and return its SHA-256, hashed without re-reading.

    The archive previously wrote each artifact and then re-opened it to hash the bytes
    it had just produced. Building the payload in memory first lets the same bytes be
    hashed once and written once: the file on disk, its size and its digest are
    byte-for-byte what the previous implementation produced, because these ARE the same
    bytes. Temporary-file naming, flush, fsync and atomic replace are unchanged.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return hashlib.sha256(blob).hexdigest()


def canonical_gzip_bytes(payload: bytes) -> bytes:
    """Deterministic gzip container for ``payload`` (mtime=0, no embedded filename)."""
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as zipped:
        zipped.write(payload)
    return buffer.getvalue()


def exact_frame_npz_bytes(frame: pd.DataFrame) -> bytes:
    """The lossless NumPy archive payload for ``frame``, as bytes."""
    index = pd.DatetimeIndex(frame.index)
    payload = {
        "index_ns": index.asi8,
        "index_unit": np.array([
            str(getattr(index.dtype, "unit", np.datetime_data(index.dtype)[0]))
        ], dtype="U16"),
        "index_timezone": np.array([str(index.tz or "")], dtype="U128"),
        "index_name": np.array([str(index.name or "Date")], dtype="U128"),
        "columns": np.array([str(column) for column in frame.columns], dtype="U256"),
    }
    for position, column in enumerate(frame.columns):
        values = frame[column].to_numpy()
        if values.dtype.kind not in "biufcMm":
            raise TypeError(f"Unsupported non-numeric OHLCV dtype: {column}={values.dtype}")
        payload[f"column_{position}"] = values
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **payload)
    return buffer.getvalue()


def _write_frame(path: Path, frame: pd.DataFrame) -> tuple[str, int]:
    """Human-readable artifact. Returns ``(content_sha256, uncompressed_bytes)``."""
    payload = canonical_frame_bytes(frame)
    _publish_bytes(path, canonical_gzip_bytes(payload))
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _write_frame_hashed(path: Path, frame: pd.DataFrame) -> tuple[str, int, str]:
    """As :func:`_write_frame`, also returning the FILE digest computed while writing."""
    payload = canonical_frame_bytes(frame)
    file_digest = _publish_bytes(path, canonical_gzip_bytes(payload))
    return hashlib.sha256(payload).hexdigest(), len(payload), file_digest


def _write_exact_frame(path: Path, frame: pd.DataFrame) -> str:
    """Write a lossless, compressed NumPy archive without pickle objects."""
    _publish_bytes(path, exact_frame_npz_bytes(frame))
    return _exact_frame_hash(frame)


def _write_exact_frame_hashed(path: Path, frame: pd.DataFrame) -> tuple[str, str]:
    """As :func:`_write_exact_frame`, also returning the FILE digest."""
    file_digest = _publish_bytes(path, exact_frame_npz_bytes(frame))
    return _exact_frame_hash(frame), file_digest


def _read_exact_frame(path: Path) -> pd.DataFrame:
    with np.load(path, allow_pickle=False) as archive:
        columns = [str(value) for value in archive["columns"].tolist()]
        data = {
            column: archive[f"column_{position}"].copy()
            for position, column in enumerate(columns)
        }
        index_ns = archive["index_ns"].copy()
        index_unit = str(archive["index_unit"][0])
        timezone_name = str(archive["index_timezone"][0])
        index_name = str(archive["index_name"][0])
    if timezone_name:
        index = pd.to_datetime(index_ns, unit=index_unit, utc=True).tz_convert(timezone_name)
    else:
        index = pd.to_datetime(index_ns, unit=index_unit)
    if hasattr(index, "as_unit"):
        index = index.as_unit(index_unit)
    index.name = index_name
    return pd.DataFrame(data, index=index)


def _read_frame(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        frame = pd.read_csv(handle, index_col=0)
    frame.index = pd.to_datetime(frame.index, errors="raise")
    frame.index.name = "Date"
    return frame


def _compressed_content_hash(path: Path) -> str:
    """Hash the exact canonical payload stored inside a gzip artifact."""
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Finalization — idempotent, Windows-safe directory promotion
# --------------------------------------------------------------------------- #
#
# Windows refuses to rename a directory while ANY file inside it is open by any
# process — including an antivirus or indexer that opened a just-written file a
# millisecond earlier. That surfaces as ``PermissionError: [WinError 5]``, which
# is transient and self-clearing. Publishing a finished dataset must therefore
# retry that specific condition, and must be safe to call again afterwards: the
# archive is written once, but finalization may legitimately be attempted twice.

FINALIZED = "FINALIZED"
ALREADY_FINALIZED = "ALREADY_FINALIZED"
RECOVERED_FROM_STAGING = "RECOVERED_FROM_STAGING"
CONFLICT_PRESERVED = "CONFLICT_PRESERVED"
STAGING_MISSING = "STAGING_MISSING"
TRANSIENT_LOCK_TIMEOUT = "TRANSIENT_LOCK_TIMEOUT"
INVALID_MANIFEST = "INVALID_MANIFEST"

#: Statuses where a complete, valid dataset directory exists afterwards.
SUCCESS_STATUSES = frozenset({FINALIZED, ALREADY_FINALIZED, RECOVERED_FROM_STAGING})

#: Small, bounded backoff. Never an unlimited loop.
PROMOTION_ATTEMPTS = 5
PROMOTION_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.0)

STAGING_NAME = "dataset.staging"
DATASET_NAME = "dataset"


class DatasetFinalizationError(RuntimeError):
    """Raised only for structural faults that must not be retried."""


class TransientPromotionTimeout(DatasetFinalizationError):
    """The destination stayed locked for every bounded attempt."""


@dataclass(frozen=True)
class FinalizationResult(Mapping):
    """Typed outcome of one finalization attempt.

    Behaves as a read-only mapping over the resulting manifest so existing
    callers that treat the return value as the manifest keep working, while new
    callers can branch on :attr:`status` instead of a bare boolean.
    """

    status: str
    manifest: dict | None = None
    dataset_path: Path | None = None
    detail: str = ""
    attempts: int = 0
    quarantined: Path | None = None

    # -- mapping façade over the manifest ---------------------------------- #
    def __getitem__(self, key):
        return (self.manifest or {})[key]

    def __iter__(self):
        return iter(self.manifest or {})

    def __len__(self) -> int:
        return len(self.manifest or {})

    @property
    def ok(self) -> bool:
        """True when a complete dataset directory exists after this call."""
        return self.status in SUCCESS_STATUSES

    def summary(self) -> str:
        """One sanitized line safe for a UI warning — never a traceback."""
        detail = f" — {self.detail}" if self.detail else ""
        return f"{self.status}{detail}"


def read_manifest(directory: Path) -> dict | None:
    """Return a dataset directory's manifest, or ``None`` when unreadable."""
    path = Path(directory) / "MANIFEST.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def manifest_problem(manifest: dict | None, directory: Path | None = None) -> str:
    """Return why a dataset is not a valid completed archive, else ``""``."""
    if manifest is None:
        return "manifest missing or unreadable"
    if manifest.get("status") != "COMPLETED":
        return f"manifest status is {manifest.get('status') or 'unknown'}"
    if not isinstance(manifest.get("records"), list):
        return "manifest has no records list"
    if not manifest.get("dataset_hash"):
        return "manifest has no dataset hash"
    if directory is not None and (Path(directory) / "ARCHIVE_STATE.json").is_file():
        # An interrupted publish leaves the staging marker behind.
        return "interrupted archive marker present"
    return ""


def manifests_equivalent(left: dict | None, right: dict | None) -> bool:
    """Compare the identity of two archives without reading 891 files."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    keys = ("schema_version", "dataset_hash", "symbols_archived", "settings_sha256")
    if any(left.get(key) != right.get(key) for key in keys):
        return False
    return len(left.get("records") or []) == len(right.get("records") or [])


def _replace_directory(source: Path, destination: Path) -> None:
    """The single directory-rename call site.

    Kept as its own indirection so a test can make *promotion* fail without also
    breaking every unrelated ``os.replace`` used by the atomic file writers.
    """
    os.replace(source, destination)


def promote_directory(staging: Path, destination: Path, *,
                      attempts: int = PROMOTION_ATTEMPTS,
                      delays=PROMOTION_DELAYS, sleep=time.sleep) -> int:
    """Rename ``staging`` onto an ABSENT ``destination``; retry transient locks.

    Only ``PermissionError`` is retried — on Windows that is WinError 5 (access
    denied) and WinError 32 (sharing violation), both of which mean "something
    still holds a handle" and both of which clear on their own. Every other
    error, and every structural condition, is raised immediately: a conflict is
    never resolved by waiting, and a destination is never overwritten.
    """
    staging, destination = Path(staging), Path(destination)
    total = max(1, int(attempts))
    last_error: OSError | None = None
    for attempt in range(1, total + 1):
        # Re-verify the preconditions before every single attempt.
        if not staging.is_dir():
            raise DatasetFinalizationError(f"staging directory vanished: {staging.name}")
        if destination.exists():
            raise FileExistsError(f"destination already exists: {destination.name}")
        try:
            _replace_directory(staging, destination)
            return attempt
        except PermissionError as error:
            last_error = error
            if attempt >= total:
                break
            sleep(delays[min(attempt - 1, len(delays) - 1)])
    raise TransientPromotionTimeout(
        f"destination stayed locked after {total} attempts"
    ) from last_error


def _quarantine(directory: Path, suffix: str) -> Path:
    """Move a directory aside without ever deleting it."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = directory.with_name(f"{directory.name}.{suffix}-{stamp}")
    counter = 1
    while target.exists():
        target = directory.with_name(f"{directory.name}.{suffix}-{stamp}-{counter}")
        counter += 1
    os.replace(directory, target)
    return target


_FINALIZE_LOCKS: dict[str, threading.Lock] = {}
_FINALIZE_LOCKS_GUARD = threading.Lock()


def _finalize_lock_for(run_dir: Path) -> threading.Lock:
    """One in-process lock per run directory."""
    key = str(Path(run_dir).resolve() if Path(run_dir).parent.exists()
              else Path(run_dir))
    with _FINALIZE_LOCKS_GUARD:
        lock = _FINALIZE_LOCKS.get(key)
        if lock is None:
            lock = _FINALIZE_LOCKS[key] = threading.Lock()
        return lock


class _RunFinalizeLock:
    """Serialize finalization per run: in-process first, then cross-process.

    The lock file lives OUTSIDE the directory being renamed (under
    ``data/runtime``), so holding it can never be the thing that blocks the
    rename. A stale lock file is harmless: the OS drops the advisory lock when
    its owner exits, and an unobtainable OS lock never blocks the operation —
    the in-process lock plus the idempotent state machine keep it safe.
    """

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self._thread_lock = _finalize_lock_for(self.run_dir)
        safe = "".join(char if char.isalnum() or char in "._-" else "_"
                       for char in self.run_dir.name) or "run"
        self.path = (_PROJECT_ROOT / "data" / "runtime" / "dataset_finalize"
                     / f"{safe}.lock")
        self._fd = None

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
            _lock_exclusive(self._fd)
        except OSError:
            self._release(locked=False)
        return self

    def __exit__(self, *exc):
        try:
            self._release(locked=True)
        finally:
            if self._thread_lock.locked():
                self._thread_lock.release()
        return False

    def _release(self, *, locked: bool):
        if self._fd is None:
            return
        try:
            if locked:
                _unlock(self._fd)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None


def finalize_run_directory(run_dir: Path, *, expected_manifest: dict | None = None,
                           attempts: int = PROMOTION_ATTEMPTS,
                           delays=PROMOTION_DELAYS,
                           sleep=time.sleep) -> FinalizationResult:
    """Publish ``dataset.staging`` as ``dataset`` idempotently.

    Four explicit states, none of which ever overwrites or deletes user data:

    * **A** staging only → validate, promote (with bounded retry) → FINALIZED
    * **B** destination only → validate → ALREADY_FINALIZED
    * **C** both → equivalent: keep the destination and quarantine the redundant
      staging (ALREADY_FINALIZED); destination incomplete: quarantine it and
      promote staging (RECOVERED_FROM_STAGING); genuinely different: change
      nothing (CONFLICT_PRESERVED)
    * **D** neither → STAGING_MISSING
    """
    with _RunFinalizeLock(run_dir):
        return _finalize_unlocked(run_dir, expected_manifest=expected_manifest,
                                  attempts=attempts, delays=delays, sleep=sleep)


def _finalize_unlocked(run_dir: Path, *, expected_manifest: dict | None = None,
                       attempts: int = PROMOTION_ATTEMPTS,
                       delays=PROMOTION_DELAYS,
                       sleep=time.sleep) -> FinalizationResult:
    """The state machine itself. The caller must already hold the run lock."""
    run_dir = Path(run_dir)
    staging = run_dir / STAGING_NAME
    destination = run_dir / DATASET_NAME

    if True:
        staging_exists = staging.is_dir()
        destination_exists = destination.is_dir()

        # -- CASE D ---------------------------------------------------------- #
        if not staging_exists and not destination_exists:
            return FinalizationResult(
                STAGING_MISSING, None, None,
                "neither dataset nor dataset.staging exists")

        destination_manifest = read_manifest(destination) if destination_exists else None
        destination_problem = (manifest_problem(destination_manifest, destination)
                               if destination_exists else "")

        # -- CASE B ---------------------------------------------------------- #
        if destination_exists and not staging_exists:
            if destination_problem:
                return FinalizationResult(
                    INVALID_MANIFEST, destination_manifest, destination,
                    f"existing dataset is not usable: {destination_problem}")
            return FinalizationResult(
                ALREADY_FINALIZED, destination_manifest, destination,
                "dataset was already published")

        staging_manifest = expected_manifest or read_manifest(staging)
        staging_problem = manifest_problem(staging_manifest, None)

        # -- CASE C ---------------------------------------------------------- #
        if staging_exists and destination_exists:
            if not destination_problem:
                if manifests_equivalent(destination_manifest, staging_manifest):
                    quarantined = _quarantine(staging, "superseded")
                    return FinalizationResult(
                        ALREADY_FINALIZED, destination_manifest, destination,
                        "identical dataset already published; staging preserved",
                        quarantined=quarantined)
                return FinalizationResult(
                    CONFLICT_PRESERVED, destination_manifest, destination,
                    "dataset and dataset.staging differ; both preserved untouched")
            if staging_problem:
                return FinalizationResult(
                    INVALID_MANIFEST, None, None,
                    f"destination incomplete ({destination_problem}) and staging "
                    f"unusable ({staging_problem}); both preserved")
            quarantined = _quarantine(destination, "incomplete")
            used = promote_directory(staging, destination, attempts=attempts,
                                     delays=delays, sleep=sleep)
            return FinalizationResult(
                RECOVERED_FROM_STAGING, staging_manifest, destination,
                f"incomplete dataset quarantined as {quarantined.name}; "
                f"staging promoted", attempts=used, quarantined=quarantined)

        # -- CASE A ---------------------------------------------------------- #
        if staging_problem:
            return FinalizationResult(
                INVALID_MANIFEST, staging_manifest, None,
                f"staging is not a completed archive: {staging_problem}")
        try:
            used = promote_directory(staging, destination, attempts=attempts,
                                     delays=delays, sleep=sleep)
        except TransientPromotionTimeout as error:
            return FinalizationResult(
                TRANSIENT_LOCK_TIMEOUT, staging_manifest, None, str(error),
                attempts=attempts)
        return FinalizationResult(FINALIZED, staging_manifest, destination,
                                  "dataset published", attempts=used)


class DatasetArchive:
    """Stage exact run inputs and publish them atomically on completion."""

    def __init__(self, run_dir: Path, settings_snapshot: dict, symbols=()):
        self.run_dir = Path(run_dir)
        self.staging = self.run_dir / "dataset.staging"
        self.destination = self.run_dir / "dataset"
        self.settings_snapshot = settings_snapshot
        self.symbols = list(symbols)
        self.records: dict[tuple[str, str], dict] = {}
        self.staging.mkdir(parents=True, exist_ok=False)
        atomic_json(self.staging / "ARCHIVE_STATE.json", {
            "status": "STAGING",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "schema_version": ARCHIVE_SCHEMA_VERSION,
        })

    def capture(self, symbol: str, frame: pd.DataFrame, stage: str, metadata=None) -> None:
        if frame is None or frame.empty:
            return
        if stage not in {"raw", "normalized"}:
            raise ValueError(f"Unsupported archive stage: {stage}")
        symbol = str(symbol)
        content_hash = frame_hash(frame)
        key = (stage, symbol)
        existing = self.records.get(key)
        if existing:
            if existing["content_sha256"] != content_hash:
                raise RuntimeError(
                    f"Dataset changed inside one run for {symbol} ({stage}); refusing ambiguous archive"
                )
            return
        relative = Path(stage) / f"{_safe_symbol(symbol)}.{DATASET_FORMAT}"
        csv_relative = Path(stage) / f"{_safe_symbol(symbol)}.csv.gz"
        # Both digests come from the bytes as they are written, so the artifact is not
        # re-read from disk purely to hash what was just produced.
        saved_hash, npz_file_hash = _write_exact_frame_hashed(
            self.staging / relative, frame)
        _, uncompressed_bytes, csv_file_hash = _write_frame_hashed(
            self.staging / csv_relative, frame)
        provider = dict(frame.attrs.get("market_data", {}))
        provider.update(metadata or {})
        self.records[key] = {
            "symbol": symbol,
            "stage": stage,
            "path": relative.as_posix(),
            "format": DATASET_FORMAT,
            "human_readable_path": csv_relative.as_posix(),
            "rows": int(len(frame)),
            "columns": [str(column) for column in frame.columns],
            "dtypes": {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
            "start": pd.Timestamp(frame.index.min()).isoformat(),
            "end": pd.Timestamp(frame.index.max()).isoformat(),
            "content_sha256": saved_hash,
            "file_sha256": npz_file_hash,
            "human_readable_file_sha256": csv_file_hash,
            "uncompressed_bytes": uncompressed_bytes,
            "provider_metadata": _json_value(provider),
        }

    def finalize(self, failures=(), ai_predictions: pd.DataFrame | None = None,
                 *, attempts: int = PROMOTION_ATTEMPTS, delays=PROMOTION_DELAYS,
                 sleep=time.sleep) -> FinalizationResult:
        """Write the manifest and publish the dataset. Idempotent and typed.

        Returns a :class:`FinalizationResult`, which still behaves as the manifest
        mapping for existing callers. A previously published dataset is reported,
        never overwritten, and a transient Windows lock is retried rather than
        turned into a failed run.
        """
        # One lock spans the manifest write AND the promotion, so a second caller
        # can never write into a staging directory that is being renamed.
        with _RunFinalizeLock(self.run_dir):
            return self._finalize_locked(failures, ai_predictions, attempts=attempts,
                                         delays=delays, sleep=sleep)

    def _finalize_locked(self, failures, ai_predictions, *, attempts, delays,
                         sleep) -> FinalizationResult:
        if self.destination.is_dir() and not self.staging.is_dir():
            # Already published by an earlier attempt — report it, do not raise.
            return _finalize_unlocked(self.run_dir, attempts=attempts,
                                      delays=delays, sleep=sleep)
        symbols_path = self.staging / "symbols_used.csv"
        pd.DataFrame({"Symbol": self.symbols}).to_csv(symbols_path, index=False, encoding="utf-8")
        failure_rows = list(failures or [])
        pd.DataFrame(failure_rows).to_csv(
            self.staging / "failed_symbols.csv", index=False, encoding="utf-8"
        )
        prediction_info = None
        if ai_predictions is not None:
            prediction_path = self.staging / "ai_predictions.csv.gz"
            prediction_hash, _ = _write_frame(
                prediction_path,
                ai_predictions.set_index(ai_predictions.columns[0])
                if isinstance(ai_predictions.index, pd.RangeIndex) and len(ai_predictions.columns)
                else ai_predictions,
            )
            prediction_info = {
                "path": prediction_path.name,
                "rows": int(len(ai_predictions)),
                "content_sha256": prediction_hash,
                "file_sha256": sha256_file(prediction_path),
            }
        records = sorted(self.records.values(), key=lambda row: (row["stage"], row["symbol"]))
        normalized = [row for row in records if row["stage"] == "normalized"]
        dataset_digest = hashlib.sha256()
        for row in normalized:
            dataset_digest.update(row["symbol"].encode("utf-8"))
            dataset_digest.update(row["content_sha256"].encode("ascii"))
        manifest = {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "status": "COMPLETED",
            "format": DATASET_FORMAT,
            "dataset_hash": dataset_digest.hexdigest(),
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "symbols_requested": len(self.symbols),
            "symbols_archived": len({row["symbol"] for row in normalized}),
            "failures": len(failure_rows),
            "date_range": {
                "start": min((row["start"] for row in normalized), default=None),
                "end": max((row["end"] for row in normalized), default=None),
            },
            "records": records,
            "ai_predictions": prediction_info,
            "settings_sha256": hashlib.sha256(
                json.dumps(_json_value(self.settings_snapshot), sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "retention": "NO_AUTOMATIC_DELETION",
        }
        atomic_json(self.staging / "MANIFEST.json", manifest)
        try:
            (self.staging / "ARCHIVE_STATE.json").unlink(missing_ok=True)
        except PermissionError:
            # Another process momentarily holds the marker. Promotion below will
            # hit the same lock and report TRANSIENT_LOCK_TIMEOUT, which is the
            # honest outcome — never a half-published dataset.
            pass
        # Every writer above is context-managed, flushed and fsynced, so no handle
        # of ours can block the rename. The retry inside covers a handle held by
        # another process (antivirus, indexer) at the moment of promotion.
        return _finalize_unlocked(self.run_dir, expected_manifest=manifest,
                                  attempts=attempts, delays=delays, sleep=sleep)

    def abort(self, reason: str) -> None:
        if self.staging.exists():
            atomic_json(self.staging / "ARCHIVE_STATE.json", {
                "status": "INTERRUPTED",
                "reason": str(reason),
                "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
            })


def activate_archive(archive: DatasetArchive):
    return _ACTIVE_ARCHIVE.set(archive)


def deactivate_archive(token) -> None:
    # Runs are serialized by the current application.  Clearing explicitly is
    # safer than restoring an older nested test/run token as the active owner.
    _ACTIVE_ARCHIVE.set(None)


def capture_active(symbol: str, frame: pd.DataFrame, stage: str, metadata=None) -> None:
    archive = _ACTIVE_ARCHIVE.get()
    if archive is not None:
        archive.capture(symbol, frame, stage, metadata)


def load_archived_frames(run_dir: Path) -> tuple[dict[str, pd.DataFrame], dict]:
    dataset = Path(run_dir) / "dataset"
    manifest_path = dataset / "MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Run has no completed dataset archive; replay is unavailable")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "COMPLETED":
        raise RuntimeError("Dataset archive is incomplete")
    frames = {}
    for record in manifest.get("records", []):
        if record.get("stage") != "normalized":
            continue
        path = dataset / record["path"]
        if not path.is_file() or sha256_file(path) != record.get("file_sha256"):
            raise RuntimeError(f"Archived dataset integrity failure: {record.get('symbol')}")
        if record.get("format") == "npz" or path.suffix.lower() == ".npz":
            frame = _read_exact_frame(path)
            if _exact_frame_hash(frame) != record.get("content_sha256"):
                raise RuntimeError(f"Archived candle content changed: {record.get('symbol')}")
        else:
            # Backward-compatible verification for early Phase 8 CSV archives.
            if _compressed_content_hash(path) != record.get("content_sha256"):
                raise RuntimeError(f"Archived candle content changed: {record.get('symbol')}")
            frame = _read_frame(path)
        frame.attrs["market_data"] = record.get("provider_metadata", {})
        frames[str(record["symbol"])] = frame
    if not frames:
        raise RuntimeError("Dataset archive contains no normalized candles")
    return frames, manifest


@contextmanager
def replay_dataset(frames: dict[str, pd.DataFrame]):
    """Force provider reads to archived frames; network fallback is impossible."""

    token = _REPLAY_FRAMES.set(frames)
    try:
        yield
    finally:
        _REPLAY_FRAMES.reset(token)


def replay_frame(symbol: str) -> pd.DataFrame | None:
    frames = _REPLAY_FRAMES.get()
    if frames is None:
        return None
    if symbol not in frames:
        raise KeyError(f"Replay dataset is missing required symbol: {symbol}")
    return frames[symbol].copy()


def replay_active() -> bool:
    return _REPLAY_FRAMES.get() is not None


def discard_staging(run_dir: Path) -> None:
    """Explicit maintenance helper; never called automatically by retention."""

    staging = Path(run_dir) / "dataset.staging"
    if staging.exists():
        shutil.rmtree(staging)
