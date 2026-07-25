"""Adapt an EODHD-normalized daily frame into the exact ``load_history`` contract.

Produces a DataFrame byte-for-byte compatible with what the backtest engine already
consumes — DatetimeIndex named 'Date' (tz-naive), float64 [Open, High, Low, Close,
Adj Close, Volume] in that order, no NaN, and an ``attrs['market_data']`` provenance
dict — WITHOUT modifying the frozen engine. Used only by the shadow backtest-comparison
harness; it never switches the active provider.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

CONTRACT_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
CAIRO = ZoneInfo("Africa/Cairo")


def to_load_history_frame(frame, *, symbol, price_series="SPLIT_ADJUSTED",
                          adj_close=None, purpose="backtest", provider="eodhd"):
    """Return a contract-compliant frame from an OHLCV frame with a Date column.

    ``frame`` needs Date, Open, High, Low, Close, Volume (a split-adjusted EODHD frame).
    ``adj_close`` optionally supplies the Adj Close column (else = Close).
    """
    df = frame.copy()
    idx = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    out = pd.DataFrame({
        "Open": pd.to_numeric(df["Open"], errors="coerce").astype("float64"),
        "High": pd.to_numeric(df["High"], errors="coerce").astype("float64"),
        "Low": pd.to_numeric(df["Low"], errors="coerce").astype("float64"),
        "Close": pd.to_numeric(df["Close"], errors="coerce").astype("float64"),
        "Adj Close": pd.to_numeric(adj_close if adj_close is not None else df["Close"],
                                   errors="coerce").astype("float64"),
        "Volume": pd.to_numeric(df["Volume"], errors="coerce").fillna(0).astype("float64"),
    })
    out.index = pd.DatetimeIndex(idx.values)
    out.index.name = "Date"
    out = out[CONTRACT_COLUMNS].dropna().sort_index()
    out = out[~out.index.duplicated(keep="last")]
    now = datetime.now(timezone.utc).astimezone(CAIRO).isoformat()
    out.attrs["market_data"] = {
        "provider": provider, "received_timestamp": now, "cache_hit": "True",
        "cache_fetched_at": now, "requested_provider": provider,
        "effective_provider": provider, "purpose": purpose, "fallback_active": "False",
        "fallback_reason": "None", "delayed": "True", "delay_minutes": "None",
        "provider_status": "available", "database_status": "None",
        "requested_provider_status": "None", "comparison_latest_timestamp": "None",
        "latest_exchange_timestamp": (out.index[-1].isoformat() if len(out) else "None"),
        "session_phase": "SHADOW", "session_lag": "0", "freshness_status": "SHADOW",
        "price_series": price_series, "symbol": str(symbol),
    }
    return out


def matches_contract(frame, contract):
    """Assert a frame satisfies the recorded contract; returns (ok, problems)."""
    problems = []
    if type(frame).__name__ != contract["type"]:
        problems.append("type")
    if type(frame.index).__name__ != contract["index"]["type"]:
        problems.append("index_type")
    if frame.index.name != contract["index"]["name"]:
        problems.append("index_name")
    if getattr(frame.index, "tz", None) is not None:
        problems.append("index_tz_not_none")
    if list(frame.columns) != contract["columns_order"]:
        problems.append("columns_order")
    for col, dt in contract["dtypes"].items():
        if col in frame.columns and str(frame[col].dtype) != dt:
            problems.append(f"dtype:{col}")
    if contract.get("no_nan") and int(frame.isna().sum().sum()) != 0:
        problems.append("has_nan")
    md = frame.attrs.get(contract["attrs_key"])
    if not isinstance(md, dict):
        problems.append("attrs_missing")
    else:
        for f in contract["attrs_required_fields"]:
            if f not in md:
                problems.append(f"attrs:{f}")
    return (not problems, problems)
