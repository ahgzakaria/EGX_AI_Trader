"""Regression tests for explicit Full History versus validated OOS scope."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from services.backtest_service import VALIDATED_OOS, run_backtest
from strategy.trading_decision import TradingDecisionService


class _Run:
    run_id = "RUN_TEST"
    run_dir = "reports/RUN_TEST"
    metadata = {"status": "RUNNING"}

    def fail(self, _error):
        self.metadata["status"] = "FAILED"


def _result():
    return {
        "mode": TradingDecisionService.STRATEGY_ONLY,
        "trades": [], "executed": [], "failures": [],
        "signal_rejections": {}, "audits": [],
        "simulation": {
            "rejected_trades": [], "rejection_reasons": {},
            "effective_max_positions": 5, "final_cash": 100000,
        },
        "summary": {},
    }


def test_validated_scope_uses_phase5_oos_context_for_strategy_only():
    context = {
        "coverage_start": __import__("pandas").Timestamp("2020-08-06"),
        "coverage_end": __import__("pandas").Timestamp("2026-06-08"),
        "validation": SimpleNamespace(aggregate={}, folds=__import__("pandas").DataFrame()),
    }
    config = SimpleNamespace(AI_MODE=TradingDecisionService.STRATEGY_ONLY)

    # The UI-aware service emits progress before returning the exact same
    # chronological Phase 5 context to the calculation path.
    def context_updates(*_args):
        yield {"type": "stage", "phase": "Walk-Forward validation"}
        return context

    with (
        patch("services.backtest_service.settings.reload"),
        patch("services.backtest_service.load_backtest_config", return_value=config),
        patch("services.backtest_service.load_symbols", return_value=["TEST.CA"]),
        patch("services.backtest_service.ExperimentRun", return_value=_Run()),
        patch(
            "services.backtest_service._walk_forward_context_with_progress",
            side_effect=context_updates,
        ) as walk,
        patch("services.backtest_service._run_overlay_mode", return_value=_result()) as overlay,
        patch("services.backtest_service.BacktestReport"),
        patch("services.backtest_service._complete_experiment"),
    ):
        updates = list(run_backtest(scope=VALIDATED_OOS))

    walk.assert_called_once()
    assert overlay.call_args.args[1] == TradingDecisionService.STRATEGY_ONLY
    assert updates[-1]["backtest_scope"] == VALIDATED_OOS
    assert updates[-1]["comparison_start"] == "2020-08-06"
    assert updates[-1]["comparison_end"] == "2026-06-08"


def test_closing_running_generator_marks_experiment_cancelled():
    config = SimpleNamespace(AI_MODE=TradingDecisionService.STRATEGY_ONLY)

    def context_updates(*_args):
        yield {"type": "stage", "phase": "Preparing labels"}
        return None

    with (
        patch("services.backtest_service.settings.reload"),
        patch("services.backtest_service.load_backtest_config", return_value=config),
        patch("services.backtest_service.load_symbols", return_value=["TEST.CA"]),
        patch("services.backtest_service.ExperimentRun", return_value=_Run()),
        patch(
            "services.backtest_service._walk_forward_context_with_progress",
            side_effect=context_updates,
        ),
        patch("services.backtest_service.RunRepository.mark_interrupted") as mark,
    ):
        execution = run_backtest(scope=VALIDATED_OOS)
        assert next(execution)["type"] == "started"
        assert next(execution)["type"] == "stage"
        execution.close()

    mark.assert_called_once_with(
        "RUN_TEST", "Backtest cancelled from Streamlit"
    )
