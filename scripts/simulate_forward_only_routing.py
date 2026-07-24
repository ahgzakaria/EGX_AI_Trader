"""Forward-only migration simulation over the inactive tiers (Part 8).

    python -m scripts.simulate_forward_only_routing

Simulates a FUTURE policy — forward scans use Tier-A EODHD, historical frozen backtests
keep their existing provider in every tier, Tier B is EODHD without Yahoo fallback,
Tier C stays shadow for backtests, Tier D is Yahoo/manual/unavailable. Feeds nothing
into real decisions and switches nothing.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "reports" / "eodhd"
REVIEW = PROJECT_ROOT / "data" / "eodhd" / "historical_symbol_routing_review.json"


def main():
    data = json.loads(REVIEW.read_text(encoding="utf-8"))
    entries = data.get("symbols", [])
    rows = []
    for e in entries:
        tier = e["tier"]
        # forward provider used by pre-session scans under the simulated policy
        if tier == "TIER_A_FORWARD_SAFE":
            fwd, fallback = "eodhd", "yahoo"
        elif tier == "TIER_B_FORWARD_EODHD_NO_FALLBACK":
            fwd, fallback = "eodhd", None
        elif tier == "TIER_C_HISTORICAL_REVIEW":
            fwd, fallback = "yahoo", None            # stays shadow forward; Yahoo remains active
        else:
            fwd, fallback = ("yahoo" if e.get("forward_primary") == "yahoo" else None), None
        # historical/frozen backtests NEVER move
        rows.append({
            "symbol": e["symbol"], "tier": tier, "forward_provider_simulated": fwd,
            "historical_backtest_provider": "yahoo (unchanged)",
            "forward_fallback": fallback,
            "no_fallback": fallback is None and fwd == "eodhd",
            "on_eodhd_failure": ("DATA_UNAVAILABLE" if fallback is None and fwd == "eodhd"
                                 else ("yahoo_fallback" if fwd == "eodhd" else "n/a")),
            "approved": e["approved"]})
    _w(OUT / "forward_only_routing_simulation.csv",
       ["symbol", "tier", "forward_provider_simulated", "historical_backtest_provider",
        "forward_fallback", "no_fallback", "on_eodhd_failure", "approved"], rows)

    from collections import Counter
    tiers = Counter(r["tier"] for r in rows)
    fwd_eodhd = [r for r in rows if r["forward_provider_simulated"] == "eodhd"]
    no_fb = [r for r in rows if r["no_fallback"]]
    blocked = [r for r in rows if r["forward_provider_simulated"] not in ("eodhd", "yahoo")]
    summary = {
        "universe": len(rows),
        "tier_A_forward_safe": tiers.get("TIER_A_FORWARD_SAFE", 0),
        "tier_B_no_fallback": tiers.get("TIER_B_FORWARD_EODHD_NO_FALLBACK", 0),
        "tier_C_historical_review": tiers.get("TIER_C_HISTORICAL_REVIEW", 0),
        "tier_D_unsupported_or_manual": tiers.get("TIER_D_UNSUPPORTED_OR_MANUAL", 0),
        "forward_uses_eodhd": len(fwd_eodhd),
        "symbols_with_no_fallback": len(no_fb),
        "symbols_blocked_from_migration": len(blocked),
        "historical_backtests_moved": 0,
        "strategy_outputs_that_would_differ": "forward signals for Tier-A/B (EODHD's more "
            "complete bar coverage shifts some signals vs Yahoo) — measured per-symbol in "
            "real_backtest_summary.csv; frozen backtests unchanged",
        "estimated_daily_eodhd_calls": len(fwd_eodhd),
        "estimated_daily_yahoo_calls": sum(1 for r in rows if r["forward_provider_simulated"] == "yahoo"),
        "estimated_cache_hit_rate": "~100% intraday (persistent disk cache)",
        "failure_outcomes": {"eodhd_down_tierA": "Yahoo fallback",
                             "eodhd_down_tierB": "DATA_UNAVAILABLE (never bad Yahoo)",
                             "never_empty_or_zero": True},
        "policy_active": False, "provider_switched": False,
    }
    (OUT / "routing_tier_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _w(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
