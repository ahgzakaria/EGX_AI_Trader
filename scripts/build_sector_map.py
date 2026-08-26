"""Build data/sectors.csv from the EGX active-listing workbook.

The workbook is the sector authority: EGX's official sector per company, mapped
to the 18-sector taxonomy. The operational universe (core.universe) is the
symbol authority. This script joins the two and transcribes the result; it never
invents a classification.

The join is on **ISIN first, ticker second**. ISIN is the stable identifier --
EGX tickers are reused and renamed -- but neither key alone is sufficient here:
ISIN matches 226 of the 241 active symbols and ticker matches 220, while the two
together reach 232. Which key produced each row is recorded in ``MatchedBy`` so
a ticker-only match can be reviewed.

Symbols the workbook does not classify stay unmapped and are reported.
``decision_support.sector_analysis`` treats an absent ticker as "Unknown" by
design, and that contract is preserved.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.universe import UNIVERSE_SOURCE, active_universe


DEFAULT_WORKBOOK = r"D:\EGX_00\EGX_Active_Stocks_By_Sector_Market_Cap.xlsx"
DEFAULT_OUTPUT = "data/sectors.csv"
DEFAULT_REPORT = "reports/sector_map_reconciliation.csv"
STOCK_SHEET = "All_Stocks"
EXCEPTION_SHEET = "Exceptions"


def _key(value):
    return str(value).strip().upper()


def read_workbook(workbook):
    """Return the workbook's classified equities indexed by ISIN and by ticker."""

    frame = pd.read_excel(workbook, sheet_name=STOCK_SHEET)
    missing = {"EGX Ticker", "Sector"} - set(frame.columns)
    if missing:
        raise ValueError(f"{STOCK_SHEET} is missing required columns: {sorted(missing)}")

    by_isin, by_code = {}, {}
    for _, row in frame.iterrows():
        sector = str(row["Sector"]).strip()
        code = _key(row["EGX Ticker"])
        if not sector or sector.lower() == "nan" or not code or code == "NAN":
            continue
        market_cap = row.get("Market Cap (EGP)")
        record = {
            "Sector": sector,
            "MarketCapEGP": "" if pd.isna(market_cap) else int(market_cap),
        }
        isin = _key(row.get("ISIN", ""))
        if isin and isin != "NAN":
            by_isin[isin] = record
        by_code[code] = record
    return by_isin, by_code


def read_excluded_instruments(workbook):
    """Return EGX codes the workbook excluded as non-equity, with the reason."""

    try:
        frame = pd.read_excel(workbook, sheet_name=EXCEPTION_SHEET)
    except ValueError:
        return {}
    if "EGX Ticker" not in frame or "Issue Type" not in frame:
        return {}
    return {
        _key(row["EGX Ticker"]).split("_")[0]: str(row["Issue Type"]).strip()
        for _, row in frame.iterrows()
        if str(row.get("Issue Type", "")).strip().lower().startswith("excluded")
    }


def deduplicate_by_isin(universe):
    """Collapse renamed companies that appear twice under one ISIN.

    Four EGX companies are listed in the universe under both their old and new
    tickers -- Pioneers/Aspire, Sarwa/Contact, Arabian Metal/Arab Valves and
    Lift Slab/Creast Mark -- sharing one ISIN each. Classifying both would put
    a single company's turnover into its sector twice the moment the retired
    ticker starts resolving. The row the live feed has actually observed wins;
    with nothing to separate them, the first is kept so the result is stable.
    """

    chosen, dropped = {}, []
    for record in universe:
        isin = _key(record.isin)
        if not isin or isin == "NAN":
            chosen[f"__no_isin__{record.canonical_symbol}"] = record
            continue
        held = chosen.get(isin)
        if held is None:
            chosen[isin] = record
            continue
        observed = str(record.rubix_mapping_status or "").startswith("VERIFIED")
        held_observed = str(held.rubix_mapping_status or "").startswith("VERIFIED")
        if observed and not held_observed:
            chosen[isin] = record
            dropped.append(held)
        else:
            dropped.append(record)
    return list(chosen.values()), dropped


def build_rows(universe, by_isin, by_code, excluded):
    """Return the mapped rows and the unmapped reconciliation rows."""

    mapped, unmapped = [], []
    for record in universe:
        isin = _key(record.isin)
        code = _key(record.canonical_symbol)
        # ISIN is the stable identifier, so it decides when both keys match.
        match, matched_by = by_isin.get(isin), "ISIN"
        if match is None:
            match, matched_by = by_code.get(code), "TICKER"

        if match is None:
            issue = excluded.get(code)
            unmapped.append({
                "Ticker": record.engine_symbol,
                "EGXCode": code,
                "ISIN": record.isin,
                "CompanyName": record.company_name,
                "Reason": "NON_EQUITY_INSTRUMENT" if issue else "NOT_IN_WORKBOOK",
                "Detail": issue or "No ISIN or ticker match in the listing workbook.",
            })
            continue

        mapped.append({
            "Ticker": record.engine_symbol,
            "Sector": match["Sector"],
            "CompanyName": record.company_name,
            "ISIN": record.isin,
            "MarketCapEGP": match["MarketCapEGP"],
            "MatchedBy": matched_by,
        })

    return (
        pd.DataFrame(mapped).sort_values("Ticker").reset_index(drop=True),
        pd.DataFrame(unmapped).sort_values("Ticker").reset_index(drop=True)
        if unmapped else pd.DataFrame(),
    )


def build(workbook, output_path, report_path, universe_path=None):
    by_isin, by_code = read_workbook(workbook)
    excluded = read_excluded_instruments(workbook)
    universe = active_universe(universe_path)
    deduplicated, superseded = deduplicate_by_isin(universe)
    mapped, unmapped = build_rows(deduplicated, by_isin, by_code, excluded)
    if superseded:
        rows = pd.DataFrame([{
            "Ticker": record.engine_symbol,
            "EGXCode": record.canonical_symbol,
            "ISIN": record.isin,
            "CompanyName": record.company_name,
            "Reason": "SUPERSEDED_BY_ISIN_TWIN",
            "Detail": "Another active symbol carries the same ISIN and is feed-observed.",
        } for record in superseded])
        unmapped = pd.concat([unmapped, rows], ignore_index=True) if not unmapped.empty else rows
        unmapped = unmapped.sort_values("Ticker").reset_index(drop=True)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    mapped.to_csv(output_path, index=False)
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    unmapped.to_csv(report_path, index=False)
    return mapped, unmapped, universe


def main():
    parser = argparse.ArgumentParser(description="Build the EGX sector map from the listing workbook.")
    parser.add_argument("--workbook", default=DEFAULT_WORKBOOK)
    parser.add_argument("--universe", default=None, help=f"Defaults to {UNIVERSE_SOURCE}")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()

    mapped, unmapped, universe = build(args.workbook, args.output, args.report, args.universe)
    print(f"universe        : {len(universe)} active symbols ({args.universe or UNIVERSE_SOURCE})")
    print(f"sector map      : {len(mapped)} classified across {mapped['Sector'].nunique()} sectors"
          f" -> {args.output}")
    print(f"coverage        : {len(mapped) / len(universe):.1%}")
    print(f"matched by      : {mapped['MatchedBy'].value_counts().to_dict()}")
    print(f"unmapped        : {len(unmapped)} -> {args.report}")
    if not unmapped.empty:
        for reason, count in unmapped["Reason"].value_counts().items():
            print(f"  {reason}: {count}")
        print(f"  {', '.join(unmapped['EGXCode'])}")
    print()
    print(mapped["Sector"].value_counts().to_string())


if __name__ == "__main__":
    raise SystemExit(main())
