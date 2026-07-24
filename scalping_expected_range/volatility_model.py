"""Phase 2 — historical volatility model for EXPECTED_RANGE_SCALPER.

From completed daily OHLCV only, derive the daily-range distribution (ADR%,
median range%, ATR%), upside/downside excursions from the previous Close, range
consistency at several thresholds, the fixed-2%-target opportunity frequency,
volatility stability and the recent volatility trend.

IMPORTANT daily-bar limitation: daily OHLC does not reveal the intraday order of
High and Low. These metrics rank *opportunity potential* only — they never claim
a 2% trade was executable. Execution is confirmed with live/chronological
evidence elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

CONSISTENT_HIGH_VOLATILITY = "CONSISTENT_HIGH_VOLATILITY"
RISING_VOLATILITY = "RISING_VOLATILITY"
NORMAL_VOLATILITY = "NORMAL_VOLATILITY"
DECLINING_VOLATILITY = "DECLINING_VOLATILITY"
ONE_DAY_SPIKE = "ONE_DAY_SPIKE"
INSUFFICIENT_VOLATILITY = "INSUFFICIENT_VOLATILITY"


@dataclass
class VolatilityProfile:
    sessions_available: int = 0
    adr_percent_20: float | None = None
    adr_percent_10: float | None = None
    adr_percent_30: float | None = None
    median_range_percent_20: float | None = None
    atr_percent_14: float | None = None

    avg_upside_excursion_percent: float | None = None
    avg_downside_excursion_percent: float | None = None
    median_upside_excursion_percent: float | None = None
    median_downside_excursion_percent: float | None = None

    range_ge_1pct: float | None = None
    range_ge_2pct: float | None = None
    range_ge_3pct: float | None = None
    range_ge_4pct: float | None = None
    range_ge_5pct: float | None = None

    target_2pct_frequency: float | None = None   # daily range >= 2%
    upside_2pct_frequency: float | None = None    # High >= +2% vs prev close

    volatility_stability: float | None = None     # 1 - normalized dispersion
    one_day_spike_ratio: float | None = None      # max range / median range
    classification: str = INSUFFICIENT_VOLATILITY

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _daily_frames(daily: pd.DataFrame):
    high = pd.to_numeric(daily["High"], errors="coerce")
    low = pd.to_numeric(daily["Low"], errors="coerce")
    close = pd.to_numeric(daily["Close"], errors="coerce")
    prev_close = close.shift(1)
    frame = pd.DataFrame({"High": high, "Low": low, "Close": close, "PrevClose": prev_close})
    frame = frame.dropna(subset=["High", "Low", "PrevClose"])
    frame = frame[frame["PrevClose"] > 0]
    return frame


def compute_volatility(daily: pd.DataFrame, cfg) -> VolatilityProfile:
    profile = VolatilityProfile()
    if daily is None or daily.empty:
        return profile
    frame = _daily_frames(daily)
    n = len(frame)
    profile.sessions_available = n
    if n < 5:
        return profile

    w = int(cfg.historical_window)
    daily_range_pct = (frame["High"] - frame["Low"]) / frame["PrevClose"] * 100.0
    upside = (frame["High"] - frame["PrevClose"]) / frame["PrevClose"] * 100.0
    downside = (frame["PrevClose"] - frame["Low"]) / frame["PrevClose"] * 100.0
    # Downside excursion is a magnitude (how far below prev close the low reached).
    downside = downside.clip(lower=0)
    upside = upside.clip(lower=0)

    win_range = daily_range_pct.tail(w)
    profile.adr_percent_20 = _r(daily_range_pct.tail(w).mean())
    profile.adr_percent_10 = _r(daily_range_pct.tail(10).mean())
    profile.adr_percent_30 = _r(daily_range_pct.tail(30).mean())
    profile.median_range_percent_20 = _r(win_range.median())

    profile.atr_percent_14 = _atr_percent(frame, cfg.atr_period)

    profile.avg_upside_excursion_percent = _r(upside.tail(w).mean())
    profile.avg_downside_excursion_percent = _r(downside.tail(w).mean())
    profile.median_upside_excursion_percent = _r(upside.tail(w).median())
    profile.median_downside_excursion_percent = _r(downside.tail(w).median())

    for thr, attr in [(1, "range_ge_1pct"), (2, "range_ge_2pct"), (3, "range_ge_3pct"),
                      (4, "range_ge_4pct"), (5, "range_ge_5pct")]:
        setattr(profile, attr, _r((win_range >= thr).mean(), 4))

    profile.target_2pct_frequency = _r((win_range >= 2.0).mean(), 4)
    profile.upside_2pct_frequency = _r((upside.tail(w) >= 2.0).mean(), 4)

    # Volatility stability: 1 - IQR/median of the daily range (broad vs spike).
    med = float(win_range.median()) if len(win_range) else 0.0
    if med > 0 and len(win_range) >= 4:
        iqr = float(win_range.quantile(0.75) - win_range.quantile(0.25))
        profile.volatility_stability = _r(max(0.0, min(1.0, 1.0 - iqr / med)), 4)
        profile.one_day_spike_ratio = _r(float(win_range.max()) / med, 3)

    profile.classification = _classify(daily_range_pct, cfg)
    return profile


def _atr_percent(frame: pd.DataFrame, period: int):
    tr = pd.concat([
        frame["High"] - frame["Low"],
        (frame["High"] - frame["PrevClose"]).abs(),
        (frame["Low"] - frame["PrevClose"]).abs(),
    ], axis=1).max(axis=1)
    if len(tr) < period:
        return None
    atr = tr.rolling(period).mean().iloc[-1]
    last_prev = float(frame["PrevClose"].iloc[-1])
    if not np.isfinite(atr) or last_prev <= 0:
        return None
    return _r(atr / last_prev * 100.0)


def _classify(daily_range_pct: pd.Series, cfg) -> str:
    if len(daily_range_pct) < 5:
        return INSUFFICIENT_VOLATILITY
    r5 = float(daily_range_pct.tail(5).median())
    r10 = float(daily_range_pct.tail(10).median())
    r20 = float(daily_range_pct.tail(int(cfg.historical_window)).median())
    win = daily_range_pct.tail(int(cfg.historical_window))
    med = float(win.median()) if len(win) else 0.0
    spike = (float(win.max()) / med) if med > 0 else 0.0

    # A single abnormal session dominating the window is a spike, not real vol.
    if med > 0 and spike >= 4.0 and float(win.tail(int(cfg.historical_window) - 1).median()) < 2.0:
        return ONE_DAY_SPIKE
    if r20 < 1.0:
        return INSUFFICIENT_VOLATILITY
    if r20 >= 2.0 and r10 >= 2.0 and r5 >= 2.0:
        return CONSISTENT_HIGH_VOLATILITY
    if r5 > r20 * 1.25:
        return RISING_VOLATILITY
    if r5 < r20 * 0.75:
        return DECLINING_VOLATILITY
    return NORMAL_VOLATILITY


def _r(value, digits=4):
    try:
        if value is None or not np.isfinite(float(value)):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None
