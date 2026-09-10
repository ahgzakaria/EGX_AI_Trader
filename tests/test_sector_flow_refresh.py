"""The daily refresh skips the work rather than splitting it.

A full rebuild costs minutes. What makes a daily schedule affordable is that on
most runs -- every weekend, every holiday, every run after the day's first --
nothing has completed since the last build, so there is nothing to derive.

The second guard is subtler: EODHD publishes a session a day late and sometimes
two, so a build can be entirely correct and still not advance. Without it the
scheduler would pay a full rebuild on every run until the provider caught up.
"""

from datetime import date, timedelta
import sqlite3

import pandas as pd
import pytest

from sector_flow import HISTORY_TABLE, METADATA_TABLE
from sector_flow import builder
from sector_flow.builder import sessions_behind, stored_latest_session
from scripts import refresh_sector_flow


SESSIONS = ["2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26"]


def write_history(path, sessions=SESSIONS, coverage=1.0, metadata=None):
    rows = [{"SessionDate": session, "Sector": "Banks", "TurnoverShare": 0.5,
             "RVOL": 1.0, "SessionCoverage": coverage} for session in sessions]
    with sqlite3.connect(path) as connection:
        pd.DataFrame(rows).to_sql(HISTORY_TABLE, connection, if_exists="replace", index=False)
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {METADATA_TABLE} "
            "(built_at TEXT PRIMARY KEY, metadata_json TEXT NOT NULL)")
        if metadata is not None:
            import json
            connection.execute(f"INSERT OR REPLACE INTO {METADATA_TABLE} VALUES (?, ?)",
                               ("2026-08-27T10:00:00+03:00", json.dumps(metadata)))
    return str(path)


@pytest.fixture(autouse=True)
def fixed_calendar(monkeypatch):
    """Pin the trading calendar.

    These tests are about counting sessions between two dates. Left reading the
    live calendar they change verdict whenever an operator records a closure --
    which is exactly what happened when 2026-08-27 was declared a holiday.
    """

    from core import egx_calendar, egx_session

    monkeypatch.setattr(egx_calendar, "effective_holidays", lambda *a, **k: set())
    monkeypatch.setattr(
        egx_session, "is_regular_trading_day",
        lambda day, holidays=None: day.weekday() in (6, 0, 1, 2, 3),  # Sun-Thu
    )


@pytest.fixture(autouse=True)
def isolated_lock(tmp_path, monkeypatch):
    """Never reach for the real lock file.

    Not hypothetical: the first run of the guard's own tests came back BUSY
    across the board because a genuine rebuild was in flight on this machine and
    holding it. A test must not depend on whether the scheduler happens to be
    working right now.
    """

    monkeypatch.setattr(refresh_sector_flow, "LOCK_FILE", tmp_path / "refresh.lock")


@pytest.fixture
def database(tmp_path):
    return str(tmp_path / "sector_flow.db")


# --------------------------------------------------------------------------- #
# Reading the store cheaply
# --------------------------------------------------------------------------- #

def test_the_latest_complete_session_is_read_from_the_store(database):
    write_history(database)
    assert stored_latest_session(database) == date(2026, 8, 26)


def test_an_incomplete_session_is_not_the_latest(database):
    write_history(database, coverage=0.1)
    assert stored_latest_session(database) is None


def test_a_missing_database_has_no_latest_session(tmp_path):
    assert stored_latest_session(str(tmp_path / "absent.db")) is None


def test_an_empty_store_reports_that_a_full_build_is_required(tmp_path):
    """None, not zero: nothing stored is different from nothing missing."""

    assert sessions_behind(str(tmp_path / "absent.db")) is None


def test_a_current_store_is_zero_sessions_behind(database, monkeypatch):
    write_history(database)
    monkeypatch.setattr(builder, "_expected_session", lambda: date(2026, 8, 26))
    assert sessions_behind(database) == 0


def test_only_trading_days_count_as_missing(database, monkeypatch):
    """Friday and Saturday are not EGX sessions and must not look like a gap."""

    write_history(database)
    monkeypatch.setattr(builder, "_expected_session", lambda: date(2026, 8, 30))
    # 27th Thu, 28th Fri, 29th Sat, 30th Sun -> two trading days.
    assert sessions_behind(database) == 2


def test_an_empty_store_still_needs_a_build_when_the_session_is_unknown(tmp_path, monkeypatch):
    """The two questions are asked in the order that cannot lie.

    An unresolvable expected session used to be answered before the store was
    looked at, so "nothing is stored" came back as zero sessions behind: a full
    build required, reported as nothing to do. It is reachable in a plain
    process -- the router caches the expected session, and a lookup that failed
    once used to stay failed.
    """

    monkeypatch.setattr(builder, "_expected_session", lambda: None)
    assert sessions_behind(str(tmp_path / "absent.db")) is None


def test_an_unknown_session_over_a_populated_store_is_not_a_rebuild(database, monkeypatch):
    """Still zero here: something is stored, and nothing is known to be missing."""

    write_history(database)
    monkeypatch.setattr(builder, "_expected_session", lambda: None)
    assert sessions_behind(database) == 0


# --------------------------------------------------------------------------- #
# What the refresh decides
# --------------------------------------------------------------------------- #

def test_nothing_missing_means_no_rebuild(database, monkeypatch, tmp_path):
    write_history(database)
    monkeypatch.setattr(builder, "_expected_session", lambda: date(2026, 8, 26))
    monkeypatch.setattr(refresh_sector_flow, "LOG_DIR", tmp_path)

    def never(*args, **kwargs):
        raise AssertionError("a rebuild was started when nothing was missing")
    monkeypatch.setattr(refresh_sector_flow, "build", never)

    result = refresh_sector_flow.refresh(database=database)
    assert result["status"] == "UP_TO_DATE"
    assert result["rebuilt"] is False


def test_a_session_already_attempted_waits_on_the_provider(database, monkeypatch, tmp_path):
    """A correct build that did not advance must not be retried every run."""

    write_history(database, metadata={"attempted_for_session": "2026-08-27"})
    monkeypatch.setattr(builder, "_expected_session", lambda: date(2026, 8, 27))
    monkeypatch.setattr(refresh_sector_flow, "_expected_session", lambda: date(2026, 8, 27))
    monkeypatch.setattr(refresh_sector_flow, "LOG_DIR", tmp_path)

    def never(*args, **kwargs):
        raise AssertionError("rebuilt for a session already attempted")
    monkeypatch.setattr(refresh_sector_flow, "build", never)

    result = refresh_sector_flow.refresh(database=database)
    assert result["status"] == "WAITING_ON_PROVIDER"
    assert result["rebuilt"] is False


def test_a_newly_completed_session_does_rebuild(database, monkeypatch, tmp_path):
    write_history(database, metadata={"attempted_for_session": "2026-08-26"})
    monkeypatch.setattr(builder, "_expected_session", lambda: date(2026, 8, 27))
    monkeypatch.setattr(refresh_sector_flow, "_expected_session", lambda: date(2026, 8, 27))
    monkeypatch.setattr(refresh_sector_flow, "LOG_DIR", tmp_path)

    calls = []

    def fake_build():
        calls.append(True)
        history = pd.DataFrame({"SessionDate": pd.to_datetime(["2026-08-27"]),
                                "Sector": ["Banks"], "TurnoverShare": [1.0]})
        outcomes = pd.DataFrame({"Ticker": ["COMI.CA"], "Status": ["LOADED"]})
        return history, outcomes, {"loaded_symbols": 1, "classified_symbols": 1,
                                   "held_symbols": 0, "unavailable_symbols": 0,
                                   "last_complete_session": "2026-08-27",
                                   "built_at": "2026-08-27T16:00:00+03:00"}
    monkeypatch.setattr(refresh_sector_flow, "build", fake_build)
    monkeypatch.setattr(refresh_sector_flow, "save", lambda *a, **k: None)

    result = refresh_sector_flow.refresh(
        database=database, coverage_report=str(tmp_path / "coverage.csv"))
    assert calls, "a missing session should trigger a rebuild"
    assert result["status"] == "OK"
    assert result["latest_complete_session"] == "2026-08-27"


def test_force_rebuilds_even_when_nothing_is_missing(database, monkeypatch, tmp_path):
    write_history(database, metadata={"attempted_for_session": "2026-08-26"})
    monkeypatch.setattr(builder, "_expected_session", lambda: date(2026, 8, 26))
    monkeypatch.setattr(refresh_sector_flow, "_expected_session", lambda: date(2026, 8, 26))
    monkeypatch.setattr(refresh_sector_flow, "LOG_DIR", tmp_path)

    calls = []

    def fake_build():
        calls.append(True)
        return (pd.DataFrame({"SessionDate": pd.to_datetime(["2026-08-26"]),
                              "Sector": ["Banks"]}),
                pd.DataFrame({"Ticker": ["COMI.CA"], "Status": ["LOADED"]}),
                {"loaded_symbols": 1, "classified_symbols": 1, "held_symbols": 0,
                 "unavailable_symbols": 0, "last_complete_session": "2026-08-26",
                 "built_at": "x"})
    monkeypatch.setattr(refresh_sector_flow, "build", fake_build)
    monkeypatch.setattr(refresh_sector_flow, "save", lambda *a, **k: None)

    result = refresh_sector_flow.refresh(
        force=True, database=database, coverage_report=str(tmp_path / "coverage.csv"))
    assert calls and result["status"] == "OK"


def test_a_build_failure_is_reported_not_raised(database, monkeypatch, tmp_path):
    """A scheduled task must exit with a status, not a traceback."""

    write_history(database, metadata={"attempted_for_session": "2026-08-26"})
    monkeypatch.setattr(builder, "_expected_session", lambda: date(2026, 8, 27))
    monkeypatch.setattr(refresh_sector_flow, "_expected_session", lambda: date(2026, 8, 27))
    monkeypatch.setattr(refresh_sector_flow, "LOG_DIR", tmp_path)

    def boom():
        raise RuntimeError("provider down")
    monkeypatch.setattr(refresh_sector_flow, "build", boom)

    result = refresh_sector_flow.refresh(database=database)
    assert result["status"] == "FAILED"
    assert "provider down" in result["error"]
    assert refresh_sector_flow.main(["--database", database]) == 1


# --- a failed run has to say so ----------------------------------------------
#
# On 2026-09-02 the 16:00 scheduled run logged "rebuilding" and then nothing,
# exited 1, and left the store two sessions behind. Only build() was inside the
# try; writing the coverage CSV and saving to SQLite -- the steps that contend
# with a reader -- were not. The failure escaped as a traceback into a log that
# did not capture it, and the only evidence was a missing line.

def test_a_failure_after_the_build_is_reported_not_raised(tmp_path, monkeypatch):
    """Saving is the step that contends with a reader, and it was unguarded."""

    import pandas as pd

    from scripts import refresh_sector_flow as module

    monkeypatch.setattr(module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(module, "stored_latest_session", lambda *a, **k: None)
    monkeypatch.setattr(module, "sessions_behind", lambda *a, **k: 2)
    monkeypatch.setattr(module, "_expected_session", lambda: None)
    monkeypatch.setattr(module, "load_project_environment", lambda: None)
    monkeypatch.setattr(module, "build", lambda: (
        pd.DataFrame([{"SessionDate": "2026-09-02", "Sector": "Banks"}]),
        pd.DataFrame([{"symbol": "COMI"}]),
        {"loaded_symbols": 1, "classified_symbols": 1, "held_symbols": 0,
         "unavailable_symbols": 0, "last_complete_session": "2026-09-02"},
    ))

    def _locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(module, "save", _locked)

    result = module.refresh(database=str(tmp_path / "sector.db"),
                            coverage_report=str(tmp_path / "coverage.csv"))

    assert result["status"] == "FAILED"
    assert "database is locked" in result["error"]
    written = (tmp_path / module.LOG_NAME).read_text(encoding="utf-8")
    assert "FAILED" in written, "the run must record its own failure"


def test_a_failure_writing_the_coverage_report_is_also_reported(tmp_path, monkeypatch):
    import pandas as pd

    from scripts import refresh_sector_flow as module

    monkeypatch.setattr(module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(module, "stored_latest_session", lambda *a, **k: None)
    monkeypatch.setattr(module, "sessions_behind", lambda *a, **k: 1)
    monkeypatch.setattr(module, "_expected_session", lambda: None)
    monkeypatch.setattr(module, "load_project_environment", lambda: None)

    class _Unwritable(pd.DataFrame):
        def to_csv(self, *args, **kwargs):
            raise OSError("no space left on device")

    monkeypatch.setattr(module, "build", lambda: (
        pd.DataFrame([{"SessionDate": "2026-09-02"}]),
        _Unwritable([{"symbol": "COMI"}]),
        {"loaded_symbols": 1, "classified_symbols": 1, "held_symbols": 0,
         "unavailable_symbols": 0, "last_complete_session": "2026-09-02"},
    ))

    result = module.refresh(database=str(tmp_path / "sector.db"),
                            coverage_report=str(tmp_path / "coverage.csv"))
    assert result["status"] == "FAILED"
    assert "no space left" in result["error"]


# --- one rebuild at a time ---------------------------------------------------
#
# save() replaces the whole table, so two builds finishing together do not
# produce a winner. On 2026-09-02 two finished 74 ms apart and the store kept
# *both*: every (SessionDate, Sector) twice, 143,870 rows against 71,935
# distinct pairs. The per-sector numbers in each pair were identical and only
# the market totals differed -- 190 symbols against 187 -- because the two runs
# had loaded 222 and 219 symbols. Nothing failed. Nothing was logged. The page
# just showed every row twice.
#
# Task Scheduler's MultipleInstances=IgnoreNew did not cover it: it stops the
# scheduler starting a second copy of its own task and says nothing about a
# hand-run rebuild landing on top of one. A full build takes minutes and the
# schedule repeats hourly, so the overlap needs no unusual timing at all.

def test_a_second_run_refuses_while_one_is_in_flight(tmp_path, monkeypatch):
    from scripts import refresh_sector_flow as module
    from scripts.launcher_process_utils import SingleInstanceLock

    monkeypatch.setattr(module, "LOG_DIR", tmp_path)
    lock_file = tmp_path / "refresh.lock"
    held = SingleInstanceLock(lock_file, label="Sector flow refresh").acquire()

    def never(*args, **kwargs):
        raise AssertionError("a second rebuild started while one was running")
    monkeypatch.setattr(module, "build", never)

    try:
        result = module.refresh(database=str(tmp_path / "s.db"), lock_file=lock_file)
    finally:
        held.release()

    assert result["status"] == "BUSY"
    assert result["rebuilt"] is False


def test_being_busy_is_not_a_failure_exit(tmp_path, monkeypatch):
    """The scheduler must not report a red run because it was already working."""

    from scripts import refresh_sector_flow as module

    monkeypatch.setattr(module, "refresh",
                        lambda **kwargs: {"status": "BUSY", "rebuilt": False})
    assert module.main([]) == 0


def test_the_lock_is_released_so_the_next_scheduled_run_can_work(tmp_path, monkeypatch):
    from scripts import refresh_sector_flow as module
    from scripts.launcher_process_utils import SingleInstanceLock

    monkeypatch.setattr(module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(module, "stored_latest_session", lambda *a, **k: None)
    monkeypatch.setattr(module, "sessions_behind", lambda *a, **k: 0)
    lock_file = tmp_path / "refresh.lock"

    first = module.refresh(database=str(tmp_path / "s.db"), lock_file=lock_file)
    assert first["status"] == "UP_TO_DATE"

    # If the lock survived the run, this would raise instead of acquiring.
    SingleInstanceLock(lock_file, label="probe").acquire().release()


def test_the_lock_is_released_even_when_the_build_raises(tmp_path, monkeypatch):
    """A crashed rebuild must not wedge every later run into BUSY forever."""

    from scripts import refresh_sector_flow as module
    from scripts.launcher_process_utils import SingleInstanceLock

    monkeypatch.setattr(module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(module, "stored_latest_session", lambda *a, **k: None)
    monkeypatch.setattr(module, "sessions_behind", lambda *a, **k: 2)
    monkeypatch.setattr(module, "_expected_session", lambda: None)
    monkeypatch.setattr(module, "load_project_environment", lambda: None)

    def explode():
        raise MemoryError("out of memory mid-build")
    monkeypatch.setattr(module, "build", explode)

    lock_file = tmp_path / "refresh.lock"
    result = module.refresh(database=str(tmp_path / "s.db"), lock_file=lock_file)
    assert result["status"] == "FAILED"

    SingleInstanceLock(lock_file, label="probe").acquire().release()


# --- turnover measured, not estimated ----------------------------------------
#
# turnover_series derives turnover as (High + Low + Close) / 3 x Volume, which
# is within 0.37% at the median and 248% at its worst. Sector share is one
# symbol's turnover over the market's, so on 5% of sessions since 2020 that
# error moved a sector's share by more than 35 points -- once by 58.77, when
# Non-bank Financial Services was ranked at 75.77% against a true 16.99%.

def test_a_measured_turnover_replaces_the_estimate():
    from sector_flow.history import turnover_series

    frame = pd.DataFrame({
        "High": [11.0], "Low": [9.0], "Close": [10.0],
        "Volume": [1000.0], "Turnover": [999_999.0],
    })
    assert turnover_series(frame).iloc[0] == 999_999.0


def test_a_session_the_store_does_not_cover_keeps_the_estimate():
    """Per session, not per symbol: the export ends the day it was taken."""

    from sector_flow.history import turnover_series

    frame = pd.DataFrame({
        "High": [11.0, 11.0], "Low": [9.0, 9.0], "Close": [10.0, 10.0],
        "Volume": [1000.0, 1000.0], "Turnover": [999_999.0, float("nan")],
    })
    result = turnover_series(frame)
    assert result.iloc[0] == 999_999.0
    assert result.iloc[1] == pytest.approx(10_000.0)


def test_no_turnover_column_at_all_still_builds():
    """A machine that has never run the import must build exactly as before."""

    from sector_flow.history import turnover_series

    frame = pd.DataFrame({"High": [11.0], "Low": [9.0], "Close": [10.0],
                          "Volume": [1000.0]})
    assert turnover_series(frame).iloc[0] == pytest.approx(10_000.0)


# --- a price quoted in dollars, a turnover reported in pounds -----------------
#
# Eleven symbols were in the tradeable universe marked EGP while being quoted in
# dollars, one of them named "Faisal Islamic Bank of Egypt - In US Dollars" in
# our own file. turnover / (volume x close) is the price over the price for an
# EGP symbol -- COMI sits at 1.0004 -- and the exchange rate for those: 5.77 in
# 2005, 8.77 in 2016, 48.36 in 2024, tracking the currency year by year.

def test_the_universe_marks_the_foreign_quoted_symbols():
    from core.universe import UNIVERSE_SOURCE

    universe = pd.read_csv(UNIVERSE_SOURCE)
    base = universe["canonical_symbol"].astype(str).str.split(".").str[0].str.upper()
    known = {"SAIB", "EGBE", "NAHO", "EGSA", "VLMR", "CFGH",
             "FAITA", "MOIL", "GTEX", "TRTO", "GPPL", "SPHT"}
    marked = universe[base.isin(known)]
    assert len(marked) == len(known), "a foreign-quoted symbol left the universe"
    assert set(marked["currency"]) <= {"USD", "EUR"}, (
        "these are not priced in EGP; the currency column said otherwise for all "
        "of them, which is how their turnover was understated fifty-fold")
    # is_active stays true: it says what the exchange lists, and the shipped
    # file is held to a validated snapshot. What an EGP account can buy is a
    # different question, answered where the engine's universe is decided.
    from core.symbols import SYMBOL_SOURCE, load_active_symbols, load_tradeable_symbols

    # Still listed: is_active describes the exchange, and 241 is a number a
    # migration validated. Not tradeable: that is the account's question.
    listed = {str(t).split(".")[0].upper() for t in load_active_symbols(SYMBOL_SOURCE)}
    assert known <= listed, "these are still listed on the exchange"

    engine = {str(t).split(".")[0].upper() for t in load_tradeable_symbols(SYMBOL_SOURCE)}
    assert not (known & engine), (
        "an account settling in EGP cannot trade these, and ten of them had "
        f"already produced forward-test signals: {sorted(known & engine)}")


def test_the_detector_separates_a_currency_from_a_split():
    """Both push the ratio off 1; only one of them is a currency."""

    import inspect

    from sector_flow import measured_turnover

    source = inspect.getsource(measured_turnover.foreign_quoted_symbols)
    assert "since" in source, (
        "judged over all history a 25-for-1 split looks foreign too; ASPI sits "
        "at 0.040 before 2021-10-11 and 1.000 after, with the close continuous")


# --- symbols the provider has nothing for ------------------------------------
#
# ACGC, NCCW, JUFO and EDBM are UNAVAILABLE in every coverage report this
# project has produced: zero bars, no contribution to any sector's turnover, in
# no scan. The export holds 5,527, 4,769, 3,851 and 3,811 sessions for them,
# going back to 2003.

def test_a_symbol_the_provider_cannot_serve_comes_from_the_export(tmp_path):
    import sqlite3 as sql

    from sector_flow import measured_turnover as store

    database = str(tmp_path / "measured.db")
    with sql.connect(database) as connection:
        connection.executescript(store.SCHEMA)
        connection.executemany(
            f"INSERT INTO {store.TABLE} "
            "(ticker, session_date, turnover, volume, open, high, low, close) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [("ACGC.CA", "2003-11-03", 5000.0, 100.0, 1.0, 1.2, 0.9, 1.1),
             ("ACGC.CA", "2003-11-04", 6000.0, 120.0, 1.1, 1.3, 1.0, 1.2)])

    frame = store.frame_for("ACGC.CA", database)
    assert frame is not None and len(frame) == 2
    assert list(frame.columns)[:6] == ["Open", "High", "Low", "Close",
                                       "Volume", "Turnover"]
    # A store that records whether a close is the auction price says so in a
    # trailing column. Trailing and named, so a caller reading the contract by
    # name is unaffected and one that never asks keeps the same six.
    assert list(frame.columns)[6:] in ([], ["CloseConfirmed"])
    assert frame.attrs["market_data"]["effective_provider"] == "mubasher_export"
    assert frame.attrs["market_data"]["automatic_use_permitted"] is False, (
        "these prices are not dividend-adjusted; they describe turnover and "
        "must never become an entry")


def test_the_export_is_only_a_fallback_not_a_replacement():
    """The provider is asked first; this answers when it has nothing at all."""

    import inspect

    from sector_flow import builder

    source = inspect.getsource(builder.load_universe_frames)
    body = source.partition("except Exception as error:")[2]
    assert "measured_frame(symbol)" in body, "the fallback belongs on the failure path"
    assert source.index("load_history") < source.index("measured_frame")


def test_volume_is_filled_only_where_the_provider_left_it_empty(tmp_path):
    """Prices are never touched: they are not dividend-adjusted."""

    import sqlite3 as sql

    from sector_flow import measured_turnover as store

    database = str(tmp_path / "measured.db")
    with sql.connect(database) as connection:
        connection.executescript(store.SCHEMA)
        connection.execute(
            f"INSERT INTO {store.TABLE} "
            "(ticker, session_date, turnover, volume, open, high, low, close) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("COMI.CA", "2026-09-06", 1.0, 777.0, 9.0, 9.0, 9.0, 9.0))

    frame = pd.DataFrame(
        {"Close": [50.0, 50.0], "Volume": [float("nan"), 111.0]},
        index=pd.to_datetime(["2026-09-06", "2026-09-07"]))
    filled = store.fill_missing_volume(frame, "COMI.CA", database)

    assert filled["Volume"].iloc[0] == 777.0, "the empty one is filled"
    assert filled["Volume"].iloc[1] == 111.0, "the provider's own value stands"
    assert list(filled["Close"]) == [50.0, 50.0], "prices are never touched"
