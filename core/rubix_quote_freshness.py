"""Shared, phase-aware Rubix quote freshness. Pure: no clock, no I/O.

The previous rule returned ``RUBIX_FRESH`` whenever a quote's session equalled
the latest completed EGX session and the market was not open. On 2026-08-04 a
quote stamped 2026-08-03 14:30 Cairo was therefore labelled ``FRESH`` at
00:53 Cairo the next morning, 4.45 hours old, above a 2026-07-30 daily candle.
Nothing malfunctioned: the rule conflated "belongs to the most recent session"
with "is live", and the elapsed-age check only ever ran inside the OPEN branch.

Session membership and exchange phase decide here; age is a check *within* the
permitted session, never a substitute for it. Every input is passed in - the
evaluation instant included - so the same quote classifies deterministically
whenever it is replayed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import Enum


class RubixQuoteStatus(str, Enum):
    """What a Rubix quote is, in the only terms the UI may use."""

    #: Verified, in the current session, and the phase permits a live quote.
    RUBIX_LIVE_CURRENT = "RUBIX_LIVE_CURRENT"
    #: Belongs to the current/latest session but the market is closed, or it is
    #: a closing observation. Displayable as a reference. NEVER "Live".
    RUBIX_CURRENT_SESSION_LAST = "RUBIX_CURRENT_SESSION_LAST"
    #: Belongs to an exchange session before the permitted one.
    RUBIX_PREVIOUS_SESSION = "RUBIX_PREVIOUS_SESSION"
    #: Right session, but receive/market freshness exceeds the budget.
    RUBIX_STALE = "RUBIX_STALE"
    #: No usable quote at all.
    RUBIX_UNAVAILABLE = "RUBIX_UNAVAILABLE"
    #: No verified mapping from the operational symbol to a Rubix ticker.
    RUBIX_UNMAPPED = "RUBIX_UNMAPPED"
    #: Missing, malformed, future or contradictory timestamp evidence.
    #: Deliberately NOT collapsed into UNAVAILABLE: "we have no quote" and
    #: "we have a quote we cannot trust" call for different operator action.
    RUBIX_TIMESTAMP_INVALID = "RUBIX_TIMESTAMP_INVALID"


#: Operator-facing wording. A generic green "Fresh" is deliberately absent.
STATUS_LABEL = {
    RubixQuoteStatus.RUBIX_LIVE_CURRENT: "RUBIX LIVE CURRENT",
    RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST: "RUBIX CURRENT SESSION LAST — NOT LIVE",
    RubixQuoteStatus.RUBIX_PREVIOUS_SESSION: "RUBIX PREVIOUS SESSION",
    RubixQuoteStatus.RUBIX_STALE: "RUBIX STALE",
    RubixQuoteStatus.RUBIX_UNAVAILABLE: "RUBIX UNAVAILABLE",
    RubixQuoteStatus.RUBIX_UNMAPPED: "RUBIX UNMAPPED",
    RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID: "RUBIX TIMESTAMP INVALID",
}


class ExchangePhase(str, Enum):
    """EGX phases, at the granularity a quote verdict actually needs.

    The old code treated the whole 10:00-14:15 window as one ``OPEN`` state, so
    a closing-auction print and a continuous print were indistinguishable.
    """

    PRE_OPEN = "PRE_OPEN"
    CONTINUOUS = "CONTINUOUS"
    CLOSING_AUCTION = "CLOSING_AUCTION"
    POST_MARKET = "POST_MARKET"
    WEEKEND = "WEEKEND"
    HOLIDAY = "HOLIDAY"


#: Only these phases can produce a genuinely live quote.
LIVE_PHASES = frozenset({ExchangePhase.CONTINUOUS, ExchangePhase.CLOSING_AUCTION})

#: EGX regular hours, Cairo local time.
CONTINUOUS_OPEN = time(10, 0)
CONTINUOUS_CLOSE = time(14, 15)
AUCTION_CLOSE = time(14, 25)

#: Settings key for the live-quote receive budget.
#:
#: DATA-QUALITY, not a strategy threshold: it changes no indicator, score or
#: decision rule. It only decides whether a quote inside the permitted session
#: may still be called live.
FRESHNESS_SETTING_KEY = "rubix_live_quote_freshness_seconds"

#: Default receive budget, in seconds.
#:
#: Taken from the operational evidence already in this repository rather than
#: invented: the existing intraday rule used ``open_stale_after_minutes=5``
#: during the continuous session, and the Rubix provider's own
#: ``stale_after_minutes`` gate works at the same order of magnitude. 300s
#: keeps that behaviour unchanged for live quotes while the session and phase
#: checks - which are new - do the work that age alone was wrongly doing.
DEFAULT_FRESHNESS_SECONDS = 300.0

#: How far a receive timestamp may precede its market timestamp before the
#: pair is contradictory. Small clock skew between the exchange feed and the
#: collector host is normal; minutes of it is not evidence, it is a fault.
RECEIVE_BEFORE_MARKET_TOLERANCE_SECONDS = 120.0

#: How far a market timestamp may exceed the evaluation instant. A quote from
#: the future is never trustworthy, but a second or two of skew is not a fault.
FUTURE_TOLERANCE_SECONDS = 60.0


def freshness_budget_seconds(settings_data=None):
    """The configured receive budget, or the documented default."""
    if settings_data is None:
        try:
            from config.settings_manager import settings

            settings_data = settings.data
        except Exception:
            return DEFAULT_FRESHNESS_SECONDS
    try:
        value = float((settings_data or {}).get(
            FRESHNESS_SETTING_KEY, DEFAULT_FRESHNESS_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_FRESHNESS_SECONDS
    return value if value > 0 else DEFAULT_FRESHNESS_SECONDS


# --------------------------------------------------------------------------- #
# Session and phase
# --------------------------------------------------------------------------- #

def _cairo(value):
    from core.egx_session import CAIRO

    return value.astimezone(CAIRO)


def exchange_phase(evaluated_at, *, holidays=None):
    """The EGX phase at ``evaluated_at``. Never reads the clock itself."""
    from core.egx_session import TRADING_WEEKDAYS, _as_holiday_set

    local = _cairo(evaluated_at)
    if local.weekday() not in TRADING_WEEKDAYS:
        return ExchangePhase.WEEKEND
    if local.date() in _as_holiday_set(holidays):
        return ExchangePhase.HOLIDAY
    moment = local.time()
    if moment < CONTINUOUS_OPEN:
        return ExchangePhase.PRE_OPEN
    if moment < CONTINUOUS_CLOSE:
        return ExchangePhase.CONTINUOUS
    if moment < AUCTION_CLOSE:
        return ExchangePhase.CLOSING_AUCTION
    return ExchangePhase.POST_MARKET


def _as_datetime(value):
    """Parse to an aware UTC datetime, or None. Never guesses a timezone."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RubixQuoteAssessment:
    """One quote's verdict plus the evidence behind it."""

    symbol: str
    status: RubixQuoteStatus
    quote_session: str = ""
    permitted_session: str = ""
    market_timestamp: str = ""
    receive_timestamp: str = ""
    phase: str = ""
    receive_lag_seconds: float | None = None
    budget_seconds: float = DEFAULT_FRESHNESS_SECONDS
    reason: str = ""

    @property
    def label(self) -> str:
        return STATUS_LABEL[self.status]

    @property
    def is_live(self) -> bool:
        return self.status is RubixQuoteStatus.RUBIX_LIVE_CURRENT

    def message(self) -> str:
        """The operator-facing wording for this verdict."""
        if self.status is RubixQuoteStatus.RUBIX_PREVIOUS_SESSION:
            return (f"RUBIX PREVIOUS SESSION\nLast quote session: "
                    f"{self.quote_session}\nDaily EODHD close retained.")
        if self.status is RubixQuoteStatus.RUBIX_STALE:
            return (f"RUBIX QUOTE STALE\nLast market time: {self.market_timestamp}\n"
                    f"Last receive time: {self.receive_timestamp}\n"
                    "Daily EODHD close retained.")
        if self.status is RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST:
            return (f"{self.label}\nQuote session: {self.quote_session}\n"
                    "Daily EODHD close retained.")
        return f"{self.label}" + (f"\n{self.reason}" if self.reason else "")

    def as_row(self) -> dict:
        return {
            "RubixQuoteStatus": self.status.value,
            "RubixMarketTimestamp": self.market_timestamp,
            "RubixReceiveTimestamp": self.receive_timestamp,
            "RubixQuoteSession": self.quote_session,
            "RubixPermittedSession": self.permitted_session,
            "RubixExchangePhase": self.phase,
            "RubixReceiveLagSeconds": self.receive_lag_seconds,
            "RubixFreshnessBudgetSeconds": self.budget_seconds,
            "RubixStatusReason": self.reason,
        }


def classify_rubix_quote(
    symbol,
    *,
    evaluated_at,
    mapping_verified,
    quote_price=None,
    market_timestamp=None,
    receive_timestamp=None,
    permitted_session=None,
    holidays=None,
    budget_seconds=None,
    source_progressing=None,
):
    """Classify one quote. Deterministic for a given ``evaluated_at``.

    ``permitted_session`` is the session a live quote must belong to. Callers
    normally pass the exchange's current or latest completed session; passing
    it explicitly is what makes a replay reproducible.
    """

    budget = float(budget_seconds if budget_seconds is not None
                   else DEFAULT_FRESHNESS_SECONDS)
    now = _as_datetime(evaluated_at)
    phase = exchange_phase(now, holidays=holidays) if now else ExchangePhase.POST_MARKET
    market = _as_datetime(market_timestamp)
    receive = _as_datetime(receive_timestamp)
    quote_session = _cairo(market).date().isoformat() if market else ""
    permitted = str(permitted_session or "")[:10]

    def verdict(status, reason="", lag=None):
        return RubixQuoteAssessment(
            symbol=str(symbol), status=status, quote_session=quote_session,
            permitted_session=permitted,
            market_timestamp=market.isoformat() if market else "",
            receive_timestamp=receive.isoformat() if receive else "",
            phase=phase.value, receive_lag_seconds=lag, budget_seconds=budget,
            reason=reason,
        )

    # Mapping first: an overlay on an unverified symbol is a wrong-symbol price,
    # which is worse than no price.
    if not mapping_verified:
        return verdict(RubixQuoteStatus.RUBIX_UNMAPPED,
                       "no verified Rubix mapping for this operational symbol")

    if quote_price is None or _invalid_price(quote_price):
        return verdict(RubixQuoteStatus.RUBIX_UNAVAILABLE, "no usable quote price")

    # Timestamp trust, before any session or age reasoning.
    if market is None:
        return verdict(RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID,
                       "missing or malformed market timestamp")
    if receive is None:
        return verdict(RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID,
                       "missing or malformed receive timestamp")
    if now is None:
        return verdict(RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID,
                       "no evaluation instant supplied")
    if (market - now).total_seconds() > FUTURE_TOLERANCE_SECONDS:
        return verdict(RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID,
                       f"market timestamp {market.isoformat()} is in the future")
    if (market - receive).total_seconds() > RECEIVE_BEFORE_MARKET_TOLERANCE_SECONDS:
        return verdict(RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID,
                       "receive timestamp precedes the market timestamp beyond tolerance")

    lag = (now - receive).total_seconds()

    # Session membership decides before age does.
    if permitted and quote_session and quote_session < permitted:
        return verdict(RubixQuoteStatus.RUBIX_PREVIOUS_SESSION,
                       f"quote belongs to {quote_session}, permitted session is "
                       f"{permitted}", lag)
    if permitted and quote_session and quote_session > permitted:
        return verdict(RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID,
                       f"quote session {quote_session} is after the permitted "
                       f"session {permitted}", lag)

    # Right session. Is the market in a phase that can produce a live quote?
    if phase not in LIVE_PHASES:
        return verdict(RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST,
                       f"market is {phase.value}; this is the session's last "
                       "observation, not a live quote", lag)

    if source_progressing is False:
        return verdict(RubixQuoteStatus.RUBIX_STALE,
                       "the source is not advancing", lag)
    if lag > budget:
        return verdict(RubixQuoteStatus.RUBIX_STALE,
                       f"received {lag:.0f}s ago; budget is {budget:.0f}s", lag)

    return verdict(RubixQuoteStatus.RUBIX_LIVE_CURRENT, "", lag)


def _invalid_price(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return True
    return number <= 0.0


# --------------------------------------------------------------------------- #
# Overlay permission
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RubixOverlayPermission:
    """Three separate permissions the old single status string conflated."""

    may_display_quote: bool
    may_label_live: bool
    may_overlay_display_price: bool
    may_enter_decision_inputs: bool
    retained_daily_close: bool
    denial_reason: str = ""
    status: RubixQuoteStatus = RubixQuoteStatus.RUBIX_UNAVAILABLE

    @property
    def decision_price_source(self) -> str:
        return "rubix_live" if self.may_enter_decision_inputs else "eodhd_daily_close"

    @property
    def display_price_source(self) -> str:
        return "rubix_quote" if self.may_overlay_display_price else "eodhd_daily_close"

    def as_row(self) -> dict:
        return {
            "RubixOverlayApplied": self.may_overlay_display_price,
            "RubixDecisionInputAllowed": self.may_enter_decision_inputs,
            "DisplayPriceSource": self.display_price_source,
            "DecisionPriceSource": self.decision_price_source,
            "RubixOverlayDenialReason": self.denial_reason,
        }

    def denial_message(self) -> str:
        return ("Decision price: EODHD daily close\n"
                f"Rubix overlay: not applied — {self.denial_reason}")


def evaluate_overlay_permission(assessment, *, daily_symbol_current):
    """Decide what a quote may do, given the daily symbol's own freshness.

    A Rubix quote can never rescue a daily-stale symbol: the strategy reads a
    daily candle, and a live tick is not one. So a price enters decision inputs
    only when the daily symbol is CURRENT *and* the quote is genuinely live.
    """

    status = assessment.status
    live = status is RubixQuoteStatus.RUBIX_LIVE_CURRENT
    displayable = status in (
        RubixQuoteStatus.RUBIX_LIVE_CURRENT,
        RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST,
        RubixQuoteStatus.RUBIX_PREVIOUS_SESSION,
        RubixQuoteStatus.RUBIX_STALE,
    )

    if not live:
        reason = assessment.reason or STATUS_LABEL[status]
        return RubixOverlayPermission(
            may_display_quote=displayable, may_label_live=False,
            may_overlay_display_price=False, may_enter_decision_inputs=False,
            retained_daily_close=True, denial_reason=reason, status=status)

    if not daily_symbol_current:
        return RubixOverlayPermission(
            may_display_quote=True, may_label_live=True,
            may_overlay_display_price=False, may_enter_decision_inputs=False,
            retained_daily_close=True,
            denial_reason="daily candle for this symbol is not CURRENT",
            status=status)

    return RubixOverlayPermission(
        may_display_quote=True, may_label_live=True,
        may_overlay_display_price=True, may_enter_decision_inputs=True,
        retained_daily_close=False, denial_reason="", status=status)


__all__ = [
    "DEFAULT_FRESHNESS_SECONDS",
    "FRESHNESS_SETTING_KEY",
    "LIVE_PHASES",
    "STATUS_LABEL",
    "ExchangePhase",
    "RubixOverlayPermission",
    "RubixQuoteAssessment",
    "RubixQuoteStatus",
    "classify_rubix_quote",
    "evaluate_overlay_permission",
    "exchange_phase",
    "freshness_budget_seconds",
]
