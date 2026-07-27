# Rubix Launcher Startup Performance Audit

Date: 2026-07-27
Repository: `D:\EGX_AI_Trader`
Isolated worktree: `D:\EGX_AI_Trader\.claude\worktrees\rubix-launcher-startup-freeze`
Branch: `fix/rubix-launcher-startup-freeze`
Base commit: `4fb930794c3f9c572e682f2254dbc2bd99de3908`
Launcher: `scripts/launch_rubix_production.py`
Python: `D:\EGX_AI_Trader\venv\Scripts\python.exe`
Windows: Windows 11, build 10.0.26200

## Exact root cause

There were two independent Tk-thread blockers.

1. `RubixAuthenticationAssistantUI.__init__()` built the complete multi-tab UI and
   called `_init_static_status()` before `mainloop()`. `_init_static_status()` called
   `refresh_status()`, which called `current_research_status(online=False)`. Despite the
   name, that function called `research_router.eodhd_history("COMI")`. A stale EODHD
   cache could therefore enter the HTTP client's 25-second, three-retry policy (plus
   backoff and more than one endpoint) before Tk processed its first event. The real
   machine probe measured 16.849 seconds in this one call on `MainThread`.

2. Once a collector existed, `_refresh()` called `ProductionSupervisor.health()` on
   the Tk thread every two seconds. The provider health implementation aggregated the
   complete 390,651,904-byte Rubix database: `COUNT(*)`/`MAX()` over quotes and candles,
   grouped every ticker, read up to 20,000 feed events, and grouped all event counts.
   The real database probe measured 7.165 seconds per call on `MainThread`. Because the
   next refresh became due before the prior scan ended, Tk entered another scan after
   roughly one 500 ms callback interval. At the measured rate, 117 cycles in 15 minutes
   spend about 838 seconds (13.97 minutes) inside SQLite. This repeated scan is the
   operation that explains the prolonged Windows “Not Responding” state.

No recursive filesystem walk was found in the normal launcher path. The freeze was
network retry plus repeated full-database health work on Tk.

## Ranked before/after measurements

| Rank | Operation | Before | Thread before | After/design |
|---:|---|---:|---|---|
| 1 | Rubix full health over live 390.7 MB DB | 7.165 s per scan, repeatedly | `MainThread` | unchanged provider calculation, coalesced background worker every 15 s |
| 2 | EODHD current-research status | 16.849 s | `MainThread`, before first frame | strict offline cache peek; 1.843 s against the same real cache, background only |
| 3 | Full pre-frame Tk UI construction | 3.998 s to first frame in the initial refactor probe | `MainThread` | lightweight shell first; detailed controls deferred (0.119 s) |
| 4 | Historical Rubix readiness, 2026-07-27 log | 331.066 s from collector start to Streamlit request | worker, but UI repeatedly blocked by health scans | cancellable 0.75 s polls using three targeted DB queries |
| 5 | Historical Streamlit readiness, 2026-07-27 log | 36.927 s | worker | cancellable 0.5 s health polls, 45 s typed timeout |
| 6 | Separate-port Streamlit smoke after fix | n/a | n/a | process start 0.364 s; ready 4.035 s |

The original combined first frame was not re-launched after the live cache was refreshed,
because doing so would no longer reproduce the same 16.849-second stale-cache condition.
The measured pre-frame components give a conservative lower bound of 20.847 seconds
(3.998 + 16.849). Two post-fix end-to-end real Tk probes measured 0.189–0.256 seconds.

## Post-fix real Tk probe

The controlled probe constructed the real launcher, left status and Ollama checks
enabled, moved the window, minimized/restored it, and closed without starting or stopping
Rubix, Streamlit, or Ollama.

| Metric | Result |
|---|---:|
| First rendered frame | 0.189–0.256 s |
| Longest event-loop stall (worst observed) | 0.108 s |
| Heartbeat samples | 141–164 |
| Window move | passed |
| Minimize/restore | passed |
| Subprocesses started | 0 |
| Directories traversed | 0 |
| Config load before mainloop | 0.000367 s |
| Runtime-state load before mainloop | 0.000076 s |
| Tk construction before mainloop | 0.070873–0.095801 s |
| Deferred detailed UI construction | 0.069105–0.118545 s |
| Background EODHD card (no token in worktree) | 0.001157 s |
| Background EGX calendar | 0.010173 s |
| Background safety flags | 0.006220 s |
| Background Ollama health/model check | 0.033515 s |

The event-loop maximum is below the 250 ms acceptance threshold. Moving and minimizing
the window while checks were active demonstrates that Windows continued dispatching UI
events; no “Not Responding” interval was observed.

## New worker/state-machine design

`services/launcher_startup.py` provides:

- an explicit `StartupState` enum: `UI_READY`, `PREFLIGHT_RUNNING`,
  `PREFLIGHT_READY`, `STARTING_RUBIX`, `WAITING_FOR_AUTH`,
  `STARTING_COLLECTOR`, `STARTING_STREAMLIT`, `WAITING_FOR_READINESS`, `READY`,
  `DEGRADED`, `FAILED`, `STOPPING`, and `STOPPED`;
- coalescing daemon workers;
- immutable `WorkerEvent` queue messages;
- a cancellation event shared by startup/readiness jobs;
- typed cancellation/timeout results;
- a short-TTL health cache that preserves the last successful value after refresh
  failure;
- monotonic `PhaseRecord` timing with thread, path, row, endpoint, subprocess, timeout,
  traversal, and pre-mainloop metadata.

Tk constructs only a lightweight shell before `mainloop()`. After the first idle frame,
the detailed controls are constructed and background status begins. Workers never access
Tk variables or widgets. Tk drains the queue every 50 ms through `root.after`.

The stage and elapsed time remain visible. Duplicate Start, Refresh, Self Check, Stop,
browser-open, and persistence jobs are coalesced. Old card values remain visible while a
refresh runs.

## Startup phase audit

| Phase | Thread after fix | Traversal | Subprocess | Timeout / bound | Direct scope |
|---|---|---:|---:|---|---|
| Python imports | main, before loop | no | no | n/a | explicit imports |
| tracked config | main, before loop | no | no | n/a | one JSON file |
| local/runtime state | main, then migration worker | no | no | n/a | two explicit JSON files |
| single-instance lock/PID validation | main, before loop | no | no | immediate OS/PID check | one PID file |
| Tk shell | main, before loop | no | no | 0.256 s observed frame | widgets only |
| self-check/path validation | worker | no | no | lightweight target under 3 s | explicit files only |
| auth-frame inspection | worker | no | no | 1 MB file cap | selected file only |
| Rubix DB discovery/schema/latest quote | worker | no | no | 750 ms SQLite busy timeout | three queries, at most 260 rows |
| EODHD token/cache/session | worker | no | no | offline only | `.env` status plus one cache file |
| EGX calendar | worker | no | no | local | central calendar |
| Ollama health/model | worker | no | no | 3 s | `http://127.0.0.1:11434/api/tags` |
| process/PID/port checks | worker or constant-time UI poll | no | no | socket 350 ms; HTTP 750 ms | configured PID/port only |
| Rubix collector start | worker | no | yes, `Popen` | long lived | owned supervisor only |
| Streamlit start | worker | no | yes, `Popen` | long lived | owned Streamlit only |
| Rubix readiness | worker | no | no | 90 s typed timeout; 0.75 s polls | targeted DB health |
| Streamlit readiness | worker | no | no | 45 s typed timeout; 0.5 s polls | local health endpoint |
| dashboard open | worker | no | browser helper | no synchronous Tk wait | localhost URL |
| log preview | Tk queue drain | no | no | bounded queued lines | no full-log parse |

Normal startup contains no `os.walk`, `rglob`, recursive glob, checksum, reports scan,
symbol-universe validation, model inference, AI narrative, backtest, or deep diagnostics.
Full diagnostics remain behind **Run Deep Diagnostics**.

## SQLite audit

The old latest-quote plan (`ORDER BY received_at DESC LIMIT 1`) showed `SCAN quotes` plus
`USE TEMP B-TREE FOR ORDER BY`; the production DB had no `received_at` index. The new
launcher query uses the existing integer `rowid` insertion order:

1. targeted `sqlite_master` table check;
2. `SELECT rowid,... FROM quotes ORDER BY rowid DESC LIMIT 1`;
3. `SELECT observed_at,event FROM feed_metrics ORDER BY id DESC LIMIT 256`.

Connections are `mode=ro`, `query_only=ON`, use `busy_timeout=750`, and close promptly.
Missing, busy/locked, incomplete-schema, timeout, and cancellation states are typed and
sanitized. No `integrity_check` runs during startup.

Before collector start, the launcher records the latest quote rowid. Readiness requires a
newer quote. A post-baseline quote proves the newly started collector connected and
authenticated without scanning old authentication history. The full existing provider
health calculation remains unchanged and runs only as a coalesced background status job.

## Subprocess audit

| Call | Location after fix | Policy |
|---|---|---|
| Rubix supervisor `Popen` | worker | launcher-owned PID; pipe continuously drained; supervisor writes durable log |
| Streamlit `Popen` | worker | launcher-owned PID file; output continuously drained to `logs/streamlit.log` |
| `taskkill /PID ... /T` | stop worker | exact owned PID only; 5 s timeout |
| forced `taskkill /PID ... /T /F` | stop worker | only after bounded graceful timeout; exact owned PID |
| `process.wait()` | stop worker | 5 s bound; never Tk |
| browser open | worker | localhost dashboard only |

No broad Python, browser, Streamlit, or Ollama process enumeration or termination is
used. The separate-port smoke test started PID 17940 on port 8598, reached ready in
4.035 seconds, and stopped only that owned process.

## Cache and timeout policy

- Research token/cache session: 30 s.
- EGX calendar and safety flags: 60 s.
- Local Ollama reachability/model: 15 s.
- Rubix full coverage: coalesced background refresh; process liveness itself is polled
  directly and is not cached.
- Rubix SQLite busy wait: 750 ms.
- Rubix authentication/first-quote readiness: 90 s.
- Streamlit readiness: 45 s.
- Streamlit health request: 750 ms per poll.
- Local Ollama tags request: 3 s.
- Stop helper and process wait: 5 s each.

A failed refresh retains the previous successful value and marks it stale. Authentication
and current process-alive state are revalidated rather than cached indefinitely.

## Cancellation and error presentation

Stop/close sets the shared cancellation event before scheduling process cleanup. The
window remains alive while launcher-owned cleanup runs, then destroys Tk only after
startup/stop workers finish or the bounded close deadline expires. Workers never call
widgets, and `_refresh` exits immediately after destruction.

The UI shows concise messages such as “Rubix database busy”, “Waiting for Rubix
authentication”, and component-specific timeouts. Detailed sanitized exceptions and
phase timings go to `logs/rubix_launcher.log`; the main panel does not render tracebacks.

## Validation

- Focused launcher/config/single-instance suite: **92 passed**.
- Launcher, Rubix, one-click, AI Stock Analysis, AI Narrative: **508 passed**.
- Full repository suite in a main-equivalent environment: **1179 passed, 7 skipped**.
  The isolated worktree was seeded with a copy of main's ignored frozen market-data
  cache because worktrees do not inherit ignored runtime files; no source/provider
  behavior was changed.
- Real Tk responsiveness probe: passed.
- Separate-port Streamlit smoke: passed.
- `git diff --check`: passed.

Production, broker execution, and automatic execution remain disabled. No trading,
provider, AI calculation, operational Yahoo policy, Rubix collection, or EODHD research
calculation was changed. No runtime database/history was deleted.
