# Backtest UI Audit

Date: 2026-07-12  
Scope: Settings & Backtest > Tools UI and execution lifecycle only

## Executive conclusion

The Run Backtest button was connected to `services.backtest_service.run_backtest`
and did start one service generator per click. The main symptom was therefore
primarily unclear UI feedback, but the audit also found two real lifecycle bugs:

1. The completed result existed only in the button branch and disappeared on a
   later Streamlit rerun.
2. Stopping an active Streamlit script could leave stale progress on screen and
   could leave an experiment as `RUNNING` (or record an empty-error failure)
   instead of closing it explicitly.

Confidence in this diagnosis: **High**. It was reproduced in a real local
Streamlit session, including creation of a run record, visible service progress,
and cancellation behavior.

## Files inspected

- `app.py`
- `dashboard/settings.py`
- `dashboard/ui.py`
- `services/backtest_service.py`
- `services/experiment_tracking.py`
- `backtest.py`
- `backtesting/engine.py`
- `backtesting/config.py`
- `config/settings_manager.py`
- `tests/test_backtest_scope.py`

The audit also followed the existing Experiment Tracking paths used by Run
History. No strategy, indicator, ranking, portfolio, AI, or calculation module
was changed.

## Root causes

### Execution feedback

Validated OOS first builds the chronological Walk-Forward context. That work was
previously one blocking call with no intermediate events, so the progress bar
could remain at zero for a long time. The page showed no start time, active
stage, elapsed time, or explicitly disabled run control.

### Result persistence

Result rendering was nested inside `if st.button(...)`. Streamlit reruns the
script after widget interactions, so local variables were lost and the latest
result could no longer be rendered after a rerun.

### Failure and cancellation lifecycle

The page had no UI exception boundary and no persisted error details. Streamlit
Stop can terminate the generator before normal completion; the next page state
had no reliable way to reconcile the active Run ID. Cancellation with an empty
`GeneratorExit` message could also be recorded as a generic failed run.

### Save Settings ambiguity

Save Settings was previously always enabled while the page was idle and wrote
the three sections separately. During long Streamlit execution, the frontend
globally disables widgets, which explains the disabled-looking screenshot, but
there was no dirty-state explanation. Users could not distinguish “unchanged”
from “temporarily unavailable while running.”

## Files modified

- `dashboard/settings.py`
  - Added explicit idle/running/completed/failed/cancelled presentation.
  - Added disabled running control, start time, stage, symbol progress, percent,
    and elapsed-time feedback.
  - Persisted result, scope, timestamps, Run ID, and errors in
    `st.session_state`.
  - Moved result rendering outside the click-only lifetime.
  - Added a friendly exception message plus retained technical traceback.
  - Added exact settings dirty-state comparison and one explicit save operation.
  - Kept selected Settings tab across Streamlit reruns.
- `dashboard/backtest_state.py` (new)
  - Added testable scope mapping, state initialization, one-call service
    consumption, dirty-state comparison, result scope formatting, and
    interrupted-run recovery.
- `services/backtest_service.py`
  - Added UI-only `started`, `stage`, and per-symbol progress events around the
    existing calculation path.
  - Extracted the original Walk-Forward context calculation into one shared
    function so silent scripts and the UI use identical logic.
  - Classified generator cancellation as `CANCELLED`; real exceptions remain
    `FAILED`.
- `services/experiment_tracking.py`
  - Added safe terminal cleanup for a `RUNNING` experiment without deleting or
    overwriting historical artifacts.
- `app.py`
  - Added next-rerun recovery for an interrupted Streamlit backtest.
- `backtest.py`
  - Updated CLI event handling so new UI-neutral lifecycle events are not
    mistaken for the terminal result.
- `tests/test_backtest_ui_state.py` (new)
  - Added scope, state persistence, one-call execution, missing terminal event,
    interrupted cleanup, dirty-state, and result formatting tests.
- `tests/test_backtest_scope.py`
  - Kept the existing scope contract test aligned with progress events and added
    cancellation finalization coverage.

## UI behavior before and after

| State | Before | After |
|---|---|---|
| Idle | Button only | Explicit “idle and ready” status |
| Running | Global Streamlit spinner; progress could look stuck | Disabled Run button, start time, stage, current symbol, progress, elapsed time |
| Completed | Results only inside the click branch | Success state and full latest result persisted across reruns/tab switches |
| Failed | Exception could terminate the page | Friendly error, logged full traceback, technical details retained |
| Cancelled | Stale progress / ambiguous Run History state | Terminal `CANCELLED` cleanup; never intentionally left `RUNNING` |
| Save unchanged | Ambiguous enabled/disabled appearance | Disabled by design with an explanation |
| Save changed | No dirty-state guidance; multiple writes | Enabled only after a change; one explicit persisted snapshot |

The final results show the selected scope and evaluation date range. The service
receives `VALIDATED_PHASE5_OOS` for the validated selector and `FULL_HISTORY`
for the research selector. Historical AI modes remain restricted to their
existing validated OOS behavior.

## Validation results

### Automated and static checks

- Python syntax compilation: passed for all modified application modules.
- Full automated suite: **42 passed**.
- Targeted UI-state tests: **7 passed** (included in the full total).
- Scope contract and cancellation finalization: passed.

### Actual Streamlit smoke test

The application was started locally on port 8513 and tested through the real UI.
Observed behavior:

- Settings page loaded successfully.
- Tools tab opened successfully.
- Validated Phase 5 OOS selector mapped correctly.
- Idle state was visible.
- Run Backtest fired once.
- Run button changed to disabled `Backtest Running` state.
- Progress advanced with `Preparing chronological AI labels`, current symbol,
  percentage, and elapsed seconds.
- Save Settings was disabled with the explicit unchanged-state explanation.
- Streamlit Stop was exercised and the run reached a terminal history state.

## Regression results

The locked Phase 5 reproduction script was run after all UI/service lifecycle
changes, using coverage **2020-08-06 to 2026-06-08**.

| Mode | Return | Net Profit | Profit Factor | Max Drawdown | Trades |
|---|---:|---:|---:|---:|---:|
| Strategy Only | 65.81% | 65,811.22 | 1.28 | 17.91% | 730 |
| AI Ranking Only | 71.62% | 71,618.87 | 1.30 | 16.48% | 736 |

These match the validated Phase 5 reference. **No validated trading metric
changed.**

## Trading-logic confirmation

No trading rule, threshold, indicator, AI feature, AI threshold, model behavior,
ranking formula, portfolio sizing rule, market-regime rule, entry, exit, cost,
slippage value, or backtest statistic was changed. The only service refactor
wraps the same deterministic passes with lifecycle/progress events and shares
the pre-existing Walk-Forward calculation through one function.
