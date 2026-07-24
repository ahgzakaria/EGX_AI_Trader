"""Generate the four INACTIVE approval tiers (Part 7).

    python -m scripts.build_eodhd_routing_tiers

Synthesizes the real-engine backtest, corp-action reconciliation, historical
revalidation and coverage evidence into data/eodhd/historical_symbol_routing_review.json
with four tiers, all approved:false. Generates a proposal only; activates nothing.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                         # noqa: E402

REP = PROJECT_ROOT / "reports" / "eodhd"
OUT = PROJECT_ROOT / "data" / "eodhd" / "historical_symbol_routing_review.json"


def _csv(name):
    try:
        return pd.read_csv(REP / name)
    except Exception:
        return pd.DataFrame()


def main():
    mapping = pd.read_csv(REP.parent / "eodhd_symbol_mapping.csv")
    results = _csv("full_universe_symbol_results.csv")
    queue = _csv("manual_queue_resolution.csv")
    ca = _csv("corporate_action_inventory.csv")
    reval = _csv("eodhd_correct_revalidation.csv")
    bt = _csv("real_backtest_summary.csv")

    cat = dict(zip(results.get("symbol", []), results.get("category", []))) if not results.empty else {}
    qcls = dict(zip(queue.get("symbol", []), queue.get("classification", []))) if not queue.empty else {}
    revcls = dict(zip(reval.get("symbol", []), reval.get("classification", []))) if not reval.empty else {}
    # symbols with a SPLIT in the last ~3 years (recent corp action → forward/backtest risk)
    recent_splits = set()
    if not ca.empty:
        s = ca[(ca["action_type"] == "SPLIT") & (ca["action_date"].astype(str) >= "2023-01-01")]
        recent_splits = set(s["symbol"])
    # tested-symbol 1y backtest verdicts
    bt1y = {}
    if not bt.empty:
        for _, r in bt[bt["window"] == "1y"].iterrows():
            bt1y[r["symbol"]] = str(r.get("classification"))

    entries = []
    for _, m in mapping.iterrows():
        sym = str(m["internal_symbol"]).upper()
        mapped = str(m["mapping_status"]) == "VERIFIED_EXACT"
        e = {"symbol": sym, "tier": "TIER_D_UNSUPPORTED_OR_MANUAL",
             "forward_primary": "yahoo", "historical_backtest_provider": "yahoo",
             "fallback_provider": None, "price_series": "PROJECT_CURRENT",
             "volume_policy": "PROJECT_CURRENT", "evidence_status": "", "risk_level": "n/a",
             "approved": False, "approval_reason": "", "last_reviewed": date.today().isoformat()}

        if not mapped:
            if sym.endswith("ETF"):
                e.update(evidence_status="non_equity", risk_level="excluded",
                         approval_reason="index ETF excluded from EODHD equity routing")
            else:
                e.update(evidence_status="coverage_gap",
                         approval_reason="NOT_SUPPORTED_BY_EODHD; keep Yahoo/Rubix")
            entries.append(e); continue

        q, c = str(qcls.get(sym, "")), str(cat.get(sym, ""))
        rv = str(revcls.get(sym, ""))
        if sym == "ORAS":
            e.update(tier="TIER_B_FORWARD_EODHD_NO_FALLBACK", forward_primary="eodhd",
                     historical_backtest_provider="eodhd_or_local", fallback_provider=None,
                     price_series="SPLIT_ADJUSTED", volume_policy="EVENT_SPECIFIC",
                     evidence_status="rubix_confirmed", risk_level="medium",
                     approval_reason="EODHD==Rubix; Yahoo proven bad — no Yahoo fallback ever")
        elif q == "SYMBOL_NOT_LIQUID" or c == "MANUAL_REVIEW":
            e.update(tier="TIER_D_UNSUPPORTED_OR_MANUAL", forward_primary=None,
                     evidence_status="insufficient", risk_level="hold",
                     approval_reason="illiquid/unresolved — manual review")
        elif c == "DATA_UNAVAILABLE":
            e.update(tier="TIER_B_FORWARD_EODHD_NO_FALLBACK", forward_primary="eodhd",
                     historical_backtest_provider="eodhd", fallback_provider=None,
                     price_series="SPLIT_ADJUSTED", volume_policy="EVENT_SPECIFIC",
                     evidence_status="eodhd_only", risk_level="medium",
                     approval_reason="Yahoo has no usable data; EODHD only — DATA_UNAVAILABLE on failure")
        elif (sym in recent_splits or rv == "ADJUSTMENT_DIFFERENCE"
              or bt1y.get(sym) in ("MATERIAL_SIGNAL_CHANGE", "MATERIAL_BACKTEST_CHANGE",
                                   "CORPORATE_ACTION_DIFFERENCE")):
            e.update(tier="TIER_C_HISTORICAL_REVIEW", forward_primary="eodhd_shadow",
                     historical_backtest_provider="yahoo", fallback_provider="yahoo",
                     price_series="SPLIT_ADJUSTED", volume_policy="EVENT_SPECIFIC",
                     evidence_status="corp_action_or_deep_history_diff", risk_level="high",
                     approval_reason="corp-action/deep-history/bar-coverage differences — "
                     "forward-only maybe; must NOT replace frozen backtest history")
        else:
            e.update(tier="TIER_A_FORWARD_SAFE", forward_primary="eodhd",
                     historical_backtest_provider="yahoo", fallback_provider="yahoo",
                     price_series="SPLIT_ADJUSTED", volume_policy="EVENT_SPECIFIC",
                     evidence_status=("backtest_1y_" + bt1y[sym].lower()) if sym in bt1y
                                     else "recent_validated_not_backtested",
                     risk_level="low",
                     approval_reason="recent prices validated, no scale issue, no recent split; "
                     "forward-safe candidate (backtests still stay on Yahoo)")
        entries.append(e)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "schema_version": 1, "active": False,
        "description": "PROPOSED four-tier EODHD routing review — INACTIVE. Every entry "
                       "approved:false. Historical/frozen backtests remain on their current "
                       "provider in every tier. Nothing is switched.",
        "generated_at": date.today().isoformat(), "symbols": entries}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    from collections import Counter
    print(json.dumps({"symbols": len(entries), "tiers": dict(Counter(e["tier"] for e in entries)),
                      "approved_any": any(e["approved"] for e in entries)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
