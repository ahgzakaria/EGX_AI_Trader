# Phase 2A → Phase 2B Gate Decision

> **Superseded — historical record.** This is the independent reviewer's interim
> gate, issued before the gate-closure replay work. The authoritative verdict is
> `APPROVED_FOR_PHASE2B_CORE` in
> [`../PHASE2A_PHASE2B_FINAL_GATE.md`](../PHASE2A_PHASE2B_FINAL_GATE.md). This
> document is retained because it records the defects found and the evidence
> behind them; where the two disagree, the final gate governs.

---

# `APPROVED_WITH_BLOCKERS_RESOLVED` *(superseded)*

---

## What this verdict means

The Phase 2A data foundation is **structurally sound and honest**. Four blocking defects were
found, confirmed against real data, and fixed in-review with regression tests that fail against
the pre-review code. With those closed, the foundation is fit to build Phase 2B core logic on.

It is **not** yet fit to validate or enable Phase 2B decisions. That is a data limitation, not a
code limitation, and Phase 2A discovered and reported it correctly — which is what Phase 2A was
for. The conditions in §3 are binding before any Phase 2B signal is displayed, alerted or acted
on, in paper or otherwise.

This document assesses data-foundation correctness and safety only. It makes no statement about
profitability.

---

## 1. Blockers found and resolved in this review

| # | Class | Defect | Status |
|---|---|---|---|
| 1 | `BLOCKER_FIX` | No duplicate detection when `sequence` is absent — the production condition on 100% of rows. 53.8% of the real feed is exact redelivery; each repeat was stored separately and inflated `orb_bars.update_count`, the only activity proxy the schema offers | **Resolved.** Market-payload identity is now the dedup key. `test_reconnect_redelivery_without_sequence_is_deduplicated`, `test_update_count_is_not_inflated_by_feed_redelivery` |
| 2 | `BLOCKER_FIX` | A late opening-range correction was recorded only as a hash inside an audit string. The corrected range itself was discarded, so a reader could learn a correction existed but never what it was — despite the schema being built for revisions | **Resolved.** Corrections persist at the next revision; `revision=0` stays frozen and untouched. `test_shadow_ingestion_persists_the_corrected_range_not_only_an_audit_note` |
| 3 | `SAFETY_FIX` | Closing-auction quotes became `current_bid` / `current_ask` / `SPREAD_AVAILABLE` in the capability snapshot and were persisted that way. A Phase 2B renderer would have shown an auction print as the live continuous quote | **Resolved.** Capability snapshots read continuous phases only. `test_auction_quotes_never_become_the_current_continuous_market` |
| 4 | `SAFETY_FIX` | Constructing the repository writes immediately, and the guard was a seven-name basename list missing four real production databases. Any renamed copy, sibling database or non-database file would have been created into and migrated | **Resolved.** Deny list completed plus a read-only content probe that refuses any existing non-ORB file. `test_an_existing_foreign_database_is_never_migrated` and four others |

Supporting change: `universe_membership_status` and `operationally_eligible` are now typed,
persisted and indexed, so "observed in the Rubix database" can no longer be mistaken for
"eligible for a new ORB trade".

---

## 2. What was verified sound and needs no further work

- Session phases are exact to the microsecond on all five boundaries, half-open throughout,
  deterministic across the Egypt DST changes, and reject naive datetimes at every entry point.
- Non-trading dates return `NON_TRADING_DAY` before any time-of-day logic runs.
- Auction ticks cannot enter one-minute bars, five-minute bars or the opening range.
- Price quality and volume quality are genuinely independent across all seven failure modes.
  No negative volume, no invented zero, and a genuine zero is distinguishable from an unknown.
- One-minute and five-minute bars complete only after their boundary; the 10:10–10:15 bar is
  unavailable at 10:15 − 1 µs and available at 10:15.
- Opening-range freezing is enforced at the database level by a partial unique index, not by
  application discipline.
- The capability model cannot misrepresent unavailable data. `TRUE_VWAP_UNAVAILABLE` is
  unconditional, no name combines "PROXY" with "VWAP", and the RVOL threshold is config-driven.
- No secrets, auth frames or quote payloads are persisted anywhere.
- WAL concurrency behaves correctly: readers are not blocked and never observe a partial write;
  a contending writer waits rather than failing.
- Retention is bounded, executable and preserves derived evidence.
- The readiness audit is reproducible byte-for-byte and never wrote to a production database.

---

## 3. Binding conditions before Phase 2B decisions go live

Phase 2B **core construction may begin**. Each condition below must be closed before any
Phase 2B signal is rendered, alerted, or acted on.

### Blocking — must be closed

| # | Condition | Why |
|---|---|---|
| **G1** | **Validate the Phase 2A pipeline end-to-end against real quotes.** Run `scalping_orb` over the 2026-08-02 `quotes` rows into a throwaway research database and compare its opening-range readiness against the audit's 162 | The readiness audit measures Rubix's own `candles_1m` table. **Nothing has ever validated `scalping_orb.bars` + `opening_range` against real source data.** The two differ by design: identity rules, a 60 s freshness budget, zero out-of-order tolerance and delta allocation. This is the largest open gap in Phase 2A |
| **G2** | **Establish a real ORB evidence base.** All 162 opening ranges come from a **single session** (2026-08-02). Twelve of thirteen sessions produced zero | "13 intraday dates" is arithmetically true and operationally misleading. One session cannot support any Phase 2B parameter choice or threshold |
| **G3** | **Resolve the freshness contradiction.** Median receive-minus-market lag is **165 s** against a configured 60 s budget; **58.9%** of rows exceed it | Either `maximum_quote_age_seconds` is wrong for this feed, or `market_timestamp` carries publication semantics rather than trade time. Until this is settled, a Phase 2B freshness gate cannot be calibrated — and a first-pullback strategy on a 1–5 minute timeframe cannot tolerate a 165 s median lag |
| **G4** | **Decide the out-of-order policy deliberately.** `out_of_order_tolerance_seconds = 0.0` discards **20,590 rows (0.83%)** of real prices outright, not merely flagging them | The rejection is audited, so nothing is silent. But a zero tolerance on a feed with no sequence and a large delivery lag is a data-policy decision that was inherited as a default rather than chosen |
| **G5** | **Wire retention to an actual trigger.** `prune_normalized_events_before` works and is tested; **nothing ever calls it.** `derived_data_retention_days` has no prune function at all | The mechanism exists but never runs |

### Required before a live shadow run

| # | Condition | Why |
|---|---|---|
| **G6** | **Measure one real shadow session before optimising anything.** `ingest` reloads the entire session and re-aggregates every bar on each call, then issues roughly `2 + 2·symbols` write transactions — about 455 at 225 symbols, quadratic in session length | Do not restructure speculatively. Batching boundaries, in increasing order of risk, are in `PHASE2A_DATABASE_REVIEW.md` §7. Do **not** merge events, bars and derived rows into one transaction — that would trade away the recoverability property |
| **G7** | **Bound `_seen_payloads` memory and clear it on session rollover.** Roughly 85k hashes per session, on the order of 10 MB of live state | New state introduced by fix #1; unmeasured under a full live session |

### Should be closed, low risk

| # | Condition |
|---|---|
| **G8** | **Unify the two opening-range freeze implementations.** `OpeningRangeRegistry` is dead on the production path and has already diverged from `shadow.py`'s logic — the registry compares `source_identity` **and** high **and** low; the shadow path compares `source_identity` only. Keep the registry as the contract; delete or rewire the duplicate |
| **G9** | **Add a trading-day filter to `_candidate_dates`.** It never calls `is_regular_trading_day`. All 13 current dates are valid by luck, not construction |
| **G10** | **Order `load_events` by `source_identity` rather than the always-`NULL` `sequence`,** so reload order matches aggregation order exactly |
| **G11** | **Phase-separate the freshness distribution** in the readiness audit, and report the count of negative-lag rows currently dropped silently (3,885), which biases the percentiles upward |
| **G12** | **Move the hand-authored ORB contracts out of the gitignored `reports/` tree.** `ORB_FIRST_PULLBACK_STATE_MACHINE.md` and `ORB_FIRST_PULLBACK_DATABASE_SCHEMA.md` — the documents Phase 2B is meant to implement — are in a directory Git will never track. See `PHASE2A_GENERATED_FILE_POLICY.md` §3 |
| **G13** | **Add a `read_only=True` repository mode** using `mode=ro` + `query_only=ON` and skipping `migrate()`, for Phase 2B analysis tooling that must never mutate research evidence |
| **G14** | **Guard `_write_reports` against empty `opening_rows` / `minute_rows` / `volume_rows`.** Only `session_rows` is currently checked |

---

## 4. Universe eligibility — settled

The contract is verified and now enforced in code rather than by convention.

```
265 observed in Rubix  =  225 active-and-verified  +  40 archived-with-verified-mapping
                          ↑ eligible for a new     ↑ exit-monitoring exception only
                            ORB entry
```

Zero unknown, zero unmapped, zero malformed. The 16 remaining active symbols were never observed
on the feed. Full per-symbol detail for all 317 universe rows is in
`PHASE2A_SYMBOL_UNIVERSE_RECONCILIATION.csv`.

All historical observations are preserved, including all 40 archived symbols. Phase 2B must read
`operationally_eligible` — it must never infer eligibility from presence in
`orb_normalized_events`.

---

## 5. Test results

| Run | Result |
|---|---|
| Phase 2A dedicated tests | 56 passed |
| Phase 2A review regressions (new) | 53 passed |
| Full suite, before review fixes | 1957 passed, 7 skipped, 0 failed |
| **Full suite, after review fixes** | **2010 passed, 7 skipped, 0 failed** |
| `git diff --check` | clean |
| Readiness command re-run read-only | success; all six artifacts byte-identical |
| Protected production files, sha256 before vs after | **byte-identical** |

No commit, no push, no production database write. `main` remains at `3d6240f`.
