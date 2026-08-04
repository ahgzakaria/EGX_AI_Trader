# Daily Scan Metadata Ownership — Validation Record

**Research only. Production execution disabled.**

Evidence for the contract described in
[DAILY_SCAN_METADATA_OWNERSHIP_AUDIT.md](DAILY_SCAN_METADATA_OWNERSHIP_AUDIT.md).
No production scan was run. `RUN_20260804_225158` was read in place and its
files are byte-identical afterwards.

---

## 1. The affected run, read through the new reader

| Measure | Value | Source |
|---|---|---|
| Classification | **`RECOVERABLE_V2_EXPORT`** | was `LEGACY_V1` |
| `metadata_recovered` | `True` | |
| `coverage_available` | `True` | was `False` |
| Operational universe | 241 | coverage audit row count |
| Current decisions | 185 | decisions file |
| Excluded | 56 | audit − decisions |
| **Coverage** | **76.8%** | recomputed |
| Expected session | 2026-08-04 | single distinct value across 241 audit rows |
| Dominant observed session | 2026-08-04 | audit distribution |
| BUY / WATCH / AVOID | 3 / 127 / 55 | decisions file |
| Stale | 10 | `SKIPPED_STALE_DAILY_DATA` |
| FUTURE_DATE | 0 | no such outcome present |
| Alias identical | `True` | row comparison |

Reported as unavailable, never invented: `generated_at_utc`,
`evaluation_time_utc`, `strategy_config_identity`,
`rubix_freshness_policy_identity`, `minimum_market_coverage_percent`.

## 2. Tests

`tests/test_run_metadata_ownership.py` — 28 tests:

* **Ownership** — both sections coexist; the experiment writer cannot erase
  the export; additive updates preserve the other section; conflicting
  `run_id`, conflicting expected session, schema downgrade, future document
  schema, export claiming run keys and conflicting filenames all refused; a
  refused write leaves the previous document byte-identical with no `.tmp`
  residue.
* **Publication** — `publish_archive` composes rather than overwrites; the
  full two-writer sequence ends `SCHEMA_V2_VALID` and never needs recovery.
* **The affected shape** — recoverable, not plain legacy; proves its coverage;
  names what it cannot prove; discloses the recovered state; refused when the
  alias mismatches, the current-rows invariant breaks, audit symbols duplicate
  or expected sessions conflict; the invalid marker still overrides; a genuine
  legacy archive stays legacy; reading never writes.
* **The real run** — `RUN_20260804_225158` asserted end to end, with a
  before/after byte-and-mtime comparison of every file in the directory.
* **Single writer** — an AST guard flags any module outside the service whose
  own write-call arguments name `run_metadata.json`.

Two of these tests exist because a first draft failed them: the real
experiment writer is driven directly (`experiment_tracking._write_run_metadata`)
rather than simulated, after a mutation showed the simulation proved nothing
about the function that caused the incident.

## 3. Mutation testing — 9/9 killed

| Mutation | Result |
|---|---|
| M1 experiment metadata overwrites the export section again | KILLED |
| M2 the service drops the export section when none is supplied | KILLED |
| M3 reader treats the affected shape as ordinary legacy | KILLED |
| M4 recovery skips the compatibility-alias check | KILLED |
| M5 recovery fabricates the unavailable config identity | KILLED |
| M6 recovery skips the current-rows/decisions invariant | KILLED |
| M7 the invalid marker no longer overrides recovery | KILLED |
| M8 a conflicting run id is silently accepted | KILLED |
| M9 `publish_archive` overwrites instead of composing | KILLED |

M1 survived the first pass. The gap was real: every ownership test called
`write_run_metadata_atomic` directly, so reverting
`experiment_tracking._write_run_metadata` to a raw `_atomic_json` changed
nothing any test observed — the same class of gap that let the original defect
ship. Closed by driving the production function.

M2 is worth recording separately: it caught a genuine bug in the first draft
of the service, where `allow_missing_export=True` deleted an existing export
section instead of merely tolerating its absence. The fix under test
reintroduced the defect it was written to prevent.

## 4. Tests updated rather than worked around

Three existing tests encoded the old document shape or the old classification:

* two archive-reader tests mutated the export fields at the document root —
  realigned to the `daily_scan_export` section, which is where they now live;
* `test_the_version_is_never_inferred_from_a_filename` asserted that a missing
  version means legacy. Its rule still holds and is now stated more precisely:
  a missing version is never `SCHEMA_V2_VALID`; it is legacy, or explicitly
  `RECOVERABLE_V2_EXPORT` with every figure recomputed and the loss disclosed.
  A companion test pins that a declared version is still required for plain
  validity, so recovery can never become a silent upgrade;
* the download guard asserted a literal `SchemaStatus.SCHEMA_V2_VALID` —
  updated to `COVERAGE_BEARING`, with a new test that a recovered archive
  discloses its state before offering any download.

## 5. Repair command

`scripts/repair_daily_scan_metadata.py --run-id RUN_20260804_225158 --dry-run`
was executed against the real run. It printed the full reconstruction, the
five unavailable fields and the source artifact hashes, and wrote nothing.
The sidecar was **not** created — no `--write` was authorised in this task.

## 6. Suite totals

Recorded in the merge commit. No production scan, no archive rewrite, no
Rubix process change, no scheduled task trigger; the ORB runtime worktree
remains pinned at `9246d3a`.
