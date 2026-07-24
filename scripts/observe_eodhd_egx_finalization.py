"""Phase 3/4 — EODHD EGX daily finalization observer (run live, after the auction).

Polls EODHD for today's EOD bar for a basket of liquid symbols, starting ~14:30
Cairo, at a rate-limit-respecting interval, and appends immutable observation rows
(one per symbol+poll) with a response hash — so FIRST_VISIBLE_TIME,
FIRST_COMPLETE_OHLCV_TIME, revisions and STABLE_FINAL_TIME can be measured from
REAL data. Restart-safe (dedups poll rows by symbol+poll-timestamp+response-hash),
exposes no credentials, and refuses to run (recording API_KEY_MISSING) when no key
is configured — it never fabricates a bar.

    python scripts/observe_eodhd_egx_finalization.py [--date YYYY-MM-DD]
        [--interval-seconds 300] [--max-polls N] [--once]
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import warnings

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.egx_session import cairo_now  # noqa: E402
from core.providers.eodhd_daily import API_KEY_MISSING, OK, EodhdDailyClient  # noqa: E402

OBS_CSV = "reports/eodhd_finalization_observations.csv"
BASKET = ["COMI.CA", "SWDY.CA", "TMGH.CA", "FWRY.CA", "ADIB.CA", "ABUK.CA", "ARAB.CA", "PRDC.CA"]
FIELDS = ["poll_timestamp_cairo", "requested_session_date", "engine_symbol", "eodhd_symbol",
          "bar_exists", "open", "high", "low", "close", "adjusted_close", "volume",
          "ohlcv_complete", "status", "http_status", "response_hash", "detail"]


def _existing_keys(path):
    p = Path(path)
    if not p.is_file():
        return set()
    with p.open(encoding="utf-8") as f:
        return {(r["engine_symbol"], r["poll_timestamp_cairo"], r.get("response_hash", ""))
                for r in csv.DictReader(f)}


def _append(path, rows):
    if not rows:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.is_file()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def _today_bar(data, session_date):
    """Extract today's dated bar from an EOD JSON array, if present."""
    if not isinstance(data, list):
        return None
    for row in reversed(data):
        if str(row.get("date")) == session_date:
            return row
    return None


def poll_once(client, session_date, seen):
    now = cairo_now().isoformat()
    rows = []
    from providers.symbol_mapping import to_eodhd_symbol
    for engine_symbol in BASKET:
        r = client.eod(engine_symbol, date_from=session_date)
        rec = {"poll_timestamp_cairo": now, "requested_session_date": session_date,
               "engine_symbol": engine_symbol, "eodhd_symbol": to_eodhd_symbol(engine_symbol),
               "status": r.status, "http_status": r.http_status, "response_hash": r.response_hash,
               "detail": r.detail}
        bar = _today_bar(r.data, session_date) if r.ok else None
        if bar:
            rec.update({"bar_exists": True, "open": bar.get("open"), "high": bar.get("high"),
                        "low": bar.get("low"), "close": bar.get("close"),
                        "adjusted_close": bar.get("adjusted_close"), "volume": bar.get("volume")})
            rec["ohlcv_complete"] = all(bar.get(k) not in (None, "") for k in
                                        ("open", "high", "low", "close", "volume"))
        else:
            rec["bar_exists"] = False
            rec["ohlcv_complete"] = False
        key = (rec["engine_symbol"], rec["poll_timestamp_cairo"], rec.get("response_hash") or "")
        if key not in seen:
            rows.append(rec)
            seen.add(key)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--interval-seconds", type=int, default=300)
    ap.add_argument("--max-polls", type=int, default=24)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)

    session_date = args.date or cairo_now().date().isoformat()
    client = EodhdDailyClient(min_interval_seconds=max(1.0, args.interval_seconds / len(BASKET) / 4))

    if not client.key_configured:
        # Record the honest blocker; never fabricate a bar.
        _append(OBS_CSV, [{"poll_timestamp_cairo": cairo_now().isoformat(),
                           "requested_session_date": session_date, "engine_symbol": s,
                           "eodhd_symbol": "", "bar_exists": False, "ohlcv_complete": False,
                           "status": API_KEY_MISSING, "detail": "no EODHD key configured"}
                          for s in BASKET])
        print(json.dumps({"status": API_KEY_MISSING, "session_date": session_date,
                          "note": "observer recorded the blocker; provide a key to measure finalization"}))
        return 2

    seen = _existing_keys(OBS_CSV)
    polls = 0
    while True:
        rows = poll_once(client, session_date, seen)
        _append(OBS_CSV, rows)
        polls += 1
        print(json.dumps({"poll": polls, "appended": len(rows), "session_date": session_date}))
        if args.once or polls >= args.max_polls:
            break
        time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
