"""One versioned reader for Daily Scan archives. Pages read this, not files.

Parsing archives inside each page is how readers drift: one would treat
``scan_results.csv`` as the universe, another as decisions, and a legacy run
would silently be read under whichever assumption its reader happened to hold.

This module answers one question - *what may this archive be used for?* - from
``run_metadata.export_schema_version``, never from a filename's presence. An
``INVALID_DATA_PROVENANCE.md`` marker overrides everything.

Reading is filesystem work; the classification and comparison logic around it is
pure and separately testable.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from services.daily_scan_export import (
    COMPATIBILITY_FILENAME,
    COVERAGE_AUDIT_FILENAME,
    CURRENT_DECISIONS_FILENAME,
    DAILY_SCAN_EXPORT_SCHEMA_VERSION,
    OUTCOME_CURRENT,
    validate_archive,
)


class SchemaStatus(str, Enum):
    """What kind of archive this is, and whether it may drive decisions."""

    SCHEMA_V2_VALID = "SCHEMA_V2_VALID"
    SCHEMA_V2_INVALID = "SCHEMA_V2_INVALID"
    LEGACY_V1 = "LEGACY_V1"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"
    INVALID_DATA_PROVENANCE = "INVALID_DATA_PROVENANCE"
    INCOMPLETE_ARCHIVE = "INCOMPLETE_ARCHIVE"
    UNSUPPORTED_FUTURE_SCHEMA = "UNSUPPORTED_FUTURE_SCHEMA"


LEGACY_WARNING = (
    "LEGACY ARCHIVE — COVERAGE SEMANTICS UNKNOWN\n\n"
    "This run predates the versioned current-decision and full-coverage export "
    "contract. Decision rows may be displayed for historical inspection, but "
    "full-universe coverage cannot be reconstructed safely."
)

INVALID_WARNING = "NOT FOR DECISION USE"

INVALID_MARKER = "INVALID_DATA_PROVENANCE.md"


@dataclass(frozen=True)
class DailyScanArchive:
    """A typed view of one archived Daily Scan run."""

    run_id: str
    run_path: str
    schema_version: object
    schema_status: SchemaStatus
    invalid_data_provenance: bool
    run_metadata: dict = field(default_factory=dict)
    current_decisions: tuple = ()
    coverage_audit: tuple = ()
    compatibility_decisions: tuple = ()
    archive_warnings: tuple = ()
    missing_artifacts: tuple = ()
    cache_identity: str = ""

    # -- metadata passthrough, typed and safe ------------------------------ #

    def _meta(self, key, default=None):
        value = self.run_metadata.get(key, default)
        return default if value is None else value

    @property
    def expected_completed_session(self):
        return self._meta("expected_completed_session", "")

    @property
    def operational_universe_count(self):
        """Only ever the DECLARED universe. Never the decision row count.

        Inferring a universe from however many rows survived is precisely how a
        partial scan gets reported as full coverage.
        """
        if self.schema_status is SchemaStatus.SCHEMA_V2_VALID:
            return int(self._meta("operational_universe_count", 0))
        return None

    @property
    def current_count(self):
        if self.schema_status is SchemaStatus.SCHEMA_V2_VALID:
            return int(self._meta("current_count", 0))
        return None

    @property
    def excluded_count(self):
        if self.schema_status is SchemaStatus.SCHEMA_V2_VALID:
            return int(self._meta("excluded_count", 0))
        return None

    @property
    def current_coverage_percent(self):
        if self.schema_status is SchemaStatus.SCHEMA_V2_VALID:
            return float(self._meta("current_coverage_percent", 0.0))
        return None

    @property
    def loaded_result_current_percent(self):
        if self.schema_status is SchemaStatus.SCHEMA_V2_VALID:
            return float(self._meta("loaded_result_current_percent", 0.0))
        return None

    @property
    def observed_session_distribution(self):
        return dict(self._meta("observed_session_distribution", {}) or {})

    @property
    def dominant_observed_session(self):
        return self._meta("dominant_observed_session", "")

    @property
    def market_wide_summary_allowed(self):
        if self.schema_status is SchemaStatus.SCHEMA_V2_VALID:
            return bool(self._meta("market_wide_summary_allowed", False))
        return None

    @property
    def market_wide_summary_block_reason(self):
        return self._meta("market_wide_summary_block_reason", "")

    @property
    def decision_counts(self):
        rows = self.current_decisions
        return {name: sum(1 for row in rows if row.get("Decision") == name)
                for name in ("BUY", "WATCH", "AVOID")}

    @property
    def decision_use_allowed(self) -> bool:
        """Whether decisions may be presented as valid research output."""
        if self.invalid_data_provenance:
            return False
        return self.schema_status in (SchemaStatus.SCHEMA_V2_VALID,
                                      SchemaStatus.LEGACY_V1,
                                      SchemaStatus.LEGACY_UNKNOWN)

    @property
    def coverage_available(self) -> bool:
        """Legacy archives cannot supply full-universe coverage. Say so."""
        return self.schema_status is SchemaStatus.SCHEMA_V2_VALID

    def current_symbols(self) -> set:
        return {row.get("Symbol") for row in self.current_decisions}


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #

def resolve_artifact(run_path, filename):
    """Resolve a file that must live inside the run directory.

    Refuses traversal and any link whose target escapes the run - a download
    control must never become a way to read an env file or a credential store.
    """
    root = Path(run_path).resolve()
    candidate = (root / Path(filename).name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"artifact {filename!r} escapes the run directory") from error
    return candidate


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def _read_csv(path):
    if not path.is_file():
        return None
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


def _read_json(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return None


def _digest(path):
    """Content identity, so a changed file cannot be served from cache."""
    if not path.is_file():
        return "absent"
    stat = path.stat()
    return f"{stat.st_size}:{int(stat.st_mtime_ns)}"


def archive_cache_identity(run_path):
    """Identity for caching a read archive.

    Keyed on the content of every artifact that could change what may be shown,
    including the invalid marker. Caching by run id alone would let a run that
    has just been marked invalid keep serving its previous valid presentation.
    """
    root = Path(run_path)
    parts = [str(root.resolve())]
    for name in ("run_metadata.json", CURRENT_DECISIONS_FILENAME,
                 COVERAGE_AUDIT_FILENAME, COMPATIBILITY_FILENAME, INVALID_MARKER):
        parts.append(f"{name}={_digest(root / name)}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def read_archive(run_path):
    """Load and classify one archive. Never rewrites anything it reads."""

    root = Path(run_path)
    run_id = root.name
    metadata = _read_json(root / "run_metadata.json") or {}
    invalid = (root / INVALID_MARKER).is_file()
    identity = archive_cache_identity(root)
    warnings, missing = [], []

    raw_version = metadata.get("export_schema_version")
    version = raw_version if isinstance(raw_version, int) else None

    decisions = _read_csv(root / CURRENT_DECISIONS_FILENAME)
    audit = _read_csv(root / COVERAGE_AUDIT_FILENAME)
    compatibility = _read_csv(root / COMPATIBILITY_FILENAME)

    def build(status, extra_warnings=()):
        return DailyScanArchive(
            run_id=run_id, run_path=str(root), schema_version=raw_version,
            schema_status=status, invalid_data_provenance=invalid,
            run_metadata=metadata,
            current_decisions=tuple(decisions or ()),
            coverage_audit=tuple(audit or ()),
            compatibility_decisions=tuple(compatibility or ()),
            archive_warnings=tuple(warnings) + tuple(extra_warnings),
            missing_artifacts=tuple(missing), cache_identity=identity)

    if invalid:
        # Overrides every other consideration, whatever the schema claims.
        return build(SchemaStatus.INVALID_DATA_PROVENANCE, (INVALID_WARNING,))

    if version is None:
        # Never inferred from a filename: an archive that happens to contain a
        # decisions file says nothing about what that file means.
        status = (SchemaStatus.LEGACY_V1 if compatibility is not None
                  else SchemaStatus.LEGACY_UNKNOWN)
        return build(status, (LEGACY_WARNING,))

    if version > DAILY_SCAN_EXPORT_SCHEMA_VERSION:
        return build(SchemaStatus.UNSUPPORTED_FUTURE_SCHEMA, (
            f"archive declares schema {version}; this build understands "
            f"{DAILY_SCAN_EXPORT_SCHEMA_VERSION}",))

    if version < DAILY_SCAN_EXPORT_SCHEMA_VERSION:
        return build(SchemaStatus.LEGACY_V1, (LEGACY_WARNING,))

    for name, rows in ((CURRENT_DECISIONS_FILENAME, decisions),
                       (COVERAGE_AUDIT_FILENAME, audit)):
        if rows is None:
            missing.append(name)
    if missing:
        return build(SchemaStatus.INCOMPLETE_ARCHIVE, (
            "required artifact(s) missing: " + ", ".join(missing),))

    # The metadata must name the files that actually exist.
    for key, expected in (("current_decisions_filename", CURRENT_DECISIONS_FILENAME),
                          ("coverage_audit_filename", COVERAGE_AUDIT_FILENAME)):
        if metadata.get(key) not in (None, expected):
            warnings.append(f"metadata {key} does not match the archive layout")

    # The compatibility alias must genuinely be the decisions file.
    if compatibility is not None and list(compatibility) != list(decisions):
        return build(SchemaStatus.SCHEMA_V2_INVALID, (
            "scan_results.csv does not match scan_current_decisions.csv",))

    universe = [row.get("Symbol") for row in audit]
    validation = validate_archive(decisions=list(decisions), audit=list(audit),
                                  metadata=metadata, universe_symbols=universe)
    if not validation.ok:
        return build(SchemaStatus.SCHEMA_V2_INVALID, validation.violations)

    return build(SchemaStatus.SCHEMA_V2_VALID)


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #

class DisappearanceReason(str, Enum):
    """Why a symbol present in run A is absent from run B's decisions."""

    DATA_COVERAGE_CHANGE = "DATA_COVERAGE_CHANGE"
    DECISION_CHANGE = "DECISION_CHANGE"
    UNIVERSE_CHANGE = "UNIVERSE_CHANGE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    LEGACY_SEMANTICS_UNKNOWN = "LEGACY_SEMANTICS_UNKNOWN"


COVERAGE_CAVEAT = (
    "DECISION COUNTS ARE NOT DIRECTLY COMPARABLE\n\n"
    "Current-data coverage changed from {before} to {after}.\n"
    "Part or all of the decision-count difference may be caused by data "
    "availability rather than strategy behaviour."
)

LEGACY_COMPARISON_WARNING = (
    "LIMITED LEGACY COMPARISON\n\n"
    "At least one run predates schema v2. Full data-coverage attribution is "
    "unavailable, so decision-count differences cannot be reliably separated "
    "from data-availability differences."
)


@dataclass(frozen=True)
class ArchiveComparison:
    decision_counts_a: dict
    decision_counts_b: dict
    current_in_both: tuple
    current_only_in_a: tuple
    current_only_in_b: tuple
    disappearance_reasons: dict
    decision_changes: tuple
    coverage_available: bool
    coverage_caveat: str = ""
    legacy_warning: str = ""
    blocked_reason: str = ""

    @property
    def comparable(self) -> bool:
        return not self.blocked_reason

    @property
    def strategy_claim_allowed(self) -> bool:
        """Statements about strategy require the like-for-like subset."""
        return self.comparable and bool(self.current_in_both)


def compare_archives(archive_a, archive_b):
    """Separate a strategy difference from a data-coverage difference.

    A symbol that vanished because its data went stale is not a strategy exit.
    Saying otherwise turns a provider outage into a false finding about the
    model, which is the single most misleading thing this comparison could do.
    """

    if archive_a.invalid_data_provenance or archive_b.invalid_data_provenance:
        return ArchiveComparison(
            decision_counts_a={}, decision_counts_b={}, current_in_both=(),
            current_only_in_a=(), current_only_in_b=(), disappearance_reasons={},
            decision_changes=(), coverage_available=False,
            blocked_reason=("a run is marked INVALID_DATA_PROVENANCE and may not "
                            "be compared as valid research output"))

    # Decisions ALWAYS come from the decisions file, never the coverage audit.
    symbols_a = archive_a.current_symbols()
    symbols_b = archive_b.current_symbols()
    both = tuple(sorted(symbols_a & symbols_b))
    only_a = tuple(sorted(symbols_a - symbols_b))
    only_b = tuple(sorted(symbols_b - symbols_a))

    audit_b = {row.get("Symbol"): row for row in archive_b.coverage_audit}
    audit_a = {row.get("Symbol"): row for row in archive_a.coverage_audit}
    coverage_available = archive_a.coverage_available and archive_b.coverage_available

    reasons = {}
    for symbol in only_a:
        if not coverage_available:
            reasons[symbol] = DisappearanceReason.LEGACY_SEMANTICS_UNKNOWN.value
            continue
        audited = audit_b.get(symbol)
        if audited is None:
            reasons[symbol] = DisappearanceReason.UNIVERSE_CHANGE.value
        elif audited.get("ScanOutcome") in ("PROVIDER_ERROR", "EODHD_CACHE_MISS",
                                            "CALCULATION_ERROR"):
            reasons[symbol] = DisappearanceReason.PROVIDER_FAILURE.value
        elif audited.get("ScanOutcome") != OUTCOME_CURRENT:
            reasons[symbol] = DisappearanceReason.DATA_COVERAGE_CHANGE.value
        else:
            reasons[symbol] = DisappearanceReason.DECISION_CHANGE.value

    by_a = {row.get("Symbol"): row for row in archive_a.current_decisions}
    by_b = {row.get("Symbol"): row for row in archive_b.current_decisions}
    changes = tuple(
        {"Symbol": symbol,
         "DecisionA": by_a[symbol].get("Decision"),
         "DecisionB": by_b[symbol].get("Decision"),
         "ScoreA": by_a[symbol].get("Score"),
         "ScoreB": by_b[symbol].get("Score"),
         "Changed": by_a[symbol].get("Decision") != by_b[symbol].get("Decision")}
        for symbol in both)

    caveat = ""
    if coverage_available:
        before, after = archive_a.current_coverage_percent, archive_b.current_coverage_percent
        if before != after:
            caveat = COVERAGE_CAVEAT.format(
                before=f"{archive_a.current_count}/{archive_a.operational_universe_count}"
                       f" ({before:.1f}%)",
                after=f"{archive_b.current_count}/{archive_b.operational_universe_count}"
                      f" ({after:.1f}%)")

    return ArchiveComparison(
        decision_counts_a=archive_a.decision_counts,
        decision_counts_b=archive_b.decision_counts,
        current_in_both=both, current_only_in_a=only_a, current_only_in_b=only_b,
        disappearance_reasons=reasons, decision_changes=changes,
        coverage_available=coverage_available, coverage_caveat=caveat,
        legacy_warning="" if coverage_available else LEGACY_COMPARISON_WARNING)


__all__ = [
    "COVERAGE_CAVEAT",
    "INVALID_MARKER",
    "INVALID_WARNING",
    "LEGACY_COMPARISON_WARNING",
    "LEGACY_WARNING",
    "ArchiveComparison",
    "DailyScanArchive",
    "DisappearanceReason",
    "SchemaStatus",
    "archive_cache_identity",
    "compare_archives",
    "read_archive",
    "resolve_artifact",
]
