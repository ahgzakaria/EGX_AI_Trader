"""Read-only operational health command for the Rubix SQLite adapter."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from providers.rubix_sqlite_provider import RubixSQLiteProvider  # noqa: E402
from providers.yahoo_provider import YahooProvider  # noqa: E402
from config.settings_manager import settings  # noqa: E402
from core.symbols import load_symbols  # noqa: E402


def _utc(value, naive_zone="UTC"):
    """Normalize comparison timestamps without exposing session material."""

    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(naive_zone)
    return timestamp.tz_convert("UTC")


def main():
    parser = argparse.ArgumentParser(
        description="Inspect Rubix adapter SQLite without writing to it"
    )
    parser.add_argument(
        "--database", default=(
            os.getenv("RUBIX_DB_PATH")
            or settings.get("market_data").get("rubix_db_path")
            or str(PROJECT_ROOT / "data" / "rubix_live_market.db")
        ),
        help="Path to the adapter-owned SQLite database",
    )
    parser.add_argument("--compare-yahoo", action="store_true")
    parser.add_argument("--symbol", default="COMI.CA")
    parser.add_argument(
        "--route-smoke", action="store_true",
        help="Run the real Dashboard ProviderManager route for --symbol",
    )
    args = parser.parse_args()
    market_cfg = settings.get("market_data")
    health = RubixSQLiteProvider(
        db_path=args.database,
        stale_after_minutes=float(market_cfg.get("rubix_quote_stale_seconds", 60)) / 60,
        bar_stale_after_minutes=float(market_cfg.get("rubix_bar_stale_seconds", 120)) / 60,
        expected_symbols=load_symbols("data/symbols.csv"),
    ).health()
    if args.compare_yahoo:
        try:
            yahoo = YahooProvider().load_history(args.symbol, "10y", "1d")
            yahoo_latest = yahoo.index[-1].isoformat()
            rubix_latest = health.get("latest_exchange_timestamp")
            rubix_newer = bool(
                rubix_latest
                and _utc(rubix_latest) > _utc(yahoo.index[-1], naive_zone="Africa/Cairo")
            )
            health["comparison"] = {
                "symbol": args.symbol,
                "rubix_latest": rubix_latest,
                "yahoo_latest": yahoo_latest,
                "selected": (
                    "rubix" if rubix_newer else "yahoo"
                ),
                "rubix_newer": rubix_newer,
            }
        except Exception as error:
            health["comparison"] = {"symbol": args.symbol, "error": str(error)}
    if args.route_smoke:
        try:
            if args.database:
                os.environ["RUBIX_DB_PATH"] = args.database
            from core.data_provider import load_history, reset_provider_instances

            reset_provider_instances()
            frame = load_history(args.symbol, purpose="dashboard")
            metadata = dict(frame.attrs.get("market_data", {}))
            health["route_smoke"] = {
                "symbol": args.symbol,
                "bars": len(frame),
                "latest_candle": frame.index[-1].isoformat(),
                "requested_provider": metadata.get("requested_provider"),
                "effective_provider": metadata.get("effective_provider"),
                "fallback_active": metadata.get("fallback_active"),
                "fallback_reason": metadata.get("fallback_reason"),
                "freshness": metadata.get("freshness_status"),
                "database_status": metadata.get("database_status"),
            }
        except Exception as error:
            health["route_smoke"] = {"symbol": args.symbol, "error": str(error)}
    print(json.dumps(health, indent=2, default=str))
    return 0 if health.get("schema_valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
