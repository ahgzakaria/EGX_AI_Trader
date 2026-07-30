"""Historical backtest orchestration with leakage-safe AI overlay support."""

import time
from datetime import timedelta

import pandas as pd

from ai.dataset import DatasetBuilder
from ai.walk_forward import WalkForwardValidator
from backtesting.config import load as load_backtest_config
from backtesting.engine import BacktestEngine
from backtesting.report import BacktestReport
from backtesting.statistics import BacktestStatistics
from config.settings_manager import settings
from core.symbols import SYMBOL_SOURCE, load_symbols
from portfolio.portfolio_simulator import PortfolioSimulator
from services.risk_overlay_reporting import create_risk_overlay_reports
from services.experiment_tracking import ExperimentRun, RunRepository
from strategy.trading_decision import REJECTION_REASONS, TradingDecisionService


OVERLAY_MODES = (
    TradingDecisionService.STRATEGY_ONLY,
    TradingDecisionService.AI_HARD_FILTER,
    TradingDecisionService.AI_POSITION_SIZING,
    TradingDecisionService.AI_RANKING_ONLY,
    TradingDecisionService.AI_HYBRID,
)

FULL_HISTORY = "FULL_HISTORY"
VALIDATED_OOS = "VALIDATED_PHASE5_OOS"


def _empty_rejections():
    return {reason: 0 for reason in REJECTION_REASONS}


def _portfolio_result(trades, cfg, mode):
    trades = sorted(trades, key=lambda trade: trade.entry_date)
    simulation = PortfolioSimulator(
        trades,
        initial_capital=cfg.INITIAL_CAPITAL,
        risk_percent=cfg.RISK_PERCENT,
        allow_overlapping_trades=cfg.ALLOW_OVERLAPPING_TRADES,
        max_open_positions=cfg.MAX_OPEN_POSITIONS,
        max_portfolio_risk_percent=cfg.MAX_PORTFOLIO_RISK_PERCENT,
    ).run()
    executed = simulation["executed_trades"]
    return {
        "mode": mode,
        "trades": trades,
        "executed": executed,
        "simulation": simulation,
        "summary": BacktestStatistics(
            executed, initial_capital=cfg.INITIAL_CAPITAL
        ).summary(),
    }


def _run_pass(
    symbols, decision_service, start_date=None, end_date=None,
    execution_delay_bars=0,
):
    trades, failures, audits, rejections = [], [], [], _empty_rejections()
    for symbol in symbols:
        try:
            engine = BacktestEngine(
                symbol,
                decision_service=decision_service,
                execution_delay_bars=execution_delay_bars,
            )
            trades.extend(engine.run(start_date=start_date, end_date=end_date))
            audits.extend(engine.signal_audits)
            for reason, count in engine.signal_rejections.items():
                rejections[reason] += count
        except Exception as error:
            failures.append({"Symbol": symbol, "Error": str(error)})
    return trades, failures, rejections, audits


def _run_pass_with_progress(
    symbols, decision_service, phase, start_date=None, end_date=None,
    execution_delay_bars=0,
):
    """Yield UI progress while preserving the same deterministic pass logic."""
    trades, failures, audits, rejections = [], [], [], _empty_rejections()
    total = len(symbols)
    for current, symbol in enumerate(symbols, 1):
        try:
            engine = BacktestEngine(
                symbol,
                decision_service=decision_service,
                execution_delay_bars=execution_delay_bars,
            )
            trades.extend(engine.run(start_date=start_date, end_date=end_date))
            audits.extend(engine.signal_audits)
            for reason, count in engine.signal_rejections.items():
                rejections[reason] += count
        except Exception as error:
            failures.append({"Symbol": symbol, "Error": str(error)})
        yield {
            "type": "progress",
            "phase": phase,
            "current": current,
            "total": total,
            "symbol": symbol,
            "percent": round(current / total * 100, 1) if total else 100,
        }
    return trades, failures, rejections, audits


def _walk_forward_context(symbols, cfg):
    """Build labels and chronological models without ever loading global AI."""
    label_service = TradingDecisionService(mode=TradingDecisionService.STRATEGY_ONLY)
    label_trades, failures, rejections, audits = _run_pass(symbols, label_service)
    return _build_walk_forward_context(
        label_trades, failures, rejections, audits, cfg
    )


def _walk_forward_context_with_progress(symbols, cfg):
    """Build the identical context while emitting UI-only progress events."""
    label_service = TradingDecisionService(mode=TradingDecisionService.STRATEGY_ONLY)
    label_trades, failures, rejections, audits = yield from _run_pass_with_progress(
        symbols, label_service, "Preparing chronological AI labels"
    )
    yield {
        "type": "stage",
        "phase": "Walk-Forward validation",
        "message": "Training chronological folds using only past data...",
    }
    return _build_walk_forward_context(
        label_trades, failures, rejections, audits, cfg
    )


def _build_walk_forward_context(label_trades, failures, rejections, audits, cfg):
    """Single calculation path shared by silent and progress-aware callers."""
    samples = DatasetBuilder.from_trades(label_trades)
    validation = WalkForwardValidator(n_splits=cfg.WALK_FORWARD_SPLITS).validate(samples)
    evaluated_folds = validation.folds[validation.folds["status"] == "evaluated"]
    if evaluated_folds.empty:
        raise RuntimeError("Walk-forward validation produced no evaluable folds")
    return {
        "validation": validation,
        "coverage_start": pd.Timestamp(evaluated_folds["test_start"].min()),
        "coverage_end": pd.Timestamp(evaluated_folds["test_end"].max()),
        "label_failures": failures,
        "label_rejections": rejections,
        "label_audits": audits,
    }


def _decision_service(mode, historical_filter=None):
    if mode == TradingDecisionService.STRATEGY_ONLY:
        return TradingDecisionService(mode=mode)
    return TradingDecisionService(
        mode=mode,
        historical_ai_filter=historical_filter,
        strict_historical_predictions=True,
    )


def _run_overlay_mode(symbols, mode, context, cfg, execution_delay_bars=0):
    service = _decision_service(mode, context["validation"].historical_filter)
    trades, failures, rejections, audits = _run_pass(
        symbols,
        service,
        context["coverage_start"],
        context["coverage_end"],
        execution_delay_bars=execution_delay_bars,
    )
    missing = [
        row for row in failures if "prediction" in row["Error"].lower()
    ]
    if missing:
        raise RuntimeError(
            f"Walk-forward prediction coverage failed for {mode}: {missing[:3]}"
        )
    result = _portfolio_result(trades, cfg, mode)
    result["failures"] = failures
    result["signal_rejections"] = rejections
    result["audits"] = audits
    return result


def _finished_payload(result, symbols, elapsed_seconds, context=None):
    executed = result.get("executed", [])
    entries = [str(trade.entry_date) for trade in executed if trade.entry_date]
    exits = [str(trade.exit_date) for trade in executed if trade.exit_date]
    return {
        "type": "finished",
        "mode": result["mode"],
        "summary": result["summary"],
        "symbols": len(symbols),
        "signals": len(result["trades"]),
        "trades": len(result["executed"]),
        "rejected": len(result["simulation"]["rejected_trades"]),
        "signal_rejections": result.get("signal_rejections", _empty_rejections()),
        "portfolio_rejections": result["simulation"]["rejection_reasons"],
        "effective_max_positions": result["simulation"]["effective_max_positions"],
        "final_cash": result["simulation"]["final_cash"],
        "errors": result.get("failures", []),
        "elapsed": str(timedelta(seconds=elapsed_seconds)),
        "elapsed_seconds": elapsed_seconds,
        "walk_forward": context["validation"].aggregate if context else None,
        "walk_forward_folds": (
            context["validation"].folds.to_dict("records") if context else []
        ),
        "comparison_start": (
            context["coverage_start"].date().isoformat() if context else None
        ),
        "comparison_end": (
            context["coverage_end"].date().isoformat() if context else None
        ),
        "actual_start": min(entries) if entries else None,
        "actual_end": max(exits) if exits else None,
    }


def _complete_experiment(run, result, symbols, context=None, scope=FULL_HISTORY):
    """Persist run provenance after calculations, without altering results."""
    run.save_backtest_artifacts(result)
    failures = result.get("failures", [])
    run.save_records("failed_symbols.csv", failures)
    summary = dict(result["summary"])
    summary.update({
        "Signals": len(result["trades"]),
        "RejectedTrades": len(result["simulation"]["rejected_trades"]),
        "AIAcceptanceRate": round(
            len(result["executed"]) / len(result["trades"]) * 100, 2
        ) if result["trades"] else 0,
    })
    run.complete(
        metrics=summary,
        failures=failures,
        successful_symbols=max(len(symbols) - len(failures), 0),
        walk_forward_status="COMPLETED" if context else "NOT_REQUIRED",
        extra_metadata={
            "walk_forward_metrics": context["validation"].aggregate if context else None,
            "comparison_start": str(context["coverage_start"].date()) if context else None,
            "comparison_end": str(context["coverage_end"].date()) if context else None,
            "backtest_scope": scope,
        },
    )


def run_backtest(scope=FULL_HISTORY):
    """Run the configured single historical mode; Strategy Only is the default."""
    start_time = time.time()
    settings.reload()
    cfg = load_backtest_config()
    symbols = load_symbols(SYMBOL_SOURCE)
    mode = cfg.AI_MODE
    if scope not in {FULL_HISTORY, VALIDATED_OOS}:
        raise ValueError(f"Unsupported backtest scope: {scope}")

    if mode == TradingDecisionService.STRATEGY_ONLY:
        # Experiment tracking is a passive wrapper: the run object receives
        # only completed results and cannot influence trading calculations.
        experiment = ExperimentRun("BACKTEST", mode, symbols)
        try:
            yield {
                "type": "started", "run_id": experiment.run_id,
                "run_directory": str(experiment.run_dir), "mode": mode,
                "backtest_scope": scope,
            }
            if scope == VALIDATED_OOS:
                # This is the exact Phase 5 comparison contract: first build
                # chronological folds, then run Strategy Only on identical OOS
                # coverage with a fresh portfolio. AI never affects decisions.
                context = yield from _walk_forward_context_with_progress(symbols, cfg)
                yield {
                    "type": "stage", "phase": "Validated OOS backtest",
                    "message": (
                        f"Running frozen Strategy Only from "
                        f"{context['coverage_start'].date()} to "
                        f"{context['coverage_end'].date()}..."
                    ),
                }
                result = _run_overlay_mode(symbols, mode, context, cfg)
                yield {
                    "type": "stage", "phase": "Saving experiment",
                    "message": "Writing immutable reports and final metrics...",
                }
                BacktestReport(result["executed"]).save_all()
                _complete_experiment(
                    experiment, result, symbols, context, scope=scope
                )
                payload = _finished_payload(
                    result, symbols, round(time.time() - start_time, 2), context
                )
                payload.update({
                    "run_id": experiment.run_id,
                    "run_directory": str(experiment.run_dir),
                    "backtest_scope": scope,
                })
                yield payload
                return

            trades, failures, rejections, audits = yield from _run_pass_with_progress(
                symbols,
                _decision_service(mode),
                "Strategy Only",
            )
            result = _portfolio_result(trades, cfg, mode)
            result.update({
                "failures": failures,
                "signal_rejections": rejections,
                "audits": audits,
            })
            yield {
                "type": "stage", "phase": "Saving experiment",
                "message": "Writing immutable reports and final metrics...",
            }
            BacktestReport(result["executed"]).save_all()
            _complete_experiment(
                experiment, result, symbols, scope=FULL_HISTORY
            )
            payload = _finished_payload(
                result, symbols, round(time.time() - start_time, 2)
            )
            payload.update({
                "run_id": experiment.run_id,
                "run_directory": str(experiment.run_dir),
                "backtest_scope": FULL_HISTORY,
            })
            yield payload
        except BaseException as error:
            # Cancellation and real failures are terminal but distinct in Run
            # History, and neither may leave an ambiguous RUNNING record.
            if experiment.metadata.get("status") != "COMPLETED":
                if isinstance(error, GeneratorExit) or not str(error):
                    if hasattr(experiment, "cancel"):
                        experiment.cancel("Backtest cancelled from Streamlit")
                    else:  # Backward-compatible test/service doubles.
                        RunRepository.mark_interrupted(
                            experiment.run_id, "Backtest cancelled from Streamlit"
                        )
                else:
                    experiment.fail(error)
            raise
        return

    # Historical AI variants are strictly Walk-Forward and never use a model
    # stored on disk. WALK_FORWARD_AI remains the old hard-filter alias.
    if mode == TradingDecisionService.WALK_FORWARD_AI:
        mode = TradingDecisionService.AI_HARD_FILTER
    if mode not in OVERLAY_MODES:
        raise ValueError(f"Unsupported historical AI mode: {cfg.AI_MODE}")
    if not settings.get("ai").get("enabled", False):
        raise RuntimeError("Historical AI mode requires ai.enabled=true")

    experiment = ExperimentRun("BACKTEST", mode, symbols)
    try:
        yield {
            "type": "started", "run_id": experiment.run_id,
            "run_directory": str(experiment.run_dir), "mode": mode,
            "backtest_scope": VALIDATED_OOS,
        }
        context = yield from _walk_forward_context_with_progress(symbols, cfg)
        yield {
            "type": "stage", "phase": f"{mode} OOS backtest",
            "message": (
                f"Applying fold-local predictions from "
                f"{context['coverage_start'].date()} to "
                f"{context['coverage_end'].date()}..."
            ),
        }
        result = _run_overlay_mode(symbols, mode, context, cfg)
        yield {
            "type": "stage", "phase": "Saving experiment",
            "message": "Writing immutable reports and final metrics...",
        }
        BacktestReport(result["executed"]).save_all()
        _complete_experiment(
            experiment, result, symbols, context, scope=VALIDATED_OOS
        )
        payload = _finished_payload(
            result, symbols, round(time.time() - start_time, 2), context
        )
        payload.update({
            "run_id": experiment.run_id,
            "run_directory": str(experiment.run_dir),
            "backtest_scope": VALIDATED_OOS,
        })
        yield payload
    except BaseException as error:
        if experiment.metadata.get("status") != "COMPLETED":
            if isinstance(error, GeneratorExit) or not str(error):
                if hasattr(experiment, "cancel"):
                    experiment.cancel("Backtest cancelled from Streamlit")
                else:
                    RunRepository.mark_interrupted(
                        experiment.run_id, "Backtest cancelled from Streamlit"
                    )
            else:
                experiment.fail(error)
        raise


def run_ai_risk_overlay_comparison():
    """Compare all Phase 4 modes on one OOS period and one configuration."""
    start_time = time.time()
    settings.reload()
    cfg = load_backtest_config()
    if not settings.get("ai").get("enabled", False):
        raise RuntimeError("AI risk-overlay comparison requires ai.enabled=true")

    symbols = load_symbols(SYMBOL_SOURCE)
    experiment = ExperimentRun(
        "BACKTEST", "AI_RISK_OVERLAY_COMPARISON", symbols
    )
    context = _walk_forward_context(symbols, cfg)
    results = {
        mode: _run_overlay_mode(symbols, mode, context, cfg)
        for mode in OVERLAY_MODES
    }
    comparison = create_risk_overlay_reports(
        results,
        context["coverage_start"],
        context["coverage_end"],
    )
    elapsed_seconds = round(time.time() - start_time, 2)
    # Comparison writers keep their legacy outputs; the tracker archives every
    # changed report inside the immutable Run folder.
    experiment.complete(
        metrics={
            mode: result["summary"] for mode, result in results.items()
        },
        failures=context["label_failures"],
        successful_symbols=max(len(symbols) - len(context["label_failures"]), 0),
        walk_forward_status="COMPLETED",
        extra_metadata={
            "comparison_start": str(context["coverage_start"].date()),
            "comparison_end": str(context["coverage_end"].date()),
            "walk_forward_metrics": context["validation"].aggregate,
        },
    )
    yield {
        "type": "finished",
        "mode": "AI_RISK_OVERLAY_COMPARISON",
        "modes": list(OVERLAY_MODES),
        "results": results,
        "comparison": comparison,
        "symbols": len(symbols),
        "elapsed": str(timedelta(seconds=elapsed_seconds)),
        "elapsed_seconds": elapsed_seconds,
        "walk_forward": context["validation"].aggregate,
        "walk_forward_folds": context["validation"].folds.to_dict("records"),
        "comparison_start": context["coverage_start"].date().isoformat(),
        "comparison_end": context["coverage_end"].date().isoformat(),
        "run_id": experiment.run_id,
        "run_directory": str(experiment.run_dir),
    }
