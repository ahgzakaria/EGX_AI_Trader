"""Audit or explicitly recover orphaned experiment RUNNING states."""

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.experiment_tracking import RunRepository


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--age-hours", type=float, default=6)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        RunRepository.recover_stale_runs(args.age_hours, args.apply),
        indent=2,
    ))
