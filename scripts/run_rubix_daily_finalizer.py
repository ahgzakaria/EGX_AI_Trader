"""Phase 5 — post-auction Rubix daily finalizer (~14:35-14:40 Cairo, Sun-Thu).

Builds validated completed daily bars from captured chronological Rubix events,
writes immutable per-session raw artifacts, and appends/versions rows into the
normalized daily cache (no silent historical rewrite). Records only — never places
an order, never enables production, never changes a strategy parameter.

    python scripts/run_rubix_daily_finalizer.py [--date YYYY-MM-DD] [--db-path PATH]
        [--dry-run] [--force-rebuild] [--compare-only]
        [--backfill-days N] [--no-backfill]
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
    session_close_datetime,
    session_is_completed,
)

REPORT_DIR = Path("reports/daily_bridge")

#: How long after the 14:30 close a session is treated as safely finished, so
#: no further legitimate write for that date is expected. Named once because
#: the scheduled task's trigger time has to sit after it, and two places that
#: each carried their own 15 is how a task came to fire at 14:42 against a
#: 14:45 boundary and do nothing, every session, reporting success.
CLOSE_SAFETY_MINUTES = 15

#: How far back a scheduled run looks for a session it never finalized.
#: Long enough to cover a machine left off for a working week, short enough
#: that the probe stays cheap. Sessions older than this are EODHD's to serve.
BACKFILL_TRADING_DAYS = 10

#: Distinct captured minutes below which a session counts as never collected.
#: An EGX session runs about 270 minutes. Every collected session observed has
#: shown 228-302 distinct minutes, and every session where the collector was
#: not running has shown 1 -- the snapshot each symbol receives on connect.
#: Nothing has fallen in between, so any threshold in the gap separates the two
#: causes; this one sits an order of magnitude clear of both.
CAPTURE_MINUTES_REQUIRED = 30


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


def captured_minutes(db_path, session_date):
    """Distinct minutes of ``session_date`` present in the Rubix capture.

    The one question the backfill needs answered cheaply: was the collector
    running that day at all? It reads a count, never the events themselves, so
    asking it of ten sessions costs nothing.

    It counts over ``quotes`` on ``received_at``, which is the table and the
    predicate ``RubixDailyBuilder._load_session_quotes`` itself selects on. A
    probe that measured a different table could answer yes to a session the
    builder would find empty, and the point of the probe is to predict the
    builder.

    Returns 0 when the capture cannot be read -- the caller then leaves the
    session to EODHD, which is the safe direction to fail in.
    """

    import sqlite3

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        row = connection.execute(
            "SELECT COUNT(DISTINCT substr(received_at, 12, 5)) FROM quotes "
            "WHERE substr(received_at, 1, 10) = ?", (session_date,)).fetchone()
    except sqlite3.Error:
        return 0
    finally:
        connection.close()
    return int(row[0]) if row and row[0] else 0


def previous_attempt(session_date):
    """What the last finalizer run made of ``session_date``, or ``None``.

    Read so that a session which has already been built and yielded nothing is
    not built again every day until it ages out of the window. 2026-08-16 is the
    case: 228 captured minutes, every symbol classified incomplete, 0 FINAL
    bars, and 78 seconds to reach that answer a second time.

    The attempt is only binding while the capture behind it is unchanged, which
    is why the minute count is compared rather than the verdict alone. A summary
    written before this was recorded has no count, reads as unknown, and earns
    one more attempt.
    """

    path = REPORT_DIR / session_date / "session_summary.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _already_shown_unbuildable(session_date, minutes):
    attempt = previous_attempt(session_date)
    if not attempt or attempt.get("final_bars") != 0:
        return False
    return attempt.get("captured_minutes") == minutes


def sessions_awaiting_finalization(db_path, cache, upto, holidays,
                                   days=BACKFILL_TRADING_DAYS):
    """Recent trading sessions with no bar in the cache, and why.

    Two causes look identical in the cache and could not differ more in what
    should be done about them. A session the collector captured and no one ever
    finalized is ours to build: the events are still in the Rubix store, and
    2026-08-24 sat there for six days with 291 captured minutes and no bar,
    served from EODHD instead while its own candle was buildable the whole
    time. A session the collector never saw cannot be built at all, now or
    later, and belongs to EODHD when it publishes.

    ``upto`` is excluded -- the current session is the caller's own job.
    """

    import datetime as _dt

    pending, day, examined = [], upto - _dt.timedelta(days=1), 0
    while examined < days:
        if is_regular_trading_day(day, holidays):
            examined += 1
            session = day.isoformat()
            if not cache.all_active(session):
                minutes = captured_minutes(db_path, session)
                if minutes < CAPTURE_MINUTES_REQUIRED:
                    action = "NO_RUBIX_CAPTURE"
                elif _already_shown_unbuildable(session, minutes):
                    action = "ALREADY_UNBUILDABLE"
                else:
                    action = "FINALIZE"
                pending.append({"session_date": session,
                                "captured_minutes": minutes,
                                "action": action})
        day -= _dt.timedelta(days=1)
    return list(reversed(pending))


def _backfill(session_date, *, db_path, holidays, now, days, dry_run, compare_only):
    """Finalize sessions an earlier run never did, and name the ones it cannot.

    The scheduled run builds exactly one candle: whichever session it happens
    to wake up on. Miss the slot -- the machine asleep, the task not yet
    installed, a run that exited early because the settlement grace had not
    passed -- and that session's bar is never built again, while its events sit
    in the Rubix store indefinitely. This is the retry that was missing.

    It is deliberately not a repair of everything: a session with no capture is
    reported, not attempted, because no amount of retrying builds a candle out
    of minutes nobody recorded.
    """

    import datetime as _dt

    try:
        cache = NormalizedDailyCache()
        pending = sessions_awaiting_finalization(
            db_path, cache, _dt.date.fromisoformat(session_date), holidays, days)
    except Exception as error:                                   # noqa: BLE001
        return {"status": "UNAVAILABLE",
                "error": f"{type(error).__name__}: {error}"}

    handled = []
    for entry in pending:
        if entry["action"] != "FINALIZE":
            # Both remaining causes end the same way -- EODHD serves the
            # session -- but they are not the same fact, and a run that
            # reported them identically would hide a collector that had
            # stopped behind a session that merely could not be built.
            handled.append({**entry, "left_to": "eodhd"})
            continue
        result = finalize_session(entry["session_date"], db_path=db_path,
                                  holidays=holidays, now=now, dry_run=dry_run,
                                  compare_only=compare_only)
        handled.append({**entry, "status": result.get("status"),
                        "final_bars": result.get("final_bars", 0),
                        "cache_inserted": result.get("cache_inserted", 0)})
    def _count(action):
        return sum(1 for e in handled if e.get("action") == action)

    return {"status": "OK", "trading_days_examined": days,
            "finalized": _count("FINALIZE"),
            "left_to_eodhd": _count("NO_RUBIX_CAPTURE"),
            "already_unbuildable": _count("ALREADY_UNBUILDABLE"),
            "sessions": handled}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--db-path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-rebuild", action="store_true")
    ap.add_argument("--compare-only", action="store_true")
    ap.add_argument("--backfill-days", type=int, default=BACKFILL_TRADING_DAYS,
                    help="trading days a scheduled run looks back for a session "
                         "it never finalized")
    ap.add_argument("--no-backfill", action="store_true")
    args = ap.parse_args(argv)

    from config.settings_manager import settings
    db_path = args.db_path or settings.get("market_data").get(
        "rubix_db_path", "data/rubix_live_market.db")
    from core.egx_calendar import effective_holidays
    holidays = effective_holidays()   # shared calendar (built-in ∪ configured)
    now = cairo_now()
    session_date = args.date or now.date().isoformat()

    summary = finalize_session(session_date, db_path=db_path, holidays=holidays,
                               now=now, dry_run=args.dry_run,
                               force_rebuild=args.force_rebuild,
                               compare_only=args.compare_only)

    # A named --date is a deliberate one-off; only the scheduled run heals. The
    # backfill runs even when today is a holiday or the session has not settled,
    # because those are exactly the runs with nothing else to do and a Thursday
    # to catch up on.
    if args.date is None and not args.no_backfill:
        summary["backfill"] = _backfill(
            session_date, db_path=db_path, holidays=holidays, now=now,
            days=args.backfill_days, dry_run=args.dry_run,
            compare_only=args.compare_only)

    print(json.dumps(summary, indent=2, default=str))
    return summary


def finalize_session(session_date, *, db_path, holidays, now,
                     dry_run=False, force_rebuild=False, compare_only=False):
    """Build, record and cache one session. Returns the summary; prints nothing."""

    import datetime as _dt
    d = _dt.date.fromisoformat(session_date)
    trading = is_regular_trading_day(d, holidays)
    completed = session_is_completed(d, value=now,
                                     close_safety_minutes=CLOSE_SAFETY_MINUTES,
                                     holidays=holidays)

    out = {"session_date": session_date, "trading_day": trading,
           "session_completed": completed, "dry_run": dry_run,
           "force_rebuild": force_rebuild, "compare_only": compare_only}
    if not trading:
        out["status"] = "NON_TRADING_DAY"
        return out
    if not completed and not force_rebuild:
        out["status"] = "SESSION_NOT_COMPLETED"
        out["cache_sessions_behind"] = _sessions_behind(db_path, d, holidays)
        # Say how early, not just that it was early.
        #
        # The scheduled task fired at 14:42 against a 14:45 boundary -- close
        # 14:30 plus close_safety_minutes -- and it fired once a day with no
        # repetition. So it exited here every session, returned 0, and the
        # candle was only ever built the next day by the backfill. Three
        # minutes, and the run reported success while doing nothing.
        #
        # A run that lands inside the safety window is a schedule set against a
        # boundary it does not know about, and the number of minutes is the
        # whole diagnosis. Reading it off the status line is the difference
        # between one line and an afternoon.
        ready_at = session_close_datetime(d) + _dt.timedelta(
            minutes=CLOSE_SAFETY_MINUTES)
        current = cairo_now(now)
        if current < ready_at:
            out["ready_at"] = ready_at.isoformat()
            out["too_early_by_minutes"] = round(
                (ready_at - current).total_seconds() / 60, 1)
        return out

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
    if not dry_run and not compare_only:
        cache = NormalizedDailyCache()
        inserted, skipped, versioned = cache.upsert_bars(final_bars, force_rebuild=force_rebuild)

    summary = {
        **out, "status": "OK", "symbols": len(bars), "final_bars": len(final_bars),
        # Recorded so a later run can tell "built this and it yielded nothing"
        # from "built this before the capture was complete".
        "captured_minutes": captured_minutes(db_path, session_date),
        "completeness": completeness, "finalization": finalization,
        "cache_inserted": inserted, "cache_skipped": skipped, "cache_versioned": versioned,
        "flags": {"production_enabled": False, "records_only": True},
    }
    (sdir / "session_summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                               encoding="utf-8")
    (sdir / "validation_log.txt").write_text(
        f"{now.isoformat()}  finalizer: {len(final_bars)}/{len(bars)} FINAL; "
        f"inserted={inserted} skipped={skipped} versioned={versioned} dry_run={dry_run}\n",
        encoding="utf-8")

    # rolling reports
    _update_rolling(bars, final_bars, session_date)
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
