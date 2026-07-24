"""EGX trading-calendar facade — one shared holiday/session source for the app.

This is a thin, backward-compatible facade over the JSON-backed
``core.calendar.egx_calendar_service``. Every session-aware module calls these
functions (directly, or via ``core.egx_session`` which defaults to them), so the
dynamic calendar (official sync + manual approval, all stored in
``data/calendar/*.json``) flows everywhere without code edits.

Holidays are *configured*, never guessed from calendar-day age. Operator-configured
dates in ``settings['rubix_daily_bridge']['holidays']`` remain honored (merged in)
for backward compatibility. This module owns the holiday/session dimension only and
never touches signal, indicator, ranking, scoring, TP/SL, provider, or execution
logic.
"""

from __future__ import annotations

from datetime import date, datetime

from core.calendar.egx_calendar_service import service

_CONFIGURED_HOLIDAY_LABEL = "Configured EGX holiday"


def _to_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _configured_holidays():
    """Legacy operator holidays from settings (merged for backward compatibility)."""
    out = {}
    try:
        from config.settings_manager import settings
        raw = (settings.get("rubix_daily_bridge") or {}).get("holidays", []) or []
    except Exception:
        raw = []
    for item in raw:
        day = _to_date(item)
        if day is not None:
            out.setdefault(day, _CONFIGURED_HOLIDAY_LABEL)
    return out


def holiday_calendar() -> dict:
    """Merged {date: holiday_name} from the JSON calendar + legacy config."""
    calendar = dict(_configured_holidays())
    for d in service().holiday_dates():
        calendar[d] = service().holiday_name(d) or calendar.get(d, "EGX holiday")
    return calendar


def holiday_dates() -> set:
    """Set of confirmed EGX full-day/exceptional closure dates (non-trading)."""
    return set(service().holiday_dates()) | set(_configured_holidays())


def is_official_holiday(day) -> bool:
    """True when ``day`` is a confirmed/official EGX full-day closure."""
    return _to_date(day) in holiday_dates()


def holiday_name(day):
    """Holiday name for ``day``, or None when it is not a confirmed holiday."""
    resolved = _to_date(day)
    return service().holiday_name(resolved) or (
        _CONFIGURED_HOLIDAY_LABEL if resolved in _configured_holidays() else None)


def effective_holidays() -> tuple:
    """Sorted tuple of every confirmed EGX holiday date — the one shared list."""
    return tuple(sorted(holiday_dates()))


# --- rich session status (dynamic calendar) ---------------------------------


def session_status(now=None):
    """Full ``SessionStatus`` for a moment (confirmed/pending/uncertain/partial)."""
    return service().session_status(now)


def review_required(now=None) -> bool:
    """True when the day needs manual calendar review (pending/uncertain closure)."""
    return bool(service().session_status(now).review_required)


def session_is_uncertain(now=None) -> bool:
    """True when the session status is pending-review or uncertain (safe-gate)."""
    return service().session_status(now).status in (
        "HOLIDAY_PENDING_REVIEW", "SESSION_STATUS_UNCERTAIN")


def next_trading_session(day):
    """Next regular EGX trading date after ``day`` (calendar-aware)."""
    return service().next_trading_session(day)
