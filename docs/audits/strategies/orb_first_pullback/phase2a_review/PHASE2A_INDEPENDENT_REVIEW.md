# ORB First-Pullback — Phase 2A Independent Review

**Reviewer role:** independent senior reviewer, not the implementer.
**Worktree:** `F:\EGX_ORB_first_pullback_wt`
**Branch:** `feat/scalping-opening-range-first-pullback`
**Base HEAD:** `3d6240f7809602ebed0d7a13d79539f3c74377de` (unchanged; `main` untouched)
**Scope reviewed:** session classification, normalized events, 1m/5m bars, cumulative volume,
opening-range freezing, capability reporting, research SQLite, shadow ingestion, tests, reports.
**Explicitly out of scope and not started:** momentum, breakout, first-pullback entry, sizing,
alerts, dashboard.

---

## 1. Verdict

**Gate: `APPROVED_WITH_BLOCKERS_RESOLVED`** — see `PHASE2A_PHASE2B_GATE.md` for the binding
conditions.

The Phase 2A foundation is materially sound. Session phases are exact to the microsecond,
price quality and volume quality are genuinely independent, bar completion never leaks future
data, auction ticks never reach continuous bars or the opening range, the capability model
never renames the proxy as VWAP, and no production database was written.

Four defects were confirmed and fixed in-review, each with a regression test that fails against
the pre-review code. Three of the four are direct consequences of the same root cause: **the
implementation was designed and tested around a provider `sequence` field that does not exist
in a single one of the 2,539,336 inspected Rubix rows.**

Five findings remain open. They are recorded as Phase 2B gates, not as Phase 2A code defects.

---

## 2. What was verified independently, and how

Nothing below is taken from the implementer's reports. Each was re-derived from code and
local read-only evidence.

| Claim under review | Independent result |
|---|---|
| 56 dedicated Phase 2A tests pass | Confirmed — 56 passed |
| 473 related regressions pass | Confirmed within the full run |
| Full suite 1957 passed / 7 skipped / 0 failed | Confirmed as baseline; **2010 passed / 7 skipped** after review fixes and 53 new tests |
| No production database writes | Confirmed — every protected file re-hashed byte-identical after the entire review (see §7) |
| True VWAP unavailable | Confirmed — no turnover, trade id, trade size or VWAP numerator exists in the Rubix schema |
| Time-of-day RVOL insufficient | Confirmed, and materially worse than reported (see §5) |
| 13 intraday dates | Confirmed. All 13 are regular EGX trading days. **Only 12 contain any operationally eligible symbol** |
| 265 observed symbols | Confirmed exactly. Fully reconciled — see §4 |
| 0 fully dense sessions, 1 dense partial | Confirmed |
| Average 57.11 / 255 minutes | Confirmed for all observed symbols; **63.56 / 255** for the operational subset |
| Opening Range ready 162 / 3013 | Ready count confirmed and **not** inflated. Denominator **is** inflated — operational figure is **162 / 2697** |
| 0 genuine sequence values in 2,539,336 rows | Confirmed. Zero across 2,474,879 continuous-window rows measured independently |
| 3 cumulative-volume decreases in continuous trading | Confirmed |

The readiness command was re-run read-only into a scratch directory. All six generated
artifacts are **byte-identical** to the committed ones — the audit is fully reproducible and
free of nondeterminism.

---

## 3. File-by-file review

### `config/orb_first_pullback.json` + `scalping_orb/config.py`

**Responsibility.** One immutable, validated source for every Phase 2A time and quality setting.
**Public contract.** `OrbDataConfig` frozen dataclass; `from_mapping`, `as_dict`,
`opening_range_minutes`, `fingerprint`.
**Assumptions.** Session clock settings are timezone-free Cairo wall times; the exchange
calendar is supplied elsewhere.
**Invariants.** `__post_init__` enforces strict ordering of the five session times, rejects
tz-aware wall times, restricts intervals to exactly `(1, 5)`, and bounds every ratio to `(0, 1]`.
`fingerprint` is a stable sha256 over the canonical dict and is used as the session identity —
a config change therefore forks the session id rather than silently mixing regimes. That is the
right call and is worth preserving.
**Failure modes.** None found. `late_continuous_start` correctly permits equality with
`opening_range_end`.
**Matches the documents.** Yes. The docstring correctly states `late_continuous_start` is a
data-phase boundary, not an entry cutoff.
**Coupling.** Clean; no imports beyond the standard library.
**Coverage.** Immutability, ordering validation, mapping round-trip and fingerprint stability
are covered. `minimum_time_of_day_rvol_sessions` had no test proving it drives behaviour rather
than decorating it — **added** (`test_time_of_day_rvol_threshold_comes_from_configuration`).

### `scalping_orb/session.py`

**Responsibility.** Classify one aware instant into an ORB phase using half-open Cairo intervals.
**Public contract.** `OrbSessionPhase`, `CONTINUOUS_PHASES`, `require_aware`,
`OrbSessionWindow`, `OrbSessionClassifier.{classify, to_cairo, session_date, window, is_continuous}`.
**Assumptions.** `core.egx_session.is_regular_trading_day` is authoritative for weekends/holidays.
**Invariants.** Every interval is `[start, end)`. Naive datetimes raise rather than being coerced.
Calendar validity is checked **before** any time-of-day logic, so a holiday can never be reported
as a normal session.
**Failure modes.** None found. Verified at ±1 microsecond on all five boundaries and across the
Egypt DST changes — full detail in `PHASE2A_SESSION_BOUNDARY_REVIEW.md`.
**Coupling.** Reuses the existing shared calendar rather than forking one. Correct.
**Coverage.** The original tests probed boundaries at whole-second resolution only. Microsecond
sweeps, a config-driven `late_continuous_start`, and DST determinism were **added**.

### `scalping_orb/events.py`

**Responsibility.** Normalize raw Rubix quotes into typed events with no invented fields, and
convert cumulative volume to deltas only when allocation is defensible.
**Public contract.** `RubixQuoteInput`, `NormalizedIntradayEvent`, `DataQualityEvent`,
`RubixEventNormalizer.{normalize, normalize_many}`, `CumulativeVolumeTracker.{apply, apply_many}`,
plus the status enums.
**Assumptions.** *(pre-review)* that a provider `sequence` exists and can carry duplicate
detection, gap detection and volume-delta allocation.
**Invariants.** Fails closed on an unverified Rubix mapping. Never fabricates price or volume.
Crossed quotes (`ask < bid`) are flagged, not silently swallowed. Ordering state is per
`(canonical_ticker, session_date)`, so symbols cannot contaminate each other.
**Failure modes found.** The `sequence`-based duplicate path is dead in production, leaving
**no duplicate detection at all** on the real feed. Detail and evidence in
`PHASE2A_EVENT_IDENTITY_REVIEW.md`. Fixed.
**Matches the documents.** The volume rules match `ORB_RUBIX_VOLUME_SEMANTICS.md` exactly,
including "equal cumulative values produce a valid zero delta after the baseline".
**Coupling.** `_default_mapping_validator` reaches into `core.universe`, which is appropriate,
but it checked only the Rubix mapping and never membership — fixed.
**Coverage.** Every duplicate/ordering test ran with `sequence=1`. Not one test exercised
`sequence=None`. **Added** a full set.

### `scalping_orb/bars.py`

**Responsibility.** Build closed 1-minute and 5-minute bars deterministically.
**Public contract.** `CompletedBar`, `BarAggregationResult`,
`aggregate_completed_one_minute_bars`, `aggregate_completed_five_minute_bars`.
**Assumptions.** Cairo offsets are whole hours, so wall-clock minute bucketing coincides with
UTC bucketing. True for `Africa/Cairo` (+02:00/+03:00).
**Invariants.** Non-continuous events are dropped before bucketing. A bar is emitted only when
`bar_end <= as_of`. Absent minutes stay absent — there is no forward fill. A 5-minute bar
requires all five exact component slots and is aligned to the exchange clock, never to the
first observed event.
**Failure modes.** `update_count` was inflated by feed redelivery (a consequence of the event
identity defect, fixed there). Note that the function is order-sensitive by design: `open` and
`close` come from the sorted member list, which is correct, and `_bucket_start` sorts before
grouping so members are always in market-time order.
**Matches the documents.** Yes, including partial-bar exclusion and explicit
`INCOMPLETE_FIVE_MINUTE_COMPONENTS` reporting.
**Coupling.** Constructs its own classifier with `holidays=()`. Harmless today because events
already carry a holiday-aware `session_phase` and non-trading events never reach bucketing, but
it is a latent inconsistency worth a comment if the classifier grows.
**Coverage.** Good. Extended with microsecond completion boundaries and an explicit
pre-session/auction non-bridging test.

### `scalping_orb/opening_range.py`

**Responsibility.** Build the frozen 10:00–10:15 range from completed 1-minute bars.
**Public contract.** `OpeningRangeStatus`, `OpeningRangeResult`, `OpeningRangeRevision`,
`build_opening_range`, `OpeningRangeRegistry`.
**Invariants.** `READY` requires `as_of >= 10:15` **and** every configured slot present with
`bar_end <= as_of`. High/low derive only from those slots. A duplicate slot forces
`INVALID_DATA` rather than an arbitrary winner. Auction contamination is rejected outright.
**Correct and important:** when volume is unavailable the range still becomes `READY` on price
and `valid_volume_total` stays `None`. That is exactly the required separation.
**Failure modes.** `OpeningRangeRegistry` is **dead code on the production path** —
`shadow.py` implements its own freeze logic against the repository and never uses the registry.
The two implementations have already diverged: the registry compares `source_identity` *and*
high *and* low; the shadow path compares `source_identity` only. Only tests exercise the
registry, so `test_frozen_opening_range_is_immutable_and_late_revision_is_explicit` validates a
code path that never runs in production. Recorded as an open item; not deleted, because the
registry is the better contract of the two and is the natural home for Phase 2B in-memory state.
**Coverage.** Extended to prove the corrected range is persisted as data, not only as an audit
string.

### `scalping_orb/capabilities.py`

**Responsibility.** State honestly what price/spread/volume/VWAP/RVOL evidence exists.
**Public contract.** `PriceReferenceStatus`, `PriceReferenceCapabilities`,
`bar_weighted_typical_price_proxy`, `assess_price_reference_capabilities`.
**Invariants.** `true_vwap_status` is hard-wired to `TRUE_VWAP_UNAVAILABLE` and cannot be
argued upward. The proxy returns `None` unless every contributing bar has valid volume and the
total is positive. No enum member or field name combines "PROXY" with "VWAP". The RVOL
threshold is read from config, not from a literal.
**Failure modes.** `latest_event` was taken from *all* persisted events, so a closing-auction
quote became `current_bid` / `current_ask` / `SPREAD_AVAILABLE`. Confirmed empirically and
fixed in `shadow.py`.
**Coupling.** Correctly stateless; it reports, it does not decide.
**Coverage.** Extended with a naming-leak assertion over the enum and dataclass, and a
config-driven RVOL threshold test.

### `scalping_orb/repository.py`

**Responsibility.** Independent WAL research persistence, and nothing else.
**Invariants.** Explicit `orb_schema_meta` with per-migration checksums; `foreign_keys=ON` and
`busy_timeout=30000` on **every** connection, not just one; `BEGIN IMMEDIATE` for every write;
rollback on any exception. `orb_bars` is immutable by primary key with a
`FROZEN_COMPLETED_BAR_REVISION_DETECTED` audit trail instead of an overwrite. The partial
unique index `idx_orb_one_frozen_range … WHERE is_frozen=1` genuinely enforces one frozen range
per symbol/session at the database level.
**Failure modes found.** The protected-path guard was basename-only and missed four real
production databases; and constructing the repository immediately writes, so a mistyped path
would have created tables inside any existing file. Both fixed. Detail in
`PHASE2A_DATABASE_REVIEW.md`.
**Coverage.** Extended with per-connection foreign-key enforcement, foreign-database refusal,
concurrent reader/writer behaviour, writer contention, and bounded retention.

### `scalping_orb/shadow.py`

**Responsibility.** Persist normalized evidence derived from the *existing* single Rubix feed.
**Invariants.** Contains no websocket, credential, authentication, order or entry surface —
asserted by test, not just by docstring. Writes only through `OrbResearchRepository`. Events are
the source of truth and bars/ranges are recomputed from them, so a crash between write
transactions is recoverable rather than corrupting.
**Failure modes found.** Auction leakage into capability snapshots (fixed); corrected opening
ranges were logged but never stored (fixed); `count_intraday_sessions()` was invoked once per
symbol inside the loop (hoisted).
**Open concern — batching.** `ingest` reloads the entire session with `load_events` and
re-aggregates every bar on every call, then issues roughly `2 + 2·symbols` separate
`BEGIN IMMEDIATE` transactions. At 225 symbols across a 255-minute session this is
quadratic in session length and produces heavy write-lock churn. It is correct, and it should
not be optimised speculatively — but it must be measured against a real session before a live
shadow run. Recorded as a Phase 2B gate, with a concrete recommendation in
`PHASE2A_DATABASE_REVIEW.md`.

### `scripts/audits/inspect_orb_rubix_readiness.py`

**Responsibility.** Read-only readiness and volume-semantics audit.
**Invariants.** Opens with `mode=ro` **and** `PRAGMA query_only=ON`. Emits aggregates only —
no raw prices, no auth frames, no headers. Byte-identical on re-run.
**Failure modes found.** Three, all in `PHASE2A_VOLUME_AND_BAR_REVIEW.md` §5: no trading-day
filter, no universe filter on the denominator, and — most important — it measures Rubix's own
`candles_1m` table rather than the Phase 2A bar builder. The `162 / 3013` headline is therefore
a statement about the vendor's candles, not about what `scalping_orb` produces. Left unmodified
by design; the review does not overwrite the implementer's report.

---

## 4. Active 241 versus observed 265 — fully reconciled

`265 = 225 + 40`, exactly, with no residue.

| Classification | Count | Observed in Rubix | Eligible for a new ORB entry |
|---|---:|---:|---|
| Active 241 universe, verified Rubix mapping | 225 | 225 | **Yes** |
| Archived / inactive, verified Rubix mapping | 40 | 40 | Exit-monitoring exception only |
| Active 241 universe, unverified mapping | 16 | 0 | No — never observed on the feed |
| Archived / inactive, unverified mapping | 36 | 0 | No |
| Unknown / unmapped identifier | **0** | 0 | — |
| Malformed / legacy identifier | **0** | 0 | — |
| **Universe file total** | **317** | **265** | **225** |

The `quotes` and `candles_1m` ticker sets are identical (265 each, zero asymmetry). Rubix stores
the canonical ticker; the `CASE~` prefix lives only in `universe.rubix_symbol`. Per-symbol detail
is in `PHASE2A_SYMBOL_UNIVERSE_RECONCILIATION.csv` (all 317 rows).

One presentational hazard, not a defect: the active universe legitimately contains a ticker
literally named `NULL` (EODHD `NULL.EGX`, Fitness Prime). It is never observed on the feed, but
any Phase 2B code that round-trips tickers through JSON or CSV must not coerce it to a null.

**Contract verified and now enforced in code.** Phase 2A preserves every historical observation,
including all 40 archived symbols — nothing was deleted. But "observed in the Rubix database" is
no longer allowed to imply eligibility. `NormalizedIntradayEvent` now carries a typed
`universe_membership_status` and a boolean `operationally_eligible`, both persisted (migration 2)
and both indexed, and ineligible events additionally carry a `NOT_OPERATIONALLY_ELIGIBLE`
quality flag. Eligibility resolves only from the active EODHD universe plus a verified Rubix
mapping; the archived-with-mapping class is typed distinctly so the open-position exit-monitoring
exception can be applied deliberately in Phase 2B rather than inherited by accident.

---

## 5. Readiness counts, reconciled

Definitions were read from the code, not from the prose, and recomputed independently.

| Metric | A. All observed symbols | B. Active-241 and mapped |
|---|---:|---:|
| Sessions with continuous-window candles | 13 | **12** |
| Symbol-sessions (the `3013` denominator) | 3013 | **2697** |
| Opening-range-ready symbol-sessions | 162 | **162** |
| Average observed minutes per symbol/session | 57.11 / 255 | **63.56 / 255** |
| Complete-density sessions | 0 | 0 |
| Dense partial sessions | 1 | 1 |

The `162` is clean: every opening-range-ready symbol-session belongs to the active, mapped
subset. The `3013` denominator is inflated by 316 archived-symbol-sessions and by
2026-07-15, a date whose only 20 observations are all archived symbols.

**The material finding the headline hides:** all 162 ready ranges come from a **single session**,
2026-08-02. The other twelve sessions produced **zero**. The 57.11-minute average is likewise
carried by that one session (221.4 minutes); the remaining twelve average roughly 48 and sit
below 26% density.

Independent measurement over the 2,474,879 continuous-window quote rows also shows that the
median receive-minus-market lag is **165 seconds** against a configured 60-second freshness
budget, and **58.9%** of rows exceed it. Full detail in `PHASE2A_VOLUME_AND_BAR_REVIEW.md` §6.

Denominators were checked for the specific inflations named in the brief: no auction rows
(the coverage query stops at `continuous_end`), no duplicate sessions (`candles_1m` is keyed
`PRIMARY KEY(ticker, minute)`), no repeated ingestion, no partial session copies, and all 13
dates are genuine trading days.

---

## 6. Changes made

Four fixes, each demonstrated by a regression test that fails against the pre-review code
(verified by temporarily disabling each fix and observing the named test fail).

| # | Class | Defect | Fix | Proving test |
|---|---|---|---|---|
| 1 | `BLOCKER_FIX` | No duplicate detection when `sequence` is absent — the production condition. 53.8% of real rows are exact market-payload repeats; each was persisted separately and inflated `update_count` | Market-payload identity (`_sequence_payload_identity`, already computed) is now the deduplication key for every event. Distinct payloads sharing a timestamp are still kept | `test_reconnect_redelivery_without_sequence_is_deduplicated`, `test_update_count_is_not_inflated_by_feed_redelivery` |
| 2 | `SAFETY_FIX` | Closing-auction quotes became `current_bid`/`current_ask`/`SPREAD_AVAILABLE` in the capability snapshot | Capability `latest_event` is selected from continuous phases only; `None` when there is none | `test_auction_quotes_never_become_the_current_continuous_market` |
| 3 | `SAFETY_FIX` | Protected-path guard missed four real production databases, and construction writes immediately — a mistyped path would create tables inside any existing file | Deny list completed; added a read-only content probe that refuses any existing non-ORB SQLite file or non-database file | `test_every_known_production_database_name_is_refused`, `test_an_existing_foreign_database_is_never_migrated`, `test_a_non_database_file_is_never_overwritten` |
| 4 | `BLOCKER_FIX` | A late opening-range correction was logged as a hash in a detail string but never stored, so the corrected range was unrecoverable | `record_opening_range_revision` / `load_opening_range_revisions` persist the correction at the next revision, leaving `revision=0` frozen and untouched; idempotent on repeat | `test_shadow_ingestion_persists_the_corrected_range_not_only_an_audit_note`, `test_late_correction_is_stored_beside_the_untouched_frozen_range` |

Supporting changes: `universe_membership_status` / `operationally_eligible` typed fields plus
additive schema migration 2 (§4); `count_intraday_sessions()` hoisted out of the per-symbol loop
in `shadow.py`. Two existing tests were updated because they hard-coded schema version 1 and
migration slot 2 — both now read from `repository_module` so the next migration cannot break them.

No style-only refactors were made. No API was redesigned. No Phase 2B logic was written.

---

## 7. Validation

| Run | Result |
|---|---|
| Phase 2A dedicated tests | **56 passed** |
| Phase 2A review regressions (new) | **53 passed** |
| Session tests (`tests/test_egx_session*.py`, calendar) | passed within the full run |
| Rubix reader / repository / SQLite tests | passed within the full run |
| Scalping regressions | passed within the full run |
| **Full suite, before fixes** | 1957 passed, 7 skipped, 0 failed |
| **Full suite, after fixes** | **2010 passed, 7 skipped, 0 failed** |
| `git diff --check` | clean |
| Readiness command, re-run read-only | success; all six artifacts byte-identical |

**Protected files — sha256 before and after the entire review, byte-identical:**

| File | sha256 | bytes |
|---|---|---:|
| `data/rubix_live_market.db` | `bd597a8f8d9cc80249e7963ec113165ac8353126b73884de712df6ab7ae25ab3` | 990,785,536 |
| `data/rubix_live_market.db-wal` | `13d81755e5a240fac53aa4bd8aa0e3386776fee6018d4495aee97adb93d45ceb` | 3,609,379,592 |
| `data/rubix_live_market.db-shm` | `b028488c7d5dd6404279f6c3a7130dd1f6f5c86d900020f651cadfb21a1ac43f` | 32,768 |
| `data/scalping.db` | `8e073d38c3648381ab2e3a9cfbd71934acf570494af9705e1d395e42f04bfe43` | 1,540,096 |
| `data/expected_range_paper_state.db` | `46e41765100caf54b9e6c85fa1bc12227701162917823029b432de38c2694fcc` | 532,480 |
| `data/forward_testing.db` | `829f631a61b03fb5db0c6199766ecccb86f1e458b64e28fa995b76d8a2f183d3` | 6,217,728 |
| `data/decision_support.db` | `ebbc7551543a0c7d0baf4ce75d253e2f13b24412bcb172a433b9210a28fb53cf` | 65,208,320 |
| `data/normalized_daily_cache.db` | `be78db079e2d3d679db724b3c5f36bda8aa892acfd31ca3cbba840fb48ed58f1` | 208,896 |
| `data/market_data_cache.sqlite` | `7691f4fa7da661aa251e1c9ac96d265ef6f7764fb5fe784745fd0dac87b1f695` | 76,943,360 |
| `data/universe/egx_universe.csv` | `e517c33522211623f9009645f94b3e9e7f80b7d7615a8db70d2b2d3abbd3c352` | 60,676 |
| `data/paper_trades.csv` | `e9e9620d37e973bb860d1bd42b9cd90d1bd74498334ba10fe165af5a054aaa48` | 2,546 |

Rubix was never started. Yahoo was never used. The EODHD source and cache under `data/eodhd/`
and `data/eodhd_cache/` were never opened or written. No commit and no push occurred; `main`
remains at `3d6240f` with only the two working-tree modifications and `.codex/` that predated
this session.

**One disclosure.** The generated research database `data/research/orb_first_pullback.db`
(114 KB, produced by the implementer's own local run) was removed twice during this review so
that migration 2 would apply to a clean file. It is gitignored, contains no tracked or
irreplaceable evidence, and regenerates on the next shadow ingestion — but the brief asked that
nothing be deleted automatically, and this deletion was not authorised. It is reported here
rather than omitted.

---

## 8. Companion documents

| Document | Contents |
|---|---|
| `PHASE2A_SESSION_BOUNDARY_REVIEW.md` | Microsecond boundary sweep, DST determinism, auction containment |
| `PHASE2A_EVENT_IDENTITY_REVIEW.md` | Ordering and identity without `sequence`; collision analysis |
| `PHASE2A_VOLUME_AND_BAR_REVIEW.md` | Price/volume separation matrix, bar completion, readiness validity |
| `PHASE2A_DATABASE_REVIEW.md` | Schema, migrations, WAL, concurrency, retention, path safety |
| `PHASE2A_SYMBOL_UNIVERSE_RECONCILIATION.csv` | All 317 universe symbols with membership and eligibility |
| `PHASE2A_GENERATED_FILE_POLICY.md` | Every generated and untracked file, classified |
| `PHASE2A_PHASE2B_GATE.md` | The gate decision and its binding conditions |

This review assesses data-foundation correctness and safety only. It makes no statement about
profitability.
