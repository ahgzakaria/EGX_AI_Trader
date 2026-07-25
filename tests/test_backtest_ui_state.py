from types import SimpleNamespace

import pytest

from dashboard.backtest_state import (
    consume_backtest_updates,
    displayed_scope,
    initialize_backtest_state,
    recover_interrupted_backtest,
    scope_from_label,
    settings_are_dirty,
)
from services.backtest_service import FULL_HISTORY, VALIDATED_OOS


def test_scope_labels_map_to_exact_service_contracts():
    assert scope_from_label(
        "Validated Phase 5 OOS (comparable baseline)"
    ) == VALIDATED_OOS
    assert scope_from_label(
        "Full Available History (research only)"
    ) == FULL_HISTORY
    with pytest.raises(ValueError, match="Unknown backtest scope"):
        scope_from_label("unexpected")


def test_initialization_preserves_result_across_reruns():
    state = {"backtest_result": {"run_id": "RUN_1"}}
    initialize_backtest_state(state)
    initialize_backtest_state(state)
    assert state["backtest_result"] == {"run_id": "RUN_1"}
    assert state["backtest_status"] == "IDLE"


def test_button_execution_calls_service_once_and_returns_terminal_result():
    calls = []
    observed = []

    def runner(*, scope):
        calls.append(scope)
        yield {"type": "started", "run_id": "RUN_TEST"}
        yield {"type": "progress", "current": 1, "total": 1}
        yield {"type": "finished", "summary": {"TotalReturn": 65.81}}

    result = consume_backtest_updates(VALIDATED_OOS, runner, observed.append)
    assert calls == [VALIDATED_OOS]
    assert result["summary"]["TotalReturn"] == 65.81
    assert [event["type"] for event in observed] == [
        "started", "progress", "finished"
    ]


def test_missing_terminal_event_fails_loudly():
    def runner(*, scope):
        yield {"type": "started", "scope": scope}

    with pytest.raises(RuntimeError, match="without returning a finished result"):
        consume_backtest_updates(FULL_HISTORY, runner)


def test_interrupted_run_is_closed_and_ui_state_is_recoverable():
    state = {
        "backtest_running": True,
        "backtest_active_run_id": "RUN_TEST",
    }
    calls = []
    repository = SimpleNamespace(
        mark_interrupted=lambda run_id, reason: calls.append((run_id, reason))
    )
    assert recover_interrupted_backtest(state, repository) is True
    assert calls and calls[0][0] == "RUN_TEST"
    assert state["backtest_running"] is False
    assert state["backtest_status"] == "CANCELLED"


def test_unchanged_settings_keep_save_disabled_until_a_real_change():
    saved = {"strategy": {"min_score": 65}, "ai": {"enabled": False}}
    assert settings_are_dirty(saved, saved.copy()) is False
    changed = {"strategy": {"min_score": 66}, "ai": {"enabled": False}}
    assert settings_are_dirty(saved, changed) is True


def test_result_scope_formatting_uses_persisted_service_scope():
    assert displayed_scope({"backtest_scope": VALIDATED_OOS}) == (
        "Validated Phase 5 OOS"
    )
    assert displayed_scope({"backtest_scope": FULL_HISTORY}) == (
        "Full Available History"
    )
