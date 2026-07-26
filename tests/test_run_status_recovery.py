"""Effective run status via an immutable recovery sidecar.

`RUN_20260726_212722` holds a valid finalized dataset while its `run_metadata.json`
still says `RUNNING`, because the original process died before writing completion
metadata. The correction is recorded *beside* that file, never inside it, and a reader
must never see the run as still active — nor may any RUNNING run be silently promoted
without the evidence to back it.

All tests use `tmp_path`; none touches the real reports directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from services.dataset_archive import DatasetArchive
from services.run_status import (
    COMPLETED,
    RECOVERED_COMPLETED,
    RECOVERY_ALREADY_RECORDED,
    RECOVERY_CONFLICT_PRESERVED,
    RECOVERY_CREATED,
    RECOVERY_NOT_ELIGIBLE,
    RECOVERY_SIDECAR_NAME,
    RUNNING,
    SOURCE_METADATA,
    SOURCE_SIDECAR,
    UNKNOWN,
    read_recovery_sidecar,
    record_finalization_recovery,
    resolve_effective_status,
    status_label,
)

REAL_REPORTS = Path(__file__).resolve().parents[1] / "reports"


@pytest.fixture(autouse=True)
def _never_touch_real_reports():
    before = {p.name for p in REAL_REPORTS.iterdir()} if REAL_REPORTS.is_dir() else set()
    yield
    after = {p.name for p in REAL_REPORTS.iterdir()} if REAL_REPORTS.is_dir() else set()
    assert before == after, "a test modified the real reports directory"


def _candles(rows: int = 6) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=rows, freq="B", name="Date")
    values = np.arange(rows, dtype=float)
    return pd.DataFrame({
        "Open": 10 + values, "High": 11 + values, "Low": 9 + values,
        "Close": 10.5 + values, "Volume": 1000 + values,
    }, index=index)


def _crashed_run(tmp_path: Path, name: str = "RUN_20260726_212722") -> Path:
    """A run with a finalized dataset but metadata frozen at RUNNING."""
    run_dir = tmp_path / name
    run_dir.mkdir()
    archive = DatasetArchive(run_dir, {"data": {}}, ["COMI.CA"])
    archive.capture("COMI.CA", _candles(), "normalized", {})
    archive.finalize()
    (run_dir / "run_metadata.json").write_text(
        json.dumps({"run_id": name, "status": RUNNING, "created_at": "2026-07-26T21:27:22"}),
        encoding="utf-8")
    return run_dir


def _plain_run(tmp_path: Path, status: str, name: str = "RUN_PLAIN") -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir()
    (run_dir / "run_metadata.json").write_text(
        json.dumps({"run_id": name, "status": status}), encoding="utf-8")
    return run_dir


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# Recording a recovery
# --------------------------------------------------------------------------- #

def test_original_metadata_stays_byte_identical(tmp_path):
    run_dir = _crashed_run(tmp_path)
    metadata_path = run_dir / "run_metadata.json"
    before = metadata_path.read_bytes()

    result = record_finalization_recovery(run_dir)

    assert result.status == RECOVERY_CREATED
    assert metadata_path.read_bytes() == before
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["status"] == RUNNING


def test_recovered_run_resolves_to_recovered_completed(tmp_path):
    run_dir = _crashed_run(tmp_path)
    assert resolve_effective_status(run_dir).status == RUNNING     # before recovery

    record_finalization_recovery(run_dir)
    resolved = resolve_effective_status(run_dir)

    assert resolved.status == RECOVERED_COMPLETED
    assert resolved.source == SOURCE_SIDECAR
    assert resolved.recovered
    assert resolved.labels == ("تم الاستكمال بعد انقطاع الحفظ", "Recovered Completed")


def test_sidecar_records_the_expected_evidence(tmp_path):
    run_dir = _crashed_run(tmp_path)
    record_finalization_recovery(run_dir)
    sidecar = read_recovery_sidecar(run_dir)

    assert sidecar["run_id"] == run_dir.name
    assert sidecar["original_status"] == RUNNING
    assert sidecar["effective_status"] == RECOVERED_COMPLETED
    assert sidecar["finalization_result"] == "FINALIZED"
    assert sidecar["dataset_path"] == "dataset"
    assert sidecar["destination_path_present"] is True
    assert sidecar["staging_path_present"] is False
    assert sidecar["manifest_sha256"] == _sha(run_dir / "dataset" / "MANIFEST.json")
    assert sidecar["original_metadata_sha256"] == _sha(run_dir / "run_metadata.json")
    assert sidecar["recovery_reason"]
    # No dataset contents leak into the sidecar.
    assert "records" not in sidecar and "symbols" not in sidecar


def test_no_dataset_file_is_rewritten(tmp_path):
    run_dir = _crashed_run(tmp_path)
    dataset = run_dir / "dataset"
    before = {path.relative_to(dataset).as_posix(): _sha(path)
              for path in dataset.rglob("*") if path.is_file()}

    record_finalization_recovery(run_dir)

    after = {path.relative_to(dataset).as_posix(): _sha(path)
             for path in dataset.rglob("*") if path.is_file()}
    assert before == after and before


# --------------------------------------------------------------------------- #
# The resolver never over-promotes
# --------------------------------------------------------------------------- #

def test_ordinary_running_run_stays_running(tmp_path):
    run_dir = _plain_run(tmp_path, RUNNING)
    resolved = resolve_effective_status(run_dir)
    assert resolved.status == RUNNING
    assert resolved.source == SOURCE_METADATA
    assert not (run_dir / RECOVERY_SIDECAR_NAME).exists()


def test_ordinary_completed_run_stays_completed(tmp_path):
    run_dir = _plain_run(tmp_path, COMPLETED)
    assert resolve_effective_status(run_dir).status == COMPLETED


def test_missing_metadata_resolves_to_unknown(tmp_path):
    run_dir = tmp_path / "RUN_NO_META"
    run_dir.mkdir()
    assert resolve_effective_status(run_dir).status == UNKNOWN
    (run_dir / "run_metadata.json").write_text("{ not json", encoding="utf-8")
    assert resolve_effective_status(run_dir).status == UNKNOWN


def test_running_run_without_a_dataset_cannot_be_recovered(tmp_path):
    run_dir = _plain_run(tmp_path, RUNNING)
    result = record_finalization_recovery(run_dir)
    assert result.status == RECOVERY_NOT_ELIGIBLE
    assert not (run_dir / RECOVERY_SIDECAR_NAME).exists()
    assert resolve_effective_status(run_dir).status == RUNNING


def test_invalid_manifest_cannot_produce_a_recovered_state(tmp_path):
    run_dir = _plain_run(tmp_path, RUNNING, name="RUN_BAD_MANIFEST")
    dataset = run_dir / "dataset"
    dataset.mkdir()
    (dataset / "MANIFEST.json").write_text(json.dumps({"status": "STAGING"}),
                                           encoding="utf-8")

    result = record_finalization_recovery(run_dir)

    assert result.status == RECOVERY_NOT_ELIGIBLE
    assert "manifest is not valid" in result.detail
    assert resolve_effective_status(run_dir).status == RUNNING


def test_a_forged_sidecar_without_a_dataset_is_ignored(tmp_path):
    run_dir = _plain_run(tmp_path, RUNNING, name="RUN_FORGED")
    (run_dir / RECOVERY_SIDECAR_NAME).write_text(json.dumps({
        "run_id": "RUN_FORGED", "effective_status": RECOVERED_COMPLETED,
        "manifest_sha256": "deadbeef",
    }), encoding="utf-8")

    resolved = resolve_effective_status(run_dir)

    assert resolved.status == RUNNING
    assert "sidecar ignored" in resolved.detail


def test_mismatched_run_id_is_rejected(tmp_path):
    run_dir = _crashed_run(tmp_path, name="RUN_A")
    sidecar = run_dir / RECOVERY_SIDECAR_NAME
    record_finalization_recovery(run_dir)
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["run_id"] = "RUN_SOMEWHERE_ELSE"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    resolved = resolve_effective_status(run_dir)

    assert resolved.status == RUNNING
    assert "run id does not match" in resolved.detail


def test_manifest_hash_mismatch_is_rejected(tmp_path):
    run_dir = _crashed_run(tmp_path)
    record_finalization_recovery(run_dir)
    sidecar = run_dir / RECOVERY_SIDECAR_NAME
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["manifest_sha256"] = "0" * 64
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    resolved = resolve_effective_status(run_dir)

    assert resolved.status == RUNNING
    assert "manifest hash does not match" in resolved.detail


def test_already_completed_run_needs_no_recovery(tmp_path):
    run_dir = _crashed_run(tmp_path, name="RUN_DONE")
    (run_dir / "run_metadata.json").write_text(
        json.dumps({"run_id": "RUN_DONE", "status": COMPLETED}), encoding="utf-8")
    result = record_finalization_recovery(run_dir)
    assert result.status == RECOVERY_NOT_ELIGIBLE
    assert resolve_effective_status(run_dir).status == COMPLETED


# --------------------------------------------------------------------------- #
# Idempotency and conflict
# --------------------------------------------------------------------------- #

def test_repeated_recovery_is_idempotent(tmp_path):
    run_dir = _crashed_run(tmp_path)
    first = record_finalization_recovery(run_dir)
    sidecar_bytes = (run_dir / RECOVERY_SIDECAR_NAME).read_bytes()
    metadata_bytes = (run_dir / "run_metadata.json").read_bytes()

    second = record_finalization_recovery(run_dir)
    third = record_finalization_recovery(run_dir)

    assert first.status == RECOVERY_CREATED
    assert second.status == third.status == RECOVERY_ALREADY_RECORDED
    assert (run_dir / RECOVERY_SIDECAR_NAME).read_bytes() == sidecar_bytes
    assert (run_dir / "run_metadata.json").read_bytes() == metadata_bytes
    assert second.sidecar["recovered_at"] == first.sidecar["recovered_at"]
    assert len(list(run_dir.glob("finalization_recovery*.json"))) == 1


def test_conflicting_sidecar_is_preserved_not_overwritten(tmp_path):
    run_dir = _crashed_run(tmp_path)
    sidecar = run_dir / RECOVERY_SIDECAR_NAME
    sidecar.write_text(json.dumps({
        "run_id": run_dir.name, "effective_status": RECOVERED_COMPLETED,
        "manifest_sha256": "1" * 64, "recovered_at": "2020-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    before = sidecar.read_bytes()

    result = record_finalization_recovery(run_dir)

    assert result.status == RECOVERY_CONFLICT_PRESERVED
    assert not result.ok
    assert sidecar.read_bytes() == before                    # untouched
    assert resolve_effective_status(run_dir).status == RUNNING


# --------------------------------------------------------------------------- #
# Readers
# --------------------------------------------------------------------------- #

def test_run_repository_exposes_the_effective_status(tmp_path, monkeypatch):
    from services import experiment_tracking

    reports = tmp_path / "reports"
    reports.mkdir()
    run_dir = _crashed_run(reports)
    record_finalization_recovery(run_dir)
    monkeypatch.setattr(experiment_tracking, "REPORTS_ROOT", reports)

    listed = experiment_tracking.RunRepository.list_runs()
    fetched = experiment_tracking.RunRepository.get(run_dir.name)

    assert listed and listed[0]["effective_status"] == RECOVERED_COMPLETED
    assert listed[0]["status"] == RUNNING                    # original left as recorded
    assert fetched["effective_status"] == RECOVERED_COMPLETED
    assert fetched["effective_status_source"] == SOURCE_SIDECAR
    # Reading must never mutate the stored file.
    assert json.loads((run_dir / "run_metadata.json").read_text())["status"] == RUNNING


def test_run_history_displays_the_recovered_label(tmp_path, monkeypatch):
    from services import experiment_tracking
    from dashboard import run_history

    reports = tmp_path / "reports"
    reports.mkdir()
    run_dir = _crashed_run(reports)
    record_finalization_recovery(run_dir)
    monkeypatch.setattr(experiment_tracking, "REPORTS_ROOT", reports)

    frame = run_history._history_frame(experiment_tracking.RunRepository.list_runs())

    assert list(frame["Status"]) == ["Recovered Completed"]
    assert "Running" not in list(frame["Status"])


def test_run_history_still_labels_ordinary_runs(tmp_path, monkeypatch):
    from services import experiment_tracking
    from dashboard import run_history

    reports = tmp_path / "reports"
    reports.mkdir()
    _plain_run(reports, RUNNING, name="RUN_20260101_000001")
    _plain_run(reports, COMPLETED, name="RUN_20260101_000002")
    monkeypatch.setattr(experiment_tracking, "REPORTS_ROOT", reports)

    frame = run_history._history_frame(experiment_tracking.RunRepository.list_runs())

    assert set(frame["Status"]) == {"Running", "Completed"}


def test_compare_and_open_can_load_a_recovered_run(tmp_path, monkeypatch):
    from services import experiment_tracking

    reports = tmp_path / "reports"
    reports.mkdir()
    first = _crashed_run(reports, name="RUN_20260726_212722")
    record_finalization_recovery(first)
    second = _plain_run(reports, COMPLETED, name="RUN_20260726_220000")
    monkeypatch.setattr(experiment_tracking, "REPORTS_ROOT", reports)

    # Open Run
    opened = experiment_tracking.RunRepository.get(first.name)
    assert opened["effective_status"] == RECOVERED_COMPLETED

    # Compare must not raise for a recovered run.
    comparison = experiment_tracking.RunRepository.compare(first.name, second.name)
    assert comparison is not None


def test_status_labels_are_bilingual():
    assert status_label(RECOVERED_COMPLETED) == (
        "تم الاستكمال بعد انقطاع الحفظ", "Recovered Completed")
    assert status_label(COMPLETED)[1] == "Completed"
    assert status_label(RUNNING)[1] == "Running"
    assert status_label("WHATEVER") == ("WHATEVER", "WHATEVER")


def test_recovery_reruns_no_scan(tmp_path):
    """Recovery reads evidence only — it must never invoke the scanner."""
    import services.run_status as run_status

    source = Path(run_status.__file__).read_text(encoding="utf-8")
    imports = "\n".join(line for line in source.splitlines()
                        if line.strip().startswith(("import ", "from ")))
    for forbidden in ("scanner", "strategy", "data_provider", "indicators", "portfolio"):
        assert forbidden not in imports, f"run_status must not import {forbidden}"


def test_run_history_status_filter_matches_the_rendered_labels(tmp_path, monkeypatch):
    """The filter must offer the labels actually shown, including the recovered one."""
    from services import experiment_tracking
    from dashboard import run_history

    reports = tmp_path / "reports"
    reports.mkdir()
    recovered = _crashed_run(reports, name="RUN_20260726_212722")
    record_finalization_recovery(recovered)
    _plain_run(reports, COMPLETED, name="RUN_20260101_000002")
    _plain_run(reports, "FAILED", name="RUN_20260101_000003")
    monkeypatch.setattr(experiment_tracking, "REPORTS_ROOT", reports)

    frame = run_history._history_frame(experiment_tracking.RunRepository.list_runs())
    options = sorted(frame["Status"].dropna().astype(str).unique().tolist())

    assert "Recovered Completed" in options
    assert options == ["Completed", "Failed", "Recovered Completed"]
    # Every option selects at least one row — no dead filter entry.
    for option in options:
        assert not frame[frame["Status"] == option].empty
