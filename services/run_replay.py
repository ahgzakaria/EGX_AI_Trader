"""Deterministic backtest replay using only an archived run dataset."""

from __future__ import annotations

from copy import deepcopy
import gzip
import json
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

from backtesting.config import load as load_backtest_config
from config.settings_manager import settings
from core.data_provider import reset_provider_instances
from services.dataset_archive import load_archived_frames, replay_dataset
from services.experiment_tracking import ExperimentRun, RunRepository
from services.backtest_service import (
    FULL_HISTORY,
    VALIDATED_OOS,
    _complete_experiment,
    _decision_service,
    _portfolio_result,
    _run_overlay_mode,
    _run_pass,
    _walk_forward_context,
)
from strategy.trading_decision import TradingDecisionService


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _symbols_from_archive(run_dir: Path, frames: dict[str, pd.DataFrame]) -> list[str]:
    path = run_dir / "dataset" / "symbols_used.csv"
    if not path.is_file():
        raise FileNotFoundError("Archived symbols_used.csv is required for replay")
    symbols = pd.read_csv(path).get("Symbol", pd.Series(dtype=str)).dropna().astype(str).tolist()
    failure_path = run_dir / "dataset" / "failed_symbols.csv"
    declared_failures = set()
    if failure_path.is_file():
        try:
            failures = pd.read_csv(failure_path)
            column = "Symbol" if "Symbol" in failures else "symbol" if "symbol" in failures else None
            if column:
                declared_failures = set(failures[column].dropna().astype(str))
        except pd.errors.EmptyDataError:
            pass
    unexplained = [
        symbol for symbol in symbols
        if symbol not in frames and symbol not in declared_failures
    ]
    if unexplained:
        raise RuntimeError(
            f"Replay archive is incomplete; {len(unexplained)} missing symbols have no recorded failure: {unexplained[:5]}"
        )
    return [symbol for symbol in symbols if symbol in frames]


def _metric_diff(expected: dict, actual: dict) -> dict:
    differences = {}
    keys = sorted(set(expected).intersection(actual))
    for key in keys:
        before, after = expected[key], actual[key]
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            if abs(float(before) - float(after)) > 1e-9:
                differences[key] = {"expected": before, "actual": after}
        elif before != after:
            differences[key] = {"expected": before, "actual": after}
    return differences


def _validate_prediction_snapshot(source_dir: Path, validation) -> dict:
    info = _read_json(source_dir / "dataset" / "MANIFEST.json").get("ai_predictions")
    if not info:
        return {"required": False, "match": None, "reason": "strategy-only run"}
    source = source_dir / "dataset" / info["path"]
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        archived = pd.read_csv(handle, index_col=0)
    regenerated = validation.predictions.copy()
    # CSV round-tripping can alter dtype labels, so compare canonical string
    # values and shape rather than model objects or in-memory dtype metadata.
    left = archived.reset_index(drop=True)
    right = regenerated.reset_index(drop=True)
    common = [column for column in left.columns if column in right.columns]
    match = bool(common) and len(left) == len(right)
    for column in common:
        if not match:
            break
        if any(token in column.lower() for token in ("date", "start", "end")):
            first = pd.to_datetime(left[column], errors="coerce")
            second = pd.to_datetime(right[column], errors="coerce")
            match = first.equals(second)
            continue
        first_number = pd.to_numeric(left[column], errors="coerce")
        second_number = pd.to_numeric(right[column], errors="coerce")
        if first_number.notna().sum() == len(left) and second_number.notna().sum() == len(right):
            match = bool(np.allclose(first_number, second_number, rtol=0, atol=1e-12))
        else:
            match = left[column].fillna("").astype(str).equals(
                right[column].fillna("").astype(str)
            )
    return {
        "required": True,
        "match": match,
        "archived_rows": len(archived),
        "regenerated_rows": len(regenerated),
        "columns_compared": common,
    }


def replay_run(run_id: str) -> dict[str, Any]:
    """Replay a completed backtest without provider or network access."""

    source_dir = RunRepository._safe_run_dir(run_id)
    metadata = RunRepository.get(run_id)
    if metadata.get("status") != "COMPLETED":
        raise RuntimeError("Only completed runs can be replayed")
    research_baseline = (
        metadata.get("run_type") == "RESEARCH"
        and metadata.get("mode") == "PHASE5_CURRENT_DATA_V2"
    )
    if metadata.get("run_type") not in {"BACKTEST", "REPLAY"} and not research_baseline:
        raise RuntimeError("Replay currently supports archived backtests only")
    frames, dataset_manifest = load_archived_frames(source_dir)
    snapshot = _read_json(source_dir / "settings_snapshot.json")
    symbols = _symbols_from_archive(source_dir, frames)
    mode = str(metadata.get("mode") or snapshot.get("backtest", {}).get("ai_mode"))
    scope = metadata.get("backtest_scope") or (
        FULL_HISTORY if mode == TradingDecisionService.STRATEGY_ONLY else VALIDATED_OOS
    )

    original_settings = deepcopy(settings.data)
    experiment = None
    try:
        settings.data = deepcopy(snapshot)
        reset_provider_instances()
        cfg = load_backtest_config()
        experiment = ExperimentRun("REPLAY", mode, symbols, settings_snapshot=snapshot)
        with replay_dataset(frames):
            context = None
            if research_baseline:
                context = _walk_forward_context(symbols, cfg)
                strategy_result = _run_overlay_mode(
                    symbols, TradingDecisionService.STRATEGY_ONLY, context, cfg
                )
                ranking_result = _run_overlay_mode(
                    symbols, TradingDecisionService.AI_RANKING_ONLY, context, cfg
                )
                result = None
            elif mode == TradingDecisionService.STRATEGY_ONLY and scope == FULL_HISTORY:
                trades, failures, rejections, audits = _run_pass(
                    symbols, _decision_service(mode)
                )
                result = _portfolio_result(trades, cfg, mode)
                result.update({
                    "failures": failures,
                    "signal_rejections": rejections,
                    "audits": audits,
                })
            else:
                context = _walk_forward_context(symbols, cfg)
                result = _run_overlay_mode(symbols, mode, context, cfg)

        prediction_check = (
            _validate_prediction_snapshot(source_dir, context["validation"])
            if context else {"required": False, "match": None}
        )
        expected = metadata.get("metrics", {})
        if research_baseline:
            actual = {
                "coverage_start": str(context["coverage_start"].date()),
                "coverage_end": str(context["coverage_end"].date()),
                "strategy_only": strategy_result["summary"],
                "ai_ranking_only": ranking_result["summary"],
            }
        else:
            actual = dict(result["summary"])
            actual.update({
                "Signals": len(result["trades"]),
                "RejectedTrades": len(result["simulation"]["rejected_trades"]),
                "AIAcceptanceRate": round(
                    len(result["executed"]) / len(result["trades"]) * 100, 2
                ) if result["trades"] else 0,
            })
        differences = _metric_diff(expected, actual)
        if research_baseline:
            experiment.save_dataframe(
                "phase5_current_data_v2.csv",
                pd.DataFrame([
                    {"Mode": "STRATEGY_ONLY", **strategy_result["summary"]},
                    {"Mode": "AI_RANKING_ONLY", **ranking_result["summary"]},
                ]),
            )
            experiment.complete(
                metrics=actual,
                failures=context["label_failures"],
                successful_symbols=len(symbols),
                walk_forward_status="COMPLETED",
                extra_metadata={
                    "replay_of": run_id,
                    "comparison_start": actual["coverage_start"],
                    "comparison_end": actual["coverage_end"],
                    "baseline_name": "PHASE5_CURRENT_DATA_V2_REPLAY",
                },
            )
        else:
            _complete_experiment(experiment, result, symbols, context, scope=scope)
        # Replay linkage is written as a separate annotation so the sealed run
        # remains immutable after completion.
        note = {
            "replay_of": run_id,
            "source_dataset_hash": dataset_manifest.get("dataset_hash"),
            "metric_match": not differences,
            "metric_differences": differences,
            "prediction_check": prediction_check,
        }
        (experiment.run_dir / "notes.json").write_text(
            json.dumps(note, indent=2, default=str), encoding="utf-8"
        )
        if differences:
            raise RuntimeError(f"Replay metrics differ from source run: {differences}")
        if prediction_check.get("required") and prediction_check.get("match") is False:
            raise RuntimeError("Replayed Walk-Forward predictions differ from archived predictions")
        return {
            "source_run_id": run_id,
            "replay_run_id": experiment.run_id,
            "dataset_hash": dataset_manifest.get("dataset_hash"),
            "metrics_match": True,
            "predictions_match": prediction_check.get("match"),
            "metrics": actual,
        }
    except BaseException as error:
        if experiment is not None and experiment.metadata.get("status") == "RUNNING":
            experiment.fail(error)
        raise
    finally:
        settings.data = original_settings
        reset_provider_instances()
