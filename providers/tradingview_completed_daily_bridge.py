"""Phase I: TradingView Completed-Daily-Candle Bridge (shadow-only merge).

Mirrors the append-only, never-overwrite, provenance-tracked contract already
proven by providers/rubix_completed_daily_bridge.py. Combines whatever
validated TradingView completed sessions exist (from user-supplied CSV exports
and/or accepted webhook deliveries) onto immutable Yahoo history.

Both upstream sources (TradingViewCsvProvider, TradingViewWebhookReceiver)
already reject invalid OHLCV and already exclude the current forming session
before data reaches this module. This module re-validates anyway (defense in
depth) and never repairs a bad candle -- it rejects it.

Not wired into core/data_provider. Production activation is a separate,
explicit, user-approved step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from providers.base_provider import normalize_history

YAHOO_DAILY = "YAHOO_DAILY"
TRADINGVIEW_CSV_DAILY = "TRADINGVIEW_CSV_DAILY"
TRADINGVIEW_WEBHOOK_DAILY = "TRADINGVIEW_WEBHOOK_DAILY"
TRADINGVIEW_REJECTED = "TRADINGVIEW_REJECTED"
CURRENT_SESSION_EXCLUDED = "CURRENT_SESSION_EXCLUDED"


@dataclass
class TVReconciliationRecord:
    symbol: str
    trading_date: str
    method: str
    open_diff_pct: float | None
    high_diff_pct: float | None
    low_diff_pct: float | None
    close_diff_pct: float | None
    volume_diff_pct: float | None
    yahoo: dict = field(default_factory=dict)
    tradingview: dict = field(default_factory=dict)


@dataclass
class TVBridgeResult:
    symbol: str
    frame: pd.DataFrame
    yahoo_latest_date: str | None
    merged_latest_date: str | None
    sessions_appended: int
    appended_dates: list
    method: str
    data_valid: bool
    rejection_reason: str | None
    provenance_rows: list
    reconciliations: list
    tradingview_available: bool


class TradingViewCompletedDailyBridge:
    """Append validated newer TradingView sessions onto Yahoo history, safely."""

    def merge(self, symbol, yahoo_frame: pd.DataFrame, tv_frame: pd.DataFrame | None,
              *, tv_method: str = "none") -> TVBridgeResult:
        if yahoo_frame is None or yahoo_frame.empty:
            return TVBridgeResult(
                symbol=symbol, frame=yahoo_frame, yahoo_latest_date=None,
                merged_latest_date=None, sessions_appended=0, appended_dates=[],
                method=tv_method, data_valid=False,
                rejection_reason="empty Yahoo history; bridge made no changes",
                provenance_rows=[], reconciliations=[], tradingview_available=False,
            )

        base = yahoo_frame.copy()
        base.attrs["market_data"] = dict(yahoo_frame.attrs.get("market_data", {}))
        yahoo_dates = {pd.Timestamp(idx).date() for idx in base.index}
        yahoo_latest = max(yahoo_dates)

        if tv_frame is None or tv_frame.empty:
            return TVBridgeResult(
                symbol=symbol, frame=base, yahoo_latest_date=yahoo_latest.isoformat(),
                merged_latest_date=yahoo_latest.isoformat(), sessions_appended=0,
                appended_dates=[], method=tv_method, data_valid=False,
                rejection_reason="no TradingView data available for this symbol",
                provenance_rows=[], reconciliations=[], tradingview_available=False,
            )

        provenance_rows = []
        appended_rows = []
        appended_dates = []
        rejection_reasons = []

        for idx, row in tv_frame.iterrows():
            trading_date = pd.Timestamp(idx).date()
            prov = {
                "symbol": symbol, "trading_date": trading_date.isoformat(),
                "method": tv_method,
            }
            if trading_date in yahoo_dates:
                # Never silently overwrite an existing Yahoo date -- this
                # session is handled by reconcile(), not merge().
                prov.update({"source": tv_method, "valid": False,
                             "reason": "date already present in Yahoo history (not appended)"})
                provenance_rows.append(prov)
                continue
            if trading_date <= yahoo_latest:
                prov.update({"source": tv_method, "valid": False,
                             "reason": "not newer than Yahoo's latest session"})
                provenance_rows.append(prov)
                continue

            failure = _validate_daily_row(row)
            if failure is not None:
                prov.update({"source": TRADINGVIEW_REJECTED, "valid": False, "reason": failure})
                provenance_rows.append(prov)
                rejection_reasons.append(f"{trading_date.isoformat()}: {failure}")
                continue

            appended_rows.append((trading_date, row))
            appended_dates.append(trading_date.isoformat())
            prov.update({"source": tv_method, "valid": True, "reason": None})
            provenance_rows.append(prov)

        merged = _append_rows(symbol, base, appended_rows)
        merged_latest = max(pd.Timestamp(i).date() for i in merged.index)

        return TVBridgeResult(
            symbol=symbol, frame=merged,
            yahoo_latest_date=yahoo_latest.isoformat(),
            merged_latest_date=merged_latest.isoformat(),
            sessions_appended=len(appended_rows),
            appended_dates=appended_dates,
            method=tv_method,
            data_valid=len(appended_rows) > 0,
            rejection_reason="; ".join(rejection_reasons) if rejection_reasons else None,
            provenance_rows=provenance_rows,
            reconciliations=[],
            tradingview_available=True,
        )

    def reconcile(self, symbol, yahoo_frame, tv_frame, *, tv_method="none"):
        if yahoo_frame is None or yahoo_frame.empty or tv_frame is None or tv_frame.empty:
            return []
        yahoo_by_date = {pd.Timestamp(i).date(): row for i, row in yahoo_frame.iterrows()}
        records = []
        for idx, tv_row in tv_frame.iterrows():
            trading_date = pd.Timestamp(idx).date()
            yahoo_row = yahoo_by_date.get(trading_date)
            if yahoo_row is None:
                continue
            records.append(TVReconciliationRecord(
                symbol=symbol, trading_date=trading_date.isoformat(), method=tv_method,
                open_diff_pct=_pct(tv_row["Open"], yahoo_row["Open"]),
                high_diff_pct=_pct(tv_row["High"], yahoo_row["High"]),
                low_diff_pct=_pct(tv_row["Low"], yahoo_row["Low"]),
                close_diff_pct=_pct(tv_row["Close"], yahoo_row["Close"]),
                volume_diff_pct=_pct(tv_row["Volume"], yahoo_row["Volume"]),
                yahoo={k: float(yahoo_row[k]) for k in ("Open", "High", "Low", "Close", "Volume")},
                tradingview={k: float(tv_row[k]) for k in ("Open", "High", "Low", "Close", "Volume")},
            ))
        return records


def _pct(tv_value, other_value):
    if other_value in (None, 0) or pd.isna(other_value):
        return None
    return round((float(tv_value) - float(other_value)) / float(other_value) * 100, 4)


def _validate_daily_row(row):
    try:
        o, h, l, c, v = (
            float(row["Open"]), float(row["High"]), float(row["Low"]),
            float(row["Close"]), float(row["Volume"]),
        )
    except (TypeError, ValueError, KeyError):
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


def _append_rows(symbol, base, appended_rows):
    if not appended_rows:
        return base
    has_adj = "Adj Close" in base.columns
    new_index, new_rows = [], []
    for trading_date, row in appended_rows:
        record = {
            "Open": float(row["Open"]), "High": float(row["High"]),
            "Low": float(row["Low"]), "Close": float(row["Close"]),
            "Volume": float(row["Volume"]),
        }
        if has_adj:
            record["Adj Close"] = record["Close"]
        new_index.append(pd.Timestamp(trading_date))
        new_rows.append(record)
    addition = pd.DataFrame(new_rows, index=pd.DatetimeIndex(new_index))
    addition = addition[[c for c in base.columns if c in addition.columns]]
    combined = pd.concat([base, addition])
    combined = combined[~combined.index.duplicated(keep="first")].sort_index()
    combined.index.name = base.index.name or "Date"
    normalized = normalize_history(
        combined.reset_index().rename(columns={combined.index.name or "index": "Date"}),
        symbol, "yahoo+tradingview_completed_daily",
    )
    metadata = dict(base.attrs.get("market_data", {}))
    metadata.update({
        "bridge_sessions_appended": len(appended_rows),
        "bridge_appended_dates": [d.isoformat() for d, _ in appended_rows],
        "latest_completed_candle": pd.Timestamp(
            max(d for d, _ in appended_rows)
        ).isoformat(),
    })
    normalized.attrs["market_data"] = metadata
    return normalized
