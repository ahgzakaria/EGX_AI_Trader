"""Walk-forward validation of the next-session sector liquidity forecast.

Run after scripts/build_sector_flow.py:

    venv/Scripts/python.exe scripts/validate_sector_forecast.py

Every target mode is scored against two naive baselines -- persistence
(tomorrow = today) and the 5-session mean -- and the verdict is taken against
whichever baseline is harder to beat. The point of this script is to be able to
say "no skill" out loud, so read the verdict before reading the forecast.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sector_flow.builder import DEFAULT_DATABASE, load_saved
from sector_flow.forecast import (
    DEFAULT_SPLITS,
    TARGET_MODES,
    build_dataset,
    forecast_next_session,
    walk_forward,
)
from sector_flow.history import DEFAULT_MIN_COVERAGE


REPORT_DIR = "reports"
HEADLINE = [
    "target_mode", "predictions", "sessions", "mae", "mae_persistence", "mae_mean5",
    "skill_vs_persistence", "skill_vs_mean5", "skill_vs_best_baseline",
    "rank_correlation", "rank_correlation_persistence",
    "top3_hit_rate", "top3_hit_rate_persistence",
]


def main():
    parser = argparse.ArgumentParser(description="Validate the sector liquidity forecast.")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--splits", type=int, default=DEFAULT_SPLITS)
    parser.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    parser.add_argument("--report-dir", default=REPORT_DIR)
    parser.add_argument("--since", default=None, help="Ignore sessions before this date.")
    args = parser.parse_args()

    history = load_saved(args.database)
    if history.empty:
        print(f"No sector history in {args.database}. Run scripts/build_sector_flow.py first.")
        return 1
    if "SessionCoverage" not in history.columns:
        print(f"{args.database} predates the session-coverage guard. Rebuild it first.")
        return 1

    dataset = build_dataset(history, args.min_coverage)
    if args.since:
        dataset = dataset[dataset["SessionDate"] >= pd.Timestamp(args.since)]
    if dataset.empty:
        print("No supervised rows to validate.")
        return 1

    print(f"dataset : {len(dataset)} rows, {dataset['SessionDate'].nunique()} sessions "
          f"[{dataset['SessionDate'].min().date()} .. {dataset['SessionDate'].max().date()}]")
    print()

    reports = Path(args.report_dir)
    reports.mkdir(parents=True, exist_ok=True)
    summaries = []
    for mode in TARGET_MODES:
        result = walk_forward(dataset, n_splits=args.splits, target_mode=mode)
        summaries.append(result.metrics)
        result.folds.to_csv(reports / f"sector_forecast_folds_{mode}.csv", index=False)
        result.predictions.to_csv(
            reports / f"sector_forecast_predictions_{mode}.csv", index=False
        )
        print(f"[{mode}] {result.verdict}")

    summary = pd.DataFrame(summaries)
    summary.to_csv(reports / "sector_forecast_metrics.csv", index=False)
    print()
    print(summary[[column for column in HEADLINE if column in summary]].to_string(index=False))
    print()
    print(f"reports -> {reports}/sector_forecast_*.csv")

    forecast = forecast_next_session(history, args.min_coverage)
    if forecast.empty:
        return 0
    after = forecast["SessionDate"].iloc[0].date()
    print()
    print(f"Forecast for the session after {after} "
          "(Baseline = 5-session mean, Predicted = learned model):")
    columns = ["Sector", "TurnoverShare", "Baseline", "BaselineChange", "Predicted", "Change"]
    print(forecast[columns].to_string(index=False, float_format=lambda value: f"{value:,.4f}"))
    print()
    print("Liquidity activity only. This does not forecast price direction and is not advice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
