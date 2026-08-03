"""Cairo-aware ORB session phases without changing global session behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from core.egx_session import is_regular_trading_day
from scalping_orb.config import OrbDataConfig


class OrbSessionPhase(str, Enum):
    PRE_SESSION = "PRE_SESSION"
    OPENING_RANGE_BUILDING = "OPENING_RANGE_BUILDING"
    CONTINUOUS_AFTER_OPENING_RANGE = "CONTINUOUS_AFTER_OPENING_RANGE"
    LATE_CONTINUOUS = "LATE_CONTINUOUS"
    CLOSING_AUCTION = "CLOSING_AUCTION"
    POST_MARKET = "POST_MARKET"
    NON_TRADING_DAY = "NON_TRADING_DAY"


CONTINUOUS_PHASES = frozenset(
    {
        OrbSessionPhase.OPENING_RANGE_BUILDING,
        OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE,
        OrbSessionPhase.LATE_CONTINUOUS,
    }
)


def require_aware(value: datetime, field: str = "timestamp") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


@dataclass(frozen=True)
class OrbSessionWindow:
    session_date: date
    continuous_start_utc: datetime
    opening_range_end_utc: datetime
    late_continuous_start_utc: datetime
    continuous_end_utc: datetime
    auction_end_utc: datetime


class OrbSessionClassifier:
    """Classify one aware instant using half-open Cairo exchange intervals."""

    def __init__(self, config: OrbDataConfig | None = None, holidays=None):
        self.config = config or OrbDataConfig()
        self.zone = ZoneInfo(self.config.timezone)
        self.holidays = holidays

    def to_cairo(self, value: datetime) -> datetime:
        return require_aware(value).astimezone(self.zone)

    def classify(self, value: datetime) -> OrbSessionPhase:
        local = self.to_cairo(value)
        if not is_regular_trading_day(local.date(), self.holidays):
            return OrbSessionPhase.NON_TRADING_DAY
        clock = local.timetz().replace(tzinfo=None)
        cfg = self.config
        if clock < cfg.continuous_start:
            return OrbSessionPhase.PRE_SESSION
        if clock < cfg.opening_range_end:
            return OrbSessionPhase.OPENING_RANGE_BUILDING
        if clock < cfg.late_continuous_start:
            return OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE
        if clock < cfg.continuous_end:
            return OrbSessionPhase.LATE_CONTINUOUS
        if clock < cfg.auction_end:
            return OrbSessionPhase.CLOSING_AUCTION
        return OrbSessionPhase.POST_MARKET

    def session_date(self, value: datetime) -> date:
        return self.to_cairo(value).date()

    def window(self, session_date: date) -> OrbSessionWindow:
        def moment(clock: time) -> datetime:
            return datetime.combine(session_date, clock, tzinfo=self.zone).astimezone(
                timezone.utc
            )

        return OrbSessionWindow(
            session_date=session_date,
            continuous_start_utc=moment(self.config.continuous_start),
            opening_range_end_utc=moment(self.config.opening_range_end),
            late_continuous_start_utc=moment(self.config.late_continuous_start),
            continuous_end_utc=moment(self.config.continuous_end),
            auction_end_utc=moment(self.config.auction_end),
        )

    def is_continuous(self, value: datetime) -> bool:
        return self.classify(value) in CONTINUOUS_PHASES
