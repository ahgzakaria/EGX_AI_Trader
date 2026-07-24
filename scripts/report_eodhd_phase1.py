"""Summarize the persisted Phase 1 provider comparison without network use."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _counts(series):
    return {
        str(key): int(value)
        for key, value in series.value_counts(dropna=False).items()
    }


def summarize(path):
    data = pd.read_csv(path)
    available = data[data["EODHDStatus"] == "AVAILABLE"]
    missing_in_eodhd = pd.to_numeric(
        available["YahooDatesMissingInEODHD"], errors="coerce"
    ).fillna(0)
    missing_in_yahoo = pd.to_numeric(
        available["EODHDDatesMissingInYahoo"], errors="coerce"
    ).fillna(0)
    missing_dates = []
    for value in available["YahooDatesMissingInEODHDList"].fillna("[]"):
        try:
            missing_dates.extend(json.loads(value))
        except (TypeError, json.JSONDecodeError):
            continue
    missing_weekdays = _counts(
        pd.Series(pd.to_datetime(missing_dates, errors="coerce")).dropna().dt.day_name()
    ) if missing_dates else {}
    technical = data[
        data["EODHDError"].fillna("").str.contains(
            "HTTP|connection|authentication|entitlement|invalid", case=False
        )
    ]
    return {
        "symbols_audited": int(len(data)),
        "eodhd_status": _counts(data["EODHDStatus"]),
        "eodhd_covered": _counts(data["EODHDCovered"]),
        "yahoo_status": _counts(data["YahooStatus"]),
        "corporate_actions_status": _counts(data["CorporateActionsStatus"]),
        "invalid_candles": int(
            pd.to_numeric(data["InvalidCandles"], errors="coerce").fillna(0).sum()
        ),
        "available_first_date_min": (
            available["EODHDFirstDate"].dropna().min() if len(available) else None
        ),
        "available_last_date_max": (
            available["EODHDLastDate"].dropna().max() if len(available) else None
        ),
        "available_rows_min": int(available["EODHDRowCount"].min()) if len(available) else 0,
        "available_rows_median": float(available["EODHDRowCount"].median()) if len(available) else 0,
        "available_rows_max": int(available["EODHDRowCount"].max()) if len(available) else 0,
        "available_with_at_least_250_rows": int(
            (pd.to_numeric(available["EODHDRowCount"], errors="coerce") >= 250).sum()
        ),
        "symbols_with_yahoo_dates_missing_in_eodhd": int((missing_in_eodhd > 0).sum()),
        "yahoo_dates_missing_in_eodhd_total": int(missing_in_eodhd.sum()),
        "yahoo_dates_missing_in_eodhd_weekdays": missing_weekdays,
        "symbols_with_eodhd_dates_missing_in_yahoo": int((missing_in_yahoo > 0).sum()),
        "eodhd_dates_missing_in_yahoo_total": int(missing_in_yahoo.sum()),
        "dividend_events": int(
            pd.to_numeric(data["DividendCount"], errors="coerce").fillna(0).sum()
        ),
        "split_events": int(
            pd.to_numeric(data["SplitCount"], errors="coerce").fillna(0).sum()
        ),
        "technical_failures": int(len(technical)),
        "technical_failure_examples": technical[
            ["EngineSymbol", "EODHDError"]
        ].head(10).to_dict(orient="records"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path,
        default=Path("reports/eodhd_phase1_symbol_comparison.csv"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(args.input)
    text = json.dumps(result, indent=2, default=str)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
