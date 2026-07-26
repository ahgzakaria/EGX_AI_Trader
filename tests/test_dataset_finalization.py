"""Windows-safe, idempotent dataset finalization.

Regression origin: RUN_20260726_212722 finished a 220-symbol scan, wrote a valid
891-file staging archive, then died on ``os.replace(dataset.staging, dataset)``
with ``PermissionError: [WinError 5]`` — a transient handle held by another
process at that instant. There was no retry and no idempotent recovery, so a
storage hiccup discarded a completed scan.

Every test writes to ``tmp_path`` only; none touches the real reports directory.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from services import dataset_archive as archive_module
from services.dataset_archive import (
    ALREADY_FINALIZED,
    CONFLICT_PRESERVED,
    FINALIZED,
    INVALID_MANIFEST,
    RECOVERED_FROM_STAGING,
    STAGING_MISSING,
    TRANSIENT_LOCK_TIMEOUT,
    DatasetArchive,
    FinalizationResult,
    TransientPromotionTimeout,
    finalize_run_directory,
    promote_directory,
)

REAL_REPORTS = Path(__file__).resolve().parents[1] / "reports"


@pytest.fixture(autouse=True)
def _never_touch_real_reports():
    """A finalization test must never write into the operator's real runs."""
    before = sorted(p.name for p in REAL_REPORTS.iterdir()) if REAL_REPORTS.is_dir() else []
    yield
    after = sorted(p.name for p in REAL_REPORTS.iterdir()) if REAL_REPORTS.is_dir() else []
    assert before == after, "a test modified the real reports directory"


def _candles(rows: int = 8) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=rows, freq="B", name="Date")
    values = np.arange(rows, dtype=float)
    return pd.DataFrame({
        "Open": 10 + values, "High": 11 + values, "Low": 9 + values,
        "Close": 10.5 + values, "Volume": 1000 + values,
    }, index=index)


def _staged_run(tmp_path: Path, name: str = "RUN_TEST") -> tuple[Path, DatasetArchive]:
    run_dir = tmp_path / name
    run_dir.mkdir()
    archive = DatasetArchive(run_dir, {"data": {"interval": "1d"}}, ["COMI.CA"])
    frame = _candles()
    archive.capture("COMI.CA", frame, "raw", {"provider": "eodhd"})
    archive.capture("COMI.CA", frame, "normalized", {"provider": "eodhd"})
    return run_dir, archive


def _manifest(directory: Path) -> dict:
    return json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))


def _fake_dataset(directory: Path, manifest: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


# --------------------------------------------------------------------------- #
# CASE A — staging exists, destination absent
# --------------------------------------------------------------------------- #

def test_staging_promoted_when_destination_absent(tmp_path):
    run_dir, archive = _staged_run(tmp_path)
    result = archive.finalize()

    assert result.status == FINALIZED
    assert result.ok and result.attempts == 1
    assert (run_dir / "dataset").is_dir()
    assert not (run_dir / "dataset.staging").exists()
    assert result.dataset_path == run_dir / "dataset"
    # Backwards compatible: the result still behaves as the manifest mapping.
    assert result["dataset_hash"] == _manifest(run_dir / "dataset")["dataset_hash"]
    assert len(result["records"]) == 2


def test_neither_directory_exists_is_typed_not_fabricated(tmp_path):
    run_dir = tmp_path / "RUN_EMPTY"
    run_dir.mkdir()
    result = finalize_run_directory(run_dir)
    assert result.status == STAGING_MISSING
    assert result.manifest is None and not result.ok
    assert not (run_dir / "dataset").exists()


# --------------------------------------------------------------------------- #
# CASE B — destination already finalized
# --------------------------------------------------------------------------- #

def test_destination_already_finalized_is_idempotent(tmp_path):
    run_dir, archive = _staged_run(tmp_path)
    first = archive.finalize()
    second = finalize_run_directory(run_dir)

    assert first.status == FINALIZED
    assert second.status == ALREADY_FINALIZED
    assert second.ok
    assert second["dataset_hash"] == first["dataset_hash"]


def test_repeated_finalize_call_returns_the_same_result(tmp_path):
    _run_dir, archive = _staged_run(tmp_path)
    first = archive.finalize()
    second = archive.finalize()
    third = archive.finalize()

    assert first.status == FINALIZED
    assert second.status == third.status == ALREADY_FINALIZED
    assert second["dataset_hash"] == third["dataset_hash"] == first["dataset_hash"]


def test_incomplete_destination_alone_is_reported_not_overwritten(tmp_path):
    run_dir = tmp_path / "RUN_PARTIAL"
    _fake_dataset(run_dir / "dataset", {"status": "STAGING"})
    result = finalize_run_directory(run_dir)
    assert result.status == INVALID_MANIFEST
    assert not result.ok
    assert (run_dir / "dataset" / "MANIFEST.json").is_file()


# --------------------------------------------------------------------------- #
# CASE C — both directories exist
# --------------------------------------------------------------------------- #

def test_equivalent_directories_keep_destination_and_preserve_staging(tmp_path):
    run_dir, archive = _staged_run(tmp_path)
    published = archive.finalize()
    manifest = _manifest(run_dir / "dataset")

    # A redundant staging reappears (e.g. a retried run wrote it again).
    staging = run_dir / "dataset.staging"
    _fake_dataset(staging, manifest)
    before = (run_dir / "dataset" / "MANIFEST.json").read_bytes()

    result = finalize_run_directory(run_dir)

    assert result.status == ALREADY_FINALIZED
    assert result.ok
    assert (run_dir / "dataset" / "MANIFEST.json").read_bytes() == before
    assert not staging.exists()                      # moved aside, never deleted
    assert result.quarantined is not None and result.quarantined.is_dir()
    assert (result.quarantined / "MANIFEST.json").is_file()
    assert published["dataset_hash"] == result["dataset_hash"]


def test_conflicting_directories_preserve_both_and_change_nothing(tmp_path):
    run_dir = tmp_path / "RUN_CONFLICT"
    destination = _fake_dataset(run_dir / "dataset", {
        "schema_version": "1.0", "status": "COMPLETED", "dataset_hash": "aaa",
        "symbols_archived": 2, "records": [{"symbol": "A"}, {"symbol": "B"}],
    })
    staging = _fake_dataset(run_dir / "dataset.staging", {
        "schema_version": "1.0", "status": "COMPLETED", "dataset_hash": "bbb",
        "symbols_archived": 3, "records": [{"symbol": "C"}],
    })
    destination_bytes = (destination / "MANIFEST.json").read_bytes()
    staging_bytes = (staging / "MANIFEST.json").read_bytes()

    result = finalize_run_directory(run_dir)

    assert result.status == CONFLICT_PRESERVED
    assert not result.ok
    assert (destination / "MANIFEST.json").read_bytes() == destination_bytes
    assert (staging / "MANIFEST.json").read_bytes() == staging_bytes
    assert result.quarantined is None


def test_incomplete_destination_plus_valid_staging_is_recovered(tmp_path):
    run_dir, archive = _staged_run(tmp_path)
    # Simulate an interrupted publish: a half-written destination is present.
    _fake_dataset(run_dir / "dataset", {"status": "STAGING"})

    result = archive.finalize()

    assert result.status == RECOVERED_FROM_STAGING
    assert result.ok
    assert result.quarantined is not None and result.quarantined.is_dir()
    assert json.loads((result.quarantined / "MANIFEST.json").read_text())["status"] == "STAGING"
    published = _manifest(run_dir / "dataset")
    assert published["status"] == "COMPLETED"
    assert not (run_dir / "dataset.staging").exists()


def test_malformed_staging_manifest_is_reported_without_promotion(tmp_path):
    run_dir = tmp_path / "RUN_BAD"
    staging = run_dir / "dataset.staging"
    staging.mkdir(parents=True)
    (staging / "MANIFEST.json").write_text("{ not json", encoding="utf-8")

    result = finalize_run_directory(run_dir)

    assert result.status == INVALID_MANIFEST
    assert not result.ok
    assert staging.is_dir() and not (run_dir / "dataset").exists()


# --------------------------------------------------------------------------- #
# Retry policy
# --------------------------------------------------------------------------- #

class _FlakyReplace:
    """Fail with WinError 5 for the first ``failures`` calls, then succeed."""

    def __init__(self, failures: int, real, error=None):
        self.failures = failures
        self.real = real
        self.calls = 0
        self.error = error

    def __call__(self, src, dst):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error or PermissionError(13, "Access is denied", str(dst), 5)
        return self.real(src, dst)


def test_transient_winerror5_then_success(tmp_path, monkeypatch):
    run_dir, archive = _staged_run(tmp_path)
    flaky = _FlakyReplace(2, os.replace)
    monkeypatch.setattr(archive_module, "_replace_directory", flaky)
    slept: list[float] = []

    result = archive.finalize(sleep=slept.append)

    assert result.status == FINALIZED
    assert result.attempts == 3
    assert slept == [0.1, 0.2]                        # bounded exponential backoff
    assert (run_dir / "dataset").is_dir()


def test_winerror5_exhaustion_is_typed_and_preserves_staging(tmp_path, monkeypatch):
    run_dir, archive = _staged_run(tmp_path)
    flaky = _FlakyReplace(99, os.replace)
    monkeypatch.setattr(archive_module, "_replace_directory", flaky)
    slept: list[float] = []

    result = archive.finalize(sleep=slept.append)

    assert result.status == TRANSIENT_LOCK_TIMEOUT
    assert not result.ok
    assert len(slept) == 4                            # 5 attempts => 4 waits
    assert (run_dir / "dataset.staging").is_dir()     # nothing lost
    assert not (run_dir / "dataset").exists()
    assert result.manifest is not None                # the manifest is still known


def test_non_transient_error_is_not_retried(tmp_path, monkeypatch):
    run_dir, archive = _staged_run(tmp_path)
    boom = _FlakyReplace(99, os.replace, error=OSError(22, "Invalid argument"))
    monkeypatch.setattr(archive_module, "_replace_directory", boom)

    with pytest.raises(OSError) as excinfo:
        archive.finalize(sleep=lambda _seconds: None)

    assert not isinstance(excinfo.value, PermissionError)
    assert boom.calls == 1                            # exactly one attempt
    assert (run_dir / "dataset.staging").is_dir()


def test_promotion_refuses_to_overwrite_an_existing_destination(tmp_path):
    run_dir = tmp_path / "RUN_X"
    staging = _fake_dataset(run_dir / "dataset.staging", {"status": "COMPLETED"})
    destination = _fake_dataset(run_dir / "dataset", {"status": "COMPLETED"})
    kept = (destination / "MANIFEST.json").read_bytes()

    with pytest.raises(FileExistsError):
        promote_directory(staging, destination)

    assert (destination / "MANIFEST.json").read_bytes() == kept
    assert staging.is_dir()


def test_promotion_rechecks_preconditions_before_each_retry(tmp_path, monkeypatch):
    """A destination appearing mid-retry must abort, never overwrite."""
    run_dir = tmp_path / "RUN_RACE"
    staging = _fake_dataset(run_dir / "dataset.staging", {"status": "COMPLETED"})
    destination = run_dir / "dataset"
    calls = {"n": 0}

    def _racing_replace(src, dst):
        calls["n"] += 1
        _fake_dataset(Path(dst), {"status": "COMPLETED"})   # another writer wins
        raise PermissionError(13, "Access is denied", str(dst), 5)

    monkeypatch.setattr(archive_module, "_replace_directory", _racing_replace)
    with pytest.raises(FileExistsError):
        promote_directory(staging, destination, sleep=lambda _s: None)
    assert calls["n"] == 1
    assert staging.is_dir()


def test_timeout_raises_a_typed_error_from_promote_directory(tmp_path, monkeypatch):
    run_dir = tmp_path / "RUN_T"
    staging = _fake_dataset(run_dir / "dataset.staging", {"status": "COMPLETED"})
    monkeypatch.setattr(archive_module, "_replace_directory",
                        _FlakyReplace(99, os.replace))
    with pytest.raises(TransientPromotionTimeout):
        promote_directory(staging, run_dir / "dataset", sleep=lambda _s: None)
    assert staging.is_dir()


# --------------------------------------------------------------------------- #
# Handles and concurrency
# --------------------------------------------------------------------------- #

def test_an_open_handle_inside_staging_blocks_promotion_and_loses_nothing(tmp_path):
    """The exact production failure mode, reproduced deterministically."""
    run_dir, archive = _staged_run(tmp_path)
    staging = run_dir / "dataset.staging"
    # A captured data file, not the state marker finalize itself removes.
    held = next(path for path in (staging / "normalized").rglob("*") if path.is_file())

    with held.open("rb"):
        result = archive.finalize(sleep=lambda _seconds: None)

    if os.name == "nt":                               # POSIX allows the rename
        assert result.status == TRANSIENT_LOCK_TIMEOUT
        assert staging.is_dir() and not (run_dir / "dataset").exists()
        # The very next attempt, once the handle is gone, must succeed.
        assert archive.finalize().status == FINALIZED
    assert (run_dir / "dataset").is_dir()


def test_all_writers_are_closed_before_promotion(tmp_path):
    """No writer of ours may still hold a handle when the rename happens."""
    run_dir, archive = _staged_run(tmp_path)
    staging = run_dir / "dataset.staging"
    assert not list(staging.rglob("*.tmp"))           # no temp file left behind
    result = archive.finalize()
    assert result.status == FINALIZED
    dataset = run_dir / "dataset"
    assert not list(dataset.rglob("*.tmp"))
    assert not (dataset / "ARCHIVE_STATE.json").exists()


def test_concurrent_finalize_calls_produce_one_dataset(tmp_path):
    run_dir, archive = _staged_run(tmp_path)
    results: list[FinalizationResult] = []
    errors: list[BaseException] = []

    def worker():
        try:
            results.append(archive.finalize())
        except BaseException as error:                # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"concurrent finalize raised: {errors!r}"
    assert len(results) == 4
    assert sum(result.status == FINALIZED for result in results) == 1
    assert all(result.ok for result in results)
    assert (run_dir / "dataset").is_dir()
    hashes = {result["dataset_hash"] for result in results}
    assert len(hashes) == 1


def test_finalize_lock_file_lives_outside_the_renamed_directory(tmp_path):
    run_dir, archive = _staged_run(tmp_path)
    lock = archive_module._RunFinalizeLock(run_dir)
    assert "dataset.staging" not in str(lock.path)
    assert "runtime" in str(lock.path).replace("\\", "/")
    archive.finalize()
    assert not list((run_dir / "dataset").rglob("*.lock"))


# --------------------------------------------------------------------------- #
# Interrupted-run recovery and Windows path behaviour
# --------------------------------------------------------------------------- #

def test_interrupted_finalize_recovers_on_a_later_call(tmp_path):
    """Exactly RUN_20260726_212722: valid staging, no destination, no crash."""
    run_dir, archive = _staged_run(tmp_path)

    # The production crash: the manifest was written, the rename never happened.
    blocked = _FlakyReplace(99, os.replace)
    original = archive_module._replace_directory
    archive_module._replace_directory = blocked
    try:
        stalled = archive.finalize(sleep=lambda _seconds: None)
    finally:
        archive_module._replace_directory = original

    assert stalled.status == TRANSIENT_LOCK_TIMEOUT
    assert (run_dir / "dataset.staging" / "MANIFEST.json").is_file()
    assert not (run_dir / "dataset").exists()

    # Recovery uses the same logic, needs no rescan, and loses nothing.
    recovered = finalize_run_directory(run_dir)
    assert recovered.status == FINALIZED
    assert (run_dir / "dataset" / "MANIFEST.json").is_file()

    # A second, independent recovery attempt must be a no-op.
    again = finalize_run_directory(run_dir)
    assert again.status == ALREADY_FINALIZED
    assert again["dataset_hash"] == recovered["dataset_hash"]


def test_windows_style_paths_with_spaces_and_dots(tmp_path):
    run_dir = tmp_path / "RUN 2026.07.26 212722"
    run_dir.mkdir()
    archive = DatasetArchive(run_dir, {}, ["COMI.CA"])
    archive.capture("COMI.CA", _candles(), "normalized", {})
    result = archive.finalize()
    assert result.status == FINALIZED
    assert (run_dir / "dataset").is_dir()


def test_finalization_never_deletes_user_data(tmp_path):
    run_dir, archive = _staged_run(tmp_path)
    staged_files = {path.name for path in (run_dir / "dataset.staging").rglob("*")
                    if path.is_file()}
    archive.finalize()
    published = {path.name for path in (run_dir / "dataset").rglob("*") if path.is_file()}
    assert staged_files - {"ARCHIVE_STATE.json"} <= published


# --------------------------------------------------------------------------- #
# A storage fault must never discard a completed scan
# --------------------------------------------------------------------------- #

def test_complete_keeps_the_run_when_publication_fails(tmp_path, monkeypatch):
    """experiment.complete() records the fault and still writes every artifact."""
    from services import experiment_tracking

    monkeypatch.setattr(experiment_tracking, "REPORTS_ROOT", tmp_path / "reports")
    run = experiment_tracking.ExperimentRun("SCAN", "LIVE_SCAN", ["COMI.CA"])
    run.dataset_archive.capture("COMI.CA", _candles(), "normalized", {})
    run.save_records("scan_results.csv", [{"Ticker": "COMI.CA", "Signal": "BUY"}])

    blocked = _FlakyReplace(99, os.replace)
    monkeypatch.setattr(archive_module, "_replace_directory", blocked)
    monkeypatch.setattr(archive_module.time, "sleep", lambda _seconds: None)

    metadata = run.complete(metrics={"Stocks": 1}, successful_symbols=1)

    assert metadata["status"] == "COMPLETED"
    assert metadata["dataset_finalization"] == TRANSIENT_LOCK_TIMEOUT
    assert metadata["dataset_finalization_ok"] is False
    # The scan evidence survives untouched, and the staged dataset is preserved.
    assert (run.run_dir / "scan_results.csv").is_file()
    assert (run.run_dir / "run_metadata.json").is_file()
    assert (run.run_dir / "dataset.staging" / "MANIFEST.json").is_file()

    # And it can be published later without rescanning.
    monkeypatch.setattr(archive_module, "_replace_directory", os.replace)
    recovered = finalize_run_directory(run.run_dir)
    assert recovered.status == FINALIZED
    assert (run.run_dir / "dataset" / "MANIFEST.json").is_file()


def test_complete_records_success_normally(tmp_path, monkeypatch):
    from services import experiment_tracking

    monkeypatch.setattr(experiment_tracking, "REPORTS_ROOT", tmp_path / "reports")
    run = experiment_tracking.ExperimentRun("SCAN", "LIVE_SCAN", ["COMI.CA"])
    run.dataset_archive.capture("COMI.CA", _candles(), "normalized", {})
    metadata = run.complete(metrics={"Stocks": 1}, successful_symbols=1)

    assert metadata["dataset_finalization"] == FINALIZED
    assert metadata["dataset_finalization_ok"] is True
    assert metadata["dataset_archive_status"] == "COMPLETED"
    assert (run.run_dir / "dataset").is_dir()


def test_dashboard_shows_a_typed_warning_instead_of_a_traceback(tmp_path, monkeypatch):
    from dashboard import home

    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "reports" / "RUN_X"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text(json.dumps({
        "dataset_finalization": TRANSIENT_LOCK_TIMEOUT,
        "dataset_finalization_ok": False,
        "dataset_finalization_detail": "destination stayed locked after 5 attempts",
    }), encoding="utf-8")

    message = home._archive_warning([{"Ticker": "COMI.CA", "RunID": "RUN_X"}])

    assert "Scan results are complete" in message
    assert TRANSIENT_LOCK_TIMEOUT in message
    assert "Traceback" not in message and "PermissionError" not in message
    assert "RUN_X" in message


def test_dashboard_is_silent_when_publication_succeeded(tmp_path, monkeypatch):
    from dashboard import home

    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "reports" / "RUN_OK"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text(json.dumps({
        "dataset_finalization": FINALIZED, "dataset_finalization_ok": True,
    }), encoding="utf-8")

    assert home._archive_warning([{"RunID": "RUN_OK"}]) == ""
    assert home._archive_warning([]) == ""
    assert home._archive_warning([{"RunID": "MISSING_RUN"}]) == ""


def test_production_remains_disabled():
    """No live-trading or broker switch may be enabled anywhere in settings."""
    from config.settings_manager import settings

    enabled: list[str] = []

    def walk(node, path=""):
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            where = f"{path}.{key}" if path else key
            if isinstance(value, dict):
                walk(value, where)
            elif value is True and any(
                word in key.lower()
                for word in ("live_trading", "production", "broker", "real_money")
            ):
                enabled.append(where)

    walk(settings.all() if hasattr(settings, "all") else settings.data)
    assert not enabled, f"production-style switches are enabled: {enabled}"


def test_the_fix_is_storage_only():
    """Finalization must not reach into strategy, provider or trading code."""
    source = Path(archive_module.__file__).read_text(encoding="utf-8")
    imports = "\n".join(line for line in source.splitlines()
                        if line.strip().startswith(("import ", "from ")))
    for forbidden in ("strategy", "portfolio", "core.scanner", "trading_decision",
                      "data_provider", "indicators"):
        assert forbidden not in imports, f"dataset_archive must not import {forbidden}"
