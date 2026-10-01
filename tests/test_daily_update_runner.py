"""The one-click daily run has to be honest about the step it cannot do.

Downloading history in MubasherTrade PRO is manual, so the most likely reason a
run produces nothing is that it did not happen. An import that reads a file
from three days ago succeeds, writes the same rows, and reports success --
which is exactly the failure this runner exists to make visible.

These tests hold the two things that make it worth clicking: it says when the
terminal's data is behind, and its exit code is non-zero when the candle did
not reach the last completed session.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "run_daily_update", ROOT / "scripts" / "run_daily_update.py")
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)

from sector_flow import mubasher_local as local  # noqa: E402
from sector_flow import measured_turnover as store  # noqa: E402


def _terminal(tmp_path, *, history_date=None, intraday_date=None):
    """A fake UserData tree holding exactly the sessions a test declares."""

    base = tmp_path / "77"
    hist = base / local.HISTORY_RELATIVE
    intra = base / local.INTRADAY_RELATIVE
    hist.parent.mkdir(parents=True, exist_ok=True)
    intra.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(hist) as connection:
        connection.execute('CREATE TABLE "_COMI" (INS TEXT, DATE TEXT, OP TEXT, '
                           'HIG TEXT, LOW TEXT, CLS TEXT, VOL TEXT, TOVR TEXT, NOTR TEXT)')
        if history_date:
            connection.execute('INSERT INTO "_COMI" VALUES (?,?,?,?,?,?,?,?,?)',
                               ("0", history_date.replace("-", ""), "1", "2",
                                "1", "1.5", "10", "15", "3"))
    with sqlite3.connect(intra) as connection:
        connection.execute("CREATE TABLE INTRADAY_MASTER (INTRADAYDATE INTEGER)")
        if intraday_date:
            connection.execute("INSERT INTO INTRADAY_MASTER VALUES (?)",
                               (int(intraday_date.replace("-", "")),))
    return base


def test_a_terminal_that_reaches_the_session_is_reported_ready(tmp_path, monkeypatch, capsys):
    base = _terminal(tmp_path, history_date="2026-09-10", intraday_date="2026-09-10")
    monkeypatch.setattr(local, "find_root", lambda *a, **k: base)

    found, fresh = runner.check_sources(dt.date(2026, 9, 10))

    assert found == base and fresh is True
    assert "2026-09-10 is present" in capsys.readouterr().out


def test_a_download_that_was_not_done_is_named(tmp_path, monkeypatch, capsys):
    """Stale is reported, and the run still continues on what is there."""

    base = _terminal(tmp_path, history_date="2026-09-07", intraday_date="2026-09-07")
    monkeypatch.setattr(local, "find_root", lambda *a, **k: base)

    found, fresh = runner.check_sources(dt.date(2026, 9, 10))

    out = capsys.readouterr().out
    assert found == base, "a stale terminal is still worth importing from"
    assert fresh is False
    assert "2026-09-10" in out and "history.db stops at 2026-09-07" in out
    assert "download" in out.lower()


def test_the_minute_store_alone_can_carry_the_session(tmp_path, monkeypatch):
    """history.db only moves on a manual download; the minute store moves itself.

    So a session the download has not reached is not necessarily missing -- the
    live minute store is what made today available at all before any download
    was run.
    """

    base = _terminal(tmp_path, history_date="2026-09-07", intraday_date="2026-09-10")
    monkeypatch.setattr(local, "find_root", lambda *a, **k: base)

    _found, fresh = runner.check_sources(dt.date(2026, 9, 10))
    assert fresh is True


def test_a_stale_archive_is_named_even_when_the_minute_store_is_current(
        tmp_path, monkeypatch, capsys):
    """The failure this exists for: the archive stopped on 2026-09-07 and the
    minute store kept the stock sessions current, so the run read as fine while
    EGX30 -- which only this file carries -- sat ten sessions behind."""

    base = _terminal(tmp_path, history_date="2026-09-07", intraday_date="2026-09-21")
    monkeypatch.setattr(local, "find_root", lambda *a, **k: base)

    _found, fresh = runner.check_sources(dt.date(2026, 9, 21))

    out = capsys.readouterr().out
    assert fresh is True, "the candle is current; this is a warning, not a failure"
    assert "10 session(s) behind" in out
    assert "EGX30" in out and "market filter" in out
    assert "Download Market History" in out


def test_an_archive_past_the_limit_says_the_filter_has_stopped(
        tmp_path, monkeypatch, capsys):
    base = _terminal(tmp_path, history_date="2026-08-06", intraday_date="2026-09-21")
    monkeypatch.setattr(local, "find_root", lambda *a, **k: base)

    runner.check_sources(dt.date(2026, 9, 21))

    out = capsys.readouterr().out
    assert "no longer blocks anything" in out


def test_an_archive_a_session_or_two_behind_is_not_shouted_about(
        tmp_path, monkeypatch, capsys):
    """One session behind is the normal state right after a close."""

    base = _terminal(tmp_path, history_date="2026-09-20", intraday_date="2026-09-21")
    monkeypatch.setattr(local, "find_root", lambda *a, **k: base)

    runner.check_sources(dt.date(2026, 9, 21))

    out = capsys.readouterr().out
    assert "1 session(s) behind" in out
    assert "Download Market History" not in out


def test_the_lag_is_counted_in_sessions_not_calendar_days():
    """Thursday to Sunday is one session, not three days: the exchange is shut."""
    assert runner._sessions_behind("2026-09-17", "2026-09-20") == 1
    assert runner._sessions_behind("2026-09-20", "2026-09-20") == 0
    assert runner._sessions_behind("not a date", "2026-09-20") == 0


def test_no_terminal_at_all_stops_the_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(local, "find_root", lambda *a, **k: None)

    found, fresh = runner.check_sources(dt.date(2026, 9, 10))

    assert found is None and fresh is False
    assert "not found" in capsys.readouterr().out


def test_an_unreadable_source_file_is_not_a_crash(tmp_path, monkeypatch):
    """A half-written database must degrade to '?', never to a traceback."""

    base = tmp_path / "88"
    (base / local.HISTORY_RELATIVE).parent.mkdir(parents=True)
    (base / local.HISTORY_RELATIVE).write_bytes(b"not a database")
    (base / local.INTRADAY_RELATIVE).parent.mkdir(parents=True)
    (base / local.INTRADAY_RELATIVE).write_bytes(b"not a database either")
    monkeypatch.setattr(local, "find_root", lambda *a, **k: base)

    found, fresh = runner.check_sources(dt.date(2026, 9, 10))
    assert found == base and fresh is False


def _store(tmp_path, sessions):
    database = tmp_path / "measured.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(store.SCHEMA)
        connection.executemany(
            f"INSERT INTO {store.TABLE} (ticker, session_date, turnover) VALUES (?,?,?)",
            [(ticker, day, 1000.0) for day, tickers in sessions.items()
             for ticker in tickers])
    return str(database)


def test_the_candle_check_fails_when_the_store_is_behind(tmp_path, monkeypatch, capsys):
    """The exit code exists for this: a run that looks fine while the candle
    stayed a day back is the failure the click replaces."""

    import sector_flow.measured_turnover as module

    database = _store(tmp_path, {"2026-09-09": ["COMI.CA", "SWDY.CA"]})
    monkeypatch.setattr(module, "DEFAULT_DATABASE", database)

    assert runner.check_candle(dt.date(2026, 9, 10)) is False
    assert "2026-09-09" in capsys.readouterr().out


def test_a_store_ahead_of_the_calendar_is_not_a_failure(tmp_path, monkeypatch, capsys):
    """The archive now carries today's session as soon as the terminal
    downloads it, while the calendar only calls today completed once the
    settlement grace has passed. On 2026-09-21 that read as "the candle is not
    at the last completed session" and failed a run that had just imported it."""

    import sector_flow.measured_turnover as module

    database = _store(tmp_path, {"2026-09-20": ["COMI.CA"], "2026-09-21": ["COMI.CA"]})
    monkeypatch.setattr(module, "DEFAULT_DATABASE", database)

    assert runner.check_candle(dt.date(2026, 9, 20)) is True
    assert "ahead of the calendar" in capsys.readouterr().out


def test_the_candle_check_passes_when_the_session_is_there(tmp_path, monkeypatch):
    import sector_flow.measured_turnover as module

    database = _store(tmp_path, {"2026-09-10": ["COMI.CA", "SWDY.CA"]})
    monkeypatch.setattr(module, "DEFAULT_DATABASE", database)

    assert runner.check_candle(dt.date(2026, 9, 10)) is True


def test_a_calendar_that_cannot_answer_is_not_reported_as_current(tmp_path, monkeypatch):
    """Unknown is not the same as fine.

    The router's own comment says caching a failed lookup told every later
    caller there is no completed session, and callers treating unknown as
    "nothing is missing" then reported a store days behind as current.
    """

    import sector_flow.measured_turnover as module

    database = _store(tmp_path, {"2026-09-10": ["COMI.CA"]})
    monkeypatch.setattr(module, "DEFAULT_DATABASE", database)

    assert runner.check_candle(None) is False


# --- the file that is actually double-clicked -------------------------------

def test_the_batch_file_is_what_the_operator_was_promised():
    text = (ROOT / "RUN_DAILY.bat").read_text(encoding="utf-8")

    assert "run_daily_update.py" in text, "it must call the runner"
    assert "pause" in text, (
        "the window has to stay open; a summary that scrolls past and closes "
        "is a summary nobody reads")
    assert "chcp 65001" in text and "PYTHONIOENCODING=utf-8" in text
    assert 'cd /d "%~dp0"' in text, (
        "double-clicking starts in the operator's own directory, not the "
        "project root")
    # It must not resurrect anything that needs the retired feed.
    for retired in ("rubix", "launch_rubix_production", "run_rubix_daily_finalizer"):
        assert retired not in text.lower(), f"the daily run still calls {retired}"


def test_the_run_records_what_the_two_breakout_pages_named():
    """Both pages rendered and forgot until 2026-09-21. The recorder belongs to
    this click: it reads the measured store the run has just imported."""

    text = (ROOT / "scripts" / "run_daily_update.py").read_text(encoding="utf-8")
    assert "record_swing_breakout_forward.py" in text
    # And the run notices when it stops recording, the way it does for the rest.
    assert "swing_breakout_forward.db" in text


def test_the_run_builds_the_t0_radar_after_the_import():
    """The radar is read after the close, so it belongs to the click that
    imports the close -- and its record is watched like every other one."""

    text = (ROOT / "scripts" / "run_daily_update.py").read_text(encoding="utf-8")
    assert "scripts/run_t0_radar.py" in text
    assert "t0_radar_forward.db" in text
    main = text[text.index("def main("):]
    # The radar reads each name's cost from the bank, so the bank goes first.
    assert main.index("run_cost_bank()") < main.index("run_t0_radar()")


def test_the_daily_run_no_longer_waits_on_eodhd():
    """The owner is not renewing EODHD (2026-10-01). That day the shadow step spent
    an hour re-downloading the histories EODHD had not yet updated, holding up the
    radar and the recorders behind it, so it left the click for good."""

    text = (ROOT / "scripts" / "run_daily_update.py").read_text(encoding="utf-8")
    main = text[text.index("def main("):]
    assert "record_live_source_shadow" not in main
    assert "live_source_shadow.db" not in text[text.index("RECORDERS = ("):text.index("def check_recorders")]


def test_the_run_banks_the_trading_cost_while_the_archive_still_holds_it():
    """The trade archive keeps fourteen sessions; one not banked inside that
    window is gone. It ran by hand until 2026-10-01 and fell three weeks behind."""

    text = (ROOT / "scripts" / "run_daily_update.py").read_text(encoding="utf-8")
    assert "scripts/bank_effective_cost.py" in text


def test_the_runner_forces_the_sector_rebuild():
    """Left to its own missing-session check it would find none and skip.

    The store it reads has just been replaced wholesale, so every session it
    already holds is still carrying the estimate it was built from.
    """

    text = (ROOT / "scripts" / "run_daily_update.py").read_text(encoding="utf-8")
    assert '"--force"' in text
    assert "refresh_sector_flow.py" in text


@pytest.mark.parametrize("raw,expected", [
    ("20260910", "2026-09-10"),
    ("", None),
    ("2026091", None),
])
def test_source_dates_are_parsed_or_refused(tmp_path, raw, expected):
    """A date it cannot parse becomes None, which reads as '?' and never as OK."""

    path = tmp_path / "history.db"
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE "_COMI" (DATE TEXT)')
        if raw:
            connection.execute('INSERT INTO "_COMI" VALUES (?)', (raw,))

    assert runner._max_history_date(path) == expected


# --- the recorders nobody else watches --------------------------------------

def test_a_stalled_recorder_is_named(tmp_path, monkeypatch, capsys):
    """A recorder that stops says nothing on its own.

    The gap forward test yields about 38 observations a session and needs a
    month to be worth reading, so a fortnight of silence is half the
    experiment -- and it is written by a scheduled task this run never calls.
    """

    store_path = tmp_path / "gap.db"
    with sqlite3.connect(store_path) as connection:
        connection.execute("CREATE TABLE gap_predictions (session TEXT)")
        connection.execute("INSERT INTO gap_predictions VALUES ('2026-09-01')")
    monkeypatch.setattr(runner, "RECORDERS",
                        (("gap forward", str(store_path), "gap_predictions", "session"),))

    stalled = runner.check_recorders(dt.date(2026, 9, 10))

    out = capsys.readouterr().out
    assert stalled == ["gap forward"]
    assert "2026-09-01" in out and "behind" in out


def test_a_current_recorder_is_not_named(tmp_path, monkeypatch):
    store_path = tmp_path / "gap.db"
    with sqlite3.connect(store_path) as connection:
        connection.execute("CREATE TABLE gap_predictions (session TEXT)")
        connection.execute("INSERT INTO gap_predictions VALUES ('2026-09-10')")
    monkeypatch.setattr(runner, "RECORDERS",
                        (("gap forward", str(store_path), "gap_predictions", "session"),))

    assert runner.check_recorders(dt.date(2026, 9, 10)) == []


def test_a_missing_recorder_store_is_not_a_failure(tmp_path, monkeypatch, capsys):
    """A store that was never created is not a recorder that stopped."""

    monkeypatch.setattr(runner, "RECORDERS",
                        (("gap forward", str(tmp_path / "nope.db"),
                          "gap_predictions", "session"),))

    assert runner.check_recorders(dt.date(2026, 9, 10)) == []
    assert "not present" in capsys.readouterr().out


def test_a_corrupt_recorder_store_is_reported_not_raised(tmp_path, monkeypatch):
    path = tmp_path / "gap.db"
    path.write_bytes(b"not a database")
    monkeypatch.setattr(runner, "RECORDERS",
                        (("gap forward", str(path), "gap_predictions", "session"),))

    assert runner.check_recorders(dt.date(2026, 9, 10)) == ["gap forward"]
