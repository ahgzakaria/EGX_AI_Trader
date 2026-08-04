# Daily Scan Metadata Ownership — Audit

**Research only. Production execution disabled.**

`RUN_20260804_225158` published three correct CSV files and lost every figure
describing them. Both writes involved were individually atomic. Atomicity says
nothing about ownership.

---

## 1. The two writers

| # | Writer | File | Payload |
|---|---|---|---|
| 1 | `services/daily_scan_export.publish_archive` | `run_metadata.json` | schema-v2 export metadata |
| 2 | `services/experiment_tracking.ExperimentRun.complete` | `run_metadata.json` | the experiment's own dict |

Writer 2 has four call sites — `_write_snapshots`, `complete`, `fail` and
`RunRepository.mark_interrupted` — each previously a plain
`_atomic_json(self.run_dir / "run_metadata.json", self.metadata)`.

### Proven order for the affected run

Persisted mtimes, not source reading:

```
22:51:58.384  settings_snapshot.json      ← writer 2, _write_snapshots
22:53:53.232  scan_current_decisions.csv  ┐
22:53:53.235  scan_coverage_audit.csv     ├ writer 1, publish_archive
22:53:53.240  scan_results.csv            ┘   (metadata written here too)
22:53:53.575  README.md                   ┐ writer 2, complete()
22:53:53.705  run_metadata.json           ┘   ← 465 ms later, overwrote it
22:54:00.812  RUN_INTEGRITY.json
```

`_write_readme()` runs immediately before writer 2's metadata write, so the
README timestamp places the final write unambiguously inside `complete()`.
The file mode is `444` because `_make_completed_read_only()` runs after.

The overwrite therefore happened **after** the archive was published and
**as part of** marking the run completed. No in-memory structure ever held
both payloads — each writer knew only its own.

## 2. Consequence

The CSV files were and remain correct: 185 decisions, 241 audit rows, alias
byte-identical. Only the metadata describing them was lost, so
`read_archive` found no `export_schema_version` and classified the run
`LEGACY_V1` with `coverage_available = False`. Run History could not show
185/241, and Compare Runs could not separate a coverage change from a
strategy change.

## 3. Chosen model — one composed document

```json
{
  "run_metadata_schema_version": 2,
  "run_id": "...", "status": "COMPLETED", "run_type": "SCAN",
  "daily_scan_export": { "export_schema_version": 2, "..." : "..." }
}
```

**Deviation from the brief's Option A, stated explicitly:** the generic run
fields stay flat at the document root rather than nesting under `"run"`. Six
consumers — `run_status`, `run_history`, `compare_runs`, `backup_manager`,
`dashboard/home` and the phase-5 baseline script — read `status`,
`run_type` and `dataset_finalization_ok` directly from the root. Nesting them
would be a rename touching every one of them, with no safety benefit: the
defect was never about where the generic fields live. It was about a second
writer erasing a section it did not own.

Option B (split filenames) was rejected: it replaces one ownership question
with two files that can disagree.

## 4. The service

`services/run_metadata_service.py` is the sole writer.

| Function | Contract |
|---|---|
| `load_run_metadata` | splits a document into `run` / `export`; understands composed, flat-legacy and flat-export shapes |
| `compose_run_metadata` | builds one document; refuses an export that claims run keys |
| `write_run_metadata_atomic` | preserves the export section **unconditionally**, validates, fsyncs, `os.replace` |
| `update_run_metadata_section` | additive, one section at a time |

Refused: dropping the export section, downgrading either schema, a conflicting
`run_id`, a conflicting `expected_completed_session`, conflicting artifact
filenames, and an on-disk document newer than this build understands.

`allow_missing_export=True` means only "this caller supplies no export and
does not need one to exist". It never authorises deleting a section already on
disk — an earlier draft of the service made exactly that mistake, which the
mutation suite caught.

## 5. Publication lifecycle

1. build decisions, audit and export metadata in memory;
2. load the existing run section and compose one document;
3. `validate_archive` — every cross-file invariant;
4. stage all four artifacts beside their destinations;
5. flush;
6. `os.replace` each into place — nothing partial is ever visible;
7. the experiment marks the run completed **through the service**, which
   carries the export section forward untouched.

## 6. Backward compatibility

| Shape | Classification |
|---|---|
| composed document, valid | `SCHEMA_V2_VALID` |
| flat export document (pre-service) | `SCHEMA_V2_VALID` |
| legacy experiment metadata only | `LEGACY_V1` / `LEGACY_UNKNOWN` |
| **valid v2 CSVs + overwritten metadata** | **`RECOVERABLE_V2_EXPORT`** |
| any of the above with `INVALID_DATA_PROVENANCE.md` | `INVALID_DATA_PROVENANCE` — always wins |

## 7. Recovery contract

Recovery is **earned, never assumed**. `_recover_overwritten_export` returns
`None` — leaving the run plainly legacy — unless every one of these holds:

- decisions, audit and compatibility files all present;
- the alias is row-identical to the decisions file;
- audit symbols are unique and non-empty;
- the audit carries the required provenance columns;
- exactly one distinct `ExpectedCompletedSession` across the run;
- `SUCCESS_CURRENT` audit rows == decision rows — the same invariant the
  writer enforces;
- every decision row carries a BUY/WATCH/AVOID value.

Fields that existed only in the lost metadata are listed in
`unavailable_fields` and left **absent** from the recovered payload:
`generated_at_utc`, `evaluation_time_utc`, `strategy_config_identity`,
`rubix_freshness_policy_identity`, `minimum_market_coverage_percent`.

## 8. Optional repair

`scripts/repair_daily_scan_metadata.py` is dry-run by default and writes a
**sidecar** `DAILY_SCAN_EXPORT_RECOVERY.json`. It never rewrites
`run_metadata.json`: that file is the evidence of what happened, and replacing
it would destroy the trace the repair exists to document. The reader recovers
in memory on every read, so the sidecar is for audit, not for function.

It refuses any run that is not classified `RECOVERABLE_V2_EXPORT`, refuses to
overwrite an existing sidecar, and records the source artifact hashes it
derived from.

## 9. Remaining limitations

- The five unavailable fields are unrecoverable for every affected historical
  run. A recovered archive can prove what was decided and how much was
  covered; it cannot prove which config identity produced it.
- Runs completed before this change keep whatever metadata survived. Nothing
  is rewritten retroactively.
- Recovery depends on the coverage audit carrying one row per universe symbol.
  An archive without it stays legacy, correctly.
