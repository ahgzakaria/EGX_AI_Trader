# Rubix Launcher — Config / Runtime State Separation

**Date:** 2026-07-26
**Branch:** `chore/rubix-launcher-state-separation`
**Base:** `0d0934461bb36857bb316ccae768e18982e1b670`

## The problem

The launcher kept application defaults, machine-specific settings and volatile session
state in **one tracked file**, `config/rubix_launcher_settings.json`, and wrote the whole
dictionary back on every action:

```
_save_preferences()  →  save_launcher_preferences(self.preferences, LAUNCHER_CONFIG)
```

Simply opening and closing the launcher therefore rewrote a tracked file — new window
position, a new `recent_launches` record, reordered keys — leaving a permanent `M
config/rubix_launcher_settings.json` in `git status`. It reappeared after every launch, was
stashed and restored repeatedly, and had to be reasoned about before every merge. Runtime
state is not source code and must never dirty the repository.

Writes came from six call sites (`_select_auth_file`, self-check, Rubix start, research
start, stop, close) plus `_record_launch`, all funnelling through the single writer above.

## Key classification

Classified by how the code actually uses each key, not by name.

| Key | Class | Evidence in code | New home |
| --- | --- | --- | --- |
| `streamlit_port` | **A — tracked default** | `port_var` defaults to `8501`; a documented product default | tracked defaults (user change → local) |
| `theme` | **A — tracked default** | `theme_var` defaults to `"System"`; applied via `ttk.Style().theme_use` | tracked defaults (user change → local) |
| `adapter_path` | **B — local persistent** | set from a `filedialog.askdirectory` result; absolute machine path | local overrides |
| `database_path` | **B — local persistent** | absolute machine path; already env-overridable via `RUBIX_DB_PATH` | local overrides |
| `last_auth_folder` | **B — local persistent** | only used as `initialdir` for the auth-file dialog | local overrides |
| `window_geometry` | **C — volatile runtime** | written from `self.root.geometry()` on every `<Configure>` event | runtime state |
| `recent_launches` | **C — volatile runtime** | appended by `_record_launch` on each launch outcome; capped at 10 | runtime state |

No process IDs were ever stored in this file (the launcher keeps those in its own
`*.pid.json` metadata), so none were carried over as live processes.

## The three layers

| Layer | Path | Git |
| --- | --- | --- |
| Application defaults | `config/rubix_launcher_settings.json` | **tracked**, read-only at runtime |
| Local user/machine settings | `config/rubix_launcher_settings.local.json` | **ignored** |
| Volatile runtime state | `data/runtime/rubix_launcher_state.json` | **ignored** |

Implemented in `services/rubix_launcher_config.py`:

```
load_default_settings()      load_runtime_state()      migrate_legacy_settings()
load_local_overrides()       update_runtime_state()
resolve_effective_settings() save_window_geometry()
save_local_overrides()       append_recent_launch()
```

`.gitignore` covers the local file, its lock/temp siblings, and the whole
`data/runtime/` directory. The tracked defaults file is **not** ignored.

## Precedence

```
tracked defaults  →  local overrides  →  environment (where already supported)
```

`RUBIX_DB_PATH` is the only environment override, matching the launcher's pre-existing
behaviour. Runtime state sits outside this chain entirely and can never override a
setting: each layer is filtered through its own allow-list (`SETTING_KEYS` /
`RUNTIME_KEYS`), so an unknown key — or a settings key placed in the runtime file — is
dropped rather than silently becoming configuration.

All runtime writes target only the runtime-state file; all persistent user-setting writes
target only the local override file; **no runtime action writes the tracked defaults
file**, which is asserted by both a static source check and a byte-comparison test.

## Migration

`migrate_legacy_settings()` runs on launcher start and is idempotent:

* runtime keys are written **only when the runtime file does not yet exist**, so a second
  run cannot duplicate, reorder or truncate launch history, and cannot move the window;
* local settings are filled in **only for keys the local file does not already define**,
  so a later user choice is never overwritten;
* a value equal to the tracked default is not duplicated into the local file;
* the tracked defaults file is never rewritten.

Verified on the real preserved values: window geometry `1080x820+540+85` migrated intact,
all **10** recent-launch records byte-identical and in the same order, and
`adapter_path` / `database_path` / `last_auth_folder` moved to the local file while
`theme` and `streamlit_port` stayed as tracked defaults. Re-running the migration three
more times changed nothing (all three files byte-identical).

## Backup of the pre-split file

```
D:\EGX_AI_Trader_ARCHIVE_2026-07-25\runtime\config\rubix_launcher_settings_pre_split_2026-07-26.json
SHA-256: 2fae49ebfe7a27ddb7805810abe1fe48ce52684cc6b1f83731ee9367c65112a6
Size:    1484 bytes
```

Byte-identical to the dirty working copy at the moment of capture, and never modified
since. The tracked file was restored to `HEAD` only after this backup was verified.

## Failure recovery

* **Atomic writes** — reuses the project's existing `write_json_atomic` (temp file in the
  destination directory, `flush` + `fsync`, then `os.replace`). An interrupted write leaves
  the previous complete document in place and no partial destination; a test simulates a
  failure at the replace step and asserts exactly that.
* **Concurrency** — a per-path `threading.Lock` serializes writers inside the process
  (the Tk thread and worker threads both persist state) plus the project's existing
  cross-platform advisory file lock for a second launcher process. *A real defect was found
  here during testing:* `write_json_atomic` derives its temp filename from the PID, so two
  threads in one process collided on the same temp file; the in-process lock removes that
  race. No new dependency was added.
* **Malformed runtime JSON** — never crashes the launcher. The file is renamed to
  `rubix_launcher_state.json.malformed-<UTC timestamp>` for diagnosis, safe defaults are
  used, and a sanitized warning is returned that names the file and failure kind only —
  never file contents.
* **Missing directory** — created on demand.

## Recent-launch history

Ordering (oldest first, newest appended), retention (last **10**), timestamp meaning
(UTC ISO-8601, recorded when the launch finished) and the success/failure result strings
are all unchanged. Loading or resolving configuration never appends a record, and tests
write only to `tmp_path` — an autouse fixture fails the test run if any production
launcher file is touched.

## Tests

`tests/test_rubix_launcher_config.py` — 28 tests: defaults load, local precedence,
environment precedence, runtime separation, geometry/launch writes hitting only the runtime
file, user settings hitting only the local file, legacy migration without data loss,
idempotency (×3), no duplicate launch records, a later user choice not overwritten, unknown
keys dropped, malformed JSON quarantined without secrets in the warning, interrupted write
leaving no partial file, concurrent writers, missing directory creation, tracked defaults
containing no runtime or machine state, and static checks that neither the module nor the
launcher hands the tracked path to a writer.

Totals: launcher/Rubix suites **79 passed**; full repository suite **1032 passed, 0
failed, 0 skipped**.

## Proof that launching no longer dirties Git

Driving the launcher's own `_record_launch` and `_save_preferences` (a launch plus a moved
window, exactly what runs on close):

```
tracked defaults byte-identical : True
geometry persisted to runtime   : True   (1080x820+321+123)
launch appended to runtime      : True   (research/ready, still capped at 10)
git status                      : no tracked file modified by launcher activity
```

Unchanged: Rubix, EODHD, AI Narrative, providers, trading, strategy, portfolio and
production behaviour. Production remains disabled. `.env` remains present and ignored.
