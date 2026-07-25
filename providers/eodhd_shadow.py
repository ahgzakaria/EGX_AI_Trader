"""EODHD × Yahoo shadow comparison (read-only, decision-neutral).

Fetches the same daily history from EODHD (separately) and Yahoo (the active provider),
aligns them on EGX trading sessions, and reports OHLC / adjusted-close / volume
differences, missing/extra sessions, freshness, and corporate-action discontinuities.
It never feeds strategy decisions and never switches the active provider.
"""

from __future__ import annotations

import pandas as pd

from providers.eodhd_historical_provider import CANONICAL_COLUMNS, EODHDHistoricalProvider
from providers.yahoo_provider import YahooProvider

_ABS = 1e-9


def _yahoo_canonical(base_symbol, period="1y", downloader=None):
    """Canonical [Date, OHLC, Adj Close, Volume] from Yahoo for an EGX base ticker."""
    prov = YahooProvider(downloader=downloader) if downloader else YahooProvider()
    frame = prov.load_history(f"{_base(base_symbol)}.CA", period=period, interval="1d")
    if frame is None or frame.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    df = frame.reset_index()
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    out = pd.DataFrame({
        "Date": pd.to_datetime(df[date_col]).dt.date,
        "Open": pd.to_numeric(df.get("Open"), errors="coerce"),
        "High": pd.to_numeric(df.get("High"), errors="coerce"),
        "Low": pd.to_numeric(df.get("Low"), errors="coerce"),
        "Close": pd.to_numeric(df.get("Close"), errors="coerce"),
        "Adj Close": pd.to_numeric(df.get("Adj Close", df.get("Close")), errors="coerce"),
        "Volume": pd.to_numeric(df.get("Volume"), errors="coerce").fillna(0),
    }).dropna(subset=["Open", "High", "Low", "Close"])
    return out.drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True)


def _base(symbol):
    return str(symbol).strip().upper().split(".")[0]


def compare(base_symbol, *, window, eodhd_from=None, yahoo_period="1y", eodhd_client=None,
            yahoo_downloader=None, holidays=None):
    """Compare one symbol/window. Returns (aligned_frame, summary)."""
    prov = EODHDHistoricalProvider(eodhd_client)
    e_res = prov.load_history(base_symbol, from_date=eodhd_from)
    e = e_res.frame.copy()
    y = _yahoo_canonical(base_symbol, period=yahoo_period, downloader=yahoo_downloader)

    e_dates = set(e["Date"]) if not e.empty else set()
    y_dates = set(y["Date"]) if not y.empty else set()
    common = sorted(e_dates & y_dates)
    only_e = sorted(e_dates - y_dates)
    only_y = sorted(y_dates - e_dates)

    em = e.set_index("Date") if not e.empty else e
    ym = y.set_index("Date") if not y.empty else y
    rows = []
    for d in common:
        er, yr = em.loc[d], ym.loc[d]
        rows.append({
            "Date": d.isoformat(),
            "eodhd_Close": er["Close"], "yahoo_Close": yr["Close"],
            "close_abs_diff": abs(er["Close"] - yr["Close"]),
            "close_pct_diff": _pct(er["Close"], yr["Close"]),
            "open_abs_diff": abs(er["Open"] - yr["Open"]),
            "high_abs_diff": abs(er["High"] - yr["High"]),
            "low_abs_diff": abs(er["Low"] - yr["Low"]),
            "adj_close_abs_diff": abs(er["Adj Close"] - yr["Adj Close"]),
            "eodhd_Volume": er["Volume"], "yahoo_Volume": yr["Volume"],
            "volume_abs_diff": abs(er["Volume"] - yr["Volume"]),
            "volume_pct_diff": _pct(er["Volume"], yr["Volume"]),
            # corporate-action discontinuity: adj/close ratio should match if adjustments agree
            "adj_ratio_gap": abs(_ratio(er["Adj Close"], er["Close"])
                                 - _ratio(yr["Adj Close"], yr["Close"])),
        })
    aligned = pd.DataFrame(rows)
    summary = {
        "symbol": _base(base_symbol), "window": window,
        "eodhd_symbol": e_res.eodhd_symbol, "yahoo_symbol": f"{_base(base_symbol)}.CA",
        "eodhd_rows": int(len(e)), "yahoo_rows": int(len(y)), "common_sessions": len(common),
        "only_in_eodhd": [d.isoformat() for d in only_e],
        "only_in_yahoo": [d.isoformat() for d in only_y],
        "eodhd_latest": e["Date"].iloc[-1].isoformat() if not e.empty else None,
        "yahoo_latest": y["Date"].iloc[-1].isoformat() if not y.empty else None,
        "max_close_abs_diff": _max(aligned, "close_abs_diff"),
        "mean_close_pct_diff": _mean(aligned, "close_pct_diff"),
        "max_close_pct_diff": _max(aligned, "close_pct_diff"),
        "max_adj_close_abs_diff": _max(aligned, "adj_close_abs_diff"),
        "max_ohlc_abs_diff": max(_max(aligned, c) for c in
                                 ("open_abs_diff", "high_abs_diff", "low_abs_diff", "close_abs_diff"))
                             if not aligned.empty else 0.0,
        "max_volume_abs_diff": _max(aligned, "volume_abs_diff"),
        "mean_volume_pct_diff": _mean(aligned, "volume_pct_diff"),
        "max_adj_ratio_gap": _max(aligned, "adj_ratio_gap"),
        "eodhd_error": e_res.error,
    }
    return aligned, summary


def _pct(a, b):
    denom = abs(b) if abs(b) > _ABS else _ABS
    return abs(a - b) / denom * 100.0


def _ratio(a, b):
    return a / b if abs(b) > _ABS else 1.0


def _max(frame, col):
    return float(frame[col].max()) if (not frame.empty and col in frame) else 0.0


def _mean(frame, col):
    return round(float(frame[col].mean()), 6) if (not frame.empty and col in frame) else 0.0
