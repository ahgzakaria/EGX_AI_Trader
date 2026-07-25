"""Phase 7 regression tests for chronology, immutability, and restart safety."""

import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd
import pytest

import forward_testing.reporting as reporting
import forward_testing.service as service_module
from forward_testing.database import ForwardDatabase
from forward_testing.service import ForwardTestingService, settings_hash


def _frame(rows):
    dates = pd.date_range("2025-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=dates)


def _signal(frame, signal="BUY"):
    return {
        "Ticker": "TEST.CA", "Signal": signal, "Price": 100.0,
        "Score": 75, "Confidence": 80, "RR": 2.0,
        "AIProbability": 66.0, "Regime": "BULL", "Rank": 1,
        "Reasons": "Frozen strategy decision", "BuyLow": 99.0,
        "BuyHigh": 101.0, "StopLoss": 95.0, "Target1": 110.0,
        "Target2": 120.0, "AIFeatures": frame.iloc[-1], "Data": frame,
    }


@pytest.fixture
def service(tmp_path):
    return ForwardTestingService(ForwardDatabase(tmp_path / "forward.db"))


def test_signal_uuid_deduplication_and_immutability(service):
    frame = _frame([{"Open": 100, "High": 102, "Low": 99, "Close": 100, "Volume": 1000}])
    timestamp = datetime(2025, 1, 1, 15, 0, tzinfo=timezone.utc)
    assert service.record_signals([_signal(frame)], "RUN_ONE", settings_hash(), timestamp) == 1
    assert service.record_signals([_signal(frame)], "RUN_TWO", settings_hash(), timestamp) == 0

    signals = service.database.rows("SELECT * FROM signals")
    assert len(signals) == 1
    assert signals[0]["signal_id"]
    assert json.loads(signals[0]["indicators_json"])["Close"] == 100
    with pytest.raises(sqlite3.IntegrityError):
        with service.database.transaction() as connection:
            connection.execute("UPDATE signals SET score=1")
    with pytest.raises(sqlite3.IntegrityError):
        with service.database.transaction() as connection:
            connection.execute("DELETE FROM signals")


def test_evaluations_use_only_strictly_future_candles(service):
    signal_frame = _frame([{"Open": 100, "High": 102, "Low": 99, "Close": 100, "Volume": 1000}])
    timestamp = datetime(2025, 1, 1, 15, 0, tzinfo=timezone.utc)
    service.record_signals([_signal(signal_frame)], "RUN_ONE", settings_hash(), timestamp)

    # The signal candle has an artificial target hit; it must never count.
    rows = [{"Open": 100, "High": 999, "Low": 1, "Close": 100, "Volume": 1000}]
    rows.extend(
        {"Open": 100, "High": 105, "Low": 98, "Close": 101 + index / 10, "Volume": 1000}
        for index in range(20)
    )
    full_frame = _frame(rows)
    assert service.evaluate_pending({"TEST.CA": full_frame}, timestamp) == 5
    evaluations = service.database.rows(
        "SELECT * FROM signal_evaluations ORDER BY horizon_days"
    )
    assert [row["horizon_days"] for row in evaluations] == [1, 3, 5, 10, 20]
    assert all(row["hit_tp"] == 0 and row["hit_sl"] == 0 for row in evaluations)
    assert evaluations[-1]["status"] == "EXPIRED"
    assert all(row["as_of_date"] > "2025-01-01" for row in evaluations)
    assert service.evaluate_pending({"TEST.CA": full_frame}, timestamp) == 0


def test_conservative_stop_precedes_target_in_same_future_candle(service):
    initial = _frame([{"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000}])
    timestamp = datetime(2025, 1, 1, 15, 0, tzinfo=timezone.utc)
    service.record_signals([_signal(initial)], "RUN_ONE", settings_hash(), timestamp)
    both = _frame([
        {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
        {"Open": 100, "High": 111, "Low": 94, "Close": 103, "Volume": 1000},
    ])
    service.evaluate_pending({"TEST.CA": both}, timestamp)
    evaluation = service.database.row("SELECT * FROM signal_evaluations")
    assert evaluation["status"] == "HIT_SL"
    assert evaluation["hit_sl"] == 1
    assert evaluation["hit_tp"] == 0


def test_paper_position_uses_existing_position_sizing(service):
    initial = _frame([{"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000}])
    timestamp = datetime(2025, 1, 1, 15, 0, tzinfo=timezone.utc)
    service.record_signals([_signal(initial)], "RUN_ONE", settings_hash(), timestamp)
    future = _frame([
        {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
        {"Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 1000},
    ])
    assert service.update_paper_portfolio({"TEST.CA": future}, timestamp) == 1
    position = service.database.row("SELECT * FROM paper_positions")
    assert position["status"] == "OPEN"
    assert position["entry_date"] == "2025-01-02"
    assert position["shares"] > 0
    assert position["risk_amount"] > 0


def test_session_resume_reports_and_phase6_run_link(service, tmp_path, monkeypatch):
    monkeypatch.setattr(reporting, "REPORT_ROOT", tmp_path / "reports")
    run_one = tmp_path / "RUN_ONE"
    run_two = tmp_path / "RUN_TWO"
    run_one.mkdir()
    run_two.mkdir()
    frame = _frame([{"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000}])
    timestamp = datetime(2025, 1, 1, 15, 0, tzinfo=timezone.utc)
    watch = _signal(frame, signal="WATCH")

    first = service.process_scan([watch], "RUN_ONE", run_one, timestamp)
    second = service.process_scan([watch], "RUN_TWO", run_two, timestamp)
    assert first["new_signals"] == 1
    assert second["new_signals"] == 0
    assert len(service.database.rows("SELECT * FROM live_sessions")) == 2
    assert (run_one / "DAILY_REPORT_20250101.md").exists()
    assert (run_one / "signal_outcomes.csv").exists()
    assert (run_two / "DAILY_REPORT_20250101.md").exists()
    assert service.database.row("SELECT run_id FROM signals")["run_id"] == "RUN_ONE"


def test_missing_forward_frame_uses_dedicated_live_provider_route(service, monkeypatch):
    calls = []
    expected = _frame([
        {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000}
    ])
    monkeypatch.setattr(
        service_module,
        "load_data",
        lambda ticker, **kwargs: calls.append((ticker, kwargs)) or expected,
    )
    actual = service._frame("TEST.CA", {}, load_missing=True)
    pd.testing.assert_frame_equal(actual, expected)
    assert calls == [("TEST.CA", {"purpose": "forward_testing"})]
