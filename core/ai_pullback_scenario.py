"""Deterministic, research-only daily pullback continuation analysis.

The engine consumes completed daily candles and already-computed EMA20, EMA50 and ATR14
series from AI Analysis.  It performs no I/O, no provider access and no production
decision mutation.  Confirmed pivots are causal at the supplied cutoff: a pivot is used
only after its right-hand confirmation bars are already inside the frozen frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Iterable

import numpy as np
import pandas as pd

from core.ai_pullback_config import (
    DEFAULT_PULLBACK_RESEARCH_CONFIG,
    PullbackResearchConfig,
)
from core.ai_stock_analysis_contract import PullbackScenarioResult, PullbackState


PULLBACK_ENGINE_VERSION = "ai_pullback_scenario@1.0.0"


@dataclass(frozen=True)
class _Pivot:
    position: int
    timestamp: pd.Timestamp
    confirmed_at: pd.Timestamp
    value: float


GATE_ORDER = (
    "valid_completed_eodhd_observation",
    "valid_prior_uptrend",
    "confirmed_swing_high_found",
    "valid_impulse_low_found",
    "correction_detected",
    "support_zone_identified",
    "support_reached_or_approached",
    "volume_acceptable",
    "reversal_confirmation_detected",
    "entry_trigger_crossed",
    "acceptable_available_upside",
    "acceptable_reward_risk",
    "confirmed_pullback_entry",
)


def _gates(**values):
    return tuple((name, str(values.get(name, "SKIPPED"))) for name in GATE_ORDER)


def _number(value, digits=4):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _date_text(value) -> str | None:
    if value is None:
        return None
    return pd.Timestamp(value).date().isoformat()


def _freeze_frame(frame: pd.DataFrame, data_cutoff: date | str | None) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame()
    daily = frame.copy()
    if "Date" in daily.columns and not isinstance(daily.index, pd.DatetimeIndex):
        daily.index = pd.to_datetime(daily["Date"], errors="coerce")
    else:
        daily.index = pd.to_datetime(daily.index, errors="coerce")
    daily = daily[~daily.index.isna()].sort_index()
    daily = daily[~daily.index.duplicated(keep="last")]
    if data_cutoff is not None:
        cutoff = pd.Timestamp(data_cutoff).normalize()
        if cutoff.tzinfo is not None:
            cutoff = cutoff.tz_localize(None)
        index = daily.index
        if index.tz is not None:
            index = index.tz_localize(None)
        daily = daily.loc[index.normalize() <= cutoff]
    required = ("Open", "High", "Low", "Close", "Volume")
    if any(column not in daily.columns for column in required):
        return pd.DataFrame()
    numeric = daily.loc[:, required].apply(pd.to_numeric, errors="coerce")
    valid = numeric[["Open", "High", "Low", "Close"]].gt(0).all(axis=1)
    valid &= numeric[["Open", "High", "Low", "Close"]].notna().all(axis=1)
    daily = numeric.loc[valid].copy()
    daily.attrs = dict(getattr(frame, "attrs", {}))
    return daily


def _confirmed_pivots(series: pd.Series, radius: int, *, high: bool,
                      position_offset: int = 0) -> tuple[_Pivot, ...]:
    """Return pivots confirmed by bars already present at the frozen cutoff."""
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    pivots: list[_Pivot] = []
    for position in range(radius, len(values) - radius):
        centre = values[position]
        if not math.isfinite(centre):
            continue
        left = values[position - radius:position]
        right = values[position + 1:position + radius + 1]
        if not np.isfinite(left).all() or not np.isfinite(right).all():
            continue
        if high:
            qualifies = centre >= float(np.max(left)) and centre > float(np.max(right))
        else:
            qualifies = centre <= float(np.min(left)) and centre < float(np.min(right))
        if qualifies:
            pivots.append(_Pivot(
                position + position_offset,
                pd.Timestamp(series.index[position]),
                pd.Timestamp(series.index[position + radius]),
                centre,
            ))
    return tuple(pivots)


def _slope_percent_per_bar(series: pd.Series, end: int, lookback: int) -> float | None:
    start = end - lookback
    if start < 0:
        return None
    first = _number(series.iloc[start], 10)
    last = _number(series.iloc[end], 10)
    if first in (None, 0) or last is None:
        return None
    return (last / first - 1.0) * 100.0 / lookback


def _descending(values: Iterable[float], tolerance_percent=0.25) -> bool:
    points = tuple(float(value) for value in values)
    if len(points) < 2:
        return False
    tolerance = tolerance_percent / 100.0
    return all(current < previous * (1.0 + tolerance)
               for previous, current in zip(points, points[1:]))


def _not_applicable(*, cutoff, current_price=None, reason, missing=(), evidence=(),
                    gate_results=(), rejection_reasons=(), swing_high=None,
                    impulse_low=None):
    return PullbackScenarioResult(
        state=PullbackState.NOT_APPLICABLE,
        historical_data_cutoff=cutoff,
        current_price=_number(current_price),
        swing_high=_number(swing_high.value) if swing_high else None,
        swing_high_date=_date_text(swing_high.timestamp) if swing_high else None,
        swing_high_confirmation_date=(
            _date_text(swing_high.confirmed_at) if swing_high else None),
        impulse_low=_number(impulse_low.value) if impulse_low else None,
        impulse_low_date=_date_text(impulse_low.timestamp) if impulse_low else None,
        prior_trend_status="NOT_VALIDATED",
        invalidation_reason=reason,
        explanation_ar="لا يوجد اتجاه صاعد ودفعة سعرية موثوقان لتقييم تصحيح حالي.",
        explanation_en="No reliable prior uptrend and impulse are available for a pullback.",
        evidence=tuple(evidence),
        missing_measurements=tuple(missing),
        gate_results=tuple(gate_results),
        primary_rejection_reason=reason,
        rejection_reasons=tuple(rejection_reasons or (reason,)),
    )


def evaluate_pullback_scenario(
    frame: pd.DataFrame,
    *,
    ema20: pd.Series,
    ema50: pd.Series,
    atr14: pd.Series,
    volume_safe: bool,
    data_cutoff: date | str | None = None,
    config: PullbackResearchConfig = DEFAULT_PULLBACK_RESEARCH_CONFIG,
    normalized_completed_frame: bool = False,
) -> PullbackScenarioResult:
    """Evaluate one frozen daily pullback without consulting live data or an LLM."""
    if normalized_completed_frame:
        daily = frame
        if data_cutoff is not None and not daily.empty:
            cutoff_value = pd.Timestamp(data_cutoff).normalize()
            if pd.Timestamp(daily.index[-1]).normalize() > cutoff_value:
                daily = daily.loc[pd.to_datetime(daily.index).normalize() <= cutoff_value]
    else:
        daily = _freeze_frame(frame, data_cutoff)
    cutoff = _date_text(daily.index[-1]) if not daily.empty else _date_text(data_cutoff)
    if daily.empty or len(daily) < config.minimum_history_bars:
        return _not_applicable(
            cutoff=cutoff,
            reason="INSUFFICIENT_COMPLETED_DAILY_HISTORY",
            missing=("confirmed_swing_high", "impulse_low", "pullback_measurements"),
            gate_results=_gates(valid_completed_eodhd_observation="FAIL"),
        )

    provider = str(daily.attrs.get("market_data", {}).get("provider", "")).lower()
    if provider != "eodhd":
        return _not_applicable(
            cutoff=cutoff,
            current_price=float(daily["Close"].iloc[-1]),
            reason="EODHD_COMPLETED_DAILY_REQUIRED",
            missing=("eodhd_completed_daily_history",),
            gate_results=_gates(valid_completed_eodhd_observation="FAIL"),
        )

    # Align the series to exactly the completed bars that survived normalization.
    def aligned(series):
        values = series if isinstance(series, pd.Series) else pd.Series(series, copy=False)
        if values.index.equals(daily.index):
            return values.astype(float)
        values.index = pd.to_datetime(values.index, errors="coerce")
        return pd.to_numeric(values.reindex(daily.index), errors="coerce")

    ema20 = aligned(ema20)
    ema50 = aligned(ema50)
    atr14 = aligned(atr14)
    close = daily["Close"].astype(float)
    high = daily["High"].astype(float)
    low = daily["Low"].astype(float)
    volume = daily["Volume"].astype(float)
    current_position = len(daily) - 1
    current_price = float(close.iloc[-1])
    current_atr = _number(atr14.iloc[-1], 10)
    if current_atr is None or current_atr <= 0:
        return _not_applicable(
            cutoff=cutoff,
            current_price=current_price,
            reason="ATR_UNAVAILABLE",
            missing=("pullback_atr", "structural_stop", "support_zone"),
            gate_results=_gates(valid_completed_eodhd_observation="FAIL"),
        )

    window_start = max(0, len(daily) - config.structure_lookback_bars)
    pivot_scan_start = max(
        0, window_start - config.maximum_impulse_lookback_bars - config.pivot_radius)
    high_pivots_all = _confirmed_pivots(
        high.iloc[pivot_scan_start:], config.pivot_radius, high=True,
        position_offset=pivot_scan_start)
    low_pivots_all = _confirmed_pivots(
        low.iloc[pivot_scan_start:], config.pivot_radius, high=False,
        position_offset=pivot_scan_start)
    high_pivots = tuple(p for p in high_pivots_all if p.position >= window_start)

    swing_high = None
    swing_candidate = None
    impulse_low = None
    for candidate in reversed(high_pivots):
        if candidate.position >= current_position:
            continue
        decline = candidate.value - current_price
        if decline <= 0:
            continue
        decline_percent = decline / candidate.value * 100.0
        decline_atr = decline / current_atr
        if (decline_percent < config.minimum_pullback_percent
                and decline_atr < config.minimum_pullback_atr):
            continue
        if swing_candidate is None:
            swing_candidate = candidate
        preceding = [p for p in low_pivots_all
                     if p.position < candidate.position
                     and candidate.position - p.position <= config.maximum_impulse_lookback_bars]
        if preceding:
            swing_high = candidate
            impulse_low = preceding[-1]
            break

    if swing_candidate is None:
        return _not_applicable(
            cutoff=cutoff,
            current_price=current_price,
            reason="NO_CONFIRMED_SWING_HIGH",
            missing=("confirmed_swing_high",),
            evidence=(f"causal_pivot_radius={config.pivot_radius}",),
            gate_results=_gates(
                valid_completed_eodhd_observation="PASS",
                confirmed_swing_high_found="FAIL",
                correction_detected="FAIL",
            ),
        )
    if swing_high is None or impulse_low is None:
        return _not_applicable(
            cutoff=cutoff,
            current_price=current_price,
            reason="NO_VALID_IMPULSE_LOW",
            missing=("confirmed_impulse_low",),
            evidence=(f"causal_pivot_radius={config.pivot_radius}",),
            gate_results=_gates(
                valid_completed_eodhd_observation="PASS",
                confirmed_swing_high_found="PASS",
                valid_impulse_low_found="FAIL",
                correction_detected="PASS",
            ),
            swing_high=swing_candidate,
        )

    impulse_size = swing_high.value - impulse_low.value
    impulse_duration = swing_high.position - impulse_low.position
    atr_at_high = _number(atr14.iloc[swing_high.position], 10) or current_atr
    impulse_strength_atr = impulse_size / atr_at_high if atr_at_high > 0 else None
    pullback_amount = swing_high.value - current_price
    pullback_percent = pullback_amount / swing_high.value * 100.0
    pullback_atr = pullback_amount / current_atr
    retracement = impulse_size and pullback_amount / impulse_size * 100.0
    correction_bars = current_position - swing_high.position

    ema20_at_high = _number(ema20.iloc[swing_high.position], 10)
    ema50_at_high = _number(ema50.iloc[swing_high.position], 10)
    ema20_slope_prior = _slope_percent_per_bar(
        ema20, swing_high.position, config.slope_lookback_bars)
    ema50_slope_prior = _slope_percent_per_bar(
        ema50, swing_high.position, config.slope_lookback_bars)
    ema50_slope_current = _slope_percent_per_bar(
        ema50, current_position, config.slope_lookback_bars)

    prior_highs = [p.value for p in high_pivots_all if p.position <= swing_high.position]
    prior_lows = [p.value for p in low_pivots_all if p.position <= impulse_low.position]
    structure_reliable = len(prior_highs) >= 2 and len(prior_lows) >= 2
    structure_up = (not _descending(prior_highs[-2:])
                    and not _descending(prior_lows[-2:])) if structure_reliable else None
    structure_status = (
        "HIGHER_HIGH_HIGHER_LOW" if structure_up is True
        else "DESCENDING_STRUCTURE" if structure_up is False
        else "UNRELIABLE"
    )

    trend_checks = {
        "PRICE_ABOVE_EMA20_AT_SWING": (
            ema20_at_high is not None and float(close.iloc[swing_high.position]) > ema20_at_high),
        "EMA20_ABOVE_EMA50_AT_SWING": (
            ema20_at_high is not None and ema50_at_high is not None
            and ema20_at_high > ema50_at_high),
        "EMA20_SLOPE_POSITIVE": (
            ema20_slope_prior is not None
            and ema20_slope_prior >= config.minimum_ema20_slope_percent_per_bar),
        "EMA50_SLOPE_POSITIVE": (
            ema50_slope_prior is not None
            and ema50_slope_prior >= config.minimum_ema50_slope_percent_per_bar),
        "IMPULSE_DURATION_VALID": impulse_duration >= config.minimum_impulse_duration_bars,
        "IMPULSE_STRENGTH_VALID": (
            impulse_strength_atr is not None
            and impulse_strength_atr >= config.minimum_impulse_strength_atr),
        "STRUCTURE_NOT_DESCENDING": structure_up is not False,
    }
    trend_valid = all(trend_checks.values())
    prior_trend_status = "VALID_UPTREND" if trend_valid else "INVALID_PRIOR_TREND"

    missing = []
    if not structure_reliable:
        missing.append("reliable_higher_high_higher_low_structure")
    if not volume_safe:
        missing.extend(("impulse_average_volume", "correction_average_volume",
                        "selling_volume_ratio", "current_volume_ratio"))

    # Support candidates are grouped into a zone so near-identical levels do not imply
    # false precision or multiple unrelated supports.
    current_ema20 = _number(ema20.iloc[-1], 10)
    current_ema50 = _number(ema50.iloc[-1], 10)
    candidates: list[tuple[str, float]] = [
        ("PREVIOUS_SWING_LOW", impulse_low.value),
        ("EMA20", current_ema20),
        ("EMA50", current_ema50),
        ("FIB_38_2", swing_high.value - 0.382 * impulse_size),
        ("FIB_50_0", swing_high.value - 0.500 * impulse_size),
        ("FIB_61_8", swing_high.value - 0.618 * impulse_size),
    ]
    horizontal_window = low.iloc[max(0, swing_high.position - 20):swing_high.position]
    if not horizontal_window.empty:
        candidates.append(("HORIZONTAL_SUPPORT", float(horizontal_window.min())))
    older_highs = [p for p in high_pivots_all if p.position < impulse_low.position]
    if older_highs:
        prior_breakout = older_highs[-1].value
        if prior_breakout < swing_high.value:
            candidates.append(("PRIOR_BREAKOUT_RETEST", prior_breakout))
    candidates = [(name, float(value)) for name, value in candidates
                  if value is not None and math.isfinite(float(value)) and float(value) > 0]
    cluster_tolerance = max(
        current_atr * config.support_cluster_tolerance_atr,
        current_price * config.support_cluster_tolerance_percent / 100.0,
    )
    clusters = []
    for anchor_name, anchor_value in candidates:
        cluster = tuple((name, value) for name, value in candidates
                        if abs(value - anchor_value) <= cluster_tolerance)
        lo = min(value for _, value in cluster)
        hi = max(value for _, value in cluster)
        distance = 0.0 if lo <= current_price <= hi else min(
            abs(current_price - lo), abs(current_price - hi))
        clusters.append((distance, -len({name for name, _ in cluster}), -hi, cluster))
    selected_cluster = min(clusters, key=lambda item: item[:3])[-1]
    padding = current_atr * config.support_zone_padding_atr
    support_zone_low = min(value for _, value in selected_cluster) - padding
    support_zone_high = max(value for _, value in selected_cluster) + padding
    support_confluence = tuple(dict.fromkeys(name for name, _ in selected_cluster))
    support_distances_percent = tuple(
        (name, _number((value - current_price) / current_price * 100.0, 2))
        for name, value in candidates
    )
    approach = current_atr * config.support_approach_atr
    support_reached = (
        float(low.iloc[-1]) <= support_zone_high + approach
        and current_price >= support_zone_low - approach
    )
    stop_loss = support_zone_low - current_atr * config.stop_buffer_atr

    if (retracement is not None and retracement >= config.failed_retracement_percent
            or pullback_atr >= config.failed_pullback_atr):
        depth_classification = "FAILED_DEPTH"
    elif (retracement is not None
          and retracement > config.healthy_retracement_maximum_percent
          or pullback_atr > config.healthy_pullback_maximum_atr):
        depth_classification = "DEEP"
    elif (pullback_percent >= config.minimum_pullback_percent
          or pullback_atr >= config.minimum_pullback_atr):
        depth_classification = "HEALTHY"
    else:
        depth_classification = "SHALLOW"

    # Volume comparisons use the actual impulse and correction windows.  Unsafe volume
    # never becomes zero or positive evidence.
    impulse_average_volume = correction_average_volume = current_volume_ratio = None
    selling_volume_ratio = None
    correction_impulse_ratio = None
    volume_behaviour = "UNAVAILABLE"
    if volume_safe:
        impulse_volume = volume.iloc[impulse_low.position:swing_high.position + 1]
        correction_volume = volume.iloc[swing_high.position + 1:]
        impulse_average_volume = float(impulse_volume.mean()) if len(impulse_volume) else None
        correction_average_volume = (
            float(correction_volume.mean()) if len(correction_volume) else None)
        prior_average = volume.iloc[max(0, len(volume) - config.volume_window - 1):-1].mean()
        if math.isfinite(float(prior_average)) and prior_average > 0:
            current_volume_ratio = float(volume.iloc[-1]) / float(prior_average)
        correction_slice = daily.iloc[swing_high.position + 1:]
        down_mask = correction_slice["Close"] < correction_slice["Close"].shift(1)
        selling = correction_slice.loc[down_mask, "Volume"]
        selling_average = float(selling.mean()) if len(selling) else correction_average_volume
        if impulse_average_volume and selling_average is not None:
            selling_volume_ratio = selling_average / impulse_average_volume
        correction_impulse_ratio = (
            correction_average_volume / impulse_average_volume
            if impulse_average_volume and correction_average_volume is not None else None)
        expansion_values = tuple(value for value in (
            selling_volume_ratio, correction_impulse_ratio, current_volume_ratio)
                                 if value is not None)
        expansion_measure = max(expansion_values) if expansion_values else 0.0
        contraction_measure = correction_impulse_ratio
        if expansion_measure >= config.aggressive_selling_volume_ratio:
            volume_behaviour = "AGGRESSIVE_SELLING_EXPANSION"
        elif expansion_measure >= config.expanding_volume_ratio:
            volume_behaviour = "SELLING_VOLUME_EXPANDING"
        elif (contraction_measure is not None
              and contraction_measure <= config.contracting_volume_ratio):
            volume_behaviour = "SELLING_VOLUME_CONTRACTING"
        else:
            volume_behaviour = "MIXED_VOLUME"

    # Deterministic reversal evidence.  A rejection candle is reported, but entry is not
    # called confirmed until price also reclaims a typed reference.
    previous_close = float(close.iloc[-2])
    previous_high = float(high.iloc[-2])
    current_open = float(daily["Open"].iloc[-1])
    current_high = float(high.iloc[-1])
    current_low = float(low.iloc[-1])
    candle_range = current_high - current_low
    body = abs(current_price - current_open)
    lower_wick = min(current_open, current_price) - current_low
    close_location = ((current_price - current_low) / candle_range
                      if candle_range > 0 else None)
    bullish_rejection = (
        current_price > current_open
        and lower_wick >= max(body, current_atr * 0.05)
        * config.bullish_rejection_wick_to_body
        and close_location is not None
        and close_location >= config.bullish_rejection_close_location
    )
    close_back_above_zone = previous_close <= support_zone_high < current_price
    previous_ema20 = _number(ema20.iloc[-2], 10)
    reclaim_ema20 = (
        previous_ema20 is not None and current_ema20 is not None
        and previous_close <= previous_ema20 and current_price > current_ema20)
    close_above_previous_high = current_price > previous_high
    correction_highs = high.iloc[swing_high.position + 1:-1]
    trendline_break = False
    if len(correction_highs) >= 3:
        x = np.arange(len(correction_highs), dtype=float)
        slope, intercept = np.polyfit(x, correction_highs.to_numpy(dtype=float), 1)
        projected = intercept + slope * len(correction_highs)
        trendline_break = slope < 0 and current_price > projected

    confirmation_reasons = tuple(name for name, flag in (
        ("BULLISH_REJECTION_CANDLE", bullish_rejection),
        ("CLOSE_BACK_ABOVE_SUPPORT_ZONE", close_back_above_zone),
        ("EMA20_RECLAIM", reclaim_ema20),
        ("CLOSE_ABOVE_PREVIOUS_CANDLE_HIGH", close_above_previous_high),
        ("CORRECTION_TRENDLINE_BREAK", trendline_break),
    ) if flag)
    strong_confirmation = any((
        close_back_above_zone, reclaim_ema20,
        close_above_previous_high, trendline_break,
    ))
    # A deep correction whose selected structural zone includes EMA50 must be
    # able to confirm at that zone.  EMA20 reclaim remains valid confirmation
    # evidence, but it is not forced into the trigger for this distinct setup.
    deep_ema50_pullback = (
        depth_classification == "DEEP" and "EMA50" in support_confluence)
    trigger_references = [previous_high, support_zone_high]
    if not deep_ema50_pullback:
        trigger_references.append(current_ema20)
    entry_trigger = max(value for value in trigger_references if value is not None)
    entry_confirmed = support_reached and current_price >= entry_trigger and strong_confirmation
    if entry_confirmed:
        confirmation_status = "CONFIRMED"
    elif bullish_rejection and support_reached:
        confirmation_status = "REVERSAL_CANDLE_TRIGGER_PENDING"
    elif support_reached:
        confirmation_status = "WAITING"
    else:
        confirmation_status = "NOT_AT_SUPPORT"
    resistance_values = sorted({p.value for p in high_pivots_all
                                if p.value > entry_trigger})
    target_1 = resistance_values[0] if resistance_values else None
    target_2 = resistance_values[1] if len(resistance_values) > 1 else None
    major_resistance = swing_high.value if swing_high.value > entry_trigger else (
        resistance_values[-1] if resistance_values else None)
    risk_amount = entry_trigger - stop_loss
    reward_risk = ((target_1 - entry_trigger) / risk_amount
                   if target_1 is not None and risk_amount > 0 else None)
    meaningful_values = [value for value in resistance_values
                         if value - entry_trigger
                         >= config.meaningful_target_minimum_distance_atr * current_atr]
    meaningful_target = meaningful_values[0] if meaningful_values else None
    broader_target = meaningful_values[-1] if meaningful_values else major_resistance
    reward_risk_meaningful = (
        (meaningful_target - entry_trigger) / risk_amount
        if meaningful_target is not None and risk_amount > 0 else None)
    reward_risk_broader = (
        (broader_target - entry_trigger) / risk_amount
        if broader_target is not None and broader_target > entry_trigger and risk_amount > 0
        else None)

    post_high_highs = [swing_high.value] + [p.value for p in high_pivots_all
                                           if p.position > swing_high.position]
    post_high_lows = [p.value for p in low_pivots_all if p.position > swing_high.position]
    descending_channel = (_descending(post_high_highs[-3:])
                          and _descending(post_high_lows[-2:]))
    structural_failure = (
        current_price < stop_loss
        or current_price < impulse_low.value - current_atr * config.stop_buffer_atr
    )
    ema50_failed = (ema50_slope_current is not None
                    and ema50_slope_current
                    <= config.materially_declining_ema50_percent_per_bar)
    aggressive_selling = volume_behaviour == "AGGRESSIVE_SELLING_EXPANSION"
    poor_upside = reward_risk is None or reward_risk < config.minimum_reward_risk

    trend_score = sum(100.0 for ok in trend_checks.values() if ok) / len(trend_checks)
    depth_score = {"HEALTHY": 90.0, "SHALLOW": 65.0, "DEEP": 45.0,
                   "FAILED_DEPTH": 0.0}.get(depth_classification, 0.0)
    support_score = min(100.0, 25.0 + 25.0 * len(support_confluence))
    volume_score = {"SELLING_VOLUME_CONTRACTING": 95.0, "MIXED_VOLUME": 60.0,
                    "SELLING_VOLUME_EXPANDING": 25.0,
                    "AGGRESSIVE_SELLING_EXPANSION": 0.0}.get(volume_behaviour)
    confirmation_score = (100.0 if entry_confirmed else 65.0 if bullish_rejection
                          else 20.0 if support_reached else 0.0)
    weighted = [(trend_score, 0.30), (depth_score, 0.20), (support_score, 0.20),
                (confirmation_score, 0.15)]
    if volume_score is not None:
        weighted.append((volume_score, 0.15))
    weight_total = sum(weight for _, weight in weighted)
    research_score = sum(score * weight for score, weight in weighted) / weight_total

    invalidation_reason = None
    if not trend_valid:
        state = PullbackState.NOT_APPLICABLE
        invalidation_reason = "INVALID_PRIOR_UPTREND"
    elif structural_failure:
        state = PullbackState.FAILED_PULLBACK
        invalidation_reason = "CLOSE_BELOW_STRUCTURAL_SUPPORT"
    elif depth_classification == "FAILED_DEPTH":
        state = PullbackState.FAILED_PULLBACK
        invalidation_reason = "CORRECTION_EXCEEDS_RESEARCH_DEPTH_LIMIT"
    elif descending_channel:
        state = PullbackState.FAILED_PULLBACK
        invalidation_reason = "ESTABLISHED_DESCENDING_CHANNEL"
    elif ema50_failed:
        state = PullbackState.FAILED_PULLBACK
        invalidation_reason = "EMA50_DECLINING_MATERIALLY"
    elif aggressive_selling:
        state = PullbackState.FAILED_PULLBACK
        invalidation_reason = "AGGRESSIVE_SELLING_VOLUME_EXPANSION"
    elif depth_classification == "DEEP" and not entry_confirmed:
        state = PullbackState.DEEP_PULLBACK
        invalidation_reason = "DEEP_PULLBACK_REQUIRES_STRONGER_CONFIRMATION"
    elif not support_reached:
        state = PullbackState.DEVELOPING_PULLBACK
    elif not entry_confirmed:
        state = PullbackState.WAIT_REVERSAL_CONFIRMATION
    elif poor_upside:
        state = (PullbackState.DEEP_PULLBACK if depth_classification == "DEEP"
                 else PullbackState.HEALTHY_PULLBACK)
        invalidation_reason = "REWARD_RISK_BELOW_RESEARCH_THRESHOLD"
    elif volume_behaviour == "SELLING_VOLUME_EXPANDING":
        state = PullbackState.HEALTHY_PULLBACK
        invalidation_reason = "SELLING_VOLUME_NOT_SUPPORTIVE"
    elif volume_behaviour == "UNAVAILABLE":
        state = PullbackState.HEALTHY_PULLBACK
        invalidation_reason = "VOLUME_BEHAVIOUR_UNAVAILABLE"
    else:
        state = PullbackState.CONFIRMED_PULLBACK_ENTRY

    volume_acceptable = volume_behaviour in (
        "SELLING_VOLUME_CONTRACTING", "MIXED_VOLUME")
    available_upside = target_1 is not None and target_1 > entry_trigger
    reward_risk_acceptable = (
        reward_risk is not None and reward_risk >= config.minimum_reward_risk)
    gate_results = _gates(
        valid_completed_eodhd_observation="PASS",
        valid_prior_uptrend="PASS" if trend_valid else "FAIL",
        confirmed_swing_high_found="PASS",
        valid_impulse_low_found="PASS",
        correction_detected="PASS",
        support_zone_identified="PASS" if candidates else "FAIL",
        support_reached_or_approached="PASS" if support_reached else "FAIL",
        volume_acceptable=("PASS" if volume_acceptable else
                           "SKIPPED" if volume_behaviour == "UNAVAILABLE" else "FAIL"),
        reversal_confirmation_detected="PASS" if strong_confirmation else "FAIL",
        entry_trigger_crossed="PASS" if current_price >= entry_trigger else "FAIL",
        acceptable_available_upside="PASS" if available_upside else "FAIL",
        acceptable_reward_risk="PASS" if reward_risk_acceptable else "FAIL",
        confirmed_pullback_entry=(
            "PASS" if state == PullbackState.CONFIRMED_PULLBACK_ENTRY else "FAIL"),
    )
    rejection_reasons = []
    rejection_reasons.extend(
        f"PRIOR_TREND_{name}_FAILED" for name, passed in trend_checks.items() if not passed)
    if structural_failure:
        rejection_reasons.append("CLOSE_BELOW_STRUCTURAL_SUPPORT")
    if depth_classification == "FAILED_DEPTH":
        rejection_reasons.append("RETRACEMENT_TOO_DEEP")
    if descending_channel:
        rejection_reasons.append("ESTABLISHED_DESCENDING_CHANNEL")
    if ema50_failed:
        rejection_reasons.append("EMA50_SLOPE_INVALIDATED")
    if not support_reached:
        rejection_reasons.append("PULLBACK_DID_NOT_REACH_SUPPORT")
    if volume_behaviour == "AGGRESSIVE_SELLING_EXPANSION":
        rejection_reasons.append("AGGRESSIVE_VOLUME_EXPANSION")
    elif volume_behaviour == "SELLING_VOLUME_EXPANDING":
        rejection_reasons.append("SELLING_VOLUME_EXPANDING")
    elif volume_behaviour == "UNAVAILABLE":
        rejection_reasons.append("VOLUME_UNAVAILABLE")
    if not strong_confirmation:
        rejection_reasons.append("NO_STRONG_REVERSAL_CONFIRMATION")
    if current_price < entry_trigger:
        rejection_reasons.append("ENTRY_TRIGGER_NOT_CROSSED")
    if not available_upside:
        rejection_reasons.append("NO_AVAILABLE_UPSIDE_TARGET")
    if available_upside and not reward_risk_acceptable:
        rejection_reasons.append("REWARD_RISK_BELOW_THRESHOLD")
    if (target_1 is not None and meaningful_target is not None
            and target_1 < meaningful_target):
        rejection_reasons.append("MINOR_PIVOT_BEFORE_MEANINGFUL_TARGET")
    rejection_reasons = list(dict.fromkeys(rejection_reasons))
    primary_rejection_reason = (
        None if state == PullbackState.CONFIRMED_PULLBACK_ENTRY
        else invalidation_reason or (rejection_reasons[0] if rejection_reasons else state.value)
    )

    explanation_by_state = {
        PullbackState.NOT_APPLICABLE: (
            "الاتجاه السابق لا يحقق شروط الاتجاه الصاعد البحثية.",
            "The prior move does not satisfy the research uptrend rules."),
        PullbackState.DEVELOPING_PULLBACK: (
            "التصحيح قائم لكنه لم يصل بعد إلى منطقة دعم ذات معنى.",
            "The correction is developing but has not reached meaningful support."),
        PullbackState.WAIT_REVERSAL_CONFIRMATION: (
            "السعر وصل منطقة الدعم، لكن لم يظهر تأكيد انعكاس صالح للدخول.",
            "Price reached support, but a valid reversal entry confirmation is absent."),
        PullbackState.HEALTHY_PULLBACK: (
            "التصحيح صحي، لكن الدخول غير مؤهل بسبب شرط بحثي متبقٍ.",
            "The pullback is healthy, but one research entry gate remains unsatisfied."),
        PullbackState.DEEP_PULLBACK: (
            "التصحيح عميق ويختبر دعماً رئيسياً مع ارتفاع المخاطر الهيكلية.",
            "The pullback is deep and is testing major support with higher structural risk."),
        PullbackState.CONFIRMED_PULLBACK_ENTRY: (
            "ظهر تأكيد انعكاس حتمي والعائد/المخاطرة يحقق حد البحث فقط.",
            "A deterministic reversal is confirmed and R/R passes the research-only gate."),
        PullbackState.FAILED_PULLBACK: (
            "فشل التصحيح في الحفاظ على بنية الاتجاه الصاعد.",
            "The correction failed to preserve the prior uptrend structure."),
    }
    explanation_ar, explanation_en = explanation_by_state[state]
    evidence = [name for name, ok in trend_checks.items() if ok]
    evidence.extend(support_confluence)
    evidence.extend(confirmation_reasons)
    evidence.append(volume_behaviour)

    return PullbackScenarioResult(
        state=state,
        research_score=_number(research_score, 1),
        prior_trend_status=prior_trend_status,
        historical_data_cutoff=cutoff,
        swing_high=_number(swing_high.value),
        swing_high_date=_date_text(swing_high.timestamp),
        swing_high_confirmation_date=_date_text(swing_high.confirmed_at),
        impulse_low=_number(impulse_low.value),
        impulse_low_date=_date_text(impulse_low.timestamp),
        current_price=_number(current_price),
        pullback_percent=_number(pullback_percent, 2),
        pullback_atr=_number(pullback_atr, 2),
        impulse_retracement_percent=_number(retracement, 2),
        correction_bars=int(correction_bars),
        impulse_strength_atr=_number(impulse_strength_atr, 2),
        impulse_duration_bars=int(impulse_duration),
        ema20_slope_percent_per_bar=_number(ema20_slope_prior, 4),
        ema50_slope_percent_per_bar=_number(ema50_slope_prior, 4),
        ema20=_number(current_ema20),
        ema50=_number(current_ema50),
        price_vs_ema20_percent=_number(
            (current_price - current_ema20) / current_ema20 * 100.0
            if current_ema20 else None, 2),
        price_vs_ema50_percent=_number(
            (current_price - current_ema50) / current_ema50 * 100.0
            if current_ema50 else None, 2),
        ema_alignment_status=(
            "EMA20_ABOVE_EMA50" if current_ema20 and current_ema50
            and current_ema20 > current_ema50 else "EMA_ALIGNMENT_FAILED"),
        structure_status=structure_status,
        depth_classification=depth_classification,
        support_zone_low=_number(support_zone_low),
        support_zone_high=_number(support_zone_high),
        support_reached=bool(support_reached),
        support_confluence=support_confluence,
        support_distances_percent=support_distances_percent,
        volume_behaviour=volume_behaviour,
        impulse_average_volume=_number(impulse_average_volume, 2),
        correction_average_volume=_number(correction_average_volume, 2),
        current_volume_ratio=_number(current_volume_ratio, 2),
        selling_volume_ratio=_number(selling_volume_ratio, 2),
        correction_to_impulse_volume_ratio=_number(correction_impulse_ratio, 2),
        confirmation_status=confirmation_status,
        confirmation_reasons=confirmation_reasons,
        entry_trigger=_number(entry_trigger),
        stop_loss=_number(stop_loss),
        target_1=_number(target_1),
        target_2=_number(target_2),
        major_resistance=_number(major_resistance),
        minor_pivot_resistance=_number(target_1),
        meaningful_structural_target=_number(meaningful_target),
        broader_structural_target=_number(broader_target),
        risk_amount=_number(risk_amount),
        reward_risk=_number(reward_risk, 2),
        reward_risk_meaningful_target=_number(reward_risk_meaningful, 2),
        reward_risk_broader_target=_number(reward_risk_broader, 2),
        invalidation_reason=invalidation_reason,
        explanation_ar=explanation_ar,
        explanation_en=explanation_en,
        evidence=tuple(dict.fromkeys(evidence)),
        missing_measurements=tuple(dict.fromkeys(missing)),
        gate_results=gate_results,
        primary_rejection_reason=primary_rejection_reason,
        rejection_reasons=tuple(rejection_reasons),
    )


__all__ = ["PULLBACK_ENGINE_VERSION", "evaluate_pullback_scenario"]
