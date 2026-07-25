"""Reproduce the locked Phase 5 baselines after engineering-only changes.

This validation intentionally calls the same OOS helpers used by the Phase 5
audit.  It never writes settings and never loads the global model.
"""

import json
import sys
from pathlib import Path

import pandas as pd


# Allow execution as a file while keeping all imports rooted in the project.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.config import load as load_backtest_config  # noqa: E402
from config.settings_manager import settings  # noqa: E402
from core.symbols import load_symbols  # noqa: E402
from services.backtest_service import (  # noqa: E402
    _run_overlay_mode,
    _walk_forward_context,
)
from services.experiment_tracking import ExperimentRun  # noqa: E402
from strategy.trading_decision import TradingDecisionService  # noqa: E402


def main():
    settings.reload()
    config = load_backtest_config()
    symbols = load_symbols("data/symbols.csv")
    experiment = ExperimentRun("RESEARCH", "PHASE5_CURRENT_DATA_V2", symbols)
    try:
        context = _walk_forward_context(symbols, config)
        strategy = _run_overlay_mode(
            symbols, TradingDecisionService.STRATEGY_ONLY, context, config
        )
        ranking = _run_overlay_mode(
            symbols, TradingDecisionService.AI_RANKING_ONLY, context, config
        )
        result = {
            "coverage_start": str(context["coverage_start"].date()),
            "coverage_end": str(context["coverage_end"].date()),
            "strategy_only": strategy["summary"],
            "ai_ranking_only": ranking["summary"],
        }
        experiment.save_dataframe(
            "phase5_current_data_v2.csv",
            pd.DataFrame([
                {"Mode": "STRATEGY_ONLY", **strategy["summary"]},
                {"Mode": "AI_RANKING_ONLY", **ranking["summary"]},
            ]),
        )
        experiment.complete(
            metrics=result,
            failures=context["label_failures"],
            successful_symbols=max(len(symbols) - len(context["label_failures"]), 0),
            walk_forward_status="COMPLETED",
            extra_metadata={
                "comparison_start": result["coverage_start"],
                "comparison_end": result["coverage_end"],
                "baseline_name": "PHASE5_CURRENT_DATA_V2",
            },
        )
        result["run_id"] = experiment.run_id
        print("PHASE6_REGRESSION_RESULT=" + json.dumps(result, default=str))
    except BaseException as error:
        experiment.fail(error)
        raise


if __name__ == "__main__":
    main()
