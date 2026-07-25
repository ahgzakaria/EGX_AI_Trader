"""Generate the proposed, INACTIVE per-symbol routing policy (Part 7).

    python -m scripts.build_eodhd_routing_policy

Synthesizes all Phase-2/3 audit evidence into data/eodhd/historical_symbol_routing.json
with every entry ``approved: false``. Generates a proposal only — it activates nothing
and switches no provider.
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
OUT = PROJECT_ROOT / "data" / "eodhd" / "historical_symbol_routing.json"


def _csv(name):
    try:
        return pd.read_csv(REP / name)
    except Exception:
        return pd.DataFrame()


def main():
    mapping = pd.read_csv(REP.parent / "eodhd_symbol_mapping.csv")
    results = _csv("full_universe_symbol_results.csv")
    queue = _csv("manual_queue_resolution.csv")
    cat = dict(zip(results.get("symbol", []), results.get("category", []))) if not results.empty else {}
    qcls = dict(zip(queue.get("symbol", []), queue.get("classification", []))) if not queue.empty else {}

    entries = []
    for _, m in mapping.iterrows():
        sym = str(m["internal_symbol"]).upper()
        status = str(m["mapping_status"])
        e = {"symbol": sym, "primary_provider": "yahoo", "fallback_provider": None,
             "price_series": "PROJECT_CURRENT", "policy": "YAHOO_ONLY", "yahoo_forbidden": False,
             "reason": "", "evidence_status": "", "approved": False,
             "last_reviewed": date.today().isoformat(), "notes": ""}

        if status != "VERIFIED_EXACT":
            if sym.endswith("ETF"):
                e.update(policy="EXCLUDED_NON_EQUITY", primary_provider=None,
                         reason="index ETF; EODHD EGX list is common-stock only")
            else:
                e.update(policy="YAHOO_ONLY", reason="NOT_SUPPORTED_BY_EODHD (active in Rubix); "
                         "keep current provider", evidence_status="coverage_gap")
            entries.append(e); continue

        q = str(qcls.get(sym, ""))
        c = str(cat.get(sym, ""))
        if sym == "ORAS":
            e.update(policy="EODHD_PRIMARY", primary_provider="eodhd", fallback_provider=None,
                     price_series="SPLIT_ADJUSTED", yahoo_forbidden=True,
                     reason="EODHD matches live Rubix; Yahoo .CA proven stale/mis-scaled — never Yahoo",
                     evidence_status="rubix_confirmed")
        elif q == "YAHOO_CORRECT":
            e.update(policy="YAHOO_ONLY", reason="EODHD off vs Rubix on this penny stock; keep Yahoo",
                     evidence_status="rubix_confirmed_yahoo")
        elif q == "SYMBOL_NOT_LIQUID":
            e.update(policy="MANUAL_REVIEW", primary_provider=None,
                     reason="no valid Rubix price to adjudicate (illiquid) — hold",
                     evidence_status="insufficient")
        elif c == "DATA_UNAVAILABLE":
            e.update(policy="EODHD_ONLY", primary_provider="eodhd", price_series="SPLIT_ADJUSTED",
                     reason="Yahoo has no usable data; EODHD is the only source",
                     evidence_status="eodhd_only")
        elif q == "EODHD_CORRECT" or c in ("CLEAN_MATCH", "MINOR_ROUNDING_DIFFERENCE"):
            e.update(policy="EODHD_PRIMARY", primary_provider="eodhd", fallback_provider="yahoo",
                     price_series="SPLIT_ADJUSTED",
                     reason="EODHD split-adjusted matches Yahoo/Rubix recently; propose primary + Yahoo fallback",
                     evidence_status="validated",
                     notes="deep-history/backtests spanning splits may differ — forward use only")
        else:
            e.update(policy="MANUAL_REVIEW", primary_provider=None,
                     reason=f"category {c or 'unknown'} — needs review", evidence_status="pending")
        entries.append(e)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "schema_version": 1, "active": False,
        "description": "PROPOSED per-symbol historical routing — INACTIVE. No entry is "
                       "operational; the active provider remains Yahoo. Approve per symbol "
                       "and wire in a future phase.",
        "generated_at": date.today().isoformat(), "symbols": entries},
        indent=2, ensure_ascii=False), encoding="utf-8")

    from collections import Counter
    print(json.dumps({"symbols": len(entries),
                      "policies": dict(Counter(e["policy"] for e in entries)),
                      "approved_any": any(e["approved"] for e in entries)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
