"""EGX trading-calendar holiday classification tests (2026-07-23 Revolution Day).

Deterministic: a fixed Cairo datetime on the holiday is injected. No live feed,
database, or scheduled process is touched. Calendar/session logic only — never
strategy, thresholds, scoring, or execution.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import core.egx_calendar as cal
import core.egx_session as s

CAIRO = ZoneInfo("Africa/Cairo")
HOLIDAY = date(2026, 7, 23)          # 23 July Revolution Day (Thursday)
PREV_SESSION = date(2026, 7, 22)     # Wednesday
NEXT_SESSION = date(2026, 7, 26)     # Sunday (Fri/Sat are the EGX weekend)


def _at(hh, mm):
    return datetime(2026, 7, 23, hh, mm, tzinfo=CAIRO)


# --- centralized calendar ----------------------------------------------------

def test_revolution_day_is_official_holiday():
    assert cal.is_official_holiday(HOLIDAY) is True
    assert cal.holiday_name(HOLIDAY) == "23 July Revolution Day"
    assert HOLIDAY in cal.holiday_dates()
    assert HOLIDAY in cal.effective_holidays()


def test_regular_weekday_is_not_a_holiday():
    assert cal.is_official_holiday(date(2026, 7, 22)) is False
    assert cal.holiday_name(date(2026, 7, 22)) is None


# --- session classification --------------------------------------------------

def test_holiday_classified_holiday_not_open():
    assert s.egx_session_phase(_at(11, 45)) == "HOLIDAY"
    assert s.is_regular_trading_day(HOLIDAY) is False


def test_no_open_phase_at_1030_on_holiday():
    # 10:30 is inside regular hours, but a holiday must never be OPEN
    assert s.egx_session_phase(_at(10, 30)) == "HOLIDAY"
    assert s._auction_phase(_at(10, 30)) == "HOLIDAY"


def test_no_auction_phase_at_1420_on_holiday():
    # 14:20 is inside the closing-auction window on a trading day, but not on a holiday
    assert s._auction_phase(_at(14, 20)) == "HOLIDAY"
    assert s.egx_session_phase(_at(14, 20)) == "HOLIDAY"


def test_next_trading_session_is_2026_07_26():
    assert s.next_trading_session(HOLIDAY) == NEXT_SESSION
    # weekend transition too: Friday 2026-07-24 -> Sunday 2026-07-26
    assert s.next_trading_session(date(2026, 7, 24)) == NEXT_SESSION


def test_previous_trading_date_skips_holiday_and_weekend():
    assert s.previous_trading_date(HOLIDAY) == PREV_SESSION
    # Sunday 2026-07-26's previous session skips Fri/Sat and the 07-23 holiday
    assert s.previous_trading_date(NEXT_SESSION) == PREV_SESSION


# --- history freshness on the holiday ---------------------------------------

def test_latest_completed_session_is_prev_day_no_false_lag():
    now = _at(11, 45)
    assert s.expected_latest_session_date(now) == PREV_SESSION
    assert s.expected_latest_completed_session(now) == PREV_SESSION
    # a 07-22 exchange date must be zero sessions behind on the holiday
    assert s.trading_session_lag(PREV_SESSION, now) == 0


def test_prev_session_history_is_current_on_holiday():
    hf = s.classify_history_freshness("2026-07-22", now=_at(11, 45))
    assert hf.status == "HISTORY_CURRENT"
    assert hf.is_current is True
    assert hf.session_phase == "HOLIDAY"
    assert hf.lag_sessions == 0


def test_holiday_quote_is_usable_not_stale():
    # a last-session (07-22) quote on the holiday is a usable snapshot, not "stale"
    f = s.assess_quote_freshness(
        "2026-07-22T19:11:11+00:00", "2026-07-22T19:11:11+00:00", value=_at(11, 45))
    assert f.usable is True
    assert f.phase == "HOLIDAY"
    assert f.session_lag == 0
    assert f.reason is None            # no degraded-feed warning on a holiday


# --- scheduled-task gating building blocks ----------------------------------

def test_pre_session_gate_is_non_trading_on_holiday():
    # the pre-session snapshot writes NON_TRADING_DAY exactly when this gate is False
    assert s.is_regular_trading_day(HOLIDAY, cal.effective_holidays()) is False


def test_daily_finalizer_gate_non_trading_on_holiday():
    now = _at(11, 45)
    hol = cal.effective_holidays()
    assert s.is_regular_trading_day(HOLIDAY, hol) is False
    assert s.session_is_completed(HOLIDAY, value=now, holidays=hol) is False


def test_holiday_banner_reports_on_holiday(monkeypatch):
    # egx_holiday_banner renders and returns True on the holiday, False otherwise
    import dashboard.ui as ui
    calls = []
    monkeypatch.setattr(ui.st, "markdown", lambda *a, **k: calls.append(a))
    assert ui.egx_holiday_banner(now=_at(11, 45)) is True
    assert calls, "banner should have rendered markdown"
    assert ui.egx_holiday_banner(now=datetime(2026, 7, 22, 11, 45, tzinfo=CAIRO)) is False
