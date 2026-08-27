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

### Steps 2 and 4 were subsequently authorised and done; 1 and 3 were not

**Step 2 — symbol mapping rebuilt.** `scripts/audit_eodhd_symbol_mapping.py`
already read the live universe; it had simply never been re-run. Against
EODHD's real EGX list (242 symbols) the result is **241/241 `VERIFIED_EXACT`** —
no not-found, no non-equity, no duplicates. Every one of the 16, QNBA and AUTO
and ORMT included, is genuinely carried by EODHD. They were "unsupported" only
because a file from 2026-07-23 did not name them.

**Step 4 — routing review regenerated.** 265 entries become 241: 40 retired
symbols removed, 16 active ones added, and **zero tier changes for the 225
symbols that were already there**. The regeneration is a realignment to the
universe, not a reclassification of anything previously evidenced. Every entry
remains `approved: false`.

Two defects in the generator were fixed on the way:

* `pd.read_csv` read the EGX ticker `NULL` (Fitness Prime) as a missing value,
  which would have written a routing entry for the symbol `"NAN"`.
* The final branch granted `TIER_A_FORWARD_SAFE` — the most permissive tier —
  with the reason *"recent prices validated, no scale issue, no recent split"*
  to any mapped symbol that fell through. For the 15 symbols appearing in **no**
  evidence audit, not one of those things had been checked. Being mapped to
  EODHD is not evidence about the data behind the mapping. Such a symbol is now
  held at `evidence_status: not_evaluated`, `risk_level: hold`, with the reason
  *"mapped to EODHD but absent from every evidence audit; re-run the audits
  before any tier can be justified"*.

**The 16 are still blocked, and that is the correct outcome.** Loading `QNBA`
after the rebuild still raises `DATA_UNAVAILABLE`. Steps 2 and 4 can align the
artifacts with the universe; they cannot manufacture the evidence that decides a
tier. Unblocking these symbols requires step 3 — re-running the coverage,
corporate-action, revalidation and backtest audits — after which they would
tier on their own merits. Promoting them without it was available and was
declined.

**Steps 1 and 3 were not done.** Step 1 is an operational decision about real
securities, and the code deliberately routes it to a human. Step 3 rebuilds the
evidence that `approved`, `evidence_status` and `last_reviewed` rest on.

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


---

## Step 3 — the evidence audits, and why most of the rerun was discarded

Authorised after steps 2 and 4. The result is 229 of 241 active symbols loading,
up from 198 of 232 when this audit began — but the route there found a defect in
the audit itself.

### The rerun manufactured discrepancies — and the first explanation was wrong

Re-running `audit_eodhd_full_universe` moved `MANUAL_REVIEW` from 15 symbols to
43, and regenerating the tiers on that evidence demoted **34 working symbols** to
`TIER_D` — Elsewedy Electric, Faraz Pharma, Juhayna and Arabian Cement among
them. Every one of those then failed to load.

The cause is visible in the audit's own columns:

| | 2026-07-23 run | rerun |
| --- | --- | --- |
| SWDY `yahoo_rows` | 248 | **248** |
| SWDY `common_sessions` | 243 | **243** |
| SWDY `eodhd_rows` | 253 | 255 |
| SWDY `max_close_pct_diff` | 1.01% | **12.43%** |

The first reading of this was that Yahoo is a frozen seed that had stopped
advancing. **That was wrong.** `_yahoo_cached()` fetches live Yahoo on a
two-day cache; the row counts stayed level only because the window is a rolling
year. The real cause was found by comparing the two series print by print.

### The actual cause: the reference carries its last close forward

Over 38 sessions of SWDY, EODHD and Yahoo agree to **six decimal places on 35 of
them**. Three do not:

| Session | EODHD | Yahoo |
| --- | --- | --- |
| 2026-08-09 | 110.00 | 107.00 — 2026-08-06's close, repeated |
| 2026-08-10 | 108.00 | 107.00 — repeated again |
| 2026-08-16 | 120.90 | 107.53 — 2026-08-13's close, repeated |

Yahoo has no print for those sessions and carries the previous close forward.
EODHD is the one that is right: 107.53 on the 13th to 122.12 on the 17th passes
through EODHD's 120.90, not through a flat 107.53.

The categoriser keys on `max_close_pct_diff` — a maximum over the window, which
is maximally sensitive to a single bad print in the yardstick. In July SWDY was
flat around 90 and a carried-forward close cost 1%; by August it had rallied
30% and the same defect cost 12.4%. Nothing about EODHD changed. The audit was
blaming the subject for a gap in the reference.

**The fix**: a session where the reference repeats its own previous close *while
the subject moves* is not a measurement — an unchanged stock leaves both series
unchanged — so it is excluded from the percentage metrics and counted in a new
`reference_carry_forward_sessions` column. The comparison window is also
anchored to the end of the overlap rather than to the clock, and a window with
too few usable sessions now returns `INSUFFICIENT_OVERLAP` instead of a verdict.

Verified: SWDY, ARCC and PHAR go from `MINOR_ROUNDING_DIFFERENCE` to
`CLEAN_MATCH` with a **maximum difference of 0.00%** once 3 carried-forward
sessions are excluded from 32. Across the universe, `CLEAN_MATCH` rises from 20
to 165 and `MANUAL_REVIEW` falls from 43 to 13 — and the 13 survive scrutiny:

* `JUFO` differs by exactly **25.000%** for seven consecutive sessions, then
  stops — a 1.25 ratio, a corporate action one provider applied before the other.
* `LUTS` differs by exactly **21.05%** for eight consecutive sessions.
* `ARVA` has only 13 overlapping sessions as a newly renamed ticker and is
  correctly held as `INSUFFICIENT_OVERLAP`.

The filter removes the noise and leaves the signal.

### What was kept

Old evidence for the 225 symbols it covers — gathered while the Yahoo baseline
was current, and therefore the valid measurement. New evidence only for the 16
symbols that had none, where it is the only measurement that exists.

`corporate_action_inventory.csv` was kept from the rerun in full: it is
Yahoo-independent, covers all 241 symbols, and found 2,182 actions with zero
invalid splits. `real_backtest_summary.csv` was **not** re-run — its symbol list
is ten hardcoded tickers and its windows are frozen dates, so a rerun returns
the same rows and none of the 16 appear in it.

On the merged evidence, **two** pre-existing symbols change tier: `ACGC` and
`OCPH`, both `TIER_A → TIER_C`, because the fresh corporate-action inventory
found recent splits. Both had been failing the sector build with
`VOLUME_POLICY_UNRESOLVED`, so the more conservative tier agrees with what was
already observed.

### A third generator defect

With the queue resolution restored to its earlier state, `SEIGA` — classified
`PRICE_SCALE_ANOMALY` by the fresh audit — fell through to the final branch and
was granted `TIER_A_FORWARD_SAFE` on the reason *"no scale issue"*. A price-scale
anomaly is precisely what must not be called forward-safe. The generator now
holds an unresolved scale anomaly at `scale_anomaly_unresolved` instead.

### Where the universe stands

**229 of 241 active symbols load.** The remaining 12:

| Cause | Count | Symbols |
| --- | --- | --- |
| `VOLUME_POLICY_UNRESOLVED` | 5 | ACGC, JUFO, MTIE, NCCW, OCPH |
| `LOCAL_SEED_ONLY_STALE` | 4 | CFGH, DEIN, MEGM, TRTO |
| `LOCAL_PLUS_RUBIX_STALE` | 1 | NAHO |
| Retired ISIN twin, held | 1 | EDBM |
| Scale anomaly, held | 1 | SEIGA |

Fourteen of the sixteen are now reachable. The sector history rebuilt to **218
of 228 classified symbols** across 5,864 sessions.

Step 1 — the 16 Rubix feed mappings — remains open, and is still the operator's.
Those symbols now have daily history but no live quote overlay.

---

## The stale local seeds are not a bridge fault

Five symbols — CFGH, DEIN, MEGM, TRTO, NAHO — sat on a local seed frozen at
2026-07-22 with `bridge_appended=0`, while the collector was actively recording
all five (`VERIFIED_FEED_OBSERVED`, present in the last week's minute candles).
That looked like a bridge defect. It is not.

The chain, traced end to end:

1. `RubixDailyBuilder.build_session()` covers the universe exactly — 241 raw bars
   for 241 active symbols on 2026-08-26, with no symbol missing and none extra.
2. All five **are** processed, and all five are **rejected**, with specific
   reasons recorded in `reports/daily_bridge/<session>/rejected_bars.csv`:

   | Symbol | Rejection |
   | --- | --- |
   | DEIN, MEGM | `SYMBOL_INACTIVE` — "only 0 continuous prints (naturally inactive)" |
   | CFGH | `DATA_GAP` — continuous gap 54min against a 45min limit, no auction events |
   | NAHO | `DATA_GAP` — gap 78min |
   | TRTO | `PARTIAL_LATE_START` — first print 10:41:23 against a 10:15 limit |

3. The finalizer is refusing to write a `FINAL` daily bar from a session it did
   not fully observe. That is the safety design working, not failing.
4. Because these symbols are `TIER_D`, the router sends them down the local-seed
   + Rubix-bridge path **exclusively** — and for a thinly traded stock that path
   can never advance, because thin sessions are exactly the ones the finalizer
   rejects.

### The real finding

Every one of the 14 symbols that still fails to load is `TIER_D`, and **13 of
them have current EODHD data, to 2026-08-26, sitting unused**:

| Symbol | EODHD rows | EODHD latest |
| --- | --- | --- |
| ACGC, CFGH, DEIN, EDBM, JUFO, LUTS, MEGM, MTIE, NCCW, OCPH, SEIGA | ~396 | 2026-08-26 |
| NAHO | 343 | 2026-08-26 |
| TRTO | 285 | 2026-08-26 |
| ARVA | 328 | 2026-08-04 (newly renamed ticker) |

So there is no bridge bug to fix. There is one policy question, and it accounts
for every remaining failure: **`TIER_D` currently means "no data at all", when it
was meant to mean "not yet approved for automatic forward use".** A symbol held
for manual review and a symbol with no obtainable data are routed identically,
even when a complete, current series is available.

Whether to separate those two meanings is an operator's decision about the
routing policy, not a defect to be fixed in place.

### The separation, once authorised

`TIER_D` now distinguishes "held for manual review" from "no obtainable data",
under a **default-deny** design: `get_current_research_history(..., allow_held)`
is `False` unless a caller asks, so **no existing caller changes behaviour**. A
symbol held for review stays blocked for everything that has not opted in. That
is deliberately safer than serving held data everywhere and trusting each
consumer to check a flag.

A frame served under the opt-in carries its own warning:

```
"automatic_use_permitted": False,
"held_reason": "TIER_D held for manual review; the local-seed path cannot
                advance for this symbol, so EODHD is served for display
                and analysis only"
```

The flag is `True` on every normally-routed frame rather than merely absent, so
a consumer can test it without having to know which paths set it.

The sector history opts in, because it describes where turnover went and places
no order. Its coverage report marks those rows `LOADED_HELD` and the build
records `held_symbols` and `held_tickers`, which the dashboard's provenance
panel names on screen.

**Result: 222 of 228 classified symbols now contribute, up from 218.** Six are
held for display: ARVA, CFGH, DEIN, LUTS, NAHO, TRTO.

Six remain unavailable, and deliberately so:

| Symbols | Why the opt-in does not reach them |
| --- | --- |
| ACGC, JUFO, MTIE, NCCW, OCPH | The volume-policy gate still applies. An unresolved corporate action makes the volume untrustworthy, and turnover is price × volume — admitting them would corrupt the very numbers this history exists to report. |
| MEGM | Fewer bars than the minimum once zero-volume sessions are dropped. It is genuinely inactive. |

The volume gate was kept on the held path on purpose. Loosening the tier does
not loosen the measurements a tier was never about.
