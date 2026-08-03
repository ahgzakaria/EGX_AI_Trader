# Phase 2C — Shadow Safety Contract

The invariants the Shadow integration must never lose, and the reason each one
exists. Written during the final pre-commit review, because five of the six
defects that review found were places where an invariant was *intended* but not
actually enforced by anything.

Research only. The furthest state reachable is `ENTRY_READY_RESEARCH`, always
labelled Research Only. Production execution is disabled.

---

## 1. Source access

| Invariant | Enforced by |
|---|---|
| The source is opened `mode=ro` with `PRAGMA query_only=ON` | `ShadowSourceReader.connect` |
| A write to the source raises | SQLite itself; asserted by test on the real connection configuration |
| No `ATTACH`, `VACUUM`, checkpoint, WAL truncation or schema change | absent from the code; asserted by a code-only scan |
| No websocket, authentication, subscription change or process spawn | absent; asserted by a code-only scan |
| Paths come from CLI/environment | `--rubix-db-path`, `--research-db-path`; runtime modules contain no machine path (asserted) |
| The research target is never the source | `_assert_distinct_databases` — resolves both paths, and also refuses the source's `-wal` / `-shm` sidecars |
| The research target is never a production database | `PROTECTED_DATABASE_NAMES` deny-list plus a content probe |

**Why the sidecar check exists:** refusing only the exact source path would let
`…/rubix_live_market.db-wal` through as a "different" file, and opening that as
a research target would corrupt the collector's write-ahead log.

## 2. Cursor

`quotes.id` is a **source-progress cursor and nothing else.** `quotes.sequence`
is 100% NULL in production, so no ordering or identity may depend on it. Market
identity remains Phase 2A's `_source_identity` (row-unique) and
`_sequence_payload_identity` (market payload only).

| Case | Required behaviour |
|---|---|
| A — persistence fails before the cursor commits | cursor does **not** advance |
| B — persistence succeeds | cursor advances **in the same transaction** |
| C — the same batch is replayed | no duplicate event, bar, cycle or Lane A row |
| D — two distinct payloads share ticker + timestamp | **both** survive |
| E — one payload redelivered under different row ids | one market payload; cursor still advances past every row |

A `(ticker, market_timestamp)` key would fail case D: the source has
one-second granularity and 88,268 colliding groups per 200,000 rows.

The cursor is stored **only** in the ORB research database. Nothing is ever
written to the Rubix source.

## 3. Lane A is append-only

Lane A records what was **safely knowable at an actual observation time**.

- Its unique key includes the observation cycle, so a later correction is a
  *new row*, never an update.
- Each row carries the opening-range version it was actually bound to.
- A corrected opening range cannot replace or mutate a stored Lane A row —
  asserted by regression test at the storage layer.
- Stale evidence, unreliable market timestamps and unfinalized bars cannot
  advance Lane A. The engine is handed **only** operationally final bars, so an
  ineligible bar cannot advance state even by accident.
- **Reconstruction writes no Lane A rows at all.** A `--reconstruct` run never
  observed anything live; manufacturing Lane A rows from it would fabricate
  live history and could overwrite a real run's account of the session.

## 4. Lane B is separate and idempotent

Explicitly `HISTORICAL_REPLAY`, in its own table, with its own identity. It may
accept delayed evidence and bind to a revised opening range. An identical
reconstruction inserts nothing. It may **never** be presented as something that
was available live.

## 5. The comparison must not collapse

Seven categories, evaluated most-decisive-first:

```
DATA_UNAVAILABLE → HISTORICAL_ONLY → LIVE_ONLY → IDENTICAL
                 → FRESHNESS_REJECTED_LIVE → OPENING_RANGE_REVISED
                 → STATE_DIFFERENCE
```

Two ordering decisions matter:

**Freshness outranks an opening-range revision.** When both are true, staleness
is *why* the state was unreachable live; the revision is secondary detail.
Reporting the revision alone would imply a versioning artefact and understate
that the reconstructed state was never actionable.

**Freshness means two different things, and both count.** The *feed* can be
stale (visible in `live_status`), **or** the *live decision capability* can be
disabled while the feed is perfectly healthy (visible only in the Lane A
record's own state and rejection reasons). `_live_rejected_on_freshness` checks
both. Keying only on feed status mis-attributed the entire smoke — 34 symbols,
including the one research candidate — to opening-range revisions.

`STATE_DIFFERENCE` exists so a genuine divergence is never mislabelled with a
cause that was not established.

## 6. Watermark and bar finality

Two clocks, never conflated: the **exchange watermark** (from
`market_timestamp`) decides which bars are closed; the **source cursor**
(`quotes.id`) decides what has been read. Conflating them is how a pipeline
convinces itself a bar is final because it stopped receiving rows.

A bar is operationally final for Lane A only when **all** hold: its end has
passed; every component bar exists; the lateness grace has elapsed; live
evidence is fresh; it is not auction data and does not bridge into the auction.

Negative receive lag is **clock skew, not freshness** — counted explicitly, and
never treated as fresh. Clamping it would flatter the feed.

## 7. Session classification cannot be talked up

`FULL_SHADOW_SESSION` requires every configured condition. A run started after
10:00 Cairo is **always** partial — it could not have observed the opening range
forming, and no later evidence fills that hole. A run after the close is a
`PARTIAL_SMOKE_SESSION`.

There is **no upgrade path**: `classify_session` has no `force`, `full` or
`override` parameter, and `smoke` only ever downgrades. Backdating
`--session-date` makes a run look *more* partial, never full, because
classification is computed from the runner's real start/finish instants against
that date's exchange window.

## 8. Storage records values, not reprs

Enums are persisted through `_enum_value`, which writes `member.value`.

On Python 3.11+, `str(member)` of a `str`-mixin Enum returns
`"ClassName.MEMBER"`. Writing that silently corrupted every status column and
broke equality against the declared vocabulary — including the comparison,
which reads `live_status` back to decide whether freshness was the cause. The
data looked fine until something tried to use it.

## 9. Nothing that trades

No orders, executions, positions, trades, P&L, broker events, notifications or
alerts — asserted at the **column** level, not only by table name. No position
sizing, portfolio heat or daily-loss limit. No Dashboard or Streamlit file. The
runner is deliberately not registered with the production launcher, starts no
process, and leaves no thread or child behind.

## 10. What this contract does not claim

That the strategy works, is calibrated, or is profitable. That live decisions
are safe to enable. That a full Shadow session has happened. Those remain open,
and Phase 2C's job was only to make observing them possible without risk.
