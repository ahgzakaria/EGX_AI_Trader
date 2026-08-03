# Phase 2A — Event Ordering and Identity Without Sequence Data

**Headline: this was the most serious defect found in Phase 2A, and it was invisible to the
existing test suite because every test supplied a `sequence` value that production never has.**

---

## 1. Confirming the premise

Independently measured over the continuous window of all 13 observed sessions:

| Measurement | Value |
|---|---:|
| Quote rows in `[10:00, 14:15)` | 2,474,879 |
| Rows carrying a non-null `sequence` | **0** |
| Rows carrying `market_timestamp` | 2,474,879 |
| Rows carrying `received_at` | 2,474,879 |
| Rows with `received_at < market_timestamp` (clock skew) | 3,885 |

The implementer's figure of 0 sequence values in 2,539,336 rows is confirmed; the small
difference is window scope (theirs includes the auction). **There is no provider sequence at all.**

---

## 2. Every implementation path that depended on `sequence`

| Path | Depended on sequence? | Production consequence before the fix |
|---|---|---|
| Duplicate detection | **Yes, exclusively** — `if sequence is not None:` gated the entire `_seen_sequences` check | **No duplicate detection ran at all** |
| Reconnect handling | Yes, implicitly — a redelivered quote was expected to repeat its sequence | Redelivery was accepted as a new event |
| Ordering | No — ordering uses `market_timestamp_utc` with `out_of_order_tolerance_seconds` | Correct, but see §5 |
| Cumulative-volume deltas | Partly — `SEQUENCE_GAP_UNALLOCATABLE` and `SEQUENCE_REGRESSION_UNALLOCATABLE` | Both statuses are unreachable in production; delta safety instead rests on the decrease and >60 s gap rules, which do fire |
| Unique database keys | No — `orb_normalized_events` is keyed on `source_identity` | Correct, but `source_identity` includes replay metadata (§3) |
| Replay reproducibility | No | Deterministic from a database replay; **not** deterministic across a live reconnect |
| Bar `update_count` | No | **Inflated** — see §4 |
| `duplicate_sequence_policy` config field | Yes | Dead configuration in production |

---

## 3. The identity hash was too wide

Two hashes were already being computed for every event:

| Function | Fields included | Used where |
|---|---|---|
| `_source_identity` | ticker, rubix symbol, **market timestamp, receive timestamp**, sequence, price, cumulative volume, bid, ask, event type, **source row id** | Database primary key |
| `_sequence_payload_identity` | ticker, rubix symbol, market timestamp, sequence, price, cumulative volume, bid, ask, event type | *Only inside the dead `sequence is not None` branch* |

`_source_identity` is the primary key, and it includes `receive_timestamp` and `source_row_id`.
Those are **replay metadata, not market facts**. The same market event redelivered after a
reconnect carries a different receive time and a different row id, so it hashes differently and
survives `INSERT OR IGNORE` as a distinct row.

`_sequence_payload_identity` is exactly the right key — the market payload with replay metadata
excluded — and it was already there, computed, unused on the only code path that runs.

Reproduced directly:

```
exact object replay            second_accepted=True   identity unchanged  (saved only by the PK)
reconnect redelivery           second_accepted=True   identity DIFFERS    <-- duplicate row
same timestamp, distinct price second_accepted=True   identity DIFFERS    <-- correct, must stay
```

---

## 4. Scale of the exposure in real data

Measured across the same 2,474,879 continuous rows:

| Measurement | Count | Share |
|---|---:|---:|
| Rows that exactly repeat an earlier market payload for the same symbol and session | **1,330,647** | **53.8%** |
| Rows sharing the market timestamp of the immediately preceding row for the same symbol | 1,339,789 | 54.1% |
| Symbols affected by exact payload repeats | **264 of 265** | 99.6% |

More than half the feed is redelivery. Concretely, before the fix:

- `orb_normalized_events` would have stored roughly **2.2 rows per real market event**.
- `orb_bars.update_count` was inflated by the same factor. Since the Rubix schema has no trade
  count, `update_count` is the natural activity proxy for Phase 2B — it would have been
  measuring **feed chattiness, not market activity**. The readiness document already warns that
  the vendor's `candles_1m.updates` "is not treated as trades"; the defect recreated that exact
  trap inside Phase 2A's own table.
- Event retention would have run against roughly double the necessary volume.

Price OHLC and volume were *not* corrupted: repeats carry the same price, and a repeated
cumulative value yields a legitimate zero delta.

Direct reproduction — a single minute with three real quotes, each redelivered once:

```
before fix:  events kept = 6   update_count = 6
after fix:   events kept = 3   update_count = 3
```

---

## 5. The ordering key, stated explicitly

Phase 2A's real ordering key, with no sequence available, is:

1. **`market_timestamp_utc`** — primary and authoritative. Bucketing, bar assignment, opening-range
   slot assignment and out-of-order detection all use it exclusively.
2. **`canonical_ticker`** — a tie-break only; ordering state is already partitioned per
   `(canonical_ticker, session_date)`, so symbols never interleave.
3. **`source_identity`** — the final deterministic tie-break inside `bars.py`'s sort key. It is a
   content hash, so the ordering of two same-timestamp events is stable across runs but
   arbitrary with respect to true arrival order.
4. **Insertion order is *not* used.** `load_events` orders by
   `market_timestamp_utc, canonical_ticker, sequence` — and with `sequence` always `NULL`, the
   effective ordering is timestamp then ticker, then SQLite's unspecified row order.

This is deterministic and defensible. Two observations:

- **Point 3 is benign for OHLC.** High and low are order-independent. Open and close are
  order-dependent, so two distinct trades in the same microsecond could in principle swap
  open/close within a minute. With microsecond market timestamps this is an edge case, and the
  ordering is at least stable across replays.
- **Point 4 should be tightened.** `load_events` should order by
  `market_timestamp_utc, canonical_ticker, source_identity` so the reload order matches the
  aggregation order exactly. Recommended, not applied — it changes a read path with no
  demonstrated failure, which is outside the fix criteria for this review.

### Out-of-order rejection is strict, and costs real data

`out_of_order_tolerance_seconds` is `0.0`, so **any** event whose market timestamp precedes the
last seen timestamp for that symbol/session is rejected outright with `OUT_OF_ORDER_REJECTED` —
its price is discarded, not merely flagged.

Measured cost: **20,590 rows (0.83%)** arrive out of market-timestamp order relative to insertion
order. Those prices would be dropped. The rejection is audited, so nothing is silent, but a
zero tolerance on a feed with no sequence and a 165-second median delivery lag deserves a
deliberate decision rather than a default. Recorded as a Phase 2B gate; not changed here,
because widening the tolerance is a data-policy decision, not a defect fix.

---

## 6. Same-timestamp events are not collapsed

The requirement is precise: two distinct events sharing a timestamp must not be collapsed merely
because sequence is absent, while exact replayed duplicates must remain idempotent. Both hold.

| Scenario | Behaviour | Correct? |
|---|---|---|
| Same timestamp, different price | Both kept, distinct identities, no quality event | Yes |
| Same timestamp, different cumulative volume | Both kept | Yes |
| Same timestamp, different bid or ask | Both kept | Yes |
| Same timestamp, identical every market field, different receive time | Second dropped, `DUPLICATE_MARKET_PAYLOAD_IDENTICAL` recorded | Yes |
| Same timestamp, identical every market field, different source row id | Second dropped | Yes |
| Whole batch replayed after a process restart | Zero new rows, zero new quality events | Yes |

Collapsing the last three is correct on the merits: two events identical in every verified
market field are indistinguishable as market facts and contribute nothing distinct to a bar.
The only thing that differed was how the row reached us.

---

## 7. Collision safety for the observed Rubix schema

A false collapse requires two genuinely different market events that agree on **all** of ticker,
Rubix symbol, market timestamp (microsecond), sequence, last price, cumulative volume, bid, ask
and event type.

Because `cumulative_volume` is monotonic within a session, any two events separated by a real
trade differ in it. Two events can therefore only collide if the same symbol printed the same
price, the same bid, the same ask and the *same cumulative volume* at the same microsecond —
which, given a monotonic counter, means no trade occurred between them. That is a redelivery,
not two events.

sha256 is used, so accidental hash collision is not a practical concern.

**Assessment: collision-safe for the observed Rubix schema.** The one caveat is that safety
rests on `cumulative_volume` being present and monotonic. When `cumulative_volume` is `NULL`
across two same-timestamp quote refreshes with unchanged price and unchanged bid/ask, they are
indistinguishable and will collapse — correctly, since such events carry no distinguishable
market information.

---

## 8. What changed

**`BLOCKER_FIX`** — the market-payload identity is now the deduplication key for every event,
not only sequenced ones:

```python
seen_payloads = self._seen_payloads.setdefault(key, set())
if sequence_identity in seen_payloads:
    issue("DUPLICATE_MARKET_PAYLOAD_IDENTICAL")
    return None, tuple(issues)
```

The pre-existing sequence checks are untouched and still run first, so
`DUPLICATE_SEQUENCE_IDENTICAL` and `DUPLICATE_SEQUENCE_CONFLICT` retain their meaning if a
provider sequence ever appears.

**Memory note.** `_seen_payloads` holds one 64-character hash per unique payload per
symbol/session. At the observed rate — roughly 1.1M unique payloads across 13 sessions, so about
85k per session — this is on the order of 10 MB of live state for a full session. Acceptable
for a shadow service, but it must be cleared on session rollover and should be measured during
the first live shadow run.

**Tests added** (each verified to fail against the pre-review code):

- `test_reconnect_redelivery_without_sequence_is_deduplicated`
- `test_update_count_is_not_inflated_by_feed_redelivery`
- `test_same_timestamp_distinct_events_are_never_collapsed_without_sequence`
- `test_replaying_a_whole_batch_changes_nothing` — full ingest replayed through a *restarted*
  service; asserts zero new events, zero new bars and zero new quality events
