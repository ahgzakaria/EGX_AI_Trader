# Windows Dataset Finalization Audit

**Date:** 2026-07-26 · **Branch:** `fix/windows-dataset-finalization` · **Base:** `0e78a48`
**Affected run:** `RUN_20260726_212722` (Dashboard Market Scan, 220 symbols)

## The failure

```
PermissionError: [WinError 5] Access is denied:
  'reports\\RUN_20260726_212722\\dataset.staging'
  -> 'reports\\RUN_20260726_212722\\dataset'

dashboard/home.py → core/scanner.py
  → services/experiment_tracking.py::complete
    → services/dataset_archive.py::finalize
      → os.replace(self.staging, self.destination)
```

## Pre-recovery folder state

| Item | State |
| --- | --- |
| `dataset.staging` | **present** — 891 files, 46,560,482 bytes |
| `dataset` | **absent** |
| `MANIFEST.json` | present, `status: COMPLETED`, 220 symbols archived, 444 records, 45 failures |
| `MANIFEST.json` SHA-256 | `d01aa70995897b5b8a0e22b612649933bbf287cfd70a180d796e498dfb9c5cf3` |
| `raw/` · `normalized/` | 448 files (22,703,957 B) · 440 files (22,335,445 B) |
| Temporary / lock / partial files | **none** |
| `scan_results.csv` | 220 rows — the scan itself completed |
| `failed_symbols.csv` | 45 rows |
| `run_metadata.json` | `status: RUNNING`, no `dataset_hash` (never updated) |

External backup taken before any edit:
`D:\EGX_AI_Trader_ARCHIVE_2026-07-25\failed-finalization\RUN_20260726_212722`
(903 files, 47,241,712 bytes, manifest SHA-256 identical).

## Root cause

The destination did **not** exist, so this was never a structural conflict. Two probes settle it:

1. **The identical tree renames fine.** A byte-for-byte copy of the 891-file staging
   directory was promoted with `os.replace` in **2.2 ms**, same volume, no error. Structure,
   contents, path lengths and filesystem are all sound.
2. **An open handle inside the tree reproduces the exact error.** With a file inside
   `dataset.staging` held open, `os.replace` on the directory fails with precisely
   `PermissionError [WinError 5]`; with the process CWD inside it, the error is WinError 32
   instead — so the failure signature matches *an open file handle*, not a locked CWD.

Windows refuses to rename a directory while any file inside it is open by **any** process.
Every writer in `dataset_archive.py` is context-managed, flushed and `fsync`-ed, so no
handle of ours survives — confirmed by audit and by probe 1. The holder was therefore an
external process (real-time antivirus / Search indexer / sync agent) that opened the
just-written `MANIFEST.json` — 1.5 MB, written milliseconds before the rename — or one of
the 891 freshly-written files.

That condition is **transient and self-clearing**. The defect is not who held the handle;
it is that `finalize()` treated a momentary lock as fatal:

- **no retry** for a transient, self-clearing error;
- **no idempotency** — `finalize()` raised `FileExistsError` whenever the destination
  existed, so re-running could never recover;
- **failure was fatal to the whole run** — the exception propagated before
  `_close_archive()`, before `run_metadata.json` was updated, and out through
  `scan_symbols()`, so the dashboard lost 220 already-scanned symbols and showed a
  traceback.

## Idempotent state machine

`finalize_run_directory()` now resolves four explicit states, and **never deletes or
overwrites user data** in any of them:

| Case | Condition | Outcome |
| --- | --- | --- |
| **A** | staging only | validate manifest → promote with bounded retry → `FINALIZED` |
| **B** | destination only | validate → `ALREADY_FINALIZED` (or `INVALID_MANIFEST`) |
| **C** | both, equivalent | keep destination, move staging aside → `ALREADY_FINALIZED` |
| **C** | both, destination incomplete + staging valid | quarantine destination, promote staging → `RECOVERED_FROM_STAGING` |
| **C** | both, genuinely different | change nothing → `CONFLICT_PRESERVED` |
| **D** | neither | `STAGING_MISSING` — no manifest is fabricated |

Equivalence compares `schema_version`, `dataset_hash`, `symbols_archived`,
`settings_sha256` and record count — identity, not 891 file reads. Quarantine renames to
`dataset.superseded-<UTC>` / `dataset.incomplete-<UTC>`; nothing is ever removed.

## Retry policy

Bounded and narrow: **5 attempts**, delays **0.1 / 0.2 / 0.4 / 0.8 / 1.0 s**, both
configurable. Only `PermissionError` is retried — on Windows that is WinError 5 (access
denied) and WinError 32 (sharing violation), the two "someone holds a handle" errors. Every
other error, and every structural condition, raises immediately: a conflict is never
resolved by waiting. Before **each** attempt the preconditions are re-verified — staging
still exists, destination still absent — so a destination appearing mid-retry aborts rather
than being overwritten. Exhaustion returns typed `TRANSIENT_LOCK_TIMEOUT` with the staging
directory fully intact.

## Handle-closing audit

| Writer | Handling |
| --- | --- |
| `atomic_json` (manifest, state marker) | `with` + `flush()` + `os.fsync()` + `os.replace` — **fsync added** |
| `_write_frame` (`.csv.gz`) | `with` + gzip context + `flush` + `fsync` |
| `_write_exact_frame` (`.npz`) | `with` + `flush` + `fsync` |
| `sha256_file`, `_read_frame`, `_read_exact_frame` | context-managed |
| `pandas.to_csv(path)` | pandas opens and closes its own handle |

No writer relies on garbage collection. Tests assert no `*.tmp` file survives into the
published dataset and that `ARCHIVE_STATE.json` is gone.

## Concurrency protection

One finalization per run: a per-run-directory `threading.Lock` (in-process) plus the
project's existing cross-platform advisory file lock (no new dependency). The lock file
lives at `data/runtime/dataset_finalize/<run>.lock` — **outside** the directory being
renamed, so holding it can never be what blocks the rename. A stale lock file is harmless:
the OS releases the advisory lock when its owner exits, and an unobtainable OS lock never
blocks the operation, because the in-process lock plus the idempotent state machine keep it
safe. The lock spans the manifest write *and* the promotion, so a second caller can never
write into a directory that is being renamed. Repeated `complete()` calls return the same
result.

## Typed result and dashboard behaviour

`FinalizationResult` carries `status`, `manifest`, `dataset_path`, `detail`, `attempts` and
`quarantined`, and still behaves as a read-only mapping over the manifest so existing
callers keep working. `complete()` no longer aborts on an archival fault: it records
`dataset_finalization`, `dataset_finalization_detail` and `dataset_finalization_ok`, and
writes every artifact. The dashboard reads those fields and shows one concise sentence —
results are complete, the archive was not published, nothing was lost, it can be published
again without rescanning. No traceback reaches the user; the full traceback stays in the log.

## Recovery of RUN_20260726_212722

| | |
| --- | --- |
| State before | `dataset.staging` present (891 files), `dataset` absent |
| Result | **`FINALIZED`** on attempt **1** |
| Final dataset path | `reports\RUN_20260726_212722\dataset` |
| Manifest SHA-256 before | `d01aa70995897b5b8a0e22b612649933bbf287cfd70a180d796e498dfb9c5cf3` |
| Manifest SHA-256 after | `d01aa70995897b5b8a0e22b612649933bbf287cfd70a180d796e498dfb9c5cf3` (**unchanged**) |
| `dataset_hash` | `9767422bdf45596c4576df3734f33bf3ae14479589a8b3f5adf6f05e0c3ac5e5` |
| Files | 891 before → 891 after |
| Quarantined | none |
| Data lost | **none** — replay loads all **220** symbols |
| Scan rerun | **no** — the archive was repaired from the existing staging directory |

Residual, deliberately not touched: `run_metadata.json` still reads `status: RUNNING`
because the original `complete()` died before updating it. Rewriting it now would
invalidate the run-integrity record and rewrite history; the honest record is that this run
was interrupted during finalization and its dataset was published later.

## Tests

`tests/test_dataset_finalization.py` — 28 tests, `tmp_path` only, with an autouse fixture
that fails the run if the real `reports/` directory changes. Covers all four cases,
equivalent/conflicting/incomplete directory pairs, transient WinError 5 then success,
retry exhaustion, non-transient errors not retried, precondition re-checks mid-retry,
refusal to overwrite, repeated and concurrent finalize, an open handle inside staging
(the production failure, reproduced), writers closed before rename, malformed manifests,
Windows paths with spaces and dots, no data deletion, scan preserved when publication
fails, the typed dashboard warning, and that production stays disabled.

Regression: dataset archive · experiment tracking · phase-8 hardening · market-data
providers — all green; full repository suite green.

## Scope

Storage only. A test asserts `dataset_archive.py` imports nothing from strategy, portfolio,
scanner, trading-decision, data-provider or indicator modules. No strategy, provider,
scanner, AI, portfolio or trading behaviour was changed, and no scan was rerun.
