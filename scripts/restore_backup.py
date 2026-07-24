"""Restore a verified backup into an isolated destination."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.backup_manager import restore_backup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    print(f"Restored to: {restore_backup(Path(args.backup), Path(args.destination))}")


if __name__ == "__main__":
    main()
