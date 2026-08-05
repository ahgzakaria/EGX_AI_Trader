"""Honest Phase 2A price, spread, volume, VWAP and RVOL capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from scalping_orb.bars import CompletedBar
from scalping_orb.config import OrbDataConfig
from scalping_orb.events import (
    LiveFreshnessStatus,
    MarketTimeStatus,
    NormalizedIntradayEvent,
    SpreadCapability,
    VolumeCapability,
)
from scalping_orb.opening_range import OpeningRangeResult


class PriceReferenceStatus(str, Enum):
    TRUE_VWAP_UNAVAILABLE = "TRUE_VWAP_UNAVAILABLE"
    BAR_WEIGHTED_PRICE_PROXY_AVAILABLE = "BAR_WEIGHTED_PRICE_PROXY_AVAILABLE"
    BAR_WEIGHTED_PRICE_PROXY_UNAVAILABLE = "BAR_WEIGHTED_PRICE_PROXY_UNAVAILABLE"
    TIME_OF_DAY_RVOL_INSUFFICIENT_HISTORY = (
        "TIME_OF_DAY_RVOL_INSUFFICIENT_HISTORY"
    )
    TIME_OF_DAY_RVOL_HISTORY_SUFFICIENT_NOT_IMPLEMENTED = (
        "TIME_OF_DAY_RVOL_HISTORY_SUFFICIENT_NOT_IMPLEMENTED"
    )


class HistoricalBarCapability(str, Enum):
    HISTORICAL_BAR_RECONSTRUCTABLE = "HISTORICAL_BAR_RECONSTRUCTABLE"
    HISTORICAL_BAR_UNAVAILABLE = "HISTORICAL_BAR_UNAVAILABLE"


class LiveDecisionCapability(str, Enum):
    """Whether *this symbol* may carry a live research decision, right now.

    Phase 2A gave this enum no enabled member at all, which made live research
    readiness unreachable by construction. That was the correct default while
    per-symbol freshness was not being evaluated, but it also meant a session
    could observe 6,847 valid live breakouts across 86 symbols — as 2026-08-05
    did, with a median receive lag of 0.818 s against a 60 s budget — and reject
    every one of them for "freshness" without ever consulting a quote's age.

    The enabled member is deliberately named `RESEARCH_ONLY`: it authorises the
    engine to reach `ENTRY_READY_RESEARCH`, which is a research candidate. It
    authorises no order, no size, no routing and no execution, and there is no
    code path in this package that could turn it into one.
    """

    LIVE_DECISION_DISABLED_PHASE2B = "LIVE_DECISION_DISABLED_PHASE2B"
    LIVE_DECISION_DISABLED_STALE_QUOTE = "LIVE_DECISION_DISABLED_STALE_QUOTE"
    LIVE_DECISION_DISABLED_MARKET_TIME_UNRELIABLE = (
        "LIVE_DECISION_DISABLED_MARKET_TIME_UNRELIABLE"
    )
    LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE = (
        "LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE"
    )
    LIVE_DECISION_DISABLED_SOURCE_UNHEALTHY = (
        "LIVE_DECISION_DISABLED_SOURCE_UNHEALTHY"
    )
    LIVE_DECISION_DISABLED_NO_SYMBOL_EVIDENCE = (
        "LIVE_DECISION_DISABLED_NO_SYMBOL_EVIDENCE"
    )
    LIVE_DECISION_ENABLED_RESEARCH_ONLY = "LIVE_DECISION_ENABLED_RESEARCH_ONLY"


#: The single enabled member, named once so no caller has to spell it.
LIVE_DECISION_ENABLED = LiveDecisionCapability.LIVE_DECISION_ENABLED_RESEARCH_ONLY


def assess_live_decision_capability(
    latest_event: NormalizedIntradayEvent | None,
    *,
    evaluated_at_utc,
    config: OrbDataConfig | None = None,
    source_healthy: bool,
) -> tuple[LiveDecisionCapability, str]:
    """Per-symbol live decision authority, evaluated at `evaluated_at_utc`.

    Every clause must hold, and anything missing or unverifiable fails closed:

    1. the session/source watermark is healthy — but that is a *source* health
       signal only, and never by itself grants a symbol authority;
    2. this symbol has its own evidence — never another symbol's;
    3. its market timestamp is verified and reliable;
    4. its receive lag is inside the freshness budget;
    5. its quote is still inside the budget **as of now**.

    Clause 5 is the one that does the real work. `live_freshness_status` and
    `collector_age_seconds` are both settled when the event is *normalized*, so
    a symbol that printed once at 10:05 with a perfect 0.4 s lag keeps
    `LIVE_FRESHNESS_PASSED` for the rest of the day. Re-measuring the age
    against the evaluation instant is what stops a four-hour-old print from
    authorising a decision at 14:00 — and it is exactly the case a session-wide
    watermark cannot see, because some *other* symbol ticking a millisecond ago
    keeps that watermark fresh.
    """

    cfg = config or OrbDataConfig()
    budget = float(cfg.maximum_quote_age_seconds)
    tolerance = float(cfg.out_of_order_tolerance_seconds)

    if not source_healthy:
        return (
            LiveDecisionCapability.LIVE_DECISION_DISABLED_SOURCE_UNHEALTHY,
            "session watermark is not fresh; the source itself is not trusted",
        )
    if latest_event is None:
        return (
            LiveDecisionCapability.LIVE_DECISION_DISABLED_NO_SYMBOL_EVIDENCE,
            "no normalized event for this symbol",
        )
    if latest_event.market_time_status is MarketTimeStatus.MARKET_TIME_UNRELIABLE:
        return (
            LiveDecisionCapability.LIVE_DECISION_DISABLED_MARKET_TIME_UNRELIABLE,
            "market timestamp is not verified",
        )
    if latest_event.live_freshness_status is not LiveFreshnessStatus.LIVE_FRESHNESS_PASSED:
        return (
            LiveDecisionCapability.LIVE_DECISION_DISABLED_STALE_QUOTE,
            "receive lag or collector age exceeded the freshness budget on arrival",
        )

    receive_lag = latest_event.receive_lag_seconds
    if receive_lag is None or receive_lag < -tolerance or receive_lag > budget:
        return (
            LiveDecisionCapability.LIVE_DECISION_DISABLED_STALE_QUOTE,
            f"receive lag {receive_lag!r}s outside the {budget}s budget",
        )

    received = latest_event.receive_timestamp_utc
    if received is None or evaluated_at_utc is None:
        return (
            LiveDecisionCapability.LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE,
            "no receive timestamp to age against",
        )
    age_now = (evaluated_at_utc - received).total_seconds()
    if age_now < -tolerance:
        return (
            LiveDecisionCapability.LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE,
            f"quote received {abs(age_now)}s in the future; clock is not trusted",
        )
    if age_now > budget:
        return (
            LiveDecisionCapability.LIVE_DECISION_DISABLED_STALE_QUOTE,
            f"this symbol's last quote is {age_now:.1f}s old, over the {budget}s budget",
        )

    return (
        LIVE_DECISION_ENABLED,
        f"symbol quote is {age_now:.1f}s old within the {budget}s budget",
    )


@dataclass(frozen=True)
class PriceReferenceCapabilities:
    opening_range_high: float | None
    opening_range_low: float | None
    current_bid: float | None
    current_ask: float | None
    spread_status: SpreadCapability
    volume_status: VolumeCapability
    true_vwap_status: PriceReferenceStatus
    bar_weighted_typical_price_proxy: float | None
    bar_weighted_typical_price_proxy_status: PriceReferenceStatus
    time_of_day_rvol_status: PriceReferenceStatus
    intraday_history_sessions: int
    historical_bar_capability: HistoricalBarCapability
    live_decision_capability: LiveDecisionCapability
    live_decision_reason: str


def bar_weighted_typical_price_proxy(
    bars: Iterable[CompletedBar],
) -> float | None:
    """Research-only OHLCV proxy. It is deliberately not named VWAP."""

    values = tuple(bar for bar in bars if bar.completed)
    if not values or any(bar.volume is None for bar in values):
        return None
    total_volume = sum(float(bar.volume) for bar in values)
    if total_volume <= 0:
        return None
    numerator = sum(
        ((bar.high + bar.low + bar.close) / 3) * float(bar.volume)
        for bar in values
    )
    return numerator / total_volume


def assess_price_reference_capabilities(
    *,
    opening_range: OpeningRangeResult | None,
    latest_event: NormalizedIntradayEvent | None,
    completed_bars: Iterable[CompletedBar],
    intraday_history_sessions: int,
    config: OrbDataConfig | None = None,
) -> PriceReferenceCapabilities:
    cfg = config or OrbDataConfig()
    session_count = max(0, int(intraday_history_sessions))
    bars = tuple(completed_bars)
    proxy = bar_weighted_typical_price_proxy(bars)
    live_quote_usable = bool(
        latest_event is not None
        and latest_event.market_time_status != MarketTimeStatus.MARKET_TIME_UNRELIABLE
        and latest_event.live_freshness_status
        == LiveFreshnessStatus.LIVE_FRESHNESS_PASSED
    )
    if latest_event is None:
        live_capability = (
            LiveDecisionCapability.LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE
        )
        live_reason = "no continuous quote with collector freshness evidence"
    elif latest_event.market_time_status == MarketTimeStatus.MARKET_TIME_UNRELIABLE:
        live_capability = (
            LiveDecisionCapability.LIVE_DECISION_DISABLED_MARKET_TIME_UNRELIABLE
        )
        live_reason = "market timestamp is not verified"
    elif latest_event.live_freshness_status != LiveFreshnessStatus.LIVE_FRESHNESS_PASSED:
        live_capability = LiveDecisionCapability.LIVE_DECISION_DISABLED_STALE_QUOTE
        live_reason = "receive lag or collector age exceeded the configured freshness budget"
    else:
        live_capability = LiveDecisionCapability.LIVE_DECISION_DISABLED_PHASE2B
        live_reason = "Phase 2B live decisions are not enabled"
    return PriceReferenceCapabilities(
        opening_range_high=(
            opening_range.opening_range_high if opening_range is not None else None
        ),
        opening_range_low=(
            opening_range.opening_range_low if opening_range is not None else None
        ),
        current_bid=latest_event.bid if live_quote_usable else None,
        current_ask=latest_event.ask if live_quote_usable else None,
        spread_status=(
            latest_event.spread_capability
            if live_quote_usable
            else SpreadCapability.SPREAD_UNAVAILABLE
        ),
        volume_status=(
            latest_event.volume_capability
            if live_quote_usable
            else VolumeCapability.VOLUME_UNAVAILABLE
        ),
        true_vwap_status=PriceReferenceStatus.TRUE_VWAP_UNAVAILABLE,
        bar_weighted_typical_price_proxy=proxy,
        bar_weighted_typical_price_proxy_status=(
            PriceReferenceStatus.BAR_WEIGHTED_PRICE_PROXY_AVAILABLE
            if proxy is not None
            else PriceReferenceStatus.BAR_WEIGHTED_PRICE_PROXY_UNAVAILABLE
        ),
        time_of_day_rvol_status=(
            PriceReferenceStatus.TIME_OF_DAY_RVOL_INSUFFICIENT_HISTORY
            if session_count < cfg.minimum_time_of_day_rvol_sessions
            else PriceReferenceStatus.TIME_OF_DAY_RVOL_HISTORY_SUFFICIENT_NOT_IMPLEMENTED
        ),
        intraday_history_sessions=session_count,
        historical_bar_capability=(
            HistoricalBarCapability.HISTORICAL_BAR_RECONSTRUCTABLE
            if bars
            else HistoricalBarCapability.HISTORICAL_BAR_UNAVAILABLE
        ),
        live_decision_capability=live_capability,
        live_decision_reason=live_reason,
    )
