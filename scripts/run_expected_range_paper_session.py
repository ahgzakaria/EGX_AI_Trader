"""Run one EXPECTED_RANGE_SCALPER paper forward-recording cycle (records only).

Freezes the immutable pre-session snapshot, evaluates & records every scenario
state (WAIT/READY/rejected), appends immutable decision-time READY snapshots
(duplicate-suppressed, none after 14:15 Cairo), and computes outcomes from
chronological Rubix quotes. NEVER places an order, auto-executes, or enables
production.

    python scripts/run_expected_range_paper_session.py [--date YYYY-MM-DD]
        [--anchor-latest-quote] [--no-outcomes]
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

from core.egx_session import cairo_now, latest_completed_session_date  # noqa: E402
from scalping_expected_range.audit import _pctl  # noqa: E402
from scalping_expected_range.config import ExpectedRangeConfig  # noqa: E402
from scalping_expected_range.paper_recorder import PaperRecorder  # noqa: E402
from scalping_expected_range.scanner import ExpectedRangeScanner  # noqa: E402


def _resolve_session_date(arg_date, holidays, now):
    if arg_date:
        return arg_date
    current = cairo_now(now)
    # The session being papered is today if it is a trading day, else the last one.
    from core.egx_session import is_regular_trading_day
    if is_regular_trading_day(current.date(), holidays):
        return current.date().isoformat()
    d = latest_completed_session_date(holidays=holidays, value=now)
    return d.isoformat() if d else current.date().isoformat()


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
    ap.add_argument("--anchor-latest-quote", action="store_true",
                    help="anchor 'now' to the latest Rubix quote (offline demonstration)")
    ap.add_argument("--no-outcomes", action="store_true")
    args = ap.parse_args(argv)

    cfg = ExpectedRangeConfig.load()
    # Hard safety preconditions.
    assert cfg.paper_enabled, "paper_enabled must be true"
    assert not cfg.production_enabled, "production must stay disabled"
    assert not cfg.automatic_execution and not cfg.broker_orders_enabled, "no execution/orders"

    from config.settings_manager import settings
    rubix_path = settings.get("market_data").get("rubix_db_path", "data/rubix_live_market.db")
    from core.egx_calendar import effective_holidays
    holidays = effective_holidays()   # shared calendar (built-in ∪ configured)

    now = None
    if args.anchor_latest_quote:
        now = _latest_quote_time(rubix_path)

    session_date = _resolve_session_date(args.date, holidays, now)

    scanner = ExpectedRangeScanner(config=cfg, now=now, holidays=holidays)
    scan = scanner.scan(with_live=True)
    universe = scan["universe"].copy()
    analyses = scan["analyses"]

    # Add the separate raw-volume / turnover ranks for the immutable snapshot.
    universe["_RawVolumeRank"] = _pctl(universe.get("liq_avg_volume_20")).rank(
        ascending=False, method="min")
    universe["_TurnoverRank"] = _pctl(universe.get("liq_avg_turnover_egp_20")).rank(
        ascending=False, method="min")
    historical_through = universe["prov_latest_completed_session"].dropna().max() \
        if "prov_latest_completed_session" in universe else None

    recorder = PaperRecorder(cfg, session_date, rubix_db_path=rubix_path, now=now,
                             holidays=holidays)
    _, dataset_hash = recorder.write_presession_snapshot(universe, historical_through)
    cycle = recorder.evaluate_cycle(analyses, universe, scanner._live_quote, dataset_hash,
                                    historical_through)
    outcomes = [] if args.no_outcomes else recorder.record_outcomes(cycle["signals"])
    summary = recorder.write_session_summary(universe, cycle, outcomes)

    print(json.dumps({
        "session_date": session_date,
        "anchored_now": now.isoformat() if now else "system",
        "dataset_hash": dataset_hash,
        "ready_signals": len(cycle["signals"]),
        "transitions": len(cycle["transitions"]),
        "rejected_states": len(cycle["rejected"]),
        "outcomes": len(outcomes),
        "session_summary": summary,
        "flags": {"paper_enabled": cfg.paper_enabled, "production_enabled": cfg.production_enabled,
                  "automatic_execution": cfg.automatic_execution,
                  "broker_orders_enabled": cfg.broker_orders_enabled},
        "artifacts_dir": str(recorder.session_dir),
    }, indent=2, default=str))
    return summary


if __name__ == "__main__":
    main()
