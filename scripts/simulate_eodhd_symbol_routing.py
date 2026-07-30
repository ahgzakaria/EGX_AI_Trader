"""Dry-run the proposed per-symbol routing (Parts 8-9). Activates NOTHING.

    python -m scripts.simulate_eodhd_symbol_routing

Shows, for the scan universe, which provider + price series each symbol WOULD use, the
fallback behavior on EODHD failure (incl. ORAS never falling back to bad Yahoo), and
estimated API/cache usage. It never alters provider settings and never feeds simulated
data into live strategy decisions.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from providers.eodhd_routing import load_policy, resolve_route  # noqa: E402
from core.universe import ARCHIVED_LEGACY_SOURCE

OUT = PROJECT_ROOT / "reports" / "eodhd"


def _universe():
    out = []
    for line in (PROJECT_ROOT / ARCHIVED_LEGACY_SOURCE).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and s.lower() != "ticker":
            out.append(s.upper().split(".")[0])
    return out


def main():
    policy = load_policy()
    if not policy:
        print(json.dumps({"error": "routing policy not found — run build_eodhd_routing_policy first"}))
        return 1
    symbols = _universe()

    rows = []
    for sym in symbols:
        normal = resolve_route(sym, policy, eodhd_ok=True, yahoo_ok=True)
        on_eodhd_fail = resolve_route(sym, policy, eodhd_ok=False, yahoo_ok=True)
        rows.append({
            "symbol": sym, "policy": normal["policy"],
            "selected_provider": normal["provider"], "price_series": normal["series"],
            "on_eodhd_failure_provider": on_eodhd_fail["provider"],
            "on_eodhd_failure_result": on_eodhd_fail["policy"],
            "fallback_used_on_failure": on_eodhd_fail["fallback_used"],
            "reason": normal["reason"]})

    _w(OUT / "routing_dry_run.csv",
       ["symbol", "policy", "selected_provider", "price_series",
        "on_eodhd_failure_provider", "on_eodhd_failure_result", "fallback_used_on_failure",
        "reason"], rows)

    eodhd = [r for r in rows if r["selected_provider"] == "eodhd"]
    yahoo = [r for r in rows if r["selected_provider"] == "yahoo"]
    unresolved = [r for r in rows if r["policy"] in ("MANUAL_REVIEW",)]
    unavailable = [r for r in rows if r["selected_provider"] is None
                   and r["policy"] not in ("MANUAL_REVIEW", "EXCLUDED_NON_EQUITY")]
    excluded = [r for r in rows if r["policy"] == "EXCLUDED_NON_EQUITY"]
    # fallback safety: any symbol that would fall back to Yahoo despite being forbidden?
    forbidden_fallback = [r for r in rows if r["symbol"] == "ORAS"
                          and r["on_eodhd_failure_provider"] == "yahoo"]

    summary = {
        "universe": len(symbols),
        "eodhd_primary_or_only": len(eodhd),
        "yahoo": len(yahoo),
        "yahoo_only_count": sum(1 for r in rows if r["policy"] == "YAHOO_ONLY"),
        "eodhd_only_count": sum(1 for r in rows if r["policy"] == "EODHD_ONLY"),
        "eodhd_primary_count": sum(1 for r in rows if r["policy"] == "EODHD_PRIMARY"),
        "manual_review": len(unresolved),
        "excluded_non_equity": len(excluded),
        "unavailable": len(unavailable),
        # one EODHD EOD call/symbol/day, cached thereafter → daily live calls ≈ #eodhd
        "estimated_daily_eodhd_calls": len(eodhd),
        "estimated_daily_yahoo_calls": len(yahoo),
        "estimated_cache_hit_rate_after_first_run": "~100% intraday (persistent disk cache)",
        "eodhd_failure_falls_back_to_yahoo": sum(1 for r in rows if r["fallback_used_on_failure"]),
        "eodhd_failure_returns_unavailable": sum(
            1 for r in rows if r["on_eodhd_failure_result"] == "DATA_UNAVAILABLE"),
        "ORAS_never_falls_back_to_yahoo": not forbidden_fallback,
        "policy_active": False, "provider_switched": False,
    }
    (OUT / "routing_dry_run_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _w(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
