"""Typed, local, fail-closed trading-day boundary for the Shadow orchestrator.

Never touches the network. Resolves from the shared EGX calendar plus explicit
orchestrator overrides, and records the exact calendar identity it used.

Why this exists rather than calling ``is_regular_trading_day`` directly:
``core.egx_session._resolve_holidays`` swallows every exception and returns an
**empty set** when the holiday calendar cannot be loaded. That is a reasonable
default for display code, but for an unattended orchestrator it means a broken
or missing calendar silently makes every weekday a trading day — and the run
would then claim a full session on a day the exchange was shut. This module
fails closed instead: it reports ``CALENDAR_UNAVAILABLE`` and refuses to start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
import hashlib
import json
from pathlib import Path


#: Cairo trading week: Sunday through Thursday. Friday and Saturday are closed.
TRADING_WEEKDAYS = frozenset({0, 1, 2, 3, 6})


class TradingDayStatus(str, Enum):
    """Why a date may or may not host a Shadow session."""

    TRADING_DAY = "TRADING_DAY"
    WEEKEND = "WEEKEND"
    CONFIGURED_HOLIDAY = "CONFIGURED_HOLIDAY"
    EXCEPTIONAL_CLOSURE = "EXCEPTIONAL_CLOSURE"
    #: An explicitly configured session on a day that would otherwise be closed.
    SPECIAL_TRADING_DAY = "SPECIAL_TRADING_DAY"
    #: The calendar could not be resolved. Fail closed; never assume open.
    CALENDAR_UNAVAILABLE = "CALENDAR_UNAVAILABLE"


class CalendarAvailability(str, Enum):
    """Why a calendar is usable, or is not.

    ``CALENDAR_LOAD_FAILED`` and ``CALENDAR_MALFORMED`` both fail closed, but
    they are different operator problems and the report must say which.

    ``VALID_EMPTY_CALENDAR`` exists so a structurally valid calendar that
    genuinely lists no holidays in range is not mistaken for a failed load. The
    distinction cannot be inferred from an empty set alone — it has to be
    asserted by the source — so an unattested empty result stays
    ``CALENDAR_LOAD_FAILED``.
    """

    AVAILABLE = "AVAILABLE"
    VALID_EMPTY_CALENDAR = "VALID_EMPTY_CALENDAR"
    CALENDAR_LOAD_FAILED = "CALENDAR_LOAD_FAILED"
    CALENDAR_MALFORMED = "CALENDAR_MALFORMED"


USABLE_AVAILABILITY = frozenset(
    {CalendarAvailability.AVAILABLE, CalendarAvailability.VALID_EMPTY_CALENDAR}
)


@dataclass(frozen=True)
class TradingDayDecision:
    session_date: date
    status: TradingDayStatus
    reason: str
    calendar_identity: str
    availability: CalendarAvailability = CalendarAvailability.AVAILABLE

    @property
    def is_trading_day(self) -> bool:
        return self.status in (
            TradingDayStatus.TRADING_DAY,
            TradingDayStatus.SPECIAL_TRADING_DAY,
        )


@dataclass(frozen=True)
class ShadowTradingCalendar:
    """One resolved calendar, with a stable identity.

    ``holidays`` and ``exceptional_closures`` are both non-trading; they are kept
    apart because an unscheduled closure is operationally different from a known
    public holiday, and the report should say which it was.
    ``special_trading_days`` opens a date the weekday/holiday rules would close.
    """

    holidays: frozenset[date] = frozenset()
    exceptional_closures: frozenset[date] = frozenset()
    special_trading_days: frozenset[date] = frozenset()
    availability: CalendarAvailability = CalendarAvailability.AVAILABLE
    source: str = "EGX_SHARED_CALENDAR"
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        """Usable for a decision. A *valid* empty calendar still counts."""

        return self.availability in USABLE_AVAILABILITY

    @property
    def identity(self) -> str:
        """Content hash, persisted with every run so a decision is reproducible."""

        payload = {
            "source": self.source,
            "availability": self.availability.value,
            "holidays": sorted(day.isoformat() for day in self.holidays),
            "exceptional_closures": sorted(
                day.isoformat() for day in self.exceptional_closures
            ),
            "special_trading_days": sorted(
                day.isoformat() for day in self.special_trading_days
            ),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32]

    def classify(self, session_date: date) -> TradingDayDecision:
        if not self.available:
            return TradingDayDecision(
                session_date=session_date,
                status=TradingDayStatus.CALENDAR_UNAVAILABLE,
                reason=(
                    f"{self.availability.value}: "
                    f"{self.unavailable_reason or 'calendar could not be resolved'}"
                ),
                calendar_identity=self.identity,
                availability=self.availability,
            )
        # An explicit special session outranks the weekday and holiday rules,
        # because it is the most specific statement anyone has made about the day.
        if session_date in self.special_trading_days:
            return TradingDayDecision(
                session_date, TradingDayStatus.SPECIAL_TRADING_DAY,
                "explicitly configured special trading day", self.identity,
            )
        if session_date in self.exceptional_closures:
            return TradingDayDecision(
                session_date, TradingDayStatus.EXCEPTIONAL_CLOSURE,
                "configured exceptional closure", self.identity,
            )
        if session_date in self.holidays:
            return TradingDayDecision(
                session_date, TradingDayStatus.CONFIGURED_HOLIDAY,
                "configured EGX holiday", self.identity,
            )
        if session_date.weekday() not in TRADING_WEEKDAYS:
            return TradingDayDecision(
                session_date, TradingDayStatus.WEEKEND,
                f"weekday {session_date.weekday()} is not a Cairo trading weekday",
                self.identity,
            )
        return TradingDayDecision(
            session_date, TradingDayStatus.TRADING_DAY,
            "regular Cairo trading weekday", self.identity,
        )


def _load_overrides(path: Path | None) -> tuple[frozenset[date], frozenset[date]]:
    """Optional local JSON: exceptional closures and special trading days."""

    if path is None:
        return frozenset(), frozenset()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    def _dates(key: str) -> frozenset[date]:
        return frozenset(
            date.fromisoformat(str(value)[:10]) for value in payload.get(key, []) or []
        )

    return _dates("exceptional_closures"), _dates("special_trading_days")


def load_trading_calendar(
    *,
    overrides_path: Path | None = None,
    holidays: frozenset[date] | None = None,
    require_holidays: bool = True,
    allow_empty_calendar: bool = False,
) -> ShadowTradingCalendar:
    """Resolve the calendar locally, or return an explicitly unavailable one.

    ``require_holidays`` keeps the fail-closed promise honest: an empty holiday
    set is indistinguishable from "the calendar failed to load" in the shared
    helper, so an empty result is treated as unavailable unless a caller has
    deliberately passed an empty set.
    """

    exceptional: frozenset[date] = frozenset()
    special: frozenset[date] = frozenset()
    if overrides_path is not None:
        try:
            exceptional, special = _load_overrides(overrides_path)
        except (OSError, ValueError, TypeError) as error:
            return ShadowTradingCalendar(
                availability=CalendarAvailability.CALENDAR_MALFORMED,
                source=f"OVERRIDES:{overrides_path}",
                unavailable_reason=f"calendar overrides unreadable: {error}",
            )

    if holidays is not None:
        # An explicit set is an assertion by the caller, so an empty one is a
        # *valid* empty calendar rather than an ambiguous load result.
        resolved_explicit = frozenset(holidays)
        return ShadowTradingCalendar(
            holidays=resolved_explicit,
            exceptional_closures=exceptional,
            special_trading_days=special,
            availability=(
                CalendarAvailability.AVAILABLE
                if resolved_explicit
                else CalendarAvailability.VALID_EMPTY_CALENDAR
            ),
            source="EXPLICIT",
        )

    try:
        from core.egx_calendar import holiday_dates

        resolved = frozenset(holiday_dates())
    except Exception as error:  # pragma: no cover - defensive
        return ShadowTradingCalendar(
            availability=CalendarAvailability.CALENDAR_LOAD_FAILED,
            source="EGX_SHARED_CALENDAR",
            unavailable_reason=f"shared EGX calendar unavailable: {error}",
        )

    if not resolved:
        if allow_empty_calendar:
            # The operator has asserted that an empty calendar is genuine, so
            # it becomes a *valid* empty calendar rather than a failed load.
            return ShadowTradingCalendar(
                exceptional_closures=exceptional,
                special_trading_days=special,
                availability=CalendarAvailability.VALID_EMPTY_CALENDAR,
                source="EGX_SHARED_CALENDAR",
            )
        if require_holidays:
            # The shared helper returns an empty set both when there genuinely
            # are no holidays and when loading failed, and it swallows the
            # exception that would tell them apart. Unattended, that ambiguity
            # must not resolve to "the market is open".
            return ShadowTradingCalendar(
                availability=CalendarAvailability.CALENDAR_LOAD_FAILED,
                source="EGX_SHARED_CALENDAR",
                unavailable_reason=(
                    "shared EGX calendar returned no holidays; cannot distinguish "
                    "a genuinely empty calendar from a failed load. Pass "
                    "--allow-empty-calendar to assert it is genuinely empty."
                ),
            )

    return ShadowTradingCalendar(
        holidays=resolved,
        exceptional_closures=exceptional,
        special_trading_days=special,
        source="EGX_SHARED_CALENDAR",
    )


__all__ = [
    "TRADING_WEEKDAYS",
    "USABLE_AVAILABILITY",
    "CalendarAvailability",
    "ShadowTradingCalendar",
    "TradingDayDecision",
    "TradingDayStatus",
    "load_trading_calendar",
]
