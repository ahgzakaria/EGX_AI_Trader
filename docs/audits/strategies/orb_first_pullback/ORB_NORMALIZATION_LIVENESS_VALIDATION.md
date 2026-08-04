# ORB Normalization and Liveness — Validation Record

**Research only. Production execution disabled.**

Validation evidence for the fix described in
[ORB_NORMALIZATION_CAP_INCIDENT_2026-08-04.md](ORB_NORMALIZATION_CAP_INCIDENT_2026-08-04.md).

Nothing in this document promotes, re-rates, or re-opens the 2026-08-04
session. That session remains `PARTIAL_SHADOW_SESSION`.

---

## 1. Controlled high-volume replay

**HISTORICAL CONTROLLED REPLAY / NOT LIVE LANE A EVIDENCE /
NOT ADMISSIBLE AS `FULL_SHADOW_SESSION_OBSERVED`.**

An isolated snapshot of the 2026-08-04 continuous session (10:00–14:15 Cairo)
was copied out of the canonical Rubix database — opened read-only, never
written — into a scratchpad SQLite file, and replayed through the fixed
normalizer. No production database, research database, report or task state
was touched.

| Measure | Result |
|---|---|
| Raw rows replayed | 1,027,173 |
| Events admitted | **459,230** |
| Exact redeliveries collapsed | 567,943 |
| Identity store peak entries | 100,000 / 100,000 capacity |
| Rolling-window evictions | 359,230 |
| Capacity exhausted | `False` |
| Critical stall observed | `False` |
| Quality codes emitted | `DUPLICATE_MARKET_PAYLOAD_IDENTICAL` only |

**The old ceiling would have admitted exactly 250,000.** The replay admitted
459,230 distinct payloads from the same rows, so the previous implementation
discarded **209,230 genuinely new market payloads** on 2026-08-04 while
reporting the session healthy.

Two properties hold simultaneously, which is the whole point of the fix:

* every distinct payload is admitted,
* memory stays bounded — the identity store never exceeded its 100,000-entry
  retention window across a million rows.

Deduplication still works: 567,943 exact redeliveries were collapsed and were
the *only* quality code emitted across the entire replay.

### 1.1 Identity-level reconciliation

The replay admitted 459,230 events against 459,223 distinct identities measured
by SQL. Those seven events are accounted for exactly, not approximately.

| Quantity | Value |
|---|---|
| `A` events admitted | 459,230 |
| `P` distinct Python payload identities (unbounded ledger) | 459,223 |
| `S` distinct SQL identities | 459,223 |
| `P − S` identity-expression drift | **0** |
| `A − P` re-admissions after rolling-window eviction | **7** |
| `A + exact redeliveries = raw rows` | 459,230 + 567,943 = 1,027,173 ✔ |

**The two identity expressions agree perfectly.** Every row is either admitted
or collapsed as an exact redelivery; nothing is dropped, and no row expands
into more than one event. The seven are not a measurement artefact and not
rounding.

They are re-admissions of an *unchanged* payload whose identity had already
been evicted:

| Source row | First seen | Gap (raw rows) | Symbol | Market timestamp | Received |
|---|---|---|---|---|---|
| 5,029,737 | 4,002,888 | 1,026,849 | DEIN | 07:00:01 | 11:15:01 |
| 5,029,833 | 4,722,080 | 307,753 | ICLE | 09:56:47 | 11:15:03 |
| 5,029,837 | 4,003,289 | 1,026,548 | SIMO | 07:00:01 | 11:15:03 |
| 5,029,893 | 4,287,015 | 742,878 | SAIB | 07:57:33 | 11:15:04 |
| 5,029,894 | 4,003,273 | 1,026,621 | GPPL | 07:00:01 | 11:15:04 |
| 5,029,931 | 4,322,026 | 707,905 | MEGM | 08:06:12 | 11:15:04 |
| 5,029,963 | 4,185,572 | 844,391 | SPHT | 07:34:20 | 11:15:04 |

All seven share the same shape:

* **`last_price = 0.0` and `volume = 0.0`** on every one — non-trading
  placeholder quotes carrying at most a lone bid or ask. `_number(...,
  positive=True)` maps zero to `None`, so the normalized events carry no
  price and cannot move any bar's open, high, low or close.
* **frozen market timestamps** (07:00:01 through 09:56:47) for symbols that
  never traded again that session, which is why the recurrence is not
  out-of-order.
* **received at 11:15:01–11:15:04 UTC** — 14:15:01–14:15:04 Cairo, the
  collector's sweep at the continuous close.
* **gaps of 307,753 to 1,026,849 raw rows**, far beyond the 100,000-admission
  retention window.

An unchanged quote re-emitted hours later, after nothing retained could
recognise it, is admitted again. This is **outcome A**: a legitimate, bounded,
deterministic expansion — 7 events in 459,230, or 0.0015%. It is not duplicate
inflation, because within the retention window the identical payload is always
collapsed, and Lane B replays the same rows in the same order under the same
retention, so it reproduces the same seven.

### 1.2 The invariant

```
events admitted
  = unique source payload identities
  + payloads recurring after more than `deduplication_retention_payloads`
    distinct admissions
```

with the second term deterministic in the retention size and zero when
retention exceeds the session's distinct-identity count. Pinned by
`test_a_payload_recurring_beyond_the_window_is_a_bounded_documented_expansion`,
which asserts both halves: re-admitted beyond the window, collapsed inside it.

## 2. Test suite

New file: `tests/test_orb_normalization_liveness.py` (21 tests) covering
normalization health, evaluation health, live status, session classification
and the configuration contract.

Extended: `tests/test_orb_phase2a_gate_closure.py` — the previous
`test_dedup_memory_is_bounded_and_cleared_on_session_rollover` asserted the
defect as correct behaviour (an unseen payload being blocked at capacity). It
now asserts the opposite contract, alongside three new deduplication tests.

Notable contracts under test:

* an unseen payload is never dropped for capacity reasons,
* an exact redelivery inside the retention window is still collapsed,
* 5,000 distinct payloads against a 1,000-entry window admit all 5,000,
* breaching the hard-capacity backstop latches the defect but still admits
  the payload,
* a quiet source is never reported as a normalization stall, however long it
  stays quiet,
* a recorded critical stall survives recovery,
* `classify_session` liveness arguments are required, not defaulted.

## 3. Mutation testing

Seven mutations, each reintroducing a specific route back to the 2026-08-04
failure. All seven were killed.

| Mutation | Result |
|---|---|
| M1 never evict — the identity store grows without bound | KILLED |
| M2 restore the hard drop — discard unseen payloads at capacity | KILLED |
| M3 capacity exhaustion never reaches the health state | KILLED |
| M4 blame normalization for a quiet source | KILLED |
| M5 let a recovered run erase its recorded critical stall | KILLED |
| M6 live status ignores pipeline liveness (source freshness only) | KILLED |
| M7 liveness criteria default to `True` instead of being required | KILLED |

M2 and M4 survived the first pass. Both were genuine gaps rather than
mutation-harness artefacts:

* the hard-capacity branch was unreachable from any test, so a regression
  that reintroduced the hard drop went unnoticed;
* the quiet-source test observed a single idle cycle, which was too short to
  distinguish "idle" from "accumulating toward a stall".

Both gaps were closed with tests that fail against the mutation and pass
against the fix.

## 4. Regression scope

`OrbDataConfig.fingerprint` changes with this commit:
`maximum_seen_payloads_per_session` is removed and five settings are added.
Runs recorded under the old and new deduplication contracts therefore carry
different configuration identities and cannot be silently compared.

The full test suite was run before committing; results are recorded in the
commit message.
