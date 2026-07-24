"""Scalping V2 — intraday range detection and classification (isolated).

Pure geometry over already-validated intraday bars. No look-ahead: every level
is computed only from bars at or before the decision timestamp. Never computes
levels from incomplete/malformed bars (the caller passes sanitized bars, and
this module additionally guards against empty/degenerate input).

This module is research/decision-support only and is completely independent of
the Swing/Daily engine, the Adaptive Selector, and the preserved fixed-2%
scalping strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class RangeClass(str, Enum):
    CLEAN_RANGE = "CLEAN_RANGE"
    EXPANDING_RANGE = "EXPANDING_RANGE"
    TRENDING_UP = "TRENDING_UP"
    BREAKOUT_IN_PROGRESS = "BREAKOUT_IN_PROGRESS"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    CHOPPY = "CHOPPY"
    ILLIQUID = "ILLIQUID"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


@dataclass(frozen=True)
class RangeLevels:
    classification: RangeClass
    bar_count: int
    minutes_elapsed: float
    session_open: float | None = None
    session_high: float | None = None
    session_low: float | None = None
    last: float | None = None
    midpoint: float | None = None
    upper_third: float | None = None
    lower_third: float | None = None
    opening_range_15_high: float | None = None
    opening_range_15_low: float | None = None
    opening_range_30_high: float | None = None
    opening_range_30_low: float | None = None
    vwap: float | None = None
    vwap_valid: bool = False
    intraday_support: float | None = None
    intraday_resistance: float | None = None
    prev_day_high: float | None = None
    prev_day_low: float | None = None
    prev_day_close: float | None = None
    range_position_percent: float | None = None
    session_range_percent: float | None = None
    reason: str = ""
    detail: dict = field(default_factory=dict)

    def as_row(self):
        payload = {k: v for k, v in self.__dict__.items() if k != "detail"}
        payload["classification"] = self.classification.value
        return payload


def detect_range(
    session_bars: pd.DataFrame,
    *,
    prev_day=None,
    min_bars=15,
    lower_zone_percent=33.0,
    upper_zone_percent=66.0,
    swing_window=5,
):
    """Return :class:`RangeLevels` for one session's validated bars.

    ``session_bars`` must be a DatetimeIndex OHLCV frame already sanitized
    (positive OHLC, ordered) and restricted to the current session. ``prev_day``
    is an optional dict with High/Low/Close from the prior completed session.
    """

    if session_bars is None or session_bars.empty:
        return RangeLevels(RangeClass.DATA_INSUFFICIENT, 0, 0.0,
                           reason="no session bars")
    frame = session_bars.sort_index()
    bar_count = len(frame)
    minutes_elapsed = _minutes_span(frame)
    prev = prev_day or {}
    base = dict(
        bar_count=bar_count, minutes_elapsed=minutes_elapsed,
        prev_day_high=_opt(prev.get("High")),
        prev_day_low=_opt(prev.get("Low")),
        prev_day_close=_opt(prev.get("Close")),
    )

    if bar_count < min_bars:
        return RangeLevels(RangeClass.DATA_INSUFFICIENT, reason=(
            f"only {bar_count} valid bars (< {min_bars} minimum)"), **base)

    session_open = float(frame["Open"].iloc[0])
    session_high = float(frame["High"].max())
    session_low = float(frame["Low"].min())
    last = float(frame["Close"].iloc[-1])
    span = session_high - session_low

    if session_open <= 0 or span <= 0:
        return RangeLevels(RangeClass.DATA_INSUFFICIENT, session_open=session_open,
                           session_high=session_high, session_low=session_low,
                           last=last, reason="degenerate session range (span<=0)", **base)

    midpoint = session_low + span * 0.5
    lower_third = session_low + span * (lower_zone_percent / 100.0)
    upper_third = session_low + span * (upper_zone_percent / 100.0)
    range_position = (last - session_low) / span * 100.0
    session_range_percent = span / session_open * 100.0

    or15 = _opening_range(frame, 15)
    or30 = _opening_range(frame, 30)
    vwap, vwap_valid = _vwap(frame)
    support, resistance = _intraday_support_resistance(frame, swing_window)

    classification = _classify(
        frame, session_open, session_high, session_low, last, span,
        or15, range_position,
    )

    return RangeLevels(
        classification=classification,
        session_open=session_open, session_high=session_high, session_low=session_low,
        last=last, midpoint=midpoint, upper_third=upper_third, lower_third=lower_third,
        opening_range_15_high=or15[0], opening_range_15_low=or15[1],
        opening_range_30_high=or30[0], opening_range_30_low=or30[1],
        vwap=vwap, vwap_valid=vwap_valid,
        intraday_support=support, intraday_resistance=resistance,
        range_position_percent=round(range_position, 2),
        session_range_percent=round(session_range_percent, 4),
        reason=classification.value,
        **base,
    )


def _classify(frame, s_open, s_high, s_low, last, span, or15, range_position):
    """Explainable, conservative range classification."""

    close = frame["Close"]
    # Directionality: net move from open vs. the total path traversed.
    net_move = abs(last - s_open)
    path = close.diff().abs().sum()
    directionality = net_move / path if path > 0 else 0.0

    # Recent range expansion: last-third bar ranges vs first-third.
    n = len(frame)
    third = max(1, n // 3)
    early_rng = (frame["High"].head(third) - frame["Low"].head(third)).mean()
    late_rng = (frame["High"].tail(third) - frame["Low"].tail(third)).mean()
    expanding = late_rng > early_rng * 1.5 if early_rng > 0 else False

    or15_high = or15[0]
    above_or = or15_high is not None and last > or15_high

    # Strong one-directional drift -> trending, not a mean-reverting range.
    if directionality >= 0.5 and last > s_open and range_position >= 60:
        # Distinguish a fresh breakout push from an established uptrend.
        if above_or and range_position >= 85:
            return RangeClass.BREAKOUT_IN_PROGRESS
        return RangeClass.TRENDING_UP

    # Poked above opening range then fell back into it -> failed breakout.
    if or15_high is not None and s_high > or15_high * 1.002 and last < or15_high:
        return RangeClass.FAILED_BREAKOUT

    if expanding:
        return RangeClass.EXPANDING_RANGE

    # Choppy: lots of direction changes with little net progress.
    if directionality < 0.25:
        return RangeClass.CHOPPY

    return RangeClass.CLEAN_RANGE


def _opening_range(frame, minutes):
    if frame.empty:
        return (None, None)
    start = frame.index[0]
    window = frame[frame.index <= start + pd.Timedelta(minutes=minutes)]
    if window.empty or len(window) < 2:
        return (None, None)
    return (float(window["High"].max()), float(window["Low"].min()))


def _vwap(frame):
    volume = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0)
    if volume.sum() <= 0:
        return (None, False)
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    vwap = float((typical * volume).sum() / volume.sum())
    return (vwap, True)


def _intraday_support_resistance(frame, window):
    """Nearest swing low below last (support) and swing high above (resistance)."""

    if len(frame) < window * 2 + 1:
        return (None, None)
    highs = frame["High"]
    lows = frame["Low"]
    last = float(frame["Close"].iloc[-1])
    swing_highs = []
    swing_lows = []
    for i in range(window, len(frame) - window):
        hi = highs.iloc[i]
        lo = lows.iloc[i]
        if hi == highs.iloc[i - window:i + window + 1].max():
            swing_highs.append(float(hi))
        if lo == lows.iloc[i - window:i + window + 1].min():
            swing_lows.append(float(lo))
    support = max((s for s in swing_lows if s < last), default=None)
    resistance = min((r for r in swing_highs if r > last), default=None)
    return (support, resistance)


def _minutes_span(frame):
    if len(frame) < 2:
        return 0.0
    return (frame.index[-1] - frame.index[0]).total_seconds() / 60.0


def _opt(value):
    if value is None or pd.isna(value):
        return None
    return float(value)
