"""Create and optionally explicitly prune production-data backups."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.backup_manager import create_backup, prune_backups


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-root", default=str(PROJECT_ROOT / "backups"))
    parser.add_argument("--forward-db", default=str(PROJECT_ROOT / "data" / "forward_testing.db"))
    parser.add_argument("--portfolio-db", default=str(PROJECT_ROOT / "data" / "portfolio.db"))
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--confirm-prune", action="store_true")
    parser.add_argument("--keep", type=int, default=10)
    args = parser.parse_args()
    backup = create_backup(args.backup_root, forward_db=args.forward_db,
                           portfolio_db=args.portfolio_db)
    print(f"Backup created: {backup}")
    if args.prune:
        deleted = prune_backups(args.backup_root, args.keep, args.confirm_prune)
        print(f"Pruned: {deleted}")


if __name__ == "__main__":
    main()
