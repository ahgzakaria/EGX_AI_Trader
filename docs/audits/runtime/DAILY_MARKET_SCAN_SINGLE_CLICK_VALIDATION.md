# Daily Market Scan — Single-Click Validation

Evidence that one click now starts exactly one scan, shows it while it runs, and
adopts its result automatically.

**No production scan was run for this proof.** The scanner is replaced by an
injected runner that emits the real progress events and contacts nothing: no
provider, no archive, no database, no Rubix, no scheduled task.

---

## 1. What was proved

The controlled run drives the real page functions (`resolve_page_workspace`,
`discover_job`, `_adopt_finished_job`) and the real `ScanJobRegistry` through one
complete lifecycle shaped like the observed incident (241 symbols, 194
successful).

| Claim | Result |
|---|---|
| one workspace key | `file:…922142::dashboard::file:…922776::rubix` |
| key stable across 5 reruns | ✅ |
| key survives a `chdir` elsewhere in the process | ✅ |
| first click created a job | ✅ |
| job id | `a3773499e137` |
| **second rapid click created a job** | **❌ — attached to the same job** |
| active jobs for the workspace | **1** |
| rerun reattaches to the same job | ✅ |
| progress visible while running | `SCANNING · 241/241 · 194 successful` |
| adoption attempted while running | refused |
| recovers after total session-state loss | ✅ |
| duplicate scan after state loss | **none** |
| workers started | **1** |
| final state | `COMPLETED_WITH_GAPS` |
| final counts | 241 total, 194 successful |
| result adopted automatically | ✅ (194 rows) |
| second adoption | refused |
| **clicks required** | **1** |
| **jobs created** | **1** |
| later scan allowed after adoption | ✅ |

Counts note: the injected runner reports non-successful symbols with the typed
status `INVALID_HISTORY`, which the outcome classifier buckets as *skipped*
rather than *failed*. The archived incident runs recorded the same 47 symbols as
`failed_symbols` in run metadata. The bucket labels differ; the symbol counts do
not, and neither is changed by this work.

## 2. Two rapid clicks

Simulated by calling `start_scan_job` twice in immediate succession against the
same workspace:

```
first  click : created=True   scan_id=a3773499e137
second click : created=False  scan_id=a3773499e137   (same job)
REGISTRY.active_count() == 1
```

The invariant is enforced by the registry under one lock, not by the disabled
button — a page that fails to disable the button still cannot create a second
scan.

## 3. Recovery after session-state loss

`st.session_state` was cleared entirely mid-scan, simulating a browser refresh:

```
resolve_page_workspace()   -> same key restored
discover_job(key)          -> the SAME running job
start_scan_job(...)        -> created=False, attached to the running job
```

No scan was restarted because the page reran.

## 4. Mutation verification

Each protection was individually reverted and the suite re-run. All six are load
bearing — none is decoration:

| Reverted protection | Outcome |
|---|---|
| workspace key resolved against CWD again | 1 failed |
| configuration failure degrades to `"unknown"` | 3 failed |
| adoption consumes before the result exists | 1 failed |
| poller stops at the first terminal reading | 1 failed |
| page recomputes the key every render | 1 failed |
| second worker may be started | 1 failed |

Baseline and restored state: **128 passed**.

## 5. Test coverage

`tests/test_daily_scan_job_state.py` — 48 tests:

* **Workspace key** — identical from any CWD; relative source resolved from the
  repository root; junction aliases collapse to one identity; two files with
  identical bytes do not; configuration failure raises instead of becoming
  `"unknown"`; an unconfigured provider and an unknown purpose are refused; a
  deliberate provider change produces a new key; a scan cannot start in an
  unidentifiable workspace; no volatile value in the key.
* **Concurrency** — eight simultaneous creates yield one job; exactly one worker
  claim succeeds; `start_scan_job` starts exactly one worker for two rapid
  clicks; different workspaces run independently; a completed job stops blocking
  once adopted; failed and cancelled jobs report themselves honestly.
* **Session state** — key survives reruns; active job rediscovered after refresh;
  finished unconsumed job rediscovered after refresh; no duplicate scan after
  session-state loss; configuration failure surfaced, not papered over.
* **Result adoption** — a terminal job without its result is not consumed; a
  result arriving later is still adopted; adopted exactly once; a second observer
  sees the rows without repeating downstream work; an active job is never
  adopted; a failed run is settled without publishing an empty scan; a persistent
  stall is reported and still not consumed; the pending counter resets.
* **Page wiring** — the key is resolved once and reused; job state is read once
  per render; the button is disabled while a result is pending; the poller stays
  mounted at the mount site; the running panel shows job id, start time,
  processed/total and success/failure counts; the page never returns early while
  a result is owed.
* **Boundaries** — no test touches Rubix, ORB, a scheduled task or a production
  database; no test sleeps; every job is created with an explicit `autostart`,
  and `autostart=True` only ever against an injected runner.

## 6. Suite totals

| Suite | Result |
|---|---|
| `test_daily_scan_job_state.py` | 48 passed |
| `test_scan_job_manager.py` + `test_scan_terminal_handoff.py` + `test_clean_dashboard_ui.py` | 85 passed |
| Full suite | see the final report |

No test sleeps, and no pytest process is left running: the single worker is
joined with a timeout and asserted dead before the test returns.

## 7. What this proof does not cover

* **A real 241-symbol production scan was deliberately not run.** The brief
  forbids running two full production scans to prove the fix, and the failure
  was in job identity and result handoff — neither of which depends on real
  provider data. The injected runner exercises the same registry, the same
  progress events and the same adoption path.
* **A real browser was not driven.** The page functions were called directly with
  a Streamlit stand-in. The tkinter/Streamlit rendering layer itself is
  unchanged apart from the assertions listed above.
* **Streamlit process restart** is still not recoverable — the registry is
  in-memory and per-process. See the audit's limitations section.
* Scan strategy, thresholds, provider selection and result calculations are
  untouched by this work.
