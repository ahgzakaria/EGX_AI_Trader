# A Rubix candle for every session the machine was awake for

**Date:** 2026-08-30 · **Scope:** `scripts/run_rubix_daily_finalizer.py`
**Status:** implemented, one missing session healed, two correctly left to EODHD.

The rule this implements, as asked for: *take a candle from Rubix every day,
except on days the machine was not running — Rubix cannot have built one then,
so take that session from EODHD when it lands.*

The rule was already the intent of the design. It was not the behaviour, because
nothing ever retried.

---

## What was actually happening

The finalizer builds exactly one candle per run: whichever session it wakes up
on. There was no second chance. Miss the slot — the machine asleep, the task
installed later, a run that exited early because the settlement grace had not
passed — and that session's bar was never built again, while its events sat in
the Rubix store indefinitely.

`2026-08-24` is the case that exposed it:

| | |
|---|---|
| Quotes captured | 1,041,946 rows across **291 distinct minutes** |
| Bars in the normalized cache | **none** |
| Bars buildable, six days later | **206** |

The session had been fully collected. Nothing had ever turned it into candles.

## Why it was invisible

Because the history still looked complete. EODHD publishes a session a day or
two late and owns the settled body of every covered symbol, so 2026-08-24
arrived from EODHD and the gap closed itself for 225 of 241 symbols.

It did not close for the 16 `TIER_D` symbols, whose history is a frozen Yahoo
seed plus Rubix bridge bars and nothing else. For those, a session Rubix fails to
finalize is a permanent hole. `ARAB` is one: after the backfill its history
carries 2026-08-24; before it, that date did not exist.

## Telling the two causes apart

A session missing from the cache has two possible causes that look identical
from the cache alone and could not differ more in what to do:

| Cause | Evidence | Action |
|---|---|---|
| Captured, never finalized | quotes present for the session | build it — this is ours |
| Collector was not running | ~1 distinct minute | leave it to EODHD |

The separation is not close. Measured over `quotes.received_at`, the same table
and predicate `RubixDailyBuilder._load_session_quotes` selects on:

| Session | Distinct minutes | |
|---|---|---|
| 2026-08-16 | 228 | collected |
| **2026-08-17** | **1** | collector not running |
| 2026-08-18 | 299 | collected |
| 2026-08-19 | 300 | collected |
| **2026-08-20** | **1** | collector not running |
| 2026-08-23 | 302 | collected |
| 2026-08-24 | 291 | collected |
| 2026-08-30 | 296 | collected |

A collected session shows 228–302 minutes; a session where the collector was not
running shows **1** — the snapshot each symbol receives on connect. Nothing has
ever fallen between, so the threshold (`CAPTURE_MINUTES_REQUIRED = 30`) sits an
order of magnitude clear of both. It is a count, not a scan, so asking it of ten
sessions costs nothing.

## What the scheduled run now does

After finalizing its own session, it looks back over ten trading days and, for
every session with no bar in the cache, classifies and acts:

- **`FINALIZE`** — captured and unbuilt. Build it.
- **`NO_RUBIX_CAPTURE`** — the collector was not running. Report it, leave it to
  EODHD. No amount of retrying builds a candle out of minutes nobody recorded.
- **`ALREADY_UNBUILDABLE`** — built before, yielded 0 FINAL bars, and the capture
  behind that verdict has not changed since. Do not spend 78 seconds proving it
  again. `2026-08-16` is this case: 228 minutes captured, every symbol classified
  incomplete. The verdict binds only while the minute count is unchanged, so a
  capture that grows earns a fresh attempt.

It runs on holidays and on runs whose own session has not settled, because those
are exactly the runs with nothing else to do and a missed Thursday to catch.

A `--date` stays a deliberate one-off and does not trigger the backfill.

## Result of the first run

```
today:    2026-08-30  OK  204 bars, 204 skipped (already present)
backfill: examined 10 trading days — finalized 0, left_to_eodhd 2, already_unbuildable 1
  2026-08-16  228 min  ALREADY_UNBUILDABLE
  2026-08-17    1 min  NO_RUBIX_CAPTURE   -> eodhd
  2026-08-20    1 min  NO_RUBIX_CAPTURE   -> eodhd
```

`2026-08-24` is absent from that list because it was healed: **206 bars
inserted**, 0 versioned, nothing overwritten.

## What this deliberately does not change

Rubix bars are raw. They carry `swing_daily_eligible = False`, hardcoded in
`core/daily_bridge/schema.py` as "always false until official OHLC + CA +
external recon", and on a typical session most are
`COMPLETE_CONTINUOUS_AUCTION_MISSING` — no closing auction, so no official close.

So the router keeps EODHD's split-adjusted series as the body and appends Rubix
only after EODHD's last published date, renaming the result
`SPLIT_ADJUSTED_PLUS_RUBIX_RAW_TAIL` so nothing downstream reads it as uniformly
adjusted. Making a raw Rubix bar override an already-published adjusted one is a
separate decision with a real cost, and this change does not take it.

## Two defects found on the way, both fixed

They are the same failure as the missing candle, one layer up: a system that
could not tell "I do not know" from "there is nothing missing".

**1. A failed session lookup was cached forever.**
`core/research_router.py` resolves the expected completed session once per scan
and caches it. `_compute_expected_completed_session()` returns `None` only when
the calendar lookup *raised* — a failure, not an answer — and that `None` was
cached with `set: True`. One transient failure then made every later caller in
the process believe there is no completed session, until restart. Observed
directly: the cache held `{'value': None, 'set': True}` while recomputing the
same call returned `2026-08-30`. Now only a resolved session is remembered.

**2. `sessions_behind()` asked its two questions in the order that lies.**
`sector_flow/builder.py` checked "is the expected session unknown → 0" *before*
"is the store empty → None". With the poisoned cache above, an empty store —
a full rebuild required — was reported as **zero sessions behind**, nothing to
do. The guards are reordered; the empty store is now checked first.

The second was surfacing as an intermittent test failure that passed in
isolation and failed in the full suite, which is exactly what a process-global
cache poisoned by an earlier test looks like. The suite is now green at
**3,793 passed**.

## Tests

`tests/test_finalizer_backfill.py` — 24 tests. The probe (distinct minutes, dead
sessions, unreadable stores, a store with no `quotes` table), the classification
(captured/uncaptured/already-cached/already-unbuildable, weekends, holidays,
trading-day windows, ordering), and who gets a backfill at all (`--date` and
`--no-backfill` suppress it; a holiday run still gets one).

The test that matters most asserts a negative: an uncaptured session must never
be handed to the builder.
