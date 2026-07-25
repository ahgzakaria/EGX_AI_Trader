"""Immutable, deterministic input-data archives for experiment runs.

This module observes data at the provider boundary.  It never transforms a
trading input and therefore cannot influence strategy, AI, or portfolio logic.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from typing import Any

import numpy as np
import pandas as pd


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_value(payload), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
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


def _write_frame(path: Path, frame: pd.DataFrame) -> tuple[str, int]:
    payload = canonical_frame_bytes(frame)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    # mtime=0 makes the compressed bytes reproducible as well as the content.
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            zipped.write(payload)
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(temporary, path)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _write_exact_frame(path: Path, frame: pd.DataFrame) -> str:
    """Write a lossless, compressed NumPy archive without pickle objects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
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
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return _exact_frame_hash(frame)


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
        saved_hash = _write_exact_frame(self.staging / relative, frame)
        _, uncompressed_bytes = _write_frame(self.staging / csv_relative, frame)
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
            "file_sha256": sha256_file(self.staging / relative),
            "human_readable_file_sha256": sha256_file(self.staging / csv_relative),
            "uncompressed_bytes": uncompressed_bytes,
            "provider_metadata": _json_value(provider),
        }

    def finalize(self, failures=(), ai_predictions: pd.DataFrame | None = None) -> dict:
        if self.destination.exists():
            raise FileExistsError("Completed dataset archive already exists")
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
        (self.staging / "ARCHIVE_STATE.json").unlink(missing_ok=True)
        os.replace(self.staging, self.destination)
        return manifest

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
