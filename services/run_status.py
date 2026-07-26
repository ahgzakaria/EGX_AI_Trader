"""Effective run status, including runs whose completion metadata was never written.

`RUN_20260726_212722` finished a 220-symbol scan and holds a fully valid dataset, but
its `run_metadata.json` still says `RUNNING`: the original process died publishing the
archive, before completion metadata was written. That file is historical evidence of what
actually happened and must stay byte-for-byte intact — so the correction is recorded
*beside* it, never inside it.

A recovery **sidecar** (`finalization_recovery.json`) states that a finalized dataset was
published later, and a central resolver decides the status a reader should show. The
resolver is deliberately conservative: a sidecar can only lift a run to
`RECOVERED_COMPLETED` when the dataset really is there, its manifest validates, the run id
matches, and the sidecar's recorded manifest hash still matches the manifest on disk. A
stale, forged or mismatched sidecar changes nothing.

Nothing here rewrites metadata, metrics, predictions, failures, dataset files or
timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from services.dataset_archive import (
    DATASET_NAME,
    STAGING_NAME,
    manifest_problem,
    read_manifest,
)

RECOVERY_SIDECAR_NAME = "finalization_recovery.json"
RECOVERY_VERSION = "1.0"

# Statuses a reader may see.
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
RECOVERED_COMPLETED = "RECOVERED_COMPLETED"
UNKNOWN = "UNKNOWN"

# Where the effective status came from.
SOURCE_SIDECAR = "recovery_sidecar"
SOURCE_METADATA = "run_metadata"
SOURCE_MISSING = "unavailable"

# Outcomes of an attempt to record a recovery.
RECOVERY_CREATED = "RECOVERY_CREATED"
RECOVERY_ALREADY_RECORDED = "RECOVERY_ALREADY_RECORDED"
RECOVERY_CONFLICT_PRESERVED = "RECOVERY_CONFLICT_PRESERVED"
RECOVERY_NOT_ELIGIBLE = "RECOVERY_NOT_ELIGIBLE"

STATUS_LABELS = {
    RECOVERED_COMPLETED: ("تم الاستكمال بعد انقطاع الحفظ", "Recovered Completed"),
    COMPLETED: ("مكتمل", "Completed"),
    RUNNING: ("قيد التشغيل", "Running"),
    "FAILED": ("فشل", "Failed"),
    "CANCELLED": ("أُلغي", "Cancelled"),
    "INTERRUPTED": ("انقطع", "Interrupted"),
    UNKNOWN: ("غير معروف", "Unknown"),
}


def status_label(status: str) -> tuple[str, str]:
    """(arabic, english) label for a status token."""
    return STATUS_LABELS.get(str(status), (str(status), str(status)))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


# --------------------------------------------------------------------------- #
# Sidecar
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RecoveryResult:
    """Typed outcome of recording (or re-checking) a finalization recovery."""
    status: str
    sidecar_path: Path | None = None
    sidecar: dict | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (RECOVERY_CREATED, RECOVERY_ALREADY_RECORDED)


def read_recovery_sidecar(run_dir) -> dict | None:
    """Return the recovery sidecar for a run, or ``None``."""
    return _load_json(Path(run_dir) / RECOVERY_SIDECAR_NAME)


def sidecar_problem(sidecar: dict | None, run_dir) -> str:
    """Return why a sidecar may NOT lift a run's status, else ``""``.

    Every condition here is a guard against a sidecar that says "completed" when the
    evidence does not support it.
    """
    run_dir = Path(run_dir)
    if not isinstance(sidecar, dict):
        return "sidecar missing or unreadable"
    if sidecar.get("effective_status") != RECOVERED_COMPLETED:
        return f"sidecar effective_status is {sidecar.get('effective_status')!r}"
    if str(sidecar.get("run_id") or "") != run_dir.name:
        return "sidecar run id does not match the run directory"

    dataset = run_dir / DATASET_NAME
    if not dataset.is_dir():
        return "no finalized dataset directory"
    manifest = read_manifest(dataset)
    problem = manifest_problem(manifest, dataset)
    if problem:
        return f"dataset manifest is not valid: {problem}"

    manifest_path = dataset / "MANIFEST.json"
    recorded = str(sidecar.get("manifest_sha256") or "")
    if not recorded:
        return "sidecar records no manifest hash"
    if recorded != _sha256_file(manifest_path):
        return "sidecar manifest hash does not match the finalized manifest"
    return ""


def record_finalization_recovery(run_dir, *, reason: str = "", now=None) -> RecoveryResult:
    """Record that a run's dataset was finalized after its process crashed.

    Idempotent: an existing valid sidecar is returned untouched, so ``recovered_at``
    never moves and no duplicate is created. A sidecar that conflicts with the dataset
    on disk is preserved and reported — never overwritten.
    """
    run_dir = Path(run_dir)
    sidecar_path = run_dir / RECOVERY_SIDECAR_NAME
    metadata_path = run_dir / "run_metadata.json"
    dataset = run_dir / DATASET_NAME

    existing = read_recovery_sidecar(run_dir)
    if existing is not None:
        problem = sidecar_problem(existing, run_dir)
        if not problem:
            return RecoveryResult(RECOVERY_ALREADY_RECORDED, sidecar_path, existing,
                                  "recovery already recorded")
        return RecoveryResult(
            RECOVERY_CONFLICT_PRESERVED, sidecar_path, existing,
            f"existing sidecar preserved untouched: {problem}")

    if not dataset.is_dir():
        return RecoveryResult(RECOVERY_NOT_ELIGIBLE, None, None,
                              "no finalized dataset directory")
    manifest = read_manifest(dataset)
    problem = manifest_problem(manifest, dataset)
    if problem:
        return RecoveryResult(RECOVERY_NOT_ELIGIBLE, None, None,
                              f"dataset manifest is not valid: {problem}")

    metadata = _load_json(metadata_path)
    original_status = (metadata or {}).get("status") or UNKNOWN
    if metadata is not None and str(metadata.get("run_id") or run_dir.name) != run_dir.name:
        return RecoveryResult(RECOVERY_NOT_ELIGIBLE, None, None,
                              "run id in metadata does not match the run directory")
    if original_status == COMPLETED:
        return RecoveryResult(RECOVERY_NOT_ELIGIBLE, None, None,
                              "run already reports COMPLETED; no recovery needed")

    moment = now or datetime.now(timezone.utc).astimezone()
    sidecar = {
        "run_id": run_dir.name,
        "recovery_version": RECOVERY_VERSION,
        "original_status": original_status,
        "effective_status": RECOVERED_COMPLETED,
        "finalization_result": "FINALIZED",
        "recovered_at": moment.isoformat() if hasattr(moment, "isoformat") else str(moment),
        "dataset_path": dataset.name,
        "manifest_sha256": _sha256_file(dataset / "MANIFEST.json"),
        "staging_path_present": (run_dir / STAGING_NAME).exists(),
        "destination_path_present": dataset.is_dir(),
        "recovery_reason": reason or (
            "the original process crashed publishing the dataset archive before it "
            "could write completion metadata; the dataset was finalized afterwards "
            "from the intact staging directory without rescanning"
        ),
        "original_metadata_sha256": (
            _sha256_file(metadata_path) if metadata_path.is_file() else ""
        ),
    }
    # Written once, atomically, and never rewritten afterwards.
    temporary = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
    text = json.dumps(sidecar, indent=2, ensure_ascii=False)
    import os

    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, sidecar_path)
    return RecoveryResult(RECOVERY_CREATED, sidecar_path, sidecar,
                          "recovery sidecar written")


# --------------------------------------------------------------------------- #
# Effective status
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class EffectiveStatus:
    """The status a reader should display, and where it came from."""
    status: str
    source: str
    detail: str = ""

    @property
    def recovered(self) -> bool:
        return self.status == RECOVERED_COMPLETED

    @property
    def labels(self) -> tuple[str, str]:
        return status_label(self.status)


def resolve_effective_status(run_dir, metadata: dict | None = None) -> EffectiveStatus:
    """Resolve a run's display status.

    Precedence:
      1. a **valid** recovery sidecar → ``RECOVERED_COMPLETED``
      2. a normal completed ``run_metadata`` status
      3. the original ``run_metadata`` status as recorded
      4. ``UNKNOWN`` when metadata is unavailable or invalid

    A `RUNNING` run is never silently converted: step 1 applies only when the finalized
    dataset, its manifest, the run id and the recorded manifest hash all agree.
    """
    run_dir = Path(run_dir)
    if metadata is None:
        metadata = _load_json(run_dir / "run_metadata.json")

    sidecar = read_recovery_sidecar(run_dir)
    if sidecar is not None:
        problem = sidecar_problem(sidecar, run_dir)
        if not problem:
            return EffectiveStatus(RECOVERED_COMPLETED, SOURCE_SIDECAR,
                                   "recovered after an interrupted archive publish")
        # An invalid sidecar is ignored for status purposes, never trusted.
        if not isinstance(metadata, dict) or not metadata.get("status"):
            return EffectiveStatus(UNKNOWN, SOURCE_MISSING, problem)
        return EffectiveStatus(str(metadata["status"]), SOURCE_METADATA,
                               f"recovery sidecar ignored: {problem}")

    if not isinstance(metadata, dict) or not metadata.get("status"):
        return EffectiveStatus(UNKNOWN, SOURCE_MISSING,
                               "run metadata unavailable or invalid")
    return EffectiveStatus(str(metadata["status"]), SOURCE_METADATA, "")


def annotate_metadata(run_dir, metadata: dict) -> dict:
    """Return a COPY of ``metadata`` carrying the effective status for readers.

    The file on disk is never touched — this augmentation exists only in memory.
    """
    resolved = resolve_effective_status(run_dir, metadata)
    enriched = dict(metadata or {})
    enriched["effective_status"] = resolved.status
    enriched["effective_status_source"] = resolved.source
    if resolved.detail:
        enriched["effective_status_detail"] = resolved.detail
    return enriched


__all__ = [
    "RECOVERY_SIDECAR_NAME", "RECOVERY_VERSION", "RUNNING", "COMPLETED",
    "RECOVERED_COMPLETED", "UNKNOWN", "SOURCE_SIDECAR", "SOURCE_METADATA",
    "SOURCE_MISSING", "RECOVERY_CREATED", "RECOVERY_ALREADY_RECORDED",
    "RECOVERY_CONFLICT_PRESERVED", "RECOVERY_NOT_ELIGIBLE", "STATUS_LABELS",
    "status_label", "RecoveryResult", "read_recovery_sidecar", "sidecar_problem",
    "record_finalization_recovery", "EffectiveStatus", "resolve_effective_status",
    "annotate_metadata",
]
