"""EGX session-aware market-data freshness helpers.

These helpers affect provider status and launcher readiness only.  They do not
participate in signal generation, indicators, ranking, or backtest logic.
The regular EGX session is treated as Sunday-Thursday, 10:00-14:30 Cairo.
Exchange holidays remain provider/operations events and are reported rather
than guessed from calendar-day age.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


CAIRO = ZoneInfo("Africa/Cairo")
TRADING_WEEKDAYS = {0, 1, 2, 3, 6}  # Monday-Thursday and Sunday.
REGULAR_OPEN = time(10, 0)
REGULAR_CLOSE = time(14, 30)
OPENING_GRACE_MINUTES = 10


@dataclass(frozen=True)
class SessionFreshness:
    """Explain whether a quote is usable for the current EGX session phase."""

    usable: bool
    phase: str
    session_lag: int
    reason: str | None = None


def cairo_now(value=None):
    """Normalize an optional aware/naive datetime to Cairo local time."""

    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(CAIRO)


def _resolve_holidays(holidays):
    """Normalize an explicit holiday iterable, or default to the shared EGX calendar.

    ``holidays=None`` (the default for session helpers) resolves to the centralized
    ``core.egx_calendar`` so every module is holiday-aware without threading the list
    everywhere. An explicit iterable (even empty) is used as given.
    """
    if holidays is None:
        try:
            from core.egx_calendar import holiday_dates
            return holiday_dates()
        except Exception:
            return set()
    return _as_holiday_set(holidays)


def egx_session_phase(value=None, holidays=None):
    """Return HOLIDAY, WEEKEND, PRE_OPEN, OPEN, or POST_CLOSE for regular EGX hours.

    Calendar validity (weekend + official holiday) is checked BEFORE any time-of-day
    logic, so a configured holiday is never reported OPEN merely because the clock is
    between 10:00 and 14:30. Holidays default to the shared EGX calendar.
    """

    current = cairo_now(value)
    if current.weekday() not in TRADING_WEEKDAYS:
        return "WEEKEND"
    if current.date() in _resolve_holidays(holidays):
        return "HOLIDAY"
    local_time = current.timetz().replace(tzinfo=None)
    if local_time < REGULAR_OPEN:
        return "PRE_OPEN"
    if local_time <= REGULAR_CLOSE:
        return "OPEN"
    return "POST_CLOSE"


def previous_trading_date(value, holidays=None):
    """Return the previous regular EGX trading date (weekends + holidays excluded).

    ``holidays`` defaults to the shared EGX calendar; pass an explicit iterable to
    override. Existing callers that pass a date only keep working.
    """

    holiday_set = _resolve_holidays(holidays)
    candidate = value - timedelta(days=1)
    while candidate.weekday() not in TRADING_WEEKDAYS or candidate in holiday_set:
        candidate -= timedelta(days=1)
    return candidate


def next_trading_session(value, holidays=None):
    """Return the next regular EGX trading date strictly after ``value``.

    Skips weekends and official/configured holidays (defaults to the shared EGX
    calendar). e.g. next_trading_session(2026-07-23) == 2026-07-26.
    """

    holiday_set = _resolve_holidays(holidays)
    candidate = value + timedelta(days=1)
    while candidate.weekday() not in TRADING_WEEKDAYS or candidate in holiday_set:
        candidate += timedelta(days=1)
    return candidate


def expected_latest_session_date(value=None, holidays=None):
    """Return the session date expected to have produced data by now.

    Before the opening bell, on weekends, and on official holidays this is the
    previous regular session.  Once the market opens on a trading day it is today.
    Holidays default to the shared EGX calendar, so a holiday never makes today the
    expected session (which would fabricate a one-session lag).
    """

    current = cairo_now(value)
    phase = egx_session_phase(current, holidays)
    if phase in {"PRE_OPEN", "WEEKEND", "HOLIDAY"}:
        return previous_trading_date(current.date(), holidays)
    return current.date()


def trading_session_lag(exchange_date, value=None, holidays=None):
    """Count expected regular EGX sessions after an exchange date (holiday-aware)."""

    expected = expected_latest_session_date(value, holidays)
    if exchange_date >= expected:
        return 0
    holiday_set = _resolve_holidays(holidays)
    lag = 0
    candidate = exchange_date + timedelta(days=1)
    while candidate <= expected:
        if candidate.weekday() in TRADING_WEEKDAYS and candidate not in holiday_set:
            lag += 1
        candidate += timedelta(days=1)
    return lag


def is_regular_trading_day(day, holidays=None):
    """Return True when ``day`` is a regular EGX session date.

    Weekends (Friday/Saturday) and holiday dates are not trading days.  Holidays
    default to the shared EGX calendar (``core.egx_calendar``); pass an explicit
    iterable to override. This helper is disclosure/aggregation support only and
    never touches signal, indicator, ranking, or backtest logic.
    """

    if day.weekday() not in TRADING_WEEKDAYS:
        return False
    return day not in _resolve_holidays(holidays)


def session_close_datetime(day, *, early_closes=None):
    """Return the tz-aware Cairo close datetime for a trading ``day``.

    ``early_closes`` is an optional mapping of ``date -> datetime.time`` for
    explicitly-configured shortened sessions.  Absent an override the regular
    14:30 Cairo close is used.
    """

    close_time = REGULAR_CLOSE
    if early_closes:
        override = early_closes.get(day)
        if override is not None:
            close_time = override
    return datetime.combine(day, close_time, tzinfo=CAIRO)


def session_is_completed(
    trading_date,
    *,
    value=None,
    close_safety_minutes=15,
    holidays=None,
    early_closes=None,
):
    """Return True only when ``trading_date``'s EGX session is safely finished.

    A session is *completed* — and therefore eligible to contribute a frozen
    daily candle — only when all of the following hold:

    * ``trading_date`` is a regular trading day (not weekend/holiday), and
    * the current Cairo time is at or past the session close plus a configurable
      safety delay, so no further legitimate writes for that date are expected.

    A currently-forming session (today, before close+safety) always returns
    False.  Completion is derived from the exchange calendar and clock together,
    never from calendar-day age alone.
    """

    if not is_regular_trading_day(trading_date, holidays):
        return False
    now = cairo_now(value)
    close = session_close_datetime(trading_date, early_closes=early_closes)
    safety = timedelta(minutes=max(0.0, float(close_safety_minutes)))
    return now >= close + safety


def latest_completed_session_date(
    *,
    value=None,
    close_safety_minutes=15,
    holidays=None,
    early_closes=None,
    lookback_days=15,
):
    """Return the most recent trading date whose session is safely completed."""

    now = cairo_now(value)
    candidate = now.date()
    for _ in range(max(1, int(lookback_days))):
        if session_is_completed(
            candidate,
            value=now,
            close_safety_minutes=close_safety_minutes,
            holidays=holidays,
            early_closes=early_closes,
        ):
            return candidate
        candidate -= timedelta(days=1)
    return None


def _as_holiday_set(holidays):
    """Normalize a holiday iterable of dates/ISO strings into a set of dates."""

    result = set()
    for item in holidays or ():
        if isinstance(item, datetime):
            result.add(item.date())
        elif isinstance(item, date):
            result.add(item)
        else:
            try:
                result.add(date.fromisoformat(str(item)[:10]))
            except ValueError:
                continue
    return result


def assess_quote_freshness(
    received_at,
    exchange_at,
    *,
    value=None,
    open_stale_after_minutes=5,
):
    """Assess a delayed quote without treating closed-market time as staleness."""

    current = cairo_now(value)
    received = _as_datetime(received_at)
    exchange = _as_datetime(exchange_at)
    phase = egx_session_phase(current)
    lag = trading_session_lag(exchange.astimezone(CAIRO).date(), current)

    if phase == "OPEN":
        minutes_after_open = (
            datetime.combine(current.date(), current.timetz().replace(tzinfo=None))
            - datetime.combine(current.date(), REGULAR_OPEN)
        ).total_seconds() / 60
        # Opening auctions and the first prints can arrive shortly after 10:00.
        if minutes_after_open <= OPENING_GRACE_MINUTES and lag <= 1:
            return SessionFreshness(True, phase, lag, "waiting for first session print")
        age_minutes = max(
            0.0,
            (current.astimezone(timezone.utc) - received.astimezone(timezone.utc)).total_seconds()
            / 60,
        )
        usable = age_minutes <= max(1 / 60, float(open_stale_after_minutes)) and lag == 0
        reason = None if usable else (
            f"latest quote is {age_minutes:.1f} minutes old during the open session"
        )
        return SessionFreshness(usable, phase, lag, reason)

    usable = lag == 0
    reason = None if usable else f"latest quote is {lag} completed EGX session(s) behind"
    return SessionFreshness(usable, phase, lag, reason)


def _as_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --- auction-aware completed-daily-session freshness (disclosure only) -------
#
# These helpers describe the *completed daily candle* a downstream selector may
# safely consume. They participate in no signal, indicator, ranking or backtest
# logic. EGX regular phases (Cairo): continuous 10:00-14:15, closing auction
# 14:15-14:25; Friday/Saturday and configured holidays are non-trading.

CONTINUOUS_CLOSE = time(14, 15)
AUCTION_END = time(14, 25)

# History-freshness statuses.
HISTORY_CURRENT = "HISTORY_CURRENT"
TODAY_CANDLE_NOT_YET_COMPLETE = "TODAY_CANDLE_NOT_YET_COMPLETE"
PROVIDER_FINALIZATION_PENDING = "PROVIDER_FINALIZATION_PENDING"
HISTORY_STALE = "HISTORY_STALE"
HISTORY_UNAVAILABLE = "HISTORY_UNAVAILABLE"


@dataclass(frozen=True)
class HistoryFreshness:
    """Whether the latest available completed daily candle is current."""

    status: str
    latest_available: str | None
    latest_expected_completed: str | None
    lag_sessions: int
    session_phase: str            # PRE_OPEN / CONTINUOUS / CLOSING_AUCTION / POST_AUCTION / WEEKEND / HOLIDAY
    today_session_complete: bool
    provider_publication_pending: bool
    is_current: bool              # True when the data is usable / not stale
    note: str = ""


def _auction_phase(now_cairo, holidays=None):
    """Return the EGX phase for a Cairo datetime (auction-aware, holiday-aware)."""

    if not is_regular_trading_day(now_cairo.date(), holidays):
        return "HOLIDAY" if now_cairo.weekday() in TRADING_WEEKDAYS else "WEEKEND"
    t = now_cairo.timetz().replace(tzinfo=None)
    if t < REGULAR_OPEN:
        return "PRE_OPEN"
    if t < CONTINUOUS_CLOSE:
        return "CONTINUOUS"
    if t < AUCTION_END:
        return "CLOSING_AUCTION"
    return "POST_AUCTION"


def last_completed_exchange_session(now=None, holidays=None):
    """Most recent trading date whose full session (incl. auction) has finished."""

    current = cairo_now(now)
    today = current.date()
    today_trading = is_regular_trading_day(today, holidays)
    today_complete = today_trading and current.timetz().replace(tzinfo=None) >= AUCTION_END
    if today_complete:
        return today
    return previous_trading_date(today, holidays)


#: Minutes after the closing auction before today's session is treated as the
#: authoritative completed session. It covers settlement and the provider's
#: normal publication delay; it is a data-availability allowance, not a
#: strategy parameter, and no indicator, score or decision rule reads it.
SETTLEMENT_GRACE_MINUTES = 120

SETTLEMENT_GRACE_SETTING_KEY = "egx_settlement_grace_minutes"


def settlement_grace_minutes(settings_data=None):
    """Configured settlement grace, falling back to the default."""

    if settings_data is None:
        try:
            from config.settings_manager import settings

            settings_data = settings.data
        except Exception:
            return float(SETTLEMENT_GRACE_MINUTES)
    try:
        value = float((settings_data or {}).get(
            SETTLEMENT_GRACE_SETTING_KEY, SETTLEMENT_GRACE_MINUTES))
    except (TypeError, ValueError):
        return float(SETTLEMENT_GRACE_MINUTES)
    return value if value >= 0 else float(SETTLEMENT_GRACE_MINUTES)


def authoritative_completed_session(now=None, holidays=None, grace_minutes=None):
    """The latest EGX session that is definitively complete, in Cairo terms.

    This answers "which trading session has finished", which is the floor a
    daily candle is measured against. It is deliberately NOT
    ``expected_latest_completed_session``: that one answers a different
    question - "which candle should a provider have published by now" - and
    withholds today until publication is confirmed.

    Reusing the provider question as the exchange question is what classified
    real, completed 2026-08-04 candles as FUTURE_DATE at 21:56 Cairo, hours
    after the auction closed. A candle dated today, after today's session has
    finished, is current data; a provider that has not published it yet makes
    that symbol *stale*, never the exchange calendar wrong.

    Today counts only once the closing auction has ended AND the settlement
    grace has elapsed. Cairo wall clock, Sunday-Thursday, holiday aware; never
    a UTC date and never a generic Monday-Friday business day.
    """

    current = cairo_now(now)
    today = current.date()
    grace = (settlement_grace_minutes() if grace_minutes is None
             else float(grace_minutes))
    if is_regular_trading_day(today, holidays):
        auction_end = datetime.combine(today, AUCTION_END, tzinfo=current.tzinfo)
        if current >= auction_end + timedelta(minutes=grace):
            return today
    return previous_trading_date(today, holidays)


def expected_latest_completed_session(now=None, provider_finalized=None, holidays=None):
    """Return the completed daily session a provider is expected to have published.

    ``provider_finalized`` (tri-state): True if the provider has finalized today's
    just-completed candle, False if it is known pending, None if unknown. Today's
    candle is only *expected* once its session has completed AND the provider has
    published it; otherwise the previous completed session is the expectation.
    """

    current = cairo_now(now)
    exchange_latest = last_completed_exchange_session(current, holidays)
    today = current.date()
    if exchange_latest == today:
        # Today's exchange session is done. The daily candle is expected only when
        # the provider has actually finalized/published it.
        if provider_finalized is True:
            return today
        return previous_trading_date(today, holidays)
    return exchange_latest


def classify_history_freshness(available_date, now=None, provider_finalized=None,
                               holidays=None):
    """Classify the latest available completed candle against EGX expectations.

    Never labels a correct previous-session candle as stale during a forming
    today session; distinguishes a not-yet-published today candle
    (PROVIDER_FINALIZATION_PENDING) from genuinely lagging history (HISTORY_STALE).
    """

    current = cairo_now(now)
    phase = _auction_phase(current, holidays)
    today = current.date()
    today_trading = is_regular_trading_day(today, holidays)
    today_complete = today_trading and current.timetz().replace(tzinfo=None) >= AUCTION_END
    exchange_latest = last_completed_exchange_session(current, holidays)

    available = _as_date(available_date)
    if available is None:
        return HistoryFreshness(
            HISTORY_UNAVAILABLE, None,
            exchange_latest.isoformat() if exchange_latest else None, -1, phase,
            today_complete, False, False, "no completed daily history available")

    prev_of_exchange = previous_trading_date(exchange_latest, holidays)

    # Current: we hold the most recent completed exchange session (or newer).
    if available >= exchange_latest:
        if exchange_latest == today and today_complete:
            status, note, current_flag = HISTORY_CURRENT, "today's finalized candle present", True
        elif today_trading and not today_complete:
            status = TODAY_CANDLE_NOT_YET_COMPLETE
            note = "latest completed session held; today's candle not due until after 14:25 Cairo"
            current_flag = True
        else:
            status, note, current_flag = HISTORY_CURRENT, "latest completed session held", True
        return HistoryFreshness(
            status, available.isoformat(), exchange_latest.isoformat(), 0, phase,
            today_complete, False, current_flag, note)

    # Exactly one session behind the latest completed exchange session.
    if available == prev_of_exchange:
        if exchange_latest == today and today_complete:
            # The only thing missing is today's just-completed candle.
            return HistoryFreshness(
                PROVIDER_FINALIZATION_PENDING, available.isoformat(),
                exchange_latest.isoformat(), 1, phase, today_complete, True, True,
                "today's session complete but provider has not finalized the daily candle yet")
        return HistoryFreshness(
            HISTORY_STALE, available.isoformat(), exchange_latest.isoformat(), 1, phase,
            today_complete, False, False, "history is one completed session behind")

    lag = _sessions_between(available, exchange_latest, holidays)
    return HistoryFreshness(
        HISTORY_STALE, available.isoformat(), exchange_latest.isoformat(), lag, phase,
        today_complete, False, False, f"history is {lag} completed sessions behind")


def _sessions_between(start_date, end_date, holidays=None):
    """Count regular trading sessions strictly after start up to end (inclusive)."""

    if start_date >= end_date:
        return 0
    lag = 0
    candidate = start_date + timedelta(days=1)
    holiday_set = _resolve_holidays(holidays)
    while candidate <= end_date:
        if candidate.weekday() in TRADING_WEEKDAYS and candidate not in holiday_set:
            lag += 1
        candidate += timedelta(days=1)
    return lag


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
