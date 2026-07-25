"""Phase 2/3 — expected-range forecast calibration for EXPECTED_RANGE_SCALPER.

Strict walk-forward validation of the three expected-range bands
(Conservative / Base / High-Volatility). For every symbol-session the forecast
is built from prior completed sessions ONLY and frozen before the evaluated
session, then compared with the realized session. This measures *forecast
accuracy* (does the actual High/Low fall inside the predicted band) — a separate
question from candidate-selection success and from executable-scenario
performance.

Phase 3 (target-room) is opportunity-potential only: it asks whether a fixed +2%
target had statistical room from predeclared range locations. It never claims a
trade was executable — daily OHLC does not reveal the intraday order of High/Low.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scalping_expected_range.config import ExpectedRangeConfig
from scalping_expected_range.expected_range import compute_expected_range
from scalping_expected_range.historical_selector import HistoricalSelector

BANDS = ("conservative", "base", "high_volatility")


def _band_bounds(er, name):
    band = getattr(er, name, None)
    if band is None:
        return None
    return band.expected_low, band.expected_high


def run_forecast_calibration(symbols=None, config=None, test_sessions=40, cache=None,
                             now=None, holidays=()):
    cfg = config or ExpectedRangeConfig.load()
    selector = HistoricalSelector(config=cfg, cache=cache, holidays=holidays, now=now)
    from core.symbols import load_symbols
    symbols = tuple(symbols or load_symbols("data/symbols.csv"))

    obs = []          # per (symbol, session, band)
    room_obs = []     # per (symbol, session) target-room
    for s in symbols:
        daily, prov = selector._load_clean_daily(s)
        if daily is None or len(daily) < cfg.minimum_history_sessions + 6:
            continue
        daily = daily.copy()
        daily.index = pd.to_datetime(daily.index)
        closes = pd.to_numeric(daily["Close"], errors="coerce").values
        highs = pd.to_numeric(daily["High"], errors="coerce").values
        lows = pd.to_numeric(daily["Low"], errors="coerce").values
        opens = pd.to_numeric(daily["Open"], errors="coerce").values
        vols = pd.to_numeric(daily["Volume"], errors="coerce").values
        n = len(daily)
        start = max(cfg.minimum_history_sessions, n - test_sessions)
        for i in range(start, n):
            prev_close = closes[i - 1]
            if not np.isfinite(prev_close) or prev_close <= 0:
                continue
            hist = daily.iloc[:i]     # prior sessions only — no look-ahead
            er = compute_expected_range(hist, cfg, prev_close=prev_close)
            if er.base is None:
                continue
            actual_high, actual_low = highs[i], lows[i]
            actual_width = (actual_high - actual_low) / prev_close * 100.0
            # regime tags (computed from prior window only, plus this session's gap)
            adr_prior = float(np.nanmean(
                (highs[max(0, i - cfg.historical_window):i] - lows[max(0, i - cfg.historical_window):i])
                / closes[max(0, i - cfg.historical_window - 1):i - 1] * 100.0)) if i > cfg.historical_window else np.nan
            avg_vol_prior = float(np.nanmean(vols[max(0, i - cfg.historical_window):i]))
            gap = (opens[i] - prev_close) / prev_close * 100.0 if np.isfinite(opens[i]) else np.nan
            vtrend = _vol_trend(highs, lows, closes, i, cfg.historical_window)

            for name in BANDS:
                bounds = _band_bounds(er, name)
                if bounds is None:
                    continue
                exp_low, exp_high = bounds
                pred_width = (exp_high - exp_low) / prev_close * 100.0
                high_in = actual_high <= exp_high
                low_in = actual_low >= exp_low
                obs.append({
                    "Symbol": s, "SessionIndex": i, "Band": name,
                    "PrevClose": prev_close, "ActualHigh": actual_high, "ActualLow": actual_low,
                    "ExpLow": exp_low, "ExpHigh": exp_high,
                    "HighInside": bool(high_in), "LowInside": bool(low_in),
                    "FullInside": bool(high_in and low_in),
                    "HighErrorPct": (exp_high - actual_high) / prev_close * 100.0,
                    "LowErrorPct": (exp_low - actual_low) / prev_close * 100.0,
                    "UpperExceedPct": max(0.0, (actual_high - exp_high) / prev_close * 100.0),
                    "LowerExceedPct": max(0.0, (exp_low - actual_low) / prev_close * 100.0),
                    "PredWidthPct": pred_width, "ActualWidthPct": actual_width,
                    "UnusedWidthPct": pred_width - actual_width,
                    "AvgVolPrior": avg_vol_prior, "AdrPrior": adr_prior,
                    "GapPct": gap, "VolTrend": vtrend,
                })

            # Phase 3 target-room from base + high-vol bands.
            room_obs.append(_target_room(er, prev_close, actual_high, cfg, s, i))

    obs_frame = pd.DataFrame(obs)
    room_frame = pd.DataFrame([r for r in room_obs if r])
    return {
        "observations": obs_frame,
        "band_summary": _band_summary(obs_frame),
        "regime_summary": _regime_summary(obs_frame),
        "target_room": _room_summary(room_frame),
        "target_room_obs": room_frame,
    }


def _vol_trend(highs, lows, closes, i, w):
    def adr(a, b):
        if b - a < 2 or a < 1:
            return np.nan
        return float(np.nanmean((highs[a:b] - lows[a:b]) / closes[a - 1:b - 1] * 100.0))
    r5 = adr(max(1, i - 5), i)
    r20 = adr(max(1, i - w), i)
    if not np.isfinite(r5) or not np.isfinite(r20) or r20 == 0:
        return "UNKNOWN"
    if r5 > r20 * 1.25:
        return "RISING_VOL"
    if r5 < r20 * 0.75:
        return "DECLINING_VOL"
    return "STABLE_VOL"


def _target_room(er, prev_close, actual_high, cfg, symbol, i):
    base = er.base
    hv = er.high_volatility
    if base is None:
        return None
    tp = cfg.fixed_take_profit_percent / 100.0
    locations = {
        "lower_zone": base.expected_low,
        "midpoint": (base.expected_low + base.expected_high) / 2.0,
        "upper_middle": base.expected_low + 0.75 * (base.expected_high - base.expected_low),
        "upper_zone": base.expected_high,
    }
    row = {"Symbol": symbol, "SessionIndex": i, "PrevClose": prev_close}
    for loc, entry in locations.items():
        if entry <= 0:
            continue
        target = entry * (1 + tp)
        room_to_base = (base.expected_high - entry) / entry * 100.0
        row[f"{loc}_room_to_base_high_pct"] = room_to_base
        row[f"{loc}_has_2pct_room_base"] = bool(room_to_base >= cfg.fixed_take_profit_percent)
        row[f"{loc}_target_exceeds_base"] = bool(target > base.expected_high)
        row[f"{loc}_needs_highvol_ext"] = bool(
            hv is not None and target > base.expected_high and target <= hv.expected_high)
        row[f"{loc}_range_consumed"] = bool(room_to_base < cfg.no_chase_remaining_upside_percent)
    return row


def _band_summary(obs):
    if obs.empty:
        return pd.DataFrame()
    rows = []
    for name, g in obs.groupby("Band"):
        rows.append({
            "Band": name, "Observations": len(g),
            "FullRangeCoveragePct": round(g["FullInside"].mean() * 100, 2),
            "HighCoveragePct": round(g["HighInside"].mean() * 100, 2),
            "LowCoveragePct": round(g["LowInside"].mean() * 100, 2),
            "MedianHighErrorPct": round(g["HighErrorPct"].median(), 4),
            "MedianLowErrorPct": round(g["LowErrorPct"].median(), 4),
            "P90AbsHighErrorPct": round(g["HighErrorPct"].abs().quantile(0.9), 4),
            "P90AbsLowErrorPct": round(g["LowErrorPct"].abs().quantile(0.9), 4),
            "AvgPredWidthPct": round(g["PredWidthPct"].mean(), 4),
            "AvgActualWidthPct": round(g["ActualWidthPct"].mean(), 4),
            "AvgUnusedWidthPct": round(g["UnusedWidthPct"].mean(), 4),
            "UpperExceedRatePct": round((g["UpperExceedPct"] > 0).mean() * 100, 2),
            "LowerExceedRatePct": round((g["LowerExceedPct"] > 0).mean() * 100, 2),
            # forecast bias: mean signed width error (positive => forecast too wide)
            "WidthBiasPct": round(g["UnusedWidthPct"].mean(), 4),
        })
    order = {"conservative": 0, "base": 1, "high_volatility": 2}
    return pd.DataFrame(rows).sort_values("Band", key=lambda s: s.map(order)).reset_index(drop=True)


def _regime_summary(obs):
    if obs.empty:
        return pd.DataFrame()
    df = obs.copy()
    # liquidity tertiles
    df["LiquidityTier"] = pd.qcut(df["AvgVolPrior"].rank(method="first"), 3,
                                  labels=["LOW_LIQ", "MED_LIQ", "HIGH_LIQ"])
    # volatility tertiles
    df["VolatilityTier"] = pd.qcut(df["AdrPrior"].rank(method="first"), 3,
                                   labels=["LOW_VOL", "MED_VOL", "HIGH_VOL"])
    df["GapKind"] = np.where(df["GapPct"] > 0.3, "GAP_UP",
                             np.where(df["GapPct"] < -0.3, "GAP_DOWN", "FLAT_OPEN"))
    rows = []
    for dim, col in [("Liquidity", "LiquidityTier"), ("Volatility", "VolatilityTier"),
                     ("Gap", "GapKind"), ("VolTrend", "VolTrend")]:
        for (band, tier), g in df.groupby(["Band", col], observed=True):
            rows.append({
                "Dimension": dim, "Band": band, "Segment": str(tier),
                "Observations": len(g),
                "FullRangeCoveragePct": round(g["FullInside"].mean() * 100, 2),
                "HighCoveragePct": round(g["HighInside"].mean() * 100, 2),
                "LowCoveragePct": round(g["LowInside"].mean() * 100, 2),
                "AvgPredWidthPct": round(g["PredWidthPct"].mean(), 4),
                "AvgActualWidthPct": round(g["ActualWidthPct"].mean(), 4),
            })
    return pd.DataFrame(rows)


def _room_summary(room):
    if room.empty:
        return pd.DataFrame()
    rows = []
    for loc in ("lower_zone", "midpoint", "upper_middle", "upper_zone"):
        rc = f"{loc}_has_2pct_room_base"
        ex = f"{loc}_target_exceeds_base"
        cons = f"{loc}_range_consumed"
        ext = f"{loc}_needs_highvol_ext"
        if rc not in room.columns:
            continue
        rows.append({
            "EntryLocation": loc, "Observations": int(room[rc].notna().sum()),
            "Pct_2pct_room_to_ExpHigh": round(room[rc].mean() * 100, 2),
            "Pct_target_exceeds_ExpRange": round(room[ex].mean() * 100, 2),
            "Pct_RANGE_CONSUMED": round(room[cons].mean() * 100, 2),
            "Pct_needs_HighVol_extension": round(room[ext].mean() * 100, 2),
        })
    return pd.DataFrame(rows)
