"""Stage B — durable live paper monitor (10:00-14:15 Cairo, long-running).

Loads the FROZEN pre-session snapshot, refuses to run if it is missing/invalid,
rebuilds the frozen historical analyses (verifying the dataset hash matches the
snapshot), then event-drives the scenario state machines from new Rubix quotes.
Restart-safe (durable SQLite cursor + state), single-instance locked. Never places
an order.

    python scripts/run_expected_range_live_monitor.py [--date YYYY-MM-DD]
        [--once] [--stop-after-seconds N] [--anchor-latest-quote]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import warnings

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import pandas as pd  # noqa: E402

from core.egx_session import cairo_now  # noqa: E402
from scalping_expected_range.config import ExpectedRangeConfig  # noqa: E402
from scalping_expected_range.historical_selector import HistoricalSelector  # noqa: E402
from scalping_expected_range.live_monitor import (  # noqa: E402
    LiveMonitor,
    RubixQuoteReader,
    SingleInstanceLock,
)
from scalping_expected_range.orchestration import (  # noqa: E402
    HISTORY_STALE,
    HISTORY_UNAVAILABLE,
    INVALID_SNAPSHOT,
    NON_TRADING_DAY,
    SNAPSHOT_LATE,
    SNAPSHOT_VALID,
    read_manifest,
)
from scalping_expected_range.paper_state import PaperStateStore  # noqa: E402


def _load_snapshot(path):
    import csv
    with open(path, encoding="utf-8") as f:
        return {r["Symbol"]: r for r in csv.DictReader(f)}


def _latest_quote_time(rubix_path):
    import sqlite3
    p = Path(rubix_path)
    if not p.is_file():
        return None
    conn = sqlite3.connect(f"file:{p.resolve().as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(received_at) FROM quotes").fetchone()
    finally:
        conn.close()
    return pd.to_datetime(row[0], utc=True).to_pydatetime() if row and row[0] else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--once", action="store_true", help="single deterministic pass then exit")
    ap.add_argument("--stop-after-seconds", type=int)
    ap.add_argument("--anchor-latest-quote", action="store_true")
    args = ap.parse_args(argv)

    cfg = ExpectedRangeConfig.load()
    assert cfg.paper_enabled and not cfg.production_enabled, "paper on, production off"
    assert not cfg.automatic_execution and not cfg.broker_orders_enabled

    from config.settings_manager import settings
    rubix_path = settings.get("market_data").get("rubix_db_path", "data/rubix_live_market.db")
    from core.egx_calendar import effective_holidays
    holidays = effective_holidays()   # shared calendar (built-in ∪ configured)

    now = _latest_quote_time(rubix_path) if args.anchor_latest_quote else None
    session_date = args.date or cairo_now(now).date().isoformat()

    manifest = read_manifest(Path(cfg.paper_output_root) / session_date / "pre_session_manifest.json")
    if manifest is None:
        print(json.dumps({"error": "SNAPSHOT_MISSING", "session_date": session_date}))
        return 2
    status = manifest.get("snapshot_status")
    if status == NON_TRADING_DAY:
        # A weekend/holiday is not an error — there is no session to monitor. Exit
        # cleanly with no live artifacts and no alarming "error" alert.
        print(json.dumps({"status": NON_TRADING_DAY, "session_date": session_date,
                          "skipped": "non-trading day (weekend/holiday)"}))
        return 0
    if status in (HISTORY_UNAVAILABLE, INVALID_SNAPSHOT):
        print(json.dumps({"error": "REFUSING_INVALID_SNAPSHOT", "snapshot_status": status}))
        return 2
    # SNAPSHOT_VALID runs a countable session; SNAPSHOT_LATE / HISTORY_STALE record
    # with disclosure but the finalizer will not count the session.
    if status not in (SNAPSHOT_VALID, SNAPSHOT_LATE, HISTORY_STALE):
        print(json.dumps({"error": "UNEXPECTED_SNAPSHOT_STATUS", "snapshot_status": status}))
        return 2

    lock = SingleInstanceLock(Path("data") / f"ers_live_monitor_{session_date}.lock")
    if not lock.acquire():
        print(json.dumps({"error": "ALREADY_RUNNING", "session_date": session_date}))
        return 3
    try:
        snap_map = _load_snapshot(manifest["snapshot_path"])
        selector = HistoricalSelector(config=cfg, holidays=holidays)
        analyses = {s: selector.analyze(s) for s in snap_map}
        state = PaperStateStore()
        reader = RubixQuoteReader(rubix_path)
        monitor = LiveMonitor(cfg, session_date, analyses, snap_map, state_store=state,
                              rubix_reader=reader, holidays=holidays)

        # Frozen-snapshot integrity (advisory disclosure — completed history does
        # not change intraday, so a mismatch signals a mid-session cache change).
        recomputed_hash = _recompute_hash(analyses, manifest.get("latest_historical_session"))
        hash_consistent = (manifest.get("dataset_hash") == recomputed_hash)

        now_fn = (lambda: now) if args.anchor_latest_quote else None
        if args.once:
            result = {"mode": "once", **monitor.drain(now)}
        else:
            result = monitor.run(now_fn=now_fn, stop_after_seconds=args.stop_after_seconds)
        counters = state.counters(session_date)
        print(json.dumps({
            "session_date": session_date, "snapshot_status": status,
            "snapshot_hash_consistent": hash_consistent,
            "anchored_now": now.isoformat() if now else "system",
            "result": result, "counters": counters,
            "flags": {"paper_enabled": cfg.paper_enabled, "production_enabled": cfg.production_enabled,
                      "automatic_execution": cfg.automatic_execution,
                      "broker_orders_enabled": cfg.broker_orders_enabled},
        }, indent=2, default=str))
    finally:
        lock.release()
    return 0


def _recompute_hash(analyses, latest_historical):
    import hashlib
    h = hashlib.sha256()
    h.update(str(latest_historical).encode())
    h.update(str(len(analyses)).encode())
    for symbol, a in analyses.items():
        base = a.expected.base
        low = base.expected_low if base else None
        high = base.expected_high if base else None
        h.update(f"{symbol}:{low}:{high}".encode())
    return h.hexdigest()[:16]


if __name__ == "__main__":
    raise SystemExit(main())
