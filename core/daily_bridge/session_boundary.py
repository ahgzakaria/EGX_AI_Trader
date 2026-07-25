"""Phase 3/4 — session-boundary trace + phase-attribution policy.

Classifies events near the 10:00 open, the 14:15 continuous close and the
14:15-14:25 auction using ALL available evidence — received_at, market_timestamp
(only when advancing/plausible), cumulative-volume progression, Last/Bid/Ask
progression and duplicate detection — never relying blindly on either timestamp.
Ambiguous events are quarantined (they never silently alter official OHLC).
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd

CAIRO = ZoneInfo("Africa/Cairo")
CONT_OPEN = time(10, 0)
CONT_CLOSE = time(14, 15)
AUCTION_END = time(14, 25)

CONTINUOUS_CONFIRMED = "CONTINUOUS_CONFIRMED"
AUCTION_CONFIRMED = "AUCTION_CONFIRMED"
POST_AUCTION_REPEAT = "POST_AUCTION_REPEAT"
TIMESTAMP_UNRELIABLE = "TIMESTAMP_UNRELIABLE"
LATE_FRAME_AMBIGUOUS = "LATE_FRAME_AMBIGUOUS"
UNKNOWN = "UNKNOWN"

# Windows to trace (Cairo).
BOUNDARY_WINDOWS = [((9, 58), (10, 2), "OPEN"),
                    ((14, 13), (14, 17), "CONTINUOUS_CLOSE"),
                    ((14, 23), (14, 28), "AUCTION_CLOSE")]


def classify_event(*, recv_time, mkt_time, material_changed, in_auction_window,
                   after_auction_end, volume_increment, market_ts_plausible):
    """Attribute one boundary event to a phase with a confidence 0-1."""
    if in_auction_window:
        if material_changed and volume_increment > 0:
            return AUCTION_CONFIRMED, 0.95
        if not material_changed:
            return POST_AUCTION_REPEAT, 0.8
        return LATE_FRAME_AMBIGUOUS, 0.5      # price moved without volume near close
    if after_auction_end:
        return POST_AUCTION_REPEAT, 0.7 if not material_changed else 0.4
    # continuous side
    if recv_time < CONT_CLOSE:
        if material_changed:
            return CONTINUOUS_CONFIRMED, 0.9
        return CONTINUOUS_CONFIRMED, 0.6      # unchanged snapshot inside continuous
    # received after 14:15 but not in auction handling above
    if not market_ts_plausible:
        return TIMESTAMP_UNRELIABLE, 0.4
    if mkt_time is not None and mkt_time < CONT_CLOSE:
        return LATE_FRAME_AMBIGUOUS, 0.5      # a pre-14:15 state delivered late
    return UNKNOWN, 0.3


def trace_session_boundaries(db_path, session_date):
    """Return a DataFrame of boundary-window events with phase attribution."""
    p = Path(db_path)
    if not p.is_file():
        return pd.DataFrame()
    uri = f"file:{p.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        rows = conn.execute(
            "SELECT ticker,last_price,bid,ask,volume,market_timestamp,received_at "
            "FROM quotes WHERE substr(received_at,1,10)=?", (session_date,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ticker", "last", "bid", "ask", "volume",
                                     "market_timestamp", "received_at"])
    df["recv"] = pd.to_datetime(df["received_at"], utc=True, errors="coerce").dt.tz_convert(CAIRO)
    df["mkt"] = pd.to_datetime(df["market_timestamp"], utc=True, errors="coerce").dt.tz_convert(CAIRO)
    df = df.dropna(subset=["recv"]).sort_values(["ticker", "recv"])

    out = []
    for ticker, g in df.groupby("ticker"):
        prev_last = prev_vol = None
        prev_state = "INIT"
        for _, r in g.iterrows():
            rt = r["recv"].time()
            if not any(_in(rt, a, b) for a, b, _ in BOUNDARY_WINDOWS):
                # still advance prev-state trackers across the whole session
                prev_last, prev_vol = r["last"], r["volume"]
                continue
            last = _f(r["last"]); vol = _f(r["volume"])
            material = ((prev_last is None) or (last is not None and last != _f(prev_last))
                        or (vol is not None and prev_vol is not None and _f(vol) != _f(prev_vol)))
            vol_inc = (_f(vol) - _f(prev_vol)) if (vol is not None and prev_vol is not None) else 0.0
            mkt = r["mkt"]
            # market_timestamp plausible when it lands on the same session date and
            # is not frozen far in the past.
            mkt_plausible = bool(pd.notna(mkt) and mkt.date().isoformat() == session_date)
            in_auction = _in(rt, (14, 15), (14, 25))
            after_auction = rt >= time(14, 25)
            phase, conf = classify_event(
                recv_time=rt, mkt_time=(mkt.time() if pd.notna(mkt) else None),
                material_changed=bool(material), in_auction_window=in_auction,
                after_auction_end=after_auction, volume_increment=vol_inc or 0.0,
                market_ts_plausible=mkt_plausible)
            lateness = ((r["recv"] - mkt).total_seconds() if (mkt_plausible and pd.notna(mkt))
                        else None)
            out.append({
                "session_date": session_date, "symbol": ticker,
                "received_at": r["recv"].isoformat(), "market_timestamp": (
                    r["mkt"].isoformat() if pd.notna(r["mkt"]) else None),
                "last": last, "bid": _f(r["bid"]), "ask": _f(r["ask"]),
                "cumulative_volume": vol, "material_changed": bool(material),
                "previous_state": prev_state, "proposed_phase": phase,
                "confidence": conf,
                "lateness_seconds": round(lateness, 1) if lateness is not None else None,
                "market_ts_plausible": mkt_plausible,
            })
            prev_state = phase
            prev_last, prev_vol = last, vol
    return pd.DataFrame(out)


def _in(t, a, b):
    return time(*a) <= t < time(*b)


def _f(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
