"""Reconcile the 40 unmapped + 14 extra EGX symbols with evidence (Parts 1-2).

    python -m scripts.audit_eodhd_reconciliation

Uses the EODHD search API (cached), the EODHD EGX list (ISIN), and the live Rubix
ticker set. Never maps by suffix replacement, never auto-adds an extra symbol.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.environment import load_project_environment       # noqa: E402
from providers.eodhd_client import EODHDClient, EODHDError  # noqa: E402
from providers.eodhd_reconciliation import classify_extra, classify_unmapped  # noqa: E402
from core.universe import active_symbols

OUT = PROJECT_ROOT / "reports" / "eodhd"


def _internal_bases():
    """The authoritative active universe (canonical, suffix-free)."""

    return list(active_symbols())


def _rubix_bases():
    try:
        c = sqlite3.connect(f"file:{PROJECT_ROOT / 'data' / 'rubix_live_market.db'}?mode=ro", uri=True)
        return {str(t).upper().split(".")[0] for (t,) in c.execute("SELECT DISTINCT ticker FROM quotes")}
    except Exception:
        return set()


def main():
    load_project_environment()
    OUT.mkdir(parents=True, exist_ok=True)
    client = EODHDClient(max_live_calls=80)
    internal = _internal_bases()
    internal_set = set(internal)
    rubix = _rubix_bases()
    eodhd_rows = client.exchange_symbols("EGX")
    eodhd_by_code = {str(r["Code"]).upper(): r for r in eodhd_rows if r.get("Code")}
    egx_codes = set(eodhd_by_code)

    unmapped = [b for b in internal if b not in egx_codes]
    extra = sorted(egx_codes - internal_set)

    # --- Part 1: unmapped reconciliation ---
    unmapped_rows = []
    for tk in unmapped:
        try:
            search = client.search(tk)
        except EODHDError:
            search = []
        egx_cands = [r for r in (search or []) if str(r.get("Exchange")) == "EGX"]
        c = classify_unmapped(tk, in_rubix=tk in rubix, egx_search_candidates=egx_cands)
        unmapped_rows.append({
            "internal_symbol": tk, "internal_company_name": "",
            "current_internal_suffix": ".CA", "candidate_eodhd_symbol": c["candidate_eodhd_symbol"],
            "candidate_company_name": c["candidate_company_name"],
            "classification": c["classification"], "confidence": c["confidence"],
            "evidence": c["evidence"], "action_required": c["action_required"],
            "notes": f"in_rubix={tk in rubix}; egx_search_hits={len(egx_cands)}"})
    _write(OUT / "unmapped_symbol_reconciliation.csv",
           ["internal_symbol", "internal_company_name", "current_internal_suffix",
            "candidate_eodhd_symbol", "candidate_company_name", "classification",
            "confidence", "evidence", "action_required", "notes"], unmapped_rows)

    # --- Part 2: extra EODHD review ---
    extra_rows = []
    for code in extra:
        row = eodhd_by_code.get(code, {})
        c = classify_extra(code, row, in_rubix=code in rubix, internal_codes=internal_set)
        extra_rows.append({
            "eodhd_symbol": f"{code}.EGX", "eodhd_code": code,
            "eodhd_company_name": row.get("Name", ""), "isin": row.get("Isin", ""),
            "type": row.get("Type", ""), "currency": row.get("Currency", ""),
            "in_rubix": code in rubix, "classification": c["classification"],
            "confidence": c["confidence"], "evidence": c["evidence"],
            "action_required": c["action_required"]})
    _write(OUT / "extra_eodhd_symbols_review.csv",
           ["eodhd_symbol", "eodhd_code", "eodhd_company_name", "isin", "type", "currency",
            "in_rubix", "classification", "confidence", "evidence", "action_required"], extra_rows)

    from collections import Counter
    print(json.dumps({
        "unmapped": len(unmapped_rows),
        "unmapped_classes": dict(Counter(r["classification"] for r in unmapped_rows)),
        "extra": len(extra_rows),
        "extra_classes": dict(Counter(r["classification"] for r in extra_rows)),
        "live_calls": client.stats.live_calls, "cache_hits": client.stats.cache_hits,
    }, ensure_ascii=False))
    return 0


def _write(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
