"""Byte-parity between the previous archive writers and the hash-while-writing ones.

The archive used to write each artifact and then re-open it to hash bytes it had just
produced. Building the payload in memory lets the same bytes be hashed once and written
once — but "the same bytes" is a claim about immutable evidence, so it is proven here
against a literal reimplementation of the previous code rather than asserted.

Every check uses a temporary root; no production archive is touched.
"""

from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from services import dataset_archive as da


# --------------------------------------------------------------------------- #
# The previous implementations, verbatim, as the reference
# --------------------------------------------------------------------------- #

def _legacy_write_frame(path: Path, frame: pd.DataFrame):
    payload = da.canonical_frame_bytes(frame)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            zipped.write(payload)
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(temporary, path)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _legacy_write_exact_frame(path: Path, frame: pd.DataFrame):
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
        payload[f"column_{position}"] = values
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return da._exact_frame_hash(frame)


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #

def _frame(rows=750, seed=7):
    generator = np.random.default_rng(seed)
    index = pd.date_range("2019-01-02", periods=rows, freq="B", name="Date")
    close = 10 + np.cumsum(generator.normal(0, 0.35, rows))
    return pd.DataFrame({
        "Open": close + generator.normal(0, 0.1, rows),
        "High": close + abs(generator.normal(0, 0.2, rows)),
        "Low": close - abs(generator.normal(0, 0.2, rows)),
        "Close": close,
        "Volume": generator.integers(1_000, 5_000_000, rows).astype("int64"),
    }, index=index)


FRAMES = {
    "typical": _frame(),
    "single_row": _frame(rows=1, seed=3),
    "ten_year": _frame(rows=2757, seed=11),
}


@pytest.mark.parametrize("name", sorted(FRAMES))
def test_the_gzip_artifact_is_byte_identical_to_the_previous_writer(name, tmp_path):
    frame = FRAMES[name]
    legacy_path, new_path = tmp_path / "legacy.csv.gz", tmp_path / "new.csv.gz"

    legacy_content, legacy_size = _legacy_write_frame(legacy_path, frame)
    new_content, new_size, new_file_hash = da._write_frame_hashed(new_path, frame)

    assert new_path.read_bytes() == legacy_path.read_bytes()
    assert new_path.stat().st_size == legacy_path.stat().st_size
    assert (new_content, new_size) == (legacy_content, legacy_size)
    # the streamed digest equals what a re-read of the file would have produced
    assert new_file_hash == da.sha256_file(legacy_path) == da.sha256_file(new_path)


@pytest.mark.parametrize("name", sorted(FRAMES))
def test_the_npz_artifact_is_byte_identical_to_the_previous_writer(name, tmp_path):
    frame = FRAMES[name]
    legacy_path, new_path = tmp_path / "legacy.npz", tmp_path / "new.npz"

    legacy_content = _legacy_write_exact_frame(legacy_path, frame)
    new_content, new_file_hash = da._write_exact_frame_hashed(new_path, frame)

    assert new_path.read_bytes() == legacy_path.read_bytes()
    assert new_path.stat().st_size == legacy_path.stat().st_size
    assert new_content == legacy_content
    assert new_file_hash == da.sha256_file(legacy_path) == da.sha256_file(new_path)


@pytest.mark.parametrize("name", sorted(FRAMES))
def test_the_decompressed_csv_content_is_unchanged(name, tmp_path):
    frame = FRAMES[name]
    legacy_path, new_path = tmp_path / "legacy.csv.gz", tmp_path / "new.csv.gz"
    _legacy_write_frame(legacy_path, frame)
    da._write_frame_hashed(new_path, frame)
    assert gzip.decompress(new_path.read_bytes()) == gzip.decompress(
        legacy_path.read_bytes())
    assert gzip.decompress(new_path.read_bytes()) == da.canonical_frame_bytes(frame)


@pytest.mark.parametrize("name", sorted(FRAMES))
def test_the_npz_round_trips_to_the_same_values_columns_and_dtypes(name, tmp_path):
    frame = FRAMES[name]
    path = tmp_path / "round.npz"
    da._write_exact_frame_hashed(path, frame)
    restored = da._read_exact_frame(path)

    # ``freq`` is index metadata the npz format has never stored, and a real market
    # frame carries none; the VALUES, columns and dtypes are what the contract covers.
    pd.testing.assert_frame_equal(restored, frame, check_freq=False)
    assert list(restored.columns) == list(frame.columns)
    assert restored.dtypes.to_dict() == frame.dtypes.to_dict()
    assert da._exact_frame_hash(restored) == da._exact_frame_hash(frame)


def test_the_gzip_container_stays_deterministic(tmp_path):
    """mtime=0 and no embedded filename: the same frame always yields the same bytes."""
    frame = FRAMES["typical"]
    first = tmp_path / "a.csv.gz"
    second = tmp_path / "b.csv.gz"
    da._write_frame_hashed(first, frame)
    da._write_frame_hashed(second, frame)
    assert first.read_bytes() == second.read_bytes()


def test_atomic_publication_leaves_no_temporary_file(tmp_path):
    frame = FRAMES["typical"]
    path = tmp_path / "nested" / "artifact.csv.gz"
    da._write_frame_hashed(path, frame)
    assert path.is_file()
    assert not list(tmp_path.rglob("*.tmp")), "a temporary file survived publication"


def test_the_legacy_helpers_still_exist_and_agree(tmp_path):
    """The old entry points remain callable and produce the same artifacts."""
    frame = FRAMES["typical"]
    legacy_csv, new_csv = tmp_path / "l.csv.gz", tmp_path / "n.csv.gz"
    legacy_npz, new_npz = tmp_path / "l.npz", tmp_path / "n.npz"

    assert da._write_frame(legacy_csv, frame) == _legacy_write_frame(new_csv, frame)
    assert legacy_csv.read_bytes() == new_csv.read_bytes()
    assert da._write_exact_frame(legacy_npz, frame) == _legacy_write_exact_frame(
        new_npz, frame)
    assert legacy_npz.read_bytes() == new_npz.read_bytes()


def test_a_capture_records_both_artifacts_with_matching_hashes(tmp_path):
    """The manifest fields a completed run publishes are unchanged in shape and value."""
    frame = FRAMES["typical"]
    archive = da.DatasetArchive(tmp_path / "run", {"probe": True})
    archive.capture("TEST.CA", frame, "normalized")

    record = archive.records[("normalized", "TEST.CA")]
    npz_path = archive.staging / record["path"]
    csv_path = archive.staging / record["human_readable_path"]

    assert npz_path.is_file() and csv_path.is_file()
    assert record["format"] == "npz"
    assert record["file_sha256"] == da.sha256_file(npz_path)
    assert record["human_readable_file_sha256"] == da.sha256_file(csv_path)
    assert record["content_sha256"] == da._exact_frame_hash(frame)
    assert record["rows"] == len(frame)
    assert record["columns"] == [str(column) for column in frame.columns]
    assert record["uncompressed_bytes"] == len(da.canonical_frame_bytes(frame))


def test_an_equivalent_duplicate_capture_is_not_written_twice(tmp_path):
    frame = FRAMES["typical"]
    archive = da.DatasetArchive(tmp_path / "run", {"probe": True})
    archive.capture("TEST.CA", frame, "normalized")
    first = archive.records[("normalized", "TEST.CA")]["file_sha256"]
    written_at = (archive.staging / archive.records[
        ("normalized", "TEST.CA")]["path"]).stat().st_mtime_ns

    archive.capture("TEST.CA", frame, "normalized")
    assert archive.records[("normalized", "TEST.CA")]["file_sha256"] == first
    assert (archive.staging / archive.records[
        ("normalized", "TEST.CA")]["path"]).stat().st_mtime_ns == written_at


def test_a_conflicting_duplicate_capture_fails_honestly(tmp_path):
    archive = da.DatasetArchive(tmp_path / "run", {"probe": True})
    archive.capture("TEST.CA", FRAMES["typical"], "normalized")
    with pytest.raises(RuntimeError, match="refusing ambiguous archive"):
        archive.capture("TEST.CA", FRAMES["ten_year"], "normalized")


def test_raw_and_normalized_remain_separate_evidence_stages(tmp_path):
    """Identical bytes at two stages are still two artifacts; they are not merged."""
    frame = FRAMES["typical"]
    archive = da.DatasetArchive(tmp_path / "run", {"probe": True})
    archive.capture("TEST.CA", frame, "raw")
    archive.capture("TEST.CA", frame, "normalized")

    assert ("raw", "TEST.CA") in archive.records
    assert ("normalized", "TEST.CA") in archive.records
    assert (archive.staging / "raw" / "TEST.CA.npz").is_file()
    assert (archive.staging / "normalized" / "TEST.CA.npz").is_file()


def test_no_production_archive_root_is_used_by_these_tests(tmp_path):
    archive = da.DatasetArchive(tmp_path / "run", {"probe": True})
    archive.capture("TEST.CA", FRAMES["typical"], "normalized")
    assert str(tmp_path) in str(archive.staging)
    assert "reports" not in str(archive.staging).replace(str(tmp_path), "")
