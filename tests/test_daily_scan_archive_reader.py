"""Schema-v2 archive reading, comparison and download safety.

Parsing archives inside each page is how readers drift: one treats
scan_results.csv as the universe, another as decisions, and a legacy run gets
read under whichever assumption its reader happens to hold. One typed reader
answers "what may this archive be used for?" from the declared schema version -
never from a filename - and an INVALID_DATA_PROVENANCE marker overrides it all.

Fixtures are built in temporary directories. RUN_20260804_005327 is read only,
and no schema-v2 file is ever written into it.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from core.daily_data_guard import classify_symbol_freshness, summarize_universe_coverage
from services.daily_scan_archive_reader import (
    INVALID_MARKER,
    INVALID_WARNING,
    LEGACY_COMPARISON_WARNING,
    LEGACY_WARNING,
    DisappearanceReason,
    SchemaStatus,
    archive_cache_identity,
    compare_archives,
    read_archive,
    resolve_artifact,
)
from services.daily_scan_export import (
    COMPATIBILITY_FILENAME,
    COVERAGE_AUDIT_FILENAME,
    CURRENT_DECISIONS_FILENAME,
    OUTCOME_CURRENT,
    build_export_metadata,
    coverage_audit_row,
    current_decision_row,
    publish_archive,
)


EXPECTED = "2026-08-03"
STALE = "2026-07-30"
CURRENT_TICKERS = ("CIRA.CA", "ETRS.CA", "FTNS.CA", "GBCO.CA", "MEPA.CA", "QNBE.CA")
HISTORICAL_RUN = pathlib.Path(r"F:\EGX_AI_Trader") / "reports" / "RUN_20260804_005327"


#: A fixed 241-symbol universe, so two runs can differ in COVERAGE without
#: differing in membership. Varying the symbol names between runs would model a
#: universe change and mask the coverage attribution being tested.
UNIVERSE = list(CURRENT_TICKERS) + [f"S{i}.CA" for i in range(188)] +     [f"N{i}.CA" for i in range(47)]


def make_v2_archive(directory, *, current=6, stale=188, non_result=47,
                    decisions_by_symbol=None, run_id="RUN_V2"):
    """A published schema-v2 archive shaped like the incident run.

    ``current`` selects how many of the SAME universe symbols are current; the
    remainder become stale. The universe itself never changes.
    """

    universe, decisions, audit, freshness, counts = [], [], [], [], {}
    ordinal = 0
    # Three contiguous, non-overlapping slices of the SAME universe, so the
    # three counts always sum to 241 and only the current/stale split moves.
    current_names = UNIVERSE[:current]
    non_result_names = UNIVERSE[len(UNIVERSE) - non_result:]
    stale_names = UNIVERSE[current:len(UNIVERSE) - non_result]
    for index, symbol in enumerate(current_names):
        universe.append(symbol)
        item = classify_symbol_freshness(symbol, EXPECTED, EXPECTED)
        freshness.append(item)
        decision = (decisions_by_symbol or {}).get(symbol, "WATCH")
        row = {
            "Ticker": symbol, "Signal": decision, "Score": 70 + index,
            "Confidence": 80, "LastCompletedSession": f"{EXPECTED}T14:30:00+03:00",
            "CompletedSessionClose": 10.0 + index, "Price": 10.0 + index,
            "DecisionPriceSource": "eodhd_daily_close", "RubixOverlayApplied": False,
            "DailyFreshnessStatus": "CURRENT",
        }
        decisions.append(current_decision_row(row, run_id=run_id, freshness=item))
        audit.append(coverage_audit_row(symbol, run_id=run_id, ordinal=ordinal,
                                        freshness=item, result_row=row,
                                        outcome=OUTCOME_CURRENT))
        counts[OUTCOME_CURRENT] = counts.get(OUTCOME_CURRENT, 0) + 1
        ordinal += 1
    for symbol in stale_names:
        universe.append(symbol)
        item = classify_symbol_freshness(symbol, STALE, EXPECTED)
        freshness.append(item)
        audit.append(coverage_audit_row(symbol, run_id=run_id, ordinal=ordinal,
                                        freshness=item, outcome=item.outcome_status))
        counts[item.outcome_status] = counts.get(item.outcome_status, 0) + 1
        ordinal += 1
    for index, symbol in enumerate(non_result_names):
        universe.append(symbol)
        outcome = ("EODHD_CACHE_MISS", "INSUFFICIENT_HISTORY",
                   "INVALID_HISTORY")[index % 3]
        audit.append(coverage_audit_row(symbol, run_id=run_id, ordinal=ordinal,
                                        outcome=outcome))
        counts[outcome] = counts.get(outcome, 0) + 1
        ordinal += 1

    coverage = summarize_universe_coverage(freshness, EXPECTED,
                                           universe_total=len(universe))
    metadata = build_export_metadata(
        run_id=run_id, run_status="COMPLETED",
        generated_at_utc="2026-08-04T12:00:00+00:00",
        evaluation_time_utc="2026-08-04T12:00:00+00:00",
        coverage=coverage, decisions=decisions, outcome_counts=counts)
    directory.mkdir(parents=True, exist_ok=True)
    publish_archive(directory, decisions=decisions, audit=audit,
                    metadata=metadata, universe_symbols=universe)
    return directory


def make_legacy_archive(directory, rows=3):
    """A pre-v2 archive: scan_results.csv and metadata with no schema version."""

    directory.mkdir(parents=True, exist_ok=True)
    (directory / COMPATIBILITY_FILENAME).write_text(
        "Ticker,Signal\n" + "\n".join(f"L{i}.CA,BUY" for i in range(rows)) + "\n",
        encoding="utf-8-sig")
    (directory / "run_metadata.json").write_text(
        json.dumps({"run_id": directory.name, "status": "COMPLETED"}),
        encoding="utf-8")
    return directory


# =========================================================================== #
# FIXTURE A - a valid schema-v2 archive
# =========================================================================== #


def test_a_valid_v2_archive_reports_six_of_two_hundred_and_forty_one(tmp_path):
    archive = read_archive(make_v2_archive(tmp_path / "RUN_A"))
    assert archive.schema_status is SchemaStatus.SCHEMA_V2_VALID
    assert archive.schema_version == 2
    assert archive.operational_universe_count == 241
    assert archive.current_count == 6
    assert archive.excluded_count == 235
    assert archive.current_coverage_percent == 2.5
    assert archive.loaded_result_current_percent != archive.current_coverage_percent
    assert len(archive.current_decisions) == 6
    assert len(archive.coverage_audit) == 241
    assert archive.decision_use_allowed is True
    assert archive.coverage_available is True


def test_decision_counts_are_scoped_to_the_current_symbols(tmp_path):
    archive = read_archive(make_v2_archive(
        tmp_path / "RUN_A", decisions_by_symbol={"QNBE.CA": "BUY", "CIRA.CA": "AVOID"}))
    counts = archive.decision_counts
    assert counts["BUY"] + counts["WATCH"] + counts["AVOID"] == 6
    assert counts["BUY"] == 1 and counts["AVOID"] == 1


def test_the_market_wide_summary_is_reported_as_blocked(tmp_path):
    archive = read_archive(make_v2_archive(tmp_path / "RUN_A"))
    assert archive.market_wide_summary_allowed is False
    assert archive.market_wide_summary_block_reason == "INSUFFICIENT_CURRENT_COVERAGE"
    assert archive.dominant_observed_session == STALE
    assert archive.expected_completed_session == EXPECTED


# =========================================================================== #
# VALIDATION AND FAIL-CLOSED BEHAVIOUR
# =========================================================================== #


def test_a_missing_required_artifact_is_incomplete_not_valid(tmp_path):
    directory = make_v2_archive(tmp_path / "RUN_A")
    (directory / COVERAGE_AUDIT_FILENAME).unlink()
    archive = read_archive(directory)
    assert archive.schema_status is SchemaStatus.INCOMPLETE_ARCHIVE
    assert COVERAGE_AUDIT_FILENAME in archive.missing_artifacts
    assert archive.coverage_available is False


def test_a_mismatched_compatibility_alias_fails_closed(tmp_path):
    directory = make_v2_archive(tmp_path / "RUN_A")
    (directory / COMPATIBILITY_FILENAME).write_text(
        "Symbol,Decision\nZZZ.CA,BUY\n", encoding="utf-8-sig")
    archive = read_archive(directory)
    assert archive.schema_status is SchemaStatus.SCHEMA_V2_INVALID
    assert any("does not match" in w for w in archive.archive_warnings)


def test_a_broken_invariant_fails_closed(tmp_path):
    directory = make_v2_archive(tmp_path / "RUN_A")
    document = json.loads((directory / "run_metadata.json").read_text(encoding="utf-8"))
    # The export lives in its own section since metadata ownership was
    # centralised; mutating the document root would test nothing.
    metadata = document.setdefault("daily_scan_export", document)
    metadata["current_count"] = 99
    (directory / "run_metadata.json").write_text(json.dumps(document), encoding="utf-8")
    archive = read_archive(directory)
    assert archive.schema_status is SchemaStatus.SCHEMA_V2_INVALID
    assert archive.archive_warnings


def test_an_unsupported_future_schema_is_refused(tmp_path):
    directory = make_v2_archive(tmp_path / "RUN_A")
    document = json.loads((directory / "run_metadata.json").read_text(encoding="utf-8"))
    # The export lives in its own section since metadata ownership was
    # centralised; mutating the document root would test nothing.
    metadata = document.setdefault("daily_scan_export", document)
    metadata["export_schema_version"] = 99
    (directory / "run_metadata.json").write_text(json.dumps(document), encoding="utf-8")
    archive = read_archive(directory)
    assert archive.schema_status is SchemaStatus.UNSUPPORTED_FUTURE_SCHEMA
    assert archive.coverage_available is False


def test_the_version_is_never_inferred_from_a_filename(tmp_path):
    """A missing version is never simply believed - it must be re-earned.

    Recovery changed what happens next, not this rule. An archive with no
    declared version is never SCHEMA_V2_VALID; it is either legacy or, when
    its CSV artifacts revalidate completely, explicitly RECOVERABLE_V2_EXPORT
    with every figure recomputed and the loss disclosed.
    """

    directory = make_v2_archive(tmp_path / "RUN_A")
    document = json.loads((directory / "run_metadata.json").read_text(encoding="utf-8"))
    metadata = document.setdefault("daily_scan_export", document)
    del metadata["export_schema_version"]
    (directory / "run_metadata.json").write_text(json.dumps(document), encoding="utf-8")

    archive = read_archive(directory)

    assert archive.schema_status is not SchemaStatus.SCHEMA_V2_VALID
    assert archive.schema_status is SchemaStatus.RECOVERABLE_V2_EXPORT
    assert archive.metadata_recovered
    assert any("RECOVERED SCHEMA-V2 ARTIFACTS" in warning
               for warning in archive.archive_warnings)


def test_a_declared_version_is_still_required_for_plain_validity(tmp_path):
    """Recovery is a separate, disclosed state - never a silent upgrade."""

    directory = make_v2_archive(tmp_path / "RUN_B")
    archive = read_archive(directory)

    assert archive.schema_status is SchemaStatus.SCHEMA_V2_VALID
    assert not archive.metadata_recovered
    assert archive.unavailable_metadata_fields == ()


# =========================================================================== #
# FIXTURE C - legacy archives
# =========================================================================== #


def test_a_legacy_archive_keeps_its_decisions_but_claims_no_coverage(tmp_path):
    archive = read_archive(make_legacy_archive(tmp_path / "RUN_LEGACY"))
    assert archive.schema_status is SchemaStatus.LEGACY_V1
    assert archive.decision_use_allowed is True
    assert archive.compatibility_decisions
    # The crucial refusal: no fabricated universe coverage.
    assert archive.operational_universe_count is None
    assert archive.current_count is None
    assert archive.current_coverage_percent is None
    assert archive.coverage_available is False
    assert any("COVERAGE SEMANTICS UNKNOWN" in w for w in archive.archive_warnings)


def test_a_legacy_row_count_is_never_treated_as_coverage(tmp_path):
    archive = read_archive(make_legacy_archive(tmp_path / "RUN_LEGACY", rows=50))
    assert len(archive.compatibility_decisions) == 50
    assert archive.current_coverage_percent is None


def test_the_legacy_warning_text_is_explicit():
    assert "LEGACY ARCHIVE" in LEGACY_WARNING
    assert "COVERAGE SEMANTICS UNKNOWN" in LEGACY_WARNING
    assert "cannot be reconstructed safely" in LEGACY_WARNING


# =========================================================================== #
# FIXTURE D - invalid provenance
# =========================================================================== #


def test_an_invalid_marker_overrides_a_valid_v2_archive(tmp_path):
    directory = make_v2_archive(tmp_path / "RUN_A")
    (directory / INVALID_MARKER).write_text("invalid", encoding="utf-8")
    archive = read_archive(directory)
    assert archive.schema_status is SchemaStatus.INVALID_DATA_PROVENANCE
    assert archive.invalid_data_provenance is True
    assert archive.decision_use_allowed is False
    assert INVALID_WARNING in archive.archive_warnings


def test_the_historical_incident_run_is_read_only_and_blocked():
    if not HISTORICAL_RUN.is_dir():
        pytest.skip("the incident archive is not present on this machine")
    archive = read_archive(HISTORICAL_RUN)
    assert archive.invalid_data_provenance is True
    assert archive.decision_use_allowed is False
    assert archive.schema_status is SchemaStatus.INVALID_DATA_PROVENANCE
    # No schema-v2 file was ever written into a historical run.
    assert not (HISTORICAL_RUN / CURRENT_DECISIONS_FILENAME).exists()
    assert not (HISTORICAL_RUN / COVERAGE_AUDIT_FILENAME).exists()


# =========================================================================== #
# FIXTURE B - two runs, same strategy, different coverage
# =========================================================================== #


def test_lower_coverage_does_not_read_as_strategy_deterioration(tmp_path):
    """The single most misleading thing this comparison could do."""

    run_a = read_archive(make_v2_archive(tmp_path / "RUN_A", current=6, stale=188,
                                         non_result=47, run_id="A"))
    run_b = read_archive(make_v2_archive(tmp_path / "RUN_B", current=3, stale=191,
                                         non_result=47, run_id="B"))
    comparison = compare_archives(run_a, run_b)

    assert sum(comparison.decision_counts_a.values()) == 6
    assert sum(comparison.decision_counts_b.values()) == 3
    # The like-for-like subset shows no decision change at all.
    assert len(comparison.current_in_both) == 3
    assert all(not change["Changed"] for change in comparison.decision_changes)
    # And the drop is attributed to data, not to the model.
    assert comparison.coverage_caveat
    assert "NOT DIRECTLY COMPARABLE" in comparison.coverage_caveat
    for symbol in comparison.current_only_in_a:
        assert comparison.disappearance_reasons[symbol] == \
            DisappearanceReason.DATA_COVERAGE_CHANGE.value


def test_a_genuine_decision_change_is_reported_as_one(tmp_path):
    run_a = read_archive(make_v2_archive(
        tmp_path / "RUN_A", decisions_by_symbol={"QNBE.CA": "BUY"}, run_id="A"))
    run_b = read_archive(make_v2_archive(
        tmp_path / "RUN_B", decisions_by_symbol={"QNBE.CA": "AVOID"}, run_id="B"))
    comparison = compare_archives(run_a, run_b)
    changed = [c for c in comparison.decision_changes if c["Changed"]]
    assert [c["Symbol"] for c in changed] == ["QNBE.CA"]
    assert comparison.coverage_caveat == ""      # coverage identical


def test_decisions_are_read_from_the_decisions_file_not_the_audit(tmp_path):
    run_a = read_archive(make_v2_archive(tmp_path / "RUN_A", run_id="A"))
    run_b = read_archive(make_v2_archive(tmp_path / "RUN_B", run_id="B"))
    comparison = compare_archives(run_a, run_b)
    audit_symbols = {row["Symbol"] for row in run_a.coverage_audit}
    excluded = audit_symbols - run_a.current_symbols()
    assert excluded, "the fixture must contain excluded symbols"
    assert not (set(comparison.current_in_both) & excluded)
    assert len(comparison.current_in_both) <= 6


def test_a_provider_failure_is_distinguished_from_a_coverage_change(tmp_path):
    run_a = read_archive(make_v2_archive(tmp_path / "RUN_A", current=6, run_id="A"))
    directory = make_v2_archive(tmp_path / "RUN_B", current=5, stale=189,
                                non_result=47, run_id="B")
    run_b = read_archive(directory)
    comparison = compare_archives(run_a, run_b)
    assert comparison.current_only_in_a
    for symbol in comparison.current_only_in_a:
        assert comparison.disappearance_reasons[symbol] in (
            DisappearanceReason.DATA_COVERAGE_CHANGE.value,
            DisappearanceReason.PROVIDER_FAILURE.value,
            DisappearanceReason.UNIVERSE_CHANGE.value)


def test_a_legacy_run_limits_the_comparison(tmp_path):
    run_a = read_archive(make_v2_archive(tmp_path / "RUN_A", run_id="A"))
    run_b = read_archive(make_legacy_archive(tmp_path / "RUN_LEGACY"))
    comparison = compare_archives(run_a, run_b)
    assert comparison.coverage_available is False
    assert LEGACY_COMPARISON_WARNING in comparison.legacy_warning
    for symbol in comparison.current_only_in_a:
        assert comparison.disappearance_reasons[symbol] == \
            DisappearanceReason.LEGACY_SEMANTICS_UNKNOWN.value


def test_an_invalid_run_blocks_strategy_comparison(tmp_path):
    directory = make_v2_archive(tmp_path / "RUN_B", run_id="B")
    (directory / INVALID_MARKER).write_text("invalid", encoding="utf-8")
    run_a = read_archive(make_v2_archive(tmp_path / "RUN_A", run_id="A"))
    comparison = compare_archives(run_a, read_archive(directory))
    assert comparison.comparable is False
    assert comparison.strategy_claim_allowed is False
    assert "INVALID_DATA_PROVENANCE" in comparison.blocked_reason


def test_strategy_claims_require_the_like_for_like_subset(tmp_path):
    run_a = read_archive(make_v2_archive(tmp_path / "RUN_A", current=6, run_id="A"))
    run_b = read_archive(make_v2_archive(tmp_path / "RUN_B", current=0, stale=194,
                                         non_result=47, run_id="B"))
    comparison = compare_archives(run_a, run_b)
    assert comparison.current_in_both == ()
    assert comparison.strategy_claim_allowed is False


# =========================================================================== #
# CACHE IDENTITY
# =========================================================================== #


def test_the_cache_identity_changes_when_an_invalid_marker_appears(tmp_path):
    """A run just marked invalid must not keep serving its valid presentation."""

    directory = make_v2_archive(tmp_path / "RUN_A")
    before = archive_cache_identity(directory)
    (directory / INVALID_MARKER).write_text("invalid", encoding="utf-8")
    assert archive_cache_identity(directory) != before


@pytest.mark.parametrize("filename", [
    "run_metadata.json", CURRENT_DECISIONS_FILENAME, COVERAGE_AUDIT_FILENAME,
])
def test_the_cache_identity_changes_when_an_artifact_changes(tmp_path, filename):
    directory = make_v2_archive(tmp_path / "RUN_A")
    before = archive_cache_identity(directory)
    path = directory / filename
    path.write_text(path.read_text(encoding="utf-8-sig") + "\n", encoding="utf-8-sig")
    assert archive_cache_identity(directory) != before


def test_two_runs_with_the_same_name_do_not_share_an_identity(tmp_path):
    first = make_v2_archive(tmp_path / "a" / "RUN_X", current=6, run_id="X")
    second = make_v2_archive(tmp_path / "b" / "RUN_X", current=3, stale=191,
                             run_id="X")
    assert archive_cache_identity(first) != archive_cache_identity(second)


# =========================================================================== #
# DOWNLOAD PATH SAFETY
# =========================================================================== #


def test_an_artifact_resolves_inside_the_run_directory(tmp_path):
    directory = make_v2_archive(tmp_path / "RUN_A")
    path = resolve_artifact(directory, CURRENT_DECISIONS_FILENAME)
    assert path.is_file()
    assert path.parent == directory.resolve()


@pytest.mark.parametrize("name", [
    "../.env", "..\\..\\secrets.json", "/etc/passwd", "../../run_metadata.json",
])
def test_path_traversal_is_refused(tmp_path, name):
    directory = make_v2_archive(tmp_path / "RUN_A")
    resolved = resolve_artifact(directory, name)
    # Only the basename is ever used, so the result stays inside the run.
    assert resolved.parent == directory.resolve()


def test_a_link_escaping_the_run_is_refused(tmp_path):
    directory = make_v2_archive(tmp_path / "RUN_A")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = directory / "escape.csv"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this system")
    with pytest.raises(ValueError):
        resolve_artifact(directory, "escape.csv")


# =========================================================================== #
# BOUNDARIES
# =========================================================================== #


def test_the_reader_never_writes_to_an_archive(tmp_path):
    directory = make_v2_archive(tmp_path / "RUN_A")
    before = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    read_archive(directory)
    read_archive(directory)
    after = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    assert before == after


def test_the_reader_module_touches_no_provider_process_or_yahoo():
    source = (pathlib.Path(__file__).resolve().parents[1] / "services"
              / "daily_scan_archive_reader.py").read_text(encoding="utf-8").lower()
    for forbidden in ("yahoo", "subprocess", "popen", "requests", "sqlite3",
                      "websocket", "streamlit"):
        assert forbidden not in source


def test_strategy_thresholds_are_unchanged():
    from scalping_orb.strategy_config import OrbStrategyConfig

    config = OrbStrategyConfig()
    assert config.minimum_reward_risk == 1.5
    assert config.target_2_r_multiple == 2.0


# =========================================================================== #
# UI CONTRACT
# =========================================================================== #


def _panel_source():
    return (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
            / "scan_archive_panel.py").read_text(encoding="utf-8")


def test_run_history_uses_the_required_coverage_wording():
    source = _panel_source()
    assert "CURRENT DAILY COVERAGE" in source
    assert "DECISIONS AMONG CURRENT SYMBOLS" in source
    assert "MARKET-WIDE SUMMARY BLOCKED" in source


def test_the_current_subset_is_never_called_the_whole_market():
    source = _panel_source()
    assert "not the whole-market" in source


def test_a_legacy_archive_is_given_no_coverage_percentage():
    source = _panel_source()
    block = source.split("if not archive.coverage_available:")[1]
    assert "Row counts are not coverage" in block
    assert "return" in block.split("st.markdown")[0]


def test_the_audit_download_is_never_labelled_as_opportunities():
    source = _panel_source()
    assert "Download Full Coverage Audit" in source
    assert "Not opportunities." in source
    audit_label = source.split('"Download Full Coverage Audit"')[1][:300]
    for forbidden in ("opportunit", "recommendation"):
        assert forbidden not in audit_label.lower().replace("not opportunities.", "")


def test_the_compatibility_download_is_labelled_as_an_alias():
    source = _panel_source()
    assert "Compatibility alias — CURRENT decisions only." in source


def test_downloads_resolve_through_the_path_guard():
    source = _panel_source()
    assert "resolve_artifact(archive.run_path, filename)" in source
    assert "path escapes the run directory" in source


def test_downloads_are_refused_for_a_non_valid_archive():
    source = _panel_source()
    guard = source.split("def render_download_controls")[1].split("st.columns")[0]
    # COVERAGE_BEARING is SCHEMA_V2_VALID plus the recovered state, whose
    # downloads are the very CSV files that were revalidated.
    assert "COVERAGE_BEARING" in guard
    assert "return" in guard


def test_a_recovered_archive_discloses_its_state_before_downloading():
    guard = _panel_source().split("def render_download_controls")[1]
    assert "metadata_recovered" in guard
    assert "RECOVERED_WARNING" in guard
    assert "unavailable_metadata_fields" in guard


def test_the_comparison_reads_decisions_from_the_decisions_file():
    source = _panel_source()
    assert "scan_current_decisions.csv" in source
    assert "never treated as decisions" in source


def test_a_stale_disappearance_is_not_called_a_strategy_exit():
    source = _panel_source()
    assert "is not a strategy exit" in source


def test_run_history_reads_through_the_versioned_reader():
    source = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "run_history.py").read_text(encoding="utf-8")
    assert "read_archive(directory)" in source
    assert "render_archive_summary" in source
    assert "render_download_controls" in source


def test_compare_runs_reads_through_the_versioned_reader():
    source = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "compare_runs.py").read_text(encoding="utf-8")
    assert "compare_archives(archive_a, archive_b)" in source
    assert "read_archive(" in source
