"""Build and persist the per-sector daily liquidity history.

Run after scripts/build_sector_map.py:

    venv/Scripts/python.exe scripts/build_sector_flow.py

Reads completed daily candles for the whole universe through the standard
current-research routing, aggregates turnover by sector, and stores the result
in data/sector_flow.db. A coverage report is written alongside it so any symbol
that failed to load is visible -- sector shares are only as complete as the
symbols behind them.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.environment import load_project_environment
from sector_flow.builder import DEFAULT_DATABASE, DEFAULT_PURPOSE, build, save
from sector_flow.history import TURNOVER_METHODS, latest_snapshot


DEFAULT_COVERAGE_REPORT = "reports/sector_flow_coverage.csv"


def main():
    parser = argparse.ArgumentParser(description="Build the EGX sector liquidity history.")
    parser.add_argument("--universe", default=None,
                        help="Universe CSV; defaults to core.universe.UNIVERSE_SOURCE.")
    parser.add_argument("--sector-file", default="data/sectors.csv")
    parser.add_argument("--purpose", default=DEFAULT_PURPOSE,
                        choices=["dashboard", "scanner", "forward_testing"])
    parser.add_argument("--method", default="typical", choices=list(TURNOVER_METHODS))
    parser.add_argument("--period", default=None, help="Override the configured history period.")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--coverage-report", default=DEFAULT_COVERAGE_REPORT)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    load_project_environment()
    history, outcomes, metadata = build(
        universe_path=args.universe, sector_file=args.sector_file,
        purpose=args.purpose, method=args.method, period=args.period,
    )

    Path(args.coverage_report).parent.mkdir(parents=True, exist_ok=True)
    outcomes.to_csv(args.coverage_report, index=False)
    if history.empty:
        print("No sector history produced -- see", args.coverage_report)
        return 1

    save(history, metadata, args.database)
    print(f"symbols loaded  : {metadata['loaded_symbols']}/{metadata['classified_symbols']} classified"
          f" ({metadata['unavailable_symbols']} unavailable, {metadata['unclassified_symbols']} unclassified)")
    print(f"providers       : {metadata['providers']}")
    print(f"history         : {metadata['sessions']} sessions x {metadata['sectors']} sectors"
          f" [{metadata['first_session']} .. {metadata['last_session']}] -> {args.database}")
    print(f"complete        : {metadata['complete_sessions']} sessions at >= "
          f"{metadata['min_coverage']:.0%} panel coverage, latest {metadata['last_complete_session']}")
    if metadata["last_complete_session"] != metadata["last_session"]:
        print(f"  note          : {metadata['last_session']} is in progress or partial;"
              " it is stored and flagged, not ranked.")
    print(f"coverage report : {args.coverage_report}")
    print()
    snapshot = latest_snapshot(history)
    columns = ["Sector", "Symbols", "Turnover", "TurnoverShare", "ShareChange", "RVOL", "Breadth"]
    print(f"Latest complete session {metadata['last_complete_session']}:")
    print(snapshot[columns].to_string(index=False, float_format=lambda value: f"{value:,.4f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
