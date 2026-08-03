# Phase 2A — Research SQLite Review

**Target:** `data/research/orb_first_pullback.db` via `scalping_orb/repository.py`.
The generated database is mutable runtime state and is correctly gitignored
(`.gitignore:55` → `*.db`), as are its `-wal` / `-shm` sidecars.

---

## 1. Checklist

| Requirement | Result |
|---|---|
| Schema version explicit | **Yes.** `orb_schema_meta(version, migration_name, checksum, applied_at_utc)` plus `PRAGMA user_version` |
| Migrations idempotent | **Yes.** Each version is applied once and skipped thereafter; a changed migration body raises `ORB migration checksum mismatch` rather than silently drifting |
| WAL enabled through the real connection path | **Yes.** Set in `migrate()`; `journal_mode` is persistent in the database file, so every later connection inherits it. Verified: `database_status()["journal_mode"] == "WAL"` |
| `foreign_keys` on every connection | **Yes** — set in `connect()`, not just once. Now proven across repeated connections with an actual constraint violation, not merely by reading the pragma |
| Busy timeout appropriate | **Yes.** `timeout=30` on connect plus `PRAGMA busy_timeout=30000`. Belt and braces, and correct — the Python-level timeout alone would not cover statements issued after connect |
| Transactions roll back atomically | **Yes.** `BEGIN IMMEDIATE` … `commit()`, with `rollback()` on any exception and `close()` in `finally` |
| Unique keys prevent duplicates without merging distinct events | **Yes**, after the event-identity fix — see `PHASE2A_EVENT_IDENTITY_REVIEW.md` |
| Indexes match replay/read patterns | **Mostly** — see §4 |
| No auth frames or secrets persisted | **Yes** — see §5 |
| No production DB path selectable accidentally | **Was weak — now fixed** — see §2 |
| Configurable paths cannot resolve to Rubix or paper-trading DBs | **Was weak — now fixed** |
| Event retention bounded and executable | **Yes** — see §6 |
| Read-only audit mode truly uses `query_only` | **Yes** in the audit script; **not applicable** to the repository — see §7 |

---

## 2. Path safety — the most serious database finding

`OrbResearchRepository.__init__` calls `migrate()` immediately. Construction is therefore a
**write**: it creates tables, switches journal mode and stamps `user_version`. A mistyped path
does not fail harmlessly — it modifies whatever file it names.

The only guard was a case-insensitive basename check against seven names. Two gaps:

**Gap 1 — the deny list was incomplete.** Four real production/state databases in
`F:\EGX_AI_Trader\data` were not on it:

- `rubix_bridge_provenance.db`
- `scalping_historical_watchlists.db`
- `uptrend_pullback_watchlists.db`
- `tickerchart_quotes.sqlite3`

**Gap 2 — a deny list can only ever cover names someone remembered.** Any renamed copy, backup,
sibling project database or non-database file would have been created into and migrated.

**Fixed.** `_assert_safe_target` now runs before `migrate()`:

1. The deny list, completed with all four missing names.
2. A **read-only content probe** (`mode=ro` + `PRAGMA query_only=ON`) of any existing non-empty
   target. If it is not a SQLite database, or if it has tables and none begin with `orb_`, the
   constructor raises and nothing is written.

This turns an enumerate-the-bad-names guard into a positive one: the repository will only open
an ORB research database, a brand-new file, or an empty file.

Traversal was already handled — `Path("…/research/../rubix_live_market.db").name` resolves to the
protected basename — and this is now asserted rather than assumed.

**Tests:** `test_every_known_production_database_name_is_refused` (11 names),
`test_an_existing_foreign_database_is_never_migrated` (asserts the foreign file is byte-identical
afterwards), `test_a_non_database_file_is_never_overwritten`,
`test_traversal_cannot_reach_a_protected_name`.

**Residual recommendation, not applied:** the strongest form would additionally require the
resolved path to sit under a configured research root. That is a configuration-contract change
rather than a defect fix, so it is left as a Phase 2B recommendation.

---

## 3. Migrations

`_execute_script_transactionally` deliberately avoids `executescript`, which would force an
implicit commit and break the surrounding `BEGIN IMMEDIATE`. It splits on
`sqlite3.complete_statement` and raises on a trailing incomplete fragment. Correct, and the
rollback behaviour is proven by `test_failed_migration_rolls_back_schema_and_version`.

Migration 2 was added by this review (`ALTER TABLE … ADD COLUMN` ×2 plus an index) to carry
`universe_membership_status` and `operationally_eligible`. It is additive and backward-safe:
existing rows default to `UNKNOWN_UNMAPPED_IDENTIFIER` / `0`, which fails closed. This is also
the first exercise of the multi-migration path, which previously had only one migration.

Two pre-existing tests hard-coded schema version 1 and migration slot 2. Both now read from
`repository_module.SCHEMA_VERSION` and `repository_module.MIGRATIONS`, so the next migration
cannot break them.

---

## 4. Keys and indexes

| Table | Key | Assessment |
|---|---|---|
| `orb_normalized_events` | `PRIMARY KEY(source_identity)` | Correct after the identity fix |
| `orb_bars` | `PK(session_id, ticker, interval_minutes, bar_start_utc)` | Correct. Bars are immutable; a divergent recomputation is recorded as `FROZEN_COMPLETED_BAR_REVISION_DETECTED` rather than overwriting |
| `orb_opening_ranges` | `UNIQUE(session_id, ticker, revision)` + partial unique `WHERE is_frozen=1` | Correct, and the partial index is the right tool — one frozen range per symbol/session is enforced by the database, not by application discipline |
| `orb_data_quality_events` | `UNIQUE(dedupe_key)` | Correct. Content-hashed, so replay is idempotent |
| `orb_capabilities` | `UNIQUE(session_id, ticker, assessed_at_utc)` | Correct for re-ingest at the same `as_of` |
| `orb_sessions` | `UNIQUE(session_date, config_hash)` | Correct — a config change forks the session rather than mixing regimes |

Indexes cover symbol/time reads, session/time scans, bar reads, quality-code reads and
capability reads. Migration 2 adds an eligibility index.

Two observations:

- `idx_orb_events_symbol_time` is `(session_id, canonical_ticker, market_timestamp_utc)`, but
  `load_events` — the hot path, called on every ingest — filters on `session_id` alone and
  orders by `market_timestamp_utc, canonical_ticker, sequence`. The index leads with the right
  column, so the filter is served, but the ordering is a sort rather than an index walk.
- `load_events` orders by `sequence`, which is always `NULL`. As noted in the event-identity
  review, ordering by `source_identity` instead would make the reload order match the
  aggregation order exactly. Recommended, not applied.

---

## 5. No secrets persisted

`_source_identity` and `_sequence_payload_identity` hash an explicit whitelist of verified market
fields. No raw payload, header, cookie, token or auth frame is serialized anywhere. The
docstring says so and the code matches.

Quality events store a `code`, an optional short `detail` and a `source_identity` hash — never
a quote payload. Verified by test: a mapping failure carrying price 1234.56 and bid/ask
1234.0/1235.0 produces quality rows containing none of those values
(`test_quality_events_record_failures_without_quote_payloads`).

`shadow.py` contains no websocket, credential, login, authentication or order surface. Asserted
by source inspection in `test_shadow_module_carries_no_collector_auth_or_order_surface` rather
than trusted from the docstring.

---

## 6. Retention

`prune_normalized_events_before(cutoff, batch_size)` is bounded (`LIMIT batch_size`), ordered
(oldest first), transactional, and returns the number deleted so a caller can loop to
completion. `event_retention_days=30`, `derived_data_retention_days=365` and
`retention_batch_size=10000` are all configured and validated positive.

Verified executable, not merely documented: repeated bounded calls delete 5, then 10, then 0,
and **derived bars survive** — which is the right asymmetry, since bars are the durable evidence
and raw events are the reproducible input.

**Gap:** there is no scheduler, entry point or caller that ever invokes it. The mechanism exists
and works; nothing runs it. Given that the identity defect would have roughly doubled stored
event volume, wiring retention to a real trigger matters. Recorded as a Phase 2B gate.

`derived_data_retention_days` has no corresponding prune function at all.

---

## 7. Concurrency — single writer, concurrent readers

Tested with real threads against a real database.

| Scenario | Result |
|---|---|
| Reader during an open `BEGIN IMMEDIATE` write | Reader is **not blocked** and sees the pre-write committed value, never a partial write. After commit it sees the new value |
| Second writer contending with an open `BEGIN IMMEDIATE` | **Waits and succeeds.** `busy_timeout=30000` absorbs the contention; no `database is locked` |
| `integrity_check` after concurrent access | `ok` |

This is correct WAL behaviour and the settings support it properly. Multi-connection coverage
was entirely absent before; it is now covered by
`test_concurrent_readers_do_not_block_or_observe_a_partial_write` and
`test_a_second_writer_waits_rather_than_failing_immediately`.

**Ingestion and aggregation transaction shape.** `ingest` does *not* wrap everything in one
transaction. Per session it issues roughly `2 + 2·symbols` separate `BEGIN IMMEDIATE`
transactions: `ensure_session`, `insert_events`, `insert_quality_events`, `insert_bars` ×2,
`insert_quality_events`, then per symbol `insert_opening_range` and `insert_capabilities`.

That is the right trade-off for **durability** — events are the source of truth and bars, ranges
and capabilities are all recomputed from them, so a crash between transactions is recoverable
rather than corrupting. It is the wrong shape for **throughput**: at 225 symbols that is roughly
455 write transactions per ingest cycle, each taking the write lock.

Compounding it, `ingest` reloads the entire session via `load_events` and re-aggregates every
bar on every call. Over a 255-minute session that is quadratic in session length.

**Recommendation — measure before optimising.** Do not restructure speculatively. Run one real
shadow session and record wall time and lock-wait per ingest cycle. If it does not hold, the
natural boundaries, in order of value and in increasing order of risk:

1. Batch the per-symbol `insert_opening_range` + `insert_capabilities` calls into one transaction
   per session. Lowest risk, removes roughly 450 of the 455 transactions.
2. Aggregate incrementally from the current batch plus a bounded tail window instead of
   reloading the whole session. Preserves correctness because bars are already immutable and
   revisions are already audited.
3. Leave events, bars and derived rows in separate transactions. Do **not** merge them — that
   would trade the recoverability property for throughput.

---

## 8. Minor findings, not fixed

- **Connections are not explicitly closed on read paths.** `with self.connect() as connection:`
  uses SQLite's *transaction* context manager, which commits or rolls back but does **not**
  close. Every read method (`database_status`, `count_intraday_sessions`, `load_events`,
  `get_frozen_opening_range`, `table_count`, and the two new revision readers) relies on CPython
  refcounting to close the connection when the local goes out of scope. That works on CPython
  and is why no handle leak is observable, but it is implicit. A `closing()` wrapper or an
  explicit `finally: connection.close()` would make it deterministic. Not changed: no failure
  was demonstrated, and touching every read path is outside the fix criteria.
- **`PRAGMA synchronous=FULL` is set only in `migrate()`** and is per-connection, not persistent.
  Writes through `transaction()` therefore run at the WAL default (`NORMAL`). For a research
  database that is a reasonable durability/throughput point — but it is accidental rather than
  chosen, and the intent expressed in `migrate()` is not what actually applies.
- **`insert_events` sets `first_event_utc = COALESCE(first_event_utc, ?)`** using the minimum of
  the *current batch*. If batches ever arrive out of chronological order, `first_event_utc`
  records the first batch's minimum rather than the session's. Reporting only.
- **`query_only` is not offered by the repository.** The audit script correctly uses `mode=ro`
  plus `PRAGMA query_only=ON` against the Rubix database, which is the case the requirement
  targets. The research repository has no read-only mode of its own; a `read_only=True`
  constructor flag opening with `mode=ro`, `query_only=ON` and skipping `migrate()` would be a
  worthwhile addition for Phase 2B analysis tooling that must never mutate research evidence.
