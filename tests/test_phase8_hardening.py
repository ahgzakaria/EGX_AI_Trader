"""Phase 8 engineering tests; no trading calculation is exercised or changed."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

import services.experiment_tracking as tracking
from services.backup_manager import create_backup, restore_backup, verify_backup
from services.dataset_archive import (
    DatasetArchive,
    capture_active,
    frame_hash,
    load_archived_frames,
)


def candles():
    frame = pd.DataFrame({
        "Open": [10.0, 11.0], "High": [12.0, 13.0],
        "Low": [9.0, 10.0], "Close": [11.0, 12.0],
        "Volume": [1000.0, 1200.0],
    }, index=pd.to_datetime(["2026-07-12", "2026-07-13"]))
    frame.index.name = "Date"
    return frame


def test_dataset_archive_is_deterministic_and_replayable(tmp_path):
    run_dir = tmp_path / "RUN_TEST"
    run_dir.mkdir()
    archive = DatasetArchive(run_dir, {"data": {"interval": "1d"}}, ["COMI.CA"])
    frame = candles()
    archive.capture("COMI.CA", frame, "raw", {"provider": "yahoo"})
    archive.capture("COMI.CA", frame, "normalized", {"provider": "yahoo"})
    manifest = archive.finalize()
    replay, loaded = load_archived_frames(run_dir)
    assert loaded["dataset_hash"] == manifest["dataset_hash"]
    pd.testing.assert_frame_equal(
        replay["COMI.CA"], frame, check_dtype=False, check_freq=False
    )
    assert loaded["retention"] == "NO_AUTOMATIC_DELETION"


def test_dataset_archive_refuses_in_run_data_drift(tmp_path):
    run_dir = tmp_path / "RUN_TEST"
    run_dir.mkdir()
    archive = DatasetArchive(run_dir, {}, ["COMI.CA"])
    archive.capture("COMI.CA", candles(), "normalized")
    changed = candles()
    changed.loc[changed.index[-1], "Close"] = 99
    with pytest.raises(RuntimeError, match="changed inside one run"):
        archive.capture("COMI.CA", changed, "normalized")


def test_completed_experiment_contains_dataset_and_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(tracking, "REPORTS_ROOT", tmp_path / "reports")
    run = tracking.ExperimentRun("BACKTEST", "STRATEGY_ONLY", ["COMI.CA"])
    capture_active("COMI.CA", candles(), "raw")
    capture_active("COMI.CA", candles(), "normalized")
    run.complete(metrics={"TotalReturn": 1.0}, successful_symbols=1)
    metadata = tracking.RunRepository.get(run.run_id)
    assert metadata["replay_ready"] is True
    assert len(metadata["dataset_hash"]) == 64
    assert (run.run_dir / "RUN_INTEGRITY.json").is_file()
    assert (run.run_dir / "dataset" / "MANIFEST.json").is_file()


def _sqlite(path: Path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('immutable')")


def test_backup_restore_is_checksummed_and_never_overwrites(tmp_path):
    rubix = tmp_path / "rubix.db"
    forward = tmp_path / "forward.db"
    _sqlite(rubix)
    _sqlite(forward)
    backup = create_backup(tmp_path / "backups", rubix, forward)
    assert verify_backup(backup)["valid"] is True
    destination = tmp_path / "restore"
    restore_backup(backup, destination)
    assert (destination / "rubix_live_market.db").is_file()
    with pytest.raises(FileExistsError):
        restore_backup(backup, destination)


def test_orphaned_running_run_requires_explicit_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(tracking, "REPORTS_ROOT", tmp_path / "reports")
    run = tracking.ExperimentRun("BACKTEST", "STRATEGY_ONLY", [])
    metadata = tracking.RunRepository.get(run.run_id)
    metadata["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    metadata["process_id"] = None
    tracking._atomic_json(run.run_dir / "run_metadata.json", metadata)
    audit = tracking.RunRepository.recover_stale_runs(age_hours=6, apply=False)
    assert audit[0]["action"] == "WOULD_RECOVER"
    assert tracking.RunRepository.get(run.run_id)["status"] == "RUNNING"
    tracking.RunRepository.recover_stale_runs(age_hours=6, apply=True)
    assert tracking.RunRepository.get(run.run_id)["status"] == "CANCELLED"
