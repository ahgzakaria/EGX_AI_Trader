# Daily Market Scan — Job State Audit

Why one click on **فحص السوق اليومي** required a second click, and why the first
scan's results were invisible even though they had been computed and archived.

**Research only.** Nothing here changes scan strategy, thresholds, providers or
any result calculation.

---

## 1. Confirmed evidence

Two scans ran **concurrently inside one application process** (PID 31796):

| | `RUN_20260803_223656` | `RUN_20260803_223829` |
|---|---|---|
| started | 22:36:56 | 22:38:29 |
| completed | 22:45:00.291 | 22:45:00.241 |
| execution time | 483.7 s | 391.2 s |
| symbols | 241 | 241 |
| successful | 194 | 194 |
| non-successful | 47 | 47 |
| status | COMPLETED | COMPLETED |

The second scan began **93 seconds after the first, while the first was still
running**, and both produced `scan_results.csv` of identical size with identical
counts.

That is decisive on two points:

1. **The first scan worked.** It read 241 symbols, produced 194 rows and wrote
   its archive. The data path was never at fault.
2. **The UI lost the job.** The Run button was offered again while a scan owned
   the workspace, and the second click created a second job instead of
   attaching to the first.

Both runs are preserved unmodified as evidence. Nothing in this change deletes,
rewrites, merges or re-archives them.

## 2. Root cause

### 2.1 The workspace key was not deterministic

`workspace_key_for()` is what ties a page render to the job that owns its
workspace. It computed:

```python
resolved_source = str(Path(source).resolve())        # CWD-dependent!
...
except Exception:
    provider_mode = "unknown"                        # silent degradation
```

Two independent ways to produce a *different key for the same workspace*:

* **`SYMBOL_SOURCE` is relative** — `data/universe/egx_universe.csv`.
  `Path(source).resolve()` anchors a relative path to the **process working
  directory**, so any `chdir` in the process silently changes the key.
  Reproduced directly: changing CWD changed the key.
* **Configuration failure became a value.** Any exception while reading the
  provider setting was swallowed and replaced with the literal `"unknown"`,
  which is a perfectly valid-looking key component — for a different workspace.

The docstring claimed every component was "stable across reruns". Neither of
these was.

### 2.2 The page recomputed the key on every render

`show_dashboard` called `workspace_key_for(...)` fresh on each run and never
retained the result. One render's answer therefore did not have to match the
next one's — and when it did not, `REGISTRY.get(key)` returned `None`:

* no progress panel and no polling fragment were mounted;
* `active` was `False`, so the Run button re-enabled;
* `st.session_state.results` stayed `None`, so the page showed the empty state.

That is exactly the reported experience: *"the first time it completes a scan
and does nothing."*

### 2.3 Result adoption consumed the job before checking for a result

```python
if st.session_state.get(TERMINAL_CONSUMED_KEY) == job.scan_id:
    return
st.session_state[TERMINAL_CONSUMED_KEY] = job.scan_id     # claimed
...
result = job.final_result
if result is None:
    return                                                 # ...and lost forever
```

The worker assigns `final_result` and only *then* publishes its terminal state.
A reader arriving between those two moments saw a finished job carrying nothing,
burned the idempotency key, and no later render could ever adopt that scan. The
rows existed on disk and were unreachable in the app.

### 2.4 The page disagreed with itself within one render

`job.is_active` was read three times per render — once for `active`, once inside
`_render_scan_job`, once for the early return. A job finishing mid-render could
take the terminal branch in the panel (so no poller was mounted) while the early
return still saw the stale `True` — leaving a frozen page with nothing polling
and no results.

## 3. Old lifecycle

```
click -> start_scan_job -> rerun
             |
             v
  render: key = f(CWD, settings)   <-- may differ from the running job's key
             |
      +------+------------------------------+
      |                                     |
  key matches                          key differs
      |                                     |
  progress + fragment              job = None: no progress,
      |                            button re-enabled, no results
      v                                     |
  terminal reading                          v
      |                            second click -> SECOND CONCURRENT SCAN
      v
  _adopt_finished_job
      |
  mark consumed -> read final_result -> None -> result lost permanently
```

## 4. Fixed lifecycle

```
click -> start_scan_job (raises if the workspace cannot be identified)
             |
             v
  render: workspace resolved ONCE, key kept in st.session_state
             |
             v
  discover_job(key)  <-- registry is the source of truth
             |
      job active OR result_pending
             |
             v
  progress panel + polling fragment stay mounted
             |
             v
  terminal AND final_result present
             |
             v
  job.claim_result(observer)   <-- atomic, in the registry
             |
      +------+------+
      | won         | lost
      v             v
  store rows    store rows, skip one-time downstream work
      |
      v
  results render automatically; button re-enables
```

## 5. Workspace identity contract

`resolve_workspace(purpose, source)` returns a typed, frozen `ScanWorkspace`:

| Field | Source | Why it belongs |
|---|---|---|
| `repository_identity` | `st_dev`/`st_ino` of the repository root | a worktree and the main repo must never share a job; `D:` and `F:` spellings of one junctioned repo must |
| `purpose` | caller | dashboard / scanner / forward_testing are separate workspaces |
| `source_identity` | `st_dev`/`st_ino` of the universe file | two names for one file are one universe; two files with identical bytes are not |
| `provider_mode` | validated settings value | a different operational provider is a genuinely different workspace |

Guarantees:

* **No CWD dependence.** A relative universe path is resolved against
  `repository_root()`, derived from the module's own location.
* **Physical identity, not path text.** File identity collapses the
  `D:\EGX_AI_Trader` junction onto `F:\EGX_AI_Trader`; identical *content* in two
  different files never collapses.
* **No silent degradation.** A configuration failure raises
  `WorkspaceConfigurationError`. The page shows it and **refuses to start a
  scan**. A guessed workspace is what allowed two concurrent scans.
* **Nothing volatile.** No session id, tab id, widget state or timestamp.

## 6. Result-adoption contract

`_adopt_finished_job(job)` returns `True` only when it adopted, and in this
order:

1. **Terminal?** An active job is never adopted.
2. **Result present?** `job.result_pending` — terminal, no result yet, and a
   result is still owed — returns without consuming anything, so the next poll
   retries. `FAILED` is excluded: a worker that raised owes nothing, and waiting
   forever would strand the operator with a permanently disabled button.
3. **Claim atomically.** `job.claim_result(observer)` succeeds for exactly one
   observer, under the job's lock, and only when a result actually exists.
4. **Store**, then mark consumed — never the reverse.
5. A second observer that loses the claim still renders the same rows; it simply
   does not repeat the one-time decision-support snapshot.

Bounded diagnostic: after `MAX_PENDING_RESULT_POLLS` (40) consecutive polls of a
terminal job with no result, the page surfaces a warning naming the scan instead
of spinning silently. The job is still not consumed.

## 7. Single-active-job invariant

Enforced in the registry and the job, **not** by the disabled button:

* `create_or_get_active_job` performs check-and-insert under one lock; eight
  simultaneous callers receive one job and exactly one `created=True`.
* `job.claim_worker()` grants the right to run the worker to exactly one caller,
  so the single-worker property belongs to the job rather than to one call site.
* `REGISTRY.active_job(key)` and `REGISTRY.unconsumed_job(key)` let a refreshed
  page rediscover a running or finished-but-unadopted scan.
* A completed job stops blocking new scans once its result is adopted.

## 8. Remaining limitations

* **The registry is per-process and in memory.** Restarting Streamlit loses
  knowledge of a running scan; a worker from the previous process is not
  adoptable. Its archive on disk is still complete.
* **`REGISTRY` retains terminal jobs for 45 minutes** (`TERMINAL_JOB_TTL_SECONDS`).
  A refresh after that window will not rediscover a finished scan, though its
  rows remain in session state if they were adopted.
* **The workspace key changes if the universe file is replaced** (a new file has
  a new file id). That is deliberate — a different universe is a different
  workspace — but it means an in-flight scan is not rediscoverable across a
  universe swap.
* **`FAILED` runs publish no rows.** They are settled honestly so the operator
  can retry; the failure text is shown but there is nothing to adopt.
* This audit does not change scan strategy, thresholds, provider selection or
  any result calculation.
