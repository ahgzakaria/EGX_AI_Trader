# Rubix Collector Autostart — Operating Runbook

What is automated, what is not, and what the operator must still do each
morning.

**Current state: `RUBIX_AUTO_START_BLOCKED`.** The collector cannot start
unattended. See `RUBIX_AUTOSTART_ARCHITECTURE_AUDIT.md` for the full reasoning;
the short version is in §1.

---

## 1. Why there is no autostart task

The headless supervisor (`scripts/rubix_collector_supervisor.py`) is perfectly
schedulable on its own. What is not schedulable is the **authentication** in
front of it:

- the supervisor requires `--auth-frame-file` no older than **15 minutes**;
- `services/rubix_auth_assistant.py` states it *"never authenticates, controls
  a browser, or persists frame data"* — it only validates a human-selected file;
- nothing in the repository can create or refresh that frame.

Scheduling the GUI launcher instead would be worse: it opens a tkinter window
and waits for a human, and its "Start Rubix & App" button also launches the
Streamlit Dashboard, which the ORB automation contract forbids.

An installer exists and is correct
(`scripts/windows/install_rubix_collector_scheduled_task.ps1`), but it
**refuses to register** while no fresh auth frame is present, so it cannot
create a task that quietly fails every morning.

## 2. The morning sequence

| Time (Cairo) | Who | What |
|---|---|---|
| ~09:10 | **operator** | capture a fresh authentication frame |
| ~09:15 | **operator** | start the collector from the official launcher |
| ~09:18 | **operator** | run the readiness check (§3) |
| 09:40 | automatic | `EGX ORB Full Shadow Automation` fires |

If the collector is not running at 09:40, the ORB orchestrator records
`SKIPPED_SOURCE_UNAVAILABLE` and exits cleanly. Nothing is corrupted and no
false session is claimed — it fails closed by design.

## 3. Readiness check — run this before 09:40

Read-only. Starts nothing, authenticates to nothing, never takes the
supervisor lock.

```bash
python scripts/check_rubix_collector_readiness.py --rubix-db-path "F:\EGX_AI_Trader\data\rubix_live_market.db" --auth-frame-file "<path to your captured frame>"
```

It reports four things and exits non-zero when any fails:

| Check | Meaning |
|---|---|
| `supervisor_running` | a live supervisor holds the lock |
| `auth_frame` | present and within the 15-minute limit |
| `source_database` | readable, and has rows |
| `source_progressing` | last row received in the past 5 minutes — not frozen |

`READY: True` means the 09:40 ORB run should find a healthy feed. Anything else
means it will fail closed, and the message says which check failed.

Add `--json` for machine-readable output.

## 4. Starting the collector

Use the **official launcher only**:

```
scripts\start_rubix_production.bat
```

It runs `launch_rubix_production.py` under `pythonw.exe`, which opens the
authentication assistant. Press **Start Rubix & App**, or **Start Research
Only** if you do not want the Dashboard.

Never start `rubix_collector_supervisor.py` by hand unless you know the auth
frame is fresh — it is the same process the launcher spawns, and starting it
twice is prevented by its lock, not by you remembering.

## 5. Duplicate protection

Two independent layers, and the second is the real one:

1. **`SingleInstanceLock`** — an atomic OS file lock held for the whole process
   lifetime, released by the OS on exit *or crash*. A second supervisor raises
   `InstanceAlreadyRunning` immediately. The PID/metadata JSON is diagnostics
   only, never the guard, so a stale PID file cannot block a restart.
2. **`MultipleInstances = IgnoreNew`** on any scheduled task — a second belt.

The launcher additionally refuses to spawn a supervisor when
`supervisor_status()` reports a live one.

**Never weaken, replace or bypass the lock.** One credential means one session.

## 6. Stopping the collector

Use the launcher's **Stop** button, or send `SIGTERM`. The supervisor then
exits its loop, emits `supervisor_shutdown`, writes a final health record and
runs database maintenance.

**Do not hard-kill it**, and there is deliberately no automatic stop task: it
already shuts down cleanly on its own, and it was observed doing so
(2026-08-03, last write 15:15 Cairo, ~50 minutes after the auction close).

## 7. If a longer-lived credential becomes available

Only then does a real autostart task become possible:

```powershell
.\scripts\windows\install_rubix_collector_scheduled_task.ps1 `
    -ProjectRoot   "F:\EGX_AI_Trader" `
    -PythonExe     "F:\EGX_AI_Trader\venv\Scripts\python.exe" `
    -AdapterPath   "<rubix adapter directory>" `
    -AuthFrameFile "<long-lived frame>" `
    -WhatIfOnly
```

Drop `-WhatIfOnly` to register. It schedules **09:20 Cairo, Sunday–Thursday**,
targets the supervisor (never the GUI), uses `WakeToRun`,
`StartWhenAvailable`, `IgnoreNew`, `RunLevel Limited`, Interactive logon and no
stored password.

It refuses to register when the target lacks `SingleInstanceLock`, when the
target imports tkinter, when the auth frame is stale, when the Cairo timezone
cannot be resolved, or when a same-named task exists that is not ours.

Remove with `.\scripts\windows\remove_rubix_collector_scheduled_task.ps1` —
which unregisters the schedule and deletes no database, log or report.

## 8. What is deliberately not automated

- No second collector, websocket client, adapter or subscription path.
- No stored Windows password, and no credential in any task argument.
- No browser automation to obtain an auth frame.
- No automatic stop or kill after the close.
- No change to ORB's fail-closed behaviour — a running process is never
  accepted as proof of a healthy feed.
