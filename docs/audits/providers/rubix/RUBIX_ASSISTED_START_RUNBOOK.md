# Rubix Assisted Start — Operating Runbook

The morning sequence, reduced to one human action.

**Collector only. No Dashboard. No trading execution. No credential storage.**

---

## 1. What changed

Before: open the launcher GUI, browse for the auth frame, re-confirm adapter and
database paths, press a button that also starts Streamlit.

Now: a small window opens by itself, watches a folder, validates whatever frame
appears using the existing official validator, and starts **only** the headless
collector supervisor.

**The human action is unchanged and unautomated:** perform your normal Rubix
authentication/frame export and save the frame file into the inbox. In
production that file is `rubix-price-auth-frame.txt` — a delimited envelope,
**not** JSON. Nothing here
drives a browser, logs in, or stores a credential.

## 2. Timeline

| Time (Cairo) | Actor | What |
|---|---|---|
| 09:10 | scheduled task | assisted window opens |
| 09:10–09:25 | **you** | export the auth frame into the inbox |
| ~09:15 | assisted window | detects, validates, shows age and remaining validity |
| one click | **you** | **START RUBIX COLLECTOR** (or opt-in countdown) |
| before ~09:30 | assisted window | confirms the source is advancing, then minimises |
| 09:40 | ORB task | pre-check and Shadow workflow — unchanged |
| 14:15 | ORB | Lane A stops |
| post-session | ORB | Lane B, comparison, report |

If you do nothing, the window simply times out. **ORB still fails closed** with
`SKIPPED_SOURCE_UNAVAILABLE` — nothing false is ever recorded.

## 3. The inbox

Default: `data/local/rubix_auth_inbox/` — gitignored, along with the consumed
folder and the preference file.

Path rules, enforced:

- must resolve **inside the project root** — traversal is refused;
- may not be the project root, nor a top-level `data`, `logs`, `reports`,
  `backups`, `venv` or `.git` directory;
- must be a directory;
- only regular `.json` and `.txt` files are considered — `.txt` because the
  real production frame is `PRICE_AUTH_DELIMITED`, not JSON; the extension is
  only a pre-filter and never grants acceptance;
- symlinks/junctions resolving outside the inbox are skipped;
- `.tmp`, `.crdownload`, `.part`, `.partial`, `.download` are ignored entirely,
  so a half-written download is never read as a malformed frame.

## 4. Frame selection rules

Validity comes from the existing `inspect_auth_frame` — **nothing is
reimplemented**. A `.txt` containing nonsense is refused exactly like a
malformed `.json`.

**Freshness signal, by format:**

| Format | Timestamp source |
|---|---|
| JSON frame with an internal stamp | the frame's own timestamp — authoritative |
| `PRICE_AUTH_DELIMITED` `.txt` (the production format) | **file mtime only** — this format carries no embedded timestamp |

The window states which one it used (`timestamp_source`) rather than implying a
stamp that is not there. Where an internal stamp exists it wins, because a copy
or a sync client rewrites mtime freely.

| Situation | Result |
|---|---|
| no valid frame | `WAITING_FOR_FRESH_AUTH_FRAME` |
| exactly one valid | selected automatically |
| several valid, one clearly newest | newest selected |
| several within ~2 s, or one lacking a usable timestamp | `AMBIGUOUS_REQUIRES_USER_SELECTION` |

Ambiguity is never resolved by guessing: silently picking one could start a
session against the wrong credential.

The window shows filename, validated timestamp, timestamp source, current age,
remaining validity, validation result and rejection reason — and **never any
field from inside the frame**, in either format.

## 5. One-click and opt-in auto-start

**One-click is the default.** When a frame is valid, the large
**START RUBIX COLLECTOR** button enables.

**Auto-start is opt-in only** (`--auto-start`, or `-EnableAutoStart` at install
time). It shows a visible 5-second countdown with a Cancel button and aborts
immediately if the frame expires or the selection becomes ambiguous mid-count.
It never fires from an expired, malformed or ambiguous frame, and it is never
enabled silently.

## 6. What actually starts

Exactly the launcher's own supervisor command:

```
<python> scripts/rubix_collector_supervisor.py
    --adapter … --auth-frame-file … --database … --symbols …
    --batch-size 100 --pid-file … --lock-file … --log-file …
```

**Not started:** Streamlit, the Dashboard, another launcher UI, another
websocket, ORB Shadow, any broker or trading component.

If a supervisor is already running, the window shows `INSTANCE_ALREADY_RUNNING`
and spawns nothing — `supervisor_status()` is consulted before any spawn, and
`SingleInstanceLock` remains the real guarantee.

## 7. Post-start health

| State | Meaning |
|---|---|
| `STARTING` | launched; waiting for the source |
| `RUNNING_HEALTHY` | supervisor live **and the source is advancing** |
| `RUNNING_SOURCE_STALE` | process is up but the feed is not writing |
| `AUTH_FRAME_REJECTED` | no usable frame |
| `INSTANCE_ALREADY_RUNNING` | reused, nothing spawned |
| `START_FAILED` | spawn raised |
| `HEALTH_TIMEOUT` | never became healthy inside the bounded window |

**A running process alone is never `RUNNING_HEALTHY`.** Health requires source
progress, which is the only evidence the collector is doing its job rather than
merely existing. This reuses the read-only readiness checker's output.

## 8. Auth-frame disposal

**Default: `LEAVE_UNTOUCHED`.** The existing security contract treats the frame
as a short-lived file the user owns; moving or deleting it by default would mean
this code taking custody of credential-adjacent material it has no business
handling.

Two explicit opt-ins: `MOVE_TO_CONSUMED` (relocates into an ignored folder —
moved, never copied) and `DELETE`. The frame is never copied elsewhere, never
logged, never committed, and never retained as a reusable credential.

## 9. Install the schedule

```powershell
.\scripts\windows\install_rubix_assisted_start_task.ps1 `
    -WorktreePath "F:\EGX_AI_Trader" `
    -PythonwExe   "F:\EGX_AI_Trader\venv\Scripts\pythonw.exe" `
    -AdapterPath  "<rubix adapter directory>" `
    -WhatIfOnly
```

Drop `-WhatIfOnly` to register; add `-EnableAutoStart` to opt in to the
countdown. **Not installed by this change** — registration is a deliberate step.

Task: **09:10 Cairo, Sunday–Thursday**, current user, Interactive logon,
`RunLevel Limited`, `MultipleInstances IgnoreNew`, `WakeToRun`,
`StartWhenAvailable`, bounded runtime, no stored password, no session date. The
installer refuses a target that invokes Streamlit, one that does not build the
supervisor command, an unresolvable Cairo timezone, or a same-named task that is
not ours.

Remove with `remove_rubix_assisted_start_task.ps1` — unregisters only, deleting
no frame, database, log or report.

## 10. Manual fallback

The official launcher (`scripts\start_rubix_production.bat`) is unchanged and
still works. Use it if the assisted window is unavailable — but note its
"Start Rubix & App" also launches the Dashboard, and "Start Research Only"
starts Streamlit without a collector.

## 11. Limitations

- **Still requires a logged-in interactive session** — the frame export is a
  human, browser-side action.
- **Still requires the human export step.** This removes everything around it,
  not the step itself.
- The 15-minute frame lifetime is unchanged; a slow morning means recapturing.
- Auto-start shortens the click, not the export.

## 12. The Rubix source database

**Canonical path: `F:\EGX_AI_Trader\data
ubix_live_market.db`.**

`D:\EGX_AI_Trader` is a **junction** onto `F:\EGX_AI_Trader`, so both spellings
are one physical database — confirmed by the reparse tag, identical Windows File
IDs, identical `st_dev`/`st_ino`, and a `max(id)` that advances together through
both. See section 8 of the audit for the full evidence.

Practically:

- either spelling may be configured; `Path.resolve()` collapses the junction, so
  ORB records **one** `source_path_identity` and runs are never split;
- health checks and ORB read the same file the collector writes;
- **never "fix" a path mismatch by copying the database.** A copy would create a
  second, diverging source — the exact failure this check exists to catch. If
  the two ever stop resolving to one file, stop and re-run the identity checks.
