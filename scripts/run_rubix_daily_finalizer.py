"""Phase 5 — post-auction Rubix daily finalizer (~14:35-14:40 Cairo, Sun-Thu).

Builds validated completed daily bars from captured chronological Rubix events,
writes immutable per-session raw artifacts, and appends/versions rows into the
normalized daily cache (no silent historical rewrite). Records only — never places
an order, never enables production, never changes a strategy parameter.

    python scripts/run_rubix_daily_finalizer.py [--date YYYY-MM-DD] [--db-path PATH]
        [--dry-run] [--force-rebuild] [--compare-only]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import warnings

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.daily_bridge.normalized_cache import NormalizedDailyCache  # noqa: E402
from core.daily_bridge.rubix_daily_builder import RubixDailyBuilder  # noqa: E402
from core.daily_bridge.schema import CSV_FIELDS, FINAL  # noqa: E402
from core.egx_session import (  # noqa: E402
    cairo_now,
    is_regular_trading_day,
    session_is_completed,
)

REPORT_DIR = Path("reports/daily_bridge")


def _write_csv(path, fields, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _sessions_behind(db_path, session_date, holidays):
    """How many trading days the normalized cache is missing, or ``None``.

    Reported whenever this exits without writing, because exiting without
    writing is the failure that hides. From 2026-08-13 the scheduled task ran
    at 14:35 every day, found the session not yet authoritative -- the auction
    ends 14:25 and the settlement grace is 60 minutes, so nothing is
    authoritative until 15:25 -- did nothing, and exited zero. Six trading days
    of daily bars went missing and the only visible symptom was the daily scan
    quietly running on EODHD alone with yesterday's candle as its last bar.

    A count here turns that into something a log or a human can see at a
    glance. Never raises: this is a diagnostic on an exit path, and it must not
    become the reason the exit path fails.
    """

    try:
        import datetime as _dt
        import sqlite3

        from core.egx_session import is_regular_trading_day

        cache = Path("data") / "normalized_daily_cache.db"
        if not cache.is_file():
            return None
        connection = sqlite3.connect(f"file:{cache}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT MAX(session_date) FROM daily_bars WHERE active = 1"
            ).fetchone()
        finally:
            connection.close()
        if not row or not row[0]:
            return None

        latest = _dt.date.fromisoformat(str(row[0])[:10])
        missing = 0
        day = latest + _dt.timedelta(days=1)
        while day < session_date:
            if is_regular_trading_day(day, holidays):
                missing += 1
            day += _dt.timedelta(days=1)
        return missing
    except Exception:                                            # noqa: BLE001
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--db-path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-rebuild", action="store_true")
    ap.add_argument("--compare-only", action="store_true")
    args = ap.parse_args(argv)

    from config.settings_manager import settings
    db_path = args.db_path or settings.get("market_data").get(
        "rubix_db_path", "data/rubix_live_market.db")
    from core.egx_calendar import effective_holidays
    holidays = effective_holidays()   # shared calendar (built-in ∪ configured)
    now = cairo_now()
    session_date = args.date or now.date().isoformat()

    import datetime as _dt
    d = _dt.date.fromisoformat(session_date)
    trading = is_regular_trading_day(d, holidays)
    completed = session_is_completed(d, value=now, close_safety_minutes=15, holidays=holidays)

    out = {"session_date": session_date, "trading_day": trading,
           "session_completed": completed, "dry_run": args.dry_run,
           "force_rebuild": args.force_rebuild, "compare_only": args.compare_only}
    if not trading:
        out["status"] = "NON_TRADING_DAY"
        print(json.dumps(out, indent=2)); return out
    if not completed and not args.force_rebuild:
        out["status"] = "SESSION_NOT_COMPLETED"
        out["cache_sessions_behind"] = _sessions_behind(db_path, d, holidays)
        print(json.dumps(out, indent=2)); return out

    builder = RubixDailyBuilder(db_path)
    bars = builder.build_session(session_date)
    final_bars = [b for b in bars if b.finalization_status == FINAL]

    from collections import Counter
    completeness = dict(Counter(b.session_completeness for b in bars))
    finalization = dict(Counter(b.finalization_status for b in bars))

    # per-session artifacts
    sdir = REPORT_DIR / session_date
    raw_rows = [b.as_row() for b in bars]
    norm_rows = [b.as_row() for b in final_bars]
    rejected = [b.as_row() for b in bars if b.finalization_status != FINAL]
    _write_csv(sdir / "raw_daily_bars.csv", CSV_FIELDS, raw_rows)
    _write_csv(sdir / "normalized_daily_bars.csv", CSV_FIELDS, norm_rows)
    _write_csv(sdir / "rejected_bars.csv", CSV_FIELDS, rejected)

    inserted = skipped = versioned = 0
    if not args.dry_run and not args.compare_only:
        cache = NormalizedDailyCache()
        inserted, skipped, versioned = cache.upsert_bars(final_bars, force_rebuild=args.force_rebuild)

    summary = {
        **out, "status": "OK", "symbols": len(bars), "final_bars": len(final_bars),
        "completeness": completeness, "finalization": finalization,
        "cache_inserted": inserted, "cache_skipped": skipped, "cache_versioned": versioned,
        "flags": {"production_enabled": False, "records_only": True},
    }
    (sdir / "session_summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                               encoding="utf-8")
    (sdir / "validation_log.txt").write_text(
        f"{now.isoformat()}  finalizer: {len(final_bars)}/{len(bars)} FINAL; "
        f"inserted={inserted} skipped={skipped} versioned={versioned} dry_run={args.dry_run}\n",
        encoding="utf-8")

    # rolling reports
    _update_rolling(bars, final_bars, session_date)
    print(json.dumps(summary, indent=2, default=str))
    return summary


def _update_rolling(bars, final_bars, session_date):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    # rubix_daily_bars.csv: append this session's FINAL bars (rewrite session slice)
    path = REPORT_DIR / "rubix_daily_bars.csv"
    existing = _read(path)
    kept = [r for r in existing if r.get("session_date") != session_date]
    _write_csv(path, CSV_FIELDS, kept + [b.as_row() for b in final_bars])
    # quality per symbol
    qfields = ["session_date", "canonical_symbol", "session_completeness",
               "finalization_status", "validation_warnings"]
    qpath = REPORT_DIR / "rubix_daily_quality.csv"
    qkept = [r for r in _read(qpath) if r.get("session_date") != session_date]
    qrows = [{"session_date": session_date, "canonical_symbol": b.canonical_symbol,
              "session_completeness": b.session_completeness,
              "finalization_status": b.finalization_status,
              "validation_warnings": b.validation_warnings} for b in bars]
    _write_csv(qpath, qfields, qkept + qrows)
    # session finalization summary
    from collections import Counter
    sfields = ["session_date", "symbols", "final_bars", "complete_daily",
               "auction_missing", "inactive", "incomplete"]
    comp = Counter(b.session_completeness for b in bars)
    spath = REPORT_DIR / "session_finalization_summary.csv"
    skept = [r for r in _read(spath) if r.get("session_date") != session_date]
    srow = {"session_date": session_date, "symbols": len(bars), "final_bars": len(final_bars),
            "complete_daily": comp.get("COMPLETE_DAILY_BAR", 0),
            "auction_missing": comp.get("COMPLETE_CONTINUOUS_AUCTION_MISSING", 0),
            "inactive": comp.get("SYMBOL_INACTIVE", 0),
            "incomplete": len(bars) - len(final_bars)}
    _write_csv(spath, sfields, skept + [srow])


def _read(path):
    p = Path(path)
    if not p.is_file():
        return []
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


if __name__ == "__main__":
    main()
