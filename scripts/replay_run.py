"""Command-line entry point for offline experiment replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.run_replay import replay_run


def main():
    parser = argparse.ArgumentParser(description="Replay an archived EGX AI Trader run")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = replay_run(args.run_id)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
