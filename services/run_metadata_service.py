"""Single owner of ``run_metadata.json``.

Two components used to write this file independently. ``publish_archive``
wrote the schema-v2 Daily Scan export metadata; ``ExperimentRun.complete``
then wrote its own dictionary over the top. Both writes were individually
atomic, so nothing looked broken - the second simply won, and the export
fields vanished. RUN_20260804_225158 kept three correct CSV files and lost
every coverage figure describing them.

The fix is ownership, not ordering. One document, one writer, namespaced
sections:

    {
      "run_metadata_schema_version": 2,
      ... generic run/experiment fields, flat ...
      "daily_scan_export": { ... schema-v2 export fields ... }
    }

The generic run fields stay at the top level deliberately. Six consumers -
run_status, run_history, compare_runs, backup_manager, the archive reader and
the phase-5 baseline script - read ``status``, ``run_type`` and friends
directly from the root. Nesting them under ``run`` would be a rename with no
safety benefit, and the defect was never about where the generic fields live.
It was about a second writer erasing a section it did not own.

Every write goes through :func:`write_run_metadata_atomic`, which merges by
section and refuses to drop or downgrade one.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

#: Version of the *document*, distinct from the export payload's own
#: ``export_schema_version``. Bumped when section layout changes.
RUN_METADATA_SCHEMA_VERSION = 2

METADATA_FILENAME = "run_metadata.json"

#: The reserved namespace owned solely by the Daily Scan export.
EXPORT_SECTION = "daily_scan_export"

#: Fields the export section must carry to be considered present and usable.
REQUIRED_EXPORT_FIELDS = (
    "export_schema_version",
    "run_id",
    "operational_universe_count",
    "current_decision_count",
    "expected_completed_session",
)

#: Root keys that belong to the generic run document and may never be written
#: by the export side.
RESERVED_RUN_KEYS = frozenset({
    "run_metadata_schema_version", "status", "run_type", "schema_version",
})


class MetadataConflictError(RuntimeError):
    """A write would contradict or destroy metadata already on disk."""


@dataclass(frozen=True)
class RunMetadata:
    """A loaded document, with its sections separated."""

    path: str
    document: dict
    run: dict
    export: dict
    document_version: int | None

    @property
    def has_export(self) -> bool:
        return bool(self.export) and all(
            self.export.get(name) is not None for name in REQUIRED_EXPORT_FIELDS)

    @property
    def export_schema_version(self):
        return self.export.get("export_schema_version")


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_run_metadata(run_dir) -> RunMetadata:
    """Load the document and split it into its sections.

    Understands three shapes: the composed document, a legacy flat experiment
    document, and a bare schema-v2 export document written before this service
    existed. It never guesses - a flat document with ``export_schema_version``
    at the root really was written by the export writer.
    """

    path = Path(run_dir) / METADATA_FILENAME
    document = _read_json(path)
    if not isinstance(document, dict):
        return RunMetadata(str(path), {}, {}, {}, None)

    version = document.get("run_metadata_schema_version")
    export = document.get(EXPORT_SECTION)
    if not isinstance(export, dict):
        # A pre-service archive: the whole document is either the export
        # payload or the experiment payload, never both.
        export = document if document.get("export_schema_version") else {}
    run = {key: value for key, value in document.items()
           if key != EXPORT_SECTION}
    if export is document:
        run = {}
    return RunMetadata(str(path), document, run, dict(export),
                       version if isinstance(version, int) else None)


def compose_run_metadata(run_section=None, export_section=None) -> dict:
    """Build one authoritative document from its two sections."""

    run = dict(run_section or {})
    export = dict(export_section or {})
    conflicting = RESERVED_RUN_KEYS & set(export)
    if conflicting:
        raise MetadataConflictError(
            f"the export section may not own run keys: {sorted(conflicting)}")
    document = dict(run)
    document["run_metadata_schema_version"] = RUN_METADATA_SCHEMA_VERSION
    if export:
        document[EXPORT_SECTION] = export
    return document


def _check_compatible(existing: RunMetadata, run_section, export_section):
    """Refuse a write that would destroy or contradict what is on disk."""

    violations = []

    if existing.document_version is not None and \
            existing.document_version > RUN_METADATA_SCHEMA_VERSION:
        violations.append(
            f"on-disk document schema {existing.document_version} is newer than "
            f"{RUN_METADATA_SCHEMA_VERSION}; refusing to downgrade")

    if existing.has_export and not export_section:
        violations.append(
            "refusing to write metadata that drops the existing "
            f"{EXPORT_SECTION} section")

    if existing.export and export_section:
        for field in ("run_id", "expected_completed_session"):
            old, new = existing.export.get(field), export_section.get(field)
            if old and new and old != new:
                violations.append(
                    f"{EXPORT_SECTION}.{field} conflict: {old!r} vs {new!r}")
        old_version = existing.export.get("export_schema_version")
        new_version = export_section.get("export_schema_version")
        if isinstance(old_version, int) and isinstance(new_version, int) \
                and new_version < old_version:
            violations.append(
                f"refusing to downgrade export schema {old_version} to {new_version}")

    old_run_id = existing.run.get("run_id")
    new_run_id = (run_section or {}).get("run_id")
    if old_run_id and new_run_id and old_run_id != new_run_id:
        violations.append(f"run_id conflict: {old_run_id!r} vs {new_run_id!r}")

    for name in ("current_decisions_filename", "coverage_audit_filename",
                 "compatibility_export_filename"):
        old = existing.export.get(name)
        new = (export_section or {}).get(name)
        if old and new and old != new:
            violations.append(f"{name} conflict: {old!r} vs {new!r}")

    return violations


def write_run_metadata_atomic(run_dir, *, run_section=None, export_section=None,
                              allow_missing_export=False) -> dict:
    """Compose, validate and atomically publish the document.

    ``export_section`` omitted always means "leave whatever is already there".
    An existing export section is preserved unconditionally - that is the
    whole point of this service, and no flag may turn it off.

    ``allow_missing_export=True`` says only that this caller does not supply
    an export and does not need one to exist: a backtest, or an experiment
    finalizing before any scan has published. It never authorises deleting a
    section that is already on disk.
    """

    directory = Path(run_dir)
    existing = load_run_metadata(directory)

    export = dict(export_section or {})
    if not export:
        # Preserve, unconditionally. A caller that does not own the section
        # does not get to drop it by staying silent about it.
        export = dict(existing.export)

    violations = _check_compatible(existing, run_section, export)
    if violations:
        raise MetadataConflictError("; ".join(violations))
    if not export and not allow_missing_export and existing.has_export:
        raise MetadataConflictError(
            f"refusing to write metadata without the {EXPORT_SECTION} section")

    document = compose_run_metadata(run_section, export)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / METADATA_FILENAME
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False,
                                    default=str), encoding="utf-8")
    try:
        with open(temporary, "rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        pass
    os.replace(temporary, path)
    return document


def update_run_metadata_section(run_dir, section, payload) -> dict:
    """Additively update one section, preserving every other one."""

    existing = load_run_metadata(run_dir)
    if section == EXPORT_SECTION:
        return write_run_metadata_atomic(
            run_dir, run_section=existing.run, export_section=dict(payload))
    run = dict(existing.run)
    run.update(payload or {})
    return write_run_metadata_atomic(run_dir, run_section=run,
                                     allow_missing_export=True)


__all__ = [
    "EXPORT_SECTION",
    "METADATA_FILENAME",
    "MetadataConflictError",
    "REQUIRED_EXPORT_FIELDS",
    "RUN_METADATA_SCHEMA_VERSION",
    "RunMetadata",
    "compose_run_metadata",
    "load_run_metadata",
    "update_run_metadata_section",
    "write_run_metadata_atomic",
]
