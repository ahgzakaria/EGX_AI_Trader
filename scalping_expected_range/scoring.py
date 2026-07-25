"""Phase 5 — liquidity-first stock-selection score for EXPECTED_RANGE_SCALPER.

Builds the EXPECTED_RANGE_SCALPING_SCORE (0-100) from cross-sectional EGX
percentiles, with the user's priority weighting:

    Average Volume        30
    Average Turnover      25
    Historical Volatility 20
    2% Target Frequency   15
    Liquidity Consistency  5
    Live Spread/Exec       5

Combined liquidity weight is 60. Volatility must never fully compensate for poor
liquidity: a symbol failing the liquidity hard gate is excluded from the
best-candidate views and its score is gated down, never lifted by volatility.

Every component score is exposed separately. Weights are research defaults and
are NOT optimized on the dataset.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scalping_expected_range.liquidity_model import (
    LIQUIDITY_LIMITED,
    LIQUIDITY_VALID,
)

# Component -> source column produced by the scanner's flat records.
_COMPONENTS = {
    "AvgVolumeScore": "liq_avg_volume_20",
    "AvgTurnoverScore": "liq_avg_turnover_egp_20",
    "VolatilityScore": "vol_adr_percent_20",
    "TargetFrequencyScore": "vol_target_2pct_frequency",
    "LiquidityConsistencyScore": "liq_volume_consistency",
    "SpreadScore": "live_spread_percent",   # lower is better (inverted below)
}


def _col(frame: pd.DataFrame, name: str) -> pd.Series:
    """Return a numeric-friendly Series for ``name`` (NaN column if missing)."""
    if name in frame.columns:
        return frame[name]
    return pd.Series(np.nan, index=frame.index)


def _percentile(series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if not isinstance(numeric, pd.Series):
        numeric = pd.Series(numeric)
    if numeric.notna().sum() == 0:
        return pd.Series([np.nan] * len(numeric), index=numeric.index)
    return (numeric.rank(pct=True) * 100.0).round(1)


def add_scores(frame: pd.DataFrame, cfg) -> pd.DataFrame:
    """Add component scores, the total score and a liquidity gate. Pure/no I/O."""

    frame = frame.copy()

    frame["AvgVolumeScore"] = _percentile(_col(frame, "liq_avg_volume_20"))
    frame["AvgTurnoverScore"] = _percentile(_col(frame, "liq_avg_turnover_egp_20"))
    frame["VolatilityScore"] = _percentile(_col(frame, "vol_adr_percent_20"))
    frame["TargetFrequencyScore"] = _percentile(_col(frame, "vol_target_2pct_frequency"))
    frame["LiquidityConsistencyScore"] = (
        pd.to_numeric(_col(frame, "liq_volume_consistency"), errors="coerce") * 100.0).round(1)
    # Live spread: lower is better; invert the percentile. Neutral (50) when the
    # live quote is not yet available so pre-session ranking is unaffected.
    if "live_spread_percent" in frame.columns and frame["live_spread_percent"].notna().any():
        frame["SpreadScore"] = (100.0 - _percentile(frame["live_spread_percent"])).round(1)
    else:
        frame["SpreadScore"] = np.nan

    weights = {
        "AvgVolumeScore": cfg.average_volume_weight,
        "AvgTurnoverScore": cfg.average_turnover_weight,
        "VolatilityScore": cfg.volatility_weight,
        "TargetFrequencyScore": cfg.target_frequency_weight,
        "LiquidityConsistencyScore": cfg.liquidity_consistency_weight,
        "SpreadScore": cfg.spread_weight,
    }

    # Weighted average over the components that are available for each row, so a
    # missing live-spread pre-session neither inflates nor deflates the score.
    total = pd.Series(0.0, index=frame.index)
    weight_used = pd.Series(0.0, index=frame.index)
    for comp, w in weights.items():
        col = pd.to_numeric(frame[comp], errors="coerce")
        present = col.notna()
        total = total.add((col.fillna(0) * w).where(present, 0.0), fill_value=0.0)
        weight_used = weight_used.add(pd.Series(np.where(present, w, 0.0), index=frame.index),
                                      fill_value=0.0)
    raw_score = (total / weight_used.replace(0, np.nan)).round(1)

    # Liquidity gate in [0,1]: symbols that fail the liquidity hard gate cannot
    # be rescued by volatility. VALID = 1.0, LIMITED = 0.7, everything else 0.0.
    status = frame.get("liq_status")
    gate = status.map(lambda s: 1.0 if s == LIQUIDITY_VALID
                      else (0.7 if s == LIQUIDITY_LIMITED else 0.0)) if status is not None \
        else pd.Series(1.0, index=frame.index)
    frame["LiquidityGate"] = gate
    frame["RawScore"] = raw_score.fillna(0.0)
    frame["EXPECTED_RANGE_SCALPING_SCORE"] = (raw_score.fillna(0.0) * gate).round(1)
    frame["TradableCandidate"] = gate > 0.0
    return frame


def default_rank(frame: pd.DataFrame) -> pd.DataFrame:
    """Liquidity-first default ranking of tradable candidates.

    The final tradable ranking uses the liquidity-first Combined Scalping Score
    (Average Volume is still its largest single component at 30/100, but Turnover,
    consistency and volatility temper it). This deliberately prevents an extremely
    low-priced penny share from topping the tradable list on raw share count
    alone — a distortion the raw-Average-Volume view would show. Average Turnover
    then Average Volume break ties. The dedicated Highest-Average-Volume view is
    preserved separately for the user who wants the pure raw-volume list.
    """

    frame = frame.copy()
    frame["_score"] = pd.to_numeric(
        frame.get("EXPECTED_RANGE_SCALPING_SCORE"), errors="coerce").fillna(0)
    frame["_turn"] = pd.to_numeric(frame.get("liq_avg_turnover_egp_20"), errors="coerce").fillna(0)
    frame["_avgvol"] = pd.to_numeric(frame.get("liq_avg_volume_20"), errors="coerce").fillna(0)
    frame["_2pct"] = pd.to_numeric(frame.get("vol_target_2pct_frequency"), errors="coerce").fillna(0)
    ranked = frame.sort_values(
        by=["TradableCandidate", "_score", "_turn", "_avgvol", "_2pct"],
        ascending=[False, False, False, False, False],
    ).drop(columns=["_score", "_turn", "_avgvol", "_2pct"])
    ranked.insert(0, "Rank", range(1, len(ranked) + 1))
    return ranked
