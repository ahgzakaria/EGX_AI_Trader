"""Stage A — immutable pre-session snapshot + manifest (~09:45 Cairo).

Uses ONLY completed historical daily data (never current-session quotes), freezes
the liquidity-first ranks, Core/Expansion/Extreme ranges and rejection reasons,
and writes an immutable snapshot + manifest with a status. A snapshot generated
after 10:00 Cairo is marked SNAPSHOT_LATE (never silently treated as valid).

    python scripts/run_expected_range_pre_session.py [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import warnings

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.egx_session import (  # noqa: E402
    cairo_now,
    classify_history_freshness,
    is_regular_trading_day,
)
from scalping_expected_range.audit import _pctl  # noqa: E402
from scalping_expected_range.config import ExpectedRangeConfig  # noqa: E402
from scalping_expected_range.orchestration import (  # noqa: E402
    NON_TRADING_DAY,
    settings_hash,
    snapshot_status,
    write_manifest,
)
from scalping_expected_range.paper_recorder import PaperRecorder  # noqa: E402
from scalping_expected_range.scanner import ExpectedRangeScanner  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    args = ap.parse_args(argv)

    cfg = ExpectedRangeConfig.load()
    assert cfg.paper_enabled and not cfg.production_enabled, "paper on, production off"

    from config.settings_manager import settings
    from core.egx_calendar import effective_holidays
    holidays = effective_holidays()   # shared calendar (built-in ∪ configured)
    now = cairo_now()
    session_date = args.date or now.date().isoformat()
    trading = is_regular_trading_day(now.date(), holidays)

    # Dynamic-calendar safe gating: a pending-review / uncertain day is neither a
    # confirmed holiday nor a normal live session — do NOT run a countable session.
    from core.egx_calendar import session_is_uncertain, session_status
    review = session_is_uncertain(now)

    root = cfg.paper_output_root
    manifest_path = Path(root) / session_date / "pre_session_manifest.json"

    if not trading or review:
        s = session_status(now)
        write_manifest(manifest_path, {
            "session_date": session_date, "snapshot_status": NON_TRADING_DAY,
            "session_calendar_status": s.status, "review_required": s.review_required,
            "snapshot_created_at": now.isoformat(), "strategy_version": cfg.strategy_version,
            "research_version": "FORWARD_RESEARCH_V2_EODHD",
            "data_domain": "CURRENT_RESEARCH_V2",
        })
        print(json.dumps({"session_date": session_date, "snapshot_status": NON_TRADING_DAY,
                          "session_calendar_status": s.status,
                          "review_required": s.review_required}))
        return

    scanner = ExpectedRangeScanner(config=cfg, holidays=holidays)
    scan = scanner.scan(with_live=False)          # historical only — no current-session quotes
    universe = scan["universe"].copy()
    universe["_RawVolumeRank"] = _pctl(universe.get("liq_avg_volume_20")).rank(
        ascending=False, method="min")
    universe["_TurnoverRank"] = _pctl(universe.get("liq_avg_turnover_egp_20")).rank(
        ascending=False, method="min")

    latest_completed = universe["prov_latest_completed_session"].dropna().max() \
        if "prov_latest_completed_session" in universe else None
    fresh = classify_history_freshness(latest_completed, now=now, holidays=holidays)
    status = snapshot_status(now.timetz().replace(tzinfo=None), fresh.status, trading)

    recorder = PaperRecorder(cfg, session_date, now=now, holidays=holidays)
    snapshot_path, dataset_hash = recorder.write_presession_snapshot(universe, latest_completed)

    manifest = {
        "session_date": session_date,
        "snapshot_created_at": now.isoformat(),
        "latest_historical_session": latest_completed,
        "history_freshness": fresh.status,
        "provider": "eodhd+local_validated/rubix_bridge",
        "research_version": "FORWARD_RESEARCH_V2_EODHD",
        "data_domain": "CURRENT_RESEARCH_V2",
        "dataset_hash": dataset_hash,
        "symbol_count": int(len(universe)),
        "settings_hash": settings_hash(),
        "strategy_version": cfg.strategy_version,
        "snapshot_status": status,
        "snapshot_path": str(snapshot_path),
        "flags": {"paper_enabled": cfg.paper_enabled, "production_enabled": cfg.production_enabled,
                  "automatic_execution": cfg.automatic_execution,
                  "broker_orders_enabled": cfg.broker_orders_enabled},
    }
    write_manifest(manifest_path, manifest)
    print(json.dumps(manifest, indent=2, default=str))
    return manifest


if __name__ == "__main__":
    main()
