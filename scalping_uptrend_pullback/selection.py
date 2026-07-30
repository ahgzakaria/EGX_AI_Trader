"""Historical frozen-candidate selection for UPTREND_PULLBACK_SCALPING.

The selector is pre-session and historical-only. It reads completed EODHD Daily
OHLCV through an explicit D-1 (or earlier) cutoff, computes a deterministic
EMA5/EMA10 short-term uptrend definition, builds a confluence support zone from
completed daily evidence, classifies the pullback state, and produces an
immutable ranked snapshot.

Nothing here consumes Yahoo data, a current incomplete daily candle, live Rubix
movement, or an AI-generated number. Daily OHLC cannot reveal whether the
session high or low came first, so support touches and reactions are named as
proxies and ``first_touch_order_available`` stays ``False``. The output is a set
of research zones; no order is ever produced.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
import hashlib
import json
import math
from typing import Mapping

import numpy as np
import pandas as pd

from scalping_uptrend_pullback.config import UptrendPullbackSelectionConfig
from scalping_uptrend_pullback.states import (
    BLOCKING_STATE_ORDER,
    DAILY_AUCTION_DISCLOSURE,
    DAILY_PATH_DISCLOSURE,
    DATA_UNAVAILABLE,
    EMA5_SLOPE_FAILED,
    EMA10_SLOPE_FAILED,
    EMA_ALIGNMENT_FAILED,
    EODHD_DAILY,
    FIRST_TOUCH_ORDER_AVAILABLE,
    INSUFFICIENT_HISTORY,
    INSUFFICIENT_LIQUIDITY,
    PROVENANCE_REJECTED,
    REASON_CLOSE_BELOW_EMA10,
    REASON_DESCENDING_HIGHS,
    REASON_DESCENDING_LOWS,
    REASON_EMA10_SLOPE,
    REASON_EMA5_SLOPE,
    REASON_EMA_ALIGNMENT,
    REASON_EXTENDED,
    REASON_LAST_CLOSE_BELOW_EMA10,
    REASON_NEAR_SUPPORT,
    REASON_NO_STRUCTURE_PIVOTS,
    REASON_PULLBACK_TOO_DEEP,
    REASON_STALE_HISTORY,
    REASON_STATE_NOT_ELIGIBLE,
    REASON_SUPPORT_ABOVE_PRICE,
    REASON_SUPPORT_CONFIRMED,
    REASON_SUPPORT_INVALIDATED,
    REASON_SUPPORT_NO_SOURCES,
    REASON_SUPPORT_NO_TOUCHES,
    REASON_SUPPORT_WEAK,
    REASON_SUPPORT_ZONE_TOO_WIDE,
    REASON_TREND_CONFIRMED,
    REASON_TREND_TOO_YOUNG,
    REASON_TURNOVER_BELOW_MINIMUM,
    REASON_VOLUME_BELOW_MINIMUM,
    REASON_VOLUME_HISTORY_UNRESOLVED,
    REASON_WAIT_FOR_PULLBACK,
    RESEARCH_ONLY_DISCLOSURE,
    SELECTION_INSUFFICIENT,
    SELECTION_READY,
    SELECTION_STALE,
    SELECTION_UNAVAILABLE,
    SUPPORT_BROKEN,
    SUPPORT_NOT_CONFIRMED,
    SUPPORT_PROXY_DISCLOSURE,
    SUPPORT_SOURCE_BREAKOUT,
    SUPPORT_SOURCE_CONSOLIDATION,
    SUPPORT_SOURCE_EMA10,
    SUPPORT_SOURCE_SWING_LOW,
    TREND_STRUCTURE_FAILED,
    UPTREND_EXTENDED_NO_CHASE,
    UPTREND_NEAR_SUPPORT,
    UPTREND_PULLBACK_TOO_DEEP,
    UPTREND_WAIT_FOR_PULLBACK,
    VOLUME_HISTORY_READY,
    VOLUME_HISTORY_UNAVAILABLE,
    VOLUME_HISTORY_UNRESOLVED,
)


@dataclass(frozen=True)
class UptrendReadiness:
    status: str
    valid_sessions: int
    minimum_sessions: int
    preferred_sessions: int
    preferred_ready: bool
    reason: str | None = None


@dataclass(frozen=True)
class EODHDDailyLoadResult:
    symbol: str
    status: str
    frame: pd.DataFrame | None
    detail: str | None
    source_provider: str = EODHD_DAILY


@dataclass(frozen=True)
class ShortTermTrendProfile:
    """EMA5/EMA10 trend evidence from completed daily bars only."""

    fast_ema: float
    slow_ema: float
    fast_ema_slope_percent_per_session: float | None
    slow_ema_slope_percent_per_session: float | None
    ema_spread_percent: float
    aligned_sessions: int
    trend_age_window: int
    trend_confirmation_window: int
    closes_below_slow_ema: int
    last_close_below_slow_ema: bool
    last_close: float
    swing_high_pivots: tuple[float, ...]
    swing_low_pivots: tuple[float, ...]
    swing_highs_descending: bool
    swing_lows_descending: bool
    higher_low_ratio: float | None
    alignment_ok: bool
    fast_slope_ok: bool
    slow_slope_ok: bool
    structure_ok: bool
    trend_quality_score: float
    deterministic_reasons: tuple[str, ...]


@dataclass(frozen=True)
class SupportZone:
    """Confluence support zone. Daily-bar proxies only — never intraday."""

    lower: float
    upper: float
    centre: float
    width_percent: float
    source_types: tuple[str, ...]
    contributing_levels: tuple[float, ...]
    strength: float
    support_touch_proxy_count: int
    support_reaction_proxy_count: int
    support_reaction_proxy_frequency: float
    invalidation_level: float
    confirmed: bool
    deterministic_reasons: tuple[str, ...]
    first_touch_order_available: bool = FIRST_TOUCH_ORDER_AVAILABLE
    support_touch_disclosure: str = SUPPORT_PROXY_DISCLOSURE


@dataclass(frozen=True)
class PullbackProfile:
    reference_high: float
    reference_high_session: str | None
    pullback_depth_percent: float
    sessions_since_reference_high: int
    maximum_single_session_drop_percent: float
    pullback_volume_ratio: float | None
    depth_score: float
    orderliness_score: float
    volume_contraction_score: float
    pullback_quality_score: float
    depth_within_maximum: bool
    deterministic_reasons: tuple[str, ...]


@dataclass(frozen=True)
class LiquidityProfile:
    median_volume: float
    median_turnover_egp: float
    volume_consistency: float
    positive_volume_frequency: float
    liquidity_score: float
    meets_minimum: bool
    deterministic_reasons: tuple[str, ...]


@dataclass(frozen=True)
class UpsideRiskProfile:
    recent_resistance: float | None
    resistance_source: str
    available_upside_percent: float | None
    invalidation_risk_percent: float | None
    reward_risk_ratio: float | None
    upside_versus_risk_score: float
    deterministic_reasons: tuple[str, ...]


@dataclass(frozen=True)
class UptrendPullbackResult:
    """One evaluated symbol. Every field is derived from completed daily bars."""

    symbol: str
    strategy_name: str
    strategy_identity: str
    source_provider: str
    interval: str
    raw_adjusted_mode: str
    metric_version: str
    config_version: str
    data_cutoff: str
    latest_session: str | None
    source_data_fingerprint: str | None
    readiness: UptrendReadiness
    valid_session_count: int
    volume_history_status: str
    candidate_state: str
    eligible: bool
    total_score: float | None
    trend: ShortTermTrendProfile | None
    support: SupportZone | None
    pullback: PullbackProfile | None
    liquidity: LiquidityProfile | None
    upside: UpsideRiskProfile | None
    last_close: float | None
    distance_from_support_percent: float | None
    distance_from_support_centre_percent: float | None
    trend_quality_component: float | None
    support_component: float | None
    pullback_component: float | None
    liquidity_component: float | None
    upside_component: float | None
    deterministic_reasons: tuple[str, ...]
    historical_rank: int | None = None
    eligible_rank: int | None = None
    selected_candidate: bool = False
    auction_disclosure: str = DAILY_AUCTION_DISCLOSURE
    path_disclosure: str = DAILY_PATH_DISCLOSURE
    research_only_disclosure: str = RESEARCH_ONLY_DISCLOSURE
    first_touch_order_available: bool = FIRST_TOUCH_ORDER_AVAILABLE

    # --- Flattened accessors used by the frozen-watchlist writer -------------

    @property
    def ema5(self) -> float | None:
        return self.trend.fast_ema if self.trend else None

    @property
    def ema10(self) -> float | None:
        return self.trend.slow_ema if self.trend else None

    @property
    def ema5_slope(self) -> float | None:
        return (
            self.trend.fast_ema_slope_percent_per_session if self.trend else None
        )

    @property
    def ema10_slope(self) -> float | None:
        return (
            self.trend.slow_ema_slope_percent_per_session if self.trend else None
        )

    @property
    def support_zone_lower(self) -> float | None:
        return self.support.lower if self.support else None

    @property
    def support_zone_upper(self) -> float | None:
        return self.support.upper if self.support else None

    @property
    def support_zone_centre(self) -> float | None:
        return self.support.centre if self.support else None

    @property
    def support_strength(self) -> float | None:
        return self.support.strength if self.support else None

    @property
    def support_sources(self) -> tuple[str, ...]:
        return self.support.source_types if self.support else ()

    @property
    def invalidation_level(self) -> float | None:
        return self.support.invalidation_level if self.support else None

    @property
    def pullback_depth_percent(self) -> float | None:
        return self.pullback.pullback_depth_percent if self.pullback else None

    @property
    def liquidity_score(self) -> float | None:
        return self.liquidity.liquidity_score if self.liquidity else None

    @property
    def first_research_target(self) -> float | None:
        return self.upside.recent_resistance if self.upside else None

    def as_dict(self) -> dict:
        row = asdict(self)
        row["deterministic_reasons"] = list(self.deterministic_reasons)
        row["ema5"] = self.ema5
        row["ema10"] = self.ema10
        row["ema5_slope"] = self.ema5_slope
        row["ema10_slope"] = self.ema10_slope
        row["support_zone_lower"] = self.support_zone_lower
        row["support_zone_upper"] = self.support_zone_upper
        row["support_zone_centre"] = self.support_zone_centre
        row["support_strength"] = self.support_strength
        row["support_sources"] = list(self.support_sources)
        row["invalidation_level"] = self.invalidation_level
        row["pullback_depth_percent"] = self.pullback_depth_percent
        row["liquidity_score"] = self.liquidity_score
        row["first_research_target"] = self.first_research_target
        return row


@dataclass(frozen=True)
class FrozenUptrendWatchlist:
    strategy_identity: str
    source_provider: str
    metric_version: str
    config_version: str
    data_cutoff: str
    generated_at: str
    snapshot_id: str
    results: tuple[UptrendPullbackResult, ...]
    candidate_symbols: tuple[str, ...]
    candidate_limit: int
    auction_disclosure: str = DAILY_AUCTION_DISCLOSURE
    research_only_disclosure: str = RESEARCH_ONLY_DISCLOSURE

    def as_dict(self) -> dict:
        return {
            "strategy_identity": self.strategy_identity,
            "source_provider": self.source_provider,
            "metric_version": self.metric_version,
            "config_version": self.config_version,
            "data_cutoff": self.data_cutoff,
            "generated_at": self.generated_at,
            "snapshot_id": self.snapshot_id,
            "candidate_symbols": list(self.candidate_symbols),
            "candidate_limit": self.candidate_limit,
            "auction_disclosure": self.auction_disclosure,
            "research_only_disclosure": self.research_only_disclosure,
            "results": [result.as_dict() for result in self.results],
        }


# ---------------------------------------------------------------------------
# Readiness and loading
# ---------------------------------------------------------------------------


def assess_readiness(
    valid_sessions: int,
    *,
    source_available: bool = True,
    config: UptrendPullbackSelectionConfig | None = None,
) -> UptrendReadiness:
    cfg = config or UptrendPullbackSelectionConfig()
    count = max(0, int(valid_sessions))
    if not source_available:
        return UptrendReadiness(
            SELECTION_UNAVAILABLE,
            count,
            cfg.minimum_valid_sessions,
            cfg.preferred_valid_sessions,
            False,
            "EODHD daily history is unavailable; no fallback is permitted",
        )
    if count < cfg.minimum_valid_sessions:
        return UptrendReadiness(
            SELECTION_INSUFFICIENT,
            count,
            cfg.minimum_valid_sessions,
            cfg.preferred_valid_sessions,
            False,
            f"{count} valid completed sessions < {cfg.minimum_valid_sessions}",
        )
    return UptrendReadiness(
        SELECTION_READY,
        count,
        cfg.minimum_valid_sessions,
        cfg.preferred_valid_sessions,
        count >= cfg.preferred_valid_sessions,
        None,
    )


def load_eodhd_daily_history(
    symbol: str,
    *,
    client=None,
    data_cutoff: date | str | None = None,
    config: UptrendPullbackSelectionConfig | None = None,
) -> EODHDDailyLoadResult:
    """Load corporate-action-aware EODHD daily history with no fallback.

    A full cached EOD response is requested so explicit cutoff filtering stays
    in :func:`analyze_uptrend_pullback`. Prices use the repository's split
    adjustment, volume its event-specific reconciliation. Yahoo is never
    consulted.
    """

    cfg = config or UptrendPullbackSelectionConfig()
    base = _base(symbol)
    cutoff = _to_date(data_cutoff) if data_cutoff is not None else None
    try:
        if client is None:
            from providers.eodhd_client import EODHDClient

            client = EODHDClient()
        raw = client.eod(f"{base}.EGX", order="a", cache_ttl_seconds=6 * 3600)
        splits = client.get_json(
            f"splits/{base}.EGX", cache_ttl_seconds=7 * 86400
        )
    except Exception as error:
        return EODHDDailyLoadResult(
            base,
            SELECTION_UNAVAILABLE,
            None,
            f"{type(error).__name__}: {error}",
        )

    rows = [
        row
        for row in (raw or [])
        if isinstance(row, dict)
        and all(
            row.get(key) is not None
            for key in ("date", "open", "high", "low", "close")
        )
        and (cutoff is None or _to_date(row["date"]) <= cutoff)
    ]
    if not rows:
        return EODHDDailyLoadResult(
            base,
            SELECTION_UNAVAILABLE,
            None,
            "EODHD returned no daily OHLC rows",
        )

    from providers.eodhd_adjustment import adjust
    from providers.eodhd_volume_adjustment import resolve_operational_volume

    applicable_splits = [
        event
        for event in (splits or [])
        if cutoff is None
        or (
            isinstance(event, dict)
            and event.get("date") is not None
            and _to_date(event["date"]) <= cutoff
        )
    ]

    source = pd.DataFrame(
        {
            "Date": pd.to_datetime([row["date"] for row in rows], errors="coerce"),
            "Open": [row["open"] for row in rows],
            "High": [row["high"] for row in rows],
            "Low": [row["low"] for row in rows],
            "Close": [row["close"] for row in rows],
            "Volume": [row.get("volume", 0) for row in rows],
        }
    )
    try:
        adjusted = adjust(source, applicable_splits)
        output = adjusted.frame
        served_volume, volume_meta = resolve_operational_volume(
            base,
            output["Date"],
            output["Raw Volume"],
            applicable_splits,
            lookback_sessions=cfg.lookback_sessions,
        )
    except Exception as error:
        return EODHDDailyLoadResult(
            base,
            SELECTION_UNAVAILABLE,
            None,
            f"corporate-action normalization failed: {type(error).__name__}: {error}",
        )

    frame = output.set_index(pd.to_datetime(output["Date"]))[
        ["Open", "High", "Low", "Close"]
    ].copy()
    frame["Volume"] = served_volume.to_numpy()
    frame.attrs["market_data"] = {
        "provider": "eodhd",
        "effective_provider": "eodhd",
        "source_provider": cfg.source_provider,
        "provider_symbol": f"{base}.EGX",
        "interval": "1d",
        "price_series": "SPLIT_ADJUSTED",
        "raw_adjusted_mode": cfg.raw_adjusted_mode,
        "normalization_data_cutoff": pd.Timestamp(output["Date"].max())
        .date()
        .isoformat(),
        "fallback_used": False,
        "yahoo_network_used": False,
        **adjusted.provenance,
        **volume_meta,
    }
    return EODHDDailyLoadResult(base, "OK", frame, None)


# ---------------------------------------------------------------------------
# Single-symbol analysis
# ---------------------------------------------------------------------------


def analyze_uptrend_pullback(
    symbol: str,
    daily: pd.DataFrame | None,
    *,
    data_cutoff: date | str,
    source_available: bool = True,
    source_provider: str = EODHD_DAILY,
    config: UptrendPullbackSelectionConfig | None = None,
) -> UptrendPullbackResult:
    """Evaluate one symbol against the uptrend-pullback definition.

    ``data_cutoff`` is mandatory and every later row is discarded, so a current
    incomplete daily candle can never enter the calculation. A non-EODHD frame
    is rejected rather than treated as a fallback.
    """

    cfg = config or UptrendPullbackSelectionConfig()
    cutoff = _to_date(data_cutoff)
    base = _base(symbol)

    if daily is None or not source_available:
        return _blocked_result(base, cutoff, cfg, DATA_UNAVAILABLE, ())
    if source_provider != EODHD_DAILY or not _has_eodhd_provenance(daily):
        return _blocked_result(
            base,
            cutoff,
            cfg,
            PROVENANCE_REJECTED,
            ("PROVIDER_PROVENANCE_IS_NOT_EODHD_DAILY",),
        )

    cleaned = _clean_completed_daily(daily, cutoff)
    selected = cleaned.tail(cfg.lookback_sessions)
    readiness = assess_readiness(
        len(selected), source_available=True, config=cfg
    )
    if selected.empty:
        return _blocked_result(
            base,
            cutoff,
            cfg,
            DATA_UNAVAILABLE,
            ("NO_VALID_COMPLETED_EODHD_DAILY_SESSIONS",),
        )

    latest = pd.Timestamp(selected.index[-1]).date().isoformat()
    fingerprint = _frame_fingerprint(selected)
    stale_days = max(0, (cutoff - _to_date(latest)).days)
    if (
        readiness.status == SELECTION_READY
        and stale_days > cfg.maximum_stale_calendar_days
    ):
        readiness = UptrendReadiness(
            SELECTION_STALE,
            readiness.valid_sessions,
            readiness.minimum_sessions,
            readiness.preferred_sessions,
            readiness.preferred_ready,
            (
                f"latest valid session {latest} is {stale_days} calendar days "
                f"before cutoff {cutoff.isoformat()}"
            ),
        )

    volume_safe = _volume_safe(daily, cutoff)
    volume_history_status = (
        VOLUME_HISTORY_READY if volume_safe else VOLUME_HISTORY_UNRESOLVED
    )

    if readiness.status != SELECTION_READY:
        reasons = [readiness.status]
        if readiness.status == SELECTION_STALE:
            reasons.append(REASON_STALE_HISTORY)
        return _blocked_result(
            base,
            cutoff,
            cfg,
            INSUFFICIENT_HISTORY,
            tuple(reasons),
            readiness=readiness,
            latest_session=latest,
            fingerprint=fingerprint,
            volume_history_status=volume_history_status,
        )

    trend = _trend_profile(selected, cfg)
    liquidity = _liquidity_profile(selected, cfg, volume_safe=volume_safe)
    support = _support_zone(selected, trend, cfg)
    pullback = _pullback_profile(selected, cfg)
    last_close = float(selected["Close"].iloc[-1])
    upside = _upside_risk_profile(selected, support, last_close, cfg)

    distance = _distance_from_support_percent(last_close, support)
    centre_distance = (
        _round((last_close - support.centre) / support.centre * 100.0)
        if support.centre > 0
        else None
    )

    state, reasons = _resolve_state(
        trend=trend,
        support=support,
        pullback=pullback,
        liquidity=liquidity,
        upside=upside,
        distance_percent=distance,
        cfg=cfg,
    )
    components, total = _score(trend, support, pullback, liquidity, upside, distance, cfg)
    eligible = state in tuple(cfg.eligible_states)
    if not eligible and REASON_STATE_NOT_ELIGIBLE not in reasons:
        reasons = reasons + (REASON_STATE_NOT_ELIGIBLE,)

    return UptrendPullbackResult(
        symbol=base,
        strategy_name=cfg.strategy_name,
        strategy_identity=cfg.strategy_identity,
        source_provider=cfg.source_provider,
        interval="1d",
        raw_adjusted_mode=cfg.raw_adjusted_mode,
        metric_version=cfg.metric_version,
        config_version=cfg.config_version,
        data_cutoff=cutoff.isoformat(),
        latest_session=latest,
        source_data_fingerprint=fingerprint,
        readiness=readiness,
        valid_session_count=int(readiness.valid_sessions),
        volume_history_status=volume_history_status,
        candidate_state=state,
        eligible=eligible,
        total_score=total,
        trend=trend,
        support=support,
        pullback=pullback,
        liquidity=liquidity,
        upside=upside,
        last_close=_round(last_close),
        distance_from_support_percent=distance,
        distance_from_support_centre_percent=centre_distance,
        trend_quality_component=components["trend"],
        support_component=components["support"],
        pullback_component=components["pullback"],
        liquidity_component=components["liquidity"],
        upside_component=components["upside"],
        deterministic_reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Frozen snapshot
# ---------------------------------------------------------------------------


def build_frozen_uptrend_watchlist(
    histories: Mapping[str, pd.DataFrame | None],
    *,
    data_cutoff: date | str,
    unavailable_symbols: set[str] | frozenset[str] = frozenset(),
    generated_at: datetime | str | None = None,
    config: UptrendPullbackSelectionConfig | None = None,
) -> FrozenUptrendWatchlist:
    """Build one immutable ranked snapshot for the pre-session watchlist.

    There is no Streamlit, live quote, or current-session input, so reruns and
    Rubix movement cannot reorder the result. The candidate limit is a maximum:
    when fewer symbols qualify, fewer are returned.
    """

    cfg = config or UptrendPullbackSelectionConfig()
    cutoff = _to_date(data_cutoff)
    unavailable = {_base(value) for value in unavailable_symbols}
    normalized = {_base(symbol): frame for symbol, frame in histories.items()}
    symbols = sorted(set(normalized) | unavailable)

    raw_results = [
        analyze_uptrend_pullback(
            symbol,
            normalized.get(symbol),
            data_cutoff=cutoff,
            source_available=symbol not in unavailable,
            source_provider=cfg.source_provider,
            config=cfg,
        )
        for symbol in symbols
    ]

    scored = sorted(
        (result for result in raw_results if result.total_score is not None),
        key=lambda result: (-float(result.total_score), result.symbol),
    )
    ranks = {result.symbol: index + 1 for index, result in enumerate(scored)}
    eligible_scored = [result for result in scored if result.eligible]
    eligible_ranks = {
        result.symbol: index + 1 for index, result in enumerate(eligible_scored)
    }
    selected = {
        result.symbol
        for result in eligible_scored[: cfg.candidate_display_limit]
    }

    ranked = tuple(
        sorted(
            (
                _replace_ranking(
                    result,
                    historical_rank=ranks.get(result.symbol),
                    eligible_rank=eligible_ranks.get(result.symbol),
                    selected_candidate=result.symbol in selected,
                )
                for result in raw_results
            ),
            key=lambda result: (
                result.historical_rank is None,
                result.historical_rank or 10**9,
                result.symbol,
            ),
        )
    )
    candidates = tuple(
        result.symbol for result in ranked if result.selected_candidate
    )
    stamp = _iso_timestamp(generated_at)
    payload = {
        "strategy_identity": cfg.strategy_identity,
        "source": cfg.source_provider,
        "metric_version": cfg.metric_version,
        "config_version": cfg.config_version,
        "cutoff": cutoff.isoformat(),
        "candidate_limit": cfg.candidate_display_limit,
        "results": [
            (
                result.symbol,
                result.source_data_fingerprint,
                result.total_score,
                result.candidate_state,
                result.historical_rank,
                result.eligible,
                result.eligible_rank,
                result.selected_candidate,
            )
            for result in ranked
        ],
    }
    snapshot_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return FrozenUptrendWatchlist(
        cfg.strategy_identity,
        cfg.source_provider,
        cfg.metric_version,
        cfg.config_version,
        cutoff.isoformat(),
        stamp,
        snapshot_id,
        ranked,
        candidates,
        cfg.candidate_display_limit,
    )


# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------


def _trend_profile(
    daily: pd.DataFrame, cfg: UptrendPullbackSelectionConfig
) -> ShortTermTrendProfile:
    trend_cfg = cfg.trend
    close = daily["Close"].astype(float)
    high = daily["High"].astype(float)
    low = daily["Low"].astype(float)

    fast = _ema(close, trend_cfg.fast_ema_period)
    slow = _ema(close, trend_cfg.slow_ema_period)
    fast_value = float(fast.iloc[-1])
    slow_value = float(slow.iloc[-1])
    last_close = float(close.iloc[-1])

    fast_slope = _slope_percent_per_session(
        fast, trend_cfg.slope_lookback_sessions
    )
    slow_slope = _slope_percent_per_session(
        slow, trend_cfg.slope_lookback_sessions
    )
    spread_percent = (
        (fast_value - slow_value) / slow_value * 100.0 if slow_value > 0 else 0.0
    )

    age_window = trend_cfg.trend_age_window
    aligned = int((fast.tail(age_window) > slow.tail(age_window)).sum())
    window = trend_cfg.trend_confirmation_window
    tolerance = 1.0 - trend_cfg.below_slow_ema_tolerance_percent / 100.0
    below = int((close.tail(window) < slow.tail(window) * tolerance).sum())
    last_close_below = bool(last_close < slow_value * tolerance)

    structure_window = daily.tail(trend_cfg.structure_lookback_sessions)
    swing_highs = _pivot_values(
        structure_window["High"].astype(float),
        trend_cfg.structure_pivot_radius,
        high=True,
    )
    swing_lows = _pivot_values(
        structure_window["Low"].astype(float),
        trend_cfg.structure_pivot_radius,
        high=False,
    )
    considered = max(2, trend_cfg.minimum_structure_pivots)
    highs_descending = _sequence_descending(
        swing_highs[-considered:], trend_cfg.descending_structure_tolerance_percent
    )
    lows_descending = _sequence_descending(
        swing_lows[-considered:], trend_cfg.descending_structure_tolerance_percent
    )
    higher_low_ratio = _non_descending_ratio(
        swing_lows, trend_cfg.descending_structure_tolerance_percent
    )

    reasons: list[str] = []
    alignment_ok = fast_value > slow_value
    if not alignment_ok:
        reasons.append(REASON_EMA_ALIGNMENT)

    fast_slope_ok = (
        fast_slope is not None
        and fast_slope >= trend_cfg.minimum_fast_slope_percent_per_session
    )
    if not fast_slope_ok:
        reasons.append(REASON_EMA5_SLOPE)

    slow_slope_ok = (
        slow_slope is not None
        and slow_slope >= trend_cfg.minimum_slow_slope_percent_per_session
    )
    if not slow_slope_ok:
        reasons.append(REASON_EMA10_SLOPE)

    structure_ok = True
    if aligned < trend_cfg.minimum_trend_sessions:
        structure_ok = False
        reasons.append(REASON_TREND_TOO_YOUNG)
    if last_close_below:
        structure_ok = False
        reasons.append(REASON_LAST_CLOSE_BELOW_EMA10)
    if below > trend_cfg.maximum_closes_below_slow_ema:
        structure_ok = False
        reasons.append(REASON_CLOSE_BELOW_EMA10)
    if highs_descending:
        structure_ok = False
        reasons.append(REASON_DESCENDING_HIGHS)
    if lows_descending:
        structure_ok = False
        reasons.append(REASON_DESCENDING_LOWS)
    if len(swing_lows) < trend_cfg.minimum_structure_pivots:
        # Absence of confirmed pivots is disclosed, never used as evidence of a
        # descending structure.
        reasons.append(REASON_NO_STRUCTURE_PIVOTS)

    score = _trend_quality_score(
        spread_percent=spread_percent,
        fast_slope=fast_slope,
        slow_slope=slow_slope,
        aligned=aligned,
        window=age_window,
        below=below,
        higher_low_ratio=higher_low_ratio,
        cfg=cfg,
    )
    if alignment_ok and fast_slope_ok and slow_slope_ok and structure_ok:
        reasons.append(REASON_TREND_CONFIRMED)

    return ShortTermTrendProfile(
        fast_ema=_round(fast_value),
        slow_ema=_round(slow_value),
        fast_ema_slope_percent_per_session=_round(fast_slope),
        slow_ema_slope_percent_per_session=_round(slow_slope),
        ema_spread_percent=_round(spread_percent),
        aligned_sessions=aligned,
        trend_age_window=age_window,
        trend_confirmation_window=window,
        closes_below_slow_ema=below,
        last_close_below_slow_ema=last_close_below,
        last_close=_round(last_close),
        swing_high_pivots=tuple(_round(value) for value in swing_highs[-5:]),
        swing_low_pivots=tuple(_round(value) for value in swing_lows[-5:]),
        swing_highs_descending=bool(highs_descending),
        swing_lows_descending=bool(lows_descending),
        higher_low_ratio=_round(higher_low_ratio),
        alignment_ok=bool(alignment_ok),
        fast_slope_ok=bool(fast_slope_ok),
        slow_slope_ok=bool(slow_slope_ok),
        structure_ok=bool(structure_ok),
        trend_quality_score=_round(score, 2),
        deterministic_reasons=tuple(dict.fromkeys(reasons)),
    )


def _trend_quality_score(
    *,
    spread_percent: float,
    fast_slope: float | None,
    slow_slope: float | None,
    aligned: int,
    window: int,
    below: int,
    higher_low_ratio: float | None,
    cfg: UptrendPullbackSelectionConfig,
) -> float:
    trend_cfg = cfg.trend
    spread_score = _band_unit(
        spread_percent,
        0.0,
        trend_cfg.ideal_ema_spread_minimum_percent,
        trend_cfg.ideal_ema_spread_maximum_percent,
        trend_cfg.maximum_ema_spread_percent,
    )
    fast_score = _ramp_unit(
        fast_slope,
        trend_cfg.minimum_fast_slope_percent_per_session,
        trend_cfg.healthy_fast_slope_percent_per_session,
    )
    slow_score = _ramp_unit(
        slow_slope,
        trend_cfg.minimum_slow_slope_percent_per_session,
        trend_cfg.healthy_slow_slope_percent_per_session,
    )
    persistence = _unit(aligned / window) if window else 0.0
    discipline = _unit(
        1.0
        - below
        / max(1, trend_cfg.maximum_closes_below_slow_ema + 1)
    )
    structure = 0.5 if higher_low_ratio is None else _unit(higher_low_ratio)
    return 100.0 * (
        0.25 * spread_score
        + 0.20 * fast_score
        + 0.15 * slow_score
        + 0.20 * persistence
        + 0.10 * discipline
        + 0.10 * structure
    )


# ---------------------------------------------------------------------------
# Support zone
# ---------------------------------------------------------------------------


def _support_zone(
    daily: pd.DataFrame,
    trend: ShortTermTrendProfile,
    cfg: UptrendPullbackSelectionConfig,
) -> SupportZone:
    support_cfg = cfg.support
    close = daily["Close"].astype(float)
    low = daily["Low"].astype(float)
    high = daily["High"].astype(float)
    last_close = float(close.iloc[-1])

    levels: list[tuple[str, float]] = [
        (SUPPORT_SOURCE_EMA10, float(trend.slow_ema))
    ]

    swing_window = daily.tail(support_cfg.swing_low_lookback_sessions)
    swing_lows = _pivot_values(
        swing_window["Low"].astype(float),
        support_cfg.swing_low_pivot_radius,
        high=False,
    )
    for value in swing_lows[-support_cfg.maximum_swing_lows :]:
        levels.append((SUPPORT_SOURCE_SWING_LOW, float(value)))

    shelf = _consolidation_level(daily, cfg)
    if shelf is not None:
        levels.append(shelf)

    reasons: list[str] = []
    group = _governing_cluster(levels, last_close, support_cfg)
    if not group:
        return _unconfirmed_support(
            last_close, support_cfg, (REASON_SUPPORT_ABOVE_PRICE,)
        )

    values = sorted(float(value) for _, value in group)
    source_types = tuple(sorted({source for source, _ in group}))
    centre = float(np.median(values))
    lower = min(values)
    upper = max(values)
    if SUPPORT_SOURCE_EMA10 in source_types:
        ema_value = next(
            value for source, value in group if source == SUPPORT_SOURCE_EMA10
        )
        half = ema_value * support_cfg.ema_zone_half_width_percent / 100.0
        lower = min(lower, ema_value - half)
        upper = max(upper, ema_value + half)
    floor_half = centre * support_cfg.minimum_zone_half_width_percent / 100.0
    lower = min(lower, centre - floor_half)
    upper = max(upper, centre + floor_half)
    width_percent = (upper - lower) / centre * 100.0 if centre > 0 else float("inf")

    tolerance = support_cfg.touch_tolerance_percent / 100.0
    touch_low = lower * (1.0 - tolerance)
    touch_high = upper * (1.0 + tolerance)
    touch_mask = (low <= touch_high) & (high >= touch_low)
    touch_positions = [
        index for index, flag in enumerate(touch_mask.to_numpy()) if flag
    ]
    reaction_target = upper * (1.0 + support_cfg.reaction_advance_percent / 100.0)
    closes = close.to_numpy()
    reactions = 0
    for position in touch_positions:
        forward = closes[
            position + 1 : position + 1 + support_cfg.reaction_lookahead_sessions
        ]
        if forward.size and float(np.max(forward)) >= reaction_target:
            reactions += 1
    touch_count = len(touch_positions)
    reaction_frequency = reactions / touch_count if touch_count else 0.0

    strength = _support_strength(
        source_types=source_types,
        touch_count=touch_count,
        reaction_frequency=reaction_frequency,
        width_percent=width_percent,
        cfg=cfg,
    )
    invalidation = lower * (
        1.0 - support_cfg.invalidation_buffer_percent / 100.0
    )

    if len(source_types) < support_cfg.minimum_support_sources:
        reasons.append(REASON_SUPPORT_NO_SOURCES)
    if touch_count < support_cfg.minimum_touch_count:
        reasons.append(REASON_SUPPORT_NO_TOUCHES)
    if width_percent > support_cfg.maximum_zone_width_percent:
        reasons.append(REASON_SUPPORT_ZONE_TOO_WIDE)
    if strength < support_cfg.minimum_support_strength:
        reasons.append(REASON_SUPPORT_WEAK)
    confirmed = not reasons
    if confirmed:
        reasons.append(REASON_SUPPORT_CONFIRMED)

    return SupportZone(
        lower=_round(lower),
        upper=_round(upper),
        centre=_round(centre),
        width_percent=_round(width_percent),
        source_types=source_types,
        contributing_levels=tuple(_round(value) for value in values),
        strength=_round(strength, 2),
        support_touch_proxy_count=touch_count,
        support_reaction_proxy_count=reactions,
        support_reaction_proxy_frequency=_round(reaction_frequency),
        invalidation_level=_round(invalidation),
        confirmed=confirmed,
        deterministic_reasons=tuple(dict.fromkeys(reasons)),
    )


def _governing_cluster(
    levels: list[tuple[str, float]],
    last_close: float,
    support_cfg,
) -> list[tuple[str, float]]:
    """Return the highest confluence cluster that can act as support.

    Clusters are formed greedily from the lowest level upward. The governing
    cluster is the highest one whose centre sits at or below the last close
    (within the touch tolerance). When price has fallen under every identified
    level, the lowest cluster is returned so the broken-support state can be
    detected instead of silently claiming no support exists.
    """

    usable = [
        (source, float(value))
        for source, value in levels
        if value is not None and np.isfinite(float(value)) and float(value) > 0
    ]
    if not usable:
        return []
    usable.sort(key=lambda item: item[1])

    # A level joins the open cluster when it is close to the previous level and
    # the resulting zone still fits inside the maximum zone width. Chaining
    # lets a shelf floor, a swing low and the EMA10 area form one zone; the
    # width cap stops a chain from sprawling into a meaningless band.
    gap_limit = 1.0 + support_cfg.cluster_tolerance_percent / 100.0
    span_limit = 1.0 + support_cfg.maximum_zone_width_percent / 100.0
    clusters: list[list[tuple[str, float]]] = []
    for source, value in usable:
        if (
            clusters
            and value <= clusters[-1][-1][1] * gap_limit
            and value <= clusters[-1][0][1] * span_limit
        ):
            clusters[-1].append((source, value))
        else:
            clusters.append([(source, value)])

    ceiling = last_close * (1.0 + support_cfg.touch_tolerance_percent / 100.0)
    below = [
        cluster
        for cluster in clusters
        if float(np.median([value for _, value in cluster])) <= ceiling
    ]
    return below[-1] if below else clusters[0]


def _consolidation_level(
    daily: pd.DataFrame, cfg: UptrendPullbackSelectionConfig
) -> tuple[str, float] | None:
    """Highest prior consolidation shelf floor, marked broken-out when proven."""

    support_cfg = cfg.support
    window = daily.tail(support_cfg.consolidation_lookback_sessions)
    span = support_cfg.consolidation_window_sessions
    if len(window) < span + 1:
        return None
    highs = window["High"].astype(float).to_numpy()
    lows = window["Low"].astype(float).to_numpy()
    closes = window["Close"].astype(float).to_numpy()
    last_close = float(closes[-1])

    best: tuple[str, float] | None = None
    for start in range(0, len(window) - span + 1):
        stop = start + span
        floor = float(np.min(lows[start:stop]))
        ceiling = float(np.max(highs[start:stop]))
        if floor <= 0:
            continue
        if (ceiling - floor) / floor * 100.0 > support_cfg.consolidation_maximum_range_percent:
            continue
        if floor > last_close:
            continue
        after = closes[stop:]
        broke_out = bool(
            after.size
            and float(np.max(after))
            >= ceiling * (1.0 + support_cfg.breakout_confirmation_percent / 100.0)
        )
        source = (
            SUPPORT_SOURCE_BREAKOUT if broke_out else SUPPORT_SOURCE_CONSOLIDATION
        )
        if best is None or floor > best[1]:
            best = (source, floor)
    return best


def _support_strength(
    *,
    source_types: tuple[str, ...],
    touch_count: int,
    reaction_frequency: float,
    width_percent: float,
    cfg: UptrendPullbackSelectionConfig,
) -> float:
    support_cfg = cfg.support
    diversity = _unit(len(set(source_types)) / 3.0)
    touches = _unit(touch_count / support_cfg.strong_touch_count)
    reaction = _unit(reaction_frequency)
    tightness = _unit(
        1.0 - width_percent / support_cfg.maximum_zone_width_percent
    )
    return 100.0 * (
        support_cfg.source_diversity_weight * diversity
        + support_cfg.touch_weight * touches
        + support_cfg.reaction_proxy_weight * reaction
        + support_cfg.tightness_weight * tightness
    )


def _unconfirmed_support(
    last_close: float, support_cfg, reasons: tuple[str, ...]
) -> SupportZone:
    return SupportZone(
        lower=_round(last_close),
        upper=_round(last_close),
        centre=_round(last_close),
        width_percent=0.0,
        source_types=(),
        contributing_levels=(),
        strength=0.0,
        support_touch_proxy_count=0,
        support_reaction_proxy_count=0,
        support_reaction_proxy_frequency=0.0,
        invalidation_level=_round(
            last_close * (1.0 - support_cfg.invalidation_buffer_percent / 100.0)
        ),
        confirmed=False,
        deterministic_reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Pullback, liquidity, upside
# ---------------------------------------------------------------------------


def _pullback_profile(
    daily: pd.DataFrame, cfg: UptrendPullbackSelectionConfig
) -> PullbackProfile:
    pullback_cfg = cfg.pullback
    window = daily.tail(pullback_cfg.pullback_reference_lookback_sessions)
    highs = window["High"].astype(float).to_numpy()
    closes = window["Close"].astype(float).to_numpy()
    volumes = window["Volume"].astype(float).to_numpy()
    peak_position = int(np.argmax(highs))
    reference_high = float(highs[peak_position])
    reference_session = (
        pd.Timestamp(window.index[peak_position]).date().isoformat()
        if len(window)
        else None
    )
    last_close = float(closes[-1])
    depth = (
        max(0.0, (reference_high - last_close) / reference_high * 100.0)
        if reference_high > 0
        else 0.0
    )
    sessions_since = int(len(window) - 1 - peak_position)

    leg_closes = closes[peak_position:]
    if leg_closes.size > 1:
        steps = (leg_closes[1:] - leg_closes[:-1]) / leg_closes[:-1] * 100.0
        maximum_drop = float(max(0.0, -float(np.min(steps))))
    else:
        maximum_drop = 0.0

    leg_volumes = volumes[peak_position + 1 :]
    base_volumes = volumes[: peak_position + 1]
    volume_ratio = None
    if leg_volumes.size and base_volumes.size:
        base_median = float(np.median(base_volumes))
        if base_median > 0:
            volume_ratio = float(np.median(leg_volumes)) / base_median

    depth_score = _band_unit(
        depth,
        0.0,
        pullback_cfg.ideal_pullback_depth_minimum_percent,
        pullback_cfg.ideal_pullback_depth_maximum_percent,
        pullback_cfg.maximum_pullback_depth_percent,
    )
    orderliness = _unit(
        1.0 - maximum_drop / pullback_cfg.maximum_single_session_drop_percent
    )
    if volume_ratio is None:
        contraction = 0.5
    elif volume_ratio <= pullback_cfg.ideal_pullback_volume_ratio:
        contraction = 1.0
    else:
        contraction = _unit(
            (pullback_cfg.maximum_pullback_volume_ratio - volume_ratio)
            / (
                pullback_cfg.maximum_pullback_volume_ratio
                - pullback_cfg.ideal_pullback_volume_ratio
            )
        )
    quality = 100.0 * (
        pullback_cfg.depth_weight * depth_score
        + pullback_cfg.orderliness_weight * orderliness
        + pullback_cfg.volume_contraction_weight * contraction
    )

    within_maximum = depth <= pullback_cfg.maximum_pullback_depth_percent
    reasons = () if within_maximum else (REASON_PULLBACK_TOO_DEEP,)

    return PullbackProfile(
        reference_high=_round(reference_high),
        reference_high_session=reference_session,
        pullback_depth_percent=_round(depth),
        sessions_since_reference_high=sessions_since,
        maximum_single_session_drop_percent=_round(maximum_drop),
        pullback_volume_ratio=_round(volume_ratio),
        depth_score=_round(100.0 * depth_score, 2),
        orderliness_score=_round(100.0 * orderliness, 2),
        volume_contraction_score=_round(100.0 * contraction, 2),
        pullback_quality_score=_round(quality, 2),
        depth_within_maximum=bool(within_maximum),
        deterministic_reasons=reasons,
    )


def _liquidity_profile(
    daily: pd.DataFrame,
    cfg: UptrendPullbackSelectionConfig,
    *,
    volume_safe: bool,
) -> LiquidityProfile:
    liquidity_cfg = cfg.liquidity
    volume = daily["Volume"].astype(float)
    turnover = daily["Close"].astype(float) * volume
    median_volume = float(volume.median())
    median_turnover = float(turnover.median())
    consistency = (
        float(
            (
                volume
                >= liquidity_cfg.consistency_reference_fraction * median_volume
            ).mean()
        )
        if median_volume > 0
        else 0.0
    )
    positive = float((volume > 0).mean())
    score = 100.0 * (
        liquidity_cfg.turnover_weight
        * _log_unit(
            median_turnover,
            liquidity_cfg.minimum_median_turnover_egp,
            liquidity_cfg.strong_median_turnover_egp,
        )
        + liquidity_cfg.volume_weight
        * _log_unit(
            median_volume,
            liquidity_cfg.minimum_median_volume,
            liquidity_cfg.strong_median_volume,
        )
        + liquidity_cfg.consistency_weight * consistency
        + liquidity_cfg.positive_volume_weight * positive
    )

    reasons: list[str] = []
    if not volume_safe:
        reasons.append(REASON_VOLUME_HISTORY_UNRESOLVED)
    if median_turnover < liquidity_cfg.minimum_median_turnover_egp:
        reasons.append(REASON_TURNOVER_BELOW_MINIMUM)
    if median_volume < liquidity_cfg.minimum_median_volume:
        reasons.append(REASON_VOLUME_BELOW_MINIMUM)

    return LiquidityProfile(
        median_volume=_round(median_volume, 2),
        median_turnover_egp=_round(median_turnover, 2),
        volume_consistency=_round(consistency),
        positive_volume_frequency=_round(positive),
        liquidity_score=_round(score, 2),
        meets_minimum=not reasons,
        deterministic_reasons=tuple(reasons),
    )


def _upside_risk_profile(
    daily: pd.DataFrame,
    support: SupportZone,
    last_close: float,
    cfg: UptrendPullbackSelectionConfig,
) -> UpsideRiskProfile:
    upside_cfg = cfg.upside
    window = daily.tail(upside_cfg.resistance_lookback_sessions)
    highs = window["High"].astype(float)
    pivots = _pivot_values(highs, upside_cfg.resistance_pivot_radius, high=True)
    above = [value for value in pivots if value > last_close]

    reasons: list[str] = []
    if above:
        resistance = float(min(above))
        source = "CONFIRMED_DAILY_SWING_HIGH"
    else:
        window_high = float(highs.max())
        if window_high > last_close:
            resistance = window_high
            source = "LOOKBACK_WINDOW_HIGH"
        else:
            resistance = None
            source = "NO_RESISTANCE_ABOVE_LAST_CLOSE"
            reasons.append("NO_CONFIRMED_RESISTANCE_ABOVE_LAST_CLOSE")

    upside_percent = (
        (resistance - last_close) / last_close * 100.0
        if resistance is not None and last_close > 0
        else None
    )
    risk_percent = (
        (last_close - support.invalidation_level) / last_close * 100.0
        if last_close > 0
        else None
    )
    reward_risk = (
        upside_percent / risk_percent
        if upside_percent is not None and risk_percent and risk_percent > 0
        else None
    )
    if upside_percent is not None and upside_percent < upside_cfg.minimum_upside_percent:
        reasons.append("AVAILABLE_UPSIDE_BELOW_MINIMUM")

    score = 100.0 * _ramp_unit(
        reward_risk,
        upside_cfg.minimum_scored_reward_risk_ratio,
        upside_cfg.ideal_reward_risk_ratio,
    )

    return UpsideRiskProfile(
        recent_resistance=_round(resistance),
        resistance_source=source,
        available_upside_percent=_round(upside_percent),
        invalidation_risk_percent=_round(risk_percent),
        reward_risk_ratio=_round(reward_risk),
        upside_versus_risk_score=_round(score, 2),
        deterministic_reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# State and score
# ---------------------------------------------------------------------------


def _distance_from_support_percent(
    last_close: float, support: SupportZone
) -> float | None:
    """Distance to the nearest edge of the zone, in percent of that edge.

    Inside the zone is exactly zero; below the zone is negative. Both are
    honest statements about a completed close, not an intraday reaction.
    """

    if support.upper <= 0 or support.lower <= 0:
        return None
    if last_close >= support.upper:
        return _round((last_close - support.upper) / support.upper * 100.0)
    if last_close >= support.lower:
        return 0.0
    return _round((last_close - support.lower) / support.lower * 100.0)


def _resolve_state(
    *,
    trend: ShortTermTrendProfile,
    support: SupportZone,
    pullback: PullbackProfile,
    liquidity: LiquidityProfile,
    upside: UpsideRiskProfile,
    distance_percent: float | None,
    cfg: UptrendPullbackSelectionConfig,
) -> tuple[str, tuple[str, ...]]:
    """Map deterministic evidence onto exactly one typed state."""

    reasons: list[str] = []
    reasons.extend(liquidity.deterministic_reasons)
    reasons.extend(trend.deterministic_reasons)
    reasons.extend(support.deterministic_reasons)
    reasons.extend(pullback.deterministic_reasons)
    reasons.extend(upside.deterministic_reasons)

    blocking: dict[str, bool] = {
        INSUFFICIENT_LIQUIDITY: not liquidity.meets_minimum,
        EMA_ALIGNMENT_FAILED: not trend.alignment_ok,
        EMA5_SLOPE_FAILED: not trend.fast_slope_ok,
        EMA10_SLOPE_FAILED: not trend.slow_slope_ok,
        TREND_STRUCTURE_FAILED: not trend.structure_ok,
        SUPPORT_NOT_CONFIRMED: not support.confirmed,
        SUPPORT_BROKEN: (
            support.confirmed
            and trend.last_close < support.invalidation_level
        ),
        UPTREND_PULLBACK_TOO_DEEP: not pullback.depth_within_maximum,
    }
    if blocking[SUPPORT_BROKEN]:
        reasons.append(REASON_SUPPORT_INVALIDATED)

    for state in BLOCKING_STATE_ORDER:
        if blocking.get(state):
            return state, tuple(dict.fromkeys(reasons))

    if distance_percent is None:
        return SUPPORT_NOT_CONFIRMED, tuple(
            dict.fromkeys(reasons + [REASON_SUPPORT_ABOVE_PRICE])
        )

    proximity = cfg.pullback
    if distance_percent <= proximity.near_support_maximum_percent:
        reasons.append(REASON_NEAR_SUPPORT)
        return UPTREND_NEAR_SUPPORT, tuple(dict.fromkeys(reasons))
    if distance_percent <= proximity.wait_for_pullback_maximum_percent:
        reasons.append(REASON_WAIT_FOR_PULLBACK)
        return UPTREND_WAIT_FOR_PULLBACK, tuple(dict.fromkeys(reasons))
    reasons.append(REASON_EXTENDED)
    return UPTREND_EXTENDED_NO_CHASE, tuple(dict.fromkeys(reasons))


def _score(
    trend: ShortTermTrendProfile,
    support: SupportZone,
    pullback: PullbackProfile,
    liquidity: LiquidityProfile,
    upside: UpsideRiskProfile,
    distance_percent: float | None,
    cfg: UptrendPullbackSelectionConfig,
) -> tuple[dict[str, float], float]:
    """Deterministic 0-100 Uptrend Pullback Scalping Score.

    The score ranks; it never overrides a hard gate. A stretched symbol can
    still score well on trend quality and remain ineligible.
    """

    weights = cfg.weights
    proximity_unit = _proximity_unit(distance_percent, cfg)
    support_component = 100.0 * (
        0.60 * _unit(support.strength / 100.0) + 0.40 * proximity_unit
    )
    components = {
        "trend": _round(trend.trend_quality_score, 2),
        "support": _round(support_component, 2),
        "pullback": _round(pullback.pullback_quality_score, 2),
        "liquidity": _round(liquidity.liquidity_score, 2),
        "upside": _round(upside.upside_versus_risk_score, 2),
    }
    total = (
        weights.short_term_trend_quality * components["trend"]
        + weights.support_confluence * components["support"]
        + weights.pullback_quality * components["pullback"]
        + weights.liquidity * components["liquidity"]
        + weights.upside_versus_risk * components["upside"]
    )
    return components, _round(_bounded(total, 0.0, 100.0), 2)


def _proximity_unit(
    distance_percent: float | None, cfg: UptrendPullbackSelectionConfig
) -> float:
    """1.0 inside the near-support band, decaying to 0 at the no-chase limit."""

    if distance_percent is None:
        return 0.0
    proximity = cfg.pullback
    if distance_percent < 0:
        return 0.0
    if distance_percent <= proximity.near_support_maximum_percent:
        return 1.0
    if distance_percent >= proximity.wait_for_pullback_maximum_percent:
        return 0.0
    span = (
        proximity.wait_for_pullback_maximum_percent
        - proximity.near_support_maximum_percent
    )
    return _unit(
        (proximity.wait_for_pullback_maximum_percent - distance_percent) / span
    )


# ---------------------------------------------------------------------------
# Frames and numeric helpers
# ---------------------------------------------------------------------------


def _blocked_result(
    symbol: str,
    cutoff: date,
    cfg: UptrendPullbackSelectionConfig,
    state: str,
    reasons: tuple[str, ...],
    *,
    readiness: UptrendReadiness | None = None,
    latest_session: str | None = None,
    fingerprint: str | None = None,
    volume_history_status: str = VOLUME_HISTORY_UNAVAILABLE,
) -> UptrendPullbackResult:
    resolved = readiness or assess_readiness(
        0, source_available=False, config=cfg
    )
    return UptrendPullbackResult(
        symbol=symbol,
        strategy_name=cfg.strategy_name,
        strategy_identity=cfg.strategy_identity,
        source_provider=cfg.source_provider,
        interval="1d",
        raw_adjusted_mode=cfg.raw_adjusted_mode,
        metric_version=cfg.metric_version,
        config_version=cfg.config_version,
        data_cutoff=cutoff.isoformat(),
        latest_session=latest_session,
        source_data_fingerprint=fingerprint,
        readiness=resolved,
        valid_session_count=int(resolved.valid_sessions),
        volume_history_status=volume_history_status,
        candidate_state=state,
        eligible=False,
        total_score=None,
        trend=None,
        support=None,
        pullback=None,
        liquidity=None,
        upside=None,
        last_close=None,
        distance_from_support_percent=None,
        distance_from_support_centre_percent=None,
        trend_quality_component=None,
        support_component=None,
        pullback_component=None,
        liquidity_component=None,
        upside_component=None,
        deterministic_reasons=tuple(dict.fromkeys((state,) + tuple(reasons))),
    )


def _replace_ranking(
    result: UptrendPullbackResult,
    *,
    historical_rank: int | None,
    eligible_rank: int | None,
    selected_candidate: bool,
) -> UptrendPullbackResult:
    return replace(
        result,
        historical_rank=historical_rank,
        eligible_rank=eligible_rank,
        selected_candidate=selected_candidate,
    )


def _clean_completed_daily(daily: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Keep only completed, sane, deduplicated sessions at or before cutoff."""

    frame = daily.copy()
    rename = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "date": "Date",
    }
    frame = frame.rename(
        columns={
            column: rename.get(str(column).lower(), column)
            for column in frame.columns
        }
    )
    if "Date" in frame.columns:
        frame.index = pd.to_datetime(frame.pop("Date"), errors="coerce")
    else:
        frame.index = pd.to_datetime(frame.index, errors="coerce")
    if isinstance(frame.index, pd.DatetimeIndex) and frame.index.tz is not None:
        frame.index = frame.index.tz_convert("Africa/Cairo").tz_localize(None)
    complete_column = next(
        (
            column
            for column in ("Complete", "IsComplete", "complete", "is_complete")
            if column in frame.columns
        ),
        None,
    )
    if complete_column is not None:
        frame = frame[frame[complete_column].map(_is_completed_flag)]
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(column not in frame.columns for column in required):
        return pd.DataFrame(columns=required)
    frame = frame[~frame.index.isna()]
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=required)
    frame = frame[
        (frame["Open"] > 0)
        & (frame["High"] > 0)
        & (frame["Low"] > 0)
        & (frame["Close"] > 0)
        & (frame["Volume"] > 0)
    ]
    frame = frame[
        (frame["High"] >= frame[["Open", "Close", "Low"]].max(axis=1))
        & (frame["Low"] <= frame[["Open", "Close", "High"]].min(axis=1))
    ]
    sessions = pd.DatetimeIndex(frame.index).normalize()
    frame = frame[pd.Index(sessions.date) <= cutoff]
    sessions = pd.DatetimeIndex(frame.index).normalize()
    duplicated = sessions.duplicated(keep=False)
    frame = frame[~duplicated]
    frame.index = sessions[~duplicated]
    return frame.sort_index()[required]


def _ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=int(period), adjust=False).mean()


def _slope_percent_per_session(values: pd.Series, lookback: int) -> float | None:
    if len(values) <= lookback:
        return None
    current = float(values.iloc[-1])
    prior = float(values.iloc[-1 - int(lookback)])
    if prior <= 0:
        return None
    return (current - prior) / prior * 100.0 / float(lookback)


def _pivot_values(values: pd.Series, radius: int, *, high: bool) -> tuple[float, ...]:
    """Confirmed daily pivots only — a pivot needs ``radius`` bars each side."""

    series = values.to_numpy(dtype=float)
    span = int(radius)
    if series.size < 2 * span + 1:
        return ()
    pivots = []
    for index in range(span, series.size - span):
        window = series[index - span : index + span + 1]
        centre = series[index]
        if high and centre >= float(np.max(window)) and centre > float(
            np.max(np.delete(window, span))
        ):
            pivots.append(centre)
        elif not high and centre <= float(np.min(window)) and centre < float(
            np.min(np.delete(window, span))
        ):
            pivots.append(centre)
    return tuple(pivots)


def _sequence_descending(values, tolerance_percent: float) -> bool:
    """True only when a pivot sequence is *clearly* descending."""

    series = [float(value) for value in values]
    if len(series) < 2 or series[0] <= 0:
        return False
    tolerance = tolerance_percent / 100.0
    if series[-1] > series[0] * (1.0 - tolerance):
        return False
    return all(
        later <= earlier * (1.0 + tolerance)
        for earlier, later in zip(series, series[1:])
    )


def _non_descending_ratio(values, tolerance_percent: float) -> float | None:
    series = [float(value) for value in values]
    if len(series) < 2:
        return None
    tolerance = tolerance_percent / 100.0
    steps = [
        later >= earlier * (1.0 - tolerance)
        for earlier, later in zip(series, series[1:])
    ]
    return sum(steps) / len(steps)


def _band_unit(
    value: float,
    hard_low: float,
    ideal_low: float,
    ideal_high: float,
    hard_high: float,
) -> float:
    """1.0 inside the ideal band, falling linearly to 0 at the hard bounds."""

    if value is None or not np.isfinite(float(value)):
        return 0.0
    value = float(value)
    if ideal_low <= value <= ideal_high:
        return 1.0
    if value < ideal_low:
        if ideal_low <= hard_low:
            return 0.0
        return _unit((value - hard_low) / (ideal_low - hard_low))
    if hard_high <= ideal_high:
        return 0.0
    return _unit((hard_high - value) / (hard_high - ideal_high))


def _ramp_unit(value: float | None, low: float, high: float) -> float:
    if value is None or not np.isfinite(float(value)) or high <= low:
        return 0.0
    return _unit((float(value) - low) / (high - low))


def _log_unit(value: float, low: float, high: float) -> float:
    if value <= 0 or low <= 0 or high <= low:
        return 0.0
    return _unit(
        (math.log10(value) - math.log10(low))
        / (math.log10(high) - math.log10(low))
    )


def _unit(value: float) -> float:
    return _bounded(value, 0.0, 1.0)


def _bounded(value: float, low: float, high: float) -> float:
    return float(max(low, min(high, float(value))))


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not np.isfinite(number):
        return None
    return round(number, digits)


def _frame_fingerprint(frame: pd.DataFrame) -> str:
    canonical = frame[["Open", "High", "Low", "Close", "Volume"]].copy()
    canonical.insert(
        0, "Date", [value.date().isoformat() for value in canonical.index]
    )
    payload = canonical.to_csv(
        index=False, float_format="%.10g", lineterminator="\n"
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _volume_safe(frame: pd.DataFrame, cutoff: date) -> bool:
    metadata = dict((frame.attrs or {}).get("market_data", {}))
    normalized_through = metadata.get("normalization_data_cutoff")
    if normalized_through and _to_date(normalized_through) > cutoff:
        return False
    return bool(metadata.get("volume_safe_for_lookback", True))


def _has_eodhd_provenance(frame: pd.DataFrame | None) -> bool:
    if frame is None:
        return False
    metadata = dict((frame.attrs or {}).get("market_data", {}))
    providers = {
        str(metadata.get(key, "")).strip().upper()
        for key in ("provider", "effective_provider", "source_provider")
        if metadata.get(key)
    }
    return bool(providers) and providers <= {"EODHD", EODHD_DAILY}


def _is_completed_flag(value) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {
            "",
            "0",
            "false",
            "forming",
            "incomplete",
            "partial",
            "pending",
        }
    return bool(value)


def _base(symbol) -> str:
    return str(symbol).strip().upper().split(".")[0]


def _to_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _iso_timestamp(value: datetime | str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat()
    return str(value)
