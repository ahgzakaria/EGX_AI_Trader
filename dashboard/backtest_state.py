"""Pure state helpers for the Streamlit backtest controls.

Keeping these transitions outside the page makes button wiring, persistence,
scope mapping, and interrupted-run recovery deterministic and testable without
starting Streamlit or touching any trading calculation.
"""

from copy import deepcopy
from datetime import datetime

from services.backtest_service import FULL_HISTORY, VALIDATED_OOS


SCOPE_LABELS = {
    "Validated Phase 5 OOS (comparable baseline)": VALIDATED_OOS,
    "Full Available History (research only)": FULL_HISTORY,
}

BACKTEST_STATE_DEFAULTS = {
    "backtest_running": False,
    "backtest_status": "IDLE",
    "backtest_result": None,
    "backtest_error": None,
    "backtest_traceback": None,
    "backtest_started_at": None,
    "backtest_finished_at": None,
    "backtest_elapsed_seconds": None,
    "backtest_stage": "Ready",
    "backtest_progress": 0.0,
    "backtest_progress_text": "",
    "backtest_active_run_id": None,
    "backtest_active_run_directory": None,
    "backtest_last_scope": None,
}


def initialize_backtest_state(state):
    """Initialize absent keys without overwriting a persisted result."""

    for key, value in BACKTEST_STATE_DEFAULTS.items():
        state.setdefault(key, deepcopy(value))
    return state


def scope_from_label(label):
    """Map the exact user-facing scope label to the service scope constant."""

    try:
        return SCOPE_LABELS[label]
    except KeyError as exc:
        raise ValueError(f"Unknown backtest scope: {label}") from exc


def settings_are_dirty(saved, proposed):
    """Return True only when a user-editable value differs from disk."""

    return saved != proposed


def consume_backtest_updates(scope, runner, observer=None):
    """Execute the service exactly once and require a terminal result event."""

    terminal = None
    for update in runner(scope=scope):
        if observer is not None:
            observer(update)
        if update.get("type") == "finished":
            terminal = update
    if terminal is None:
        raise RuntimeError("Backtest ended without returning a finished result.")
    return terminal


def recover_interrupted_backtest(state, repository, reason=None):
    """Close a run left active by Streamlit Stop, browser loss, or a rerun."""

    initialize_backtest_state(state)
    if not state.get("backtest_running"):
        return False

    run_id = state.get("backtest_active_run_id")
    message = reason or "Backtest was interrupted before completion."
    if run_id:
        repository.mark_interrupted(run_id, message)

    state["backtest_running"] = False
    state["backtest_status"] = "CANCELLED"
    state["backtest_error"] = message
    state["backtest_finished_at"] = datetime.now().astimezone().isoformat()
    state["backtest_stage"] = "Cancelled"
    return True


def displayed_scope(result):
    """Format the persisted result scope consistently across reruns."""

    return (
        "Validated Phase 5 OOS"
        if result.get("backtest_scope") == VALIDATED_OOS
        else "Full Available History"
    )
