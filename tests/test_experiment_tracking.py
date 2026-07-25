"""Phase 6 tests for immutable and backward-compatible experiment records."""

import json
from pathlib import Path

import pandas as pd
import pytest

import services.experiment_tracking as tracking


@pytest.fixture
def isolated_reports(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    monkeypatch.setattr(tracking, "REPORTS_ROOT", reports)
    return reports


def test_run_ids_are_unique_and_snapshots_are_never_overwritten(isolated_reports):
    first = tracking.ExperimentRun("SCAN", "LIVE_SCAN", ["A.CA"])
    second = tracking.ExperimentRun("SCAN", "LIVE_SCAN", ["A.CA"])

    assert first.run_id != second.run_id
    for run in (first, second):
        assert (run.run_dir / "settings_snapshot.json").exists()
        assert (run.run_dir / "environment.json").exists()
        assert (run.run_dir / "model_info.json").exists()
        assert (run.run_dir / "run_metadata.json").exists()

    first.save_dataframe("summary.csv", pd.DataFrame([{"Return": 1}]))
    with pytest.raises(FileExistsError):
        first.save_dataframe("summary.csv", pd.DataFrame([{"Return": 2}]))


def test_complete_captures_legacy_reports_without_removing_them(isolated_reports):
    isolated_reports.mkdir(parents=True)
    legacy = isolated_reports / "legacy_report.csv"
    legacy.write_text("value\nold\n", encoding="utf-8")
    run = tracking.ExperimentRun("BACKTEST", "STRATEGY_ONLY", ["A.CA"])

    legacy.write_text("value\nnew\n", encoding="utf-8")
    run.complete(
        metrics={"TotalReturn": 65.81},
        successful_symbols=1,
        walk_forward_status="NOT_REQUIRED",
    )

    assert legacy.exists()  # Backward-compatible root report remains active.
    assert (run.run_dir / "legacy_report.csv").read_text(encoding="utf-8") == "value\nnew\n"
    metadata = json.loads((run.run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "COMPLETED"
    assert metadata["metrics"]["TotalReturn"] == 65.81
    assert (run.run_dir / "README.md").exists()


def test_export_compare_and_safe_delete(isolated_reports):
    runs = []
    for value in (10.0, 20.0):
        run = tracking.ExperimentRun("BACKTEST", "STRATEGY_ONLY", ["A.CA"])
        run.save_dataframe("summary.csv", pd.DataFrame([{"TotalReturn": value}]))
        run.save_dataframe("trade_log.csv", pd.DataFrame([{
            "exit_date": "2025-01-02", "portfolio_profit": value,
        }]))
        run.save_dataframe("equity_curve.csv", pd.DataFrame([{
            "Date": "2025-01-02", "Equity": 100000 + value,
        }]))
        run.complete(metrics={
            "TotalReturn": value, "MaxDrawdown": 1, "ProfitFactor": 2,
            "SharpeRatio": 1, "SortinoRatio": 1, "CalmarRatio": 1,
            "Trades": 1, "WinRate": 100, "RejectedTrades": 0,
            "AIAcceptanceRate": 100,
        }, successful_symbols=1)
        runs.append(run)

    comparison = tracking.RunRepository.compare(runs[0].run_id, runs[1].run_id)
    assert len(comparison["metrics"]) == 10
    assert not comparison["monthly"].empty
    archive = tracking.RunRepository.export_zip(runs[0].run_id)
    assert archive.startswith(b"PK")

    with pytest.raises(ValueError):
        tracking.RunRepository.delete("../reports")
    tracking.RunRepository.delete(runs[0].run_id)
    assert not runs[0].run_dir.exists()
    assert runs[1].run_dir.exists()


def test_failed_run_retains_provenance(isolated_reports):
    run = tracking.ExperimentRun("BACKTEST", "AI_RANKING_ONLY", [])
    run.fail(RuntimeError("validation stopped"))
    metadata = tracking.RunRepository.get(run.run_id)
    assert metadata["status"] == "FAILED"
    assert metadata["error"] == "validation stopped"
