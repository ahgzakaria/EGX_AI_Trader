"""Layer 1 — CALCULATED NUMERIC EVIDENCE for AI Stock Analysis On Demand.

This module is the ONLY place numbers are produced for the feature. It is a pure,
deterministic transform: a split-adjusted CURRENT_RESEARCH_V2 daily frame (plus an
optional Rubix live quote and market context) in, a fully-populated ``AnalysisResult``
out. No I/O, no provider access, no strategy mutation.

Every technical number the UI needs is emitted as a dedicated typed evidence field
(SMA and EMA kept strictly separate; MACD/OBV/volume-ratio/expected-range-position all
explicit). No consumer ever parses a number out of a string, a KeyLevel ``basis``, a
scenario condition, or narrative text.

Determinism guarantees (relied on by ``evidence_hash`` and the tests):
  * every emitted number is rounded to a fixed number of decimals,
  * the evidence hash is computed over the rounded numeric/decision content only — never
    over wall-clock timestamps, request identifiers, or the (volatile) live quote — so
    identical input frames always yield an identical ``evidence_hash`` /
    ``evidence_version``.

Volume safety: this engine does NOT re-derive any split/volume transformation. It reads
the router-supplied, already-corrected volume series and its provenance
(``volume_safe_for_lookback``, ``volume_adjustment_policy``, ``latest_action_in_lookback``,
``data_quality_status``). When volume is not lookback-safe, ``average_volume_20`` /
``volume_ratio`` / ``obv`` / ``turnover`` are ``None`` and volume never lifts confidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from core.ai_stock_analysis_contract import (
    AnalysisRequest,
    AnalysisResult,
    ChartPoint,
    ChartSeries,
    ConfidenceBreakdown,
    ConfidenceComponent,
    DataQualitySummary,
    DataStatus,
    IndicatorSummary,
    KeyLevel,
    MarketPhase,
    MomentumState,
    PriceSummary,
    PullbackScenarioResult,
    PullbackState,
    Recommendation,
    ScenarioResult,
    ScenarioState,
    TrendState,
)
from core.ai_pullback_scenario import evaluate_pullback_scenario

# --------------------------------------------------------------------------- #
# Versions / tunables (feature-local; nothing here overrides strategy config)
# --------------------------------------------------------------------------- #

EVIDENCE_ENGINE_VERSION = "ai_analysis_evidence@1.2.0"
CONFIDENCE_METHOD_VERSION = "confidence@1.0.0"

RSI_PERIOD = 14
ATR_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
SMA_PERIODS = (20, 50, 200)
EMA_PERIODS = (20, 50, 200)
AVG_VOLUME_WINDOW = 20
CHANNEL_WINDOW = 20            # recent high/low channel for range/breakout levels
ATR_STOP_MULT = 1.75          # invalidation buffer below entry
ATR_TARGET_MULT = 2.5         # measured-move target above breakout
MIN_BARS_FULL = 200           # sessions needed for the full indicator stack
MIN_BARS_USABLE = 60          # below this, evidence is treated as insufficient

# Rounding precision by quantity kind (fixed → reproducible hashes).
PRICE_DP = 4
PCT_DP = 2
IND_DP = 4
VOL_DP = 2
CONF_DP = 1

# Scenario-level confidence by state (0..1).
_SCENARIO_CONFIDENCE = {
    ScenarioState.READY_WITH_CONDITIONS: 0.70,
    ScenarioState.NEAR_READY: 0.55,
    ScenarioState.WAIT: 0.35,
    ScenarioState.AVOID: 0.15,
    ScenarioState.INVALID: 0.10,
    ScenarioState.DATA_INSUFFICIENT: None,
}


# --------------------------------------------------------------------------- #
# Small numeric helpers
# --------------------------------------------------------------------------- #

def _r(value: Any, dp: int) -> float | None:
    """Round to ``dp`` decimals, mapping NaN/inf/None to None (JSON-safe)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, dp)


def _last(series: pd.Series) -> float | None:
    if series is None or len(series) == 0:
        return None
    val = series.iloc[-1]
    return None if pd.isna(val) else float(val)


def _sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(window=period, min_periods=period).mean()


def _ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def _rsi_wilder(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Wilder's RSI (RMA smoothing of gains/losses)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 (only gains) → RSI 100; avg_gain == 0 (only losses) → RSI 0.
    rsi = rsi.where(avg_loss != 0.0, 100.0)
    rsi = rsi.mask((avg_gain == 0.0) & (avg_loss != 0.0), 0.0)
    return rsi


def _atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = ATR_PERIOD) -> pd.Series:
    """Wilder's ATR (RMA of the true range)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _macd(close: pd.Series, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    """Return (macd_line, signal_line, histogram) using EMA(fast)-EMA(slow)."""
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return macd_line, signal_line, macd_line - signal_line


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume. Caller must only trust this when volume is lookback-safe."""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume.fillna(0.0)).cumsum()


def _obv_slope(obv_series: pd.Series, window: int = 10) -> float | None:
    if obv_series is None or len(obv_series) < 2:
        return None
    tail = obv_series.dropna().iloc[-window:]
    if len(tail) < 2:
        return None
    return float(tail.iloc[-1] - tail.iloc[0])


# --------------------------------------------------------------------------- #
# Provenance extraction from the frame metadata
# --------------------------------------------------------------------------- #

def _market_data(frame: pd.DataFrame) -> dict:
    md = getattr(frame, "attrs", {}) or {}
    inner = md.get("market_data", {})
    return dict(inner) if isinstance(inner, dict) else {}


def _volume_meta(frame: pd.DataFrame) -> dict:
    md = getattr(frame, "attrs", {}) or {}
    vm = md.get("volume_meta", {})
    return dict(vm) if isinstance(vm, dict) else {}


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def build_evidence(
    request: AnalysisRequest,
    frame: pd.DataFrame,
    *,
    market_phase: MarketPhase,
    live_quote: dict | None = None,
    intraday_frame: pd.DataFrame | None = None,
    generated_at: str,
) -> AnalysisResult:
    """Build the full ``AnalysisResult`` (Layer 1) from one symbol's daily frame."""
    md = _market_data(frame)
    vmeta = _volume_meta(frame)
    symbol = request.symbol

    close = frame["Close"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    volume = frame["Volume"].astype(float)
    n = int(len(frame))

    latest_session = md.get("latest_completed_session")
    if not latest_session and n:
        latest_session = pd.Timestamp(frame.index[-1]).date().isoformat()

    # Volume provenance (read, never re-derived here).
    volume_safe = bool(md.get("volume_safe_for_lookback",
                              vmeta.get("volume_safe_for_lookback", True)))
    latest_action = md.get("latest_action_in_lookback",
                           vmeta.get("latest_action_in_lookback"))

    # ---- Price evidence (missing → None, never zero-filled) ---------------- #
    last_close = _last(close)
    prev_close = float(close.iloc[-2]) if n >= 2 else None
    change_amount = None
    change_pct = None
    if last_close is not None and prev_close not in (None, 0):
        change_amount = last_close - prev_close
        change_pct = (last_close - prev_close) / prev_close * 100.0

    last_volume = _last(volume)
    session_turnover = (last_close * last_volume) if (
        last_close is not None and last_volume is not None) else None

    price = PriceSummary(
        symbol=symbol,
        session_date=str(latest_session) if latest_session else None,
        close=_r(last_close, PRICE_DP),
        previous_close=_r(prev_close, PRICE_DP),
        change_amount=_r(change_amount, PRICE_DP),
        change_percent=_r(change_pct, PCT_DP),
        open=_r(float(frame["Open"].iloc[-1]), PRICE_DP) if n else None,
        high=_r(float(high.iloc[-1]), PRICE_DP) if n else None,
        low=_r(float(low.iloc[-1]), PRICE_DP) if n else None,
        volume=_r(last_volume, VOL_DP),
        turnover=_r(session_turnover, VOL_DP),
        currency="EGP",
        price_series=str(md.get("price_series", "SPLIT_ADJUSTED")),
        price_adjustment_policy=str(md.get("price_adjustment_policy",
                                           "SPLIT_ADJUSTED_ALL_EVENTS")),
        last=_r((live_quote or {}).get("last"), PRICE_DP) if live_quote else None,
        bid=_r((live_quote or {}).get("bid"), PRICE_DP) if live_quote else None,
        ask=_r((live_quote or {}).get("ask"), PRICE_DP) if live_quote else None,
        spread_percent=_r((live_quote or {}).get("spread_percent"), PCT_DP)
        if live_quote else None,
        quote_timestamp=(live_quote or {}).get("quote_timestamp")
        or (live_quote or {}).get("timestamp") if live_quote else None,
    )

    # ---- Indicators -------------------------------------------------------- #
    sma_series = {p: _sma(close, p) for p in SMA_PERIODS}
    ema_series = {p: _ema(close, p) for p in EMA_PERIODS}
    sma_vals = {p: _last(series) for p, series in sma_series.items()}
    ema_vals = {p: _last(series) for p, series in ema_series.items()}
    rsi_val = _last(_rsi_wilder(close))
    atr_series = _atr_wilder(high, low, close)
    atr_val = _last(atr_series)
    macd_line, macd_signal_line, macd_hist = _macd(close)
    macd_line_val = _last(macd_line)
    macd_signal_val = _last(macd_signal_line)
    macd_hist_val = _last(macd_hist)

    # Recent channel / expected-range position (price-only; safe under unsafe volume).
    channel_high = _last(high.rolling(CHANNEL_WINDOW, min_periods=1).max())
    channel_low = _last(low.rolling(CHANNEL_WINDOW, min_periods=1).min())
    range_position = None
    if (channel_high is not None and channel_low is not None
            and channel_high > channel_low and last_close is not None):
        range_position = (last_close - channel_low) / (channel_high - channel_low) * 100.0

    # Volume-derived numbers exist only when the lookback is volume-safe.
    if volume_safe:
        avg_vol = _last(volume.rolling(AVG_VOLUME_WINDOW, min_periods=1).mean())
        avg_turnover = _last((close * volume).rolling(AVG_VOLUME_WINDOW, min_periods=1).mean())
        volume_ratio = ((last_volume / avg_vol)
                        if (avg_vol and avg_vol > 0 and last_volume is not None) else None)
        obv_series = _obv(close, volume)
        obv_val = _last(obv_series)
        obv_slope = _obv_slope(obv_series)
    else:
        avg_vol = avg_turnover = volume_ratio = obv_val = obv_slope = None

    trend, trend_strength = _classify_trend(last_close, sma_vals, ema_vals)
    momentum, momentum_strength = _classify_momentum(rsi_val, macd_hist_val)

    indicators = IndicatorSummary(
        symbol=symbol,
        computed_from_sessions=n,
        trend=trend,
        trend_strength=_r(trend_strength, PCT_DP),
        momentum=momentum,
        momentum_strength=_r(momentum_strength, PCT_DP),
        sma_20=_r(sma_vals[20], IND_DP),
        sma_50=_r(sma_vals[50], IND_DP),
        sma_200=_r(sma_vals[200], IND_DP),
        ema_20=_r(ema_vals[20], IND_DP),
        ema_50=_r(ema_vals[50], IND_DP),
        ema_200=_r(ema_vals[200], IND_DP),
        rsi_14=_r(rsi_val, PCT_DP),
        macd=_r(macd_line_val, IND_DP),
        macd_signal=_r(macd_signal_val, IND_DP),
        macd_histogram=_r(macd_hist_val, IND_DP),
        atr_14=_r(atr_val, IND_DP),
        average_volume_20=_r(avg_vol, VOL_DP),
        volume_ratio=_r(volume_ratio, PCT_DP),
        obv=_r(obv_val, VOL_DP),
        expected_range_position=_r(range_position, PCT_DP),
        turnover=_r(avg_turnover, VOL_DP),
        volume_safe=volume_safe,
        volume_series=str(md.get("volume_series", vmeta.get("volume_series", "RAW_EODHD"))),
        volume_adjustment_policy=str(md.get("volume_adjustment_policy",
                                            vmeta.get("volume_adjustment_policy", "NONE"))),
        latest_action_in_lookback=str(latest_action) if latest_action else None,
    )

    # ---- Key levels (deterministic support / resistance / EMA dynamics) ---- #
    key_levels = _key_levels(frame, last_close, sma_vals, ema_vals, channel_high,
                             channel_low, atr_val)

    # ---- Scenario engine --------------------------------------------------- #
    history_sufficient = bool(md.get("history_sufficient", n >= MIN_BARS_FULL))
    usable = n >= MIN_BARS_USABLE and last_close is not None
    scenarios = _scenarios(
        last_close=last_close, channel_high=channel_high, channel_low=channel_low,
        atr_val=atr_val, trend=trend.value, momentum=momentum.value,
        volume_safe=volume_safe, volume_ratio=volume_ratio, usable=usable,
    )
    pullback_scenario = _build_pullback_health(
        frame, ema20=ema_series[20], ema50=ema_series[50], atr14=atr_series,
        volume_safe=volume_safe, data_cutoff=latest_session)
    primary_state = scenarios[0].state if scenarios else ScenarioState.DATA_INSUFFICIENT

    # ---- Confidence (pure function of completed-session evidence) ---------- #
    confidence = _confidence(
        trend=trend.value, momentum=momentum.value, volume_safe=volume_safe,
        volume_ratio=volume_ratio, avg_turnover=avg_turnover,
        history_sufficient=history_sufficient, usable=usable,
    )

    # ---- Data quality (provenance derived, not hard-coded) ----------------- #
    data_quality = _data_quality(md, volume_safe=volume_safe,
                                 history_sufficient=history_sufficient, usable=usable,
                                 live_quote=live_quote, latest_session=latest_session)

    # ---- Recommendation + machine reasons (supplementary; not a data source) #
    recommendation, reasons = _recommendation(
        data_status=data_quality.status, primary_state=primary_state, trend=trend.value,
        momentum=momentum.value, last_close=last_close, sma_vals=sma_vals, ema_vals=ema_vals,
        volume_safe=volume_safe, volume_ratio=volume_ratio, rsi_val=rsi_val,
        macd_hist_val=macd_hist_val, obv_slope=obv_slope, range_position=range_position,
        channel_high=channel_high,
    )

    # ---- Assemble + hash --------------------------------------------------- #
    daily_series = _chart_series(
        frame.tail(180), timeframe="1D", source=str(md.get("provider", "eodhd")),
        latest_completed_session=str(latest_session) if latest_session else None,
        intraday=False,
    )
    intraday_series = _chart_series(
        intraday_frame, timeframe="1m", source="rubix",
        latest_completed_session=str(latest_session) if latest_session else None,
        intraday=True,
    ) if intraday_frame is not None and not intraday_frame.empty else None

    partial = AnalysisResult(
        request=request, price=price, indicators=indicators, confidence=confidence,
        data_quality=data_quality, recommendation=recommendation,
        market_phase=market_phase, evidence_version="", generated_at=generated_at,
        key_levels=tuple(key_levels), scenarios=tuple(scenarios),
        pullback_scenario=pullback_scenario,
        recommendation_reasons=tuple(reasons), evidence_hash=None,
        daily_chart_series=daily_series, intraday_chart_series=intraday_series,
    )
    evidence_hash = _evidence_hash(partial)
    session_token = str(latest_session or "unknown")
    evidence_version = f"evidence@{symbol}@{session_token}@{evidence_hash[7:19]}"
    return _replace_result(partial, evidence_version=evidence_version,
                           evidence_hash=evidence_hash)


def build_insufficient_evidence(
    request: AnalysisRequest,
    *,
    market_phase: MarketPhase,
    generated_at: str,
    status: DataStatus = DataStatus.DATA_UNAVAILABLE,
    reason: str = "current-research history unavailable",
    provider: str = "eodhd",
    latest_session: str | None = None,
    provenance: dict | None = None,
) -> AnalysisResult:
    """Build a well-formed DATA_INSUFFICIENT result when no usable history exists.

    Numbers are absent (None), never fabricated. Provenance is derived from ``provenance``
    metadata when available so the Yahoo-never invariant is verified, not assumed.
    """
    md = dict(provenance or {})
    symbol = request.symbol
    price = PriceSummary(symbol=symbol, session_date=latest_session)
    indicators = IndicatorSummary(symbol=symbol, computed_from_sessions=0,
                                  volume_safe=False)
    confidence = ConfidenceBreakdown(overall=0.0, method_version=CONFIDENCE_METHOD_VERSION,
                                     components=())
    data_quality = DataQualitySummary(
        status=status,
        data_domain=str(md.get("data_domain", "CURRENT_RESEARCH_V2")),
        provider=str(md.get("provider", provider)),
        freshness_status=str(md.get("freshness_status", "UNAVAILABLE")),
        latest_completed_session=latest_session or md.get("latest_completed_session"),
        expected_completed_session=md.get("expected_completed_session"),
        history_sufficient=False, volume_safe_for_lookback=False,
        live_provider=str(md.get("live_provider", "rubix")), live_available=False,
        yahoo_network_used=bool(md.get("yahoo_network_used", False)),
        yahoo_seed_present=bool(md.get("yahoo_seed_present", False)),
        notes=(reason,),
    )
    scenario = ScenarioResult(
        scenario_id="data_insufficient", title="Insufficient data",
        state=ScenarioState.DATA_INSUFFICIENT,
        invalidation_conditions=("usable CURRENT_RESEARCH_V2 history required",),
    )
    pullback_scenario = PullbackScenarioResult(
        state=PullbackState.NOT_APPLICABLE,
        historical_data_cutoff=latest_session,
        prior_trend_status="UNAVAILABLE",
        invalidation_reason="INSUFFICIENT_COMPLETED_DAILY_HISTORY",
        explanation_ar="التاريخ اليومي المكتمل غير كافٍ لتقييم جودة التصحيح.",
        explanation_en="Completed daily history is insufficient for Pullback Health Analysis.",
        missing_measurements=(
            "confirmed_swing_high", "impulse_low", "pullback_measurements"),
        primary_rejection_reason="INSUFFICIENT_COMPLETED_DAILY_HISTORY",
        rejection_reasons=("INSUFFICIENT_COMPLETED_DAILY_HISTORY",),
    )
    partial = AnalysisResult(
        request=request, price=price, indicators=indicators, confidence=confidence,
        data_quality=data_quality, recommendation=Recommendation.DATA_INSUFFICIENT,
        market_phase=market_phase, evidence_version="", generated_at=generated_at,
        scenarios=(scenario,), pullback_scenario=pullback_scenario,
        recommendation_reasons=(reason,), evidence_hash=None,
    )
    evidence_hash = _evidence_hash(partial)
    evidence_version = f"evidence@{symbol}@{latest_session or 'none'}@{evidence_hash[7:19]}"
    return _replace_result(partial, evidence_version=evidence_version,
                           evidence_hash=evidence_hash)


def _build_pullback_health(frame, *, ema20, ema50, atr14, volume_safe, data_cutoff):
    """Run the diagnostic evaluator without allowing it to break AI Analysis.

    Only the exception type is retained. The raw exception text is deliberately discarded
    because it may contain a path or provider detail. This wrapper does not alter any
    evaluator formula, threshold or decision output.
    """
    try:
        return evaluate_pullback_scenario(
            frame, ema20=ema20, ema50=ema50, atr14=atr14,
            volume_safe=volume_safe, data_cutoff=data_cutoff)
    except Exception as error:
        reason = f"PULLBACK_DIAGNOSTIC_ERROR:{type(error).__name__}"
        return PullbackScenarioResult(
            state=PullbackState.NOT_APPLICABLE,
            historical_data_cutoff=str(data_cutoff) if data_cutoff else None,
            prior_trend_status="UNAVAILABLE",
            invalidation_reason=reason,
            explanation_ar="تعذر حساب تحليل جودة التصحيح، وبقي باقي التحليل متاحًا.",
            explanation_en=(
                "Pullback Health Analysis failed locally; the rest of AI Analysis remains "
                "available."),
            missing_measurements=("pullback_calculation_failed",),
            primary_rejection_reason=reason,
            rejection_reasons=(reason,),
        )


# --------------------------------------------------------------------------- #
# Classification / level / scenario / confidence internals
# --------------------------------------------------------------------------- #

def _classify_trend(last_close, sma_vals, ema_vals):
    s20, s50, s200 = sma_vals.get(20), sma_vals.get(50), sma_vals.get(200)
    if last_close is None or s20 is None or s50 is None:
        return TrendState.DATA_INSUFFICIENT, None
    e20, e50, e200 = ema_vals.get(20), ema_vals.get(50), ema_vals.get(200)
    above_200 = s200 is None or last_close > s200
    below_200 = s200 is None or last_close < s200
    ema_strong_up = all(v is not None for v in (e20, e50, e200)) and e20 > e50 > e200
    ema_strong_down = all(v is not None for v in (e20, e50, e200)) and e20 < e50 < e200
    if last_close > s20 > s50 and above_200:
        state = TrendState.STRONG_UPTREND if ema_strong_up else TrendState.UPTREND
        return state, 100.0 if state == TrendState.STRONG_UPTREND else 80.0
    if last_close < s20 < s50 and below_200:
        state = TrendState.STRONG_DOWNTREND if ema_strong_down else TrendState.DOWNTREND
        return state, 100.0 if state == TrendState.STRONG_DOWNTREND else 80.0
    if last_close > s50 and above_200:
        return TrendState.UPTREND, 65.0
    if last_close < s50 and below_200:
        return TrendState.DOWNTREND, 65.0
    return TrendState.SIDEWAYS, 50.0


def _classify_momentum(rsi_val, macd_hist_val):
    if rsi_val is None:
        return MomentumState.DATA_INSUFFICIENT, None
    hist_pos = macd_hist_val is not None and macd_hist_val > 0
    hist_neg = macd_hist_val is not None and macd_hist_val < 0
    if rsi_val >= 70:
        return MomentumState.STRONG_POSITIVE, min(100.0, float(rsi_val))
    if rsi_val >= 60 and hist_pos:
        return MomentumState.STRONG_POSITIVE, min(100.0, float(rsi_val))
    if rsi_val >= 55 or (rsi_val >= 50 and hist_pos):
        return MomentumState.POSITIVE, max(55.0, float(rsi_val))
    if rsi_val <= 30:
        return MomentumState.STRONG_NEGATIVE, min(100.0, 100.0 - float(rsi_val))
    if rsi_val <= 40 and hist_neg:
        return MomentumState.STRONG_NEGATIVE, min(100.0, 100.0 - float(rsi_val))
    if rsi_val <= 45 or (rsi_val < 50 and hist_neg):
        return MomentumState.NEGATIVE, max(55.0, 100.0 - float(rsi_val))
    return MomentumState.NEUTRAL, 50.0


def _distance_pct(level, ref):
    if level is None or ref in (None, 0):
        return None
    return _r((level - ref) / ref * 100.0, PCT_DP)


def _key_levels(frame, last_close, sma_vals, ema_vals, channel_high, channel_low, atr_val):
    levels: list[KeyLevel] = []
    if last_close is None:
        return levels

    def add(kind, price, basis, confidence=None):
        if price is None:
            return
        touches, last_touch = _level_touches(frame, price, atr_val)
        strength = min(1.0, (confidence or 0.4) + min(touches, 5) * 0.06)
        levels.append(KeyLevel(kind=kind, price=_r(price, PRICE_DP), basis=basis,
                               timeframe="1D", touches=touches,
                               last_touch_date=last_touch,
                               distance_percent=_distance_pct(price, last_close),
                               strength=_r(strength, 2), confidence=confidence))

    add("RESISTANCE", channel_high, f"{CHANNEL_WINDOW}d high", 0.7)
    add("BREAKOUT", channel_high, f"{CHANNEL_WINDOW}d high breakout", 0.7)
    add("SUPPORT", channel_low, f"{CHANNEL_WINDOW}d low", 0.6)
    for period in SMA_PERIODS:
        val = sma_vals.get(period)
        if val is not None:
            add("SUPPORT" if val <= last_close else "RESISTANCE", val, f"SMA{period}", 0.5)
    for period in EMA_PERIODS:
        val = ema_vals.get(period)
        if val is not None:
            add("SUPPORT" if val <= last_close else "RESISTANCE", val, f"EMA{period}", 0.45)
    if atr_val is not None:
        add("STOP", last_close - ATR_STOP_MULT * atr_val, f"{ATR_STOP_MULT}x ATR below last close")

    levels.sort(key=lambda lv: (
        lv.kind,
        abs(lv.distance_percent) if lv.distance_percent is not None else 1e9,
    ))
    return levels


def _level_touches(frame, price, atr_val):
    """Count completed-session touches without inventing intraday precision."""
    if frame is None or frame.empty or price in (None, 0):
        return 0, None
    recent = frame.tail(60)
    tolerance = max(abs(float(price)) * 0.005, float(atr_val or 0) * 0.25)
    touched = (
        (recent["Low"].astype(float) <= float(price) + tolerance)
        & (recent["High"].astype(float) >= float(price) - tolerance)
    )
    dates = recent.index[touched]
    return int(touched.sum()), (
        pd.Timestamp(dates[-1]).date().isoformat() if len(dates) else None
    )


def _chart_series(frame, *, timeframe, source, latest_completed_session, intraday):
    """Convert provider-normalized frames to the typed chart contract."""
    if frame is None or frame.empty:
        return None
    points = []
    continuous = []
    auction = []
    local_dates = []
    for timestamp, row in frame.iterrows():
        ts = pd.Timestamp(timestamp)
        if intraday:
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            cairo = ts.tz_convert("Africa/Cairo")
            local_dates.append(cairo.date())
            minute = cairo.hour * 60 + cairo.minute
            if 10 * 60 <= minute < 14 * 60 + 15:
                phase = "CONTINUOUS"
            elif 14 * 60 + 15 <= minute < 14 * 60 + 25:
                phase = "CLOSING_AUCTION"
            elif minute < 10 * 60:
                phase = "PRE_SESSION"
            else:
                phase = "CLOSED"
        else:
            cairo = ts
            local_dates.append(ts.date())
            phase = "COMPLETED_SESSION"
        point = ChartPoint(
            timestamp=cairo.isoformat(),
            open=_r(row.get("Open"), PRICE_DP),
            high=_r(row.get("High"), PRICE_DP),
            low=_r(row.get("Low"), PRICE_DP),
            close=_r(row.get("Close"), PRICE_DP),
            volume=_r(row.get("Volume"), VOL_DP),
            source=source,
            session_phase=phase,
        )
        points.append(point)
        if phase == "CONTINUOUS":
            continuous.append(point)
        elif phase == "CLOSING_AUCTION":
            auction.append(point)
    session_date = max(local_dates).isoformat() if local_dates else None
    if intraday:
        # The contract exposes the most recent Rubix session only. Older stored minutes
        # cannot be mistaken for a current/live chart.
        points = [p for p in points if pd.Timestamp(p.timestamp).date().isoformat() == session_date]
        continuous = [
            p for p in continuous if pd.Timestamp(p.timestamp).date().isoformat() == session_date
        ]
        auction = [
            p for p in auction if pd.Timestamp(p.timestamp).date().isoformat() == session_date
        ]
    return ChartSeries(
        timeframe=timeframe,
        session_date=session_date,
        points=tuple(points),
        continuous_points=tuple(continuous),
        auction_points=tuple(auction),
        latest_completed_session=latest_completed_session,
        source=source,
        data_status="AVAILABLE" if points else "UNAVAILABLE",
    )


def _scenarios(*, last_close, channel_high, channel_low, atr_val, trend, momentum,
               volume_safe, volume_ratio, usable):
    if not usable or last_close is None or channel_high is None or atr_val is None:
        return (ScenarioResult(
            scenario_id="data_insufficient", title="Insufficient data",
            state=ScenarioState.DATA_INSUFFICIENT,
            invalidation_conditions=("sufficient history for a scenario required",)),)

    breakout = channel_high
    entry_low = _r(breakout, PRICE_DP)
    entry_high = _r(breakout + 0.5 * atr_val, PRICE_DP)
    target = _r(breakout + ATR_TARGET_MULT * atr_val, PRICE_DP)
    stop = _r(max(channel_low or 0.0, last_close - ATR_STOP_MULT * atr_val), PRICE_DP)
    rr = None
    if entry_low is not None and stop is not None and target is not None:
        risk = entry_low - stop
        reward = target - entry_low
        if risk > 0:
            rr = _r(reward / risk, PCT_DP)
    remaining_room = None
    if target is not None and last_close:
        remaining_room = _r((target - last_close) / last_close * 100.0, PCT_DP)

    dist_to_breakout = (breakout - last_close) / last_close * 100.0 if last_close else None
    requirements = [f"daily close above {entry_low}"]
    requirements.append("volume >= average_volume_20" if volume_safe
                        else "resolve volume safety before trusting confirmation")
    invalidation = [f"daily close below {stop}"]
    if not volume_safe:
        invalidation.append("volume unsafe for lookback")

    bearish = trend in ("DOWNTREND", "STRONG_DOWNTREND") or momentum in (
        "NEGATIVE", "STRONG_NEGATIVE")
    if bearish:
        state = ScenarioState.AVOID
    elif not volume_safe:
        state = ScenarioState.WAIT
    elif last_close >= breakout:
        state = ScenarioState.READY_WITH_CONDITIONS
    elif dist_to_breakout is not None and dist_to_breakout <= 3.0 and trend in (
            "UPTREND", "STRONG_UPTREND"):
        state = ScenarioState.NEAR_READY
    else:
        state = ScenarioState.WAIT

    primary = ScenarioResult(
        scenario_id="breakout_continuation",
        title=f"Breakout continuation above {entry_low}",
        state=state, trigger=entry_low, entry_low=entry_low, entry_high=entry_high,
        target=target, stop=stop, remaining_room_percent=remaining_room, risk_reward=rr,
        confidence=_SCENARIO_CONFIDENCE.get(state),
        confirmation_requirements=tuple(requirements),
        invalidation_conditions=tuple(invalidation),
    )

    breakdown_state = (ScenarioState.INVALID if last_close is not None and stop is not None
                       and last_close < stop else ScenarioState.WAIT)
    breakdown = ScenarioResult(
        scenario_id="support_breakdown", title=f"Breakdown below {stop}",
        state=breakdown_state, trigger=stop, stop=stop,
        confidence=_SCENARIO_CONFIDENCE.get(breakdown_state),
        invalidation_conditions=(f"daily close below {stop}",),
    )
    return (primary, breakdown)


def _confidence(*, trend, momentum, volume_safe, volume_ratio, avg_turnover,
                history_sufficient, usable):
    trend_score = {
        "STRONG_UPTREND": 85.0, "UPTREND": 65.0, "SIDEWAYS": 45.0,
        "DOWNTREND": 30.0, "STRONG_DOWNTREND": 15.0,
        "DATA_INSUFFICIENT": 25.0,
    }.get(trend, 40.0)
    momentum_score = {
        "STRONG_POSITIVE": 85.0, "POSITIVE": 70.0, "NEUTRAL": 50.0,
        "NEGATIVE": 30.0, "STRONG_NEGATIVE": 15.0,
        "DATA_INSUFFICIENT": 30.0,
    }.get(momentum, 45.0)

    # Volume must never lift confidence when it is not lookback-safe.
    liquidity_score = 20.0
    if volume_safe:
        liquidity_score = 60.0
        if avg_turnover is not None and avg_turnover >= 5_000_000:
            liquidity_score += 20.0
        if volume_ratio is not None and volume_ratio >= 1.0:
            liquidity_score += 15.0
        liquidity_score = min(liquidity_score, 100.0)

    # Data-quality score uses only completed-session facts (NOT the volatile live quote),
    # so confidence — and therefore evidence_hash — is a pure function of the frame.
    dq_score = (50.0 if usable else 0.0) + (50.0 if history_sufficient else 0.0)

    components = (
        ConfidenceComponent("trend", 0.35, _r(trend_score, CONF_DP)),
        ConfidenceComponent("momentum", 0.25, _r(momentum_score, CONF_DP)),
        ConfidenceComponent("liquidity", 0.20, _r(liquidity_score, CONF_DP)),
        ConfidenceComponent("data_quality", 0.20, _r(dq_score, CONF_DP)),
    )
    overall = sum(c.weight * c.score for c in components)
    return ConfidenceBreakdown(overall=_r(overall, CONF_DP),
                               method_version=CONFIDENCE_METHOD_VERSION,
                               components=components)


def _data_quality(md, *, volume_safe, history_sufficient, usable, live_quote,
                  latest_session):
    """Derive provenance from frame metadata (never hard-coded).

    The Yahoo-never invariant is *verified* here from ``md['yahoo_network_used']`` — the
    router always sets it False — rather than assumed.
    """
    freshness = str(md.get("freshness_status", "UNKNOWN"))
    router_status = str(md.get("data_quality_status", ""))
    provider = str(md.get("provider", "eodhd"))
    if not usable:
        status = DataStatus.HISTORY_INSUFFICIENT
    elif not volume_safe:
        status = DataStatus.VOLUME_UNSAFE
    elif live_quote is not None and not live_quote.get("available"):
        status = DataStatus.LIVE_UNAVAILABLE
    elif freshness in ("HISTORY_CURRENT", "SHADOW", "CURRENT"):
        status = DataStatus.CURRENT
    elif "STALE" in freshness or "PENDING" in freshness:
        status = DataStatus.CACHE_MODE
    else:
        status = DataStatus.CURRENT if history_sufficient else DataStatus.CACHE_MODE

    notes = []
    if md.get("volume_series"):
        notes.append(f"volume_series={md.get('volume_series')}")
    if md.get("routing_tier"):
        notes.append(f"tier={md.get('routing_tier')}")
    if router_status:
        notes.append(f"router_status={router_status}")

    return DataQualitySummary(
        status=status,
        data_domain=str(md.get("data_domain", "CURRENT_RESEARCH_V2")),
        provider=provider,
        freshness_status=freshness,
        expected_completed_session=md.get("expected_completed_session"),
        latest_completed_session=(latest_session and str(latest_session))
        or md.get("latest_completed_session"),
        history_sufficient=history_sufficient,
        volume_safe_for_lookback=volume_safe,
        live_provider=str(md.get("live_provider", "rubix")),
        live_available=bool(live_quote and live_quote.get("available")),
        yahoo_network_used=bool(md.get("yahoo_network_used", False)),   # derived
        yahoo_seed_present=bool(md.get("yahoo_seed_present", False)),   # derived
        notes=tuple(notes),
    )


def _recommendation(*, data_status, primary_state, trend, momentum, last_close, sma_vals,
                    ema_vals, volume_safe, volume_ratio, rsi_val, macd_hist_val, obv_slope,
                    range_position, channel_high):
    """Machine reasons — human-readable audit strings. They are SUPPLEMENTARY: every
    number they mention already exists as a typed evidence field; the UI never parses
    them for values."""
    reasons: list[str] = []
    s20, s50 = sma_vals.get(20), sma_vals.get(50)
    if last_close is not None and s20 is not None and s50 is not None:
        if last_close > s20 > s50:
            reasons.append("price above SMA20 and SMA50")
        elif last_close < s20 < s50:
            reasons.append("price below SMA20 and SMA50")
    e20 = ema_vals.get(20)
    if last_close is not None and e20 is not None:
        reasons.append(f"price {'above' if last_close >= e20 else 'below'} EMA20")
    if rsi_val is not None:
        reasons.append(f"RSI14 {momentum.lower()}")
    if macd_hist_val is not None:
        reasons.append(f"MACD histogram {'positive' if macd_hist_val > 0 else 'negative'}")
    if volume_safe:
        reasons.append("volume safe for lookback")
        if volume_ratio is not None:
            reasons.append("volume ratio at or above average"
                           if volume_ratio >= 1.0 else "volume ratio below average")
        if obv_slope is not None:
            reasons.append(f"OBV {'rising' if obv_slope > 0 else 'falling'}")
    else:
        reasons.append("volume unsafe for lookback (OBV suppressed)")
    if range_position is not None:
        reasons.append("upper half of the recent channel"
                       if range_position >= 50 else "lower half of the recent channel")

    if data_status in (DataStatus.HISTORY_INSUFFICIENT, DataStatus.DATA_UNAVAILABLE):
        return Recommendation.DATA_INSUFFICIENT, tuple(reasons or ("insufficient history",))

    mapping = {
        ScenarioState.READY_WITH_CONDITIONS: Recommendation.READY_WITH_CONDITIONS,
        ScenarioState.NEAR_READY: Recommendation.NEAR_READY,
        ScenarioState.WAIT: Recommendation.WAIT,
        ScenarioState.AVOID: Recommendation.AVOID,
        ScenarioState.INVALID: Recommendation.AVOID,
        ScenarioState.DATA_INSUFFICIENT: Recommendation.DATA_INSUFFICIENT,
    }
    rec = mapping.get(primary_state, Recommendation.WAIT)
    if rec == Recommendation.WAIT and trend in ("UPTREND", "STRONG_UPTREND") and volume_safe:
        rec = Recommendation.WATCH
    if channel_high is not None and last_close is not None and last_close < channel_high:
        reasons.append("awaiting close above resistance")
    return rec, tuple(reasons)


# --------------------------------------------------------------------------- #
# Hashing / reproducibility
# --------------------------------------------------------------------------- #

# Live-derived / volatile fields excluded from the deterministic hash.
_PRICE_LIVE_FIELDS = ("last", "bid", "ask", "spread_percent", "quote_timestamp")
_DQ_VOLATILE_FIELDS = ("freshness_status", "live_available")


def _hash_payload(result: AnalysisResult) -> dict:
    """Canonical numeric/decision content for hashing (excludes wall-clock/live fields)."""
    return {
        "engine": EVIDENCE_ENGINE_VERSION,
        "symbol": result.request.symbol,
        "market_phase": result.market_phase.value,
        "price": {k: v for k, v in asdict(result.price).items()
                  if k not in _PRICE_LIVE_FIELDS},
        "indicators": asdict(result.indicators),
        "key_levels": [asdict(k) for k in result.key_levels],
        "scenarios": [asdict(s) for s in result.scenarios],
        "pullback_scenario": (
            asdict(result.pullback_scenario) if result.pullback_scenario else None),
        "confidence": asdict(result.confidence),
        "data_quality": {k: v for k, v in asdict(result.data_quality).items()
                         if k not in _DQ_VOLATILE_FIELDS},
        "recommendation": result.recommendation.value,
        "recommendation_reasons": list(result.recommendation_reasons),
    }


def _evidence_hash(result: AnalysisResult) -> str:
    payload = json.dumps(_hash_payload(result), sort_keys=True, default=str,
                         separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _replace_result(result: AnalysisResult, **changes) -> AnalysisResult:
    data = {
        "request": result.request, "price": result.price, "indicators": result.indicators,
        "confidence": result.confidence, "data_quality": result.data_quality,
        "recommendation": result.recommendation, "market_phase": result.market_phase,
        "evidence_version": result.evidence_version, "generated_at": result.generated_at,
        "key_levels": result.key_levels, "scenarios": result.scenarios,
        "pullback_scenario": result.pullback_scenario,
        "recommendation_reasons": result.recommendation_reasons,
        "daily_chart_series": result.daily_chart_series,
        "intraday_chart_series": result.intraday_chart_series,
        "evidence_hash": result.evidence_hash,
    }
    data.update(changes)
    return AnalysisResult(**data)
