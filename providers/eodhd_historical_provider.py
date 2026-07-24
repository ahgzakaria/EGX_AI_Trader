"""EODHD daily historical provider — canonical, validated OHLCV (shadow-safe).

Returns the project's canonical daily frame with columns:
    Date, Open, High, Low, Close, Adj Close, Volume
with ascending unique dates, numeric validation, non-negative volume, the raw vs
adjusted close distinction preserved, and provenance metadata. It never fabricates a
missing row and never substitutes a zero price — invalid rows are dropped and counted,
not silently repaired.

This provider is built for SHADOW comparison. It does not change provider selection,
Yahoo, Rubix, strategy, scoring, thresholds, TP/SL, or execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from providers.eodhd_client import EODHDClient, EODHDError

CANONICAL_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]


@dataclass
class HistoryResult:
    base_symbol: str
    eodhd_symbol: str
    frame: pd.DataFrame
    provenance: dict = field(default_factory=dict)
    quality: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self):
        return self.error is None and not self.frame.empty


class EODHDHistoricalProvider:
    def __init__(self, client: EODHDClient | None = None, exchange="EGX"):
        self.client = client or EODHDClient()
        self.exchange = exchange

    def to_eodhd_symbol(self, base_symbol):
        base = str(base_symbol).strip().upper()
        base = base.split(".")[0]                   # strip any .CA / .EGX suffix
        return f"{base}.{self.exchange}"

    def load_history(self, base_symbol, *, from_date=None, to_date=None, force=False):
        eod_symbol = self.to_eodhd_symbol(base_symbol)
        try:
            raw = self.client.eod(eod_symbol, from_date=from_date, to_date=to_date,
                                  order="a", force=force)
        except EODHDError as error:
            return HistoryResult(str(base_symbol), eod_symbol, _empty(),
                                 provenance={"provider": "EODHD", "exchange": self.exchange,
                                             "eodhd_symbol": eod_symbol,
                                             "requested_from": str(from_date),
                                             "requested_to": str(to_date)},
                                 error=str(error))
        frame, quality = _canonicalize(raw)
        provenance = {
            "provider": "EODHD",
            "exchange": self.exchange,
            "eodhd_symbol": eod_symbol,
            "requested_from": str(from_date) if from_date else None,
            "requested_to": str(to_date) if to_date else None,
            "rows": int(len(frame)),
            "first_date": frame["Date"].iloc[0].isoformat() if not frame.empty else None,
            "last_date": frame["Date"].iloc[-1].isoformat() if not frame.empty else None,
            "adjusted_close_distinct": bool(quality.get("adjusted_close_distinct")),
        }
        return HistoryResult(str(base_symbol), eod_symbol, frame, provenance, quality)


def _empty():
    return pd.DataFrame(columns=CANONICAL_COLUMNS)


def _canonicalize(raw):
    """Validate + normalize EODHD eod rows into the canonical frame.

    Drops (never repairs) rows with non-numeric or non-positive OHLC; keeps zero/NaN
    volume as 0 only when it is genuinely reported; de-dupes dates (keep last); sorts
    ascending. Returns (frame, quality_dict).
    """
    quality = {"raw_rows": 0, "dropped_non_numeric": 0, "dropped_zero_or_negative": 0,
               "duplicate_dates": 0, "negative_volume_zeroed": 0,
               "adjusted_close_distinct": False}
    if not isinstance(raw, list) or not raw:
        return _empty(), quality
    quality["raw_rows"] = len(raw)
    rows = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        d = pd.to_datetime(r.get("date"), errors="coerce")
        o = pd.to_numeric(r.get("open"), errors="coerce")
        h = pd.to_numeric(r.get("high"), errors="coerce")
        low = pd.to_numeric(r.get("low"), errors="coerce")
        c = pd.to_numeric(r.get("close"), errors="coerce")
        adj = pd.to_numeric(r.get("adjusted_close"), errors="coerce")
        v = pd.to_numeric(r.get("volume"), errors="coerce")
        if pd.isna(d) or any(pd.isna(x) for x in (o, h, low, c)):
            quality["dropped_non_numeric"] += 1
            continue
        if min(o, h, low, c) <= 0:                  # never substitute a zero price
            quality["dropped_zero_or_negative"] += 1
            continue
        if pd.isna(adj):
            adj = c                                  # adj close falls back to raw close only
        if pd.isna(v) or v < 0:
            quality["negative_volume_zeroed"] += 1 if (not pd.isna(v) and v < 0) else 0
            v = 0
        rows.append({"Date": d.date(), "Open": float(o), "High": float(h),
                     "Low": float(low), "Close": float(c), "Adj Close": float(adj),
                     "Volume": float(v)})
    if not rows:
        return _empty(), quality
    frame = pd.DataFrame(rows)
    before = len(frame)
    frame = frame.drop_duplicates(subset="Date", keep="last")
    quality["duplicate_dates"] = before - len(frame)
    frame = frame.sort_values("Date").reset_index(drop=True)
    quality["adjusted_close_distinct"] = bool((frame["Adj Close"] != frame["Close"]).any())
    frame["Date"] = pd.to_datetime(frame["Date"]).dt.date
    return frame[CANONICAL_COLUMNS], quality
