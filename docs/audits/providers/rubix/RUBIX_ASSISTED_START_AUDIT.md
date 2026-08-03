# Rubix Assisted Start — Audit of the Current Launcher

Read-only audit of the existing startup path, performed to design a
collector-only assisted mode. **No collector was started or stopped.**

---

## 1. How the user selects the auth frame today

`RubixAuthenticationAssistantUI` renders a row labelled
**"Fresh auth-frame file (Browse only)"** wired to `_select_auth_file`, which
opens `filedialog.askopenfilename`. The entry is deliberately
`state="readonly"`:

> *"The launcher accepts a path, never pasted session content. Read-only input
> prevents accidental on-screen secret exposure."*

The UI states plainly that *"Authentication data is selected for this launch
only and is never saved or shown in logs."*

**Design consequence:** the assisted mode must also accept only a *path*, never
frame content, and must never render the JSON.

## 2. How freshness is validated

`services/rubix_auth_assistant.inspect_auth_frame(path, *, now, max_age_minutes,
max_size_bytes)` reads the file in memory and returns `AuthFrameInspection`
carrying **only non-secret metadata**:

| Field | Meaning |
|---|---|
| `status` | `AUTH_VALID` / `AUTH_EXPIRED` / `AUTH_INVALID` |
| `structure_valid`, `structure_reason` | shape check |
| `file_mtime_utc`, `file_age_seconds`, `file_age_valid` | filesystem age |
| `embedded_timestamp_found`, `embedded_timestamp_utc`, `embedded_age_seconds` | **the frame's own timestamp** |
| `validation_decision`, `validation_reason` | the outcome |

The decision is:

```python
status = AUTH_VALID if valid and file_age_valid else (
    AUTH_EXPIRED if valid else AUTH_INVALID)
```

So **structure and age are independent**: a well-formed but old frame is
`AUTH_EXPIRED`, a malformed one is `AUTH_INVALID`. `validate_auth_frame_or_raise`
raises unless `AUTH_VALID`.

The module's contract line is explicit:

> *"This module never authenticates, controls a browser, or persists frame
> data. It only validates a user-selected temporary JSON file in memory…"*

**Design consequence:** the assisted mode must call this validator and must not
reimplement any part of it. `embedded_timestamp_utc` is the authoritative
freshness signal; the filesystem mtime is supporting evidence only.

### 2a. The real frame is **not** JSON — a defect this audit caught

The module's own prose says *"temporary JSON file"*, and an early version of the
assisted inbox believed it, accepting only `.json`. Inspecting the actual
production frame disproved it:

| Property | Observed |
|---|---|
| Path | `C:\secure-temp\rubix-price-auth-frame.txt` |
| Size | 741 bytes |
| First character | `1` |
| `parses_as_json` | **False** |
| `structure` (official validator) | **`PRICE_AUTH_DELIMITED`** |
| `structure_valid` | True |
| `embedded_timestamp_found` | **False** |

The genuine format is a delimited envelope — tag/value `0x02`, record separator
`0x1c` — which `_classify_delimited_structure` recognises by its required tags
(`150=1`, `24=30`, `1000=1`, `62=WEB`, a session field of at least 16
characters, and the `9/132/92/50/99` set).

Two consequences, both now implemented:

1. `FRAME_SUFFIXES = {".json", ".txt"}`. A `.json`-only inbox would have
   rejected **every genuine frame** and the assisted window would have waited
   forever on a valid morning. The extension stays a cheap pre-filter and never
   a decision: `inspect_auth_frame` remains the sole authority, so a `.txt` of
   nonsense is still refused (`test_the_extension_alone_cannot_bypass_validation`).
2. For this format `embedded_timestamp_found` is **False**, so mtime is the
   *only* freshness signal available. The UI reports `timestamp_source` honestly
   rather than implying an internal stamp it does not have. Since the export
   writes the file at capture time, mtime is a faithful proxy here — but it is
   labelled as what it is.

## 3. How the supervisor is started

`start_collector(auth_frame, symbols, batch_size)` builds exactly:

```
<venv python> scripts/rubix_collector_supervisor.py
    --adapter <adapter dir>  --auth-frame-file <frame>
    --database <rubix_live_market.db>  --symbols <universe>
    --batch-size 100  --pid-file <...>  --lock-file <...>  --log-file <...>
```

spawned with `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, `cwd = PROJECT_ROOT`.
Before spawning it calls `supervisor_status(SUPERVISOR_PID_FILE)` and raises
`InstanceAlreadyRunning` rather than attempting a doomed second launch; a
dead-PID record is cleared as stale metadata.

**Design consequence:** the assisted mode reuses this command shape and this
pre-check verbatim.

## 4. Why the existing buttons also start the Dashboard

Both of them do — this is the core reason a new mode is needed.

- **"Start Rubix & App"** → `start()`, documented as *"Begin the whole
  Rubix/Streamlit sequence in one cancellable worker."*
- **"Start Research Only"** → `start_research_only()`, which calls
  `self.supervisor.start_streamlit()`. Despite the name, "Research Only" means
  *EODHD instead of Rubix* — **it still launches Streamlit**, and in fact starts
  no collector at all.

So today there is **no button that starts the collector and nothing else**.
That gap is exactly what the assisted mode fills, and it is why the ORB
automation contract (no Dashboard on the scheduled path) cannot be satisfied by
the existing UI.

## 5. Current single-instance protections

Three layers, all preserved unchanged:

1. **`SingleInstanceLock`** (`scripts/launcher_process_utils.py`) — an atomic OS
   file lock held for the process lifetime, released by the OS on exit *or*
   crash. `acquire()` raises `InstanceAlreadyRunning` immediately when another
   live process holds it. The PID/metadata JSON is *"for diagnostics/status
   only — never the primary guard"*.
2. **Launcher pre-check** — `supervisor_status()` reads metadata
   non-destructively (verifying PID liveness *and* recorded start time, so a
   reused PID is never mistaken for the supervisor) and refuses to spawn a
   second supervisor.
3. **`claim_pid_file(LAUNCHER_PID_FILE)`** — prevents two launcher UIs.

The supervisor itself acquires the lock in `run()` and logs
`supervisor_duplicate_blocked` when refused.

**Design consequence:** the assisted mode adds no fourth lock. It calls
`supervisor_status()` first and lets `SingleInstanceLock` remain the guarantee.

## 6. What the assisted mode therefore is

A thin, collector-only front end over machinery that already exists:

| Concern | Owner |
|---|---|
| Frame validation | existing `inspect_auth_frame` — reused, not reimplemented |
| Duplicate prevention | existing `SingleInstanceLock` + `supervisor_status` |
| Websocket, adapter, subscriptions, reconnect | existing supervisor — untouched |
| Source health | existing `check_rubix_collector_readiness` |
| **New** | inbox watching, frame *selection* rules, collector-only launch, post-start health gate, one-click / opt-in countdown UI |

Nothing in the new code authenticates, opens a socket, stores a credential, or
launches Streamlit.

## 7. The remaining human action, unchanged

The user still performs the **existing official authentication/frame-export
step** in their browser and saves the frame file. The assisted mode removes
everything *around* that: no file dialog, no adapter/database re-entry, no
Dashboard, no second window — the frame is detected automatically and one
button (or an opt-in countdown) starts the collector.

The authentication contract is not bypassed, weakened or automated.

## 8. Source identity: `D:\EGX_AI_Trader` vs `F:\EGX_AI_Trader`

Two spellings of the project root are in circulation. If they were two different
copies of `rubix_live_market.db`, the collector could be filling one database
while ORB read another — the whole shadow session would be silently empty, or
worse, split across two recorded source identities.

**Verdict: `SAME_PHYSICAL_SOURCE`.** Established with Windows-native identity
evidence, not by comparing sizes, hashes, timestamps or path text:

| # | Evidence | Result |
|---|---|---|
| 1 | Reparse point on `D:\EGX_AI_Trader` | Junction, tag `0xa0000003`, Substitute Name `\??\F:\EGX_AI_Trader` |
| 2 | Windows File ID (`FileIndex`) of `.db`, `-wal`, `-shm` | Identical through both spellings (`…4a16`, `…4a18`, `…4a17`) |
| 3 | `os.stat` | Identical `st_dev=2216550835671079363`, `st_ino=1125899906861590` |
| 4 | Read-only `max(id)` sampled twice through both paths | Identical (`3,962,037`), advancing together |
| 5 | Supervisor invoked with a `D:` path | Recorded `F:` in its own PID file |

Differing volume serial numbers for `D:` and `F:` are expected and not
contradictory — a junction is allowed to cross volumes; the File ID and
`st_dev`/`st_ino` pair, not the drive letter, decide file identity.

### The canonical source

**`F:\EGX_AI_Trader\data\rubix_live_market.db`** — what ORB already reads,
what the supervisor's PID file records, and what the collector child writes.

### Why no run-splitting can occur

`_path_identity` in `scripts/run_orb_shadow_session.py` hashes
`Path.resolve()`, and `resolve()` collapses the junction on Windows. Verified
directly:

```
D:\...\rubix_live_market.db -> F:\...\rubix_live_market.db   identity e15d1f79…
F:\...\rubix_live_market.db -> F:\...\rubix_live_market.db   identity e15d1f79…
```

Either spelling therefore records **one** `source_path_identity`. This is
pinned by `test_same_physical_source_is_accepted` (a real `mklink /J` junction,
not a stand-in) and `test_distinct_sources_get_distinct_identities`; removing
the `resolve()` call makes both fail.

The database was **never copied** to resolve this — copying would have created
the exact divergence the gate exists to prevent.

## 9. Code location vs runtime data location — a second defect this caught

The scheduled task runs from a dedicated runtime worktree (the pattern the ORB
automation task already uses). The first implementation derived every runtime
path from the *script's own location*:

```python
parser.add_argument("--pid-file",
                    default=str(PROJECT_ROOT / "data" / "rubix_supervisor.pid.json"))
```

Installed at a runtime worktree, that would have produced:

| Path | Would have been | Must be |
|---|---|---|
| database | `…assisted_runtime_wt\data\rubix_live_market.db` (empty) | `F:\EGX_AI_Trader\data\rubix_live_market.db` |
| PID file | `…assisted_runtime_wt\data\rubix_supervisor.pid.json` (absent) | `F:\EGX_AI_Trader\data\rubix_supervisor.pid.json` |
| lock file | worktree copy | canonical |

The database consequence is a split source — the exact failure section 8 exists
to prevent. The PID/lock consequence is worse: `supervisor_status` would have
consulted a file the live supervisor never writes, reported `running: False`,
and the assisted window would have **spawned a second collector beside the
running one**. `SingleInstanceLock` would still have blocked it, but only after
a spawn, and the UI would have reported a start that did not happen.

**Fix:** `--runtime-root` separates the two concerns. Code location keeps
supplying `scripts/rubix_collector_supervisor.py`; the runtime root supplies
`data/` and `logs/` — database, universe, PID, lock, log and the inbox. The
installer requires `-RuntimeRoot` and refuses one without a real
`data\rubix_live_market.db`.

Verified against the live system, read-only:

```
--runtime-root F:\EGX_AI_Trader
  pid file : F:\EGX_AI_Trader\data\rubix_supervisor.pid.json
  supervisor_status -> running=True, pid=11120
  => INSTANCE_ALREADY_RUNNING, nothing spawned
```

Pinned by `test_runtime_paths_follow_the_runtime_root_not_the_code_location`
and `test_the_installer_refuses_a_runtime_root_without_the_real_database`;
reverting either protection fails a test.

## 10. Watching the existing frame path instead of a repository inbox

The first implementation watched a new directory,
`data/local/rubix_auth_inbox/`, and asked the user to save the frame there.

That was an implementation proposal mistaken for a requirement. The real
workflow has always exported to **`C:\secure-temp\rubix-price-auth-frame.txt`** — the same file the launcher's
own `-AuthFrameFile` argument has always received, and the same file this audit
inspected in section 2a. Introducing an inbox therefore added a manual copy
step to a workflow that never had one, for no gain.

**Corrected:** the assisted mode now reads that file in place.

| Concern | How it is handled |
|---|---|
| copy | never — the path is passed straight to the supervisor |
| move / rename | never |
| delete | refused outright in this mode, even with `--disposal DELETE` |
| contents | never displayed, logged, persisted or summarised |
| freshness | `inspect_auth_frame` re-run every poll, so a re-export is picked up next pass |
| missing file | `WAITING_FOR_FRESH_AUTH_FRAME` — the normal 09:10 state, not an error |

The four display states (`WAITING_FOR_FRESH_AUTH_FRAME`, `AUTH_FRAME_VALID`,
`AUTH_FRAME_EXPIRED`, `AUTH_FRAME_REJECTED`) are derived from the validator's
own `VALID`/`EXPIRED`/`INVALID` constants, imported rather than spelled out —
a hardcoded guess at those strings silently degraded every expired frame to
"waiting", hiding the one state the user can act on. Caught by running the
detector against the real file.

The inbox remains available as `--watch-mode inbox` for testing and future use.
It is not required, not installed, and not part of the normal morning.
