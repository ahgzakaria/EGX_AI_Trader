"""Rubix supervisor watchdog — verdicts, anti-repeat alerting, feed age.

No test starts or stops the real collector, opens a websocket, authenticates,
takes the supervisor lock, or reads the production database. The clock is
passed in, the supervisor state is passed in, and the only database any test
touches is a temporary one it built itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.rubix_supervisor_watchdog import (
    ALERTING_VERDICTS,
    FEED_STALL_SECONDS,
    SESSION_CLOSE,
    SESSION_OPEN,
    WatchState,
    WatchVerdict,
    alert_message,
    assess,
    in_session,
)
from scripts.watch_rubix_supervisor import feed_age_seconds

CAIRO = ZoneInfo("Africa/Cairo")
WATCHDOG_SOURCE = Path("scripts/watch_rubix_supervisor.py")


def cairo(hour, minute=0, second=0):
    return datetime(2026, 9, 8, hour, minute, second, tzinfo=CAIRO)


def verdict_at(hour, minute=0, *, running=True, age=0.0, seen=True):
    return assess(
        moment=cairo(hour, minute),
        supervisor_running=running,
        feed_age_seconds=age,
        ever_seen_alive=seen,
    )


# --- the session window ------------------------------------------------------

def test_session_window_is_the_egx_regular_session():
    assert (SESSION_OPEN.hour, SESSION_OPEN.minute) == (10, 0)
    assert (SESSION_CLOSE.hour, SESSION_CLOSE.minute) == (14, 30)


@pytest.mark.parametrize("moment, expected", [
    (cairo(9, 59, 59), False),
    (cairo(10, 0, 0), True),
    (cairo(12, 0), True),
    (cairo(14, 30, 0), True),
    (cairo(14, 30, 1), False),
    (cairo(20, 0), False),
])
def test_in_session_boundaries_are_inclusive(moment, expected):
    assert in_session(moment) is expected


def test_watchdog_is_silent_before_the_open_even_with_no_supervisor():
    # The morning start is the assisted-start task's job, not this one's.
    assert verdict_at(9, 30, running=False, seen=False) is WatchVerdict.OUTSIDE_SESSION


def test_watchdog_is_silent_after_the_close_when_the_collector_exits():
    # Documented normal behaviour: the collector exits on its own after close.
    assert verdict_at(15, 30, running=False) is WatchVerdict.OUTSIDE_SESSION


# --- verdicts ----------------------------------------------------------------

def test_live_supervisor_with_a_current_feed_is_healthy():
    assert verdict_at(11, 0) is WatchVerdict.HEALTHY


def test_missing_supervisor_never_seen_alive_is_never_started():
    assert verdict_at(10, 5, running=False, seen=False) is WatchVerdict.NEVER_STARTED


def test_missing_supervisor_after_being_seen_alive_is_a_loss():
    assert verdict_at(11, 30, running=False, seen=True) is WatchVerdict.SUPERVISOR_LOST


def test_a_live_process_with_a_silent_feed_is_a_stall():
    assert verdict_at(11, 30, age=FEED_STALL_SECONDS + 1) is WatchVerdict.FEED_STALLED


def test_a_feed_just_inside_the_stall_limit_is_still_healthy():
    assert verdict_at(11, 30, age=FEED_STALL_SECONDS) is WatchVerdict.HEALTHY


def test_an_unreadable_database_is_not_by_itself_an_alert():
    # A reader failing says something about the reader. Calling the feed dead on
    # that evidence would cry wolf on a locked file or a transient error.
    assert verdict_at(11, 30, age=None) is WatchVerdict.HEALTHY


def test_a_lost_supervisor_outranks_an_unreadable_database():
    assert verdict_at(11, 30, running=False, age=None) is WatchVerdict.SUPERVISOR_LOST


def test_only_the_three_failure_verdicts_alert():
    assert ALERTING_VERDICTS == {
        WatchVerdict.NEVER_STARTED,
        WatchVerdict.SUPERVISOR_LOST,
        WatchVerdict.FEED_STALLED,
    }


# --- anti-repeat -------------------------------------------------------------

def test_one_outage_announces_once_however_long_it_lasts():
    state = WatchState(ever_seen_alive=True)
    announced = [state.observe(WatchVerdict.SUPERVISOR_LOST) for _ in range(500)]
    assert announced.count(True) == 1
    assert announced[0] is True


def test_recovery_rearms_the_alert_so_a_second_outage_is_heard():
    state = WatchState(ever_seen_alive=True)
    assert state.observe(WatchVerdict.FEED_STALLED) is True
    assert state.observe(WatchVerdict.FEED_STALLED) is False
    assert state.observe(WatchVerdict.HEALTHY) is False
    assert state.observe(WatchVerdict.FEED_STALLED) is True


def test_health_is_what_teaches_the_state_the_collector_ever_lived():
    state = WatchState()
    assert state.ever_seen_alive is False
    state.observe(WatchVerdict.HEALTHY)
    assert state.ever_seen_alive is True


def test_a_stall_also_counts_as_having_seen_it_alive():
    # The process answered; a later disappearance is a death, not a no-show.
    state = WatchState()
    state.observe(WatchVerdict.FEED_STALLED)
    assert state.ever_seen_alive is True


def test_a_no_show_that_later_dies_reports_both_conditions():
    state = WatchState()
    assert state.observe(WatchVerdict.NEVER_STARTED) is True
    state.observe(WatchVerdict.HEALTHY)
    assert state.observe(WatchVerdict.SUPERVISOR_LOST) is True


def test_outside_session_polls_never_announce_and_never_clear():
    state = WatchState(ever_seen_alive=True)
    assert state.observe(WatchVerdict.SUPERVISOR_LOST) is True
    assert state.observe(WatchVerdict.OUTSIDE_SESSION) is False
    assert state.observe(WatchVerdict.SUPERVISOR_LOST) is False


# --- the message the operator reads ------------------------------------------

@pytest.mark.parametrize("verdict", sorted(ALERTING_VERDICTS, key=lambda v: v.value))
def test_every_alert_names_the_collector_and_an_action(verdict):
    message = alert_message(verdict, feed_age_seconds=900.0)
    assert "collector" in message.lower()
    assert any(word in message.lower() for word in ("start", "check"))


def test_a_stall_message_carries_the_silence_it_measured():
    assert "900s" in alert_message(WatchVerdict.FEED_STALLED, feed_age_seconds=900.0)


def test_no_alert_message_ever_tells_the_operator_it_restarted_anything():
    for verdict in WatchVerdict:
        assert "restart" not in alert_message(verdict).lower()


# --- feed age ----------------------------------------------------------------

def build_quotes_db(path: Path, received_at):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE quotes (id INTEGER PRIMARY KEY, ticker TEXT, received_at TEXT)"
    )
    if received_at is not None:
        connection.executemany(
            "INSERT INTO quotes (ticker, received_at) VALUES (?, ?)",
            [("COMI", "2026-09-08T06:00:00+00:00"), ("TMGH", received_at)],
        )
    connection.commit()
    connection.close()
    return path


def test_feed_age_is_measured_from_the_newest_row(tmp_path):
    moment = datetime.now(timezone.utc) - timedelta(seconds=42)
    database = build_quotes_db(tmp_path / "q.db", moment.isoformat())
    assert feed_age_seconds(database) == pytest.approx(42, abs=5)


def test_a_naive_timestamp_is_read_as_utc(tmp_path):
    moment = datetime.now(timezone.utc) - timedelta(seconds=10)
    database = build_quotes_db(tmp_path / "q.db", moment.replace(tzinfo=None).isoformat())
    assert feed_age_seconds(database) == pytest.approx(10, abs=5)


def test_a_missing_database_reads_as_unknown_not_as_zero(tmp_path):
    assert feed_age_seconds(tmp_path / "absent.db") is None


def test_an_empty_table_reads_as_unknown(tmp_path):
    assert feed_age_seconds(build_quotes_db(tmp_path / "q.db", None)) is None


def test_an_unparsable_timestamp_reads_as_unknown(tmp_path):
    assert feed_age_seconds(build_quotes_db(tmp_path / "q.db", "not a time")) is None


def test_a_file_that_is_not_a_database_reads_as_unknown(tmp_path):
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not sqlite")
    assert feed_age_seconds(broken) is None


def test_the_watchdog_opens_the_database_read_only():
    # mode=ro is the guard that keeps a reader out of the adapter's sole-writer
    # database; a regression here would be invisible until it corrupted a session.
    source = WATCHDOG_SOURCE.read_text(encoding="utf-8")
    assert "mode=ro" in source
    # The newest row through the rowid, never an aggregate: `received_at` is in
    # no index, so MAX() over it scans 22M rows and looks hung rather than slow.
    assert "SELECT received_at FROM quotes ORDER BY id DESC LIMIT 1" in source


def test_the_watchdog_never_spawns_the_supervisor():
    source = WATCHDOG_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("subprocess", "Popen", "rubix_collector_supervisor"):
        assert forbidden not in source, f"the watchdog must not reference {forbidden}"
