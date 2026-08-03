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
    LIVE_DECISION_DISABLED_PHASE2B = "LIVE_DECISION_DISABLED_PHASE2B"
    LIVE_DECISION_DISABLED_STALE_QUOTE = "LIVE_DECISION_DISABLED_STALE_QUOTE"
    LIVE_DECISION_DISABLED_MARKET_TIME_UNRELIABLE = (
        "LIVE_DECISION_DISABLED_MARKET_TIME_UNRELIABLE"
    )
    LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE = (
        "LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE"
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
