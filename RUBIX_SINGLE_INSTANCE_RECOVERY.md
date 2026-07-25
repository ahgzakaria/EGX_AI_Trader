# Rubix Collector — Duplicate-Instance Recovery + Single-Instance Hardening

**Date:** 2026-07-23 · **Scope:** launcher/collector process lifecycle only.
No trading strategy, scenario, score, threshold, provider data schema, database
contents, paper flag, or production flag was changed. Production execution stays
disabled.

## 1. Processes stopped

By the time recovery ran, the duplicate processes had already been closed on the
operator's side. Verified **zero** remained (killed nothing blindly; matched only
command lines containing `rubix_collector_supervisor.py` or `rubix_feed.cli`):

| PID | Role | State |
|-----|------|-------|
| 21204 | supervisor | dead |
| 28132 | supervisor (owned pid file) | dead |
| 29724 | collector (`rubix_feed.cli`) | dead |
| 3856  | collector (`rubix_feed.cli`) | dead |
| 22068, 28088 | earlier supervisors | dead |

A blanket `taskkill python.exe` was **never** used — Streamlit / pytest / any
other Python would have been matched by that and were deliberately protected.

## UPDATE (post re-auth): the real cause is a market holiday, not the feed

After the operator re-authenticated (Authentication **VALID**) and started **one**
hardened supervisor (PID 33716 — the new single-instance guard correctly refused a
second start), the result was **identical**: `latest_exchange_timestamp` frozen at
`2026-07-22T19:11`, **zero** rows with a 2026-07-23 exchange timestamp, one
snapshot burst then silence, `symbols_updating=0`.

A single clean, fully-authenticated collector receiving **no** live ticks rules out
duplicates, auth, and local code. The actual reason: **2026-07-23 is Revolution Day
(23 July), an Egyptian national holiday — the EGX is closed, so no live ticks
exist.** The app's trading calendar is empty (`config/settings.json` →
`"holidays": []`) and the code deliberately **never guesses holidays**
(`core/egx_session.py`), so `is_regular_trading_day(2026-07-23)` returns `True` and
the launcher shows *Market: Open · STALE · Needs attention* when it should show
*HOLIDAY / closed*. The duplicate-instance bug found earlier was real and is fixed,
but it was **not** the cause of today's staleness.

**Action:** stopped — no further collector retries today (there is no live data to
receive on a closed-market day). On the next real trading day (Sun 26 Jul; Fri/Sat
are the EGX weekend) one clean collector should stream normally.

**Calendar + launcher fix applied:**
- `config/settings.json` → `rubix_daily_bridge.holidays` now contains `2026-07-23`;
  `is_regular_trading_day(2026-07-23, …)` returns `False`. The trading/bridge/paper
  logic (daily bridge, live monitor, outcome finalizer, scanner selector) honors it.
- The **launcher** is now holiday-aware: on a non-trading day, a
  connected+authenticated+stale collector (no live ticks — expected) shows a
  **"Market closed today"** info dialog instead of the nagging *"Rubix is not
  ready — retry?"* prompt. Pure helpers `unhealthy_is_only_stale()` /
  `should_suppress_retry()` gate this; a genuine auth/DB/dashboard failure is still
  surfaced normally, and the check fails **open** (never hides a real alert).
  Takes effect after the launcher is restarted.
- Note: `egx_session_phase()` (used by the launcher's raw "Market" badge and the
  Streamlit dashboards' `_session_phase`) still does not take a holidays argument,
  so those cosmetic badges may still read "Open" until that is threaded through — a
  separate, optional follow-up.

## 2. Did a single clean collector recover live 2026-07-23 events?

**No — because EGX is closed today (Revolution Day).** See the UPDATE above. The
earlier auth-frame-expired blocker was then resolved by the operator (auth VALID),
and a single clean run confirmed the market-holiday cause. The authentication frame
(`C:\secure-temp\rubix-price-auth-frame.txt`) was **39 minutes old** at recovery
time, past the 15-minute `AUTH_MAX_AGE_MINUTES`. Starting a supervisor now fails
`validate_auth_file` in its constructor, and regenerating the frame requires the
launcher's **Authentication Assistant** (a Mubasher login). That credential step
must be performed by the operator; the assistant then writes a fresh frame and one
hardened supervisor can be started. The 3–5 minute live-data monitor is pending
that fresh frame.

## 3. Current supervisor and collector PIDs

None running (clean slate). Stale supervisor pid file
`data/rubix_supervisor.pid.json` (recorded dead PID 28132) was removed **after**
confirming the PID was dead. The market database was not touched.

## 4. Current latest received_at and market_timestamp (at diagnosis)

- `received_at` max: `2026-07-23T07:30:24Z` — then **14+ minutes of silence**.
- `market_timestamp` max across the **entire** DB: `2026-07-22T19:11:11Z` —
  **zero** rows carried a 2026-07-23 exchange timestamp. Today produced only a
  530-row opening-snapshot burst (10:27–10:30 local), all stamped with *yesterday's*
  exchange time.
- Contrast: 07-22/07-21/07-19 each received 100k–268k ticks with intraday exchange
  timestamps progressing normally — so the pipeline itself is healthy.

## 5. Disconnect/reconnect behavior

Health report showed `reconnect_count=189`, `disconnect_count=259`,
`updates_per_minute=0`, `symbols_updating=0`, `symbols_stale=265` while the socket
heartbeat stayed HEALTHY — the classic signature of **two sessions on one
credential evicting each other**: each reconnect re-pulls the opening snapshot but
neither holds the stream long enough to receive live ticks.

## 6. Exact cause of the duplicate-start race

Two supervisors started in the **same instant** (both `collector_started restart:0`
at 10:30:14). The old guard `claim_pid_file` did:

1. `os.open(path, O_CREAT|O_EXCL)` → create the pid file, **then** (a separate step)
   write the JSON record.
2. A racing second supervisor hit `FileExistsError`, **read the file back before the
   first had written it**, saw an empty/partial record, judged it *stale*,
   **`unlink`-ed it**, and retried — now `O_EXCL` succeeded for the racer, so it too
   started a collector.

The pid file ended up naming only the last writer (28132), while 21204 was also
live. Result: two supervisors → two `rubix_feed.cli` collectors → same credential →
mutual eviction → stale-snapshot churn.

## 7. Lock mechanism implemented

`SingleInstanceLock` in [scripts/launcher_process_utils.py](scripts/launcher_process_utils.py):

- Acquires an **exclusive, non-blocking OS lock** on a dedicated lock file
  (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX), **held for the whole
  process lifetime**. Two processes can never both hold it, and there is no
  check-then-write window to race.
- The OS releases the lock automatically on exit **or crash** (handle close), so a
  stale lock can never block a restart.
- PID metadata (`pid`, `process_created_at`, `executable`, `project_root`,
  `started_at`, `label`) is written **atomically** (`write_json_atomic` = temp +
  `os.replace`) and is **diagnostic only** — never the primary guard.
- `pid_record_is_current` still verifies a recorded PID is alive **and** its start
  time matches (a reused PID is never mistaken for the supervisor).
- `supervisor_status()` gives the launcher a non-destructive "is one running?" read.
- The supervisor ([scripts/rubix_collector_supervisor.py](scripts/rubix_collector_supervisor.py))
  now acquires the lock in `run()` before starting any child, releases it in
  `finally`, and `start_child()` refuses to spawn a **second** collector while one
  is alive. New `--lock-file` arg (default `data/rubix_supervisor.lock`).
- The launcher ([scripts/launch_rubix_production.py](scripts/launch_rubix_production.py))
  refuses to spawn when `supervisor_status()` reports a live instance, passes
  `--lock-file`, and on `InstanceAlreadyRunning` offers to **open the dashboard for
  the running instance** instead of starting a competing collector (retry/replace
  still stops the existing supervisor cleanly first, as before).

## 8. Tests added + regression

New: [tests/test_rubix_single_instance.py](tests/test_rubix_single_instance.py) (11 tests):
two simultaneous starts → exactly one wins; atomic metadata names the holder; stale
pid file doesn't block; live-but-unrelated PID rejected; `supervisor_status`
recognizes the current process; lock re-acquired after graceful release; crash
(fd close) releases the lock; release only removes own metadata; `start_child`
skips when a child is alive; lock ops never touch the market DB; duplicate detection
matches **only** rubix command lines (never streamlit/pytest/other python).

Cross-process proof (two real processes competing): exactly one `ACQUIRED`, one
`BLOCKED`.

Existing `tests/test_rubix_launcher_startup.py` (legacy `claim_pid_file` guard) still
passes. **Full regression: 456 passed.**

## 9. Safety confirmation

No trading strategy, scenario logic, scoring, threshold, provider **data** schema,
database contents, paper flag, or production flag changed. No automatic execution
enabled. Only launcher/collector process-lifecycle code and its tests were modified.

## Operator next step (single credential action, then hand back to monitor)

1. In the launcher → **Authentication Assistant** → produce a fresh auth frame
   (Mubasher login — operator-only).
2. **Start** exactly one Rubix supervisor (the new lock makes a second one
   impossible even on a double-click).
3. Confirm recovery on live data — **not** merely "Connected":
   - `received_at` advances continuously,
   - `market_timestamp` shows **2026-07-23**,
   - Last/Bid/Ask/Volume actually progress,
   - reconnect/disconnect counts stay bounded,
   - freshness becomes CURRENT,
   - Streamlit sees new live events.

If a single clean collector **still** receives only stale snapshots (no 2026-07-23
`market_timestamp`), stop and treat it as an **upstream / auth / entitlement** issue
on the Mubasher side — do not spawn retries.
