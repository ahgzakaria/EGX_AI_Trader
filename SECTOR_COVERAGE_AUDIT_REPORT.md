# Universe Coverage Audit — Phase 6

34 of the 232 classified symbols failed to load into the sector history. This
audits why, fixes what belongs to this work, and reports one defect that does
not.

**Result: 34 unavailable → 19.** Loaded symbols went from 198 to 209.

---

## Fixed: the indicator bar minimum was applied to turnover

`sector_flow/builder.py` inherited `min_bars: 250` from the shared data
settings. That threshold exists because **indicators** need history — a moving
average or a momentum rank cannot be computed from 50 bars. Turnover
aggregation needs none of it: a symbol listed three months ago traded real value
on those days, and excluding it understates its sector's share on exactly the
sessions a new listing is busiest.

Measured before the fix, over the last 30 sessions:

| Symbol | Bars | First session | 30-session turnover |
| --- | --- | --- | --- |
| KORA.CA | 52 | 2026-06-11 | **3,066,028,600** |
| VLMRA.CA | 177 | 2025-12-01 | 1,531,913,000 |
| TYCN.CA | 78 | 2026-04-28 | 847,475,000 |
| CPME.CA | 117 | 2025-07-23 | 781,067,300 |
| FERC.CA | 234 | 2025-09-10 | 735,033,600 |
| …5 more | | | 1,340,020,400 |

**8.30bn EGP — 2.28% of all market turnover — was being discarded**, 3.07bn of
it from a single heavily-traded new listing. Since every sector's share is a
fraction of that total, the error touched every number on the page.

`TURNOVER_MIN_BARS = 20` now applies to this path: enough to be a real trading
record, far below what indicators require.

## Fixed: four renamed companies were classified twice

Four EGX companies appear in the universe under both their old and new tickers,
sharing one ISIN each:

| Retired ticker | Live ticker | ISIN |
| --- | --- | --- |
| EDBM — Lift Slab | **CRST** — Creast Mark | EGS23141C012 |
| AMII — Arabian Metal | **ARVA** — Arab Valves | EGS3E1E1C013 |
| PIOH — Pioneers Holding | **ASPI** — Aspire Capital | EGS691L1C018 |
| SRWA — Sarwa Capital | **CNFN** — Contact Financial | EGS738I1C018 |

In each pair one row is `VERIFIED_FEED_OBSERVED` and the other
`UNVERIFIED_NO_FEED_OBSERVATION`. Both were in `data/sectors.csv`. Nothing was
double-counted only because the retired ticker happened to fail loading — an
accident, not a design. `deduplicate_by_isin()` now keeps the feed-observed row
and reports the other as `SUPERSEDED_BY_ISIN_TWIN`, and a test asserts no two
mapped tickers share an ISIN.

Sector map: 232 → 228 classified, 94.6% of the active universe.

## What the fixes changed

Rebuilt on 2026-08-26, the numbers moved enough to matter:

| | Before | After |
| --- | --- | --- |
| Symbols loaded | 198 | **209** |
| Construction — symbols | 8 | **11** |
| Construction — share | 6.30% | **8.05%** |
| Construction — RVOL | 0.97× | **1.09×** |
| Non-bank Financial — symbols | 24 | **28** |
| Real Estate — share | 25.20% | 24.32% |

Sector strength reordered with it: Trade & Distributors moved to second place
(RVOL 1.26×, previously read as 0.94×). That value feeds the live Edge score, so
this correction reaches ranking, not just the page.

---

## Not fixed: the universe refresh only reached one system

Nine symbols still fail with `DATA_UNAVAILABLE — "no validated local seed and
EODHD unsupported"`. **At least one of them is not unsupported**: `QNBA` has
4,220 EODHD bars cached, 2001-08-14 to 2026-08-17.

Following it produced a single root cause with two symptoms.

`core/research_router.py` defaults any unknown symbol to unsupported:

```python
def symbol_tier(symbol):
    entry = tier_map().get(_base(symbol))
    return (entry or {}).get("tier", "TIER_D_UNSUPPORTED_OR_MANUAL")
```

`tier_map()` reads `data/eodhd/historical_symbol_routing_review.json`, generated
`2026-07-23` with **265 entries** — the retired universe. It holds `QNBE` and
not `QNBA`, the ticker that replaced it. Regenerating it does not help on its
own: `scripts/build_eodhd_routing_tiers.py` takes its symbol list from
`reports/eodhd_symbol_mapping.csv`, which is *also* the retired 265 list of
2026-07-23. The whole EODHD evidence chain predates the universe.

Measured against the live universe:

| | Count |
| --- | --- |
| Active symbols | 241 |
| Present in the tier file | 225 |
| **Absent → silently `TIER_D_UNSUPPORTED`** | **16** |
| Tier entries for symbols no longer active | 40 |

The 16 are not obscure: **GB Corp (AUTO)**, **Orascom Investment Holding
(ORMT)**, **Qatar National Bank (QNBA)**, National Drilling, Pioneers Holding,
Sarwa Capital, Arabia Investments Holding.

### The same 16 are also missing from the live feed — and that part is by design

Every one of the 16 carries `UNVERIFIED_NO_FEED_OBSERVATION`, and the two sets
are **identical**: `absent from tier file` ∩ `never observed by the feed` = 16,
with nothing in either set alone. Both systems predate the universe's
`source_as_of` of 2026-07-30.

An earlier reading of this — that the collector is still subscribed to the old
265-symbol list — was wrong. The live `quotes` table holds 265 tickers because
it accumulated them before the migration. The current subscription is correct:
`build_rubix_subscription_plan()` requests all 241, produces 225 valid
subscriptions, and reports exactly these 16 as `unmapped_symbols`. Over the last
week the collector recorded 224 tickers, **none of them retired**.

The 16 are excluded deliberately. `providers/rubix_subscription.py` will not
emit a `CASE~TICKER` key for a symbol with no verified mapping:

> Active symbols with no VERIFIED Rubix mapping. Reported, never guessed — an
> operator resolves these; a fabricated `CASE~` key is never emitted.

That is a chicken-and-egg the code resolves in favour of safety: a symbol needs
an observed mapping to be subscribed, and cannot be observed without one, so a
human confirms it rather than the software guessing.

### What this needs, in order

1. An operator resolves the Rubix mapping for the 16 `unmapped_symbols`. This is
   the gate the code was written to enforce and it is not automatable here.
2. Rebuild `reports/eodhd_symbol_mapping.csv` against the 241-symbol universe.
3. Re-run the EODHD evidence audits that the tier generator consumes.
4. Regenerate the routing review with `scripts/build_eodhd_routing_tiers.py`.

**None of this was done here.** Step 1 is an operational decision about real
securities; steps 2–4 rebuild approval-gated artifacts (`approved`,
`evidence_status`, `last_reviewed`) that decide which provider serves which
symbol across the whole application. Neither belongs to a sector-liquidity task.

It is worth recording that this is the fourth artifact found in this work built
from the retired 265-symbol universe, after `data/symbols.csv`, the sector map
this project first shipped, and `reports/eodhd_symbol_mapping.csv`.

## The rest of the 19

| Cause | Count | Symbols | Assessment |
| --- | --- | --- | --- |
| `VOLUME_POLICY_UNRESOLVED` | 5 | ACGC, JUFO, MTIE, NCCW, OCPH | **Correctly excluded.** An unresolved corporate action sits in the volume lookback; using that volume would corrupt the turnover it feeds. Self-resolving as the action ages out. |
| `LOCAL_SEED_ONLY_STALE` | 4 | CFGH, DEIN, MEGM, TRTO | Frozen seed stops at 2026-07-22 and the Rubix bridge appended nothing. The bridge is not carrying these symbols. |
| `LOCAL_PLUS_RUBIX_STALE` | 1 | NAHO | Bridge appended 3 sessions, still short of the expected close. |
| `DATA_UNAVAILABLE` | 9 | above | Stale routing tier file. |

## Verification

* `tests/test_sector_flow.py` — 27 tests, including no two mapped tickers
  sharing an ISIN, the supersede rule being order-independent, symbols without
  an ISIN all surviving, and the turnover path not inheriting the indicator
  minimum.
* A circular import introduced with the strength module was found and broken:
  `sector_flow.builder → decision_support → service → sector_flow.strength →
  sector_flow.builder`. Tests passed only because they imported in a lucky
  order. Storage constants moved to `sector_flow/__init__.py`, and a test now
  imports each module first in a fresh interpreter.
