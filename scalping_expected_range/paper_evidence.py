"""Per-scenario paper evidence + judgment-readiness for EXPECTED_RANGE_SCALPER.

Aggregates the immutable paper signals + outcomes into per-scenario statistics and
reports whether the minimum evidence to even consider a judgment has accumulated.
Never recommends production; it only reports whether enough unbiased forward
evidence exists to START evaluating each scenario independently.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd

SIGNALS_CSV = "reports/expected_range_paper_signals.csv"
OUTCOMES_CSV = "reports/expected_range_paper_outcomes.csv"
SUMMARY_CSV = "reports/expected_range_daily_paper_summary.csv"


def _read(path):
    p = Path(path)
    if not p.is_file():
        return pd.DataFrame()
    return pd.read_csv(p)


def _max_consecutive_losses(first_hits):
    run = best = 0
    for h in first_hits:
        if h == "STOP":
            run += 1
            best = max(best, run)
        elif h == "TARGET":
            run = 0
    return best


def scenario_evidence(signals_csv=SIGNALS_CSV, outcomes_csv=OUTCOMES_CSV, cfg=None):
    signals = _read(signals_csv)
    outcomes = _read(outcomes_csv)
    if signals.empty:
        # No signals yet, but session classifications (pilot/partial/complete) still
        # count so the evidence gate reflects reality.
        return {"per_scenario": pd.DataFrame(),
                "overall": _overall(signals, pd.DataFrame(), cfg)}

    merged = signals.merge(
        outcomes, on=["SignalUUID", "SessionDate", "Symbol", "Scenario", "ActivationCycle"],
        how="left", suffixes=("", "_oc"))

    rows = []
    for scenario, g in merged.groupby("Scenario"):
        oc = g[g["FirstHit"].notna()] if "FirstHit" in g else g.iloc[0:0]
        n = len(g)
        executable = int((g.get("ExecutableEntryAvailable") == "Yes").sum()) if "ExecutableEntryAvailable" in g else 0
        first = oc["FirstHit"].tolist() if "FirstHit" in oc else []
        target_first = sum(1 for h in first if h == "TARGET")
        stop_first = sum(1 for h in first if h == "STOP")
        neither = sum(1 for h in first if h == "NEITHER")
        resolved = target_first + stop_first
        # Expectancy after costs, using net target/risk from the signal.
        net_t = pd.to_numeric(g.get("NetTarget%"), errors="coerce").median()
        net_r = pd.to_numeric(g.get("NetRisk%"), errors="coerce").median()
        wins = target_first
        losses = stop_first
        gross_win = wins * (net_t if pd.notna(net_t) else 0)
        gross_loss = losses * (net_r if pd.notna(net_r) else 0)
        pf = (gross_win / gross_loss) if gross_loss > 0 else (np.inf if gross_win > 0 else None)
        expectancy = ((gross_win - gross_loss) / resolved) if resolved else None
        rows.append({
            "Scenario": scenario, "SignalCount": n, "ExecutableCount": executable,
            "TargetFirst": target_first, "StopFirst": stop_first, "NeitherHit": neither,
            "TargetFirstRate": round(target_first / resolved, 4) if resolved else None,
            "StopFirstRate": round(stop_first / resolved, 4) if resolved else None,
            "NeitherHitRate": round(neither / len(first), 4) if first else None,
            "AvgMFE%": round(pd.to_numeric(oc.get("MFE%"), errors="coerce").mean(), 4) if len(oc) else None,
            "AvgMAE%": round(pd.to_numeric(oc.get("MAE%"), errors="coerce").mean(), 4) if len(oc) else None,
            "MedianTimeToTargetSec": round(pd.to_numeric(
                oc.get("TimeToTargetSec"), errors="coerce").median(), 1) if len(oc) else None,
            "MedianTimeToStopSec": round(pd.to_numeric(
                oc.get("TimeToStopSec"), errors="coerce").median(), 1) if len(oc) else None,
            "ExpectancyAfterCosts%": round(expectancy, 4) if expectancy is not None else None,
            "ProfitFactor": round(pf, 3) if (pf is not None and np.isfinite(pf)) else pf,
            "MaxConsecutiveLosses": _max_consecutive_losses(first),
            # Max drawdown of the cumulative net-P&L path (resolved outcomes only).
            "MaxDrawdown%": _max_drawdown(oc, net_t, net_r),
            "JudgeReady": bool(n >= (cfg.minimum_signals_per_scenario_before_judgment if cfg else 20)),
        })
    per_scenario = pd.DataFrame(rows).sort_values("SignalCount", ascending=False)
    return {"per_scenario": per_scenario, "overall": _overall(signals, merged, cfg)}


def _max_drawdown(oc, net_t, net_r):
    if oc.empty or "FirstHit" not in oc:
        return None
    pnl = []
    for h in oc["FirstHit"]:
        if h == "TARGET":
            pnl.append(net_t if pd.notna(net_t) else 0)
        elif h == "STOP":
            pnl.append(-(net_r if pd.notna(net_r) else 0))
    if not pnl:
        return None
    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak)
    return round(float(dd.min()), 4)


def _session_classifications(path=None):
    df = _read(path or SUMMARY_CSV)          # read the current module path at call time
    if df.empty or "Classification" not in df:
        return {}
    return dict(zip(df["SessionDate"].astype(str), df["Classification"].astype(str)))


def _overall(signals, merged, cfg):
    classes = _session_classifications()
    from scalping_expected_range.orchestration import (
        COMPLETE_FORWARD_SESSION, PARTIAL_LATE_START, PARTIAL_EARLY_STOP, PILOT_SESSION)
    complete_dates = {d for d, c in classes.items() if c == COMPLETE_FORWARD_SESSION}
    pilot = sum(1 for c in classes.values() if c == PILOT_SESSION)
    partial = sum(1 for c in classes.values() if c in (PARTIAL_LATE_START, PARTIAL_EARLY_STOP))

    # Signals/executable counted ONLY from complete forward sessions.
    if complete_dates and "SessionDate" in merged:
        counted = merged[merged["SessionDate"].astype(str).isin(complete_dates)]
    else:
        counted = merged.iloc[0:0]
    executable = int((counted.get("ExecutableEntryAvailable") == "Yes").sum()) \
        if "ExecutableEntryAvailable" in counted else 0
    matured_exec = int(((counted.get("ExecutableEntryAvailable") == "Yes") &
                        (counted.get("OutcomeMaturity") == "FINALIZED")).sum()) \
        if "OutcomeMaturity" in counted else 0
    pending = int((merged.get("OutcomeMaturity").isna()).sum()) if "OutcomeMaturity" in merged else 0
    invalid = int((merged.get("OutcomeMaturity") == "INVALID").sum()) if "OutcomeMaturity" in merged else 0

    min_sessions = cfg.minimum_paper_sessions_before_judgment if cfg else 20
    min_exec = cfg.minimum_executable_signals_before_judgment if cfg else 30
    complete = len(complete_dates)
    return {
        # Only COMPLETE_FORWARD_SESSIONs count toward the minimum.
        "complete_forward_sessions": complete,
        "pilot_sessions": pilot,
        "partial_sessions": partial,
        "total_signals_all_sessions": int(len(signals)),
        "counted_signals": int(len(counted)),
        "matured_executable_signals": matured_exec,
        "executable_signals": executable,
        "pending_outcomes": pending,
        "invalid_outcomes": invalid,
        "min_sessions_required": min_sessions,
        "min_executable_required": min_exec,
        "evidence_status": (
            "SUFFICIENT_TO_BEGIN_JUDGMENT"
            if (complete >= min_sessions and executable >= min_exec)
            else "COLLECTING_EVIDENCE"),
        "production_recommended": False,      # never recommended by this module
    }


def _empty_overall(cfg):
    return {"complete_forward_sessions": 0, "pilot_sessions": 0, "partial_sessions": 0,
            "total_signals_all_sessions": 0, "counted_signals": 0,
            "matured_executable_signals": 0, "executable_signals": 0,
            "pending_outcomes": 0, "invalid_outcomes": 0,
            "min_sessions_required": cfg.minimum_paper_sessions_before_judgment if cfg else 20,
            "min_executable_required": cfg.minimum_executable_signals_before_judgment if cfg else 30,
            "evidence_status": "COLLECTING_EVIDENCE", "production_recommended": False}
