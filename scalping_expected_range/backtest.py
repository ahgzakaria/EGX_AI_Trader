"""Phase 12/13 — validation for EXPECTED_RANGE_SCALPER.

Two DELIBERATELY SEPARATE questions:

A. PRE-SESSION SELECTION VALIDATION (implemented, real daily data):
   Using completed daily OHLCV only and strict walk-forward (rank on a trailing
   window, measure the NEXT session), does the liquidity-first ranking select
   stocks that actually delivered a >=2% daily range / >=2% upside more often
   than lower-ranked groups, and more consistently than a volatility-only
   ranking? This is candidate-quality validation, NOT proof of an executable
   trade — daily bars never reveal the intraday order of High and Low.

B. SCENARIO EXECUTION VALIDATION (honest scaffold):
   Requires genuine chronological intraday/event data (no future High/Low, entry
   on a future observable event, live Bid/Ask, costs). With only a handful of
   patched intraday sessions available, robust multi-scenario execution proof is
   NOT yet possible; this reports the honest status instead of fabricating one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from scalping_expected_range.config import ExpectedRangeConfig
from scalping_expected_range.historical_selector import HistoricalSelector
from core.symbols import SYMBOL_SOURCE


# --- A. selection validation -------------------------------------------------

def _session_features(frame: pd.DataFrame, window: int):
    """Trailing-window ranking features from a cleaned daily frame slice."""
    vol = pd.to_numeric(frame["Volume"], errors="coerce")
    close = pd.to_numeric(frame["Close"], errors="coerce")
    high = pd.to_numeric(frame["High"], errors="coerce")
    low = pd.to_numeric(frame["Low"], errors="coerce")
    prev = close.shift(1)
    rng = ((high - low) / prev * 100.0).replace([np.inf, -np.inf], np.nan)
    turn = close * vol
    w = frame.tail(window)
    rng_w = rng.tail(window).dropna()
    return {
        "avg_volume": float(vol.tail(window).mean()),
        "median_volume": float(vol.tail(window).median()),
        "avg_turnover": float(turn.tail(window).mean()),
        "adr": float(rng_w.mean()) if len(rng_w) else np.nan,
        "two_pct_freq": float((rng_w >= 2.0).mean()) if len(rng_w) else np.nan,
    }


def _outcome(frame: pd.DataFrame, t):
    """Realized range%/upside% at session t (prev close = the row before t)."""
    idx = list(frame.index)
    if t not in idx:
        return None
    i = idx.index(t)
    if i == 0:
        return None
    prev_close = float(frame["Close"].iloc[i - 1])
    if prev_close <= 0:
        return None
    hi = float(frame["High"].iloc[i]); lo = float(frame["Low"].iloc[i])
    return {
        "range_pct": (hi - lo) / prev_close * 100.0,
        "upside_pct": max(0.0, (hi - prev_close) / prev_close * 100.0),
    }


def _rank_score(feat, cfg):
    """Liquidity-first composite (cross-sectional ranks applied by caller)."""
    return feat  # scoring done cross-sectionally in run_selection_validation


def run_selection_validation(symbols=None, config=None, test_sessions=30, cache=None,
                             now=None, holidays=()):
    cfg = config or ExpectedRangeConfig.load()
    selector = HistoricalSelector(config=cfg, cache=cache, holidays=holidays, now=now)
    from core.symbols import load_symbols
    symbols = tuple(symbols or load_symbols(SYMBOL_SOURCE))

    frames = {}
    for s in symbols:
        daily, prov = selector._load_clean_daily(s)
        if daily is not None and len(daily) >= cfg.minimum_history_sessions + 5:
            daily = daily.copy()
            daily.index = pd.to_datetime(daily.index)
            frames[s] = daily
    if not frames:
        return {"status": "NO_DATA", "groups": pd.DataFrame(), "comparison": pd.DataFrame(),
                "test_dates": []}

    # Common calendar = union of dates; test on the last N dates that at least
    # 30 symbols share, to keep the cross-section meaningful.
    all_dates = sorted(set().union(*[set(f.index) for f in frames.values()]))
    counts = {d: sum(d in f.index for f in frames.values()) for d in all_dates}
    dense = [d for d in all_dates if counts[d] >= 30]
    test_dates = dense[-test_sessions:] if len(dense) > test_sessions else dense
    test_dates = [d for d in test_dates if all_dates.index(d) > cfg.minimum_history_sessions]

    group_rows = []
    per_date = []
    for t in test_dates:
        rows = []
        for s, f in frames.items():
            before = f[f.index < t]
            if len(before) < cfg.minimum_history_sessions:
                continue
            out = _outcome(f, t)
            if out is None:
                continue
            feat = _session_features(before, cfg.historical_window)
            rows.append({"Symbol": s, **feat, **out})
        if len(rows) < 30:
            continue
        d = pd.DataFrame(rows)
        # Cross-sectional percentiles -> liquidity-first composite score.
        d["p_vol"] = d["avg_volume"].rank(pct=True) * 100
        d["p_turn"] = d["avg_turnover"].rank(pct=True) * 100
        d["p_adr"] = d["adr"].rank(pct=True) * 100
        d["p_2p"] = d["two_pct_freq"].rank(pct=True) * 100
        d["p_cons"] = d["median_volume"].rank(pct=True) * 100
        d["liq_first_score"] = (
            cfg.average_volume_weight * d["p_vol"]
            + cfg.average_turnover_weight * d["p_turn"]
            + cfg.volatility_weight * d["p_adr"]
            + cfg.target_frequency_weight * d["p_2p"]
            + cfg.liquidity_consistency_weight * d["p_cons"]) / (
            cfg.average_volume_weight + cfg.average_turnover_weight + cfg.volatility_weight
            + cfg.target_frequency_weight + cfg.liquidity_consistency_weight)
        d["vol_only_score"] = d["p_adr"]

        n = len(d)
        groups = {
            "Top10_LiqFirst": d.nlargest(10, "liq_first_score"),
            "Top20_LiqFirst": d.nlargest(20, "liq_first_score"),
            "Top30_LiqFirst": d.nlargest(30, "liq_first_score"),
            "TopDecile_Liquidity": d.nlargest(max(1, n // 10), "p_vol"),
            "TopDecile_Volatility": d.nlargest(max(1, n // 10), "p_adr"),
            "TopDecile_Combined": d.nlargest(max(1, n // 10), "liq_first_score"),
            "TopDecile_VolatilityOnly": d.nlargest(max(1, n // 10), "vol_only_score"),
            "BottomHalf_LiqFirst": d.nsmallest(n // 2, "liq_first_score"),
            "AllSymbols": d,
        }
        for gname, g in groups.items():
            if g.empty:
                continue
            per_date.append({
                "TestDate": pd.Timestamp(t).date().isoformat(), "Group": gname,
                "Symbols": len(g),
                "Frac2pctRange": float((g["range_pct"] >= 2.0).mean()),
                "Frac2pctUpside": float((g["upside_pct"] >= 2.0).mean()),
                "AvgRange": float(g["range_pct"].mean()),
                "MedianRange": float(g["range_pct"].median()),
                "AvgVolume": float(g["avg_volume"].mean()),
                "AvgTurnover": float(g["avg_turnover"].mean()),
            })

    if not per_date:
        return {"status": "INSUFFICIENT_CROSS_SECTION", "groups": pd.DataFrame(),
                "comparison": pd.DataFrame(), "test_dates": [str(t) for t in test_dates]}

    pd_frame = pd.DataFrame(per_date)
    agg = pd_frame.groupby("Group").agg(
        TestSessions=("TestDate", "nunique"),
        AvgSymbols=("Symbols", "mean"),
        Frac2pctRange=("Frac2pctRange", "mean"),
        Frac2pctUpside=("Frac2pctUpside", "mean"),
        AvgRange=("AvgRange", "mean"),
        MedianRange=("MedianRange", "mean"),
        AvgVolume=("AvgVolume", "mean"),
        AvgTurnover=("AvgTurnover", "mean"),
    ).round(4).reset_index()

    # Q8 comparison: liquidity-first combined decile vs volatility-only decile.
    comp = agg[agg["Group"].isin(["TopDecile_Combined", "TopDecile_VolatilityOnly", "AllSymbols"])].copy()
    return {"status": "OK", "groups": agg, "comparison": comp,
            "per_date": pd_frame, "test_dates": [str(t) for t in test_dates]}


# --- B. scenario execution validation (honest scaffold) ----------------------

def scenario_execution_status(rubix_db_path="data/rubix_live_market.db",
                              min_sessions_required=20):
    """Report whether genuine chronological intraday data supports execution proof."""
    from pathlib import Path
    import sqlite3
    p = Path(rubix_db_path)
    if not p.is_file():
        return {"status": "DEFERRED_NO_INTRADAY_DB", "intraday_sessions": 0,
                "min_required": min_sessions_required,
                "reason": "Rubix intraday DB not present; execution validation deferred."}
    try:
        uri = f"file:{p.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            sessions = [r[0] for r in conn.execute(
                "SELECT DISTINCT substr(minute,1,10) d FROM candles_1m ORDER BY d").fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"status": "DEFERRED_READ_ERROR", "intraday_sessions": 0,
                "min_required": min_sessions_required, "reason": str(e)}
    n = len(sessions)
    if n < min_sessions_required:
        return {"status": "DEFERRED_INSUFFICIENT_INTRADAY", "intraday_sessions": n,
                "min_required": min_sessions_required, "sessions": sessions,
                "reason": (f"Only {n} chronological intraday session(s) available "
                           f"(<{min_sessions_required}). Multi-scenario execution proof requires "
                           "genuine event-ordered data across many sessions; deferred to paper "
                           "forward testing (Phase 14) rather than fabricated from daily bars.")}
    return {"status": "READY_FOR_EXECUTION_VALIDATION", "intraday_sessions": n,
            "min_required": min_sessions_required, "sessions": sessions,
            "reason": "Sufficient intraday sessions; per-scenario execution backtest can proceed."}
