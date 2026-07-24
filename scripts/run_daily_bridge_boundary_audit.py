"""Phase 3/5/8 — session-boundary trace, official-close verification, multi-session.

Records only; read-only. Produces the boundary trace, the per-symbol official-close
verification for the liquid test set, and a multi-session finalization summary.

    python scripts/run_daily_bridge_boundary_audit.py [--dates 2026-07-21,2026-07-22]
"""

from __future__ import annotations

import argparse
import csv
from datetime import time
import json
import os
from pathlib import Path
import sqlite3
import sys
import warnings
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import pandas as pd  # noqa: E402

from core.daily_bridge.rubix_daily_builder import RubixDailyBuilder  # noqa: E402
from core.daily_bridge.session_boundary import trace_session_boundaries  # noqa: E402

CAIRO = ZoneInfo("Africa/Cairo")
REPORT_DIR = Path("reports/daily_bridge")
TEST_SYMBOLS = ["COMI", "SWDY", "TMGH", "FWRY", "ADIB", "ABUK", "ARAB", "PRDC"]


def _write(path, fields, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _official_close_detail(db_path, session_date, tickers):
    uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        df = pd.read_sql_query(
            "SELECT ticker,last_price,bid,ask,volume,market_timestamp,received_at "
            "FROM quotes WHERE substr(received_at,1,10)=?", conn, params=(session_date,))
    finally:
        conn.close()
    df["recv"] = pd.to_datetime(df["received_at"], utc=True, errors="coerce").dt.tz_convert(CAIRO)
    df["last"] = pd.to_numeric(df["last_price"], errors="coerce")
    df["vol"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna(subset=["recv"])
    rows = []
    for t in tickers:
        s = df[(df["ticker"].str.upper() == t) & (df["last"] > 0)].sort_values("recv")
        tod = s["recv"].dt.time
        cont = s[(tod >= time(10, 0)) & (tod < time(14, 15))]
        au = s[(tod >= time(14, 15)) & (tod < time(14, 25))]
        if cont.empty:
            continue
        cc = float(cont["last"].iloc[-1]); cvol = float(cont["vol"].max())
        rec = {"session_date": session_date, "symbol": t, "continuous_close": round(cc, 4),
               "continuous_volume": cvol}
        if au.empty:
            rec.update({"auction_status": "AUCTION_MISSING", "official_close": None,
                        "selection_reason": "no auction events — official close unconfirmed"})
            rows.append(rec); continue
        au = au.sort_values("recv")
        first_au = float(au["last"].iloc[0]); last_au = float(au["last"].iloc[-1])
        avol = float(au["vol"].max()); vol_inc = max(0.0, avol - cvol)
        # last time a material value changed in the auction window
        changed = au[(au["last"].diff().fillna(1) != 0) | (au["vol"].diff().fillna(1) != 0)]
        final_material_ts = changed["recv"].iloc[-1].isoformat() if not changed.empty else None
        last_snapshot_ts = au["recv"].iloc[-1].isoformat()
        confirmed = vol_inc > 0 or abs(last_au - cc) / cc * 100 >= 0.05
        rec.update({
            "auction_status": "AUCTION_CONFIRMED" if confirmed else "POST_AUCTION_REPEAT",
            "first_auction_last": round(first_au, 4), "final_auction_last": round(last_au, 4),
            "final_auction_volume": avol, "auction_volume_increment": round(vol_inc, 1),
            "final_material_auction_ts": final_material_ts,
            "last_received_snapshot_ts": last_snapshot_ts,
            "official_close": round(last_au, 4) if confirmed else None,
            "selection_reason": (
                f"genuine auction (vol +{vol_inc:.0f}) → official close = final auction Last"
                if confirmed else "auction window shows repeats only — unconfirmed"),
        })
        rows.append(rec)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default="2026-07-21,2026-07-22")
    args = ap.parse_args(argv)
    from config.settings_manager import settings
    db_path = settings.get("market_data").get("rubix_db_path", "data/rubix_live_market.db")
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]

    # Phase 3 — boundary trace (all dates).
    trace_rows = []
    for d in dates:
        t = trace_session_boundaries(db_path, d)
        if not t.empty:
            trace_rows.extend(t.to_dict("records"))
    if trace_rows:
        _write(REPORT_DIR / "session_boundary_trace.csv", list(trace_rows[0].keys()), trace_rows)

    # Phase 5 — official-close verification (latest date, test symbols).
    detail = _official_close_detail(db_path, dates[-1], TEST_SYMBOLS)
    _write(REPORT_DIR / "official_close_verification.csv",
           ["session_date", "symbol", "continuous_close", "continuous_volume", "auction_status",
            "first_auction_last", "final_auction_last", "final_auction_volume",
            "auction_volume_increment", "final_material_auction_ts", "last_received_snapshot_ts",
            "official_close", "selection_reason"], detail)

    # Phase 8 — multi-session finalization summary.
    from collections import Counter
    multi = []
    builder = RubixDailyBuilder(db_path)
    for d in dates:
        bars = builder.build_session(d)
        cbs = Counter(b.continuous_bar_status for b in bars)
        obs = Counter(b.official_bar_status for b in bars)
        aus = Counter(b.auction_status for b in bars)
        boundary = [r for r in trace_rows if r["session_date"] == d]
        multi.append({
            "session_date": d, "symbols": len(bars),
            "final_continuous": cbs.get("FINAL_CONTINUOUS", 0),
            "final_official": obs.get("FINAL_OFFICIAL", 0),
            "auction_missing": aus.get("AUCTION_MISSING", 0),
            "auction_confirmed": aus.get("AUCTION_CONFIRMED", 0),
            "post_auction_repeat": aus.get("POST_AUCTION_REPEAT", 0),
            "expected_range_eligible": sum(1 for b in bars if b.expected_range_eligible),
            "swing_daily_eligible": sum(1 for b in bars if b.swing_daily_eligible),
            "boundary_events_traced": len(boundary),
            "late_frame_ambiguous": sum(1 for r in boundary if r["proposed_phase"] == "LATE_FRAME_AMBIGUOUS"),
            "post_auction_repeat_events": sum(1 for r in boundary if r["proposed_phase"] == "POST_AUCTION_REPEAT"),
            "official_close_confirmation_rate": round(
                obs.get("FINAL_OFFICIAL", 0) / max(1, cbs.get("FINAL_CONTINUOUS", 0)), 4),
        })
    _write(REPORT_DIR / "multi_session_finalization.csv", list(multi[0].keys()), multi)

    print(json.dumps({"dates": dates, "boundary_events": len(trace_rows),
                      "official_close_detail": detail, "multi_session": multi}, indent=2, default=str))
    return {"multi_session": multi, "detail": detail}


if __name__ == "__main__":
    main()
