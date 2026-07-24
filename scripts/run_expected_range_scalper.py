"""Generate EXPECTED_RANGE_SCALPER research reports on real data (all-disabled).

Runs the isolated liquidity-first pre-session scan + walk-forward selection
validation and writes the six deliverable CSVs. Never enables paper/production,
never places an order, never touches another strategy.

    python scripts/run_expected_range_scalper.py [--no-live] [--test-sessions N]
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

from scalping_expected_range.backtest import (  # noqa: E402
    run_selection_validation,
    scenario_execution_status,
)
from scalping_expected_range.config import ExpectedRangeConfig  # noqa: E402
from scalping_expected_range.scanner import (  # noqa: E402
    ExpectedRangeScanner,
    ImmutablePaperSignalStore,
)

UNIVERSE_CSV = "reports/expected_range_scalping_universe.csv"
TOP_CSV = "reports/expected_range_top_candidates.csv"
SCENARIOS_CSV = "reports/expected_range_scenarios.csv"
REJECTIONS_CSV = "reports/expected_range_rejections.csv"
VALIDATION_CSV = "reports/expected_range_selection_validation.csv"
PAPER_CSV = "reports/expected_range_paper_signals.csv"

UNIVERSE_COLS = [
    "Rank", "Symbol", "PrevClose", "liq_avg_volume_20", "liq_median_volume_20",
    "liq_avg_turnover_egp_20", "liq_median_turnover_egp_20", "liq_volume_consistency",
    "liq_turnover_consistency", "liq_volume_trend", "liq_status",
    "vol_adr_percent_20", "vol_median_range_percent_20", "vol_atr_percent_14",
    "vol_target_2pct_frequency", "vol_upside_2pct_frequency", "vol_classification",
    "er_base_expected_low", "er_base_expected_high", "er_base_expected_width_percent",
    "er_conservative_expected_low", "er_conservative_expected_high",
    "er_high_volatility_expected_low", "er_high_volatility_expected_high",
    "AvgVolumeScore", "AvgTurnoverScore", "VolatilityScore", "TargetFrequencyScore",
    "LiquidityConsistencyScore", "SpreadScore", "LiquidityGate",
    "EXPECTED_RANGE_SCALPING_SCORE", "TradableCandidate",
    "live_last", "live_spread_percent", "live_available",
    "prov_latest_completed_session", "prov_expected_latest_session",
    "prov_data_age_sessions", "prov_data_status", "prov_historical_provider",
    "prov_adjusted_status", "prov_fallback_reason",
]


def _select(frame, cols):
    present = [c for c in cols if c in frame.columns]
    return frame[present]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-live", action="store_true", help="skip the live Rubix snapshot")
    ap.add_argument("--test-sessions", type=int, default=30)
    args = ap.parse_args(argv)

    cfg = ExpectedRangeConfig.load()
    assert not cfg.production_enabled and not cfg.paper_enabled, "flags must stay disabled"

    scanner = ExpectedRangeScanner(config=cfg)
    result = scanner.scan(with_live=not args.no_live)
    universe = result["universe"]
    scenarios = result["scenarios"]

    Path("reports").mkdir(exist_ok=True)
    _select(universe, UNIVERSE_COLS).to_csv(UNIVERSE_CSV, index=False)

    top = universe[universe["TradableCandidate"]].head(40)
    _select(top, UNIVERSE_COLS).to_csv(TOP_CSV, index=False)

    scenarios.to_csv(SCENARIOS_CSV, index=False)

    rejected = universe[~universe["TradableCandidate"]]
    rej_cols = ["Rank", "Symbol", "liq_avg_volume_20", "liq_median_volume_20",
                "liq_avg_turnover_egp_20", "liq_volume_consistency", "liq_status",
                "liq_reasons", "vol_adr_percent_20", "prov_data_status", "prov_fallback_reason"]
    _select(rejected, rej_cols).to_csv(REJECTIONS_CSV, index=False)

    # Phase 14: immutable paper store — create header only (paper disabled).
    store = ImmutablePaperSignalStore(PAPER_CSV)
    if not Path(PAPER_CSV).is_file():
        store._write([])   # header-only; no signals recorded while paper is off

    # Phase 12/13: walk-forward selection validation on real daily data.
    validation = run_selection_validation(config=cfg, test_sessions=args.test_sessions)
    if not validation["groups"].empty:
        validation["groups"].to_csv(VALIDATION_CSV, index=False)
    else:
        import csv
        with open(VALIDATION_CSV, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["status", validation["status"]])

    exec_status = scenario_execution_status(
        rubix_db_path=__import__("config.settings_manager", fromlist=["settings"]).settings
        .get("market_data").get("rubix_db_path", "data/rubix_live_market.db"))

    print(json.dumps({
        "summary": result["summary"],
        "selection_validation_status": validation["status"],
        "selection_validation_test_sessions": len(validation.get("test_dates", [])),
        "scenario_execution_status": exec_status["status"],
        "scenario_execution_intraday_sessions": exec_status.get("intraday_sessions"),
        "reports": [UNIVERSE_CSV, TOP_CSV, SCENARIOS_CSV, REJECTIONS_CSV,
                    VALIDATION_CSV, PAPER_CSV],
        "flags": {"production_enabled": cfg.production_enabled,
                  "paper_enabled": cfg.paper_enabled,
                  "decision_support_only": cfg.decision_support_only},
    }, indent=2, default=str))

    if validation["groups"].empty:
        return
    print("\n=== Selection validation (walk-forward, daily data) ===")
    print(validation["groups"].to_string(index=False))
    return validation


if __name__ == "__main__":
    main()
