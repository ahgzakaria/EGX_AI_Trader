# Phase 2A — Price/Volume Separation, Bar Completion, and Readiness Validity

---

## 1. Price quality and volume quality are genuinely independent — verified

The required contract is that a volume-quality failure must never invalidate valid price OHLC.
Every listed failure mode was reproduced directly against the pipeline. In every case the bar
survived with correct OHLC and volume degraded independently.

| Volume failure mode | Bar produced | OHLC | `volume` | Flags |
|---|---|---|---|---|
| Cumulative reset (100 → 100 → 40) | Yes | correct | `None` | `VOLUME_UNAVAILABLE`, `VOLUME_CUMULATIVE_VOLUME_DECREASE` |
| Cumulative decrease (130 → 129) | Yes | correct | `None` | `VOLUME_UNAVAILABLE`, `VOLUME_CUMULATIVE_VOLUME_DECREASE` |
| Gap > 60 s with a positive jump | Yes | correct | `None` | `VOLUME_UNAVAILABLE`, `VOLUME_MISSING_INTERVAL_UNALLOCATABLE` |
| Missing cumulative volume (`NULL`) | Yes | correct | `None` | `CUMULATIVE_VOLUME_UNAVAILABLE`, `VOLUME_UNAVAILABLE` |
| Reconnect / first event of session | Yes | correct | `None` | `VOLUME_UNAVAILABLE`, `VOLUME_BASELINE_ESTABLISHED` |
| Duplicate cumulative value | Yes | correct | valid **zero** delta | — |
| Volume absent from every event | Yes | correct | `None` | `VOLUME_UNAVAILABLE` |

Checked against the required invariants:

| Invariant | Result |
|---|---|
| Price OHLC remains valid when enough trustworthy prices exist | Holds. A bar is emitted whenever at least one positive price exists in the minute |
| Bar volume becomes unavailable or degraded independently | Holds. `volume` is `None`, never a substitute value |
| No negative volume | Holds. A decrease yields `None`; subtraction never reaches the output |
| No invented zero presented as genuine | Holds — and this is the subtle one. A genuine zero (equal consecutive cumulative values) is `0.0` with status `AVAILABLE`; an unknown is `None` with a status naming the reason. The two are never conflated |
| Volume-dependent proxy stays unavailable | Holds. `bar_weighted_typical_price_proxy` returns `None` if **any** contributing bar has `volume is None`, and again if the total is not positive |
| Opening Range price high/low may still become `READY` | Holds — §3 |
| Its volume total must stay unavailable when invalid | Holds — `valid_volume_total is None` plus a `VOLUME_UNAVAILABLE` flag |

**Status flags survive the whole chain.** Verified end to end:
event `volume_delta_status` → bar `data_quality_flags` (prefixed `VOLUME_<STATUS>`, so the
originating reason is recoverable, not merely "unavailable") → opening-range
`data_quality_flags` → `orb_normalized_events.volume_delta_status` and
`orb_bars.data_quality_flags_json` → the readiness report's volume-semantics table.

**Now covered by test:** `test_broken_volume_never_invalidates_valid_price_ohlc` (parametrised
over five failure modes), `test_volume_gap_over_sixty_seconds_degrades_volume_only`,
`test_opening_range_price_ready_while_volume_total_stays_unavailable`.

---

## 2. One-minute bar completion

| Requirement | Result |
|---|---|
| Uses events from the correct minute only | Holds. `_bucket_start` truncates to the Cairo wall minute; grouping is per `(ticker, session_date, bucket)` |
| Completes only after the minute boundary | Holds. `if end > evaluated: continue`, with an explicit `PARTIAL_ONE_MINUTE_BAR_EXCLUDED` quality event |
| Never uses a later event's price inside an earlier bar | Holds. There is no forward fill and no cross-bucket carry; a 10:00 and a 10:02 event produce bars at 10:00 and 10:02 with nothing at 10:01 |
| Never silently creates empty bars | Holds. A bucket with no positive price is skipped with `BAR_PRICE_UNAVAILABLE` |
| Retains valid first/last timestamps | Holds. `bar_start_utc` / `bar_end_utc` are exchange-clock derived, so they are correct even if the first event arrives mid-minute |
| `update_count` correct after deduplication | **Was wrong — now fixed.** Deduplication did not occur at all in production. See `PHASE2A_EVENT_IDENTITY_REVIEW.md` §4 |

`open` and `close` are taken from the sorted member list rather than from the raw input, and
members are appended inside a loop that iterates the events already sorted by
`(market_timestamp, ticker, sequence, source_identity)`. Ordering is therefore correct by
construction, not by caller discipline.

---

## 3. Five-minute aggregation

| Requirement | Result |
|---|---|
| Requires five completed consecutive 1-minute slots | Holds. Missing any of the five exact expected slots yields `INCOMPLETE_FIVE_MINUTE_COMPONENTS` and no bar |
| Aligned to the exchange clock, not first-observed event time | Holds. `_bucket_start(bar_start, 5, zone)` uses `minute // 5 * 5` on Cairo wall time |
| Never bridges 09:59 to 10:03 | Holds. Pre-session events never form 1-minute bars, so no pre-open component can exist |
| Never bridges continuous trading into the auction | Holds. Verified with a 14:10–14:19 stream: the only 5-minute bar is 14:10–14:15 |
| Never aggregates a partial component | Holds. Components must have `interval_minutes == 1` and `completed` |
| Reports missing component minutes explicitly | Holds, with `observed=N;expected=5` in the detail |
| Reproducible after replay | Holds. Deterministic from the persisted events |

Direct probe of the required examples, with 1-minute bars present for 10:00–10:14:

```
as_of = 10:15 − 1 µs   ->  ['10:00', '10:05']
as_of = 10:15          ->  ['10:00', '10:05', '10:10']
```

**The completed 10:10–10:15 bar is not available before 10:15, to the microsecond.**
Covered by `test_five_minute_bar_is_unavailable_one_microsecond_before_its_close` and
`test_five_minute_bars_never_bridge_pre_session_or_the_auction`.

Note that 5-minute buckets align cleanly with every configured boundary because 10:00, 10:15,
13:30 and 14:15 are all multiples of five minutes. A configuration that broke that alignment
would produce a bucket spanning a phase change. `config.__post_init__` does not currently
enforce five-minute alignment of the session times — worth adding if those times ever become
operator-editable.

---

## 4. Opening-range freeze and revision semantics

| Requirement | Result |
|---|---|
| `READY` requires all configured required slots | Holds. `opening_range_minimum_bar_coverage = 1.0` means all 15 |
| High/low derived only from those slots | Holds. `max`/`min` over `valid`, which is filtered to the expected slots |
| `frozen_at` deterministic | Holds — it is the caller's `as_of`, not a wall-clock read, so a replay reproduces it |
| First `READY` cannot be overwritten silently | Holds at two levels: the `ON CONFLICT … DO UPDATE … WHERE is_frozen=0` clause refuses to touch a frozen row, and the partial unique index `WHERE is_frozen=1` enforces one frozen range per symbol/session in the database itself |
| Late data creates an explicit revision/audit record | **Was audit-only — now fixed** |
| Readers can distinguish original from correction | **Was impossible — now fixed** |
| Phase 2B state can attach to the exact range version used | Holds after the fix; `(session_id, canonical_ticker, revision)` is the version key |

**The defect.** `shadow.py` detected a divergent `READY` candidate and wrote a
`FROZEN_OPENING_RANGE_REVISION_DETECTED` quality event containing the original and revised
`source_identity` hashes — then discarded the corrected range. The schema was built for
revisions (`revision` column, `UNIQUE(session_id, canonical_ticker, revision)`), and nothing ever
wrote `revision >= 1`. A reader could learn that a correction existed but never what it was.

**The fix.** `record_opening_range_revision` persists the corrected result at the next revision
number, leaving `revision=0` frozen and byte-identical, and returns `None` if that exact
correction is already stored so repeated ingests cannot grow an endless chain.
`load_opening_range_revisions` exposes the ordered version list.

**Schema assessment: the schema could always preserve both. This is not a Phase 2B schema
blocker.** It was a code gap, now closed.

Two residual notes:

- `OpeningRangeRegistry` in `opening_range.py` is dead on the production path — `shadow.py`
  implements its own freeze logic and never uses it. The two have already diverged: the registry
  compares `source_identity` **and** high **and** low; the shadow path compares `source_identity`
  only, so a cosmetic change to a non-extreme bar registers as a revision. Both are defensible;
  having both is not. Left in place, since the registry is the better contract and the natural
  home for Phase 2B in-memory state — but they must be unified before Phase 2B depends on either.
- `OpeningRangeStatus.INVALID_DATA` is returned for a duplicate slot and, separately, for invalid
  OHLC. The distinguishing flag is present, but a reader keying on status alone cannot tell them
  apart.

---

## 5. Readiness audit validity

Definitions were read from `inspect_orb_rubix_readiness.py` and recomputed independently.

| Term | Definition as implemented |
|---|---|
| Complete session | `complete_symbols / observed_symbols >= 0.90`, where a symbol is complete at `>= 0.95 × 255` observed minutes |
| Dense partial session | not complete, and `total_rows / (symbols × 255) >= 0.80` |
| Average covered minutes | mean of per-session means of distinct observed minutes per symbol |
| Unique symbol/session count | `Σ symbols_observed` across sessions — the `3013` denominator |
| Opening Range ready | 15 distinct minute timestamps in `[10:00, 10:15)` **and** valid OHLC on every one |
| Cumulative-volume reset | `volume < previous_volume` within the continuous window |
| Gap over 60 s | `positive delta` after a market-timestamp gap exceeding `maximum_volume_delta_gap_seconds` |
| Bid/ask availability | both non-null; "valid spread" additionally requires `bid > 0 and ask >= bid` |
| Freshness distribution | `received_at − market_timestamp`, **negative values silently dropped** |

### Reproducibility

The command was re-run read-only into a scratch directory. All six artifacts are
**byte-identical** to the committed ones. The audit is deterministic.

### Denominator integrity

| Possible inflation | Present? |
|---|---|
| Auction records | **No** — the coverage query stops at `continuous_end` |
| Duplicate sessions | **No** — `candles_1m` is `PRIMARY KEY(ticker, minute)` |
| Repeated ingestion | **No** — same reason |
| Partial session copies | **No** |
| Non-trading dates | **No** in the current data — all 13 dates are regular EGX trading days. But the script never calls `is_regular_trading_day`, so this holds by luck rather than by construction |
| Inactive symbols | **Yes** — 316 archived-symbol-sessions inflate `3013` |
| Symbols outside the active 241 | **Yes** — same 316 |

### Reconciled counts

| Metric | A. All observed | B. Active-241 and mapped |
|---|---:|---:|
| Sessions | 13 | **12** |
| Symbol-sessions | 3013 | **2697** |
| Opening-range ready | 162 | **162** |
| Average minutes per symbol/session | 57.11 / 255 | **63.56 / 255** |
| Complete-density sessions | 0 | 0 |
| Dense partial sessions | 1 | 1 |

The `162` is **not** inflated — every ready range belongs to the active, mapped subset.
The `3013` **is** inflated; the operational denominator is `2697`. Session 2026-07-15 disappears
entirely under the operational filter: its only 20 observations are all archived symbols.

### Per-session detail (all observed symbols)

| Date | Day | Status | Symbols | Avg min | Density | OR ready |
|---|---|---|---:|---:|---:|---:|
| 2026-07-14 | Tue | PARTIAL | 244 | 1.96 | 0.8% | 0 |
| 2026-07-15 | Wed | PARTIAL | 20 | 1.00 | 0.4% | 0 |
| 2026-07-16 | Thu | PARTIAL | 245 | 58.76 | 23.0% | 0 |
| 2026-07-19 | Sun | PARTIAL | 245 | 64.79 | 25.4% | 0 |
| 2026-07-20 | Mon | PARTIAL | 265 | 17.55 | 6.9% | 0 |
| 2026-07-21 | Tue | PARTIAL | 245 | 52.88 | 20.7% | 0 |
| 2026-07-22 | Wed | PARTIAL | 265 | 53.71 | 21.1% | 0 |
| 2026-07-26 | Sun | PARTIAL | 245 | 58.66 | 23.0% | 0 |
| 2026-07-27 | Mon | PARTIAL | 265 | 48.69 | 19.1% | 0 |
| 2026-07-28 | Tue | PARTIAL | 244 | 55.16 | 21.6% | 0 |
| 2026-07-29 | Wed | PARTIAL | 263 | 53.83 | 21.1% | 0 |
| 2026-07-30 | Thu | PARTIAL | 243 | 53.98 | 21.2% | 0 |
| **2026-08-02** | **Sun** | **DENSE_PARTIAL** | **224** | **221.40** | **86.8%** | **162** |

**All 162 opening ranges come from one session.** Twelve of thirteen sessions produced zero.
The 57.11-minute headline is carried entirely by 2026-08-02; the other twelve average about 48
minutes and sit below 26% density. "13 intraday dates" is arithmetically true and operationally
misleading: the usable ORB evidence base is **one session**.

---

## 6. Two validity concerns with the readiness method

### 6.1 It measures the vendor's candles, not the Phase 2A pipeline

`_coverage_audit` reads Rubix's own `candles_1m` table. The Phase 2A bar builder reads `quotes`.
`162 / 3013` is therefore a statement about **the vendor's aggregation**, not about what
`scalping_orb.bars` + `scalping_orb.opening_range` would produce from the same session.

This matters because the two differ in ways Phase 2A deliberately introduced: quotes with
`sequence`-free identity, a 60-second freshness budget, zero out-of-order tolerance, and
volume-delta allocation rules. None of those apply to `candles_1m`.

**No end-to-end validation of the Phase 2A pipeline against real quote data exists.** That is
the single most important open gap, and it is a Phase 2B gate: the pipeline should be run over
the 2026-08-02 quotes into a throwaway research database and its opening-range readiness
compared against the 162.

### 6.2 The freshness distribution is biased and exceeds the configured budget

Independently measured over 2,474,879 continuous rows:

| Measurement | Value |
|---|---:|
| Median receive-minus-market lag | **165.1 s** |
| p95 lag | 3,708 s |
| Maximum lag | 45,087 s |
| **Rows exceeding the 60 s freshness budget** | **1,456,778 (58.9%)** |
| Rows with negative lag, silently excluded from the distribution | 3,885 |
| Median inter-quote gap per symbol | 4 s |
| p95 inter-quote gap | 60 s |
| Inter-quote gaps exceeding 60 s | 4.97% |

Two conclusions:

- The **inter-quote cadence is healthy** — a 4-second median means volume-delta allocation
  usually succeeds, and the `MISSING_INTERVAL_UNALLOCATABLE` rule will fire on roughly 5% of
  transitions plus one baseline per symbol/session. That is a good result for the volume model.
- The **delivery lag is not** — a 165-second median against a 60-second budget means most quotes
  would be flagged `STALE_QUOTE`. Whether that reflects true delivery latency or a
  `market_timestamp` that carries its own publication semantics is unresolved by the local
  schema, and it must be resolved before any Phase 2B freshness gate is calibrated.

The `if freshness >= 0` filter also drops all 3,885 negative-lag rows from the distribution
without reporting how many were dropped, which biases the reported percentiles upward. Minor,
but the count should be surfaced.

### 6.3 Minor robustness note

`_write_reports` indexes `opening_rows[0]`, `minute_rows[0]` and `volume_rows[0]`, but only
`session_rows` is guarded for emptiness in `run`. A dataset producing coverage rows but no
volume rows would raise `IndexError` rather than reporting "no data". Not reachable with the
current schema; worth a guard.
