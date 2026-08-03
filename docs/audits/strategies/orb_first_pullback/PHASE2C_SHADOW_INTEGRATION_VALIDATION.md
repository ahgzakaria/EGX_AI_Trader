# Phase 2C — Shadow Integration Validation

Base HEAD `6d10334`. Branch `feat/scalping-orb-shadow-integration`. Not
committed, not merged, not pushed.

---

## 1. Verdict

# `APPROVED_FOR_FULL_SHADOW_SESSION`

Read that narrowly. It says the **integration** is technically validated: it can
observe the existing collector safely, batch a session, evaluate the unchanged
Phase 2B engine, and keep live observation separate from later reconstruction.

| Claim | Status |
|---|---|
| Integration technically validated | **yes** |
| Partial smoke performed | **yes** — one bounded read-only batch against the live collector |
| Full live Shadow session | **NOT PERFORMED — still pending** |
| Strategy calibrated | **no** — no threshold was touched or tuned |
| Strategy profitability-validated | **no** — no performance measure exists or is claimed |
| Production execution | **DISABLED** |

`FULL_SHADOW_SESSION_OBSERVED` is **not** issued. It requires a runner that
genuinely started before 10:00 Cairo and stayed healthy through 14:15. This run
started after the session had already closed.

## 1b. Defects found and fixed in the final pre-commit review

Six. Five were places where an invariant was *intended* but nothing actually
enforced it — the code read correctly and the tests agreed with it.

| # | Defect | Fix |
|---|---|---|
| 1 | **`--reconstruct` wrote Lane A rows.** `run_cycle` called `evaluate_live` unconditionally, so a post-session reconstruction fabricated "live observations" it never made, and could overwrite a real run's account of the session | reconstruction writes no Lane A rows at all; regression test asserts `orb_shadow_live_states` stays empty |
| 2 | **No source/destination collision guard.** Nothing stopped the research target being the Rubix source itself — or its `-wal`/`-shm` sidecars, which would corrupt the collector's write-ahead log | `_assert_distinct_databases` resolves both paths and refuses the source and its sidecars |
| 3 | **Enums persisted as reprs.** On Python 3.11+ `str(member)` of a `str`-mixin Enum yields `"ClassName.MEMBER"`. Every `live_status`, `classification`, `evaluation_mode` and `difference_reason` column was written mangled, breaking equality against the declared vocabulary | `_enum_value` helper writes `member.value`; test asserts every stored status parses back into its enum |
| 4 | **The comparison collapsed differences.** Missing `DATA_UNAVAILABLE` and `STATE_DIFFERENCE` entirely, and a genuine divergence was labelled `LATE_EVIDENCE_CHANGED_OUTCOME` — asserting a cause it had not established | typed `ComparisonReason` with all seven required categories and a documented precedence |
| 5 | **Freshness was keyed on the wrong signal.** The category checked only feed-level `live_status`, but a feed can be perfectly fresh while Phase 2A's `LiveDecisionCapability` still refuses to let live decide. This mis-attributed **34 of 34** lane differences in the smoke — including the one research candidate — to opening-range revisions | `_live_rejected_on_freshness` checks feed status **and** the Lane A record's own state and rejection reasons |
| 6 | **Benchmark presented one phase as the whole.** `0.14 s` was the engine evaluation only; bar aggregation and opening-range cost were folded into an opaque "snapshot build", and persistence was excluded from the total | per-phase timings (§4), with the total stated explicitly |

Defect 5 materially changed how the partial smoke reads — see §5.2.

## 2. What was built

| Module | Role |
|---|---|
| `scalping_orb/shadow_source.py` | `mode=ro` incremental reader + restart-safe `ShadowCursor` |
| `scalping_orb/shadow_snapshot.py` | `ShadowSessionSnapshot`, `ExchangeWatermark`, quality summary |
| `scalping_orb/shadow_service.py` | Lane A / Lane B evaluation, comparison, session classification |
| `scalping_orb/repository.py` | migration 5 and eight additive `orb_shadow_*` tables |
| `scripts/run_orb_shadow_session.py` | CLI runner (`--once` / `--follow` / `--reconstruct`) |
| `tests/test_orb_phase2c_shadow.py` | 73 deterministic tests |

The Phase 2B engine file is **unchanged** — asserted by test, not by assertion.

## 3. Two findings that changed the picture

Both were discovered by inspecting the real source during the audit, and both
are recorded because they contradict figures previously on record.

### 3.1 `sequence` is 100% NULL

Across the most recent 200,000 production rows, `count(DISTINCT sequence) = 0`.
No exchange sequence exists. The cursor therefore uses `quotes.id`
(`INTEGER PRIMARY KEY AUTOINCREMENT`) as a **source-progress cursor only**, and
market identity stays with Phase 2A's `_source_identity` /
`_sequence_payload_identity`.

This matters because `market_timestamp` has one-second granularity and collides
constantly — 88,268 `(ticker, market_timestamp)` groups held more than one row
in that sample. A `(ticker, timestamp)` key would have silently discarded
genuinely distinct events.

### 3.2 Measured receive lag is far better than the 165 s on record

| Metric | Previously on record | Measured, 100k production rows | Measured, smoke (88,148 events) |
|---|---|---|---|
| median lag | ~165 s | 1.09 s | **1.00 s** |
| p90 | — | 1.85 s | 1.59 s |
| p95 | — | 2.07 s | 1.74 s |
| over 60 s budget | 58.9% | 0.0% | **0.0%** |
| negative lag | — | 1,119 rows | 34 events |

**This is a measurement, not a live-readiness claim.** It may reflect a
collector improvement, a quiet window, or both. Phase 2C keeps freshness
fail-closed regardless, and the full-session blocker stays open. Negative lag is
counted rather than clamped — it is clock skew, and hiding it would flatter the
feed.

### 3.3 Session density has changed materially

Phase 2B found zero breakouts largely because 5-minute bars were scarce in the
July sessions it sampled. Recent sessions are an order of magnitude denser
(2026-08-03: 1,048,339 rows / 224 tickers, vs ~185,000 / 265 in late July). This
changes no threshold and licenses no claim; it means a future full session has a
realistic chance of exercising the downstream path.

## 4. Batching — `REPLAY_SESSION_BATCHING_REQUIRED` is closed

Deterministic synthetic benchmark: **200 symbols, one full continuous session
(10:00–14:15), 204,000 rows, 4 quotes/symbol/minute.**

| Measure | Result |
|---|---|
| Source rows read | 204,000 |
| **Session loads** | **1** |
| **Repository event loads during evaluation** | **0** |
| Symbols in snapshot | 200 |
| Symbols evaluated (full batch) | 200 |
| Completed 1-minute bars | 51,000 |
| Completed 5-minute bars | 10,200 |
| Opening ranges READY | 200 |
| Peak snapshot memory | 46.0 MB |

Per-phase timings. **The engine evaluation is one phase, not the batch total** —
stating 0.14 s as "the benchmark" would be misleading:

| # | Phase | Seconds | Share |
|---|---|---:|---:|
| 1 | source read | 1.91 | 6% |
| 2 | **normalization** (Phase 2A code) | **27.23** | **85%** |
| 3 | snapshot build (total) | 2.73 | 9% |
| 3a | — group by ticker | 0.056 | |
| 3b | — bar aggregation | 2.607 | |
| 3c | — opening-range calculation | 0.038 | |
| 4 | engine evaluation, all 200 symbols | 0.138 | 0.4% |
| 5 | persistence | 0.014 | 0.04% |
| | **BATCH TOTAL** | **32.02** | 100% |
| | incremental cycle | 2.62 | |

The architectural requirement is met: **the session is loaded once, and
evaluation touches no database at all.** Evaluating 200 symbols costs 0.14 s
because they are read out of the shared snapshot — but that is 0.4% of the
batch, and the batch is what a cycle actually costs.

No production latency promise is made.

### 4.1 `NORMALIZER_THROUGHPUT_OPTIMIZATION_PENDING`

Normalization is **85%** of batch time (27.2 s per 204,000 rows ≈ 7,500 rows/s).
It is Phase 2A code and was deliberately not touched here: the permitted bar for
a change is byte-identical behaviour with tests proving identical normalized
identities, no skipped rows and bounded memory. That is a focused piece of work,
not a finalization-task edit. Recorded as the next performance target.

`REPLAY_SESSION_BATCHING_REQUIRED` remains **CLOSED**.

## 5. Partial smoke against the live collector

Permitted only because every gate condition was met first: all synthetic tests
green, source path supplied explicitly, `mode=ro` + `query_only=ON`, destination
an ignored research database, no process started or stopped, no credentials
copied, bounded to a single `--once` batch, and labelled as a smoke.

```
--rubix-db-path <production> --session-date 2026-08-03 --once --smoke
--batch-size 200000 --research-db-path data/research/orb_phase2c_smoke.db
```

| Measure | Result |
|---|---|
| Classification | **`PARTIAL_SMOKE_SESSION`** |
| Reasons | started after continuous open; insufficient heartbeat coverage; insufficient exchange-minute coverage |
| Source rows read | 200,000 |
| Normalized events | 88,148 (≈111,852 exact redeliveries removed) |
| Symbols observed | 224 |
| Session loads | **1** |
| Completed 1-minute bars | 7,186 |
| **Completed 5-minute bars** | **1,290** |
| Opening ranges READY | 169 |
| Lane A live evaluations | 205 |
| Lane B reconstruction evaluations | 224 |

For scale: Phase 2B's entire 3-session replay produced **504** completed
5-minute bars. One partial batch of one dense session produced **1,290**.

### 5.1 The one research candidate, and why it is not good news yet

Lane B reached `ENTRY_READY_RESEARCH` for exactly one symbol (`LCSW`). Lane A
recorded `BREAKOUT_REJECTED_STALE` for the same symbol.

State this plainly:

- **The LCSW `ENTRY_READY_RESEARCH` existed only in Lane B reconstruction.**
- **Lane A rejected it, on stale live-decision capability.** Phase 2A's
  `LiveDecisionCapability` has no enabled member, so live cannot reach readiness
  by construction rather than by configuration. The rejection is the fail-closed
  design working, not a data problem.
- An opening-range revision was **also** present and contributed to the lane
  difference, but it is not the headline cause (§5.2).
- **It was not actionable live.**
- **It is not a BUY signal.** No signal, alert, order or paper trade exists.
- **It is not calibration evidence.** No threshold was touched.
- **It is not profitability evidence.** No performance measure exists.
- The run was **partial** and cannot close the full-session blocker.

Any report showing this state must carry its `HISTORICAL_REPLAY` mode and
Research Only label. The generated `shadow_reconstruction_states.csv` and the
`orb_shadow_reconstruction_states` table both carry `evaluation_mode` and
`research_only`; the run summary reports live and reconstruction counts as
separate fields and never merges them.

### 5.2 Lane A versus Lane B across all 224 symbols — corrected

Review defect 5 changed this materially. The taxonomy was re-derived from the
smoke's **own persisted research database** — no new read of the live source:

| Difference reason | Symbols | Previously reported |
|---|---:|---|
| `IDENTICAL` | 135 | 171 |
| `DATA_UNAVAILABLE` | 36 | *(folded into IDENTICAL)* |
| `FRESHNESS_REJECTED_LIVE` | **34** | *(reported as `OPENING_RANGE_REVISED`)* |
| `HISTORICAL_ONLY` | 19 | 19 |

Two corrections, both making the picture more honest:

1. 36 symbols where **both** lanes had no usable evidence were previously
   counted as agreement. They are now their own category.
2. All 34 lane differences — including LCSW — were **live-capability
   rejections**, not opening-range artefacts. The earlier attribution implied a
   versioning quirk; the truth is that live decisions are disabled, which is a
   much stronger statement about what was knowable live.

19 symbols were reconstructable but not evaluable live at all. That number is
the point of the whole comparison, and it will only be meaningful after a real
full session.

An artefact worth stating: 141 reconstruction states are `AUCTION_PHASE`,
because the smoke ran after the session had closed, so `as_of` was past the
auction boundary. That is expected for a post-session smoke, not a defect.

## 6. Source safety — evidence, not assurance

The production Rubix main database changed content hash during the smoke window
(size identical, `ca6ab8bd…` → `3e9c1dab…`). That was investigated rather than
assumed:

| Test | Result |
|---|---|
| Control: two hashes 45 s apart, **no** Phase 2C activity | **identical** — file is stable when idle |
| Causal: hash → 150,000-row read-only read → hash | **identical** — the read changes nothing |
| Earlier merge task: same file grew ~4.6 MB and ~5.7 MB with **no Phase 2C code in existence** | the collector demonstrably rewrites this file on its own |
| Write attempt on the Phase 2C connection | `sqlite3.OperationalError` (asserted by test) |

Conclusion: the change was a collector-driven WAL checkpoint (same size,
rewritten pages) that overlapped the smoke window. A `mode=ro` +
`query_only=ON` connection is physically incapable of causing it. Every Phase 2C
write went to the ignored research database.

Additionally: no process was started, stopped, restarted or killed — the same 8
Python PIDs, all started 08:54, were present before and after. No checkpoint,
`VACUUM`, WAL truncation, `ATTACH`, schema change or exclusive lock was issued
by this phase.

## 7. Migration 5

Additive. `SCHEMA_VERSION` 4 → 5, eight `orb_shadow_*` tables. Verified against
a **populated** v4 fixture built through the real Phase 2A ingestion path:
identical row counts *and* identical `CREATE TABLE` text for all 13 Phase 2A/2B
tables; WAL and foreign keys retained; `foreign_key_check` clean; repeat
migration stable; injected failure rolls back atomically leaving v4 untouched;
downgrade refused.

No order, execution, position, trade, P&L, broker or notification table exists —
asserted by a column-level scan, not only a table-name scan.

Four Phase 2B tests pinned `user_version == 4` and were updated to test their
actual intent: the v3→v4 hop is now pinned explicitly with
`target_schema_version=4` so it keeps testing that specific transition, and the
default-version test compares against `SCHEMA_VERSION`.

## 8. Tests

| Suite | Result |
|---|---|
| Phase 2C Shadow | **113 passed** (73 + 40 added by this review) |
| Phase 2B Core | **131 passed** |
| Phase 2A | **122 passed** |
| Repository / migration / SQLite | 68 passed |
| Session / Rubix read-only | 331 passed |
| Scalping | 56 passed |
| AI Analysis | 274 passed |
| Infographic / card | 187 passed |
| **Full suite** | **2267 passed, 7 skipped, 0 failed** |
| `git diff --check` | clean |

The 40 added tests cover each defect in §1b plus the cases the review required:
cursor atomicity A–E; Lane A immutability under a corrected opening range;
every comparison category reachable and distinct; exact grace-deadline and
exchange-boundary behaviour (10:00 / 10:15 / 13:30 / 14:15 / 14:25, and
microsecond boundaries); source-equals-destination and sidecar refusal;
production-target refusal; `--follow` stop conditions and graceful shutdown; no
process left behind; anti-fabrication of a `FULL` classification.

The 7 skips are the pre-existing EODHD cache guards in
`tests/test_current_research_correctness.py`, untouched by this phase. No skip
was added.

### 8.1 The tests were checked for teeth

A suite that passes first time proves little. Three deliberate mutations were
injected and each was caught:

| Mutation | Caught by |
|---|---|
| `live_evidence_fresh` always `True` | 3 tests failed |
| lateness grace removed (bars promoted immediately) | 1 test failed |
| post-10:00 start allowed to classify as FULL | 1 test failed |

All sources were restored and the suite re-verified green.

## 9. Remaining blockers

1. **One full live Shadow session — still the primary blocker.** The runner must
   start before 10:00 Cairo and stay healthy through 14:15. Nothing in this
   phase substitutes for it, and the partial smoke explicitly does not.
2. **No threshold is calibrated.** Defaults remain `INITIAL_RESEARCH_DEFAULTS`.
   One partial batch is emphatically not a calibration input.
3. **No profitability evidence of any kind**, and none is claimed.
4. **`NORMALIZER_THROUGHPUT_OPTIMIZATION_PENDING`** — 85% of batch time (§4.1).
   Not addressed here; correctness was not traded for speed.
5. **Live decision capability remains disabled by construction.** Re-enabling it
   is a deliberate Phase 2A capability change to be reviewed on its own merits,
   not a Phase 2C side effect.

## 9b. Protected paths — one changed externally, and it is recorded honestly

`docs/audits/strategies/SCALPING_MULTI_SESSION_VERDICT.md` in the **main**
working copy changed during Phase 2C:

| | |
|---|---|
| before | `6FC81B43…3A64E0AA6` |
| after | `963F056D…905E1F0F53` |

It was **not** changed by this phase. It is marked "(auto-generated)", is
written only by `scripts/run_scalping_session_validation.py`, and is referenced
by no Phase 2C file. The change is data-driven regeneration by the existing
production validation workflow: 2 sessions → 8 sessions, adding 2026-07-26
through 2026-08-02, and `WAITING_FOR_SESSIONS` → `PAPER_RECORDING_ELIGIBLE`.

The current main working-copy bytes are treated as **externally generated and
authoritative**. It was not reverted, edited, staged, copied or normalized.

The isolated worktree holds the clean committed version (`edbc72ad…`), which
differs. **A future merge must not copy the worktree version over main.**

Unchanged throughout: `data/paper_trades.csv` (`e9e9620d…`) and `.codex/`
(content aggregate `3c8d9703…`, 2,671 files, 50,307,956 bytes).

## 9c. Production source hash — why a single hash is not the proof

The Rubix main database changed content hash during the smoke window (size
identical). A single before/after hash is **not** valid evidence either way,
because the running collector performs WAL checkpoints that rewrite pages in
place. Safety was established from multiple independent signals instead:

| Signal | Result |
|---|---|
| `mode=ro` + `query_only=ON` on every connection | verified |
| Write attempt on that connection | `sqlite3.OperationalError` (asserted by test) |
| No write SQL, `ATTACH`, `VACUUM`, checkpoint or WAL truncation in Phase 2C | asserted by code-only scan |
| Control: two hashes 45 s apart with **no** Phase 2C activity | identical — the file is stable when idle |
| Causal: hash → 150,000-row read-only read → hash | **identical** — the read changes nothing |
| Earlier merge task: same file grew ~4.6 MB and ~5.7 MB with no Phase 2C code in existence | the collector demonstrably rewrites it alone |
| Process ownership | same 8 PIDs, all started 08:54, before and after |
| Source schema / `user_version` | unchanged |

The collector was **not** stopped to stabilize the hash. Collector-driven change
is reported as what it is.

## 10. Not started

No Dashboard or Streamlit file, alert, notification, paper trade, order,
execution, position, position sizing, portfolio heat, daily-loss limit, broker
path, threshold optimizer or profitability metric was created or modified. The
runner is deliberately **not** registered with the production launcher.
