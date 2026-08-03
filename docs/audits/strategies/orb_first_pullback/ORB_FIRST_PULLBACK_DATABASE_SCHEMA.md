# ORB + First Pullback — Research Database Schema Proposal

Status: Phase 1 design only. No database was created, opened for writing, or migrated.

## Isolation decision

Use a new SQLite WAL research store, proposed default:

```text
data/research/orb_first_pullback.db
```

Do not add tables or indexes to:

- `data/rubix_live_market.db` (external collector owns writes),
- `data/scalping.db` (existing strategy/history),
- `data/expected_range_paper_state.db`,
- `data/forward_testing.db`, or
- any production/daily/paper database.

The ORB store is derived research evidence. It must be rebuildable from immutable source identities where source history still exists, but saved paper signals/trades remain immutable audit records and are never overwritten by a replay.

## SQLite policy

- `PRAGMA journal_mode=WAL`
- `PRAGMA foreign_keys=ON`
- explicit `busy_timeout`
- `synchronous=FULL` for signal/trade/transition writes
- one short writer transaction; dashboard readers use read-only connections
- integer surrogate IDs or UUID text, plus deterministic unique keys
- UTC aware ISO-8601 timestamps; Cairo timestamp/date stored for audit/display
- JSON only for versioned evidence blobs; filterable core facts use typed columns
- no secrets, authentication frames, broker credentials or raw provider headers
- append-only triggers for transitions, signals, fills and exits

## Migration/version tables

### `schema_migrations`

| Column | Type/constraint |
|---|---|
| `version` | INTEGER PRIMARY KEY |
| `name` | TEXT NOT NULL |
| `checksum` | TEXT NOT NULL |
| `applied_at_utc` | TEXT NOT NULL |

Migrations are ordered, checksum-verified, transactional, and tested against a copied fixture. The application fails closed if the on-disk schema is newer than the code.

### `configuration_snapshots`

| Column | Type/constraint |
|---|---|
| `config_hash` | TEXT PRIMARY KEY |
| `config_version` | TEXT NOT NULL |
| `config_json` | TEXT NOT NULL |
| `created_at_utc` | TEXT NOT NULL |

Every session, transition and signal references the exact configuration hash. Research replays never reinterpret old records under new thresholds.

## Session and source identity

### `orb_sessions`

One row per Cairo trading date and run identity.

| Column | Type/constraint |
|---|---|
| `session_id` | TEXT PRIMARY KEY |
| `session_date` | TEXT NOT NULL |
| `mode` | TEXT CHECK in `SHADOW`, `PAPER` |
| `status` | TEXT NOT NULL |
| `continuous_open_utc`, `continuous_close_utc` | TEXT NOT NULL |
| `auction_end_utc` | TEXT NOT NULL |
| `eodhd_cutoff_date` | TEXT NOT NULL |
| `universe_fingerprint` | TEXT NOT NULL |
| `rubix_schema_fingerprint` | TEXT NOT NULL |
| `config_hash` | FK to configuration snapshot |
| `started_at_utc`, `closed_at_utc` | TEXT |
| `validation_status` | TEXT NOT NULL |

Unique key: `(session_date, mode, config_hash, universe_fingerprint)`. No automatic overwrite; a replay gets a distinct session/run id.

### `source_cursors`

Restart-safe progress through the read-only Rubix source.

| Column | Type/constraint |
|---|---|
| `session_id` | FK, part of PK |
| `source_name` | TEXT, part of PK (`RUBIX_QUOTES`, `RUBIX_CANDLES_1M`) |
| `last_source_id` | INTEGER |
| `last_market_timestamp_utc` | TEXT |
| `last_received_at_utc` | TEXT |
| `updated_at_utc` | TEXT NOT NULL |

A cursor advances only in the same transaction as derived records. Sequence/id regression is a data-quality event, never silently ignored.

### `daily_context`

One immutable D-1 context row per session/symbol.

Core columns: `session_id`, `canonical_symbol`, company name, EODHD symbol, Rubix subscription/storage symbols and mapping status, history latest date, history fingerprint, daily trend label, ATR, median volume, median turnover, price, next resistance, available upside, liquidity/volatility gates, optional old-selector context, selection state/reasons, `created_at_utc`.

Primary key: `(session_id, canonical_symbol)`. Old selector fields are explicitly named `secondary_*`; they cannot determine an ORB transition.

## Derived intraday evidence

### `data_quality_observations`

Append-only measurements for the entire session or a symbol.

Columns: `quality_id`, `session_id`, optional `canonical_symbol`, `observed_at_utc`, `market_timestamp_utc`, `phase`, `metric_code`, `status`, numeric `value`, `sample_count`, `required`, `detail_json`, and `source_fingerprint`.

Examples: quote age, collector status, timestamp lag, duplicate count, missing minute slots, cumulative-volume reset, bar-volume reliability, spread state, TOD-RVOL sample count.

### `intraday_bars`

Research cache derived without modifying Rubix.

| Column group | Fields |
|---|---|
| Identity | `session_id`, `canonical_symbol`, `interval_minutes`, `bar_start_utc` |
| Time | `bar_end_utc`, `bar_start_cairo`, `bar_end_cairo`, `session_phase` |
| OHLC | `open`, `high`, `low`, `close` |
| Supported flow | nullable `volume`, nullable `turnover`, nullable `trade_count`, nullable `vwap_numerator` |
| Provenance | `source_kind`, `source_first_id`, `source_last_id`, `source_row_count`, `source_fingerprint` |
| Quality | `is_complete`, `expected_slots`, `observed_slots`, `quality_state`, `quality_reasons_json` |

Primary key: `(session_id, canonical_symbol, interval_minutes, bar_start_utc)`. Only finalized immutable bars may be used for confirmation. A partial snapshot can be kept in a separate in-memory object, not inserted as completed evidence.

No forward-filled price/volume is persisted as market fact. If a verified zero-trade minute policy is later introduced, its provenance and policy version are mandatory.

### `opening_ranges`

One frozen range per session/symbol/config activation.

Columns: `opening_range_id`, `session_id`, `canonical_symbol`, start/end UTC/Cairo, expected/observed completed minutes, high/low/mid, width percent, nullable width ATR, nullable volume/turnover/true VWAP and their capability states, completion status/reason, `frozen_at_utc`, source/config fingerprints.

Unique: `(session_id, canonical_symbol)`. An update trigger blocks changes after `completion_status='READY'`.

### `candidate_states`

Current materialized state for efficient UI reads; unlike transitions, this row is mutable by compare-and-swap.

Columns: `session_id`, `canonical_symbol`, `activation_id`, `state`, `reason_code`, `state_version`, `updated_at_utc`, `last_completed_bar_end_utc`, `opening_range_id`, `levels_json`, `data_quality_json`, `evidence_fingerprint`.

Primary key: `(session_id, canonical_symbol)`. Update requires the expected prior `state_version`, preventing duplicate workers from overwriting newer evidence.

### `state_transitions`

Immutable audit log.

Columns: `transition_id`, `session_id`, `canonical_symbol`, `activation_id`, `evaluated_at_utc`, `evaluated_at_cairo`, `prior_state`, `new_state`, `rule_code`, `reason_codes_json`, `evidence_json`, `frozen_levels_json`, `data_quality_json`, `config_hash`, `daily_context_hash`, `source_cutoff_utc`, `source_fingerprint`.

Unique dedupe key over session/symbol/activation/prior/new/evidence fingerprint. UPDATE and DELETE triggers abort.

### `opening_breakouts` and `first_pullbacks`

Use typed domain tables instead of burying all measurements in transition JSON.

`opening_breakouts`: activation id, opening-range id, completed 5m bar start/end, close, OR distance, extension ATR, distance from supported VWAP, breakout volume/turnover states, resistance distance, RR preview, qualification/anti-chase state, source fingerprint.

`first_pullbacks`: activation id, ordinal, start/end, post-breakout high, low, depth percent/ATR, completed bars, sell-volume comparison and capability, OR/VWAP/EMA distances, hold/reclaim flags, structural-failure flags, state/reason, source fingerprint.

Unique: one breakout activation and one `ordinal=1` eligible pullback per activation. Later observed pullbacks may be recorded with `eligible=0` for research, never promoted to entry.

## Signals, risk and paper trades

### `entry_ready_signals`

Immutable signal record with UUID and deterministic dedupe key.

Core fields: session/symbol/activation, confirmation bar/time/rule, trigger, frozen structural stop, invalidation structure, stop distance percent/ATR, target 1/2 and resistance, diagnostic RR, account equity snapshot, risk percent/amount, proposed quantity, spread/volume/freshness states, evidence/config/source hashes, `research_only=1`, `created_at_utc`.

Unique: `(session_id, canonical_symbol, activation_id)`. UPDATE/DELETE prohibited.

### `risk_state`

One compare-and-swap row per session: starting/current equity, realized P&L, loss count, trade count, open ORB positions, portfolio heat, symbol exposure JSON, state version and updated time. Capital is supplied at session start; no default is embedded in the database.

### `entry_attempts`, `paper_fills`, `paper_positions`, `paper_exits`

These mirror the sound audit split in `scalping.database` but use ORB structural levels and partial quantities:

- `entry_attempts`: requested executable price, quantity, status, exact rejection.
- `paper_fills`: side, purpose (`ENTRY`, `PARTIAL_1R`, `TARGET_2`, `STOP`, `TIME`, `TRAIL`, `SESSION`), reference/fill prices, quantity, fees, slippage.
- `paper_positions`: current materialized quantity and non-widenable stop; every stop revision is constrained to be equal or tighter for a long position.
- `paper_exits`: immutable realized outcome, exit reason, conservative same-bar flag, gross/net P&L.

Foreign keys connect every fill to signal/position and every position to one entry-ready signal. A uniqueness constraint prevents multiple ORB positions for one activation. There is no broker-order table.

### `rejected_candidates`

Append-only rejection evidence with session, symbol, activation/state, reason code, observed time, measurements, data quality, config/source fingerprints and a deterministic dedupe key.

### `research_alerts`

Allowed alert types: `MOMENTUM_QUALIFIED`, `WAIT_FIRST_PULLBACK`, `ENTRY_READY`, `STOP_HIT`, `TARGET_1_HIT`, `TARGET_2_HIT`, `TIME_STOP`, `LATE_SESSION_EXIT`. Store state, timestamp, levels and evidence plus `research_only=1`. No recipient/broker instruction is inferred.

### `session_reviews`

Immutable end-of-session metrics and validation status, including counts by state/reason, coverage, spread/volume capability, signals/trades, conservative outcomes, source/config hashes and generated time. This is an audit snapshot, not a profitability claim.

## Recommended indexes

- bars: `(session_id, canonical_symbol, interval_minutes, bar_start_utc)` primary key already serves replay; add `(session_id, interval_minutes, bar_end_utc)` for batch reads;
- transitions: `(session_id, canonical_symbol, transition_id)` and `(session_id, new_state, evaluated_at_utc)`;
- current state: `(session_id, state, updated_at_utc)`;
- signals: `(session_id, confirmation_time_utc)`;
- positions: partial index on open status;
- quality/rejections/alerts: `(session_id, metric_or_reason_or_type, observed_at_utc)`.

Indexes are created only by a versioned migration and benchmarked against representative Shadow volume.

## Retention and reproducibility

- Keep all transition/signal/trade records permanently unless a separately approved retention policy says otherwise.
- A research-bar cache may be compacted only through an explicit maintenance command after source fingerprints and dependencies are verified; never during normal startup.
- Configuration, universe, daily-context and source-schema fingerprints must make a replay explainable.
- Changing a threshold creates a new config hash/run; it never updates old state.

## Migration risks and controls

| Risk | Control |
|---|---|
| Accidentally writing collector DB | Separate path and repository class; Rubix connector accepts only `mode=ro` URI; test write attempts fail |
| Extending old paper DB and corrupting history | New database; no startup auto-migration of existing DBs |
| Half-applied schema | Transactional numbered migration plus checksum and startup compatibility check |
| Duplicate monitor workers | cursor transaction, compare-and-swap state version, deterministic unique keys |
| Reprocessing after restart | cursor and idempotent transition/signal constraints |
| Config drift | immutable config snapshot/hash on every run and signal |
| Timezone/date collision | UTC timestamps plus explicit Cairo session date and half-open boundaries |
| Auction contamination | phase column and CHECK/application guard; transition tests reject auction source rows |
| JSON schema drift | evidence version field and typed query columns for critical values |
| Partial bars presented as complete | persisted `is_complete`/coverage state; engine queries only complete/valid rows |
| Stop widening or averaging down | constrained paper manager, immutable fills, audited stop changes, deterministic tests |
| UI holding a write lock | dashboard uses read-only repository/read model |
| Small sample misrepresented as performance | session review always carries `INSUFFICIENT_INTRADAY_HISTORY` until data gate passes |

## Migration test gate

Before Phase 3 can use the schema:

1. Create a new temporary database from zero and verify WAL/foreign keys/integrity.
2. Apply each migration once, reapply idempotently, and reject checksum mismatch.
3. Upgrade a fixture from every prior schema version.
4. Simulate rollback on an injected migration failure.
5. Prove old databases and the Rubix file hashes/schemas are unchanged.
6. Prove duplicate transitions/signals are ignored or rejected deterministically.
7. Prove transition/fill/exit UPDATE and DELETE attempts fail.
8. Prove read-only dashboard access does not create sidecar files in an unavailable location.

## Phase 2A gate-closure implementation

The implemented research schema now has three additive versions:

- v1: sessions, normalized events, completed bars, versioned Opening Ranges,
  quality events, capabilities and collection runs;
- v2: `universe_membership_status` and `operationally_eligible`, defaulting
  migrated evidence to fail closed;
- v3: typed market-time, historical-replay and live-freshness fields on events,
  plus separate historical-bar and live-decision capability fields.

`OrbResearchRepository(read_only=True)` opens with SQLite `mode=ro` and
`query_only=ON`, skips migrations and rejects transactions. Retention is invoked
at every ingestion boundary: normalized events are pruned in bounded batches at
the event horizon, while complete derived sessions are removed atomically only
at the longer derived-data horizon.

The v1 → v2 path is validated in place with representative rows in every Phase
2A evidence table. The fixture is not deleted; hashes and values survive, safe
universe defaults are applied, indexes and foreign keys validate, a forced
interruption rolls back, and a repeated migration is idempotent.
