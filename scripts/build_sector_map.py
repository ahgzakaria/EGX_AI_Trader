"""Build data/sectors.csv from the EGX active-listing workbook.

The workbook is the security master: EGX official sector per ticker, mapped to
the 18-sector taxonomy, with ISIN and market capitalisation. This script only
transcribes it. Universe symbols the workbook does not classify stay unmapped
and are reported, never guessed -- decision_support.sector_analysis treats an
absent ticker as "Unknown" by design.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.symbols import load_symbols
from providers.symbol_mapping import to_egx_code, to_engine_symbol


DEFAULT_WORKBOOK = r"D:\EGX_00\EGX_Active_Stocks_By_Sector_Market_Cap.xlsx"
DEFAULT_OUTPUT = "data/sectors.csv"
DEFAULT_REPORT = "reports/sector_map_reconciliation.csv"
STOCK_SHEET = "All_Stocks"
EXCEPTION_SHEET = "Exceptions"


def read_workbook_sectors(workbook):
    """Return one row per classified equity, keyed by the engine ticker."""

    frame = pd.read_excel(workbook, sheet_name=STOCK_SHEET)
    missing = {"EGX Ticker", "Sector"} - set(frame.columns)
    if missing:
        raise ValueError(f"{STOCK_SHEET} is missing required columns: {sorted(missing)}")

    rows = []
    for _, row in frame.iterrows():
        code = str(row["EGX Ticker"]).strip().upper()
        sector = str(row["Sector"]).strip()
        if not code or code in {"NAN", "NONE"} or not sector or sector.lower() == "nan":
            continue
        market_cap = row.get("Market Cap (EGP)")
        rows.append({
            "Ticker": to_engine_symbol(code),
            "Sector": sector,
            "CompanyName": str(row.get("Company Name", "")).strip(),
            "ISIN": str(row.get("ISIN", "")).strip(),
            "MarketCapEGP": "" if pd.isna(market_cap) else int(market_cap),
        })

    mapped = pd.DataFrame(rows)
    duplicates = mapped["Ticker"][mapped["Ticker"].duplicated()].tolist()
    if duplicates:
        raise ValueError(f"Workbook lists a ticker more than once: {sorted(set(duplicates))}")
    return mapped.sort_values("Ticker").reset_index(drop=True)


def read_excluded_instruments(workbook):
    """Return EGX codes the workbook excluded as non-equity, with the reason."""

    try:
        frame = pd.read_excel(workbook, sheet_name=EXCEPTION_SHEET)
    except ValueError:
        return {}
    if "EGX Ticker" not in frame or "Issue Type" not in frame:
        return {}
    excluded = {}
    for _, row in frame.iterrows():
        issue = str(row.get("Issue Type", "")).strip()
        if not issue.lower().startswith("excluded"):
            continue
        excluded[to_egx_code(row["EGX Ticker"])] = issue
    return excluded


def reconcile(universe, mapped, excluded):
    """Return the universe symbols the workbook does not classify."""

    known = set(mapped["Ticker"])
    unmapped = []
    for symbol in universe:
        if symbol in known:
            continue
        code = to_egx_code(symbol)
        issue = excluded.get(code)
        unmapped.append({
            "Ticker": symbol,
            "EGXCode": code,
            "Reason": "NON_EQUITY_INSTRUMENT" if issue else "NOT_IN_ACTIVE_LISTING",
            "Detail": issue or "Absent from the workbook's active listed equities.",
        })
    return pd.DataFrame(unmapped)


def build(workbook, symbols_path, output_path, report_path):
    mapped = read_workbook_sectors(workbook)
    excluded = read_excluded_instruments(workbook)
    universe = load_symbols(symbols_path)
    unmapped = reconcile(universe, mapped, excluded)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    mapped.to_csv(output_path, index=False)
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    unmapped.to_csv(report_path, index=False)
    return mapped, unmapped, universe


def main():
    parser = argparse.ArgumentParser(description="Build the EGX sector map from the listing workbook.")
    parser.add_argument("--workbook", default=DEFAULT_WORKBOOK)
    parser.add_argument("--symbols", default="data/symbols.csv")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()

    mapped, unmapped, universe = build(args.workbook, args.symbols, args.output, args.report)
    covered = len(universe) - len(unmapped)
    print(f"sector map      : {len(mapped)} tickers across {mapped['Sector'].nunique()} sectors -> {args.output}")
    print(f"universe        : {covered}/{len(universe)} symbols classified ({covered / len(universe):.1%})")
    print(f"unmapped        : {len(unmapped)} -> {args.report}")
    if not unmapped.empty:
        for reason, count in unmapped["Reason"].value_counts().items():
            print(f"  {reason}: {count}")
    print()
    print(mapped["Sector"].value_counts().to_string())


if __name__ == "__main__":
    main()
