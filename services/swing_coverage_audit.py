"""Coverage accounting for Swing/Daily scans; no trading decisions live here."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


COVERAGE_COLUMNS = [
    "symbol", "historical_provider", "historical_row_count",
    "minimum_date", "maximum_date", "required_indicator_lookback",
    "rubix_quote_available", "rubix_minute_bars_available",
    "rubix_quote_count", "rubix_minute_bar_count", "rubix_quote_timestamp",
    "rubix_status", "accepted_into_swing_scan", "rejection_category",
    "rejection_reason", "final_status",
]


class ScanResults(list):
    """List-compatible scan output carrying non-strategy coverage evidence.

    ``status`` distinguishes a finished scan from one the operator stopped. A
    ``CANCELLED`` result carries partial diagnostic rows and is never finalized as a
    completed immutable forward session.
    """

    def __init__(self, values=(), *, coverage=None, failures=None, status="COMPLETED"):
        super().__init__(values)
        self.coverage = list(coverage or [])
        self.failures = list(failures or [])
        self.status = str(status)

    @property
    def cancelled(self) -> bool:
        return self.status == "CANCELLED"


def successful_coverage_row(symbol, frame, final_status, required_lookback):
    """Describe an analyzed symbol without recalculating its decision."""

    metadata = dict(frame.attrs.get("market_data", {}))
    return {
        "symbol": symbol,
        "historical_provider": metadata.get("historical_provider")
        or metadata.get("provider") or "unknown",
        "historical_row_count": len(frame),
        "minimum_date": _date(frame.index.min()),
        "maximum_date": _date(frame.index.max()),
        "required_indicator_lookback": int(required_lookback),
        "rubix_quote_available": bool(metadata.get("live_quote_available")),
        "rubix_minute_bars_available": bool(
            metadata.get("rubix_minute_bars_available")
        ),
        "rubix_quote_count": int(metadata.get("rubix_quote_count") or 0),
        "rubix_minute_bar_count": int(
            metadata.get("rubix_minute_bar_count") or 0
        ),
        "rubix_quote_timestamp": metadata.get("live_quote_timestamp"),
        "rubix_status": metadata.get("live_quote_status"),
        "accepted_into_swing_scan": True,
        "rejection_category": "",
        "rejection_reason": "",
        "final_status": final_status,
    }


def failed_coverage_row(symbol, evidence, error, required_lookback):
    """Classify a data/evaluation failure instead of disguising it as AVOID."""

    category, status = classify_failure(error)
    return {
        "symbol": symbol,
        "historical_provider": "local_cache:yahoo"
        if evidence.get("available") else "yahoo_unavailable",
        "historical_row_count": int(evidence.get("row_count") or 0),
        "minimum_date": evidence.get("minimum_date"),
        "maximum_date": evidence.get("maximum_date"),
        "required_indicator_lookback": int(required_lookback),
        "rubix_quote_available": bool(evidence.get("rubix_quote_available")),
        "rubix_minute_bars_available": bool(
            evidence.get("rubix_minute_bars_available")
        ),
        "rubix_quote_count": int(evidence.get("rubix_quote_count") or 0),
        "rubix_minute_bar_count": int(
            evidence.get("rubix_minute_bar_count") or 0
        ),
        "rubix_quote_timestamp": evidence.get("rubix_quote_timestamp"),
        "rubix_status": evidence.get("rubix_status"),
        "accepted_into_swing_scan": False,
        "rejection_category": category,
        "rejection_reason": str(error),
        "final_status": status,
    }


def classify_failure(error):
    text = str(error or "").lower()
    if "not enough history" in text:
        return "insufficient lookback", "DATA_INSUFFICIENT"
    if any(term in text for term in (
        "no data found", "cache miss", "contains no candles", "no price data"
    )):
        return "missing historical data", "DATA_INSUFFICIENT"
    if "missing columns" in text or "required columns" in text:
        return "missing required columns", "PROVIDER_FAILED"
    if "schema" in text:
        return "invalid schema", "PROVIDER_FAILED"
    if "mapping" in text or "unknown symbol" in text:
        return "symbol mapping failure", "PROVIDER_FAILED"
    if "stale" in text:
        return "stale data", "PROVIDER_FAILED"
    if "nan" in text or "division by zero" in text:
        return "indicator NaN", "NOT_ANALYZED"
    if any(term in text for term in (
        "provider", "connection", "timeout", "download", "curl", "yahoo"
    )):
        return "provider exception", "PROVIDER_FAILED"
    return "unknown failure", "NOT_ANALYZED"


def write_coverage_report(rows, path="reports/swing_symbol_coverage_audit.csv"):
    """Write the latest operational audit while run folders remain immutable."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=COVERAGE_COLUMNS)
    frame.to_csv(destination, index=False, encoding="utf-8-sig")
    return frame


def _date(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat()
