# ORB — Signal Qualification Persistence

**Research Only. Production execution disabled.** `ENTRY_READY_RESEARCH` is a
research candidate, never an order. `trigger_price` is a price level the rules
confirmed — **not a fill**. No order, size, routing or execution path exists in
this package, and nothing in this change adds one.

## Why

The 34 signals measured across 2026-08-06, 08-10 and 08-11 produced a median
adverse excursion of −1.46 %, with 23 of 34 reaching −1 % and 10 reaching −2 %.
Every one of those signals had a stop — the engine computed one, strictly below
its trigger and within `maximum_stop_distance_percent` (3.0 %) of it, because
readiness is unreachable otherwise. So the measured adverse excursions sit
squarely inside the band where the stops must have been, and **whether a signal
stopped out before its favourable excursion arrived is unanswerable**.

That is not a limitation of the analysis. It is a hole in the evidence: the
values were computed and then dropped on the way to the database.

## Where the values originate

An evaluation reaches `ENTRY_READY_RESEARCH` only after both
`_structural_risk` and `_targets` return without rejection reasons — otherwise
[engine.py:1094-1102](scalping_orb/engine.py:1094) moves it to
`RECLAIM_FAILED`. By the time
[engine.py:1103](scalping_orb/engine.py:1103) emits the signal, every required
value is already materialised:

| Group | Object | Fields |
|---|---|---|
| Risk | `StructuralRiskProposal` | `proposed_stop`, `stop_basis`, `raw_pullback_low`, `buffer_applied`, `buffer_basis`, `stop_distance_absolute`, `stop_distance_percent`, `stop_distance_atr` |
| Targets | `TargetProjection` | `trigger_price`, `risk_per_share`, `target_1`, `target_2`, `target_1_r_multiple`, `target_2_r_multiple`, `usable_target`, `effective_reward_risk`, `meets_minimum_reward_risk`, `resistance_before_target_1`, `nearest_daily_resistance`, `reward_before_resistance` |
| ATR | `BreakoutAssessment.intraday_atr` | `value`, `status`, `interval_minutes`, `lookback_bars`, `observed_bars` |
| Provenance | `ORBResearchEvaluation` | `strategy_fingerprint`, `engine_version`, `opening_range_version_identity`, `evidence_fingerprint`, `candidate_identity` |

**Nothing is calculated here.** `qualification_from_evaluation` copies. The one
piece not on the evaluation is `DailyContext.available`, read from the snapshot
the evaluation was run against — and only to distinguish two states a single
null cannot (below).

## Where they are persisted

`scalping_orb/qualification.py` builds a frozen `SignalQualification` and
attaches it to the `ShadowStateRecord` it belongs to, in
[shadow_service.py:evaluate_live](scalping_orb/shadow_service.py). The record
then travels the *existing* persistence path — no new writer, no second call
site, no parallel pipeline. `persist_shadow_cycle` writes the qualification row
immediately after the Lane A row it describes, inside the transaction that was
already open.

The new table is `orb_signal_qualification`, created by `MIGRATION_9`
(`SCHEMA_VERSION` 8 → 9). Additive: no existing table or column is altered or
removed, the migration is guarded by the existing checksum + `user_version`
mechanism, and re-running it is a no-op.

## Why they are transactionally coupled

Writing the levels separately would allow a signal to exist without them —
which is precisely the state that made the first 34 unmeasurable. The two rows
commit together or neither exists.

Two independent locks enforce it:

* **The shared transaction.** Cycle metrics, the Lane A row, the qualification
  row and the cursor all sit inside one `BEGIN IMMEDIATE`. A failure anywhere
  rolls back everything — verified directly: a forced CHECK violation left 0
  live states, 0 cycles and 0 qualification rows.
* **The foreign key.** `live_state_id` references
  `orb_shadow_live_states(live_state_id)`, so a qualification row cannot
  reference a signal that does not exist even if a future writer bypasses the
  path above.

The insert uses `ON CONFLICT(live_state_id) DO NOTHING`, **not**
`INSERT OR IGNORE`. Both make a repeated cycle idempotent, but `OR IGNORE` also
swallows CHECK and foreign-key violations — it would convert the schema's
guarantees into silently skipped rows. Only a primary-key collision is ignored.

Two schema-level invariants back the copy:

```sql
CHECK (proposed_stop IS NULL OR trigger_price IS NULL
       OR proposed_stop < trigger_price)
CHECK (atr_value IS NULL OR atr_status = 'INTRADAY_ATR_AVAILABLE')
```

A stop at or above its trigger is not a stop, and the engine cannot emit one
(`STOP_NOT_BELOW_TRIGGER` blocks readiness). These are persistence guards
against a future writer, not strategy rules restated.

## Signal uniqueness

**No new identity was invented.** The row is keyed on `live_state_id`, the
identity Lane A already assigns:

```
live_state_id = sha256(run_id | cycle_id | canonical_ticker | opening_range_version_identity)
```

matching Lane A's own `UNIQUE(run_id, cycle_id, canonical_ticker,
opening_range_version_identity)`.

The consequences follow the existing convention exactly:

* Re-recording the same cycle stores one qualification row, not two.
* A symbol that leaves `ENTRY_READY_RESEARCH` and re-enters it — 17 of the 34
  historical signals did, MIPH six times on 2026-08-11, always flickering
  through `BREAKOUT_REJECTED_STALE` — produces one Lane A row per cycle and
  therefore one qualification row per emitted observation. Collapsing
  re-entries into "one signal" stays an analysis decision made downstream,
  where the outcome layer already makes it.
* A corrected opening range appends rather than overwrites, and its
  qualification appends with it.

A qualification row exists **if and only if** the observation was
`ENTRY_READY_RESEARCH`.

## Unavailable is not zero

| Situation | Stored |
|---|---|
| ATR available | `atr_value` = the number, `atr_status` = `INTRADAY_ATR_AVAILABLE` |
| ATR warming up / unavailable | `atr_value` = **NULL**, `atr_status` = `INTRADAY_ATR_WARMING_UP` / `ATR_UNAVAILABLE`, with `interval_minutes` / `lookback_bars` / `observed_bars` still recorded |
| No breakout assessment (defensive) | `atr_status` = `BREAKOUT_CONTEXT_UNAVAILABLE` |

A `0.0` under an unavailable status would read downstream as "volatility was
zero" — a claim the data never made. The schema rejects it.

`nearest_daily_resistance` is `None` in two entirely different situations, and
a single null cannot tell them apart, so `daily_resistance_status` names which:

| Status | Meaning |
|---|---|
| `DAILY_RESISTANCE_ABOVE_TRIGGER` | D-1 context present, a level sits above the trigger |
| `NO_DAILY_RESISTANCE_ABOVE_TRIGGER` | D-1 context present, nothing above — the projection was made *knowing* the path was clear |
| `DAILY_CONTEXT_UNAVAILABLE` | no D-1 context — the projection was made blind |

Missing context fails closed to `DAILY_CONTEXT_UNAVAILABLE`. The repository's
coercion helpers (`_optional_float`, `_optional_int`, `_optional_bool`) return
`None` rather than a default, so `None` cannot become `0.0` or `False` anywhere
on the write path. `meets_minimum_reward_risk` and
`resistance_before_target_1` are deliberately three-state: true, false, and
"the engine never said".

## Why the historical 34 are not backfilled

They cannot be, honestly. Reconstructing their stops and targets would mean
running today's `strategy_config` against bars rebuilt after the fact and
presenting the result as what the engine decided at 10:36 on 2026-08-06. It
would not be — configuration, opening-range revision and bar finality can all
differ from the live moment — and the numbers would look exactly like evidence
while being a reconstruction.

So `orb_signal_outcomes` keeps `tp_sl_status =
TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED` for all 34, permanently. Verified after
this change: 34 rows, one distinct `tp_sl_status`, 34
`MEASUREMENT_COMPLETE` — unchanged. The preliminary performance report's
`CONCERNING` verdict stands on its own terms and is not revised by this work.

**This contract applies to signals emitted from the next session onward.**

## What was verified

| Check | Result |
|---|---|
| Focused tests | 31 passed |
| Full suite | 3,108 passed, 8 skipped |
| `git diff --check` | clean |
| Schema | 41 columns, 3 foreign keys, 5 CHECK constraints, `user_version` 9, migration recorded as `phase2c_signal_qualification_levels` |
| Exact values | 15 numeric fields compared engine-object against stored row — all identical to full float precision (e.g. `proposed_stop` 9.940000000000001, `stop_distance_atr` 1.0499999999999992) |
| Atomicity | forced CHECK violation → `IntegrityError`, 0 live states, 0 cycles, 0 qualification rows |
| One row per signal | same cycle recorded twice → 1 live state, 1 qualification |
| Pre-migration DB | a version-8 database opens, upgrades to 9, keeps every row byte-identical, gains an empty qualification table |
| Historical outcomes | 34 rows, `TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED`, untouched |

No strategy logic, threshold, Lane A/Lane B behaviour, Rubix collection, Daily
Scan, EODHD path or schedule was modified. The frozen runtime (`283f86d`) was
not touched. No live session was run, no scheduled task triggered, no Rubix
process started or stopped. Local commit only — no merge, no push, no deploy.

## Files

| File | Change |
|---|---|
| `scalping_orb/qualification.py` | new — `SignalQualification`, statuses, `qualification_from_evaluation` |
| `scalping_orb/shadow_service.py` | `ShadowStateRecord.qualification` field (defaulted); built in `evaluate_live` |
| `scalping_orb/repository.py` | `SCHEMA_VERSION` 9, `MIGRATION_9`, `_insert_signal_qualification`, optional-value coercion helpers, `table_count` allow-list |
| `tests/test_orb_signal_qualification.py` | new — 31 tests |
