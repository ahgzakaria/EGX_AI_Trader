"""Phase 1 — historical liquidity model for EXPECTED_RANGE_SCALPER.

Liquidity is the PRIMARY scalping factor. This module derives, from completed
daily OHLCV only, the average/median Volume and Turnover, their consistency,
recent trend and stability, plus a hard gate that flags illiquid or unreliable
symbols so that high volatility can never promote them into the best-candidate
views.

Median is used alongside the mean deliberately: one exceptional session must
never make an illiquid stock look liquid.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Liquidity hard-gate statuses (ordered from best to worst).
LIQUIDITY_VALID = "LIQUIDITY_VALID"
LIQUIDITY_LIMITED = "LIQUIDITY_LIMITED"
LIQUIDITY_TOO_LOW = "LIQUIDITY_TOO_LOW"
VOLUME_UNRELIABLE = "VOLUME_UNRELIABLE"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


@dataclass
class LiquidityProfile:
    status: str = DATA_INSUFFICIENT
    reasons: tuple = ()
    sessions_available: int = 0

    avg_volume_20: float | None = None
    avg_volume_10: float | None = None
    avg_volume_30: float | None = None
    median_volume_20: float | None = None

    avg_turnover_egp_20: float | None = None
    avg_turnover_egp_10: float | None = None
    avg_turnover_egp_30: float | None = None
    median_turnover_egp_20: float | None = None

    volume_consistency: float | None = None      # fraction of sessions >= ratio*median
    turnover_consistency: float | None = None     # fraction of sessions >= min tradable turnover
    low_volume_sessions: int = 0
    low_volume_fraction: float | None = None
    zero_volume_sessions: int = 0

    avg_volume_5: float | None = None
    volume_trend: str = "UNKNOWN"                 # RISING / STABLE / DETERIORATING
    volume_trend_ratio: float | None = None       # avg_5 / avg_20
    liquidity_stability: float | None = None      # 1 - normalized dispersion (0..1)
    one_session_dominance: float | None = None    # max session volume / total volume
    turnover_one_session_dominance: float | None = None  # max session turnover / total
    avg_traded_price: float | None = None         # avg turnover / avg volume (tradability)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["reasons"] = " | ".join(self.reasons)
        return d


def _safe_mean(series):
    series = pd.to_numeric(series, errors="coerce").dropna()
    return float(series.mean()) if len(series) else None


def _safe_median(series):
    series = pd.to_numeric(series, errors="coerce").dropna()
    return float(series.median()) if len(series) else None


def compute_liquidity(daily: pd.DataFrame, cfg) -> LiquidityProfile:
    """Compute the liquidity profile from completed daily OHLCV.

    ``daily`` must already be cleaned (no malformed/zero/forward-filled rows and
    no current incomplete candle) and indexed chronologically.
    """

    profile = LiquidityProfile()
    if daily is None or daily.empty:
        profile.reasons = ("NO_DAILY_HISTORY",)
        return profile

    volume = pd.to_numeric(daily["Volume"], errors="coerce")
    close = pd.to_numeric(daily["Close"], errors="coerce")
    turnover = close * volume

    n = int(volume.notna().sum())
    profile.sessions_available = n
    if n < cfg.minimum_history_sessions:
        profile.status = DATA_INSUFFICIENT
        profile.reasons = (f"ONLY_{n}_SESSIONS<{cfg.minimum_history_sessions}",)
        # still populate what we can for transparency
    w = int(cfg.historical_window)

    profile.avg_volume_20 = _safe_mean(volume.tail(w))
    profile.avg_volume_10 = _safe_mean(volume.tail(10))
    profile.avg_volume_30 = _safe_mean(volume.tail(30))
    profile.median_volume_20 = _safe_median(volume.tail(w))
    profile.avg_volume_5 = _safe_mean(volume.tail(5))

    profile.avg_turnover_egp_20 = _safe_mean(turnover.tail(w))
    profile.avg_turnover_egp_10 = _safe_mean(turnover.tail(10))
    profile.avg_turnover_egp_30 = _safe_mean(turnover.tail(30))
    profile.median_turnover_egp_20 = _safe_median(turnover.tail(w))

    window_vol = volume.tail(w).dropna()
    window_turn = turnover.tail(w).dropna()

    # Volume consistency: sessions where volume >= ratio * median volume.
    if profile.median_volume_20 and profile.median_volume_20 > 0 and len(window_vol):
        threshold = cfg.low_volume_session_ratio * profile.median_volume_20
        consistent = (window_vol >= threshold).mean()
        profile.volume_consistency = round(float(consistent), 4)
        low = int((window_vol < threshold).sum())
        profile.low_volume_sessions = low
        profile.low_volume_fraction = round(low / len(window_vol), 4)

    # Turnover consistency: sessions exceeding the minimum tradable turnover.
    if len(window_turn):
        profile.turnover_consistency = round(
            float((window_turn >= cfg.minimum_average_turnover_egp).mean()), 4)

    profile.zero_volume_sessions = int((window_vol <= 0).sum()) if len(window_vol) else 0

    # Volume trend: recent 5-session average vs 20-session average.
    if profile.avg_volume_5 and profile.avg_volume_20 and profile.avg_volume_20 > 0:
        ratio = profile.avg_volume_5 / profile.avg_volume_20
        profile.volume_trend_ratio = round(float(ratio), 4)
        if ratio >= 1.25:
            profile.volume_trend = "RISING"
        elif ratio <= 0.75:
            profile.volume_trend = "DETERIORATING"
        else:
            profile.volume_trend = "STABLE"

    # Liquidity stability: 1 - (IQR / median), clipped to [0, 1]. High means the
    # liquidity is broad-based rather than one unusual session.
    if profile.median_volume_20 and profile.median_volume_20 > 0 and len(window_vol) >= 4:
        iqr = float(window_vol.quantile(0.75) - window_vol.quantile(0.25))
        profile.liquidity_stability = round(
            float(max(0.0, min(1.0, 1.0 - iqr / profile.median_volume_20))), 4)
    if len(window_vol) and window_vol.sum() > 0:
        profile.one_session_dominance = round(
            float(window_vol.max() / window_vol.sum()), 4)
    if len(window_turn) and window_turn.sum() > 0:
        profile.turnover_one_session_dominance = round(
            float(window_turn.max() / window_turn.sum()), 4)
    # Approximate average traded price — a low value with huge volume signals a
    # penny share whose raw share count, not its tradable turnover, inflated it.
    if profile.avg_volume_20 and profile.avg_volume_20 > 0 and profile.avg_turnover_egp_20:
        profile.avg_traded_price = round(
            float(profile.avg_turnover_egp_20 / profile.avg_volume_20), 4)

    _apply_hard_gate(profile, cfg)
    return profile


def _apply_hard_gate(profile: LiquidityProfile, cfg) -> None:
    """Classify the symbol against the liquidity hard gate with exact reasons."""

    reasons = list(profile.reasons)

    if profile.sessions_available < cfg.minimum_history_sessions:
        profile.status = DATA_INSUFFICIENT
        if not reasons:
            reasons.append("INSUFFICIENT_SESSIONS")
        profile.reasons = tuple(reasons)
        return

    # Unreliable volume: too many zero-volume sessions or suspended-looking history.
    if profile.zero_volume_sessions > cfg.maximum_zero_volume_sessions:
        reasons.append(
            f"ZERO_VOLUME_SESSIONS={profile.zero_volume_sessions}>{cfg.maximum_zero_volume_sessions}")
        profile.status = VOLUME_UNRELIABLE
        profile.reasons = tuple(reasons)
        return
    if profile.median_volume_20 is None or profile.median_volume_20 <= 0:
        reasons.append("MEDIAN_VOLUME_ZERO_OR_MISSING")
        profile.status = VOLUME_UNRELIABLE
        profile.reasons = tuple(reasons)
        return

    hard_fail = []
    if (profile.avg_volume_20 or 0) < cfg.minimum_average_volume:
        hard_fail.append(
            f"AVG_VOLUME_20={_i(profile.avg_volume_20)}<{_i(cfg.minimum_average_volume)}")
    if (profile.median_volume_20 or 0) < cfg.minimum_median_volume:
        hard_fail.append(
            f"MEDIAN_VOLUME_20={_i(profile.median_volume_20)}<{_i(cfg.minimum_median_volume)}")
    if (profile.avg_turnover_egp_20 or 0) < cfg.minimum_average_turnover_egp:
        hard_fail.append(
            f"AVG_TURNOVER_20={_i(profile.avg_turnover_egp_20)}<{_i(cfg.minimum_average_turnover_egp)}")

    if hard_fail:
        profile.status = LIQUIDITY_TOO_LOW
        profile.reasons = tuple(reasons + hard_fail)
        return

    # Limited (not rejected): passes minimum liquidity but is inconsistent.
    limited = []
    if (profile.volume_consistency or 0) < cfg.minimum_volume_consistency:
        limited.append(
            f"VOLUME_CONSISTENCY={profile.volume_consistency}<{cfg.minimum_volume_consistency}")
    if (profile.low_volume_fraction or 0) > cfg.maximum_low_volume_session_fraction:
        limited.append(
            f"LOW_VOLUME_FRACTION={profile.low_volume_fraction}>{cfg.maximum_low_volume_session_fraction}")
    if (profile.one_session_dominance or 0) > 0.40:
        limited.append(f"ONE_SESSION_DOMINANCE={profile.one_session_dominance}>0.40")

    if limited:
        profile.status = LIQUIDITY_LIMITED
        profile.reasons = tuple(reasons + limited)
        return

    profile.status = LIQUIDITY_VALID
    profile.reasons = tuple(reasons)


def _i(value):
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "NA"
