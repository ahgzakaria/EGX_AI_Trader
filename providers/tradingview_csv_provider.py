"""Phase E/F: TradingView manual-CSV-export provider.

Reads only user-exported CSV files (TradingView's official "Download chart
data..." feature — see docs/audits/providers/TRADINGVIEW_ACCESS_CAPABILITY_REPORT.md).
This module
never automates the TradingView website, never scrapes, and never talks to any
TradingView endpoint. It is a pure local-file reader + validator.

Conforms to the same MarketDataProvider / normalize_history contract as Yahoo
and Rubix (see docs/audits/providers/TRADINGVIEW_PROVIDER_ARCHITECTURE_AUDIT.md
section 1), so it can
be evaluated identically, but it is not wired into core/data_provider routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from core.egx_session import session_is_completed
from providers.base_provider import (
    MarketDataProvider,
    ProviderDataError,
    ProviderSchemaError,
    normalize_history,
)

REQUIRED_TV_COLUMNS_ANY = {
    "time": ("time", "date", "datetime"),
    "open": ("open",),
    "high": ("high",),
    "low": ("low",),
    "close": ("close",),
    "volume": ("volume", "vol"),
}


@dataclass(frozen=True)
class CsvImportOutcome:
    symbol: str
    source_filename: str
    imported_at: str
    total_rows: int
    valid_rows: int
    rejected_rows: int
    excluded_forming_rows: int
    frame: pd.DataFrame | None
    rejections: list


class TradingViewCsvProvider(MarketDataProvider):
    """Load, validate, and normalize a single user-exported TradingView CSV."""

    name = "tradingview_csv"
    # Compliance basis: docs/audits/providers/TRADINGVIEW_ACCESS_CAPABILITY_REPORT.md
    delayed = None
    delay_minutes = None

    def __init__(self, *, close_safety_minutes=15, holidays=(), now=None):
        self.close_safety_minutes = close_safety_minutes
        self.holidays = tuple(holidays or ())
        self._now = now or (lambda: datetime.now(timezone.utc))

    def load_history(self, symbol, period, interval):
        """MarketDataProvider entry point. ``period`` is ignored (CSV has no
        remote history depth to request — it is exactly what was exported)."""

        raise NotImplementedError(
            "TradingViewCsvProvider requires an explicit CSV path; call "
            "import_csv(path, symbol) instead of load_history()."
        )

    def import_csv(self, path, symbol) -> CsvImportOutcome:
        path = Path(path)
        if not path.is_file():
            raise ProviderDataError(f"TradingView CSV not found: {path}")

        raw = pd.read_csv(path)
        columns = _map_columns(raw.columns)
        missing = [c for c in REQUIRED_TV_COLUMNS_ANY if c not in columns]
        if missing:
            raise ProviderSchemaError(
                f"TradingView CSV missing required columns: {', '.join(missing)} "
                f"(found: {list(raw.columns)})"
            )

        frame = raw.rename(columns={v: k for k, v in columns.items()})
        frame["_trading_date"] = frame["time"].map(_parse_time)

        rejections = []
        valid_rows = []
        excluded_forming = 0
        now = self._now()

        for position, row in frame.iterrows():
            trading_date = row["_trading_date"]
            if trading_date is None:
                rejections.append({"row": int(position), "reason": "unparseable time/date value"})
                continue

            error = _validate_ohlcv_row(row)
            if error is not None:
                rejections.append({
                    "row": int(position), "trading_date": trading_date.isoformat(),
                    "reason": error,
                })
                continue

            # Never let a still-forming session enter analysis, regardless of
            # when the file happened to be exported.
            if not session_is_completed(
                trading_date, value=now,
                close_safety_minutes=self.close_safety_minutes,
                holidays=self.holidays,
            ):
                excluded_forming += 1
                continue

            valid_rows.append({
                "Date": trading_date,
                "Open": float(row["open"]), "High": float(row["high"]),
                "Low": float(row["low"]), "Close": float(row["close"]),
                "Volume": float(row["volume"]),
            })

        imported_at = datetime.now(timezone.utc).astimezone().isoformat()
        if not valid_rows:
            return CsvImportOutcome(
                symbol=symbol, source_filename=path.name, imported_at=imported_at,
                total_rows=len(frame), valid_rows=0, rejected_rows=len(rejections),
                excluded_forming_rows=excluded_forming, frame=None, rejections=rejections,
            )

        candidate = pd.DataFrame(valid_rows)
        # Never fabricate missing dates: duplicates on the same date collapse
        # to the last occurrence (matches normalize_history's own policy) and
        # no reindexing/backfill/ffill happens anywhere in this path.
        normalized = normalize_history(candidate, symbol, self.name)
        normalized.attrs["market_data"].update({
            "source_filename": path.name,
            "imported_at": imported_at,
            "import_method": "manual_csv_export",
            "rejected_rows": len(rejections),
            "excluded_forming_rows": excluded_forming,
        })
        return CsvImportOutcome(
            symbol=symbol, source_filename=path.name, imported_at=imported_at,
            total_rows=len(frame), valid_rows=len(normalized),
            rejected_rows=len(rejections), excluded_forming_rows=excluded_forming,
            frame=normalized, rejections=rejections,
        )


def _map_columns(raw_columns):
    """Return {canonical_name: actual_column_name} using flexible aliasing."""

    lowered = {str(c).strip().lower(): c for c in raw_columns}
    result = {}
    for canonical, aliases in REQUIRED_TV_COLUMNS_ANY.items():
        for alias in aliases:
            if alias in lowered:
                result[canonical] = lowered[alias]
                break
    return result


def _parse_time(value):
    """TradingView CSV `time` may be a Unix epoch (s) or an ISO date string."""

    try:
        numeric = float(value)
        unit = "ms" if numeric > 10_000_000_000 else "s"
        ts = pd.to_datetime(numeric, utc=True, unit=unit)
    except (TypeError, ValueError):
        try:
            ts = pd.to_datetime(value, utc=True)
        except (TypeError, ValueError):
            return None
    if ts is pd.NaT or pd.isna(ts):
        return None
    from core.egx_session import CAIRO

    return ts.tz_convert(CAIRO).date()


def _validate_ohlcv_row(row):
    try:
        o, h, l, c, v = (
            float(row["open"]), float(row["high"]), float(row["low"]),
            float(row["close"]), float(row["volume"]),
        )
    except (TypeError, ValueError):
        return "non-numeric OHLCV value"
    if any(pd.isna(x) for x in (o, h, l, c, v)):
        return "NaN OHLCV value"
    if not (o > 0 and h > 0 and l > 0 and c > 0):
        return "non-positive OHLC"
    if v < 0:
        return "negative volume"
    if not (h >= l and h >= o and h >= c and l <= o and l <= c):
        return "OHLC ordering violation"
    return None
