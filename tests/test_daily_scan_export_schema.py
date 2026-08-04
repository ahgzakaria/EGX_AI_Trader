"""Daily Scan export schema v2: decisions, coverage audit, metadata, publication.

Since the per-symbol freshness gate landed, ``scan_results.csv`` has contained
current decisions only - the meaning narrowed and nothing in the archive said
so. Schema v2 writes it down and adds the missing half: an audit row for every
operational-universe symbol, so a symbol dropped without a record can no longer
be confused with one that was never attempted.

Fixtures mirror RUN_20260804_005327: universe 241, result rows 194, CURRENT 6,
excluded 188, non-result 47. The archive itself is never modified.
"""

from __future__ import annotations

import csv
import json
import pathlib

import pytest

from core.daily_data_guard import (
    SymbolFreshness,
    classify_symbol_freshness,
    summarize_universe_coverage,
)
from services.daily_scan_export import (
    COMPATIBILITY_FILENAME,
    COVERAGE_AUDIT_FILENAME,
    CURRENT_DECISIONS_FILENAME,
    DAILY_SCAN_EXPORT_SCHEMA_VERSION,
    LEGACY_SCHEMA,
    OUTCOME_CURRENT,
    ArchiveInvariantError,
    archive_is_decision_usable,
    archive_schema_version,
    build_export_metadata,
    coverage_audit_row,
    current_decision_row,
    publish_archive,
    sanitize_error,
    validate_archive,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED = "2026-08-03"
STALE = "2026-07-30"
UNIVERSE_TOTAL = 241
CURRENT_TICKERS = ("CIRA.CA", "ETRS.CA", "FTNS.CA", "GBCO.CA", "MEPA.CA", "QNBE.CA")
RUN_ID = "RUN_TEST"


def build_fixture(current=6, stale=188, non_result=47, universe_total=UNIVERSE_TOTAL):
    """A universe of `universe_total` symbols with the incident's shape."""

    universe, decisions, audit, freshness = [], [], [], []
    counts = {}
    ordinal = 0

    for index in range(current):
        symbol = CURRENT_TICKERS[index] if index < len(CURRENT_TICKERS) else f"C{index}.CA"
        universe.append(symbol)
        item = classify_symbol_freshness(symbol, EXPECTED, EXPECTED)
        freshness.append(item)
        row = {
            "Ticker": symbol, "Signal": "WATCH" if index % 3 else "AVOID",
            "Score": 70 + index, "Confidence": 80,
            "LastCompletedSession": f"{EXPECTED}T14:30:00+03:00",
            "CompletedSessionClose": 10.0 + index, "Price": 10.0 + index,
            "DecisionPriceSource": "eodhd_daily_close",
            "RubixOverlayApplied": False,
            "DailyFreshnessStatus": "CURRENT",
        }
        decisions.append(current_decision_row(row, run_id=RUN_ID, freshness=item))
        audit.append(coverage_audit_row(symbol, run_id=RUN_ID, ordinal=ordinal,
                                        freshness=item, result_row=row,
                                        outcome=OUTCOME_CURRENT))
        counts[OUTCOME_CURRENT] = counts.get(OUTCOME_CURRENT, 0) + 1
        ordinal += 1

    for index in range(stale):
        symbol = f"S{index}.CA"
        universe.append(symbol)
        item = classify_symbol_freshness(symbol, STALE, EXPECTED)
        freshness.append(item)
        audit.append(coverage_audit_row(symbol, run_id=RUN_ID, ordinal=ordinal,
                                        freshness=item,
                                        outcome=item.outcome_status))
        counts[item.outcome_status] = counts.get(item.outcome_status, 0) + 1
        ordinal += 1

    for index in range(non_result):
        symbol = f"N{index}.CA"
        universe.append(symbol)
        outcome = ("EODHD_CACHE_MISS", "INSUFFICIENT_HISTORY",
                   "INVALID_HISTORY")[index % 3]
        audit.append(coverage_audit_row(
            symbol, run_id=RUN_ID, ordinal=ordinal, outcome=outcome,
            error={"category": "SCAN", "code": outcome, "message": "no data"}))
        counts[outcome] = counts.get(outcome, 0) + 1
        ordinal += 1

    coverage = summarize_universe_coverage(freshness, EXPECTED,
                                           universe_total=universe_total)
    metadata = build_export_metadata(
        run_id=RUN_ID, run_status="COMPLETED",
        generated_at_utc="2026-08-04T12:00:00+00:00",
        evaluation_time_utc="2026-08-04T12:00:00+00:00",
        coverage=coverage, decisions=decisions, outcome_counts=counts)
    return universe, decisions, audit, metadata


# =========================================================================== #
# CURRENT DECISIONS EXPORT
# =========================================================================== #


def test_the_decisions_export_contains_current_symbols_only():
    _, decisions, _, _ = build_fixture()
    assert len(decisions) == 6
    assert all(row["DailyFreshnessStatus"] == "CURRENT" for row in decisions)
    assert all(row["EligibleForCurrentAnalysis"] is True for row in decisions)
    assert all(row["DecisionCalculated"] is True for row in decisions)
    assert {row["Symbol"] for row in decisions} == set(CURRENT_TICKERS)


def test_no_stale_or_failed_symbol_reaches_the_decisions_export():
    universe, decisions, audit, _ = build_fixture()
    excluded = {row["Symbol"] for row in audit
                if row["ScanOutcome"] != OUTCOME_CURRENT}
    assert excluded, "the fixture must contain exclusions"
    assert not (excluded & {row["Symbol"] for row in decisions})


def test_expected_and_actual_sessions_stay_distinct_in_the_export():
    _, decisions, _, _ = build_fixture()
    row = decisions[0]
    assert row["ExpectedCompletedSession"] == EXPECTED
    assert row["ActualCandleSession"] == EXPECTED
    stale = classify_symbol_freshness("SPIN.CA", STALE, EXPECTED)
    audited = coverage_audit_row("SPIN.CA", run_id=RUN_ID, ordinal=0,
                                 freshness=stale)
    assert audited["ActualCandleSession"] == STALE
    assert audited["ExpectedCompletedSession"] == EXPECTED
    assert audited["ActualCandleSession"] != audited["ExpectedCompletedSession"]


def test_a_denied_overlay_keeps_the_eodhd_decision_price():
    _, decisions, _, _ = build_fixture()
    for row in decisions:
        if not row["RubixOverlayApplied"]:
            assert row["DecisionPriceSource"] in ("", "eodhd_daily_close")


# =========================================================================== #
# FULL COVERAGE AUDIT
# =========================================================================== #


def test_the_audit_accounts_for_every_universe_symbol_exactly_once():
    universe, _, audit, _ = build_fixture()
    assert len(universe) == UNIVERSE_TOTAL
    assert len(audit) == UNIVERSE_TOTAL
    symbols = [row["Symbol"] for row in audit]
    assert len(set(symbols)) == len(symbols)
    assert set(symbols) == set(universe)


def test_every_audit_row_carries_a_typed_outcome():
    _, _, audit, _ = build_fixture()
    assert all(row["ScanOutcome"] for row in audit)
    outcomes = {row["ScanOutcome"] for row in audit}
    assert OUTCOME_CURRENT in outcomes
    assert "SKIPPED_STALE_DAILY_DATA" in outcomes


def test_every_excluded_row_carries_a_reason():
    _, _, audit, _ = build_fixture()
    for row in audit:
        if row["ScanOutcome"] == "SKIPPED_STALE_DAILY_DATA":
            assert row["ExclusionReason"]


@pytest.mark.parametrize("message", [
    "connection failed api_token=abc123",
    "auth error: Bearer eyJhbGciOi",
    "password rejected",
])
def test_credential_shaped_errors_are_redacted(message):
    assert "redacted" in sanitize_error(message)
    assert "abc123" not in sanitize_error(message)


def test_an_ordinary_error_survives_sanitisation():
    assert "not enough history" in sanitize_error("ICLE.CA: not enough history")


# =========================================================================== #
# METADATA
# =========================================================================== #


def test_the_schema_version_is_persisted():
    _, _, _, metadata = build_fixture()
    assert metadata["export_schema_version"] == DAILY_SCAN_EXPORT_SCHEMA_VERSION == 2
    assert metadata["freshness_policy_version"]


def test_coverage_uses_the_operational_universe_as_denominator():
    """6/194 would overstate exchange coverage by an order of magnitude."""

    _, _, _, metadata = build_fixture()
    assert metadata["operational_universe_count"] == 241
    assert metadata["current_count"] == 6
    assert metadata["current_coverage_percent"] == 2.5
    # The loaded-result ratio is a separately labelled secondary metric.
    assert metadata["loaded_result_current_percent"] != \
        metadata["current_coverage_percent"]


def test_the_metadata_states_the_compatibility_contract():
    _, _, _, metadata = build_fixture()
    assert metadata["scan_results_semantics"] == "CURRENT_DECISIONS_ONLY"
    assert metadata["scan_results_compatibility_alias_of"] == CURRENT_DECISIONS_FILENAME
    assert metadata["current_decisions_filename"] == CURRENT_DECISIONS_FILENAME
    assert metadata["coverage_audit_filename"] == COVERAGE_AUDIT_FILENAME


def test_the_metadata_reports_the_market_wide_block():
    _, _, _, metadata = build_fixture()
    assert metadata["market_wide_summary_allowed"] is False
    assert metadata["market_wide_summary_block_reason"] == "INSUFFICIENT_CURRENT_COVERAGE"
    assert metadata["dominant_observed_session"] == STALE
    assert metadata["expected_completed_session"] == EXPECTED


def test_decision_counts_sum_to_the_decision_count():
    _, decisions, _, metadata = build_fixture()
    total = metadata["buy_count"] + metadata["watch_count"] + metadata["avoid_count"]
    assert total == metadata["decision_count"] == len(decisions)


def test_absent_values_are_null_not_ambiguous_blanks():
    _, _, _, metadata = build_fixture()
    assert metadata["strategy_config_identity"] is None
    assert metadata["rubix_freshness_policy_identity"] is None


# =========================================================================== #
# CROSS-FILE INVARIANTS
# =========================================================================== #


def test_a_well_formed_archive_passes_every_invariant():
    universe, decisions, audit, metadata = build_fixture()
    result = validate_archive(decisions=decisions, audit=audit,
                              metadata=metadata, universe_symbols=universe)
    assert result.ok, result.violations


def test_a_stale_row_admitted_into_decisions_is_rejected():
    universe, decisions, audit, metadata = build_fixture()
    stale = classify_symbol_freshness("S0.CA", STALE, EXPECTED)
    decisions.append(current_decision_row(
        {"Ticker": "S0.CA", "Signal": "BUY",
         "LastCompletedSession": f"{STALE}T14:30:00+03:00",
         "DailyFreshnessStatus": "STALE"},
        run_id=RUN_ID, freshness=stale))
    result = validate_archive(decisions=decisions, audit=audit,
                              metadata=metadata, universe_symbols=universe)
    assert result.ok is False
    assert any("not CURRENT" in v or "excluded" in v for v in result.violations)


def test_a_decision_row_that_is_not_current_is_rejected_on_its_own():
    """Isolates the status clause from the audit-outcome and count clauses.

    Without this the CURRENT check could be removed and every test would still
    pass, because a stale decision normally also breaks the audit-outcome and
    row-count invariants. Here the audit says SUCCESS_CURRENT and the counts
    line up, so only the row's own status can catch it.
    """

    universe, decisions, audit, metadata = build_fixture()
    # The row claims a stale status while everything around it looks correct.
    decisions[0] = dict(decisions[0], DailyFreshnessStatus="STALE")
    result = validate_archive(decisions=decisions, audit=audit,
                              metadata=metadata, universe_symbols=universe)
    assert result.ok is False
    assert any("not CURRENT" in violation for violation in result.violations)


def test_an_ineligible_decision_row_is_rejected_on_its_own():
    universe, decisions, audit, metadata = build_fixture()
    decisions[0] = dict(decisions[0], EligibleForCurrentAnalysis=False)
    result = validate_archive(decisions=decisions, audit=audit,
                              metadata=metadata, universe_symbols=universe)
    assert result.ok is False
    assert any("ineligible" in violation for violation in result.violations)


def test_a_decision_row_without_a_calculated_decision_is_rejected():
    universe, decisions, audit, metadata = build_fixture()
    decisions[0] = dict(decisions[0], DecisionCalculated=False)
    result = validate_archive(decisions=decisions, audit=audit,
                              metadata=metadata, universe_symbols=universe)
    assert result.ok is False
    assert any("no calculated decision" in violation for violation in result.violations)


def test_an_omitted_universe_symbol_is_rejected():
    universe, decisions, audit, metadata = build_fixture()
    audit.pop()
    result = validate_archive(decisions=decisions, audit=audit,
                              metadata=metadata, universe_symbols=universe)
    assert result.ok is False
    assert any("omits" in v or "rows for a universe" in v for v in result.violations)


def test_a_duplicate_audit_symbol_is_rejected():
    universe, decisions, audit, metadata = build_fixture()
    audit.append(dict(audit[0]))
    result = validate_archive(decisions=decisions, audit=audit,
                              metadata=metadata, universe_symbols=universe)
    assert result.ok is False


def test_a_wrong_coverage_denominator_is_rejected():
    universe, decisions, audit, metadata = build_fixture()
    # 6/194 instead of 6/241.
    metadata = dict(metadata, current_coverage_percent=3.1)
    result = validate_archive(decisions=decisions, audit=audit,
                              metadata=metadata, universe_symbols=universe)
    assert result.ok is False
    assert any("operational universe" in v for v in result.violations)


def test_a_missing_schema_version_is_rejected():
    universe, decisions, audit, metadata = build_fixture()
    metadata = {k: v for k, v in metadata.items() if k != "export_schema_version"}
    result = validate_archive(decisions=decisions, audit=audit,
                              metadata=metadata, universe_symbols=universe)
    assert result.ok is False
    assert any("schema version" in v for v in result.violations)


def test_a_denied_overlay_that_changed_the_decision_price_is_rejected():
    universe, decisions, audit, metadata = build_fixture()
    decisions[0] = dict(decisions[0], RubixOverlayApplied=False,
                        DecisionPriceSource="rubix_live")
    result = validate_archive(decisions=decisions, audit=audit,
                              metadata=metadata, universe_symbols=universe)
    assert result.ok is False
    assert any("denied the Rubix overlay" in v for v in result.violations)


# =========================================================================== #
# ATOMIC PUBLICATION
# =========================================================================== #


def test_a_valid_archive_publishes_every_file_together(tmp_path):
    universe, decisions, audit, metadata = build_fixture()
    publish_archive(tmp_path, decisions=decisions, audit=audit,
                    metadata=metadata, universe_symbols=universe)
    for name in (CURRENT_DECISIONS_FILENAME, COVERAGE_AUDIT_FILENAME,
                 COMPATIBILITY_FILENAME, "run_metadata.json"):
        assert (tmp_path / name).is_file(), name


def test_the_compatibility_alias_matches_the_decisions_file(tmp_path):
    universe, decisions, audit, metadata = build_fixture()
    publish_archive(tmp_path, decisions=decisions, audit=audit,
                    metadata=metadata, universe_symbols=universe)
    assert (tmp_path / COMPATIBILITY_FILENAME).read_bytes() == \
        (tmp_path / CURRENT_DECISIONS_FILENAME).read_bytes()


def test_the_audit_file_holds_the_whole_universe(tmp_path):
    universe, decisions, audit, metadata = build_fixture()
    publish_archive(tmp_path, decisions=decisions, audit=audit,
                    metadata=metadata, universe_symbols=universe)
    rows = list(csv.DictReader(
        open(tmp_path / COVERAGE_AUDIT_FILENAME, encoding="utf-8-sig")))
    assert len(rows) == UNIVERSE_TOTAL
    written = list(csv.DictReader(
        open(tmp_path / CURRENT_DECISIONS_FILENAME, encoding="utf-8-sig")))
    assert len(written) == 6


def test_a_violated_invariant_publishes_nothing(tmp_path):
    universe, decisions, audit, metadata = build_fixture()
    audit.pop()
    with pytest.raises(ArchiveInvariantError):
        publish_archive(tmp_path, decisions=decisions, audit=audit,
                        metadata=metadata, universe_symbols=universe)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failing", [0, 1, 2])
def test_a_write_failure_leaves_no_partial_archive(tmp_path, failing):
    """A half-written archive is worse than none - a reader cannot tell."""

    universe, decisions, audit, metadata = build_fixture()
    calls = {"n": 0}

    def flaky(path, rows):
        if calls["n"] == failing:
            raise OSError("disk full")
        calls["n"] += 1
        pathlib.Path(path).write_text("x", encoding="utf-8")

    with pytest.raises(OSError):
        publish_archive(tmp_path, decisions=decisions, audit=audit,
                        metadata=metadata, universe_symbols=universe,
                        writer=flaky)
    published = [p.name for p in tmp_path.iterdir() if not p.name.startswith(".")]
    assert published == [], published


def test_a_failure_preserves_a_previous_valid_archive(tmp_path):
    universe, decisions, audit, metadata = build_fixture()
    publish_archive(tmp_path, decisions=decisions, audit=audit,
                    metadata=metadata, universe_symbols=universe)
    before = (tmp_path / CURRENT_DECISIONS_FILENAME).read_bytes()

    audit.pop()
    with pytest.raises(ArchiveInvariantError):
        publish_archive(tmp_path, decisions=decisions, audit=audit,
                        metadata=metadata, universe_symbols=universe)
    assert (tmp_path / CURRENT_DECISIONS_FILENAME).read_bytes() == before


# =========================================================================== #
# LEGACY ARCHIVES
# =========================================================================== #


def test_an_archive_without_a_version_is_legacy_unknown():
    assert archive_schema_version({}) == LEGACY_SCHEMA
    assert archive_schema_version(None) == LEGACY_SCHEMA
    assert archive_schema_version({"export_schema_version": 2}) == 2


def test_the_version_is_never_inferred_from_a_filename(tmp_path):
    (tmp_path / CURRENT_DECISIONS_FILENAME).write_text("x", encoding="utf-8")
    assert archive_schema_version({}) == LEGACY_SCHEMA


def test_an_invalid_marker_overrides_decision_use(tmp_path):
    (tmp_path / "INVALID_DATA_PROVENANCE.md").write_text("x", encoding="utf-8")
    usable, reason = archive_is_decision_usable(
        tmp_path, {"export_schema_version": 2})
    assert usable is False
    assert reason == "INVALID_DATA_PROVENANCE"


def test_a_legacy_archive_is_flagged_rather_than_assumed(tmp_path):
    usable, reason = archive_is_decision_usable(tmp_path, {})
    assert usable is True
    assert reason == "LEGACY_UNKNOWN_SEMANTICS"


# =========================================================================== #
# THE ARCHIVED INCIDENT RUN
# =========================================================================== #


def test_the_incident_archive_is_untouched_and_still_marked_invalid():
    run = pathlib.Path(r"F:\EGX_AI_Trader") / "reports" / "RUN_20260804_005327"
    if not run.is_dir():
        pytest.skip("the incident archive is not present on this machine")
    assert (run / "INVALID_DATA_PROVENANCE.md").is_file()
    assert (run / "scan_results.csv").is_file()
    usable, reason = archive_is_decision_usable(run, {})
    assert usable is False and reason == "INVALID_DATA_PROVENANCE"
    # v2 files are NOT written into a historical run.
    assert not (run / CURRENT_DECISIONS_FILENAME).exists()
    assert not (run / COVERAGE_AUDIT_FILENAME).exists()


# =========================================================================== #
# BOUNDARIES
# =========================================================================== #


def test_the_export_module_touches_no_provider_process_or_yahoo():
    source = (REPO_ROOT / "services" / "daily_scan_export.py").read_text(
        encoding="utf-8").lower()
    for forbidden in ("yahoo", "subprocess", "popen", "requests", "sqlite3",
                      "websocket"):
        assert forbidden not in source


def test_strategy_thresholds_are_unchanged():
    from scalping_orb.strategy_config import OrbStrategyConfig

    config = OrbStrategyConfig()
    assert config.minimum_reward_risk == 1.5
    assert config.target_2_r_multiple == 2.0


def test_the_scanner_publishes_atomically():
    import inspect

    from core import scanner

    source = inspect.getsource(scanner._publish_versioned_exports)
    assert "publish_archive(" in source
    assert "coverage_audit_row(" in source
    assert "current_decision_row(" in source
    # Every universe symbol is walked, not just the ones with results.
    assert "for ordinal, symbol in enumerate(symbols)" in source
