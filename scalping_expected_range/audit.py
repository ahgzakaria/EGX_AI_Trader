"""Phase 4/5/6 — tradability, score-sensitivity and worked-example audits.

Operates on a completed scan universe frame (already carrying liq_*, vol_*, live_*
and score columns). Read-only analysis: it never changes weights, thresholds, the
fixed target/stop, or any strategy. Volume stays the largest single scoring
component; this only exposes whether raw share count is masquerading as
tradability.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scalping_expected_range.liquidity_model import (
    LIQUIDITY_LIMITED,
    LIQUIDITY_VALID,
)


def _pctl(series):
    s = pd.to_numeric(series, errors="coerce")
    return (s.rank(pct=True) * 100).round(1)


# --- Phase 4: volume vs tradability -----------------------------------------

def build_tradability_audit(universe: pd.DataFrame) -> pd.DataFrame:
    df = universe.copy()
    out = pd.DataFrame({
        "Symbol": df["Symbol"],
        "TradableCandidate": df.get("TradableCandidate"),
        "LiquidityStatus": df.get("liq_status"),
        "AvgVolume20": df.get("liq_avg_volume_20"),
        "MedianVolume20": df.get("liq_median_volume_20"),
        "AvgTurnoverEGP20": df.get("liq_avg_turnover_egp_20"),
        "MedianTurnoverEGP20": df.get("liq_median_turnover_egp_20"),
        "VolumeConsistency": df.get("liq_volume_consistency"),
        "TurnoverConsistency": df.get("liq_turnover_consistency"),
        "AvgTradedPrice": df.get("liq_avg_traded_price"),
        "Spread%": df.get("live_spread_percent"),
        "LowVolumeSessions": df.get("liq_low_volume_sessions"),
        "VolOneSessionDominance": df.get("liq_one_session_dominance"),
        "TurnoverOneSessionDominance": df.get("liq_turnover_one_session_dominance"),
    })
    # Five separate ranks.
    out["RawVolumeRank"] = _pctl(out["AvgVolume20"]).rank(ascending=False, method="min")
    out["TurnoverRank"] = _pctl(out["AvgTurnoverEGP20"]).rank(ascending=False, method="min")
    out["VolumeConsistencyRank"] = _pctl(out["VolumeConsistency"]).rank(ascending=False, method="min")

    # Tradability score: turnover + consistency + spread (inverted) + price
    # adequacy - single-session-dominance penalty. Captures "can you actually
    # trade it", independent of raw share count.
    turn_p = _pctl(out["AvgTurnoverEGP20"]).fillna(0)
    medturn_p = _pctl(out["MedianTurnoverEGP20"]).fillna(0)
    cons = (pd.to_numeric(out["TurnoverConsistency"], errors="coerce").fillna(0) * 100)
    spread = pd.to_numeric(out["Spread%"], errors="coerce")
    spread_score = (100 - _pctl(spread)).fillna(50)   # neutral when no live spread
    price = pd.to_numeric(out["AvgTradedPrice"], errors="coerce").fillna(0)
    price_adequacy = np.clip(price / 1.0, 0, 1) * 100   # <1 EGP penny shares penalised
    dominance = pd.to_numeric(out["TurnoverOneSessionDominance"], errors="coerce").fillna(0)
    dominance_penalty = np.clip((dominance - 0.4) / 0.6, 0, 1) * 30
    out["TradabilityScore"] = (
        0.40 * turn_p + 0.20 * medturn_p + 0.15 * cons + 0.15 * spread_score
        + 0.10 * price_adequacy - dominance_penalty).round(1)
    out["TradabilityRank"] = out["TradabilityScore"].rank(ascending=False, method="min")

    out["CombinedScalpingScore"] = df.get("EXPECTED_RANGE_SCALPING_SCORE")
    out["CombinedScalpingRank"] = pd.to_numeric(
        out["CombinedScalpingScore"], errors="coerce").rank(ascending=False, method="min")

    # Flags: is this a penny share whose raw volume is inflated relative to turnover?
    out["PennyShareFlag"] = (price < 1.0) & (pd.to_numeric(out["AvgVolume20"], errors="coerce") > out["AvgVolume20"].median())
    out["SingleSessionInflatedFlag"] = dominance > 0.4
    return out.sort_values("RawVolumeRank")


# --- Phase 5: score sensitivity (no weights changed) ------------------------

def build_score_sensitivity(universe: pd.DataFrame, cfg) -> pd.DataFrame:
    df = universe.copy()
    tradable = df[df.get("TradableCandidate") == True].copy()
    score = pd.to_numeric(tradable.get("EXPECTED_RANGE_SCALPING_SCORE"), errors="coerce")
    top10_by_score = set(tradable.nlargest(10, "EXPECTED_RANGE_SCALPING_SCORE")["Symbol"])
    top10_by_volume = set(tradable.nlargest(10, "liq_avg_volume_20")["Symbol"])

    # Weak-turnover intruder into Top 10 by score.
    med_turn = pd.to_numeric(tradable.get("liq_avg_turnover_egp_20"), errors="coerce").median()
    top10_score_rows = tradable.nlargest(10, "EXPECTED_RANGE_SCALPING_SCORE")
    weak_turn_in_top10 = int((pd.to_numeric(
        top10_score_rows.get("liq_avg_turnover_egp_20"), errors="coerce") < med_turn).sum())

    # Volatility cannot rescue failing liquidity: any non-tradable with score>0?
    non_tradable = df[df.get("TradableCandidate") == False]
    rescued = int((pd.to_numeric(
        non_tradable.get("EXPECTED_RANGE_SCALPING_SCORE"), errors="coerce") > 0).sum())

    # Score concentration (top-10 share of total score) + tie count.
    total = score.fillna(0).sum()
    top10_share = round(float(score.nlargest(10).sum() / total * 100), 2) if total else None
    ties = int(score.round(1).duplicated().sum())

    # Sensitivity to one exceptional session: Top-20 by score whose ranking rests
    # on a single dominant session (>40% of window volume).
    top20 = tradable.nlargest(20, "EXPECTED_RANGE_SCALPING_SCORE")
    fragile_top20 = int((pd.to_numeric(
        top20.get("liq_one_session_dominance"), errors="coerce") > 0.4).sum())

    # Perturbation: sharply raise raw Volume (5x) for the median-volume tradable
    # and see whether it jumps into the Top 10 by score.
    changed = _volume_perturbation_effect(tradable, cfg)

    rows = [
        ("Top10_by_score_vs_by_volume_overlap", len(top10_by_score & top10_by_volume),
         "symbols shared between score-Top10 and raw-volume-Top10"),
        ("Weak_turnover_in_Top10_by_score", weak_turn_in_top10,
         "Top-10 (by score) with below-median average turnover"),
        ("Nontradable_with_positive_score", rescued,
         "liquidity-gated symbols that volatility rescued (must be 0)"),
        ("Top10_score_concentration_pct", top10_share,
         "share of total tradable score held by the top 10"),
        ("Percentile_tie_count", ties, "duplicated rounded scores (ranking ties)"),
        ("Fragile_Top20_single_session_dominant", fragile_top20,
         "Top-20 whose window volume is >40% one session"),
        ("Median_symbol_enters_Top10_after_5x_volume", int(changed),
         "does a 5x raw-volume spike push the median symbol into Top-10 by score"),
    ]
    return pd.DataFrame(rows, columns=["Metric", "Value", "Description"])


def _volume_perturbation_effect(tradable, cfg):
    from scalping_expected_range.scoring import add_scores
    if tradable.empty:
        return False
    vol = pd.to_numeric(tradable["liq_avg_volume_20"], errors="coerce")
    med_symbol = vol.sub(vol.median()).abs().idxmin()
    perturbed = tradable.copy()
    perturbed.loc[med_symbol, "liq_avg_volume_20"] = vol.loc[med_symbol] * 5.0
    rescored = add_scores(perturbed, cfg)
    top10 = set(rescored.nlargest(10, "EXPECTED_RANGE_SCALPING_SCORE")["Symbol"])
    return perturbed.loc[med_symbol, "Symbol"] in top10


# --- Phase 6: worked examples ------------------------------------------------

def build_worked_examples(universe: pd.DataFrame) -> pd.DataFrame:
    df = universe.copy()
    picks = {}

    def _first(frame, label):
        if not frame.empty:
            picks[frame.iloc[0]["Symbol"]] = label

    tradable = df[df.get("TradableCandidate") == True]
    _first(df.sort_values("liq_avg_volume_20", ascending=False), "highest_raw_avg_volume")
    _first(df.sort_values("liq_avg_turnover_egp_20", ascending=False), "highest_avg_turnover")
    _first(tradable.sort_values("EXPECTED_RANGE_SCALPING_SCORE", ascending=False),
           "highest_combined_score")
    # high volatility but rejected for low liquidity
    rej = df[(df.get("TradableCandidate") == False)].copy()
    rej_hv = rej.sort_values("vol_adr_percent_20", ascending=False)
    _first(rej_hv, "high_volatility_rejected_low_liquidity")
    # liquid but insufficient volatility
    liquid_lowvol = tradable[pd.to_numeric(tradable.get("vol_adr_percent_20"),
                                           errors="coerce") < 1.5]
    _first(liquid_lowvol.sort_values("liq_avg_volume_20", ascending=False),
           "liquid_but_low_volatility")

    cols = [
        "Rank", "Symbol", "PrevClose", "liq_avg_volume_20", "liq_median_volume_20",
        "liq_avg_turnover_egp_20", "liq_avg_traded_price", "liq_volume_consistency",
        "liq_one_session_dominance", "liq_status", "liq_reasons",
        "vol_adr_percent_20", "vol_target_2pct_frequency", "vol_classification",
        "AvgVolumeScore", "AvgTurnoverScore", "VolatilityScore", "TargetFrequencyScore",
        "LiquidityConsistencyScore", "SpreadScore", "LiquidityGate",
        "EXPECTED_RANGE_SCALPING_SCORE", "TradableCandidate",
        "er_base_expected_low", "er_base_expected_high",
        "er_high_volatility_expected_low", "er_high_volatility_expected_high",
        "prov_data_status", "prov_freshness_status",
    ]
    rows = []
    for symbol, label in picks.items():
        r = df[df["Symbol"] == symbol]
        if r.empty:
            continue
        rec = {"ExampleRole": label}
        for c in cols:
            if c in r.columns:
                rec[c] = r.iloc[0][c]
        rec["AcceptOrReject"] = ("ACCEPTED_TRADABLE" if r.iloc[0].get("TradableCandidate")
                                 else f"REJECTED::{r.iloc[0].get('liq_status')}")
        rows.append(rec)
    return pd.DataFrame(rows)
