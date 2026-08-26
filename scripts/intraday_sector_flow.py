"""Where the rest of today's liquidity is heading, by sector.

    venv/Scripts/python.exe scripts/intraday_sector_flow.py

Blends the observed opening window with the previous daily session. Prints the
session-coverage table first, because on a desktop-hosted collector the honest
answer is often "not enough of today was observed yet".

Liquidity activity only. This does not forecast price direction.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings_manager import settings
from core.environment import load_project_environment
from decision_support.sector_analysis import load_sector_map
from sector_flow.builder import DEFAULT_DATABASE, load_saved
from sector_flow.history import complete_sessions as complete_daily_sessions
from sector_flow.intraday import (
    DEFAULT_BLEND_WEIGHT,
    MIN_OPENING_MINUTES,
    evaluate,
    forecast_rest_of_day,
    load_minute_turnover,
    session_coverage,
    weight_sweep,
)


def main():
    parser = argparse.ArgumentParser(description="Intraday sector liquidity forecast.")
    parser.add_argument("--sector-file", default="data/sectors.csv")
    parser.add_argument("--daily-database", default=DEFAULT_DATABASE)
    parser.add_argument("--rubix-database", default=None)
    parser.add_argument("--weight", type=float, default=DEFAULT_BLEND_WEIGHT)
    parser.add_argument("--session", default=None, help="Forecast this session instead of the latest.")
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()

    load_project_environment()
    rubix = args.rubix_database or settings.get("market_data").get("rubix_db_path")
    sector_map = load_sector_map(args.sector_file)
    if not sector_map:
        print(f"No sector map at {args.sector_file}. Run scripts/build_sector_map.py first.")
        return 1

    minutes = load_minute_turnover(rubix, sector_map)
    if minutes.empty:
        print(f"No intraday candles in {rubix}.")
        return 1
    daily = complete_daily_sessions(load_saved(args.daily_database))
    if daily is None or daily.empty:
        print(f"No daily sector history in {args.daily_database}. Run scripts/build_sector_flow.py.")
        return 1

    coverage = session_coverage(minutes)
    complete = int(coverage["Complete"].sum())
    print(f"intraday sessions : {len(coverage)} observed, {complete} complete")
    print(coverage.tail(8).to_string(index=False))

    reports = Path(args.report_dir)
    reports.mkdir(parents=True, exist_ok=True)
    coverage.to_csv(reports / "intraday_session_coverage.csv", index=False)

    scores = evaluate(minutes, daily, weight=args.weight)
    if not scores.empty:
        print()
        print("Predicting rest-of-day sector share, on complete sessions only:")
        print(scores.to_string(index=False))
        sweep = weight_sweep(minutes, daily)
        print()
        print("Blend weight sweep (0.0 = previous session only, 1.0 = opening only):")
        print(sweep.to_string(index=False))
        scores.to_csv(reports / "intraday_blend_scores.csv", index=False)
        sweep.to_csv(reports / "intraday_blend_weight_sweep.csv", index=False)
        if scores["sessions"].max() < 30:
            print()
            print(f"NOTE: only {scores['sessions'].max()} scored sessions. The blend is measured,")
            print("      not established. Intraday history accumulates one session at a time.")

    forecast = forecast_rest_of_day(minutes, daily, session=args.session, weight=args.weight)
    if forecast.empty:
        print()
        print(f"No forecast: fewer than {MIN_OPENING_MINUTES} opening minutes observed.")
        return 0

    session = forecast["SessionDate"].iloc[0]
    observed = forecast["OpeningMinutesObserved"].iloc[0]
    print()
    print(f"Rest-of-session forecast for {session} "
          f"({observed} opening minutes observed, weight {args.weight:.0%} opening):")
    columns = ["Sector", "PreviousShare", "OpeningShare", "Forecast", "Change"]
    print(forecast[columns].to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    forecast.to_csv(reports / "intraday_sector_forecast.csv", index=False)
    print()
    print("Liquidity activity only. This does not forecast price direction and is not advice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
