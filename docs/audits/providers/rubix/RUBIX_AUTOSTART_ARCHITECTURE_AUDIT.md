# Rubix Collector Autostart — Architecture Audit

Read-only audit of the existing production collector, performed to determine
whether it can be started automatically at 09:20 Cairo ahead of the installed
`EGX ORB Full Shadow Automation` task (09:40).

**No collector was started or stopped during this audit.** Every finding comes
from reading the shipped code.

---

## 1. Verdict

# `RUBIX_AUTO_START_BLOCKED`

**Blocker: the collector cannot authenticate without a human.**

The supervisor requires an authentication frame that is **at most 15 minutes
old**, and nothing in this repository can produce or refresh one — by explicit
design. A 09:20 scheduled task would therefore start a collector that
immediately fails its own auth validation.

Full reasoning in §5. Two viable paths forward in §8.

## 2. The two entry points

There are two, and they are very different.

### 2.1 `scripts/launch_rubix_production.py` — interactive GUI launcher

`scripts/start_rubix_production.bat` runs it under **`pythonw.exe`** (windowed,
no console). Its `main()` ends in:

```python
RubixAuthenticationAssistantUI().run()      # tkinter, then mainloop()
```

It is a **tkinter GUI authentication assistant**. It starts nothing on its own:
collection begins only when a human presses **"Start Rubix & App"** or
**"Start Research Only"**.

Two consequences for automation:

- Scheduling it would open a window and wait forever. Nothing would be
  collected, but a task would appear to be "running" — worse than no
  automation, because it invites false confidence.
- **"Start Rubix & App" also launches the Streamlit Dashboard** (port 8501).
  The ORB automation contract forbids the scheduled path from launching the
  Dashboard.

### 2.2 `scripts/rubix_collector_supervisor.py` — headless supervisor

This is the real collector owner, and it *is* schedulable in principle:

- a plain `argparse` CLI, no tkinter, no console dependency;
- it owns the websocket — `FEED_URL = wss://eg-feed3.mubashertrade.com/websocket/price`;
- it owns reconnect, resubscription, health heartbeats and child restarts;
- it holds a real single-instance lock (§4);
- it handles `SIGTERM` and writes a `supervisor_shutdown` record.

The GUI builds exactly this command:

```
<venv python> scripts/rubix_collector_supervisor.py
    --adapter <adapter dir>
    --auth-frame-file <auth frame>
    --database <rubix_live_market.db>
    --symbols <universe source>
    --batch-size 100
    --pid-file <supervisor pid>
    --lock-file <supervisor lock>
    --log-file <supervisor log>
```

launched with `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, cwd = project root.

**Any autostart must target the supervisor, never the GUI**, and must not
duplicate the websocket, adapter or subscription logic.

## 3. Interactive session requirement

# `AUTOMATION_REQUIRES_LOGGED_IN_SESSION`

| Dependency | Present? | Where |
|---|---|---|
| GUI / desktop session | **yes** | `launch_rubix_production.py` — tkinter `Tk()`, `mainloop()`, `messagebox` |
| Interactive authentication | **yes** | auth frame is human-captured (§5) |
| Browser state | **indirectly** | the frame originates from an authenticated browser session |
| User-profile paths | yes | project-root relative, run as the logged-in user |
| Windows Credential Manager | **no** | no credential store is used |
| Stored password | **no** | none, and none may be added |

The supervisor *alone* is headless. The **authentication** in front of it is
not. So the workflow as a whole requires a logged-in interactive session, and
any task must run as the current user with `Interactive` logon and
`RunLevel Limited`, **storing no password**.

## 4. Single-instance protection — verified genuine

Two independent layers, both real:

**`SingleInstanceLock` (`scripts/launcher_process_utils.py`)** — an atomic OS
file lock (`os.open(O_CREAT|O_RDWR)` + exclusive lock) held for the whole
process lifetime. The OS drops it on exit *or crash*, so a stale lock cannot
block a restart. `acquire()` raises `InstanceAlreadyRunning` immediately when
another live process holds it. The PID/metadata JSON is explicitly
**diagnostics only, never the primary guard** — which is the correct design; a
PID file alone is exactly what goes stale.

**Launcher-side refusal** — `start_collector` checks `supervisor_status(...)`
first and raises `InstanceAlreadyRunning` rather than attempting a doomed second
launch; a dead-PID record is cleaned up as stale metadata.

The launcher itself additionally claims `LAUNCHER_PID_FILE` via
`claim_pid_file`, so two GUIs cannot run either.

**Conclusion: the existing lock is sound and must not be weakened, replaced or
bypassed.** A scheduled task adds `MultipleInstances = IgnoreNew` as a second
belt, not as the guarantee.

## 5. The blocker, precisely

`rubix_collector_supervisor.py` line 126:

```python
self.auth_file = validate_auth_file(Path(args.auth_frame_file), args.auth_max_age_minutes)
```

with `--auth-max-age-minutes` defaulting to **15**.

`services/rubix_auth_assistant.py` opens with:

> *"This module never authenticates, controls a browser, or persists frame
> data. It only validates a user-selected temporary JSON file in memory…"*

`inspect_auth_frame` enforces `file_age_valid` against
`max_age_minutes * 60`, and `validate_auth_frame_or_raise` raises when invalid.

So:

1. The supervisor **requires** an auth frame ≤15 minutes old.
2. The frame is **captured manually by a human** from an authenticated session
   and saved to a temporary file.
3. **No code in this repository can create or refresh that frame**, deliberately.

A task firing at 09:20 has no way to obtain a frame less than 15 minutes old.
It would start the supervisor, fail `validate_auth_file`, and exit — or, if
pointed at the GUI instead, sit at a window doing nothing.

Producing the frame automatically would require driving a browser login, i.e.
**a second authentication implementation** — which the task brief, and the
existing architecture, both forbid. Storing the credentials is likewise
forbidden.

## 6. Normal and abnormal shutdown

- **Normal:** `SIGTERM` sets `STOP_REQUESTED`; the loop exits, emits
  `supervisor_shutdown`, writes a final health record with `"shutdown": true`
  and runs database maintenance. Nothing needs an external killer.
- **Abnormal:** the OS releases the exclusive lock on crash, so a restart is
  not blocked by a stale lock; the PID record is treated as stale metadata.
- **Observed:** on 2026-08-03 the collector's processes exited on their own
  after the close (last write 15:15 Cairo, ~50 minutes after the auction end).

**Therefore no automatic stop task is warranted**, and none is created. Killing
the collector automatically after the close would be both unnecessary and
riskier than the current behaviour.

## 7. Source-health boundary

Starting a process is not evidence of a healthy feed. That distinction stays
where it already is: the ORB orchestrator at 09:40 independently verifies the
source exists, `query_only` holds, the cursor progresses, receive timestamps
are current, and freshness is acceptable — and fails closed with
`SKIPPED_SOURCE_UNAVAILABLE` otherwise.

**Nothing in this audit changes ORB's fail-closed behaviour**, and no autostart
should ever be treated as a substitute for it.

## 8. Paths forward

**Option A — accept a one-touch morning step (recommended).** A human captures
the auth frame and presses Start shortly before 09:20. This is what happens
today. Automation can still help: a read-only readiness check
(`scripts/check_rubix_collector_readiness.py`) reports whether the frame is
fresh, whether a supervisor already holds the lock, and whether the source is
progressing — so the operator knows before 09:40 whether the ORB run will
succeed.

**Option B — obtain a longer-lived credential from the provider.** If Mubasher
can issue a token with a usable lifetime, the supervisor could be scheduled
directly at 09:20 with `--auth-frame-file` pointing at it. That is a provider
and security decision, not a scripting one, and it must not be simulated by
scraping or replaying a browser session.

Until A or B is resolved, the honest state is: **the ORB task is installed and
correct, and will fail closed at 09:40 whenever the collector was not started.**

## 9. What was deliberately not done

- No second collector, websocket client, adapter or subscription path.
- No stored Windows password and no credential in any task argument.
- No automatic stop/kill task.
- No weakening of `SingleInstanceLock`.
- No Rubix scheduled task installed — the installation gates in the brief
  require a verified startup path, and §5 shows there is none.
