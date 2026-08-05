# ORB Full Shadow — Report Finalization and Artifact Ownership Audit

**Research Only. Production execution disabled.** Nothing in this document is a
trade signal, a recommendation, or a performance claim. No threshold, strategy
rule, freshness budget, deduplication policy or liveness rule was changed by the
work described here.

## Scope

Three evidence/reporting defects found while auditing the **2026-08-05** ORB
full shadow session. All three are reporting defects. None of them affected what
the session observed.

| | |
|---|---|
| Session audited | 2026-08-05 (Africa/Cairo) |
| Lane A (live, FOLLOW) | `bfe9159ec86e7cd6b44077ea060a58f4e50261444c284e3993449eaee1cda0c0` |
| Lane B (RECONSTRUCT) | `6ff99b4bbd24ee496f282073a0b9ea83d7ff22d14815ef94f4b5491cb5b67dde` |
| Orchestrator run | `5b34673926b1ae1b8516946e520054a8a9bb8f641fcfc42619a5ed82e5d438a8` |
| Deployed runtime at the time | `9246d3ad4efaab7a97ffbf0fd08ba2f2984aec09` |
| Fix branch | `fix/orb-report-artifact-ownership` |

## The session was good. The reporting was not.

2026-08-05 was the first genuine live session after the normalization-liveness
work, and the pipeline behaved:

- **435,851 normalized events** — 74% past the old 250,000 boundary, crossed at
  12:14:47 Cairo and still climbing until 14:15:20
- **zero cycles** out of 844 produced zero normalized events; zero cycles
  advanced the cursor while normalizing nothing
- normalization and evaluation both progressed to the continuous close
- **no** `NORMALIZATION_STALLED`, **no** `EVALUATION_STALLED`, **no**
  `DEDUPLICATION_CAPACITY_EXHAUSTED`
- 255/255 continuous exchange minutes covered
- Lane B and the cross-run comparison both completed

The database recorded that correctly:

```
orb_shadow_orchestrator_runs.final_verdict = FULL_SHADOW_SESSION_OBSERVED
orb_shadow_runs[bfe9159e].session_classification = FULL_SHADOW_SESSION
                          classification_reasons_json = []
```

The durable report published for the same session said:

```
# `PARTIAL_SHADOW_SESSION`
**Unmet FULL criteria:** `report_completed`
```

---

## DEFECT 1 — self-referential report completion

### Mechanism

`report_completed` was a field of `FullSessionCriteria`, the dataclass whose
every field must be true for `FULL_SHADOW_SESSION_OBSERVED`. In
`scripts/run_orb_shadow_orchestrator.py` the call order was:

```
write_report()
├── _render_report()                     # line 609
│   ├── verdict = self.criteria.verdict(...)   # report_completed is still False
│   ├── write FULL_SHADOW_SESSION_REPORT.md
│   └── write orchestrator_status.json
└── criteria = replace(criteria, report_completed=True)   # line 614 — too late

_finish()
├── verdict = criteria.verdict(...)      # now correct: FULL
├── update_orchestrator_run(final_verdict=FULL)   # database is right
└── log("final verdict: FULL...")                 # log is right
    # the .md and the .json are never rewritten
```

The criterion could only become true *after* the artifact existed, so the
verdict rendered *into* the artifact was computed with it false. This was not a
2026-08-05 accident — **every** otherwise-FULL session would have published a
PARTIAL report, permanently, naming a criterion that was in fact satisfied.

`orchestrator_status.json` inherited the same defect and additionally froze at
`state: COMPARISON_COMPLETE`, because it too was written from inside the
renderer.

### Corrected lifecycle

Approach A and B from the brief, combined. The criterion is removed from the
gate *and* publication is made atomic.

```
session evidence verdict  ← FullSessionCriteria only (observation facts)
pipeline completion status ← how far the workflow got
report publication status  ← whether the artifacts reached disk
```

- `report_completed` is **deleted** from `FullSessionCriteria`. A test asserts
  no field name in that dataclass contains `report` or `publish`.
- New `ReportPublicationStatus`: `REPORT_NOT_ATTEMPTED` / `REPORT_PUBLISHED` /
  `REPORT_PUBLICATION_FAILED`.
- New `PipelineCompletionStatus`: `PIPELINE_IN_PROGRESS` /
  `PIPELINE_SESSION_COMPLETE` / `PIPELINE_REPORT_PUBLICATION_FAILED` /
  `PIPELINE_SESSION_FAILED` / `PIPELINE_SKIPPED`, persisted on
  `orb_shadow_orchestrator_runs`.
- `session_evidence_verdict()` is the single authority. The database, the log,
  the report body and the status JSON all render that one value.
- `_finish()` no longer recomputes the verdict from the terminal state. A
  workflow that reached `SESSION_FAILED` *while publishing* did not un-observe
  the session; only a genuine Lane A failure (`live_lane_failed`) can downgrade
  the evidence verdict to `FAILED_SHADOW_SESSION`.

The report and the status JSON are rendered claiming `REPORT_PUBLISHED`. That is
honest rather than self-referential: publication is all-or-nothing, so a reader
holding either file is holding proof that publication succeeded. A failed
publication produces no such file at all.

### Failure behaviour

| | |
|---|---|
| Publication succeeds | DB = log = report = status JSON = the same verdict; state → `REPORT_COMPLETE` → `SESSION_COMPLETE` |
| Publication fails | nothing written; previous generation intact; `REPORT_PUBLICATION_FAILED` recorded as an orchestrator failure; state → `SESSION_FAILED`; **evidence verdict preserved in the database**; pipeline status `PIPELINE_REPORT_PUBLICATION_FAILED`; never `SESSION_COMPLETE` |

---

## DEFECT 2 — Lane B overwrote Lane A's summary

### Mechanism

`ShadowRunner.finalize()` wrote six artifacts under fixed names into the session
directory, regardless of run mode. The orchestrator runs Lane B *after* Lane A
into the *same* directory, so the reconstruction's files replaced the live
session's.

What survived for 2026-08-05 in `shadow_run_summary.json`:

```json
{ "mode": "RECONSTRUCT",
  "runner_started_before_open": false,
  "session_classification": "PARTIAL_SHADOW_SESSION",
  "evaluation_progress_through_continuous_end": false,
  "live_evaluations": 0,
  "classification_reasons": ["RUNNER_STARTED_AFTER_CONTINUOUS_OPEN",
                             "INSUFFICIENT_HEARTBEAT_COVERAGE",
                             "EVALUATION_STALLED_BEFORE_CONTINUOUS_END"] }
```

Every one of those is correct **for Lane B** — a reconstruction legitimately
starts after the close and evaluates nothing live. Read as the session's
summary, which is what the filename invited, all of it is wrong. Lane A's real
quality row survived only inside the database.

`shadow_session_quality.csv`, `shadow_cycle_metrics.csv`,
`shadow_reconstruction_states.csv` and `shadow_live_vs_reconstruction.csv`
collided the same way.

### Corrected ownership

Every artifact is now run-scoped and lane-labelled:

```
shadow_run_summary_LANE_A_<run16>.json
shadow_session_quality_LANE_A_<run16>.csv
shadow_cycle_metrics_LANE_A_<run16>.csv
shadow_live_states_LANE_A_<run16>.csv
shadow_reconstruction_states_LANE_B_<run16>.csv
shadow_live_vs_reconstruction_LANE_B_<run16>.csv
```

- `lane_for_mode()` derives the lane from the run mode — `RECONSTRUCT` → Lane B,
  `FOLLOW`/`ONCE` → Lane A. It is not a flag a caller can set wrongly.
- **No run writes `shadow_run_summary.json` any more.** One ambiguous name for
  two run modes is the defect; keeping it as a "convenience copy" would keep it.
- Two session-level aliases exist, each naming the lane it mirrors:
  `session_live_summary.json` and `session_reconstruction_summary.json`.
- The exact filenames are persisted: `orb_shadow_runs.artifact_lane` and
  `orb_shadow_runs.artifact_paths_json`, and echoed in the run summary's
  `artifact_paths`. Ownership is a stored fact, not a convention.
- `read_run_summary(dir, lane=..., run_id=...)` is the reader. When only a
  pre-migration `shadow_run_summary.json` exists it emits a `RuntimeWarning` and
  tags the payload `artifact_ownership: AMBIGUOUS_LEGACY_SHARED_ARTIFACT` rather
  than trusting it.
- The session report links both lanes' artifacts in a `## Per-run artifacts`
  table.

---

## DEFECT 3 — no reconstructed decision time

### Mechanism

`orb_shadow_reconstruction_states` persisted `recorded_at_utc` — the moment the
row was inserted, which for 2026-08-05 was 14:18 Cairo for all 224 rows. No
column held the historical instant the reconstructed state actually occurred at.

Consequence: the Lane A vs Lane B detection-time delta, the whole point of
running two lanes, **could not be computed at all**. The 2026-08-05 audit had to
report "not recoverable from persisted evidence" for all 14 Lane B
`ENTRY_READY_RESEARCH` setups.

### Timestamp sources

The engine already carried the real instants; nothing was persisting them.

| Field | Source | Meaning |
|---|---|---|
| `reconstructed_state_time_utc` | last `StateTransition` with a non-null `exchange_timestamp_utc` | the exchange instant that produced the final state |
| `reconstructed_breakout_time_utc` | `BreakoutAssessment.bar_end_utc` | close of the breakout bar |
| `reconstructed_pullback_time_utc` | `PullbackAssessment.low_bar_start_utc` | start of the pullback-low bar |
| `reconstructed_reclaim_time_utc` | `ReclaimAssessment.confirmation_bar_end_utc` | close of the confirming bar |
| `reconstructed_entry_ready_time_utc` | the reclaim confirmation, **only** when the final state is `ENTRY_READY_RESEARCH` | when the research setup became ready |

Explicitly **not** used: database insert time, report generation time,
reconstruction execution time, `datetime.now()`.

### When there is no instant

`reconstructed_time_status` is a typed reason, never a guess:

- `HISTORICAL_TIME_RECOVERED`
- `NO_EXCHANGE_TIMESTAMPED_TRANSITION` — the state was reached, but no
  transition carried an exchange timestamp (e.g. rejected before any completed
  bar existed)
- `STATE_HAS_NO_HISTORICAL_INSTANT` — no evidence was evaluated at all
- `NOT_RECORDED_PRE_MIGRATION` — the row predates these columns

Nothing is estimated, interpolated, or back-filled from a neighbouring symbol.

### Comparison timing

`orb_shadow_cross_run_comparison` and `orb_shadow_live_replay_comparison` gained
`lane_a_detection_time_utc`, `lane_b_detection_time_utc`,
`detection_delta_seconds` and `timing_comparison_status`.

- Lane A's instant is `ShadowStateRecord.observed_at_utc` — the live evaluation
  instant recorded *during* the session. It is an observation time, not a write
  time.
- Lane B's instant is `reconstructed_state_time_utc`.
- `detection_delta_seconds = lane_a − lane_b`: how long after the historical
  evidence instant live actually evaluated the symbol.

`TimingComparisonStatus` precedence, in order:

1. `STATE_NOT_COMPARABLE` — the lanes reached different states. Checked
   **first**: subtracting the instant of one state from the instant of a
   different state yields a number that looks like a latency and is not one.
2. `BOTH_TIMES_UNAVAILABLE`
3. `LANE_A_TIME_UNAVAILABLE`
4. `LANE_B_TIME_UNAVAILABLE`
5. `TIMING_COMPARABLE` — and only here is a delta computed.

---

## Migration 8 — `phase2c_report_artifacts_and_timing`

Strictly additive. `SCHEMA_VERSION` 7 → 8.

```sql
orb_shadow_reconstruction_states
  + reconstructed_state_time_utc         TEXT NULL
  + reconstructed_breakout_time_utc      TEXT NULL
  + reconstructed_pullback_time_utc      TEXT NULL
  + reconstructed_reclaim_time_utc       TEXT NULL
  + reconstructed_entry_ready_time_utc   TEXT NULL
  + reconstructed_time_status            TEXT NOT NULL
                                         DEFAULT 'NOT_RECORDED_PRE_MIGRATION'

orb_shadow_cross_run_comparison    } + lane_a_detection_time_utc   TEXT NULL
orb_shadow_live_replay_comparison  } + lane_b_detection_time_utc   TEXT NULL
                                     + detection_delta_seconds     REAL NULL
                                     + timing_comparison_status    TEXT NOT NULL
                                       DEFAULT 'BOTH_TIMES_UNAVAILABLE'

orb_shadow_runs
  + artifact_lane        TEXT NULL
  + artifact_paths_json  TEXT NULL

orb_shadow_orchestrator_runs
  + pipeline_completion_status  TEXT NULL
```

No column is dropped, renamed or retyped; no row is rewritten. Rows written at
version 7 remain readable and report `NOT_RECORDED_PRE_MIGRATION` /
`BOTH_TIMES_UNAVAILABLE` — honest about carrying no timestamp rather than
back-filled with a fabricated one.

The reconstruction and comparison `INSERT` statements were converted from
positional `VALUES (?,?,…)` to **named column lists**, because a bare positional
insert silently shifts every field when a table grows a column.

## Evidence preservation

The 2026-08-05 artifacts are historical evidence of the defect and were **not**
regenerated, edited or deleted. The corrected rendering was produced from a
temporary copy and is labelled **CONTROLLED REPORT REPLAY — NOT ORIGINAL LIVE
ARTIFACT**; see `ORB_FULL_REPORT_FINALIZATION_VALIDATION.md`.

`docs/audits/strategies/SCALPING_MULTI_SESSION_VERDICT.md` was not modified.

## What did not change

- ORB strategy rules, opening-range logic, breakout/pullback/reclaim rules
- freshness thresholds and the freshness budget
- deduplication retention (100,000) and hard capacity (400,000)
- normalization-liveness and evaluation-liveness rules
- FOLLOW live-source qualification, ONCE/SMOKE exclusion, the RECONSTRUCT Lane A
  prohibition
- production execution, which remains disabled and unreachable
