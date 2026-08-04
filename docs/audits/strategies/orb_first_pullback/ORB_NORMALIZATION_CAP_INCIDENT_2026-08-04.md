# ORB Normalization Cap Incident — 2026-08-04

**Research only. Production execution disabled.** Nothing here is a trade
signal, a recommendation, or a performance claim.

The 2026-08-04 ORB shadow session remains classified
`PARTIAL_SHADOW_SESSION`. This document explains why. It does not promote,
re-rate, or re-open that classification, and no evidence produced while
validating the fix is admissible as live Lane A evidence for that day.

---

## 1. What happened

The live Lane A run read **1,027,173** source rows across the continuous
session and stopped producing normalized events at **12:11 Cairo** — roughly
two hours and four minutes before the 14:15 continuous close. Throughout that
window the run kept reporting `LIVE_SHADOW_HEALTHY`, kept advancing its source
cursor, and kept recording heartbeats.

The session report claimed **778,125 exact redeliveries removed**. That number
was not measured. It was computed as `source_rows_read − normalized_events`,
so every silently discarded row was counted as a duplicate.

## 2. Root cause

Rubix never populates `quotes.sequence` — it is `NULL` on all 1,102,266 rows
recorded that day. Deduplication therefore falls back to a **market-payload
identity**: a hash over the verified market fields
(`ticker`, `market_timestamp`, `last_price`, `bid`, `ask`, `cumulative_volume`).
Two events that differ in any verified field keep distinct identities even when
they share a timestamp; an exact redelivery collapses regardless of receive
time or source row id.

Identities were accumulated in a **session-wide set** with a fixed
**count-based** ceiling:

```python
# scalping_orb/config.py (before)
maximum_seen_payloads_per_session: int = 250_000
```

```python
# scalping_orb/events.py (before)
if sequence_identity in seen_payloads:
    issue("DUPLICATE_MARKET_PAYLOAD_IDENTICAL")
    return None, tuple(issues)
if self._seen_payload_count >= self.config.maximum_seen_payloads_per_session:
    issue("DEDUP_MEMORY_LIMIT_REACHED", ...)
    return None, tuple(issues)          # <-- an UNSEEN payload, discarded
```

The second branch is the defect. Once 250,000 distinct identities had been
admitted, **every genuinely new payload took that return path**. It was not
mistaken for a duplicate by the identity check — it never reached it — but the
outcome was identical to a duplicate, and the aggregate counter then labelled
it one.

The cap was **count-based**, **global across every `(ticker, session_date)`
key**, and had **no eviction**. There was no way to fall out of the exhausted
state before the next session rollover.

Three properties made this silent rather than loud:

1. `DEDUP_MEMORY_LIMIT_REACHED` was a quality event, not a health state.
2. `live_status_for` derived health from source freshness alone, so a live
   source behind a dead normalizer still reported `LIVE_SHADOW_HEALTHY`.
3. `classify_session` had no criterion for pipeline progress, so nothing in
   the FULL gate could observe the two-hour hole.

## 3. Measured evidence

Taken from the canonical Rubix database, read-only, continuous session
(07:00–11:15 UTC = 10:00–14:15 Cairo):

| Measure | Value |
|---|---|
| Raw rows | 1,027,173 |
| Distinct payload identities | 459,223 |
| Exact redeliveries | 567,950 (55.3%) |
| Mean rows per minute | 4,028 |
| Distinct identities, whole day | 494,400 |
| `quotes.sequence` NULL | 1,102,266 / 1,102,266 |
| `quotes.id` range | 3,962,038 … 5,064,303 (monotonic) |

**The cap was exhausted by design, not by anomaly.** A normal EGX session
carries roughly 459k distinct identities in the continuous window alone —
1.8× the ceiling. The session crossed 250,000 at about 12:11 Cairo and
discarded everything after it.

### Redelivery window

For identities appearing more than once, the source row-id distance between
first and last occurrence:

| Percentile | Row-id span |
|---|---|
| p50 | 39 |
| p90 | 72 |
| p99 | 140 |
| p99.9 | 347 |
| max | 1,026,893 |

The long tail is not a redelivery. It is an unchanged quote for an illiquid
symbol repeating hours later, which the payload identity cannot distinguish
from a replay. Real reconnect redeliveries land inside a few hundred rows.

### Why a session-wide set was never necessary

`ShadowSourceReader` reads with `WHERE id > ? ORDER BY id`, so a run never
re-reads a row it has already consumed. **Polling batches do not overlap.**
Payload deduplication exists solely to absorb *source-side* redelivery — the
collector writing the same payload twice under different row ids — and that
happens within the narrow window measured above. Retaining identities for an
entire session bought nothing and cost the session.

Memory cost per identity is roughly 100–150 bytes (a 64-character hex digest,
a tuple key, and dict overhead), so 250,000 identities is on the order of
30 MB. The original ceiling was a memory guard. It was the correct concern
solved by the wrong mechanism: a hard stop instead of a bound.

## 4. The fix

**Rolling retention with eviction, not a hard stop.**

```python
# scalping_orb/config.py (after)
deduplication_retention_payloads: int = 100_000
deduplication_hard_capacity: int = 400_000
normalization_stall_max_cycles: int = 20
normalization_stall_max_seconds: float = 300.0
evaluation_stall_max_seconds: float = 600.0
```

The identity store is an `OrderedDict` used as a rolling window. When it is
full, the **oldest** identity is evicted and the new payload is admitted. An
unseen payload is never discarded for capacity reasons.

Retention of 100,000 entries covers roughly 25 minutes of source rows at the
observed 4,028 rows/minute — about **288× the p99.9 redelivery span**. Memory
stays bounded at approximately 12 MB.

The trade-off is stated explicitly: an identity evicted after 100,000 newer
rows and then re-observed is treated as new. Given the measured redelivery
distribution, such an event is an unchanged quote hours later rather than a
replay, and admitting it is the safer error — it cannot delete real data.

`deduplication_hard_capacity` is a backstop. It is unreachable while eviction
works, and if a future regression breaches it the run latches
`DEDUPLICATION_CAPACITY_EXHAUSTED` and reports itself unhealthy rather than
quietly dropping data.

Removing `maximum_seen_payloads_per_session` and adding the settings above
changes `OrbDataConfig.fingerprint`, so runs recorded under the old and new
deduplication contracts can never be compared as if they were equivalent.

## 5. Evaluation liveness

`scalping_orb/liveness.py` separates the three conditions that must all hold
for a live run to be healthy, and names the failure when they do not.

| Status | Meaning |
|---|---|
| `NORMALIZATION_HEALTHY` | rows are becoming events |
| `NORMALIZATION_STALLED` | rows arrived and produced no events, past the cycle or time limit |
| `DEDUPLICATION_CAPACITY_EXHAUSTED` | the identity store breached its hard capacity |
| `NORMALIZATION_IDLE_NO_SOURCE_ROWS` | no rows arrived — a source condition, not a normalization fault |
| `EVALUATION_HEALTHY` | events are reaching symbol evaluation |
| `EVALUATION_STALLED` | no symbol evaluated within the limit |
| `EVALUATION_IDLE_NO_EVENTS` | nothing to evaluate yet |

Two distinctions matter:

* **A quiet source is not a stall.** Zero rows read is reported as
  `NORMALIZATION_IDLE_NO_SOURCE_ROWS`. Only rows that arrive and produce
  nothing count toward a stall.
* **A recorded critical stall is permanent for the run.** The monitor latches
  it. A session that stalls at 12:11 and resumes at 13:30 still carries the
  gap, and cannot classify as FULL by running long enough afterwards.

`LIVE_SHADOW_HEALTHY` now requires a live source *and* a live pipeline. Two
new statuses — `LIVE_SHADOW_NORMALIZATION_STALLED` and
`LIVE_SHADOW_EVALUATION_STALLED` — carry the difference. An empty poll no
longer resets a latched stall to healthy.

## 6. Classification

`classify_session` gained three **required** keyword arguments:

* `normalization_progress_through_continuous_end`
* `evaluation_progress_through_continuous_end`
* `no_critical_evaluation_stall`

They are required rather than defaulted so that a caller unable to prove
liveness fails loudly instead of inheriting a FULL classification. Their
failure reasons are `NORMALIZATION_STALLED_BEFORE_CONTINUOUS_END`,
`EVALUATION_STALLED_BEFORE_CONTINUOUS_END` and
`CRITICAL_EVALUATION_STALL_OBSERVED`.

Under the new gate the 2026-08-04 shape — perfect source coverage, dead
pipeline after 12:11 — classifies as `PARTIAL_SHADOW_SESSION` on liveness
grounds alone.

## 7. Reporting

The session report now separates **source health** from **evaluation pipeline
liveness**, reports real deduplication counters instead of a subtraction, and
prints an `EVALUATION PIPELINE STALLED` warning when a critical stall was
recorded, stating plainly that source coverage describes what was *read*, not
what was *observed*.

## 8. Status of the incident session

The 2026-08-04 session remains:

> **`PARTIAL_SHADOW_SESSION`**

It is not retroactively promoted. Any replay performed while validating this
fix is **HISTORICAL CONTROLLED REPLAY / NOT LIVE LANE A EVIDENCE / NOT
ADMISSIBLE AS `FULL_SHADOW_SESSION_OBSERVED`**. The incident databases, the
session report and the comparison report were not modified.
