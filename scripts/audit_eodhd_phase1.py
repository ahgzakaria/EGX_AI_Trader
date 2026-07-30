"""Audit EODHD EGX history beside Yahoo without changing provider routing.

The API token is read from the environment or an interactive hidden prompt.
It is never accepted as a command-line argument, written to disk, or logged.
"""

from __future__ import annotations

import argparse
from getpass import getpass
import json
from pathlib import Path
import sys
import time

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings_manager import settings
from core.symbols import load_symbols
from providers.base_provider import (
    ProviderConfigurationError,
    ProviderError,
)
from providers.eodhd_provider import EODHDProvider
from providers.local_cache_provider import LocalCacheProvider
from providers.symbol_mapping import to_eodhd_symbol
from core.symbols import SYMBOL_SOURCE


DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "eodhd_phase1_symbol_comparison.csv"


def _frame_stats(prefix, frame):
    if frame is None or frame.empty:
        return {
            f"{prefix}FirstDate": None,
            f"{prefix}LastDate": None,
            f"{prefix}RowCount": 0,
        }
    return {
        f"{prefix}FirstDate": pd.Timestamp(frame.index.min()).date().isoformat(),
        f"{prefix}LastDate": pd.Timestamp(frame.index.max()).date().isoformat(),
        f"{prefix}RowCount": int(len(frame)),
    }


def _date_differences(eodhd, yahoo):
    if eodhd is None or eodhd.empty or yahoo is None or yahoo.empty:
        return {
            "YahooDatesMissingInEODHD": None,
            "EODHDDatesMissingInYahoo": None,
            "YahooDatesMissingInEODHDList": "[]",
            "EODHDDatesMissingInYahooList": "[]",
        }
    left = {pd.Timestamp(item).date() for item in eodhd.index}
    right = {pd.Timestamp(item).date() for item in yahoo.index}
    overlap_start = max(min(left), min(right))
    overlap_end = min(max(left), max(right))
    if overlap_start > overlap_end:
        missing_left, missing_right = sorted(right), sorted(left)
    else:
        left_overlap = {item for item in left if overlap_start <= item <= overlap_end}
        right_overlap = {item for item in right if overlap_start <= item <= overlap_end}
        missing_left = sorted(right_overlap - left_overlap)
        missing_right = sorted(left_overlap - right_overlap)
    return {
        "YahooDatesMissingInEODHD": len(missing_left),
        "EODHDDatesMissingInYahoo": len(missing_right),
        "YahooDatesMissingInEODHDList": json.dumps(
            [item.isoformat() for item in missing_left]
        ),
        "EODHDDatesMissingInYahooList": json.dumps(
            [item.isoformat() for item in missing_right]
        ),
    }


def _yahoo_cache():
    market_cfg = settings.get("market_data")
    return LocalCacheProvider(
        market_cfg.get("cache_path", "data/market_data_cache.sqlite"),
        source_provider="yahoo",
    )


def _save(rows, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output, index=False)


def run_audit(provider, output=DEFAULT_OUTPUT, pause=0.05, retry_failed=False):
    symbols = load_symbols(SYMBOL_SOURCE)
    cache = _yahoo_cache()
    existing = {}
    if retry_failed and output.is_file():
        prior = pd.read_csv(output).where(pd.notna, None)
        existing = {
            str(row["EngineSymbol"]): dict(row)
            for row in prior.to_dict(orient="records")
        }
    rows_by_symbol = dict(existing)
    actions_available = True
    entitlement_failure = None

    try:
        exchange_rows = provider.list_exchange_symbols("EGX")
        covered = {
            str(row.get("Code", row.get("code", ""))).strip().upper()
            for row in exchange_rows
        }
        lookup_status = "AVAILABLE"
    except ProviderError as error:
        covered = None
        lookup_status = f"UNAVAILABLE: {type(error).__name__}"

    for number, symbol in enumerate(symbols, start=1):
        if (
            retry_failed
            and symbol in existing
            and existing[symbol].get("EODHDStatus") == "AVAILABLE"
        ):
            continue
        row = {
            "EngineSymbol": symbol,
            "EODHDSymbol": to_eodhd_symbol(symbol),
            "ExchangeTimezone": "Africa/Cairo",
            "SymbolLookupStatus": lookup_status,
            "EODHDCovered": (
                None if covered is None
                else to_eodhd_symbol(symbol).split(".", 1)[0] in covered
            ),
            "EODHDStatus": "NOT_RUN",
            "EODHDError": None,
            "InvalidCandles": None,
            "DividendCount": None,
            "SplitCount": None,
            "CorporateActionsStatus": "NOT_RUN",
        }

        yahoo = None
        try:
            yahoo = cache.load_cached(
                "yahoo", symbol, "10y", "1d", allow_expired=True
            )
            row["YahooStatus"] = "AVAILABLE_CACHE"
        except ProviderError as error:
            row["YahooStatus"] = type(error).__name__
        row.update(_frame_stats("Yahoo", yahoo))

        eodhd = None
        if entitlement_failure is None:
            try:
                eodhd = provider.load_history(symbol, "max", "1d")
                row["EODHDStatus"] = "AVAILABLE"
                row["InvalidCandles"] = 0
            except ProviderConfigurationError as error:
                entitlement_failure = type(error).__name__
                row["EODHDStatus"] = "AUTH_OR_ENTITLEMENT_FAILED"
                row["EODHDError"] = str(error)
            except ProviderError as error:
                row["EODHDStatus"] = "UNAVAILABLE"
                row["EODHDError"] = str(error)
                if "invalid daily candles" in str(error):
                    row["InvalidCandles"] = "ONE_OR_MORE"
        else:
            row["EODHDStatus"] = "NOT_RUN_AFTER_ENTITLEMENT_FAILURE"
            row["EODHDError"] = entitlement_failure
        row.update(_frame_stats("EODHD", eodhd))
        row.update(_date_differences(eodhd, yahoo))

        if eodhd is not None and actions_available:
            try:
                actions = provider.corporate_actions(
                    symbol, start=row["EODHDFirstDate"], end=row["EODHDLastDate"]
                )
                row["DividendCount"] = len(actions["dividends"])
                row["SplitCount"] = len(actions["splits"])
                row["CorporateActionsStatus"] = "AVAILABLE"
            except ProviderConfigurationError:
                actions_available = False
                row["CorporateActionsStatus"] = "ENTITLEMENT_UNAVAILABLE"
            except ProviderError as error:
                row["CorporateActionsStatus"] = type(error).__name__
        elif not actions_available:
            row["CorporateActionsStatus"] = "ENTITLEMENT_UNAVAILABLE"

        rows_by_symbol[symbol] = row
        ordered_rows = [rows_by_symbol[item] for item in symbols if item in rows_by_symbol]
        _save(ordered_rows, output)
        if number % 10 == 0 or number == len(symbols):
            print(
                f"Audited {number}/{len(symbols)} symbols; "
                f"latest={symbol} status={row['EODHDStatus']}",
                flush=True,
            )
        if pause:
            time.sleep(max(0.0, float(pause)))
    return pd.DataFrame([rows_by_symbol[item] for item in symbols])


def main():
    parser = argparse.ArgumentParser(
        description="Compare EODHD EGX daily history with the frozen Yahoo cache"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pause", type=float, default=0.05)
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Keep successful rows and retry only unavailable EODHD histories",
    )
    parser.add_argument(
        "--prompt-api-key", action="store_true",
        help="Read the API key using a hidden interactive prompt",
    )
    args = parser.parse_args()
    token = getpass("EODHD API token: ") if args.prompt_api_key else None
    provider = EODHDProvider(api_token=token)
    if not provider.health()["configured"]:
        raise SystemExit(
            "Set EODHD_API_TOKEN/EODHD_API_KEY or use --prompt-api-key"
        )
    result = run_audit(
        provider, args.output, args.pause, retry_failed=args.retry_failed
    )
    available = int((result["EODHDStatus"] == "AVAILABLE").sum())
    print(f"Saved {len(result)} rows to {args.output}; EODHD available={available}")


if __name__ == "__main__":
    main()
