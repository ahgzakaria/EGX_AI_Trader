"""Checksummed backups for operational databases and experiment metadata."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_ROOT = PROJECT_ROOT / "backups"
SAFE_EXPERIMENT_FILES = {
    "run_metadata.json", "settings_snapshot.json", "model_info.json",
    "environment.json", "RUN_INTEGRITY.json", "README.md", "MANIFEST.json",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def _backup_sqlite(source: Path, destination: Path) -> dict:
    if not source.is_file():
        return {"source": str(source), "status": "MISSING"}
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    read_connection = sqlite3.connect(
        f"file:{source.resolve().as_posix()}?mode=ro", uri=True
    )
    write_connection = sqlite3.connect(temporary)
    try:
        read_connection.backup(write_connection)
    finally:
        write_connection.close()
        read_connection.close()
    check = sqlite3.connect(temporary)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if integrity != "ok":
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"SQLite backup integrity failed: {source}")
    os.replace(temporary, destination)
    return {
        "source": str(source), "status": "BACKED_UP", "path": destination.name,
        "sha256": _sha256(destination), "bytes": destination.stat().st_size,
        "integrity": integrity,
    }


def create_backup(
    backup_root=DEFAULT_BACKUP_ROOT,
    rubix_db=PROJECT_ROOT / "data" / "rubix_live_market.db",
    forward_db=PROJECT_ROOT / "data" / "forward_testing.db",
) -> Path:
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    root = Path(backup_root)
    root.mkdir(parents=True, exist_ok=True)
    final = root / f"BACKUP_{timestamp}"
    staging = root / f".BACKUP_{timestamp}.staging"
    if final.exists() or staging.exists():
        raise FileExistsError("Backup identifier collision")
    staging.mkdir()
    try:
        databases = [
            _backup_sqlite(Path(rubix_db), staging / "rubix_live_market.db"),
            _backup_sqlite(Path(forward_db), staging / "forward_testing.db"),
        ]
        metadata_zip = staging / "experiment_metadata.zip"
        with zipfile.ZipFile(metadata_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            reports = PROJECT_ROOT / "reports"
            if reports.exists():
                for path in sorted(reports.rglob("*")):
                    if not path.is_file():
                        continue
                    if path.name in SAFE_EXPERIMENT_FILES or path.parent.name == "baselines":
                        archive.write(path, path.relative_to(PROJECT_ROOT))
        files = [
            {"path": metadata_zip.name, "sha256": _sha256(metadata_zip), "bytes": metadata_zip.stat().st_size}
        ]
        files.extend(
            {key: item[key] for key in ("path", "sha256", "bytes")}
            for item in databases if item.get("status") == "BACKED_UP"
        )
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "status": "COMPLETED",
            "contains_secrets": False,
            "databases": databases,
            "files": files,
            "retention": "MANUAL_PRUNE_ONLY",
        }
        _atomic_json(staging / "BACKUP_MANIFEST.json", manifest)
        os.replace(staging, final)
        return final
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_backup(backup_dir: Path) -> dict:
    backup_dir = Path(backup_dir).resolve()
    manifest_path = backup_dir / "BACKUP_MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("BACKUP_MANIFEST.json is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = []
    for item in manifest.get("files", []):
        path = backup_dir / item["path"]
        if not path.is_file():
            failures.append({"path": item["path"], "reason": "missing"})
        elif _sha256(path) != item["sha256"]:
            failures.append({"path": item["path"], "reason": "checksum mismatch"})
    return {"valid": not failures, "failures": failures, "manifest": manifest}


def restore_backup(backup_dir: Path, destination: Path) -> Path:
    """Restore to a new isolated directory; live files are never overwritten."""
    backup_dir = Path(backup_dir).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("Restore destination already exists; live overwrite is refused")
    verification = verify_backup(backup_dir)
    if not verification["valid"]:
        raise RuntimeError(f"Backup checksum verification failed: {verification['failures']}")
    staging = destination.with_name(destination.name + ".staging")
    if staging.exists():
        raise FileExistsError("Restore staging destination already exists")
    staging.mkdir(parents=True)
    try:
        for item in verification["manifest"].get("files", []):
            source = backup_dir / item["path"]
            shutil.copy2(source, staging / source.name)
        archive_path = staging / "experiment_metadata.zip"
        if archive_path.is_file():
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    target = (staging / "metadata" / member.filename).resolve()
                    if not str(target).startswith(str((staging / "metadata").resolve())):
                        raise RuntimeError("Unsafe backup archive path")
                archive.extractall(staging / "metadata")
        for database in staging.glob("*.db"):
            connection = sqlite3.connect(database)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                connection.close()
            if integrity != "ok":
                raise RuntimeError(f"Restored database failed integrity: {database.name}")
        os.replace(staging, destination)
        return destination
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def prune_backups(backup_root=DEFAULT_BACKUP_ROOT, keep=10, confirmed=False) -> list[str]:
    if not confirmed:
        raise PermissionError("Backup pruning requires explicit confirmation")
    backups = sorted(
        (path for path in Path(backup_root).glob("BACKUP_*") if path.is_dir()),
        reverse=True,
    )
    deleted = []
    for path in backups[int(keep):]:
        shutil.rmtree(path)
        deleted.append(path.name)
    return deleted
