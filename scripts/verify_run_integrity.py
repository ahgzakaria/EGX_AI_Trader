"""Verify immutable experiment and dataset manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.dataset_archive import load_archived_frames, sha256_file
from services.experiment_tracking import RunRepository


def verify(run_id: str) -> dict:
    directory = RunRepository._safe_run_dir(run_id)
    seal_path = directory / "RUN_INTEGRITY.json"
    if not seal_path.is_file():
        raise FileNotFoundError("Run predates immutable integrity seals")
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    failures = []
    for item in seal.get("files", []):
        path = directory / item["path"]
        if not path.is_file():
            failures.append({"path": item["path"], "reason": "missing"})
        elif sha256_file(path) != item["sha256"]:
            failures.append({"path": item["path"], "reason": "checksum mismatch"})
    frames, manifest = load_archived_frames(directory)
    result = {
        "run_id": run_id,
        "valid": not failures,
        "failures": failures,
        "dataset_hash": manifest.get("dataset_hash"),
        "symbols_verified": len(frames),
    }
    if failures:
        raise RuntimeError(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.run_id), indent=2))


if __name__ == "__main__":
    main()
