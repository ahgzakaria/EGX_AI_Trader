"""Reconstruct overwritten Daily Scan export metadata as a sidecar record.

Dry-run by default. Never touches the CSV files, and never rewrites
``run_metadata.json``: that file is the historical evidence of what actually
happened to the run, and replacing it would destroy the very trace this
repair exists to document. The reconstruction is written beside it as

    DAILY_SCAN_EXPORT_RECOVERY.json

which records what was recomputed, what could not be, and the artifact hashes
it was derived from.

The reader does not need this file - it recovers in memory on every read. The
sidecar exists so an operator can see the reconstruction, and so an audit can
show when and from what it was produced.

    python scripts/repair_daily_scan_metadata.py --run-id RUN_20260804_225158 --dry-run
    python scripts/repair_daily_scan_metadata.py --run-id RUN_20260804_225158 --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.daily_scan_archive_reader import (  # noqa: E402
    SchemaStatus, read_archive,
)
from services.daily_scan_export import (  # noqa: E402
    COMPATIBILITY_FILENAME, COVERAGE_AUDIT_FILENAME, CURRENT_DECISIONS_FILENAME,
)

SIDECAR_FILENAME = "DAILY_SCAN_EXPORT_RECOVERY.json"


def digest(path):
    if not Path(path).is_file():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reports-root", default="reports")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--write", dest="dry_run", action="store_false",
                       help="write the sidecar recovery manifest")
    args = parser.parse_args(argv)

    root = Path(args.reports_root) / args.run_id
    if not root.is_dir():
        print(f"no such run: {root}")
        return 2

    archive = read_archive(root)
    print(f"run            : {args.run_id}")
    print(f"classification : {archive.schema_status.value}")

    if archive.schema_status is not SchemaStatus.RECOVERABLE_V2_EXPORT:
        print("refusing: this run is not a recoverable overwritten-metadata "
              "archive. Nothing to repair, and nothing will be written.")
        return 1

    recovered = dict(archive.run_metadata)
    unavailable = list(archive.unavailable_metadata_fields)

    print()
    print("reconstructed from the CSV artifacts:")
    for key in sorted(recovered):
        if key in ("unavailable_fields", "outcome_counts",
                   "observed_session_distribution"):
            continue
        print(f"  {key:36s} {recovered[key]}")
    print()
    print("NOT reconstructable - absent, never guessed:")
    for name in unavailable:
        print(f"  {name}")

    hashes = {name: digest(root / name) for name in
              (CURRENT_DECISIONS_FILENAME, COVERAGE_AUDIT_FILENAME,
               COMPATIBILITY_FILENAME, "run_metadata.json")}
    print()
    print("source artifact hashes:")
    for name, value in hashes.items():
        print(f"  {name:34s} {(value or 'missing')[:32]}")

    sidecar = root / SIDECAR_FILENAME
    payload = {
        "recovery_schema_version": 1,
        "run_id": args.run_id,
        "recovered_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": ("the schema-v2 export metadata was overwritten by legacy "
                   "experiment metadata written to the same filename"),
        "recovered_export_metadata": recovered,
        "unavailable_fields": unavailable,
        "source_artifact_sha256": hashes,
        "csv_files_modified": False,
        "run_metadata_json_modified": False,
    }

    print()
    if args.dry_run:
        print(f"DRY RUN — would write {sidecar}")
        print("re-run with --write to create the sidecar manifest")
        return 0

    if sidecar.exists():
        print(f"refusing: {SIDECAR_FILENAME} already exists")
        return 1
    sidecar.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    after = {name: digest(root / name) for name in hashes}
    print(f"wrote {sidecar}")
    print("CSV and run_metadata.json unchanged:",
          all(hashes[name] == after[name] for name in hashes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
