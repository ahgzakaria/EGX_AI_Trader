# Rubix Production Launcher Diagnosis Report

Date: 2026-07-22 (Africa/Cairo)

## Outcome

The production launcher startup defect was reproduced and fixed. The normal BAT
launch now creates a visible, responsive `1080x820` window centered on the
1920x1080 display. Streamlit startup is considered successful only after its
local health endpoint answers on the configured port. Collector and dashboard
startup waits are bounded and remain off the Tk event thread.

No strategy, provider, database schema, market-data calculation, risk, AI,
portfolio, backtest, replay, or forward-testing logic was changed.

## Exact root cause

There were three launcher-layer defects:

1. `RubixAuthenticationAssistantUI._save_preferences()` persisted the raw value
   returned by `root.geometry()`. When Windows minimized/parked the Tk window,
   that value became `144x1+-32000+-32000`. The next launch applied it without
   validation, producing the tiny/off-screen window.
2. The console appeared to hang because the BAT invoked the long-lived Tk
   `mainloop()` synchronously while the only usable window was effectively
   invisible. There was no Python traceback because the process had not crashed.
3. After collector health, the old flow created the Streamlit process, recorded
   the run as ready, and opened the browser immediately. It did not wait for
   `http://127.0.0.1:8501/_stcore/health`, so process creation could be mistaken
   for application readiness. A stale `rubix_supervisor.pid.json` was also
   present even though its recorded PID was dead.

The existing stdout pipes were drained by background threads, so a pipe deadlock
was not the root cause. The collector health wait was already off the Tk thread;
the missing protection was mainly window-state validation, Streamlit readiness,
durable startup diagnostics, and robust instance ownership.

## Why the tiny window appeared

Observed before the fix:

- Saved geometry: `144x1+-32000+-32000`
- Tk state: `normal`
- Display: `1920x1080`

The launcher now rejects undersized/off-screen geometry, centers a sensible
default, enforces `minsize(900, 650)`, enables Windows DPI awareness, and records
geometry only while the window is normal and visibly valid. The repaired saved
geometry is `1080x820+420+130`.

## Why Rubix/Streamlit did not visibly complete

The manual authentication controls and Start button were inside the unusable
window, so the required user action was not accessible. In addition, the old
post-health callback did not verify that Streamlit was serving HTTP before
opening the browser and recording `ready`. At diagnosis time there was no
listener on port 8501 and no matching launcher, collector, supervisor, or
Streamlit process; only a stale supervisor PID file remained.

The authentication workflow remains intentionally manual: the user selects a
fresh authentication-frame file and presses **Start Rubix**. The launcher does
not store or discover authentication material.

## Files changed

- `scripts/launch_rubix_production.py`
  - sanitized/centered geometry and DPI handling
  - bounded collector and Streamlit readiness stages
  - responsive background startup workers
  - durable stage/error logging and visible fatal errors
  - safe port ownership behavior and browser-open-after-ready
  - launcher, supervisor, and Streamlit PID ownership
  - process-tree cleanup and secret-safe command logging
- `scripts/rubix_collector_supervisor.py`
  - atomic single-instance PID claim
  - stale PID recovery
  - fatal exception/traceback logging
  - default production supervisor log path
- `scripts/start_rubix_production.bat`
  - normal `pythonw.exe` double-click mode
  - `--debug` console mode
  - `--check` installation mode
  - explicit validation and meaningful bootstrap exit codes
- `scripts/launcher_process_utils.py` (new)
  - window geometry, DPI, PID identity, port, and readiness helpers
- `tests/test_rubix_launcher_startup.py` (new)
  - geometry, stale/live PID, timeout, port ownership, and redaction tests
- `config/rubix_launcher_settings.json`
  - replaced the corrupted saved geometry with a valid centered value

One proven-stale runtime file, `data/rubix_supervisor.pid.json`, was removed. It
contained PID 15188, which was confirmed not to identify a live process. It is
recreated automatically by the next supervisor start.

## Startup flow after the fix

```text
start_rubix_production.bat
  -> validate project, venv, launcher, supervisor
  -> pythonw.exe launch_rubix_production.py
  -> claim launcher PID identity (stale-safe)
  -> create centered, responsive Tk assistant
  -> user selects fresh auth-frame and presses Start Rubix
  -> Popen rubix_collector_supervisor.py
  -> supervisor claims PID and Popen rubix_feed.cli
  -> bounded Rubix SQLite health polling (90 seconds)
  -> Popen streamlit run app.py
  -> bounded localhost health polling (45 seconds)
  -> open browser once only after HTTP readiness
```

No unbounded `wait()`, `join()`, or `communicate()` exists in the launcher start
path. Long-lived stdout is continuously consumed. Stop closes the owned process
trees; an already-running dashboard recognized through its live PID marker is
reused and is not killed by a later launcher.

## Error visibility

- `logs/rubix_launcher.log`: launcher stages, commands with auth path redacted,
  child PIDs, readiness, stop, exceptions, and tracebacks.
- `logs/rubix_supervisor.log`: structured supervisor/collector lifecycle and
  fatal supervisor tracebacks.
- `logs/streamlit.log`: Streamlit stdout/stderr.
- Normal `pythonw.exe` failures appear in a message box and the durable log.
- `--debug` mirrors launcher stages to the console.

Authentication values, cookies, tokens, and authentication-frame paths are not
written to the launcher command log.

## Duplicate-process behavior

- Launcher and collector supervisor use atomic PID claims.
- PID records include process creation time, preventing PID reuse from being
  mistaken for the original process.
- Dead/stale PID records are removed; a matching live PID is never overwritten.
- Port 8501 is reused only when a current EGX Streamlit PID marker and Streamlit
  health endpoint agree.
- An occupied unowned port fails explicitly instead of hanging or starting a
  duplicate.
- Stop terminates the owned Windows process tree, including collector children.

## Commands

Normal double-click or command-line launch:

```bat
D:\EGX_AI_Trader\scripts\start_rubix_production.bat
```

Diagnostic console mode:

```bat
D:\EGX_AI_Trader\scripts\start_rubix_production.bat --debug
```

Installation-only check:

```bat
D:\EGX_AI_Trader\scripts\start_rubix_production.bat --check
```

## Validation results

- Python syntax/import check: PASS
- BAT `--check`: PASS (`VENV`, `LAUNCHER`, `SUPERVISOR`, and `APP` all OK)
- GUI reproduction before fix: `144x1+-32000+-32000`
- GUI validation after fix: `1080x820+420+130`, state `normal`, minimum
  `900x650`
- Actual normal BAT launch: PASS; window title correct, non-zero window handle,
  Windows reported `Responding=True`
- Actual Streamlit process test: PASS; health endpoint ready on port 8501
- Automatic test shutdown: PASS; no listening process and no Streamlit PID file
  remained
- Targeted Rubix/launcher tests: 31 passed
- Full project regression suite: 411 passed in 61.14 seconds
- Port-occupied failure: covered by automated test
- Streamlit child-exit failure: covered by automated test
- Stale/live PID and duplicate prevention: covered by automated tests
- Missing installation components: explicit BAT exit codes and `--check` output

An authenticated Rubix network handshake was not fabricated for this audit. A
fresh user-selected authentication frame was not available during the automated
run, and the launcher deliberately does not extract or persist one. The actual
supervisor/collector path remains gated by the existing manual Self Check; its
health wait and failure path are bounded and visible. Streamlit and the complete
launcher/BAT lifecycle were validated on the actual machine.

## Double-click validation result

PASS. Double-click mode launches a normal usable window through `pythonw.exe`.
Closing the window releases the launcher PID. Starting the dashboard waits for
real readiness before opening the browser. A second launch cannot create a
second owned launcher/supervisor/dashboard process.

## Trading-engine confirmation

No trading or data-provider calculation file was modified. All 411 tests pass.
Validated strategy behavior and metrics are unaffected by this launcher-only
change.
