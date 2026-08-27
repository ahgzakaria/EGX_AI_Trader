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
