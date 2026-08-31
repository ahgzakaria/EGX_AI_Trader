"""One automated post-session Rubix validation command (research, all-disabled).

Runs after a completed EGX session and appends a dual-model (legacy timestamp vs
shadow value-progression) validation row without overwriting earlier sessions.
Never enables paper recording or production; never lowers any threshold; never
changes any strategy, gate, or the collector.

    python scripts/run_scalping_session_validation.py [--date YYYY-MM-DD]
        [--db-path PATH] [--output-dir PATH] [--force-rebuild] [--finalize]
        [--no-dashboard]
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from config.settings_manager import settings  # noqa: E402
from core.egx_session import (  # noqa: E402
    is_regular_trading_day,
    latest_completed_session_date,
)
from scalping.range_config import RangeScalperConfig  # noqa: E402
from scalping.session_validator import analyze_session, multisession_state  # noqa: E402

MULTISESSION_CSV = "reports/scalping_event_gate_multisession.csv"
SESSIONS_DIR = "reports/scalping_sessions"
VERDICT_MD = "docs/audits/strategies/SCALPING_MULTI_SESSION_VERDICT.md"

MULTI_FIELDS = [
    "Session Date", "Patched Collector", "Continuous Uptime %", "Frame Count",
    "Max Frame Gap Seconds", "Max Market Timestamp Gap Seconds",
    "Max Material Value Gap Seconds", "Timestamp-Only Freeze Count",
    "Full-State Freeze Count", "Connection Outage Count", "EVENT_DATA_VALID",
    "Legacy RANGE_CONFIRMED", "Value-Progression RANGE_CONFIRMED", "RANGE_PARTIAL",
    "RANGE_UNRELIABLE", "Paper Opportunities", "Acceptance Session Pass",
    "Failure Reasons", "Run Version", "Dataset Hash", "Generated At",
]


def resolve_date(arg_date, holidays):
    if arg_date:
        d = datetime.strptime(arg_date, "%Y-%m-%d").date()
        if not is_regular_trading_day(d, holidays):
            raise SystemExit(f"{arg_date} is not a regular EGX trading day (weekend/holiday)")
        return d.isoformat()
    d = latest_completed_session_date(holidays=holidays)
    if d is None:
        raise SystemExit("no completed EGX session available yet")
    return d.isoformat()


def existing_rows():
    p = Path(MULTISESSION_CSV)
    if not p.is_file():
        return []
    with p.open(encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def already_processed(rows, session_date):
    return [r for r in rows if r.get("Session Date") == session_date]


def write_session_artifacts(result, out_dir):
    d = Path(out_dir) / result.session_date
    d.mkdir(parents=True, exist_ok=True)
    (d / "session_summary.json").write_text(json.dumps({
        "session_date": result.session_date, "patched_collector": result.patched_collector,
        "continuous_uptime_pct": result.continuous_uptime_pct, "frames": result.frames,
        "max_frame_gap_s": result.max_frame_gap_s, "disconnects": result.disconnects,
        "reconnects": result.reconnects, "resubscribe_complete": result.resubscribe_complete,
        "connection_outages": result.connection_outages,
        "max_market_timestamp_gap_s": result.max_market_timestamp_gap_s,
        "max_material_value_gap_s": result.max_material_value_gap_s,
        "value_gap_median_s": result.value_gap_median_s, "value_gap_p90_s": result.value_gap_p90_s,
        "value_gap_p95_s": result.value_gap_p95_s,
        "timestamp_only_freezes": result.timestamp_only_freezes,
        "full_state_freezes": result.full_state_freezes,
        "event_data_valid": result.event_data_valid,
        "legacy_range_confirmed": result.legacy_range_confirmed,
        "value_range_confirmed": result.value_range_confirmed,
        "range_partial": result.range_partial, "range_unreliable": result.range_unreliable,
        "paper_opportunities": result.paper_opportunities, "dataset_hash": result.dataset_hash,
    }, indent=2), encoding="utf-8")
    _csv(d / "connection_health.csv", result.connection_rows)
    _csv(d / "timestamp_staleness_periods.csv", result.staleness_periods)
    _csv(d / "value_progression_periods.csv", result.value_periods)
    _csv(d / "legacy_vs_value_gate.csv", result.legacy_vs_value)
    _csv(d / "range_candidates.csv", result.range_candidates)
    _csv(d / "paper_gate_decisions.csv", result.paper_decisions or [
        {"note": "paper recording locked until multi-session gate passes; 0 permitted"}])
    (d / "validation_log.txt").write_text("\n".join(result.log), encoding="utf-8")
    return d


def _csv(path, rows):
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def append_multisession(result, force_rebuild):
    rows = existing_rows()
    prior = already_processed(rows, result.session_date)
    if prior and not force_rebuild:
        return rows, prior[-1], False   # idempotent: keep existing, do not duplicate
    run_version = (max((_int(r.get("Run Version")) for r in prior), default=0) + 1) if prior else 1
    session_pass = result.value_range_confirmed > 0
    row = {
        "Session Date": result.session_date,
        "Patched Collector": result.patched_collector,
        "Continuous Uptime %": result.continuous_uptime_pct,
        "Frame Count": result.frames, "Max Frame Gap Seconds": result.max_frame_gap_s,
        "Max Market Timestamp Gap Seconds": result.max_market_timestamp_gap_s,
        "Max Material Value Gap Seconds": result.max_material_value_gap_s,
        "Timestamp-Only Freeze Count": result.timestamp_only_freezes,
        "Full-State Freeze Count": result.full_state_freezes,
        "Connection Outage Count": result.connection_outages,
        "EVENT_DATA_VALID": result.event_data_valid,
        "Legacy RANGE_CONFIRMED": result.legacy_range_confirmed,
        "Value-Progression RANGE_CONFIRMED": result.value_range_confirmed,
        "RANGE_PARTIAL": result.range_partial, "RANGE_UNRELIABLE": result.range_unreliable,
        "Paper Opportunities": result.paper_opportunities,
        "Acceptance Session Pass": session_pass,
        "Failure Reasons": "" if session_pass else "0 value-progression RANGE_CONFIRMED",
        "Run Version": run_version, "Dataset Hash": result.dataset_hash,
        "Generated At": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    rows.append(row)
    Path(MULTISESSION_CSV).parent.mkdir(parents=True, exist_ok=True)
    with open(MULTISESSION_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MULTI_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in MULTI_FIELDS})
    return rows, row, True


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _latest_by_date(rows):
    """Dedupe to the highest Run Version per Session Date."""
    latest = {}
    for r in rows:
        d = r.get("Session Date")
        if d not in latest or _int(r.get("Run Version")) >= _int(latest[d].get("Run Version")):
            latest[d] = r
    return list(latest.values())


def maybe_write_verdict(rows):
    patched = [r for r in _latest_by_date(rows)
               if str(r.get("Patched Collector")).lower() in ("true", "1", "yes")]
    state, detail = multisession_state(rows)
    finalized = len(patched) >= 3   # a verdict is only *final* after >=3 patched sessions
    _write_verdict(patched, state, detail, finalized)   # always keep a living status doc
    return state, detail, finalized


def _write_verdict(patched, state, detail, finalized):
    def g(r, k):
        return r.get(k, "")
    header = "FINAL" if finalized else "INTERIM (living document — not final until ≥3 patched sessions)"
    lines = [
        "# Scalping Multi-Session Verdict (auto-generated)",
        "",
        f"**Status: {header}**",
        "",
        f"**Acceptance state: {state}** — {detail}",
        "",
        "Dual-model per patched session (legacy market_timestamp vs shadow value-progression);",
        "300 s threshold unchanged; all gates disabled (`event_gate_enabled` / ",
        "`paper_recording_enabled` / `production_enabled` = false); no trading.",
        "",
        "| Session | Uptime% | MaxTsGap | MaxValueGap | TsOnlyFreeze | FullStateFreeze | ConnOutage | EVENT_VALID | Legacy RC | Value RC | Pass |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|:--:|",
    ]
    for r in sorted(patched, key=lambda x: g(x, "Session Date")):
        lines.append("| {SD} | {UP} | {TG}s | {VG}s | {TO} | {FS} | {CO} | {EV} | {LR} | {VR} | {PS} |".format(
            SD=g(r, "Session Date"), UP=g(r, "Continuous Uptime %"),
            TG=g(r, "Max Market Timestamp Gap Seconds"), VG=g(r, "Max Material Value Gap Seconds"),
            TO=g(r, "Timestamp-Only Freeze Count"), FS=g(r, "Full-State Freeze Count"),
            CO=g(r, "Connection Outage Count"), EV=g(r, "EVENT_DATA_VALID"),
            LR=g(r, "Legacy RANGE_CONFIRMED"), VR=g(r, "Value-Progression RANGE_CONFIRMED"),
            PS=g(r, "Acceptance Session Pass")))
    qualifying = sum(1 for r in patched if _int(r.get("Value-Progression RANGE_CONFIRMED")) > 0)
    lines += [
        "",
        "## Answers",
        f"1. Valid patched sessions: **{len(patched)}**.",
        f"2/3/4. Freeze semantics are per-session above (timestamp-only vs full-state).",
        f"5. Material-value gaps are per-session (max column).",
        f"6. RANGE_CONFIRMED per model: Legacy vs Value-Progression columns above.",
        f"7/8. Sessions with value RANGE_CONFIRMED > 0: **{qualifying}** (need >=2 of 3).",
        f"9. Paper recording eligible: **{'YES' if state=='PAPER_RECORDING_ELIGIBLE' else 'NO'}** "
        f"(still locked — eligibility is not activation).",
        "10. Paper signals recorded: **0** (recording stays locked; eligibility only).",
        "11. Replace production timestamp gate? Only if state is PAPER_RECORDING_ELIGIBLE across "
        "sessions AND a human approves; this workflow never replaces it automatically.",
        "12. **No** threshold or strategy changed.",
    ]
    Path(VERDICT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _check_db(db_path):
    """Read-only probe used by the scheduled launcher before a full run.

    The question is only "does this open and can the quotes table be read".

    It used to answer that with ``SELECT COUNT(*) FROM quotes``, which SQLite
    can only satisfy by scanning the whole table. On 2026-08-31 that took
    **eleven minutes** -- 22,347,695 rows across 7.3 GB -- out of the task's
    thirty-minute budget, and the collector adds about a million rows a
    session, so it was getting worse every day. The task was then killed at its
    limit while its work was still running.

    Reading the newest id instead uses the primary-key index and returns
    immediately. It is reported as ``latest_id`` rather than ``quote_rows``
    because that is what it is: the highest rowid, which equals the row count
    only if nothing was ever deleted. Naming it honestly costs nothing; calling
    an approximation a count is how a number outlives the caveat that came with
    it.
    """
    import sqlite3
    p = Path(db_path)
    if not p.is_file():
        print(json.dumps({"db_ok": False, "reason": f"not found: {db_path}"}))
        return 3
    try:
        c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=15)
        try:
            row = c.execute("SELECT id FROM quotes ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            c.close()
    except sqlite3.Error as exc:
        print(json.dumps({"db_ok": False, "reason": f"sqlite error: {exc}"}))
        return 3
    print(json.dumps({"db_ok": True, "latest_id": int(row[0]) if row else 0}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--output-dir", default=SESSIONS_DIR)
    ap.add_argument("--force-rebuild", action="store_true")
    ap.add_argument("--finalize", action="store_true")
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--check-db", action="store_true",
                    help="read-only probe: verify the collector DB is readable, then exit")
    args = ap.parse_args(argv)

    # Flags stay disabled — this workflow never activates paper/production.
    cfg = RangeScalperConfig.load()
    market = settings.get("market_data")
    db_path = args.db_path or market.get("rubix_db_path", "data/rubix_live_market.db")

    if args.check_db:
        return _check_db(db_path)
    from core.egx_calendar import effective_holidays
    holidays = effective_holidays()   # shared calendar (built-in ∪ configured)

    session_date = resolve_date(args.date, holidays)
    rows = existing_rows()
    if already_processed(rows, session_date) and not args.force_rebuild:
        state, detail, _ = maybe_write_verdict(rows)
        print(json.dumps({
            "session_date": session_date, "status": "ALREADY_PROCESSED_IDEMPOTENT_SKIP",
            "acceptance_state": state, "acceptance_detail": detail,
            "event_gate_enabled": cfg.__dict__.get("event_gate_enabled", False),
        }, indent=2, default=str))
        return 0

    result = analyze_session(session_date, db_path)
    write_session_artifacts(result, args.output_dir)
    rows, _, appended = append_multisession(result, args.force_rebuild)
    state, detail, finalized = maybe_write_verdict(rows)

    print(json.dumps({
        "session_date": session_date, "patched_collector": result.patched_collector,
        "appended": appended, "continuous_uptime_pct": result.continuous_uptime_pct,
        "max_market_timestamp_gap_s": result.max_market_timestamp_gap_s,
        "max_material_value_gap_s": result.max_material_value_gap_s,
        "timestamp_only_freezes": result.timestamp_only_freezes,
        "full_state_freezes": result.full_state_freezes,
        "connection_outages": result.connection_outages,
        "event_data_valid": result.event_data_valid,
        "legacy_range_confirmed": result.legacy_range_confirmed,
        "value_range_confirmed": result.value_range_confirmed,
        "paper_opportunities": result.paper_opportunities,
        "acceptance_state": state, "acceptance_detail": detail, "verdict_finalized": finalized,
        "flags": {"event_gate_enabled": False, "paper_recording_enabled": False,
                  "production_enabled": False},
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
