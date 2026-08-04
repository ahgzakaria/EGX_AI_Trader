"""Versioned Daily Scan exports: current decisions, full coverage audit, metadata.

Since the per-symbol freshness gate landed, ``scan_results.csv`` has contained
current decisions only - the file's meaning narrowed and nothing in the archive
said so. A consumer written against the old "one row per analysed symbol"
meaning would silently see a smaller universe.

Schema v2 writes the meaning down and adds the missing half:

* ``scan_current_decisions.csv`` - CURRENT, eligible, decision-calculated rows;
* ``scan_coverage_audit.csv``    - exactly one row per operational-universe
  symbol, with its typed outcome and exclusion reason;
* ``scan_results.csv``           - retained as an explicit compatibility alias
  of the current-decisions file, because every file-based consumer found in the
  audit wants decisions.

Everything here is pure: it builds rows, metadata and a verdict. Writing and
publishing are separate, so the invariants are checked before anything is
visible to a reader.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


#: Bumped whenever the meaning of an exported file changes, never merely its
#: column list. Readers must key off this, not off a filename's presence.
DAILY_SCAN_EXPORT_SCHEMA_VERSION = 2

#: Archives written before the version existed. Their ``scan_results.csv`` is
#: not assumed to be either contract.
LEGACY_SCHEMA = "LEGACY_UNKNOWN"

FRESHNESS_POLICY_VERSION = "PER_SYMBOL_DAILY_V1"

CURRENT_DECISIONS_FILENAME = "scan_current_decisions.csv"
COVERAGE_AUDIT_FILENAME = "scan_coverage_audit.csv"
COMPATIBILITY_FILENAME = "scan_results.csv"

#: The one outcome that means "a current decision was produced".
OUTCOME_CURRENT = "SUCCESS_CURRENT"

#: A current daily candle whose decision never completed. It is current data
#: and is NOT a success: SUCCESS_CURRENT promises an exported decision row.
OUTCOME_CALCULATION_ERROR = "CALCULATION_ERROR"

#: Every typed outcome a universe symbol may carry in the audit.
AUDIT_OUTCOMES = (
    OUTCOME_CURRENT,
    "SKIPPED_STALE_DAILY_DATA",
    "SKIPPED_MISSING_DAILY_DATE",
    "SKIPPED_FUTURE_DAILY_DATE",
    "INSUFFICIENT_HISTORY",
    "INVALID_HISTORY",
    "EODHD_CACHE_MISS",
    "PROVIDER_ERROR",
    "CALCULATION_ERROR",
    "NOT_ATTEMPTED",
)

DECISIONS = ("BUY", "WATCH", "AVOID")


class ArchiveInvariantError(RuntimeError):
    """A cross-file invariant failed. Nothing is published."""


# --------------------------------------------------------------------------- #
# Row builders
# --------------------------------------------------------------------------- #

def _text(value):
    """CSV-safe scalar. ``None`` becomes an empty cell, never the string None."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    return value


def current_decision_row(result_row, *, run_id, freshness=None):
    """One row of the current-decisions export.

    ``result_row`` is a scanner result. It reached here only by passing the
    freshness gate, so its decision was genuinely calculated afterwards.
    """
    row = dict(result_row or {})
    return {
        "RunID": run_id,
        "Symbol": _text(row.get("Ticker")),
        "Company": _text(row.get("CompanyName") or row.get("اسم السهم")),
        "ExpectedCompletedSession": _text(
            getattr(freshness, "expected_session", None)
            or row.get("ExpectedSession")),
        "ActualCandleSession": _text(
            getattr(freshness, "actual_latest_session", None)
            or str(row.get("LastCompletedSession") or "")[:10]),
        "DailyFreshnessStatus": _text(row.get("DailyFreshnessStatus") or "CURRENT"),
        "EligibleForCurrentAnalysis": True,
        "DecisionCalculated": True,
        "Decision": _text(row.get("Signal")),
        "Score": _text(row.get("Score")),
        "Confidence": _text(row.get("Confidence")),
        "Regime": _text(row.get("Regime")),
        "RiskReward": _text(row.get("RR")),
        "Entry": _text(row.get("Entry")),
        "StopLoss": _text(row.get("StopLoss")),
        "Target": _text(row.get("Target")),
        "EODHDDailyClose": _text(row.get("CompletedSessionClose")),
        "DisplayPrice": _text(row.get("Price")),
        "DisplayPriceSource": _text(row.get("DisplayPriceSource")),
        "DecisionPrice": _text(row.get("FrozenSnapshotPrice") or row.get("Price")),
        "DecisionPriceSource": _text(row.get("DecisionPriceSource")),
        "RubixQuoteStatus": _text(row.get("RubixQuoteStatus")),
        "RubixMarketTimestamp": _text(row.get("RubixMarketTimestamp")),
        "RubixReceiveTimestamp": _text(row.get("RubixReceiveTimestamp")),
        "RubixQuoteSession": _text(row.get("RubixQuoteSession")),
        "RubixOverlayApplied": bool(row.get("RubixOverlayApplied")),
        "RubixOverlayDenialReason": _text(row.get("RubixOverlayDenialReason")),
        "SourceProvider": _text(row.get("CompletedSessionProvider")
                                or row.get("HistoricalProvider")),
        "SourceMode": _text(row.get("HistoricalDataSource")),
        "FreshnessIdentity": _text(getattr(freshness, "candle_identity", "")),
        "ConfigIdentity": _text(row.get("ConfigIdentity")),
    }


def coverage_audit_row(symbol, *, run_id, ordinal, freshness=None, result_row=None,
                       outcome=None, error=None, config_identity=""):
    """One row of the full coverage audit - written for EVERY universe symbol."""
    row = dict(result_row or {})
    status = getattr(freshness, "freshness_status", None)
    status_value = getattr(status, "value", status) or ""
    eligible = bool(getattr(freshness, "eligible_for_current_analysis", False))
    resolved = outcome or getattr(freshness, "outcome_status", None) or "NOT_ATTEMPTED"
    return {
        "RunID": run_id,
        "Symbol": _text(symbol),
        "Company": _text(row.get("CompanyName")),
        "UniverseOrdinal": int(ordinal),
        "ExpectedCompletedSession": _text(
            getattr(freshness, "expected_session", "")),
        "ActualCandleSession": _text(
            getattr(freshness, "actual_latest_session", "")),
        "TradingSessionsBehind": int(
            getattr(freshness, "trading_sessions_behind", 0) or 0),
        "DailyFreshnessStatus": _text(status_value),
        "EligibleForCurrentAnalysis": eligible,
        "ScanOutcome": _text(resolved),
        "DecisionCalculated": bool(result_row is not None and eligible),
        "Decision": _text(row.get("Signal")),
        "ExclusionReason": _text(getattr(freshness, "exclusion_reason", "")),
        "HistoryLoaded": bool(freshness is not None),
        "SourceProvider": _text(getattr(freshness, "source_provider", "")),
        "SourceMode": _text(getattr(freshness, "source_mode", "")),
        "CandleProvenanceIdentity": _text(getattr(freshness, "candle_identity", "")),
        "EODHDDailyClose": _text(row.get("CompletedSessionClose")),
        "RubixQuoteStatus": _text(row.get("RubixQuoteStatus")),
        "RubixMarketTimestamp": _text(row.get("RubixMarketTimestamp")),
        "RubixReceiveTimestamp": _text(row.get("RubixReceiveTimestamp")),
        "RubixQuoteSession": _text(row.get("RubixQuoteSession")),
        "RubixOverlayApplied": bool(row.get("RubixOverlayApplied")),
        "RubixOverlayDenialReason": _text(row.get("RubixOverlayDenialReason")),
        "DisplayPriceSource": _text(row.get("DisplayPriceSource")),
        "DecisionPriceSource": _text(row.get("DecisionPriceSource")),
        "ErrorCategory": _text((error or {}).get("category")),
        "ErrorCode": _text((error or {}).get("code")),
        "ErrorMessageSanitized": _text(sanitize_error((error or {}).get("message"))),
        "FreshnessPolicyVersion": FRESHNESS_POLICY_VERSION,
        "ConfigIdentity": _text(config_identity),
    }


#: Words that must never reach an exported error message.
_SECRET_TOKENS = ("api_token", "api_key", "token=", "password", "secret",
                  "authorization", "bearer ")


def sanitize_error(message):
    """Keep the cause, drop anything credential-shaped."""
    if not message:
        return ""
    text = str(message)
    lowered = text.lower()
    for token in _SECRET_TOKENS:
        if token in lowered:
            return "redacted: error text contained credential-like content"
    return text[:300]


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #

def build_export_metadata(*, run_id, run_status, generated_at_utc,
                          evaluation_time_utc, coverage, decisions,
                          outcome_counts, config_identity="",
                          rubix_policy_identity="", decision_price_policy="",
                          market_block_reason="", calculation_errors=0):
    """Run-level metadata. Typed values and nulls, never ambiguous blanks.

    ``calculation_errors`` counts symbols whose daily candle was CURRENT but
    whose decision never completed. They are current *data* and are not
    current *decisions*; conflating the two is what reported 11 successes for
    3 exported decisions.
    """
    universe = int(coverage.universe_total)
    loaded = int(coverage.history_loaded)
    daily_current = int(coverage.current)
    errors = max(0, int(calculation_errors))
    # `current` is the decision-bearing count, which is what every downstream
    # consumer of current_count means and what the archive invariant checks.
    current = daily_current - errors
    counts = {outcome: int(outcome_counts.get(outcome, 0)) for outcome in AUDIT_OUTCOMES}
    decision_counts = {name: sum(1 for row in decisions
                                 if str(row.get("Decision")) == name)
                       for name in DECISIONS}
    return {
        "export_schema_version": DAILY_SCAN_EXPORT_SCHEMA_VERSION,
        "freshness_policy_version": FRESHNESS_POLICY_VERSION,
        "run_id": run_id,
        "run_status": run_status,
        "generated_at_utc": generated_at_utc,
        "evaluation_time_utc": evaluation_time_utc,
        "expected_completed_session": coverage.expected_session or None,
        "operational_universe_count": universe,
        "history_loaded_count": loaded,
        "current_count": current,
        # The three counts kept explicitly separate, so no consumer has to
        # guess whether "current" meant data or decisions.
        "daily_current_count": daily_current,
        "current_decision_count": len(decisions),
        "decision_calculation_error_count": errors,
        "stale_count": int(coverage.stale),
        "missing_date_count": counts["SKIPPED_MISSING_DAILY_DATE"],
        "future_date_count": counts["SKIPPED_FUTURE_DAILY_DATE"],
        "insufficient_history_count": counts["INSUFFICIENT_HISTORY"],
        "invalid_history_count": counts["INVALID_HISTORY"],
        "provider_error_count": counts["PROVIDER_ERROR"],
        "cache_miss_count": counts["EODHD_CACHE_MISS"],
        "calculation_error_count": counts["CALCULATION_ERROR"],
        "not_attempted_count": counts["NOT_ATTEMPTED"],
        "excluded_count": universe - current,
        # The denominator is the OPERATIONAL UNIVERSE. Reporting 6/194 as
        # exchange coverage would overstate it by an order of magnitude.
        "current_coverage_percent": round(100.0 * current / universe, 1) if universe else 0.0,
        # Data coverage, separate from decision coverage: a symbol can hold a
        # current candle and still produce no decision.
        "daily_current_coverage_percent": (
            round(100.0 * daily_current / universe, 1) if universe else 0.0),
        "current_decision_coverage_percent": (
            round(100.0 * len(decisions) / universe, 1) if universe else 0.0),
        # A separately labelled secondary metric, never the headline.
        "loaded_result_current_percent": round(100.0 * current / loaded, 1) if loaded else 0.0,
        "minimum_market_coverage_percent": float(coverage.threshold_percent),
        "market_wide_summary_allowed": bool(coverage.market_wide_allowed),
        "market_wide_summary_block_reason": (
            market_block_reason or None if coverage.market_wide_allowed
            else "INSUFFICIENT_CURRENT_COVERAGE"),
        "dominant_observed_session": (
            max(coverage.distribution, key=lambda d: coverage.distribution[d])
            if coverage.distribution else None),
        "observed_session_distribution": dict(coverage.distribution),
        "decision_count": len(decisions),
        "buy_count": decision_counts["BUY"],
        "watch_count": decision_counts["WATCH"],
        "avoid_count": decision_counts["AVOID"],
        "current_decisions_filename": CURRENT_DECISIONS_FILENAME,
        "coverage_audit_filename": COVERAGE_AUDIT_FILENAME,
        "compatibility_export_filename": COMPATIBILITY_FILENAME,
        "scan_results_semantics": "CURRENT_DECISIONS_ONLY",
        "scan_results_compatibility_alias_of": CURRENT_DECISIONS_FILENAME,
        "decision_price_policy": decision_price_policy or "EODHD_DAILY_CLOSE_UNLESS_RUBIX_LIVE_CURRENT",
        "rubix_freshness_policy_identity": rubix_policy_identity or None,
        "daily_freshness_policy_identity": FRESHNESS_POLICY_VERSION,
        "strategy_config_identity": config_identity or None,
    }


# --------------------------------------------------------------------------- #
# Cross-file invariants
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ArchiveValidation:
    ok: bool
    violations: tuple = field(default_factory=tuple)


def archive_failure_provenance(metadata, audit):
    """One compact line describing what the run actually saw.

    Without this, a refused publication reaches the operator as a bare
    invariant string and the page falls back to "no dated candle" - even
    though the in-memory audit is full of dated rows.
    """

    metadata = dict(metadata or {})
    rows = list(audit or [])
    dated = [row.get("ActualCandleSession") for row in rows
             if row.get("ActualCandleSession")]
    distribution = {}
    for value in dated:
        distribution[value] = distribution.get(value, 0) + 1
    dominant = (max(distribution, key=lambda key: distribution[key])
                if distribution else None)
    return (
        f"expected completed session {metadata.get('expected_completed_session') or 'unknown'}"
        f"; dominant observed session {dominant or 'none'}"
        f"; universe {metadata.get('operational_universe_count', len(rows))}"
        f", daily-current {metadata.get('daily_current_count', '?')}"
        f", decisions {metadata.get('current_decision_count', '?')}"
        f", calculation errors {metadata.get('decision_calculation_error_count', '?')}"
        f", dated audit rows {len(dated)}"
    )


def validate_archive(*, decisions, audit, metadata, universe_symbols):
    """Check every cross-file invariant. Fail closed; publish nothing on error."""

    violations = []
    universe = list(universe_symbols)
    audit_symbols = [row["Symbol"] for row in audit]

    if len(audit) != len(universe):
        violations.append(
            f"audit has {len(audit)} rows for a universe of {len(universe)}")
    if len(set(audit_symbols)) != len(audit_symbols):
        violations.append("audit contains duplicate symbols")
    missing = set(universe) - set(audit_symbols)
    if missing:
        violations.append(f"audit omits {len(missing)} universe symbol(s)")
    unexpected = set(audit_symbols) - set(universe)
    if unexpected:
        violations.append(f"audit invents {len(unexpected)} symbol(s)")

    decision_symbols = [row["Symbol"] for row in decisions]
    if set(decision_symbols) - set(audit_symbols):
        violations.append("a current decision is absent from the audit")

    by_symbol = {row["Symbol"]: row for row in audit}
    for row in decisions:
        if row["DailyFreshnessStatus"] != "CURRENT":
            violations.append(f"{row['Symbol']} is in decisions but not CURRENT")
        if not row["EligibleForCurrentAnalysis"]:
            violations.append(f"{row['Symbol']} is in decisions but ineligible")
        if not row["DecisionCalculated"]:
            violations.append(f"{row['Symbol']} has no calculated decision")
        audited = by_symbol.get(row["Symbol"])
        if audited is not None and audited["ScanOutcome"] != OUTCOME_CURRENT:
            violations.append(
                f"{row['Symbol']} is a decision but audited {audited['ScanOutcome']}")
        # A denied overlay must leave the EODHD close as the decision price.
        if not row["RubixOverlayApplied"] and row["DecisionPriceSource"] not in (
                "", "eodhd_daily_close"):
            violations.append(
                f"{row['Symbol']} denied the Rubix overlay but priced from "
                f"{row['DecisionPriceSource']}")

    excluded = [row for row in audit if row["ScanOutcome"] != OUTCOME_CURRENT]
    leaked = {row["Symbol"] for row in excluded} & set(decision_symbols)
    if leaked:
        violations.append(f"{len(leaked)} excluded symbol(s) appear in decisions")

    current_rows = [row for row in audit if row["ScanOutcome"] == OUTCOME_CURRENT]
    if len(current_rows) != len(decisions):
        violations.append(
            f"audit reports {len(current_rows)} current rows but "
            f"{len(decisions)} decisions were exported")
    if int(metadata.get("current_count", -1)) != len(decisions):
        violations.append("metadata current_count does not match the decisions file")
    if int(metadata.get("current_decision_count", -1)) != len(decisions):
        violations.append(
            "metadata current_decision_count does not match the decisions file")
    daily_current = int(metadata.get("daily_current_count", -1))
    errors = int(metadata.get("decision_calculation_error_count", -1))
    if daily_current != len(decisions) + errors:
        violations.append(
            f"daily_current_count {daily_current} does not equal "
            f"{len(decisions)} decisions + {errors} calculation error(s)")
    audited_errors = sum(1 for row in audit
                         if row["ScanOutcome"] == OUTCOME_CALCULATION_ERROR)
    if audited_errors != errors:
        violations.append(
            f"audit holds {audited_errors} CALCULATION_ERROR row(s) but metadata "
            f"reports {errors}")
    if int(metadata.get("excluded_count", -1)) + len(decisions) != len(universe):
        violations.append("current + excluded does not equal the universe")

    decided = sum(int(metadata.get(f"{name.lower()}_count", 0)) for name in DECISIONS)
    if decided != int(metadata.get("decision_count", -1)):
        violations.append("BUY + WATCH + AVOID does not equal decision_count")

    if int(metadata.get("export_schema_version", 0)) != DAILY_SCAN_EXPORT_SCHEMA_VERSION:
        violations.append("schema version is missing or inconsistent")

    dated = sum(1 for row in audit if row["ActualCandleSession"])
    distribution_total = sum((metadata.get("observed_session_distribution") or {}).values())
    if distribution_total != dated:
        violations.append(
            f"session distribution totals {distribution_total} but {dated} audit "
            "rows carry a candle date")

    universe_count = int(metadata.get("operational_universe_count", 0))
    if universe_count:
        expected_percent = round(100.0 * len(decisions) / universe_count, 1)
        if abs(float(metadata.get("current_coverage_percent", -1)) - expected_percent) > 0.05:
            violations.append(
                "current_coverage_percent does not use the operational universe")

    return ArchiveValidation(not violations, tuple(violations))


# --------------------------------------------------------------------------- #
# Atomic publication
# --------------------------------------------------------------------------- #

def publish_archive(run_dir, *, decisions, audit, metadata, universe_symbols,
                    writer=None):
    """Validate, then publish every artifact together, or publish nothing.

    A half-written archive is worse than none: a reader cannot tell a missing
    audit from an empty one, and would take the decisions file for the whole
    universe. Files are staged beside their destination and moved into place
    only after every invariant holds.
    """

    validation = validate_archive(
        decisions=decisions, audit=audit, metadata=metadata,
        universe_symbols=universe_symbols)
    if not validation.ok:
        # The message carries the run's provenance, not just the violation.
        # A failed publication is the moment the operator most needs to know
        # which session was expected and what the scan actually observed.
        raise ArchiveInvariantError(
            "archive not published; " + "; ".join(validation.violations)
            + " | " + archive_failure_provenance(metadata, audit))

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    writer = writer or _write_csv

    staged = []
    try:
        for filename, rows in (
            (CURRENT_DECISIONS_FILENAME, decisions),
            (COVERAGE_AUDIT_FILENAME, audit),
            # The compatibility alias carries byte-identical decision rows.
            (COMPATIBILITY_FILENAME, decisions),
        ):
            temporary = _stage(run_dir, filename)
            writer(temporary, rows)
            staged.append((temporary, run_dir / filename))

        # Composed, not overwritten. The export owns exactly one namespaced
        # section; whatever generic run fields are already on disk survive,
        # and a later experiment write goes through the same service and
        # cannot drop this section. See services/run_metadata_service.py.
        from services.run_metadata_service import (
            METADATA_FILENAME, compose_run_metadata, load_run_metadata,
        )

        existing = load_run_metadata(run_dir)
        temporary = _stage(run_dir, METADATA_FILENAME)
        _write_json(temporary, compose_run_metadata(existing.run, metadata))
        staged.append((temporary, run_dir / METADATA_FILENAME))

        for source, destination in staged:
            os.replace(source, destination)
    except Exception:
        for source, _ in staged:
            try:
                Path(source).unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return validation


def _stage(run_dir, filename):
    handle, path = tempfile.mkstemp(prefix=f".{filename}.", dir=str(run_dir))
    os.close(handle)
    return path


def _write_csv(path, rows):
    import csv

    rows = list(rows)
    fields = list(rows[0]) if rows else []
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
        handle.flush()
        os.fsync(handle.fileno())


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def archive_schema_version(metadata):
    """The archive's declared schema, or LEGACY_UNKNOWN.

    Never inferred from a filename: an archive that happens to contain a
    ``scan_results.csv`` says nothing about what that file means.
    """
    if not isinstance(metadata, dict):
        return LEGACY_SCHEMA
    version = metadata.get("export_schema_version")
    return version if isinstance(version, int) else LEGACY_SCHEMA


def archive_is_decision_usable(run_dir, metadata=None):
    """Whether an archive may be presented for decision use.

    An INVALID_DATA_PROVENANCE marker overrides everything, whatever the schema
    says about itself.
    """
    if (Path(run_dir) / "INVALID_DATA_PROVENANCE.md").is_file():
        return False, "INVALID_DATA_PROVENANCE"
    version = archive_schema_version(metadata)
    if version == LEGACY_SCHEMA:
        return True, "LEGACY_UNKNOWN_SEMANTICS"
    return True, ""


__all__ = [
    "AUDIT_OUTCOMES",
    "COMPATIBILITY_FILENAME",
    "COVERAGE_AUDIT_FILENAME",
    "CURRENT_DECISIONS_FILENAME",
    "DAILY_SCAN_EXPORT_SCHEMA_VERSION",
    "FRESHNESS_POLICY_VERSION",
    "LEGACY_SCHEMA",
    "OUTCOME_CALCULATION_ERROR",
    "OUTCOME_CURRENT",
    "OUTCOME_CURRENT",
    "ArchiveInvariantError",
    "archive_failure_provenance",
    "ArchiveValidation",
    "archive_is_decision_usable",
    "archive_schema_version",
    "build_export_metadata",
    "coverage_audit_row",
    "current_decision_row",
    "publish_archive",
    "sanitize_error",
    "validate_archive",
]
