"""Phase 3 — expected daily range for EXPECTED_RANGE_SCALPER.

Builds three pre-session expected ranges (conservative / base / high-volatility)
from the historical distributions of upside and downside excursion measured from
the previous completed Close. Ranges are intentionally allowed to be asymmetric.

An optional open-adjusted range is produced once a reliable current-session Open
becomes available; it is added ALONGSIDE the pre-session forecast and never
silently replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class RangeBand:
    name: str
    percentile: float
    reference: float | None = None        # previous completed close
    expected_low: float | None = None
    expected_high: float | None = None
    expected_width_percent: float | None = None
    expected_upside_percent: float | None = None
    expected_downside_percent: float | None = None
    confidence: float | None = None       # 0..1
    observations: int = 0
    range_stability: float | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ExpectedRange:
    reference_close: float | None = None
    reference_kind: str = "PREVIOUS_COMPLETED_CLOSE"
    observations: int = 0
    conservative: RangeBand | None = None
    base: RangeBand | None = None
    high_volatility: RangeBand | None = None
    extreme: RangeBand | None = None         # max historical excursion reference
    open_adjusted: RangeBand | None = None   # populated only after a reliable Open

    def bands(self):
        return {"conservative": self.conservative, "base": self.base,
                "high_volatility": self.high_volatility, "extreme": self.extreme,
                "open_adjusted": self.open_adjusted}

    def as_dict(self) -> dict:
        out = {"reference_close": self.reference_close, "reference_kind": self.reference_kind,
               "observations": self.observations}
        for key, band in self.bands().items():
            if band is not None:
                for k, v in band.as_dict().items():
                    out[f"{key}_{k}"] = v
        return out


def _excursions(daily: pd.DataFrame, window: int):
    high = pd.to_numeric(daily["High"], errors="coerce")
    low = pd.to_numeric(daily["Low"], errors="coerce")
    close = pd.to_numeric(daily["Close"], errors="coerce")
    prev = close.shift(1)
    frame = pd.DataFrame({"High": high, "Low": low, "Prev": prev}).dropna()
    frame = frame[frame["Prev"] > 0].tail(window)
    upside = ((frame["High"] - frame["Prev"]) / frame["Prev"]).clip(lower=0)
    downside = ((frame["Prev"] - frame["Low"]) / frame["Prev"]).clip(lower=0)
    return upside, downside


def _stability(series: pd.Series):
    med = float(series.median()) if len(series) else 0.0
    if med <= 0 or len(series) < 4:
        return None
    iqr = float(series.quantile(0.75) - series.quantile(0.25))
    return round(float(max(0.0, min(1.0, 1.0 - iqr / med))), 4)


def _confidence(observations: int, minimum: int, stability):
    obs_conf = min(1.0, observations / max(1, minimum))
    stab = stability if stability is not None else 0.5
    return round(float(0.5 * obs_conf + 0.5 * stab), 4)


def _band(name, percentile, prev_close, upside, downside, minimum_sessions):
    up = float(np.percentile(upside, percentile)) if len(upside) else 0.0
    down = float(np.percentile(downside, percentile)) if len(downside) else 0.0
    low = prev_close * (1 - down)
    high = prev_close * (1 + up)
    width = (high - low) / prev_close * 100.0 if prev_close else None
    stability = _stability(pd.concat([upside, downside]))
    return RangeBand(
        name=name, percentile=percentile, reference=round(prev_close, 4),
        expected_low=round(low, 4), expected_high=round(high, 4),
        expected_width_percent=round(width, 4) if width is not None else None,
        expected_upside_percent=round(up * 100.0, 4),
        expected_downside_percent=round(down * 100.0, 4),
        observations=int(min(len(upside), len(downside))),
        range_stability=stability,
        confidence=_confidence(int(min(len(upside), len(downside))), minimum_sessions, stability),
    )


def compute_expected_range(daily: pd.DataFrame, cfg, prev_close=None) -> ExpectedRange:
    result = ExpectedRange()
    if daily is None or daily.empty:
        return result
    close = pd.to_numeric(daily["Close"], errors="coerce").dropna()
    if prev_close is None:
        prev_close = float(close.iloc[-1]) if len(close) else None
    if not prev_close or prev_close <= 0:
        return result
    result.reference_close = round(float(prev_close), 4)

    upside, downside = _excursions(daily, int(cfg.historical_window))
    result.observations = int(min(len(upside), len(downside)))
    if result.observations < 5:
        return result

    result.conservative = _band(
        "CONSERVATIVE", cfg.expected_range_conservative_percentile,
        prev_close, upside, downside, cfg.minimum_history_sessions)
    result.base = _band(
        "BASE", cfg.expected_range_base_percentile,
        prev_close, upside, downside, cfg.minimum_history_sessions)
    result.high_volatility = _band(
        "HIGH_VOLATILITY", cfg.expected_range_high_percentile,
        prev_close, upside, downside, cfg.minimum_history_sessions)
    # Extreme reference from the MAXIMUM historical excursion in the window. This
    # is an additional high-volatility extension reference and does NOT change the
    # configured p25/p50/p75 percentiles.
    up_max = float(np.max(upside)) if len(upside) else 0.0
    down_max = float(np.max(downside)) if len(downside) else 0.0
    stability = _stability(pd.concat([upside, downside]))
    result.extreme = RangeBand(
        name="EXTREME", percentile=100, reference=round(float(prev_close), 4),
        expected_low=round(prev_close * (1 - down_max), 4),
        expected_high=round(prev_close * (1 + up_max), 4),
        expected_width_percent=round((up_max + down_max) * 100.0, 4),
        expected_upside_percent=round(up_max * 100.0, 4),
        expected_downside_percent=round(down_max * 100.0, 4),
        observations=result.observations, range_stability=stability,
        confidence=_confidence(result.observations, cfg.minimum_history_sessions, stability))
    return result


def attach_open_adjusted(expected: ExpectedRange, daily: pd.DataFrame, cfg,
                         session_open: float) -> ExpectedRange:
    """Add an open-adjusted band from historical Open-to-High / Open-to-Low.

    Requires a reliable current-session Open. The pre-session bands are left
    untouched; the open-adjusted band is stored separately.
    """

    if expected is None or not session_open or session_open <= 0:
        return expected
    if daily is None or daily.empty or "Open" not in daily.columns:
        return expected
    o = pd.to_numeric(daily["Open"], errors="coerce")
    h = pd.to_numeric(daily["High"], errors="coerce")
    l = pd.to_numeric(daily["Low"], errors="coerce")
    frame = pd.DataFrame({"Open": o, "High": h, "Low": l}).dropna()
    frame = frame[frame["Open"] > 0].tail(int(cfg.historical_window))
    if len(frame) < 5:
        return expected
    up = ((frame["High"] - frame["Open"]) / frame["Open"]).clip(lower=0)
    down = ((frame["Open"] - frame["Low"]) / frame["Open"]).clip(lower=0)
    pct = cfg.expected_range_base_percentile
    up_p = float(np.percentile(up, pct))
    down_p = float(np.percentile(down, pct))
    stability = _stability(pd.concat([up, down]))
    expected.open_adjusted = RangeBand(
        name="OPEN_ADJUSTED", percentile=pct, reference=round(float(session_open), 4),
        expected_low=round(session_open * (1 - down_p), 4),
        expected_high=round(session_open * (1 + up_p), 4),
        expected_width_percent=round((up_p + down_p) * 100.0, 4),
        expected_upside_percent=round(up_p * 100.0, 4),
        expected_downside_percent=round(down_p * 100.0, 4),
        observations=len(frame), range_stability=stability,
        confidence=_confidence(len(frame), cfg.minimum_history_sessions, stability),
    )
    return expected
