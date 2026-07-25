"""Phase 1 — auction-aware completed-session freshness tests.

Verifies that a correct previous-session daily candle is NEVER labelled stale
during a forming today session, and that a not-yet-published today candle is
distinguished from genuinely lagging history. EGX Cairo: continuous 10:00-14:15,
auction 14:15-14:25, Fri/Sat + configured holidays non-trading.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.egx_session import (
    HISTORY_CURRENT,
    HISTORY_STALE,
    HISTORY_UNAVAILABLE,
    PROVIDER_FINALIZATION_PENDING,
    TODAY_CANDLE_NOT_YET_COMPLETE,
    classify_history_freshness,
    expected_latest_completed_session,
)

CAIRO = ZoneInfo("Africa/Cairo")

# 2026-07-16 Thu, 17 Fri, 18 Sat, 19 Sun, 20 Mon, 21 Tue, 22 Wed (all Cairo).
MON = date(2026, 7, 20)
TUE = date(2026, 7, 21)
THU = date(2026, 7, 16)


def _now(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=CAIRO)


def test_before_open_previous_session_is_current():
    f = classify_history_freshness(MON, now=_now(2026, 7, 21, 9, 0))
    assert f.is_current and f.status in (HISTORY_CURRENT, TODAY_CANDLE_NOT_YET_COMPLETE)
    assert f.lag_sessions == 0


def test_during_continuous_previous_session_not_stale():
    # THE key fix: 2026-07-20 is current during the 2026-07-21 session.
    f = classify_history_freshness(MON, now=_now(2026, 7, 21, 11, 0))
    assert f.status == TODAY_CANDLE_NOT_YET_COMPLETE
    assert f.is_current is True
    assert f.session_phase == "CONTINUOUS"


def test_during_auction_previous_session_not_stale():
    f = classify_history_freshness(MON, now=_now(2026, 7, 21, 14, 20))
    assert f.status == TODAY_CANDLE_NOT_YET_COMPLETE
    assert f.session_phase == "CLOSING_AUCTION"
    assert f.is_current is True


def test_after_auction_today_candle_unavailable_is_provider_pending():
    f = classify_history_freshness(MON, now=_now(2026, 7, 21, 15, 0),
                                   provider_finalized=False)
    assert f.status == PROVIDER_FINALIZATION_PENDING
    assert f.provider_publication_pending is True
    assert f.is_current is True          # previous session still usable, not stale


def test_after_auction_finalized_candle_available_is_current():
    f = classify_history_freshness(TUE, now=_now(2026, 7, 21, 15, 0),
                                   provider_finalized=True)
    assert f.status == HISTORY_CURRENT
    assert f.lag_sessions == 0


def test_sunday_after_thursday_session_is_current():
    # Sunday 2026-07-19 pre/mid-session, latest completed is Thursday 2026-07-16.
    f = classify_history_freshness(THU, now=_now(2026, 7, 19, 9, 0))
    assert f.is_current is True
    assert f.status in (HISTORY_CURRENT, TODAY_CANDLE_NOT_YET_COMPLETE)


def test_friday_is_non_trading_and_current():
    f = classify_history_freshness(THU, now=_now(2026, 7, 17, 11, 0))
    assert f.session_phase == "WEEKEND"
    assert f.status == HISTORY_CURRENT
    assert f.is_current is True


def test_saturday_is_non_trading_and_current():
    f = classify_history_freshness(THU, now=_now(2026, 7, 18, 11, 0))
    assert f.session_phase == "WEEKEND"
    assert f.is_current is True


def test_configured_holiday_treated_non_trading():
    # 2026-07-21 configured as a holiday -> latest completed is 2026-07-20.
    f = classify_history_freshness(MON, now=_now(2026, 7, 21, 11, 0),
                                   holidays=["2026-07-21"])
    assert f.session_phase == "HOLIDAY"
    assert f.status == HISTORY_CURRENT
    assert f.is_current is True


def test_genuinely_stale_history_flagged():
    # Wednesday 2026-07-22 mid-session: expected completed is Tuesday 2026-07-21;
    # holding only Monday 2026-07-20 is genuinely one session behind.
    f = classify_history_freshness(MON, now=_now(2026, 7, 22, 12, 0))
    assert f.status == HISTORY_STALE
    assert f.lag_sessions == 1
    assert f.is_current is False


def test_deeply_stale_history_counts_sessions():
    f = classify_history_freshness(THU, now=_now(2026, 7, 22, 12, 0))
    assert f.status == HISTORY_STALE
    assert f.lag_sessions >= 3           # Thu->Fri(x)->...->Mon,Tue expected
    assert f.is_current is False


def test_unavailable_history():
    f = classify_history_freshness(None, now=_now(2026, 7, 21, 11, 0))
    assert f.status == HISTORY_UNAVAILABLE
    assert f.is_current is False


def test_expected_latest_completed_session_during_session():
    # During the 2026-07-21 session the expected completed candle is 2026-07-20.
    assert expected_latest_completed_session(now=_now(2026, 7, 21, 11, 0)) == MON


def test_expected_latest_completed_session_after_finalization():
    assert expected_latest_completed_session(
        now=_now(2026, 7, 21, 15, 0), provider_finalized=True) == TUE
