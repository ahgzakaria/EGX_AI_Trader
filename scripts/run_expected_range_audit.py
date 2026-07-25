"""Completion audit for EXPECTED_RANGE_SCALPER — generates the 5 audit reports.

Runs the isolated scan + walk-forward forecast calibration + tradability, score-
sensitivity and worked-example audits on real data. Never enables paper/production,
never changes weights/targets/stops, never touches another strategy.

    python scripts/run_expected_range_audit.py [--test-sessions N] [--no-live]
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

from scalping_expected_range.audit import (  # noqa: E402
    build_score_sensitivity,
    build_tradability_audit,
    build_worked_examples,
)
from scalping_expected_range.calibration import run_forecast_calibration  # noqa: E402
from scalping_expected_range.config import ExpectedRangeConfig  # noqa: E402
from scalping_expected_range.scanner import ExpectedRangeScanner  # noqa: E402

CALIB_CSV = "reports/expected_range_forecast_calibration.csv"
REGIME_CSV = "reports/expected_range_forecast_by_regime.csv"
TRADABILITY_CSV = "reports/expected_range_volume_tradability_audit.csv"
SENSITIVITY_CSV = "reports/expected_range_score_sensitivity.csv"
WORKED_CSV = "reports/expected_range_worked_examples.csv"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-sessions", type=int, default=40)
    ap.add_argument("--no-live", action="store_true")
    args = ap.parse_args(argv)

    cfg = ExpectedRangeConfig.load()
    assert not cfg.production_enabled and not cfg.paper_enabled, "flags must stay disabled"
    Path("reports").mkdir(exist_ok=True)

    scanner = ExpectedRangeScanner(config=cfg)
    scan = scanner.scan(with_live=not args.no_live)
    universe = scan["universe"]

    # Phase 4/5/6
    build_tradability_audit(universe).to_csv(TRADABILITY_CSV, index=False)
    build_score_sensitivity(universe, cfg).to_csv(SENSITIVITY_CSV, index=False)
    build_worked_examples(universe).to_csv(WORKED_CSV, index=False)

    # Phase 2/3
    calib = run_forecast_calibration(config=cfg, test_sessions=args.test_sessions)
    band = calib["band_summary"]
    room = calib["target_room"]
    # merge target-room into calibration report as a separate section-tagged frame
    import pandas as pd
    band_out = band.copy(); band_out.insert(0, "Section", "BAND_COVERAGE")
    room_out = room.copy(); room_out.insert(0, "Section", "TARGET_ROOM")
    pd.concat([band_out, room_out], ignore_index=True).to_csv(CALIB_CSV, index=False)
    calib["regime_summary"].to_csv(REGIME_CSV, index=False)

    out = {
        "reports": [CALIB_CSV, REGIME_CSV, TRADABILITY_CSV, SENSITIVITY_CSV, WORKED_CSV],
        "calibration_observations": int(len(calib["observations"])),
        "band_coverage": band.to_dict("records"),
        "target_room": room.to_dict("records"),
        "flags": {"production_enabled": cfg.production_enabled,
                  "paper_enabled": cfg.paper_enabled,
                  "decision_support_only": cfg.decision_support_only},
    }
    print(json.dumps(out, indent=2, default=str))
    print("\n=== SCORE SENSITIVITY ===")
    print(build_score_sensitivity(universe, cfg).to_string(index=False))
    print("\n=== WORKED EXAMPLES ===")
    we = build_worked_examples(universe)
    print(we[["ExampleRole", "Symbol", "liq_avg_volume_20", "liq_avg_turnover_egp_20",
              "liq_avg_traded_price", "vol_adr_percent_20", "liq_status",
              "EXPECTED_RANGE_SCALPING_SCORE", "TradableCandidate"]].to_string(index=False))
    return out


if __name__ == "__main__":
    main()
