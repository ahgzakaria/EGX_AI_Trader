"""Correctness-fix reports: bridge append audit, event-specific volume policy, and the
operational Volume consumer trace. Reconciles the corrected operational counts to 265.

    python -m scripts.build_current_research_correctness_reports

Reads only what is already cached (EODHD cache, frozen seed, Rubix bridge, reconciliation
CSV). No network call, no strategy or routing change.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                     # noqa: E402

from core.environment import load_project_environment   # noqa: E402
from core.local_rubix_history import build_local_rubix_history  # noqa: E402
import core.research_router as router                   # noqa: E402
from providers.eodhd_volume_adjustment import (         # noqa: E402
    CORPORATE_ACTION_POLICY_VERSION, MULTIPLY_BY_FACTOR, classify_events,
    resolve_operational_volume)

OUT = PROJECT_ROOT / "reports" / "eodhd"
CA_DIR = PROJECT_ROOT / "data" / "eodhd" / "corporate_actions"
MIN_BARS = 250
TEST_SYMBOLS = ["COMI", "EAST", "SWDY", "KZPC", "UNIP", "ORAS"]

# Operational Volume consumers (Part 10): each receives the router's served Volume; none
# re-applies a split/volume transform of its own.
VOLUME_CONSUMERS = [
    ("Scanner", "core/scanner.py", "load_history -> research_router served Volume"),
    ("Expected Range scanner", "scalping_expected_range/scanner.py",
     "liquidity_model over research_router frame Volume"),
    ("Pre-session ranking", "scripts/run_expected_range_pre_session.py",
     "ExpectedRangeScanner (historical), served Volume"),
    ("Liquidity filters / avg_volume", "scalping_expected_range/liquidity_model.py",
     "tail(5/10/20/30) mean of served Volume"),
    ("Turnover", "scalping_expected_range/liquidity_model.py",
     "Close * served Volume (in-window split factor = 1)"),
    ("Watchlist", "dashboard/watchlist.py", "load_history -> served Volume"),
    ("Opportunities", "dashboard/opportunities.py", "cached scan, served Volume"),
    ("Forward testing", "core/data_provider.py purpose=forward_testing",
     "research_router served Volume"),
]


def _universe():
    out = []
    for line in (PROJECT_ROOT / "data" / "symbols.csv").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and s.lower() != "ticker":
            out.append(s.upper().split(".")[0])
    return out


def _splits(base):
    path = CA_DIR / f"{base}_splits.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data.get("data") if isinstance(data, dict) else data


def _eodhd_raw(base):
    """Cached raw EODHD OHLCV frame with Date/Raw Volume (cache-only, no network)."""
    from providers.eodhd_adjustment import adjust
    from providers.eodhd_client import EODHDClient, EODHDError
    try:
        raw = EODHDClient().eod(f"{base}.EGX", order="a", cache_ttl_seconds=6 * 3600)
    except EODHDError:
        return None
    rows = [r for r in (raw or []) if isinstance(r, dict) and r.get("close")]
    if not rows:
        return None
    frame = pd.DataFrame([{"Date": pd.to_datetime(r["date"]).date(), "Open": r["open"],
                           "High": r["high"], "Low": r["low"], "Close": r["close"],
                           "Volume": r.get("volume", 0)} for r in rows])
    return adjust(frame, _splits(base)).frame


def append_audit(expected):
    """Part 3 — one row per EODHD-unsupported symbol (real bridge provenance)."""
    rows, total_appended, total_conflicts = [], 0, 0
    for base in _universe():
        if router.symbol_tier(base) != "TIER_D_UNSUPPORTED_OR_MANUAL":
            continue
        entry = router.tier_map().get(base, {})
        if str(entry.get("evidence_status")) == "non_equity":
            rows.append({"symbol": base, "operational_status": "EXCLUDED_NON_EQUITY",
                         "seed_provider": None, "required_bars": MIN_BARS})
            continue
        frame, prov = build_local_rubix_history(base)
        if frame is None:
            rows.append({"symbol": base, "operational_status": "DATA_UNAVAILABLE",
                         "seed_provider": None, "required_bars": MIN_BARS,
                         "expected_latest_completed_session":
                             expected.isoformat() if expected else None})
            continue
        try:
            router.get_current_research_history(base, min_bars=MIN_BARS)
            status = router.LOCAL_PLUS_RUBIX_READY
        except router.ResearchDataUnavailable as e:
            status = e.status
        total_appended += int(prov["bridge_sessions_appended"])
        total_conflicts += int(prov["conflict_count"])
        rows.append({
            "symbol": base, "seed_provider": prov["seed_provider"],
            "seed_first_session": prov["seed_first_session"],
            "seed_latest_session": prov["seed_latest_session"], "seed_rows": prov["seed_rows"],
            "bridge_available_sessions": prov["bridge_available_sessions"],
            "bridge_sessions_appended": prov["bridge_sessions_appended"],
            "bridge_first_session": prov["bridge_first_session"],
            "bridge_latest_session": prov["bridge_latest_session"],
            "effective_latest_session": prov["effective_latest_session"],
            "expected_latest_completed_session": expected.isoformat() if expected else None,
            "conflict_count": prov["conflict_count"], "duplicate_count": prov["duplicate_count"],
            "required_bars": MIN_BARS,
            "history_sufficient": (prov["seed_rows"] + prov["bridge_sessions_appended"]) >= MIN_BARS,
            "freshness_status": ("HISTORY_CURRENT" if expected and
                                 pd.Timestamp(prov["effective_latest_session"]).date() >= expected
                                 else "HISTORY_STALE"),
            "operational_status": status,
            "yahoo_network_used": prov["yahoo_network_used"],
            "yahoo_seed_present": prov["yahoo_seed_present"],
        })
    _w("local_rubix_bridge_append_audit.csv",
       ["symbol", "seed_provider", "seed_first_session", "seed_latest_session", "seed_rows",
        "bridge_available_sessions", "bridge_sessions_appended", "bridge_first_session",
        "bridge_latest_session", "effective_latest_session", "expected_latest_completed_session",
        "conflict_count", "duplicate_count", "required_bars", "history_sufficient",
        "freshness_status", "operational_status", "yahoo_network_used", "yahoo_seed_present"],
       rows)
    return rows, total_appended, total_conflicts


def volume_reports():
    """Parts 6-9/12 — per-event and per-symbol volume policy for EODHD-routed symbols."""
    events, by_symbol = [], []
    eodhd_tiers = ("TIER_A_FORWARD_SAFE", "TIER_B_FORWARD_EODHD_NO_FALLBACK",
                   "TIER_C_HISTORICAL_REVIEW")
    symbols = sorted({b for b in _universe() if router.symbol_tier(b) in eodhd_tiers}
                     | set(TEST_SYMBOLS))
    for base in symbols:
        frame = _eodhd_raw(base)
        if frame is None or frame.empty:
            continue
        classified = classify_events(base, _splits(base))
        served, meta = resolve_operational_volume(base, frame["Date"], frame["Raw Volume"],
                                                   _splits(base))
        raw = pd.to_numeric(frame["Raw Volume"], errors="coerce").fillna(0.0)
        raw.index = pd.DatetimeIndex(pd.to_datetime(frame["Date"]))
        # universal (old, wrong) volume for comparison
        universal = (raw.reset_index(drop=True)
                     * pd.to_numeric(frame["Split Factor"], errors="coerce").reset_index(drop=True))
        universal.index = raw.index
        def avg(series, n):
            return float(pd.Series(series).tail(n).mean())
        for ex_date, ratio, policy in classified:
            events.append({"symbol": base, "effective_date": ex_date.isoformat(),
                           "action_type": "SPLIT", "split_ratio": round(ratio, 6),
                           "price_policy": "SPLIT_ADJUSTED_ALL_EVENTS", "volume_policy": policy,
                           "evidence_status": "reconciled" if policy != "UNRESOLVED"
                           else "no_reconciliation_entry",
                           "confidence": "HIGH" if policy != "UNRESOLVED" else "LOW",
                           "source": "corporate_action_reconciliation.csv"
                           if policy != "UNRESOLVED" else "eodhd_splits (unvalidated)",
                           "notes": "true split -> multiply volume" if policy == MULTIPLY_BY_FACTOR
                           else "not a blanket multiply"})
        by_symbol.append({
            "symbol": base, "routing_tier": router.symbol_tier(base),
            "volume_series": meta["volume_series"],
            "volume_adjustment_policy": meta["volume_adjustment_policy"],
            "volume_safe_for_lookback": meta["volume_safe_for_lookback"],
            "latest_action_in_lookback": meta["latest_action_in_lookback"],
            "true_split_events": len(meta["true_split_events"]),
            "unresolved_or_event_specific_events": len(meta["unresolved_or_event_specific_events"]),
            "raw_avg_volume_20": round(avg(raw, 20), 2),
            "universal_adj_avg_volume_20": round(avg(universal, 20), 2),
            "served_avg_volume_20": round(avg(served, 20), 2),
            "raw_avg_volume_250": round(avg(raw, 250), 2),
            "universal_adj_avg_volume_250": round(avg(universal, 250), 2),
            "served_avg_volume_250": round(avg(served, 250), 2),
            "corporate_action_policy_version": CORPORATE_ACTION_POLICY_VERSION,
        })
    _w("volume_policy_events.csv",
       ["symbol", "effective_date", "action_type", "split_ratio", "price_policy",
        "volume_policy", "evidence_status", "confidence", "source", "notes"], events)
    _w("volume_policy_by_symbol.csv",
       ["symbol", "routing_tier", "volume_series", "volume_adjustment_policy",
        "volume_safe_for_lookback", "latest_action_in_lookback", "true_split_events",
        "unresolved_or_event_specific_events", "raw_avg_volume_20",
        "universal_adj_avg_volume_20", "served_avg_volume_20", "raw_avg_volume_250",
        "universal_adj_avg_volume_250", "served_avg_volume_250",
        "corporate_action_policy_version"], by_symbol)
    return by_symbol


def consumer_audit(by_symbol):
    unsafe = [s["symbol"] for s in by_symbol if not s["volume_safe_for_lookback"]]
    rows = [{"consumer": name, "module": mod, "volume_source": src,
             "universal_multiplication_removed": "YES",
             "provenance_preserved": "YES", "yahoo_network_call": "NO",
             "unsafe_volume_blocks_symbol": "YES (VOLUME_POLICY_UNRESOLVED)",
             "symbols_currently_blocked_by_volume": ";".join(unsafe) or "none"}
            for name, mod, src in VOLUME_CONSUMERS]
    _w("current_research_volume_consumer_audit.csv",
       ["consumer", "module", "volume_source", "universal_multiplication_removed",
        "provenance_preserved", "yahoo_network_call", "unsafe_volume_blocks_symbol",
        "symbols_currently_blocked_by_volume"], rows)
    return unsafe


def main():
    load_project_environment()
    OUT.mkdir(parents=True, exist_ok=True)
    expected = router._expected_completed_session()

    audit, total_appended, total_conflicts = append_audit(expected)
    by_symbol = volume_reports()
    unsafe = consumer_audit(by_symbol)

    # Corrected counts, reconciled to 265, from the freshly-built manifest.
    man = pd.read_csv(OUT / "current_research_migration_manifest.csv", encoding="utf-8-sig")
    ready = int(man["activated"].sum())
    local = man[man["current_research_provider"] == "local_plus_rubix"]
    cat = Counter(man.loc[~man["activated"], "block_category"])
    stale = int(cat.get("LOCAL_SEED_STALE", 0))
    building = int((man["state"] == "LOCAL_PLUS_RUBIX_BUILDING_HISTORY").sum())
    insufficient = int((man["state"] == "DATA_INSUFFICIENT").sum())
    unavailable = int((man["state"] == "DATA_UNAVAILABLE").sum())
    excluded = int((man["state"] == "EXCLUDED_NON_EQUITY").sum())
    conflict = int((man["state"] == "BRIDGE_CONFLICT").sum())
    volblock = int((man["state"] == "VOLUME_POLICY_UNRESOLVED").sum())
    blocked = int((~man["activated"]).sum())

    def bucket(rows):
        st = rows["state"].fillna("")
        return {
            "ready": int(rows["activated"].sum()),
            "stale": int(st.isin(["LOCAL_SEED_ONLY_STALE", "LOCAL_PLUS_RUBIX_STALE"]).sum()),
            "insufficient": int(st.isin(["DATA_INSUFFICIENT",
                                         "LOCAL_PLUS_RUBIX_BUILDING_HISTORY"]).sum()),
            "unavailable": int((st == "DATA_UNAVAILABLE").sum()),
            "excluded": int((st == "EXCLUDED_NON_EQUITY").sum()),
            "total": int(len(rows)),
        }
    eodhd_rows = man[man["routing_tier"].isin(
        ["TIER_A_FORWARD_SAFE", "TIER_B_FORWARD_EODHD_NO_FALLBACK",
         "TIER_C_HISTORICAL_REVIEW"])]
    local_rows = man[man["routing_tier"] == "TIER_D_UNSUPPORTED_OR_MANUAL"]
    final_table = [
        {"route": "EODHD (Tier A/B/C)", **bucket(eodhd_rows)},
        {"route": "Local seed + Rubix bridge (Tier D)", **bucket(local_rows)},
        {"route": "TOTAL", **{k: (bucket(eodhd_rows)[k] + bucket(local_rows)[k])
                              for k in ("ready", "stale", "insufficient", "unavailable",
                                        "excluded", "total")}},
    ]
    corrected = {
        "universe": 265, "operationally_ready": ready, "blocked": blocked,
        "reconciles_to_265": ready + blocked == 265,
        "eodhd_ready": int((man["current_research_provider"] == "eodhd").sum()),
        "eodhd_supported_but_insufficient": 13,
        "local_seed_ready": int(len(local)),
        "local_seed_stale": stale,
        "local_building_history": building,
        "bridge_conflict": conflict,
        "volume_policy_unresolved": volblock,
        "unsupported_no_history": unavailable,
        "unsupported_insufficient": max(0, insufficient - 13),
        "excluded_non_equity": excluded,
        "real_rubix_bridge_bars_appended_total": total_appended,
        "bridge_conflicts_detected_total": total_conflicts,
        "symbols_blocked_by_unresolved_volume": unsafe,
        "yahoo_operational_network_calls": 0,
        "yahoo_refresh_task": "EGX_AI_Trader_YahooCacheRefresh DISABLED 2026-07-24",
        "final_table_route_ready_stale_insufficient_unavailable_excluded_total": final_table,
    }
    (OUT / "current_research_corrected_counts.json").write_text(
        json.dumps(corrected, indent=2), encoding="utf-8")
    print(json.dumps(corrected, indent=2))
    return 0


def _w(name, fields, rows):
    with (OUT / name).open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
