# Phase 2C — Shadow Integration Architecture Audit

Written **before** implementation, as required. Base HEAD `6d10334`.

Research only. The highest state this phase can reach remains
`ENTRY_READY_RESEARCH`, always labelled Research Only. No order, execution,
position, paper trade, alert, notification or Dashboard surface is in scope.

---

## 1. What was inspected

The **live production** Rubix database at `data/rubix_live_market.db`, opened
`mode=ro` with `PRAGMA query_only=ON`, while the production collector was
running. Nothing was started, stopped, checkpointed or written.

| Table | Rows | Role |
|---|---:|---|
| `quotes` | 3,890,257 | the collector's raw quote stream — **the only bar evidence Phase 2C will use** |
| `candles_1m` | 298,358 | the collector's own 1-minute aggregation — **deliberately not used** (§3) |
| `feed_metrics` | 9,523,376 | collector telemetry |

```sql
CREATE TABLE quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, last_price REAL, bid REAL, ask REAL, volume REAL,
    market_timestamp TEXT NOT NULL, received_at TEXT NOT NULL,
    exchange TEXT, sequence INTEGER, change_percent REAL,
    has_feed_timestamp INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_quotes_ticker_time ON quotes(ticker, market_timestamp);
```

`journal_mode=wal`, `page_size=4096`, `auto_vacuum=0`, `user_version=0`.

### 1.1 Findings that shape the design

**`sequence` is 100% NULL.** Across the most recent 200,000 rows,
`count(DISTINCT sequence) = 0`. The brief anticipated this; it is confirmed. **No
cursor, ordering or identity may depend on an exchange sequence.**

**`quotes.id` is a trustworthy progress cursor.** `INTEGER PRIMARY KEY
AUTOINCREMENT`, so it is monotonic in insertion order and never reused. Over the
last 50,000 rows in `id` order there were **0** inversions of `received_at`.
Ranged `id > ?` scans ride the rowid B-tree, which is the cheapest possible read.

**`market_timestamp` has one-second granularity, and collisions are the norm.**
In the last 200,000 rows, 88,268 `(ticker, market_timestamp)` groups held more
than one row. Of those, many are *exact redeliveries* (90,394 fully-identical
`(ticker, market_timestamp, last_price, volume, bid, ask)` groups), but not all —
genuinely distinct payloads share a timestamp. **A `(ticker, market_timestamp)`
key would silently discard real events.** This is exactly why `id` is a
*progress cursor only* and market identity stays with Phase 2A.

**Measured receive lag is far better than the historical figure on record.**
Last 100,000 rows:

| Metric | Historical (on record) | Measured now |
|---|---|---|
| median lag | ~165 s | **1.09 s** |
| p90 | — | 1.85 s |
| p95 | — | 2.07 s |
| max | — | 76.80 s |
| over 60 s budget | 58.9% | **0.0%** (1 row) |
| negative lag | — | 1,119 rows (clock skew) |

This is a **measurement, not a live-readiness claim.** It may reflect a
collector improvement, or a quiet window, or both. Phase 2C therefore keeps
freshness fail-closed regardless of what the numbers currently say, and the
one-full-session blocker stays open. Negative lag is real and must be handled
explicitly rather than clamped away.

**Session density has changed materially.** Phase 2B's replay found zero
breakouts largely because 5-minute bars were scarce in the July sessions it
sampled. Recent sessions are an order of magnitude denser:

| Session date | Rows | Tickers |
|---|---:|---:|
| 2026-08-03 | 1,048,339 | 224 |
| 2026-08-02 | 993,576 | 224 |
| 2026-07-30 | 183,451 | 264 |
| 2026-07-29 | 196,405 | 264 |

This does not change any threshold and does not license a profitability claim.
It does mean a future full Shadow session has a realistic chance of producing
completed 5-minute bars — which is precisely what the blocker needs.

## 2. Chosen read boundary

**A read-only SQLite reader over the collector's existing `quotes` table.**

```
Rubix collector (already running, NOT owned by this phase)
        │  writes
        ▼
data/rubix_live_market.db          ← mode=ro, query_only=ON, busy_timeout
        │  reads (id > cursor)
        ▼
ShadowSourceReader ──► Phase 2A normalizer ──► ShadowSessionSnapshot
                                                      │
                                                      ▼
                                       Phase 2B pure engine (unchanged)
                                                      │
                                                      ▼
                              ORB research DB (independent, ignored)
```

### Why this does not start a second collector

The phase never opens a websocket, never authenticates, never touches
subscriptions and never spawns a process. It is a **reader of a file another
process already writes.** `scripts/launch_rubix_production.py`,
`rubix_collector_supervisor.py` and the `.bat` starter remain the sole owners of
collector lifecycle, and Phase 2C imports none of them. The runner is
deliberately **not** registered with the production launcher in this phase.

A SQLite reader in WAL mode does not block the writer and takes no exclusive
lock. Readers see a consistent snapshot as of their transaction start; the
collector keeps appending unimpeded. No checkpoint, no `VACUUM`, no WAL
truncation, no `ATTACH`, no schema change.

The write target is the independent ORB research database. `repository.py`
already fails closed on this: `PROTECTED_DATABASE_NAMES` names
`rubix_live_market.db` explicitly, and a content probe rejects any other foreign
database. Phase 2C adds no bypass.

## 3. Why `candles_1m` is not used

It is tempting — it is already aggregated. It is rejected because:

1. Phase 2A's contract builds bars from **quote-derived** evidence with explicit
   completeness, update counts and volume-validity semantics. `candles_1m` is a
   second, differently-derived OHLC source with none of that provenance.
2. Using it would be a second OHLC implementation, which the brief forbids.
3. Its `updates` and completeness semantics are the collector's, not the ORB
   quality model's, so a "completed" bar there does not mean what a completed
   Phase 2A bar means.

Phase 2C reuses `aggregate_completed_one_minute_bars` /
`aggregate_completed_five_minute_bars` unchanged. `candles_1m` is read for
nothing.

## 4. Cursor semantics

```
ShadowCursor(
    source_table   = "quotes",
    last_source_id = int,          # exclusive low-water mark: next read is id > this
    last_market_timestamp_utc,     # observability only
    last_receive_timestamp_utc,    # observability only
    rows_observed  = int,
)
```

**Ordering.** `WHERE id > ? ORDER BY id LIMIT ?`. Deterministic, total, and
index-free (rowid). It does *not* claim to be exchange order — it is *collector
insertion* order, which is the only ordering the source actually guarantees.

**`id` is a progress cursor, never a market identity.** Market identity stays
with Phase 2A's `_source_identity` (which hashes verified market fields *plus*
`source_row_id`) and `_sequence_payload_identity` (market payload only, receive
metadata excluded). Phase 2C passes `source_row_id=quotes.id` into
`RubixQuoteInput` and changes neither function.

**Redelivery idempotency** is inherited, not reinvented: `insert_events` is
`INSERT OR IGNORE` keyed on `source_identity`. Re-reading a row id yields the
same identity and inserts nothing.

**Distinct same-timestamp events survive** because the cursor advances by `id`,
never collapsing on `(ticker, market_timestamp)`.

**No row loss across restart.** The cursor is the *exclusive* high-water id, and
is persisted in the **same transaction** as the events derived from that batch
(§7). A crash between read and commit rolls both back; the next process re-reads
the identical id range and the `INSERT OR IGNORE` makes the retry harmless.

**Fallback documented.** If `id` were ever non-monotonic (it is not, and
AUTOINCREMENT guarantees no reuse), the reader would degrade to a
`received_at`-ordered scan with an overlap window. This is documented but **not
implemented**, because implementing an untested fallback for a condition the
schema forbids would be speculative code.

The cursor lives only in the ORB research database. **Nothing is ever written to
the Rubix source.**

## 5. Batching model — closing `REPLAY_SESSION_BATCHING_REQUIRED`

The Phase 2B defect: `evaluate_symbol` loaded every event in the session and
filtered in Python, once per symbol. The SQL-filter fix removed the worst factor
but the shape was still per-symbol.

Phase 2C introduces a **shared immutable session snapshot**:

```
ShadowSessionSnapshot(frozen)
    session_date, cursor_range, config_identity, snapshot_identity
    events_by_ticker      : Mapping[str, tuple[NormalizedIntradayEvent, ...]]
    one_minute_by_ticker  : Mapping[str, tuple[CompletedBar, ...]]
    five_minute_by_ticker : Mapping[str, tuple[CompletedBar, ...]]
    opening_ranges        : Mapping[str, OpeningRangeSnapshot]
    eligibility, quality_summary, daily_context, watermark
```

Contract:

- a batch is read **once**, normalized **once**, grouped **once**;
- the evaluator reads symbols out of the snapshot — it never returns to the
  database per symbol;
- incrementally, only symbols with a **newly completed relevant bar** are
  re-evaluated; an untouched symbol is not rebuilt;
- `snapshot_identity` is content-derived, so an identical batch yields an
  identical snapshot and identical results;
- results are independent of symbol iteration order (asserted by test).

Target shape: **O(events) per batch + O(affected symbols) evaluations**, never
O(symbols × full-session reload).

## 6. Watermark and late events

Two distinct clocks, never conflated:

- **exchange watermark** — derived from `market_timestamp`; decides which bars
  are closed;
- **source cursor watermark** — `quotes.id`; decides what has been read.

A completed bar is **operationally final** for Lane A only when *all* hold:

1. its exchange end time has passed the exchange watermark;
2. every required component bar exists (no fill, no partial);
3. the configured lateness grace has elapsed since that end time;
4. its live evidence is fresh enough under the freshness budget;
5. it is not in, and does not bridge into, the auction.

An event arriving after the operational watermark is **stored**, raises a
quality event, and may participate in Lane B — but **cannot rewrite Lane A**.

## 7. Transaction boundaries

Per cycle, one write transaction on the research DB containing: normalized
events, completed bars, opening-range rows, Lane A states, the cycle metrics row,
**and the advanced cursor**. Commit is the single atomic point at which a batch
is "done".

This is what makes restart exact: the cursor can never be ahead of the data it
describes, nor behind it in a way that duplicates state, because they land
together. Heartbeats are written in their own small transactions so a long
evaluation still shows liveness.

## 8. Two lanes

| | **Lane A — live shadow** | **Lane B — reconstruction** |
|---|---|---|
| Question | what was *safely knowable* at observation time | what the evidence *supports* afterwards |
| Freshness | `received_at` governs; stale ⇒ no advance | delayed evidence admitted |
| Market time | unreliable ⇒ no advance | ordering still deterministic |
| Opening range | bound to **original** decision-time version | may select latest revision explicitly |
| Late corrections | cannot rewrite history | incorporated |
| Mutability | **append-only, immutable** | idempotent, recomputable |
| Mode | `SHADOW_LIVE` (capability fail-closed) | `HISTORICAL_REPLAY` |

Lane A statuses: `LIVE_SHADOW_HEALTHY`, `LIVE_SHADOW_STALE`,
`LIVE_SHADOW_SOURCE_UNAVAILABLE`, `LIVE_SHADOW_PARTIAL_SESSION`,
`LIVE_SHADOW_FULL_SESSION`, `LIVE_SHADOW_DISABLED_FRESHNESS`. **No
production-live enabled state is introduced.** Phase 2A's
`LiveDecisionCapability` still has no enabled member, so Lane A cannot reach
research readiness by construction rather than by configuration.

The post-session comparison is the real deliverable: *what did freshness cost
us?* Reconstructed readiness is never presented as having been actionable live.

## 9. Expected migration

Additive **migration 5**, research evidence only, in the independent ORB
database. Current `SCHEMA_VERSION = 4`.

`orb_shadow_runs`, `orb_shadow_cursors`, `orb_shadow_cycles`,
`orb_shadow_heartbeats`, `orb_shadow_session_quality`, `orb_shadow_live_states`,
`orb_shadow_reconstruction_states`, `orb_shadow_live_replay_comparison`.

No orders, executions, positions, trades, P&L, broker events or notifications.
No Phase 2A or 2B table is altered. WAL, foreign keys, checksum-verified,
idempotent, transactional; downgrade refused; existing rows preserved.

## 10. Expected performance

Synthetic benchmark: ≥200 symbols, one full continuous session, realistic
density. Measured: source rows read, **session loads**, symbol evaluations, batch
time, incremental cycle time.

Required architectural result — a *shape*, not a latency promise:

- session data loaded **once** per batch;
- no O(symbols × full-session reload);
- incremental cycles evaluate only affected symbols.

No production latency SLA is asserted. Actual measured values are recorded.

## 11. Test plan

Source safety (`mode=ro`, `query_only`, write refused, no auth/websocket, no
production path as research target) · cursor (initial, incremental, exact restart,
no loss, no duplicate, same-timestamp distinct events, id-as-progress-only) ·
batching (single load, single grouping, only affected symbols, deterministic
snapshot identity, order independence) · watermark (boundary, grace, late stored
but not promoted, Lane A immutable, auction excluded) · bars (incremental 1m/5m,
missing component, no fill, no auction bridge, volume/price validity separated) ·
opening range (no READY before 10:15, 15-slot freeze, original live, corrected
historical) · engine integration (evaluation trigger, unrelated symbol untouched,
stale prevents advancement, transition identity preserved, Research Only) ·
restart · session classification · migration · regressions.

All tests use **temporary synthetic source databases**. No test reads the
production Rubix file.

## 12. Safety risks and mitigations

| Risk | Mitigation |
|---|---|
| Accidentally writing to Rubix | `mode=ro` + `query_only=ON`; no write path exists; name deny-list and content probe on the research side |
| Becoming a second collector | no socket, no auth, no subscription, no process spawn; runner not added to the launcher |
| Blocking the live writer | WAL reader, busy timeout, no exclusive lock, no checkpoint/VACUUM |
| Cursor loss or duplication on restart | cursor committed in the same transaction as its batch; `INSERT OR IGNORE` on content identity |
| Treating reconstruction as live truth | separate lanes, separate tables, explicit comparison report |
| Good current latency read as live-readiness | freshness stays fail-closed; blocker stays open until a real full session |
| Hard-coded machine paths | all paths via CLI/config; runtime modules contain no `F:\EGX_AI_Trader` |
| Denser data tempting recalibration | thresholds are untouched in this phase, by contract |
| Late corrections rewriting history | Lane A append-only; corrections are new rows plus quality events |

## 13. Explicitly out of scope

Dashboard/Streamlit, alerts, notifications, paper trades, orders, executions,
positions, position sizing, portfolio heat, daily-loss limits, broker
integration, threshold optimization, profitability metrics, production
recommendations, BUY/SELL signals.

Maximum verdict for this run: `APPROVED_FOR_FULL_SHADOW_SESSION`.
`FULL_SHADOW_SESSION_OBSERVED` is **not** available here — it requires a runner
that genuinely started before 10:00 Cairo and stayed healthy through 14:15.
