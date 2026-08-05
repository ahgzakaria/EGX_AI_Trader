# ORB Full Shadow — Report Finalization Validation

**Research Only. Production execution disabled.** Nothing in this document is a
trade signal, a recommendation, or a performance claim.

Companion to `ORB_FULL_REPORT_FINALIZATION_AUDIT.md`, which describes the three
defects and the corrected design. This document records how the fix was proven.

No live session was run. No scheduled task was triggered. No Rubix process was
started, stopped or restarted. No production database was written.

---

## 1. Controlled replay

### CONTROLLED REPORT REPLAY — NOT ORIGINAL LIVE ARTIFACT

The 2026-08-05 research database was **copied** to a temporary workspace and the
corrected finalization lifecycle was run against the copy. The original session
directory was never a write target.

Inputs, from the copy:

- Lane A `bfe9159ec86e7cd6…` — `FOLLOW`, `runner_started_before_open = 1`,
  `session_classification = FULL_SHADOW_SESSION`, `classification_reasons = []`
- Lane B `6ff99b4bbd24ee49…` — `RECONSTRUCT`, 224 rows, 0 Lane A rows
- the 224-row cross-run comparison
- Lane A's real quality row, read from the database rather than from the
  overwritten `shadow_run_summary.json`

### Result

```
session evidence verdict (pre-render): FULL_SHADOW_SESSION_OBSERVED
unmet criteria                       : (none)
publication succeeded                : True
report publication status            : REPORT_PUBLISHED
```

| Assertion | Result |
|---|---|
| report says `FULL_SHADOW_SESSION_OBSERVED` | PASS |
| report does **not** say `PARTIAL_SHADOW_SESSION` | PASS |
| report contains no `report_completed` | PASS |
| report says "All FULL criteria met." | PASS |
| status JSON `session_evidence_verdict` = FULL | PASS |
| status JSON `final_verdict` agrees with it | PASS |
| status JSON `report_publication_status` = `REPORT_PUBLISHED` | PASS |
| status JSON `unmet_full_criteria` = `[]` | PASS |
| `FullSessionCriteria` carries no self-referential field | PASS |
| Lane A and Lane B summaries resolve to distinct paths | PASS |

Artifact paths emitted for the replayed session:

```
lane_a_summary   shadow_run_summary_LANE_A_bfe9159ec86e7cd6.json
lane_a_quality   shadow_session_quality_LANE_A_bfe9159ec86e7cd6.csv
lane_b_summary   shadow_run_summary_LANE_B_6ff99b4bbd24ee49.json
lane_b_quality   shadow_session_quality_LANE_B_6ff99b4bbd24ee49.csv
```

**The same evidence that published a permanent PARTIAL report on 2026-08-05
publishes a FULL report under the corrected lifecycle.** The session was always
FULL; only the artifact disagreed.

### Timestamps on the replayed copy

```
reconstruction rows: 224   statuses: {'NOT_RECORDED_PRE_MIGRATION': 224}
comparison rows    : 224   statuses: {'BOTH_TIMES_UNAVAILABLE': 224}
deltas computed    : 0
schema after additive migration: user_version 8
```

This is the correct and intended outcome, and it is worth stating plainly:
**2026-08-05's detection-time deltas are not retroactively recoverable.** Those
rows were written before the timestamp columns existed, and the migration
deliberately does not invent values for them. Timing evidence begins with the
next session that runs on this code, not with a back-fill.

### Original evidence unchanged

SHA-256, taken before and after the replay:

| Artifact | SHA-256 | Verdict |
|---|---|---|
| `orb_full_shadow_2026-08-05.db` | `D16B0CA52395A4DBDE6A4D52AC501EC9F51D2C972600A410451C55ABF43E48DE` | unchanged |
| `FULL_SHADOW_SESSION_REPORT.md` | `B00B2D8B7F7B95652AA83DFA82A5B2A1BC094382C6813F04C3A4745FAEB21DB4` | unchanged |
| `orchestrator_status.json` | `2F655E681FBA8A99535CDE92337A3694A3D38E52BFC3BE58D1E2E54F0145C3DB` | unchanged |
| `shadow_run_summary.json` | `B6D794BF0E014559FA5C90D360591AD1E5074A1C220AB259601BE67F9855F435` | unchanged |
| `shadow_session_quality.csv` | `26C6125822A5EA7971FC7431BCF0FB8B6BB921D1E5917E7154822CD82EBD3603` | unchanged |
| `shadow_live_vs_reconstruction.csv` | `588D827498A8542B574B04EB4C0EEFA0DF1E0F9EBBD41BAC01CE6446A61F3485` | unchanged |

`SCALPING_MULTI_SESSION_VERDICT.md` (`44C35EAB58396912877694D5DC58E3AD9DEE651CED03824ED589EF8B8581D7A6`)
and `data/paper_trades.csv` were not touched.

---

## 2. Tests

`tests/test_orb_report_artifact_ownership.py` — **50 tests**, all passing.

### Report finalization

- `report_completed` is absent from `FullSessionCriteria`, and no field name in
  that dataclass contains `report` or `publish`
- every FULL criterion is settled before any artifact exists; publishing does
  not change the evidence verdict
- a FULL session publishes a FULL report
- database, log, report and status JSON all carry the same verdict
- the status JSON never contradicts the report
- a publication failure is explicit: `REPORT_PUBLICATION_FAILED`,
  state `SESSION_FAILED`, never `SESSION_COMPLETE`
- a publication failure preserves the substantive evidence verdict in the
  database
- no contradictory artifact is published on failure
- a previous valid report survives a failed second publication
- partial publication is impossible — two writable targets stay untouched when a
  third fails
- successful and failed publications both leave no temporary files behind
- an unmet observation criterion still produces a PARTIAL report

### Artifact ownership

- lane is derived from run mode (`RECONSTRUCT` → Lane B, `FOLLOW`/`ONCE` → Lane A)
- FOLLOW and RECONSTRUCT write separate artifacts
- **Lane B cannot overwrite Lane A** — Lane A's summary and quality CSV are
  byte-identical before and after a reconstruction runs into the same directory
- no run writes any of the six legacy shared filenames
- readers select the correct lane, by run id and by lane alias
- session aliases name the lane they mirror
- a legacy shared artifact is read with a `RuntimeWarning` and tagged
  `AMBIGUOUS_LEGACY_SHARED_ARTIFACT`
- a missing summary raises rather than guessing
- run metadata records the exact artifact paths, and every recorded path exists
- the session report links both lanes' artifacts

### Reconstruction timestamps

- the historical state time comes from the last exchange-timestamped transition
- breakout / pullback / reclaim times are bar boundaries
- entry-ready time is set only when the final state is `ENTRY_READY_RESEARCH`
- an unavailable time stays `None` with a typed reason
- no reconstructed timestamp is ever the current clock
- the persisted decision time is not the insert time
- an unrecoverable time is stored as `NULL`, not as a clock reading
- a real reconstruction persists market-date instants, not the replay's run time

### Migration

- migration 8 is the head and is additive
- rows written at version 7 remain readable after upgrade, keep their values,
  and report `NOT_RECORDED_PRE_MIGRATION`

### Comparison timing

- matching states with both times produce a correct delta
- a missing Lane A / Lane B / both time yields the typed unavailable status
- **mismatched states are never timed** — `STATE_NOT_COMPARABLE`
- timing flows through to the database, and a delta exists only where the status
  is `TIMING_COMPARABLE`
- no delta is derived from a reconstruction write time
- a reconstruction without a time is reported, not estimated

### Regressions

- the 2026-08-05 FULL shape remains FULL
- the 2026-08-04 stall shapes remain PARTIAL (parametrized over
  `normalization_progressed_to_continuous_end`,
  `evaluation_progressed_to_continuous_end`, `no_critical_evaluation_stall`)
- a failed live lane is still `FAILED_SHADOW_SESSION`, not merely PARTIAL
- ONCE, SMOKE and RECONSTRUCT remain ineligible as a live source
- a RECONSTRUCT run still writes zero Lane A rows
- the report never becomes an execution instruction

---

## 3. Mutation checks

Each mutation restores the defect, runs the guarding tests, and is reverted by
restoring the file's exact bytes. **All eight were caught.**

| # | Mutation | Guard | Result |
|---|---|---|---|
| M1 | `report_completed` re-added as a FULL criterion | `test_report_completion_is_not_a_full_criterion` | CAUGHT |
| M2 | report renders a pre-final verdict | full-report / four-way-agreement tests | CAUGHT |
| M3 | status JSON diverges from the database verdict | status-agreement tests | CAUGHT |
| M4 | Lane B writes Lane A's filename | `test_lane_b_cannot_overwrite_lane_a` | CAUGHT |
| M5 | reconstruction write time used as the historical decision time | `test_the_decision_time_is_persisted_and_is_not_the_insert_time` | CAUGHT |
| M6 | missing historical time replaced with `now()` | clock / typed-reason tests | CAUGHT |
| M7 | publication failure no longer marked failed | `test_a_publication_failure_is_explicit_and_never_completes` | CAUGHT |
| M8 | delta computed for states that do not match | `test_mismatched_states_are_never_timed` | CAUGHT |

Source was verified restored after every mutation: `git diff --stat` was
byte-identical before and after the run.

> **Harness note.** An earlier version of this harness restored mutated files
> with `git checkout --`, which discards *uncommitted* work. It reverted the
> in-progress fix and the implementation had to be re-applied. The harness now
> snapshots and rewrites the exact bytes. Recorded here because the failure mode
> is easy to repeat: never restore a mutation with `git checkout` while the fix
> itself is uncommitted.

---

## 4. Suites

| Suite | Result |
|---|---|
| `tests/test_orb_report_artifact_ownership.py` (new) | 50 passed |
| ORB suite (`-k orb`) | 609 passed |
| Full project suite | see commit message / run log |
| `git diff --check` | clean |

Three existing tests were updated, each for a deliberate behaviour change:

- `test_orb_shadow_orchestrator.py` — two migration assertions moved from
  `== 7` to `>= 7`, matching the pattern the phase-2b and phase-2c migration
  tests already use for "a fresh database has at least this migration". The
  tables migration 7 owns are still asserted to exist.
- `test_orb_phase2c_shadow.py` — asserts the lane-scoped summary exists **and**
  that no `shadow_run_summary.json` is written.
- `test_orb_full_shadow_run_controls.py` — reads the run summary through
  `read_run_summary(..., lane=LANE_A, run_id=...)`.

No test was deleted, weakened, or skipped. No new skips were introduced.

---

## 5. What this does not claim

- It does not claim 2026-08-05 was profitable, or that any Lane B
  `ENTRY_READY_RESEARCH` was actionable. It was not: all 14 were rejected live
  by `LIVE_DECISION_DISABLED_FRESHNESS`.
- It does not recover 2026-08-05 timing evidence. Those rows predate the columns
  and were not back-filled.
- It does not change what the session observed. Every fix here is downstream of
  observation.
- It does not update the multi-session verdict. 2026-08-05 is still not admitted
  as multi-session evidence, and admitting it is a separate decision.
