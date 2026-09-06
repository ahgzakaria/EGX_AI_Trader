"""The finalizer retries a session it never built, and only that kind of session.

The scheduled run builds one candle: whichever session it wakes up on. Every way
of missing that slot -- the machine asleep, the task installed a day late, a run
that exited early because the settlement grace had not passed -- used to cost
that session's Rubix bar permanently, while its events stayed in the store
indefinitely. 2026-08-24 sat there for six days with 291 captured minutes and no
bar; 206 were buildable the whole time.

The distinction these tests exist to protect is between the two ways a session
can be missing from the cache. One is ours to fix and one is not, they look
identical from the cache alone, and treating them the same is wrong in both
directions: retrying a session nobody captured burns a full build on every run
forever, and *not* retrying a captured one loses a candle that was always there.
"""

from __future__ import annotations

import datetime as dt
import json
import importlib.util
import sqlite3

import pandas as pd
import pytest

CAIRO = "Africa/Cairo"


def _module():
    spec = importlib.util.spec_from_file_location(
        "rdf_backfill", "scripts/run_rubix_daily_finalizer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rdf = _module()


def _capture(path, session_date, minutes, tickers=("COMI", "ABUK")):
    """A Rubix store holding ``minutes`` distinct minutes of one session."""

    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS quotes (ticker TEXT, last_price REAL, bid REAL, "
        "ask REAL, volume REAL, market_timestamp TEXT, received_at TEXT)")
    for minute in range(minutes):
        stamp = (pd.Timestamp(f"{session_date} 10:00:00", tz=CAIRO)
                 + pd.Timedelta(minutes=minute)).tz_convert("UTC").isoformat()
        for ticker in tickers:
            connection.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?)",
                               (ticker, 100.0, 99.9, 100.1, 1000.0, stamp, stamp))
    connection.commit()
    connection.close()
    return str(path)


def _record_attempt(report_dir, session_date, *, final_bars, minutes):
    """The artifact a finalizer run leaves behind for the next one to read."""

    directory = report_dir / session_date
    directory.mkdir(parents=True, exist_ok=True)
    summary = {"session_date": session_date, "status": "OK", "final_bars": final_bars}
    if minutes is not None:
        summary["captured_minutes"] = minutes
    (directory / "session_summary.json").write_text(json.dumps(summary),
                                                    encoding="utf-8")


class _Cache:
    """A normalized cache stub: it is only ever asked which sessions it holds."""

    def __init__(self, sessions=()):
        self.sessions = set(sessions)

    def all_active(self, session_date=None):
        return [{"session_date": session_date}] if session_date in self.sessions else []


# --- the probe ---------------------------------------------------------------

def test_captured_minutes_counts_distinct_minutes(tmp_path):
    path = _capture(tmp_path / "rubix.db", "2026-08-24", minutes=240)
    assert rdf.captured_minutes(path, "2026-08-24") == 240


def test_a_session_the_collector_never_saw_counts_one_minute(tmp_path):
    """The shape of a dead day: each symbol's connect snapshot, nothing after."""

    path = _capture(tmp_path / "rubix.db", "2026-08-20", minutes=1)
    assert rdf.captured_minutes(path, "2026-08-20") == 1
    assert rdf.captured_minutes(path, "2026-08-20") < rdf.CAPTURE_MINUTES_REQUIRED


def test_an_unasked_session_has_no_captured_minutes(tmp_path):
    path = _capture(tmp_path / "rubix.db", "2026-08-24", minutes=240)
    assert rdf.captured_minutes(path, "2026-08-25") == 0


def test_an_unreadable_store_reports_nothing_captured(tmp_path):
    """Zero, not an exception: the session is then left to EODHD, which is safe."""

    assert rdf.captured_minutes(str(tmp_path / "absent.db"), "2026-08-24") == 0


def test_a_store_without_a_quotes_table_reports_nothing_captured(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    assert rdf.captured_minutes(str(path), "2026-08-24") == 0


# --- classifying what is missing ---------------------------------------------

def test_a_captured_session_with_no_bar_is_ours_to_finalize(tmp_path):
    path = _capture(tmp_path / "rubix.db", "2026-08-24", minutes=291)
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache(), dt.date(2026, 8, 25), holidays=set(), days=1)
    assert [(e["session_date"], e["action"]) for e in pending] == [
        ("2026-08-24", "FINALIZE")]


def test_a_session_nobody_captured_is_left_to_eodhd(tmp_path):
    path = _capture(tmp_path / "rubix.db", "2026-08-20", minutes=1)
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache(), dt.date(2026, 8, 23), holidays=set(), days=1)
    assert [(e["session_date"], e["action"]) for e in pending] == [
        ("2026-08-20", "NO_RUBIX_CAPTURE")]


def test_a_session_already_in_the_cache_is_not_pending(tmp_path):
    path = _capture(tmp_path / "rubix.db", "2026-08-24", minutes=291)
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache({"2026-08-24"}), dt.date(2026, 8, 25), holidays=set(), days=1)
    assert pending == []


def test_the_current_session_is_never_its_own_backfill(tmp_path):
    """``upto`` is the caller's own job; finalizing it twice in one run is not a retry."""

    path = _capture(tmp_path / "rubix.db", "2026-08-24", minutes=291)
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache(), dt.date(2026, 8, 24), holidays=set(), days=1)
    assert [e["session_date"] for e in pending] == ["2026-08-23"]


def test_friday_saturday_and_holidays_are_not_missing_sessions(tmp_path):
    """2026-08-27 was declared a holiday; 28th and 29th are the EGX weekend."""

    path = str(tmp_path / "rubix.db")
    sqlite3.connect(path).close()
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache(), dt.date(2026, 8, 30), holidays={dt.date(2026, 8, 27)}, days=2)
    assert [e["session_date"] for e in pending] == ["2026-08-25", "2026-08-26"]


def test_the_window_counts_trading_days_not_calendar_days(tmp_path):
    path = str(tmp_path / "rubix.db")
    sqlite3.connect(path).close()
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache(), dt.date(2026, 8, 30), holidays=set(), days=5)
    # Back from Sunday the 30th: the 29th and 28th are the weekend, so five
    # trading days reach the 23rd -- eight calendar days, not five.
    assert [e["session_date"] for e in pending] == [
        "2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"]


def test_pending_sessions_are_returned_oldest_first(tmp_path):
    path = str(tmp_path / "rubix.db")
    sqlite3.connect(path).close()
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache(), dt.date(2026, 8, 30), holidays=set(), days=3)
    dates = [e["session_date"] for e in pending]
    assert dates == sorted(dates)


def test_a_session_already_built_and_empty_is_not_built_again(tmp_path, monkeypatch):
    """2026-08-16: 228 captured minutes, 0 FINAL bars, 78 seconds to say so twice."""

    path = _capture(tmp_path / "rubix.db", "2026-08-24", minutes=291)
    monkeypatch.setattr(rdf, "REPORT_DIR", tmp_path / "reports")
    _record_attempt(tmp_path / "reports", "2026-08-24", final_bars=0, minutes=291)
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache(), dt.date(2026, 8, 25), holidays=set(), days=1)
    assert pending[0]["action"] == "ALREADY_UNBUILDABLE"


def test_a_grown_capture_earns_another_attempt(tmp_path, monkeypatch):
    """The verdict binds only while the capture behind it is unchanged."""

    path = _capture(tmp_path / "rubix.db", "2026-08-24", minutes=291)
    monkeypatch.setattr(rdf, "REPORT_DIR", tmp_path / "reports")
    _record_attempt(tmp_path / "reports", "2026-08-24", final_bars=0, minutes=120)
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache(), dt.date(2026, 8, 25), holidays=set(), days=1)
    assert pending[0]["action"] == "FINALIZE"


def test_an_attempt_recorded_before_minutes_were_kept_earns_another(tmp_path, monkeypatch):
    path = _capture(tmp_path / "rubix.db", "2026-08-24", minutes=291)
    monkeypatch.setattr(rdf, "REPORT_DIR", tmp_path / "reports")
    _record_attempt(tmp_path / "reports", "2026-08-24", final_bars=0, minutes=None)
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache(), dt.date(2026, 8, 25), holidays=set(), days=1)
    assert pending[0]["action"] == "FINALIZE"


def test_an_attempt_that_produced_bars_does_not_block_anything(tmp_path, monkeypatch):
    """It is absent from the cache despite having built bars -- that wants retrying."""

    path = _capture(tmp_path / "rubix.db", "2026-08-24", minutes=291)
    monkeypatch.setattr(rdf, "REPORT_DIR", tmp_path / "reports")
    _record_attempt(tmp_path / "reports", "2026-08-24", final_bars=206, minutes=291)
    pending = rdf.sessions_awaiting_finalization(
        path, _Cache(), dt.date(2026, 8, 25), holidays=set(), days=1)
    assert pending[0]["action"] == "FINALIZE"


def test_no_previous_attempt_reads_as_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(rdf, "REPORT_DIR", tmp_path / "reports")
    assert rdf.previous_attempt("2026-08-24") is None


# --- running the backfill ----------------------------------------------------

def test_an_uncaptured_session_is_reported_and_never_built(tmp_path, monkeypatch):
    """No amount of retrying builds a candle out of minutes nobody recorded."""

    path = _capture(tmp_path / "rubix.db", "2026-08-20", minutes=1)
    monkeypatch.setattr(rdf, "NormalizedDailyCache", lambda *a, **k: _Cache())

    def _never(*args, **kwargs):
        raise AssertionError("an uncaptured session must not be built")

    monkeypatch.setattr(rdf, "finalize_session", _never)
    result = rdf._backfill("2026-08-23", db_path=path, holidays=set(),
                           now=None, days=1, dry_run=True, compare_only=False)
    assert result["status"] == "OK"
    assert result["finalized"] == 0 and result["left_to_eodhd"] == 1
    assert result["sessions"][0]["left_to"] == "eodhd"


def test_a_captured_session_is_finalized(tmp_path, monkeypatch):
    path = _capture(tmp_path / "rubix.db", "2026-08-24", minutes=291)
    monkeypatch.setattr(rdf, "NormalizedDailyCache", lambda *a, **k: _Cache())
    built = []

    def _build(session_date, **kwargs):
        built.append(session_date)
        return {"status": "OK", "final_bars": 206, "cache_inserted": 206}

    monkeypatch.setattr(rdf, "finalize_session", _build)
    result = rdf._backfill("2026-08-25", db_path=path, holidays=set(),
                           now=None, days=1, dry_run=False, compare_only=False)
    assert built == ["2026-08-24"]
    assert result["finalized"] == 1
    assert result["sessions"][0]["cache_inserted"] == 206


def test_an_unreadable_cache_is_reported_not_raised(tmp_path, monkeypatch):
    """The backfill runs after the day's own session; it must not undo that run."""

    def _broken(*args, **kwargs):
        raise sqlite3.DatabaseError("cache is locked")

    monkeypatch.setattr(rdf, "NormalizedDailyCache", _broken)
    result = rdf._backfill("2026-08-30", db_path=str(tmp_path / "r.db"),
                           holidays=set(), now=None, days=3,
                           dry_run=False, compare_only=False)
    assert result["status"] == "UNAVAILABLE"
    assert "cache is locked" in result["error"]


# --- who gets a backfill -----------------------------------------------------

def test_a_named_date_stays_a_one_off(monkeypatch):
    """``--date`` is a deliberate single session, not an invitation to heal."""

    monkeypatch.setattr(rdf, "finalize_session",
                        lambda *a, **k: {"status": "NON_TRADING_DAY"})
    monkeypatch.setattr(rdf, "_backfill", lambda *a, **k: pytest.fail(
        "a named --date must not trigger the backfill"))
    assert rdf.main(["--date", "2026-07-17"])["status"] == "NON_TRADING_DAY"


def test_no_backfill_suppresses_it(monkeypatch):
    monkeypatch.setattr(rdf, "finalize_session", lambda *a, **k: {"status": "OK"})
    monkeypatch.setattr(rdf, "_backfill", lambda *a, **k: pytest.fail(
        "--no-backfill must suppress the backfill"))
    assert "backfill" not in rdf.main(["--no-backfill"])


def test_a_scheduled_run_backfills(monkeypatch):
    monkeypatch.setattr(rdf, "finalize_session", lambda *a, **k: {"status": "OK"})
    monkeypatch.setattr(rdf, "_backfill", lambda *a, **k: {"status": "OK", "sessions": []})
    assert rdf.main([])["backfill"]["status"] == "OK"


def test_a_holiday_run_still_backfills(monkeypatch):
    """The runs with nothing of their own to do are the ones with a Thursday to catch."""

    monkeypatch.setattr(rdf, "finalize_session",
                        lambda *a, **k: {"status": "NON_TRADING_DAY"})
    monkeypatch.setattr(rdf, "_backfill", lambda *a, **k: {"status": "OK", "sessions": []})
    result = rdf.main([])
    assert result["status"] == "NON_TRADING_DAY"
    assert result["backfill"]["status"] == "OK"


# --- the schedule has to land after the boundary -----------------------------
#
# The task fired at 14:42 against a 14:45 boundary -- the 14:30 close plus
# CLOSE_SAFETY_MINUTES -- once a day, with no repetition. So every session it
# exited SESSION_NOT_COMPLETED, returned 0, and the candle was built the next
# day by the backfill instead of the same afternoon. Three minutes, reported as
# a successful run, for days.
#
# The trigger time is not in this repository, so no test can hold it. What a
# test can hold is that a run landing inside the window says how far inside,
# which is the whole diagnosis.

def test_a_run_inside_the_safety_window_reports_how_early_it_is(tmp_path):
    close = dt.datetime(2026, 9, 3, 14, 30, tzinfo=dt.timezone(dt.timedelta(hours=3)))
    result = rdf.finalize_session(
        "2026-09-03", now=close + dt.timedelta(minutes=12),   # 14:42
        db_path=_capture(tmp_path / "r.db", "2026-09-03", 60),
        holidays=None, dry_run=True)

    assert result["status"] == "SESSION_NOT_COMPLETED"
    assert result["too_early_by_minutes"] == 3.0
    assert result["ready_at"].startswith("2026-09-03T14:45")


def test_a_run_after_the_boundary_carries_no_early_marker(tmp_path):
    close = dt.datetime(2026, 9, 3, 14, 30, tzinfo=dt.timezone(dt.timedelta(hours=3)))
    result = rdf.finalize_session(
        "2026-09-03", now=close + dt.timedelta(minutes=20),   # 14:50
        db_path=_capture(tmp_path / "r.db", "2026-09-03", 60),
        holidays=None, dry_run=True)

    assert result["session_completed"] is True
    assert "too_early_by_minutes" not in result


def test_the_boundary_is_named_once_not_carried_in_two_places(tmp_path):
    """The constant the schedule has to clear, and the one the check uses."""

    import inspect

    source = inspect.getsource(rdf.finalize_session)
    assert "close_safety_minutes=CLOSE_SAFETY_MINUTES" in source, (
        "a literal here and a literal in the early-exit is how they drift")
    assert rdf.CLOSE_SAFETY_MINUTES == 15


# --- the schedule is derived, not copied -------------------------------------
#
# The trigger lived in Task Scheduler and the boundary lived here, and no file
# held both, so nothing could catch a task firing three minutes early.
# scripts/windows/install_rubix_daily_finalizer_task.ps1 now asks for the
# boundary instead of being told it. These tests hold the contract it depends
# on: the flag exists, prints a parseable Cairo HH:MM, and agrees with the
# boundary the finalizer itself enforces.

def test_the_ready_time_flag_prints_a_parseable_cairo_time(capsys):
    assert rdf.main(["--print-ready-time"]) == 0
    printed = capsys.readouterr().out.strip().splitlines()[-1]
    hour, _, minute = printed.partition(":")
    assert printed.count(":") == 1
    assert 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59


def test_the_printed_time_is_the_boundary_the_finalizer_enforces(capsys):
    """The installer adds a margin to this. If it drifts, the margin is a lie."""

    from core.egx_session import cairo_now, session_close_datetime

    rdf.main(["--print-ready-time"])
    printed = capsys.readouterr().out.strip().splitlines()[-1]

    expected = (session_close_datetime(cairo_now().date())
                + dt.timedelta(minutes=rdf.CLOSE_SAFETY_MINUTES))
    assert printed == expected.strftime("%H:%M")


def test_printing_the_ready_time_does_no_work(monkeypatch, capsys):
    """It runs from an installer, which must never trigger a build."""

    def never(*args, **kwargs):
        raise AssertionError("--print-ready-time started a finalization")

    monkeypatch.setattr(rdf, "finalize_session", never)
    monkeypatch.setattr(rdf, "_backfill", never)
    assert rdf.main(["--print-ready-time"]) == 0
    assert capsys.readouterr().out.strip()


def test_the_installer_reads_the_boundary_rather_than_naming_a_time():
    """A start time typed into the installer is the bug coming back."""

    from pathlib import Path

    text = Path(
        "scripts/windows/install_rubix_daily_finalizer_task.ps1"
    ).read_text(encoding="utf-8")

    assert "--print-ready-time" in text, "the installer must ask, not assume"
    assert "$MarginMinutes" in text, "the margin is what makes the derivation safe"

    # It must take no start-time parameter. One would be a second copy of the
    # boundary, which is the whole defect: a 14:42 typed next to a 14:45.
    params = text[:text.index("$ErrorActionPreference")]
    for forbidden in ("StartTime", "StartAt", "CairoStart", "TriggerTime"):
        assert forbidden not in params, (
            "-" + forbidden + " lets a caller retype the boundary")

    # And it must refuse a trigger that does not clear the boundary.
    assert "does not clear the boundary" in text


def test_every_task_the_verifier_declares_has_an_installer():
    """The verifier names installer files; a rename must not silently unhook one."""

    import re
    from pathlib import Path

    windows = Path("scripts/windows")
    text = (windows / "verify_scheduled_tasks.ps1").read_text(encoding="utf-8")
    declared = re.findall(r'Installer = "([^"]+)"', text)

    assert len(declared) >= 6, f"expected the full task set, found {declared}"
    missing = [name for name in declared if not (windows / name).is_file()]
    assert not missing, f"verifier points at installers that do not exist: {missing}"


def test_the_verifier_never_reports_an_undetermined_check_as_a_pass():
    """A check that quietly succeeds when it failed to look is the defect itself."""

    from pathlib import Path

    text = Path("scripts/windows/verify_scheduled_tasks.ps1").read_text(encoding="utf-8")
    assert '$expect = "UNKNOWN"' in text
    assert '"UNKNOWN" }' in text, "UNKNOWN must be its own verdict, not folded into OK"


# --- the daily check has to log what it found ------------------------------
#
# Its first captured run logged the comparison table and nothing else. The
# header, the undeclared-task block and the closing verdict all go through
# Write-Host, which does not travel down a 2>&1 pipe -- so the one part that
# always looks fine survived and every finding was dropped.

def test_the_verification_task_captures_every_stream():
    from pathlib import Path

    text = Path("scripts/windows/install_scheduled_tasks_verification_task.ps1"
                ).read_text(encoding="utf-8")
    command = text.partition("$command = ")[2].partition("$encoded")[0]
    assert "*>&1" in command, "Write-Host output is lost without *>&1"
    assert "2>&1" not in command, "2>&1 drops the findings and keeps the table"
    assert "exit $rc" in command, "the verdict must reach the log and the task result"


def test_the_verifier_declares_itself():
    """An undeclared task is reported, so a checker missing from its own list
    would flag itself every morning until people stopped reading it."""

    from pathlib import Path

    text = Path("scripts/windows/verify_scheduled_tasks.ps1").read_text(encoding="utf-8")
    assert "EGX Scheduled Tasks Verification" in text
    assert "install_scheduled_tasks_verification_task.ps1" in text


def test_the_verifier_stays_read_only():
    """It is scheduled unattended against the whole task set."""

    from pathlib import Path

    verifier = Path("scripts/windows/verify_scheduled_tasks.ps1").read_text(encoding="utf-8")
    for forbidden in ("Register-ScheduledTask", "Unregister-ScheduledTask",
                      "Set-ScheduledTask", "Start-ScheduledTask"):
        assert forbidden not in verifier, (
            forbidden + " would make the 09:00 check change things unattended")

    installer = Path("scripts/windows/install_scheduled_tasks_verification_task.ps1"
                     ).read_text(encoding="utf-8")
    assert "no longer a read-only check" in installer, (
        "the installer must refuse a verifier that grew a mutating call")
