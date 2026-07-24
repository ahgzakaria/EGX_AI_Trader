"""Stage C — post-session outcome finalizer (~14:40 Cairo).

Verifies monitor coverage, appends currently-available outcomes with explicit
per-horizon maturity (never finalizing a still-forming horizon and never treating
a horizon that runs past 14:15 as NEITHER), classifies the session's forward-
evidence validity, and writes the session summary. Idempotent — never rewrites an
original READY signal or an already-finalized outcome. Never places an order.

    python scripts/run_expected_range_outcome_finalizer.py [--date YYYY-MM-DD]
        [--pilot] [--anchor-latest-quote]
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
from scalping_expected_range.orchestration import (  # noqa: E402
    COMPLETE_FORWARD_SESSION,
    classify_session,
    read_manifest,
)
from scalping_expected_range.paper_recorder import PaperRecorder  # noqa: E402
from scalping_expected_range.paper_state import PaperStateStore  # noqa: E402


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
    ap.add_argument("--pilot", action="store_true", help="classify this session as PILOT_SESSION")
    ap.add_argument("--anchor-latest-quote", action="store_true")
    args = ap.parse_args(argv)

    cfg = ExpectedRangeConfig.load()
    assert cfg.paper_enabled and not cfg.production_enabled

    from config.settings_manager import settings
    rubix_path = settings.get("market_data").get("rubix_db_path", "data/rubix_live_market.db")
    from core.egx_calendar import effective_holidays
    holidays = effective_holidays()   # shared calendar (built-in ∪ configured)

    now = _latest_quote_time(rubix_path) if args.anchor_latest_quote else None
    session_date = args.date or cairo_now(now).date().isoformat()

    manifest = read_manifest(Path(cfg.paper_output_root) / session_date / "pre_session_manifest.json")
    pilot_marker = (Path(cfg.paper_output_root) / session_date / "PILOT").is_file()
    is_pilot = args.pilot or pilot_marker

    state = PaperStateStore()
    counters = state.counters(session_date)
    cursor = state.get_cursor(session_date)
    monitor_ran = bool(counters.get("evaluations_processed") or cursor)

    cursor_time = None
    if cursor:
        cursor_time = pd.to_datetime(cursor, utc=True).tz_convert("Africa/Cairo").timetz().replace(tzinfo=None)

    # Append outcomes (idempotent; per-horizon maturity handled in the recorder).
    recorder = PaperRecorder(cfg, session_date, rubix_db_path=rubix_path, now=now, holidays=holidays)
    outcomes = recorder.record_outcomes()          # reads rolling signals for this session

    classification = classify_session(
        manifest=manifest, cursor_last_received_cairo_time=cursor_time,
        monitor_ran=monitor_ran, is_pilot=is_pilot)

    # Session summary + daily summary row (with classification for evidence gating).
    session_signals = [s for s in recorder._read(recorder.signals_csv)
                       if s.get("SessionDate") == session_date]
    executable = sum(1 for o in outcomes if o.get("ExecutableEntryAvailable") == "Yes")
    matured = sum(1 for o in outcomes if o.get("OutcomeMaturity") == "FINALIZED")
    summary = {
        "session_date": session_date, "classification": classification,
        "counts_toward_forward_minimum": classification == COMPLETE_FORWARD_SESSION,
        "is_pilot": is_pilot, "snapshot_status": manifest.get("snapshot_status") if manifest else None,
        "monitor_ran": monitor_ran, "cursor_last_received": cursor,
        "ready_signals": len(session_signals), "outcomes": len(outcomes),
        "executable_signals": executable, "matured_outcomes": matured,
        "counters": counters, "strategy_version": cfg.strategy_version,
        "flags": {"paper_enabled": cfg.paper_enabled, "production_enabled": cfg.production_enabled,
                  "automatic_execution": cfg.automatic_execution,
                  "broker_orders_enabled": cfg.broker_orders_enabled},
    }
    sdir = Path(cfg.paper_output_root) / session_date
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "session_summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                               encoding="utf-8")
    # Ensure the per-session outcomes.csv exists (header even when there are none).
    import csv
    from scalping_expected_range.paper_recorder import OUTCOME_FIELDS
    op = sdir / "outcomes.csv"
    if not op.is_file():
        with op.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=OUTCOME_FIELDS).writeheader()
    (sdir / "validation_log.txt").write_text(
        f"{cairo_now(now).isoformat()}  finalizer: classification={classification} "
        f"counts_toward_minimum={classification == COMPLETE_FORWARD_SESSION} "
        f"ready={len(session_signals)} outcomes={len(outcomes)} monitor_ran={monitor_ran} "
        f"cursor_last={cursor}\n", encoding="utf-8")
    _append_daily_summary(recorder.summary_csv, session_date, summary)

    print(json.dumps(summary, indent=2, default=str))
    return summary


def _append_daily_summary(path, session_date, summary):
    import csv
    fields = ["SessionDate", "Classification", "CountsTowardForwardMinimum", "IsPilot",
              "SnapshotStatus", "MonitorRan", "ReadySignals", "Outcomes", "ExecutableSignals",
              "MaturedOutcomes", "StrategyVersion", "PaperEnabled", "ProductionEnabled"]
    row = {"SessionDate": session_date, "Classification": summary["classification"],
           "CountsTowardForwardMinimum": summary["counts_toward_forward_minimum"],
           "IsPilot": summary["is_pilot"], "SnapshotStatus": summary["snapshot_status"],
           "MonitorRan": summary["monitor_ran"], "ReadySignals": summary["ready_signals"],
           "Outcomes": summary["outcomes"], "ExecutableSignals": summary["executable_signals"],
           "MaturedOutcomes": summary["matured_outcomes"], "StrategyVersion": summary["strategy_version"],
           "PaperEnabled": summary["flags"]["paper_enabled"],
           "ProductionEnabled": summary["flags"]["production_enabled"]}
    existing = []
    p = Path(path)
    if p.is_file():
        with p.open(encoding="utf-8") as f:
            existing = [r for r in csv.DictReader(f) if r.get("SessionDate") != session_date]
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in existing + [row]:
            w.writerow({k: r.get(k, "") for k in fields})


if __name__ == "__main__":
    main()
