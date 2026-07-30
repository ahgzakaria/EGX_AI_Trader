"""EODHD EGX capability audit — key-independent portions + honest empty-state.

Runs the real Phase 1 entitlement probe (which returns API_KEY_MISSING when no key
is configured), the offline Phase 2 symbol-mapping coverage (all 265 symbols), the
analytical Phase 10 cost/capacity model, and emits every deliverable CSV. Phases
that require authenticated live calls (finalization timing, revisions,
reconciliation, volume semantics, historical audit, strategy impact) are written as
schema-only status files marked API_KEY_MISSING — never fabricated. Never exposes a
key; never activates any provider.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import socket
import sys
import urllib.request
import warnings

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.providers.eodhd_daily import (  # noqa: E402
    API_KEY_MISSING,
    EodhdDailyClient,
    probe_summary,
)
from core.symbols import load_symbols  # noqa: E402
from providers.symbol_mapping import to_eodhd_symbol  # noqa: E402
from core.symbols import SYMBOL_SOURCE

REPORTS = Path("reports")
PENDING = "PENDING_API_KEY"


def _write(name, fields, rows):
    p = REPORTS / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _network_reachable():
    try:
        socket.setdefaulttimeout(8)
        r = urllib.request.urlopen("https://eodhd.com/robots.txt")
        return r.status == 200
    except Exception:
        return False


def main():
    client = EodhdDailyClient()
    probe = probe_summary(client)
    reachable = _network_reachable()
    key_ok = client.key_configured
    live_status = "OK" if key_ok else API_KEY_MISSING

    symbols = load_symbols(SYMBOL_SOURCE)

    # Phase 2 — symbol-mapping coverage (offline mapping; live columns PENDING).
    cov_rows = []
    for s in symbols:
        cov_rows.append({
            "internal_symbol": s, "eodhd_symbol": to_eodhd_symbol(s),
            "lookup_success": PENDING if not key_ok else "",
            "first_available_date": PENDING, "last_available_date": PENDING,
            "historical_row_count": PENDING, "currency": PENDING, "exchange": "EGX",
            "volume_available": PENDING, "adjusted_available": PENDING,
            "unsupported_reason": API_KEY_MISSING if not key_ok else "",
        })
    _write("eodhd_symbol_coverage.csv",
           ["internal_symbol", "eodhd_symbol", "lookup_success", "first_available_date",
            "last_available_date", "historical_row_count", "currency", "exchange",
            "volume_available", "adjusted_available", "unsupported_reason"], cov_rows)

    # Live-only deliverables: honest schema-only status files.
    def status_csv(name, fields):
        _write(name, fields, [{fields[0]: "ALL", "status": live_status,
                               "note": "requires authenticated EODHD calls; provide a key to populate"}
                              if "status" in fields else {}])

    status_csv("eodhd_finalization_observations.csv",
               ["engine_symbol", "poll_timestamp_cairo", "status", "note"])
    status_csv("eodhd_bar_revisions.csv",
               ["engine_symbol", "field_changed", "old_value", "new_value", "status", "note"])
    status_csv("eodhd_rubix_reconciliation.csv",
               ["symbol", "session_date", "close_diff_pct", "volume_ratio", "classification",
                "status", "note"])
    status_csv("eodhd_volume_semantics.csv",
               ["symbol", "eodhd_volume", "rubix_cumulative_volume", "ratio", "classification",
                "status", "note"])
    status_csv("eodhd_historical_quality.csv",
               ["symbol", "missing_dates", "invalid_ohlc", "zero_volume", "classification",
                "status", "note"])
    status_csv("eodhd_corporate_actions.csv",
               ["symbol", "event_type", "event_date", "raw_behavior", "adjusted_behavior",
                "status", "note"])
    status_csv("eodhd_strategy_impact.csv",
               ["variant", "qualified_universe", "top10_overlap", "data_stale_count",
                "status", "note"])

    # Phase 10 — analytical cost/capacity (documented EODHD API model, pending live).
    cost_rows = [
        {"item": "initial_full_backfill_single_symbol", "requests": len(symbols),
         "basis": "1 EOD call per symbol (full history in one call) x265"},
        {"item": "daily_incremental_bulk", "requests": 1,
         "basis": "eod-bulk-last-day/EGX returns the whole exchange in 1 call"},
        {"item": "daily_incremental_single", "requests": len(symbols),
         "basis": "1 EOD call per symbol if not using bulk"},
        {"item": "finalization_polling_per_session_single", "requests": 8 * 24,
         "basis": "8-symbol basket x 24 polls (5-min, 14:30-16:30)"},
        {"item": "finalization_polling_per_session_bulk", "requests": 24,
         "basis": "1 bulk call per poll x 24 polls"},
        {"item": "corporate_action_refresh_full", "requests": len(symbols) * 2,
         "basis": "dividends + splits per symbol (infrequent)"},
        {"item": "free_plan_daily_allowance", "requests": 20,
         "basis": "EODHD free tier (per prior EODHD_PHASE1_REPORT: exhausted, ~1yr history)"},
        {"item": "paid_eod_plan_daily_allowance", "requests": 100000,
         "basis": "EODHD EOD Historical plan documented daily API limit"},
        {"item": "estimated_normal_day_bulk", "requests": 1 + 24,
         "basis": "bulk daily update (1) + bulk finalization polling (24)"},
    ]
    _write("eodhd_cost_capacity.csv", ["item", "requests", "basis"], cost_rows)

    summary = {
        "phase1_entitlement": probe,
        "network_reachable_eodhd": reachable,
        "key_configured": key_ok,
        "phase2_universe": len(symbols),
        "phase2_mapped_to_egx": sum(1 for s in symbols if to_eodhd_symbol(s).endswith(".EGX")),
        "phase2_ambiguous": sum(1 for s in symbols if not to_eodhd_symbol(s).endswith(".EGX")),
        "live_phases_status": live_status,
        "cost_model_estimate_normal_day_bulk": 25,
        "free_plan_sufficient": False,
        "flags": {"eodhd_production_enabled": False, "eodhd_activated": False},
        "reports": sorted(str(p) for p in REPORTS.glob("eodhd_*.csv")),
    }
    print(json.dumps(summary, indent=2, default=str))
    return summary


if __name__ == "__main__":
    main()
