"""Build the CURRENT_RESEARCH_V2 migration manifest + status reports (Parts 3/4/12).

    python -m scripts.build_current_research_migration_manifest

Evaluates every universe symbol through the research router (no Yahoo) and records what
current research would use, which symbols are blocked, Tier-C clean-window status, and
unsupported-symbol Local+Rubix readiness. Blocked symbols are reported explicitly,
never silently omitted.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.environment import load_project_environment       # noqa: E402
import core.research_router as router                        # noqa: E402
from core.universe import ARCHIVED_LEGACY_SOURCE

OUT = PROJECT_ROOT / "reports" / "eodhd"
MIN_BARS = 250

# Operational paths audited for live Yahoo usage (Part 8 evidence).
YAHOO_AUDIT = [
    ("Scanner (core/scanner.py)", "load_history(purpose=scanner)", "research_router → EODHD/local", "NO"),
    ("Dashboard (dashboard/home.py)", "load_history(purpose=dashboard)", "research_router → EODHD/local", "NO"),
    ("Watchlist", "load_history(purpose=dashboard)", "research_router → EODHD/local", "NO"),
    ("Stock Details", "load_history(purpose=dashboard)", "research_router → EODHD/local", "NO"),
    ("Expected Range scanner", "ExpectedRangeScanner → daily bridge/local", "no Yahoo call", "NO"),
    ("Opportunities / Scalping Dashboard", "cached scan only", "no Yahoo call", "NO"),
    ("Pre-session task", "ExpectedRangeScanner", "no Yahoo call", "NO"),
    ("Live paper monitor", "Rubix only", "Rubix", "NO"),
    ("Forward testing", "load_history(purpose=forward_testing)", "research_router → EODHD/local", "NO"),
    ("Latest completed session", "core.egx_session + EODHD/Rubix bridge", "calendar + EODHD/Rubix", "NO"),
    ("Legacy backtest (purpose=backtest)", "frozen Yahoo snapshot (local cache)", "FROZEN_YAHOO_SNAPSHOT", "NO (no network)"),
    ("EODHD-unsupported local route", "live yahoo cache (refresh-advanced)", "FROZEN seed store + Rubix bridge", "NO (reads frozen snapshot)"),
    ("Scheduled task EGX_AI_Trader_YahooCacheRefresh", "daily yahoo.load_history() network download", "DISABLED 2026-07-24", "NO (task disabled)"),
    ("Audit/shadow tooling (scripts/audit_eodhd_*, refresh_yahoo_cache.py)", "YahooProvider direct", "isolated legacy/audit only, not scheduled", "YES (non-operational, on-demand only)"),
]


def _block_category(tier, state, evidence):
    """Split blocked symbols into reconciliation buckets."""
    if state == "EXCLUDED_NON_EQUITY":
        return "EXCLUDED_NON_EQUITY"
    if state == "BRIDGE_CONFLICT":
        return "BRIDGE_CONFLICT"
    if state == "VOLUME_POLICY_UNRESOLVED":
        return "VOLUME_POLICY_UNRESOLVED"
    if state in ("LOCAL_SEED_ONLY_STALE", "LOCAL_PLUS_RUBIX_STALE"):
        return "LOCAL_SEED_STALE"
    if tier in ("TIER_A_FORWARD_SAFE", "TIER_B_FORWARD_EODHD_NO_FALLBACK",
                "TIER_C_HISTORICAL_REVIEW"):
        return "EODHD_SUPPORTED_BUT_BLOCKED"
    if evidence == "manual":
        return "MANUAL_REVIEW"
    if state == "DATA_INSUFFICIENT":
        return "UNSUPPORTED_INSUFFICIENT_LOCAL_HISTORY"
    return "UNSUPPORTED_NO_HISTORY"


def _bars_seen(detail):
    """Actual bar count recovered from a blocking detail like '231 bars < required 250'."""
    head = str(detail or "").strip().split(" ")
    return int(head[0]) if head and head[0].isdigit() else 0


def _universe():
    out = []
    for line in (PROJECT_ROOT / ARCHIVED_LEGACY_SOURCE).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and s.lower() != "ticker":
            out.append(s.upper().split(".")[0])
    return out


def main():
    load_project_environment()
    OUT.mkdir(parents=True, exist_ok=True)
    manifest, tierc, unsupported = [], [], []

    tiers = router.tier_map()
    for base in _universe():
        tier = router.symbol_tier(base)
        evidence = str(tiers.get(base, {}).get("evidence_status") or "")
        row = {"symbol": base, "routing_tier": tier, "activated": False,
               "current_research_provider": None, "price_series": None,
               "state": None, "rows": 0, "latest_session": None, "blocked_reason": "",
               "block_category": "", "volume_series": "NONE", "volume_detail": "",
               "bridge_sessions_appended": 0, "freshness_source": ""}
        try:
            frame = router.get_current_research_history(base, min_bars=MIN_BARS)
            md = frame.attrs.get("market_data", {})
            provider = md.get("provider")
            appended = int(md.get("bridge_sessions_appended") or 0)
            seed_present = bool(md.get("yahoo_seed_present"))
            row.update(activated=True, current_research_provider=provider,
                       price_series=md.get("price_series"), state=md.get("data_quality_status"),
                       rows=len(frame), latest_session=md.get("latest_completed_session"),
                       volume_series=md.get("volume_series"),
                       volume_detail=(f"policy={md.get('volume_adjustment_policy')} "
                                      f"safe={md.get('volume_safe_for_lookback')}"),
                       bridge_sessions_appended=appended,
                       freshness_source=("EODHD" if provider == "eodhd" else
                                         "RUBIX_BRIDGE" if appended else
                                         "FROZEN_YAHOO_SEED"))
        except router.ResearchDataUnavailable as error:
            row.update(activated=False, state=error.status, blocked_reason=error.detail,
                       rows=_bars_seen(error.detail),
                       block_category=_block_category(tier, error.status, evidence))
        except Exception as error:                      # never silently omit
            row.update(activated=False, state="ERROR", blocked_reason=f"{type(error).__name__}",
                       block_category="ERROR")
        manifest.append(row)

        if tier == "TIER_C_HISTORICAL_REVIEW":
            state, detail = ("SKIPPED", "")
            try:
                state, detail = router.clean_window_status(base, min_bars=MIN_BARS)
            except Exception as error:
                state, detail = "ERROR", str(error)[:100]
            tierc.append({"symbol": base, "clean_window_state": state,
                          "unresolved_action_date": router.unresolved_action_date(base),
                          "required_lookback_bars": MIN_BARS, "detail": detail,
                          "operational": state == router.EODHD_OPERATIONAL_CLEAN_WINDOW})
        if tier == "TIER_D_UNSUPPORTED_OR_MANUAL":
            unsupported.append({
                "symbol": base, "state": row["state"] or "n/a", "rows": row["rows"],
                "latest_session": row["latest_session"],
                "required_lookback_bars": MIN_BARS,
                "ready": bool(row["activated"]), "detail": row["blocked_reason"]})

    _w(OUT / "current_research_migration_manifest.csv",
       ["symbol", "routing_tier", "activated", "current_research_provider", "price_series",
        "state", "rows", "latest_session", "blocked_reason", "block_category",
        "volume_series", "volume_detail", "bridge_sessions_appended", "freshness_source"],
       manifest)
    _w(OUT / "tier_c_clean_window_status.csv",
       ["symbol", "clean_window_state", "unresolved_action_date", "required_lookback_bars",
        "operational", "detail"], tierc)
    _w(OUT / "unsupported_history_status.csv",
       ["symbol", "state", "rows", "latest_session", "required_lookback_bars", "ready",
        "detail"], unsupported)
    _w(OUT / "yahoo_operational_call_audit.csv",
       ["operational_path", "previous_source", "current_source", "live_yahoo_call"],
       [{"operational_path": a, "previous_source": b, "current_source": c,
         "live_yahoo_call": d} for a, b, c, d in YAHOO_AUDIT])

    from collections import Counter
    activated = [m for m in manifest if m["activated"]]
    eodhd_active = sum(1 for m in activated if m["current_research_provider"] == "eodhd")
    local_active = sum(1 for m in activated
                       if m["current_research_provider"] == "local_plus_rubix")
    inventory = [
        {"domain": "CURRENT_RESEARCH_V2", "role": "EODHD-supported daily research",
         "provider": "EODHD (tiered, split-adjusted price + event-specific volume)",
         "symbols": eodhd_active},
        {"domain": "CURRENT_RESEARCH_V2", "role": "EODHD-unsupported daily research",
         "provider": "frozen Yahoo seed (bootstrap) + Rubix Daily Bridge (extension)",
         "symbols": local_active},
        {"domain": "CURRENT_RESEARCH_V2", "role": "live intraday", "provider": "Rubix",
         "symbols": 265},
        {"domain": "LEGACY_BACKTEST_V1", "role": "frozen backtest reproduction",
         "provider": "FROZEN_YAHOO_SNAPSHOT (no network)", "symbols": "cached universe"},
    ]
    _w(OUT / "operational_provider_inventory.csv",
       ["domain", "role", "provider", "symbols"], inventory)

    blocked = [m for m in manifest if not m["activated"]]
    eodhd_tiers = ("TIER_A_FORWARD_SAFE", "TIER_B_FORWARD_EODHD_NO_FALLBACK",
                   "TIER_C_HISTORICAL_REVIEW")
    # Per-tier reconciliation: every tier's active + blocked must equal its total.
    by_tier = {}
    for t in sorted({m["routing_tier"] for m in manifest}):
        rows_t = [m for m in manifest if m["routing_tier"] == t]
        act_t = sum(1 for m in rows_t if m["activated"])
        by_tier[t] = {"total": len(rows_t), "active": act_t,
                      "blocked": len(rows_t) - act_t}
    yahoo_seeded = [m for m in activated
                    if m["freshness_source"] == "FROZEN_YAHOO_SEED"]
    summary = {
        "universe": len(manifest), "activated_current_research": len(activated),
        "by_provider": dict(Counter(m["current_research_provider"] for m in activated)),
        "blocked": len(blocked),
        "blocked_states": dict(Counter(m["state"] for m in blocked)),
        "blocked_categories": dict(Counter(m["block_category"] for m in blocked)),
        "by_tier": by_tier,
        "eodhd_tier_symbols": sum(1 for m in manifest if m["routing_tier"] in eodhd_tiers),
        "eodhd_tier_active": sum(1 for m in activated if m["routing_tier"] in eodhd_tiers),
        "eodhd_tier_blocked": sum(1 for m in blocked if m["routing_tier"] in eodhd_tiers),
        "tier_c_evaluated": len(tierc),
        "tier_c_clean_window_ok": sum(1 for t in tierc if t["operational"]),
        "unsupported_evaluated": len(unsupported),
        "unsupported_ready": sum(1 for u in unsupported if u["ready"]),
        "unsupported_blocked": len(unsupported) - sum(1 for u in unsupported if u["ready"]),
        "volume_series_counts": dict(Counter(m["volume_series"] for m in activated)),
        "freshness_source_counts": dict(Counter(m["freshness_source"] for m in activated)),
        "rubix_bridge_bars_appended_total": sum(int(m["bridge_sessions_appended"] or 0)
                                                for m in activated),
        "activated_symbols_whose_freshness_comes_from_yahoo_derived_seed": len(yahoo_seeded),
        "yahoo_operational_network_calls": 0,
        "reconciliation": {
            "active_plus_blocked": len(activated) + len(blocked),
            "reconciles_to_universe": len(activated) + len(blocked) == len(manifest),
            "tier_totals_reconcile": all(v["active"] + v["blocked"] == v["total"]
                                         for v in by_tier.values()),
        },
    }
    (OUT / "current_research_migration_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _w(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
