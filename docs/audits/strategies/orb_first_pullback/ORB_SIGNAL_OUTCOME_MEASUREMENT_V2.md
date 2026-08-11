# ORB — Outcome Measurement v2

**Research Only. Production execution disabled.** No order exists, no position
exists, and nothing here creates one. `trigger_price` is a level the rules
confirmed; `entry_price_proxy` is an observed quote. Neither is a fill.

## What v2 adds

v1 could only say how far price travelled after a signal, because the
per-signal stop and targets had been dropped before reaching the database. v2
measures against the levels the engine actually qualified each signal on, read
from `orb_signal_qualification` and **never recomputed**.

That upgrade applies only to signals emitted after qualification persistence
landed. The 34 historical signals have no qualification row, are not given one,
and keep `TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED` in the v1 table.

**As of today v2 measures zero signals.** No session has run since the
qualification change. A dry run over 2026-08-04 through 08-11 reports "0
qualified signals — nothing is reconstructed" for all five databases. That is
the layer working correctly, not failing.

## A. Source of every qualification field

All from `orb_signal_qualification`, joined to `orb_shadow_live_states` on
`live_state_id` — the identity Lane A already assigns. Nothing is derived.

| Field | Column |
|---|---|
| trigger, stop, T1, T2, usable target | `trigger_price`, `proposed_stop`, `target_1`, `target_2`, `usable_target` |
| risk basis | `risk_per_share`, `stop_distance_absolute`, `stop_distance_percent`, `stop_distance_atr` |
| R multiples and gate | `target_1_r_multiple`, `target_2_r_multiple`, `effective_reward_risk` |
| provenance | `atr_status`, `daily_resistance_status`, `qualification_status`, `qualification_schema_version`, `strategy_fingerprint`, `engine_version` |

The snapshot is **fingerprinted** (SHA-256 over the level fields, excluding
identity) and the fingerprint is stored on the outcome row. A later edit to a
qualification row therefore cannot pass unnoticed: re-measuring produces a
different fingerprint against the same signal.

A signal is `(session_date, canonical_ticker)` anchored at its **earliest**
`ENTRY_READY_RESEARCH` observation, and the qualification taken is the one
belonging to that observation — the levels as of first emission, not a later
re-evaluation of the same flickering setup.

## B. Source used to resolve stop/target ordering

Rubix `quotes`, ordered by `market_timestamp`, read through v1's
`read_observations` (which parses timestamps rather than comparing offset
strings — a `+03:00` bound sorts above a `+00:00` auction row). Window:
`[first quote at or after detection, 14:15 Cairo]`.

Long only, so `stop < trigger < target_1 ≤ target_2`. A single observation can
therefore touch the stop **or** a target, never both. All ambiguity lives
*between* observations.

## C. Is quote-level data sufficient for intrabar ordering?

**Partly — and the gaps are recorded rather than papered over.**

Measured on 2026-08-11:

| | |
|---|---|
| `(ticker, market_timestamp)` groups with more than one row | 519,103 (redeliveries) |
| …of those, groups carrying **more than one distinct price** | **4,765** |
| `id` inversions under `market_timestamp` ordering (12 tickers) | 0 |
| per-ticker inter-quote gap | p50 0 s, p95 3–41 s, p99 7–93 s |

So same-timestamp price divergence is real and common. `id` order agrees with
`market_timestamp` order, but `id` is **collector insertion order, not exchange
sequence**, and Rubix's `sequence` column is 100 % NULL. It cannot break a tie
that exchange time did not break, so it is not used to.

Rubix also carries a `candles_1m` table with OHLC. v2 deliberately does not use
it: a 1-minute candle aggregating snapshots can contain both a stop and a
target with no ordering information at all, which would manufacture the exact
ambiguity the quote sequence usually avoids.

## D. Where ambiguity must remain

Three cases, each with its own status — none of them silently resolved:

1. **Same instant.** Stop and target first touched at the same
   `market_timestamp` → `BOTH_STOP_AND_TARGET_TOUCHED_SAME_INSTANT`,
   evidence `RUBIX_QUOTE_SAME_INSTANT_UNRESOLVED`. No milestone, no return, no
   R.
2. **Wide gap before the first touch.** A level reached after an observation
   gap wider than the 300 s budget → `EXIT_UNDETERMINED_PRICE_DATA_GAP`,
   evidence `RUBIX_QUOTE_OBSERVATION_GAP_UNRESOLVED`. The touch is still
   recorded in `first_*_touch_at_utc` and the `*_reached` flags; what is
   withheld is the *ordering claim*, because the path through the gap was
   never observed and the other level may have been hit inside it.
3. **Unobserved round trips.** Between any two snapshots the true path may
   have visited a level and returned. This is unobservable in principle from
   snapshot data. `maximum_observation_gap_seconds` and
   `observation_gap_before_first_touch_seconds` are persisted so a reader can
   judge how much room there was for it.

## Outcome vocabulary

| Status | Meaning |
|---|---|
| `TARGET_1_FIRST` | T1 touched at a strictly earlier instant than any stop touch |
| `TARGET_2_FIRST` | the first target-crossing observation was already at or above T2, so T1 was never independently observed |
| `STOP_FIRST` | stop touched first, or the only level touched |
| `STOP_AFTER_T1` | T1 first, stop later |
| `STOP_AFTER_T2` | T2 reached before the stop |
| `NO_EXIT_BY_CONTINUOUS_CLOSE` | no level touched inside the window |
| `BOTH_STOP_AND_TARGET_TOUCHED_SAME_INSTANT` | same `market_timestamp`; not ordered |
| `EXIT_UNDETERMINED_PRICE_DATA_GAP` | first touch followed a gap wider than the budget |
| `NO_FUTURE_DATA` | no quote at or after detection inside the window |

| Evidence source | |
|---|---|
| `RUBIX_QUOTE_SEQUENCE_DISTINCT_INSTANTS` | ordered by strictly different exchange timestamps |
| `RUBIX_QUOTE_SEQUENCE_SINGLE_MILESTONE` | only one side ever touched; nothing to order |
| `RUBIX_QUOTE_SAME_INSTANT_UNRESOLVED` | tie that exchange time did not break |
| `RUBIX_QUOTE_OBSERVATION_GAP_UNRESOLVED` | ordering would rest on unobserved time |
| `NOT_APPLICABLE` | no touch, or qualification unusable |

| Quality | |
|---|---|
| `MEASUREMENT_COMPLETE` | dense window reaching the close |
| `PARTIAL_WINDOW` | last observation more than the budget before 14:15 |
| `PRICE_DATA_GAP` | an intra-window gap wider than the budget |
| `NO_FUTURE_DATA` | nothing to measure |

Nothing beyond 14:15 is measured or zero-filled. The auction window
(14:15–14:25) and post-market rows are excluded.

## Milestones, not exits

T1 and T2 imply a staged exit under some execution model. **No execution model
exists**, so v2 records milestones and leaves position-level profit and loss
undefined:

* `stop_reached`, `target_1_reached`, `target_2_reached` and their first-touch
  timestamps are always recorded when observed, including under an ambiguous
  status.
* `target_1_observed_before_target_2` is `False` when a single observation
  crossed both — reaching T2 does not prove T1 was independently observed.
* `first_milestone` / `exit_price_proxy` / `exit_at_utc` describe the **first
  level the observed path touched**, not a closed position.
* There is no position size, no partial fill, no P&L field, and no realized
  anything.

`measurement_return_pct` and `proxy_r_multiple` are computed **only** under the
five determined statuses, and the schema enforces it:

```sql
CHECK (measurement_return_pct IS NULL OR first_milestone IS NOT NULL)
CHECK (proxy_r_multiple      IS NULL OR first_milestone IS NOT NULL)
```

`proxy_r_multiple = (exit_price_proxy − entry_price_proxy) / risk_per_share`.
Measured from the **entry proxy**, not the trigger: against the trigger the
answer would be a tautological −1R or +1R, describing the rule rather than the
session.

## Schema and migration

`orb_signal_outcomes.db` `SCHEMA_VERSION` 1 → 2, adding
`orb_signal_outcomes_v2` beside the untouched v1 table. A separate table rather
than columns bolted onto v1, because the two answer different questions from
different evidence: v1 rows have no stop or target and never will, so a shared
table would need every v2 column nullable and every reader to guess which
generation a row belongs to.

Additive and idempotent — `CREATE TABLE IF NOT EXISTS`, no column altered or
removed. Writes upsert on `(session_date, canonical_ticker, lane_a_run_id)`, so
re-measuring replaces rather than accumulating. The whole batch writes in one
transaction: a rejected row leaves none behind.

## Verification

| Check | Result |
|---|---|
| Focused tests | 32 passed |
| Full suite | 3,167 passed, 8 skipped |
| `git diff --check` | clean |
| Real-schema round trip | 8 level fields read back from a migration-9 database identical to the engine objects at full float precision |
| Ordering | `TARGET_1_FIRST` +1.048 proxy R, `STOP_FIRST` −1.048, `NO_EXIT_BY_CONTINUOUS_CLOSE` with no R |
| Dry run over real sessions | 0 qualified signals across 5 databases; nothing reconstructed |
| v1 store | 34 rows, `TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED`, unchanged |

## Limitations

1. **Zero signals measured so far.** Nothing validates v2 against live data
   until a session runs with qualification persistence deployed.
2. **Snapshots, not trades.** Rubix polls; it does not receive every print.
   Levels crossed and un-crossed between two snapshots are invisible, and no
   status can recover them.
3. **A touch is not a fill.** Even an unambiguous `TARGET_1_FIRST` says the
   observed price reached the level, not that anyone could have transacted
   there. Spread, depth and queue position are absent from this evidence.
4. **`proxy_r_multiple` is not a return on a trade.** It is the distance from
   an observed quote to an observed level, divided by the engine's risk unit.
5. **The entry proxy is not the trigger.** Median entry lag on the historical
   34 was 4.4 s, but a proxy taken after a fast move away from the trigger will
   flatter or punish the R for reasons that have nothing to do with the rules.
   Both values are stored so the difference stays visible.
6. **No verdict.** v2 measures; it does not conclude. The `CONCERNING` reading
   of the historical 34 stands on its own terms and is unaffected by this work.

## Files

| File | Role |
|---|---|
| `scalping_orb/performance/qualified_outcomes.py` | vocabulary, discovery, measurement |
| `scalping_orb/performance/outcome_store.py` | v2 table, `QualifiedOutcomeStore` |
| `scripts/run_orb_qualified_outcomes.py` | CLI |
| `tests/test_orb_qualified_outcomes.py` | 32 tests |
