"""Operational health/smoke command for the configured TickerChart bridge."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.data_provider import (
    load_history,
    provider_health,
    reset_provider_instances,
)
from scripts.launch_egx_ai_trader import inspect_database


def main():
    parser = argparse.ArgumentParser(description="Check EGX TickerChart adapter health")
    parser.add_argument(
        "--symbols", default="COMI.CA,SWDY.CA,FWRY.CA",
        help="Comma-separated engine symbols used for the smoke test",
    )
    parser.add_argument(
        "--no-load", action="store_true",
        help="Check the adapter database without loading market history",
    )
    parser.add_argument(
        "--database",
        help="Optional adapter SQLite path; also reports launcher readiness state",
    )
    args = parser.parse_args()

    if args.database:
        os.environ["TICKERCHART_DB_PATH"] = args.database
    reset_provider_instances()
    output = {"health": provider_health("dashboard"), "symbols": []}
    if args.database:
        output["launcher_database"] = asdict(inspect_database(args.database))
    if not args.no_load:
        for symbol in [item.strip().upper() for item in args.symbols.split(",") if item.strip()]:
            try:
                frame = load_history(symbol, purpose="dashboard")
                metadata = dict(frame.attrs.get("market_data", {}))
                output["symbols"].append({
                    "symbol": symbol,
                    "bars": len(frame),
                    "latest_candle": str(frame.index[-1]),
                    "effective_provider": metadata.get("effective_provider"),
                    "fallback_active": metadata.get("fallback_active"),
                    "fallback_reason": metadata.get("fallback_reason"),
                    "operational_state": metadata.get("operational_state"),
                })
            except Exception as error:
                output["symbols"].append({"symbol": symbol, "error": str(error)})
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    main()
