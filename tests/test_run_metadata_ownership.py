"""Ownership of ``run_metadata.json``.

RUN_20260804_225158 published three correct CSV files and then lost every
figure describing them. ``publish_archive`` wrote the schema-v2 export
metadata at 22:53:53.240; ``ExperimentRun.complete`` wrote its own dictionary
to the same filename at 22:53:53.705. Both writes were atomic. Atomicity says
nothing about ownership.
"""

from __future__ import annotations

import json

import pytest

from services.daily_scan_archive_reader import (
    RECOVERED_WARNING, SchemaStatus, read_archive,
)
from services.daily_scan_export import (
    COMPATIBILITY_FILENAME, COVERAGE_AUDIT_FILENAME, CURRENT_DECISIONS_FILENAME,
    DAILY_SCAN_EXPORT_SCHEMA_VERSION,
)
from services.run_metadata_service import (
    EXPORT_SECTION,
    METADATA_FILENAME,
    RUN_METADATA_SCHEMA_VERSION,
    MetadataConflictError,
    compose_run_metadata,
    load_run_metadata,
    update_run_metadata_section,
    write_run_metadata_atomic,
)

EXPORT = {
    "export_schema_version": DAILY_SCAN_EXPORT_SCHEMA_VERSION,
    "run_id": "RUN_TEST",
    "operational_universe_count": 241,
    "current_decision_count": 185,
    "current_count": 185,
    "expected_completed_session": "2026-08-04",
    "current_decisions_filename": CURRENT_DECISIONS_FILENAME,
}

RUN = {"run_id": "RUN_TEST", "status": "RUNNING", "run_type": "SCAN",
       "schema_version": 1}


# =========================================================================== #
# OWNERSHIP
# =========================================================================== #


def test_both_sections_coexist_in_one_document(tmp_path):
    write_run_metadata_atomic(tmp_path, run_section=RUN, export_section=EXPORT)

    document = json.loads((tmp_path / METADATA_FILENAME).read_text("utf-8"))

    assert document["run_metadata_schema_version"] == RUN_METADATA_SCHEMA_VERSION
    assert document["status"] == "RUNNING"          # generic fields stay flat
    assert document[EXPORT_SECTION]["current_decision_count"] == 185


def test_the_experiment_writer_cannot_erase_the_export_section(tmp_path):
    """The exact defect: a second writer with its own dict."""

    write_run_metadata_atomic(tmp_path, run_section=RUN, export_section=EXPORT)

    completed = dict(RUN, status="COMPLETED", completed_at="2026-08-04T22:53:53")
    write_run_metadata_atomic(tmp_path, run_section=completed,
                              allow_missing_export=True)

    loaded = load_run_metadata(tmp_path)
    assert loaded.run["status"] == "COMPLETED"
    assert loaded.has_export, "the export section was destroyed again"
    assert loaded.export["current_decision_count"] == 185
    assert loaded.export["expected_completed_session"] == "2026-08-04"


def test_an_additive_section_update_preserves_the_other_section(tmp_path):
    write_run_metadata_atomic(tmp_path, run_section=RUN, export_section=EXPORT)

    update_run_metadata_section(tmp_path, "run", {"status": "COMPLETED"})

    loaded = load_run_metadata(tmp_path)
    assert loaded.run["status"] == "COMPLETED"
    assert loaded.export == EXPORT


def test_a_conflicting_run_id_is_refused(tmp_path):
    write_run_metadata_atomic(tmp_path, run_section=RUN, export_section=EXPORT)

    with pytest.raises(MetadataConflictError, match="run_id conflict"):
        write_run_metadata_atomic(
            tmp_path, run_section=dict(RUN, run_id="RUN_OTHER"),
            export_section=EXPORT)


def test_a_conflicting_expected_session_is_refused(tmp_path):
    write_run_metadata_atomic(tmp_path, run_section=RUN, export_section=EXPORT)

    with pytest.raises(MetadataConflictError, match="expected_completed_session"):
        write_run_metadata_atomic(
            tmp_path, run_section=RUN,
            export_section=dict(EXPORT, expected_completed_session="2026-08-03"))


def test_a_schema_downgrade_is_refused(tmp_path):
    write_run_metadata_atomic(tmp_path, run_section=RUN, export_section=EXPORT)

    with pytest.raises(MetadataConflictError, match="downgrade"):
        write_run_metadata_atomic(
            tmp_path, run_section=RUN,
            export_section=dict(EXPORT, export_schema_version=1))


def test_a_future_document_schema_is_refused(tmp_path):
    (tmp_path / METADATA_FILENAME).write_text(json.dumps({
        "run_metadata_schema_version": RUN_METADATA_SCHEMA_VERSION + 1,
        "run_id": "RUN_TEST"}), encoding="utf-8")

    with pytest.raises(MetadataConflictError, match="newer"):
        write_run_metadata_atomic(tmp_path, run_section=RUN,
                                  export_section=EXPORT)


def test_the_export_section_may_not_own_run_keys():
    with pytest.raises(MetadataConflictError, match="run keys"):
        compose_run_metadata(RUN, dict(EXPORT, status="COMPLETED"))


def test_a_conflicting_filename_is_refused(tmp_path):
    write_run_metadata_atomic(tmp_path, run_section=RUN, export_section=EXPORT)

    with pytest.raises(MetadataConflictError, match="current_decisions_filename"):
        write_run_metadata_atomic(
            tmp_path, run_section=RUN,
            export_section=dict(EXPORT, current_decisions_filename="other.csv"))


def test_a_refused_write_leaves_the_previous_document_intact(tmp_path):
    write_run_metadata_atomic(tmp_path, run_section=RUN, export_section=EXPORT)
    before = (tmp_path / METADATA_FILENAME).read_bytes()

    with pytest.raises(MetadataConflictError):
        write_run_metadata_atomic(
            tmp_path, run_section=dict(RUN, run_id="RUN_OTHER"),
            export_section=EXPORT)

    assert (tmp_path / METADATA_FILENAME).read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_a_run_with_no_export_writes_cleanly(tmp_path):
    """A backtest legitimately has no Daily Scan export."""

    write_run_metadata_atomic(tmp_path, run_section=RUN,
                              allow_missing_export=True)

    loaded = load_run_metadata(tmp_path)
    assert loaded.run["run_id"] == "RUN_TEST"
    assert not loaded.has_export


def test_a_pre_service_export_document_still_loads(tmp_path):
    """Archives written before this service keep their flat shape."""

    (tmp_path / METADATA_FILENAME).write_text(json.dumps(EXPORT), encoding="utf-8")

    loaded = load_run_metadata(tmp_path)

    assert loaded.has_export
    assert loaded.export["operational_universe_count"] == 241


# =========================================================================== #
# THE AFFECTED SHAPE
# =========================================================================== #

AUDIT_FIELDS = ["Symbol", "ScanOutcome", "DailyFreshnessStatus",
                "ActualCandleSession", "ExpectedCompletedSession"]


def write_csv(path, fields, rows):
    import csv

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def affected_archive(tmp_path, *, universe=241, current=185, stale=10,
                     buys=3, watches=127, avoids=55, metadata=None):
    """Correct v2 CSVs plus legacy experiment metadata over the export."""

    decisions, audit = [], []
    for index in range(universe):
        symbol = f"SYM{index:03d}"
        if index < current:
            decision = ("BUY" if index < buys
                        else "WATCH" if index < buys + watches else "AVOID")
            outcome, status, actual = "SUCCESS_CURRENT", "CURRENT", "2026-08-04"
            decisions.append({"Symbol": symbol, "Decision": decision})
        elif index < current + stale:
            outcome, status, actual = ("SKIPPED_STALE_DAILY_DATA", "STALE",
                                       "2026-08-03")
        else:
            outcome, status, actual = "EODHD_CACHE_MISS", "MISSING_DATE", ""
        audit.append({"Symbol": symbol, "ScanOutcome": outcome,
                      "DailyFreshnessStatus": status,
                      "ActualCandleSession": actual,
                      "ExpectedCompletedSession": "2026-08-04"})

    write_csv(tmp_path / CURRENT_DECISIONS_FILENAME, ["Symbol", "Decision"], decisions)
    write_csv(tmp_path / COMPATIBILITY_FILENAME, ["Symbol", "Decision"], decisions)
    write_csv(tmp_path / COVERAGE_AUDIT_FILENAME, AUDIT_FIELDS, audit)
    (tmp_path / METADATA_FILENAME).write_text(json.dumps(metadata or {
        "schema_version": 1, "run_id": tmp_path.name, "run_type": "SCAN",
        "status": "COMPLETED", "number_of_symbols": universe}), encoding="utf-8")
    return tmp_path


def test_the_affected_shape_is_recoverable_not_plain_legacy(tmp_path):
    archive = read_archive(affected_archive(tmp_path))

    assert archive.schema_status is SchemaStatus.RECOVERABLE_V2_EXPORT
    assert archive.schema_status is not SchemaStatus.LEGACY_V1
    assert archive.metadata_recovered


def test_the_recovered_archive_proves_its_coverage(tmp_path):
    archive = read_archive(affected_archive(tmp_path))

    assert archive.coverage_available
    assert archive.operational_universe_count == 241
    assert archive.current_count == 185
    assert archive.excluded_count == 56
    assert archive.current_coverage_percent == pytest.approx(76.8, abs=0.05)
    assert archive.expected_completed_session == "2026-08-04"
    assert archive.dominant_observed_session == "2026-08-04"
    assert archive.decision_counts == {"BUY": 3, "WATCH": 127, "AVOID": 55}
    assert archive.run_metadata["future_date_count"] == 0
    assert archive.run_metadata["stale_count"] == 10


def test_the_recovered_archive_names_what_it_cannot_prove(tmp_path):
    archive = read_archive(affected_archive(tmp_path))

    unavailable = archive.unavailable_metadata_fields
    assert "strategy_config_identity" in unavailable
    assert "generated_at_utc" in unavailable
    for name in unavailable:
        assert name not in archive.run_metadata, f"{name} was fabricated"


def test_the_recovered_state_is_disclosed(tmp_path):
    archive = read_archive(affected_archive(tmp_path))

    assert any("RECOVERED SCHEMA-V2 ARTIFACTS" in warning
               for warning in archive.archive_warnings)
    assert RECOVERED_WARNING in archive.archive_warnings


def test_a_mismatched_alias_is_never_recovered(tmp_path):
    affected_archive(tmp_path)
    write_csv(tmp_path / COMPATIBILITY_FILENAME, ["Symbol", "Decision"],
              [{"Symbol": "SYM000", "Decision": "BUY"}])

    archive = read_archive(tmp_path)

    assert archive.schema_status is not SchemaStatus.RECOVERABLE_V2_EXPORT
    assert not archive.coverage_available


def test_a_broken_current_invariant_is_never_recovered(tmp_path):
    """Audit says 185 current; only 100 decisions exist."""

    affected_archive(tmp_path)
    import csv
    with open(tmp_path / CURRENT_DECISIONS_FILENAME, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))[:100]
    write_csv(tmp_path / CURRENT_DECISIONS_FILENAME, ["Symbol", "Decision"], rows)
    write_csv(tmp_path / COMPATIBILITY_FILENAME, ["Symbol", "Decision"], rows)

    archive = read_archive(tmp_path)

    assert archive.schema_status is not SchemaStatus.RECOVERABLE_V2_EXPORT


def test_duplicate_audit_symbols_are_never_recovered(tmp_path):
    affected_archive(tmp_path)
    import csv
    with open(tmp_path / COVERAGE_AUDIT_FILENAME, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[-1]["Symbol"] = rows[0]["Symbol"]
    write_csv(tmp_path / COVERAGE_AUDIT_FILENAME, AUDIT_FIELDS, rows)

    archive = read_archive(tmp_path)

    assert archive.schema_status is not SchemaStatus.RECOVERABLE_V2_EXPORT


def test_conflicting_expected_sessions_are_never_recovered(tmp_path):
    affected_archive(tmp_path)
    import csv
    with open(tmp_path / COVERAGE_AUDIT_FILENAME, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["ExpectedCompletedSession"] = "2026-08-03"
    write_csv(tmp_path / COVERAGE_AUDIT_FILENAME, AUDIT_FIELDS, rows)

    archive = read_archive(tmp_path)

    assert archive.schema_status is not SchemaStatus.RECOVERABLE_V2_EXPORT


def test_the_invalid_marker_still_overrides_recovery(tmp_path):
    affected_archive(tmp_path)
    (tmp_path / "INVALID_DATA_PROVENANCE.md").write_text("bad", encoding="utf-8")

    archive = read_archive(tmp_path)

    assert archive.schema_status is SchemaStatus.INVALID_DATA_PROVENANCE
    assert not archive.decision_use_allowed


def test_a_genuine_legacy_archive_is_still_legacy(tmp_path):
    """No coverage audit means nothing to recover from."""

    write_csv(tmp_path / COMPATIBILITY_FILENAME, ["Symbol", "Decision"],
              [{"Symbol": "SYM000", "Decision": "BUY"}])
    (tmp_path / METADATA_FILENAME).write_text(
        json.dumps({"schema_version": 1, "status": "COMPLETED"}), encoding="utf-8")

    archive = read_archive(tmp_path)

    assert archive.schema_status is SchemaStatus.LEGACY_V1
    assert not archive.coverage_available


def test_recovery_never_writes_to_the_archive(tmp_path):
    affected_archive(tmp_path)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in tmp_path.iterdir() if p.is_file()}

    read_archive(tmp_path)
    read_archive(tmp_path)

    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in tmp_path.iterdir() if p.is_file()}
    assert before == after


# =========================================================================== #
# PUBLICATION LIFECYCLE
# =========================================================================== #


def test_publish_archive_composes_rather_than_overwrites(tmp_path):
    from services.daily_scan_export import publish_archive

    write_run_metadata_atomic(tmp_path, run_section=RUN,
                              allow_missing_export=True)

    universe = ["SYM000"]
    decisions = [{"Symbol": "SYM000", "DailyFreshnessStatus": "CURRENT",
                  "EligibleForCurrentAnalysis": True, "DecisionCalculated": True,
                  "Decision": "BUY", "RubixOverlayApplied": False,
                  "DecisionPriceSource": "eodhd_daily_close"}]
    audit = [{"Symbol": "SYM000", "ScanOutcome": "SUCCESS_CURRENT",
              "ActualCandleSession": "2026-08-04",
              "ExpectedCompletedSession": "2026-08-04"}]
    metadata = {
        "export_schema_version": DAILY_SCAN_EXPORT_SCHEMA_VERSION,
        "run_id": "RUN_TEST", "current_count": 1, "current_decision_count": 1,
        "daily_current_count": 1, "decision_calculation_error_count": 0,
        "excluded_count": 0, "operational_universe_count": 1,
        "expected_completed_session": "2026-08-04",
        "decision_count": 1, "buy_count": 1, "watch_count": 0, "avoid_count": 0,
        "observed_session_distribution": {"2026-08-04": 1},
        "current_coverage_percent": 100.0,
        "current_decisions_filename": CURRENT_DECISIONS_FILENAME,
        "coverage_audit_filename": COVERAGE_AUDIT_FILENAME,
    }

    publish_archive(tmp_path, decisions=decisions, audit=audit,
                    metadata=metadata, universe_symbols=universe)

    loaded = load_run_metadata(tmp_path)
    assert loaded.run["status"] == "RUNNING", "the run section was destroyed"
    assert loaded.export["export_schema_version"] == DAILY_SCAN_EXPORT_SCHEMA_VERSION
    assert read_archive(tmp_path).schema_status is SchemaStatus.SCHEMA_V2_VALID


def test_a_completed_experiment_write_keeps_the_archive_valid(tmp_path):
    """The full two-writer sequence that produced the incident."""

    test_publish_archive_composes_rather_than_overwrites(tmp_path)

    # ExperimentRun.complete() equivalent, through the service.
    write_run_metadata_atomic(
        tmp_path, run_section=dict(RUN, status="COMPLETED",
                                   completed_at="2026-08-04T22:53:53"),
        allow_missing_export=True)

    archive = read_archive(tmp_path)
    assert archive.schema_status is SchemaStatus.SCHEMA_V2_VALID
    assert not archive.metadata_recovered, "it should never have needed recovery"
    assert load_run_metadata(tmp_path).run["status"] == "COMPLETED"


# =========================================================================== #
# THE REAL AFFECTED RUN
# =========================================================================== #

REAL_RUN = "F:/EGX_AI_Trader/reports/RUN_20260804_225158"


def real_run_available():
    from pathlib import Path

    root = Path(REAL_RUN)
    return all((root / name).is_file() for name in
               (CURRENT_DECISIONS_FILENAME, COVERAGE_AUDIT_FILENAME,
                COMPATIBILITY_FILENAME, METADATA_FILENAME))


@pytest.mark.skipif(not real_run_available(),
                    reason="RUN_20260804_225158 is not present in this checkout")
def test_the_real_affected_run_recovers_and_is_never_modified():
    """The production archive this task exists for. Read-only."""

    from pathlib import Path

    root = Path(REAL_RUN)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in root.iterdir() if p.is_file()}

    archive = read_archive(root)

    assert archive.schema_status is SchemaStatus.RECOVERABLE_V2_EXPORT
    assert archive.metadata_recovered
    assert archive.coverage_available
    assert archive.operational_universe_count == 241
    assert archive.current_count == 185
    assert len(archive.current_decisions) == 185
    assert len(archive.coverage_audit) == 241
    assert archive.current_coverage_percent == pytest.approx(76.8, abs=0.05)
    assert archive.expected_completed_session == "2026-08-04"
    assert archive.run_metadata["future_date_count"] == 0
    assert archive.run_metadata["stale_count"] == 10
    assert sum(archive.decision_counts.values()) == 185
    assert list(archive.compatibility_decisions) == list(archive.current_decisions)
    assert "strategy_config_identity" in archive.unavailable_metadata_fields

    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in root.iterdir() if p.is_file()}
    assert before == after, "reading the archive modified it"


def test_the_real_experiment_writer_preserves_the_export_section(tmp_path):
    """Drives services.experiment_tracking, not a stand-in for it.

    Simulating the second writer proves nothing about the second writer: the
    incident was caused by that function calling a plain atomic JSON dump.
    """

    from services import experiment_tracking

    write_run_metadata_atomic(tmp_path, run_section=RUN, export_section=EXPORT)

    experiment_tracking._write_run_metadata(
        tmp_path, dict(RUN, status="COMPLETED", completed_at="2026-08-04T22:53:53",
                       artifacts=["scan_results.csv"]))

    loaded = load_run_metadata(tmp_path)
    assert loaded.run["status"] == "COMPLETED"
    assert loaded.has_export, "the experiment writer erased the export section"
    assert loaded.export["current_decision_count"] == 185
    assert loaded.export["expected_completed_session"] == "2026-08-04"


def test_no_module_writes_run_metadata_outside_the_service():
    """One owner. A second raw writer is how this defect happened.

    Precise by construction: a write call is flagged only when the filename
    appears inside that call's own arguments, so merely mentioning the file
    (a reader, a docstring, a backup manifest) is not an offence.
    """

    import ast
    import pathlib

    writers = {"_atomic_json", "write_text", "_write_json", "dump"}
    allowed = {"services/run_metadata_service.py", "services/daily_scan_export.py"}
    offenders = []
    for path in sorted(pathlib.Path(".").glob("*/*.py")):
        relative = path.as_posix()
        if relative in allowed or relative.startswith("tests/"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in writers:
                continue
            names_the_file = any(
                isinstance(inner, ast.Constant) and inner.value == "run_metadata.json"
                for argument in node.args for inner in ast.walk(argument))
            if names_the_file:
                offenders.append(f"{relative}:{node.lineno} {name}(...)")

    assert offenders == [], f"raw run_metadata.json writers: {offenders}"
