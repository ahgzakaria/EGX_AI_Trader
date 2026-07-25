"""Create clearly separated legacy and current-data Phase 5 baselines."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.experiment_tracking import RunRepository


LEGACY_METRICS = {
    "coverage_start": "2020-08-06", "coverage_end": "2026-06-08",
    "strategy_only": {"TotalReturn": 65.81, "NetProfit": 65811.22, "MaxDrawdown": 17.91, "Trades": 730},
    "ai_ranking_only": {"TotalReturn": 71.62, "NetProfit": 71618.87, "MaxDrawdown": 16.48, "Trades": 736},
}


def _write(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def establish(current_run_id=None, replace_current=False):
    root = PROJECT_ROOT / "reports" / "baselines"
    legacy = root / "PHASE5_VALIDATED_BASELINE"
    legacy.mkdir(parents=True, exist_ok=True)
    _write(legacy / "BASELINE.json", {
        "name": "PHASE5_VALIDATED_BASELINE",
        "status": "LEGACY_METRICS_ONLY",
        "replay_ready": False,
        "reason": "The original Yahoo OHLCV snapshot was not archived and cannot be recovered exactly.",
        "metrics": LEGACY_METRICS,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
    })
    (legacy / "README.md").write_text(
        "# PHASE5_VALIDATED_BASELINE\n\nStatus: **LEGACY_METRICS_ONLY**.\n\n"
        "These validated historical metrics remain reference evidence, but the original Yahoo input bytes were not archived. "
        "They must not be presented as replayable.\n",
        encoding="utf-8",
    )
    if not current_run_id:
        return {"legacy": str(legacy), "current": None}
    run_dir = RunRepository._safe_run_dir(current_run_id)
    metadata = RunRepository.get(current_run_id)
    if metadata.get("baseline_name") != "PHASE5_CURRENT_DATA_V2":
        raise ValueError("Selected run is not a PHASE5_CURRENT_DATA_V2 validation")
    if not metadata.get("replay_ready"):
        raise RuntimeError("Current baseline run has no completed dataset archive")
    current = root / "PHASE5_CURRENT_DATA_V2"
    if current.exists():
        if not replace_current:
            raise FileExistsError("PHASE5_CURRENT_DATA_V2 already exists; baselines are immutable")
        existing = json.loads((current / "BASELINE.json").read_text(encoding="utf-8"))
        archived = root / (
            "PHASE5_CURRENT_DATA_V2_NONREPLAYABLE_"
            + str(existing.get("source_run_id") or "UNKNOWN")
        )
        if archived.exists():
            raise FileExistsError(f"Preserved diagnostic baseline already exists: {archived}")
        os.replace(current, archived)
    staging = root / ".PHASE5_CURRENT_DATA_V2.staging"
    staging.mkdir(parents=True)
    try:
        shutil.copytree(run_dir / "dataset", staging / "dataset")
        for name in ("settings_snapshot.json", "environment.json", "model_info.json", "run_metadata.json", "phase5_current_data_v2.csv"):
            if (run_dir / name).is_file():
                shutil.copy2(run_dir / name, staging / name)
        _write(staging / "BASELINE.json", {
            "name": "PHASE5_CURRENT_DATA_V2", "status": "REPRODUCIBLE_CURRENT_DATA",
            "source_run_id": current_run_id, "dataset_hash": metadata.get("dataset_hash"),
            "metrics": metadata.get("metrics"), "legacy_equivalent": False,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        })
        os.replace(staging, current)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"legacy": str(legacy), "current": str(current)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-run-id")
    parser.add_argument("--replace-current", action="store_true")
    args = parser.parse_args()
    print(json.dumps(establish(args.current_run_id, args.replace_current), indent=2))


if __name__ == "__main__":
    main()
