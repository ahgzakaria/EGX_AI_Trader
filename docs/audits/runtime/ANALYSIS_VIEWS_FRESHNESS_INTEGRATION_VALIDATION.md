# Analysis Views — Freshness Integration Validation

Evidence that Stock Details, Watchlist and AI Analysis now apply the shared
freshness contracts, and that a stale symbol reaches none of them as current.

**No production scan was run, no AI request was made, no collector process was
touched, no production database was written, and the archived run was not
modified.**

---

## 1. Shared adapter

`services/analysis_freshness_service.py` composes the two existing contracts —
`core/daily_data_guard` and `core/rubix_quote_freshness` — into one
`AnalysisFreshnessContext`. No page reimplements a freshness rule; each asks the
adapter and renders the answer.

It is pure: the evaluation instant is injected, and it reads no Streamlit state,
clock, database or network, and calls neither the strategy nor the AI engine.

Its one addition is `cache_identity`, a fingerprint over symbol, **actual candle
session**, expected session, daily status, source identity, decision price
source, Rubix status, Rubix market timestamp, config identity and AI mode. That
is what closes the leak the audit found in all three views, where caches were
keyed on the symbol alone.

## 2. Stock Details

| Symbol | Candle | Result |
|---|---|---|
| `QNBE.CA` | 2026-08-03 | current analysis allowed; decision tabs built (**`tabs_called == 1`**) |
| `SPIN.CA` | 2026-07-30 | current analysis blocked; **`tabs_called == 0`** |

The blocking panel *replaces* the decision surface rather than accompanying it —
leaving a stale BUY card beside a warning is how an operator reads the card and
ignores the warning. Verified behaviourally by driving the page with a
Streamlit stand-in and asserting the tabs are never built.

Stale symbols render:

```
DAILY DATA STALE FOR THIS SYMBOL

Expected completed session: 2026-08-03
Actual latest candle: 2026-07-30
Trading sessions behind: 2
Freshness status: STALE

This symbol was not analyzed as a current opportunity.
```

followed by an explicitly labelled `HISTORICAL SNAPSHOT` section. A stale symbol
does not block a different current symbol, and an unavailable calendar never
unblocks anything.

## 3. Watchlist

Two sections replace one table:

- **Current Opportunities** — only symbols with current daily data. Badge counts
  are computed from those rows, so a withheld symbol cannot inflate them.
- **Data Update Required** — every tracked symbol the scanner excluded, with
  expected session, actual session, freshness status, sessions behind, decision
  status and exclusion reason.

Stale symbols are **retained**, never auto-removed, and show
`STALE DATA — DECISION WITHHELD` instead of a BUY/WATCH/AVOID badge. The section
states plainly that any previous decision is historical and NOT CURRENT.

This fixes a real regression introduced by the scanner gate: because stale
symbols now produce no row at all, they had been vanishing from the user's own
watchlist.

## 4. AI Analysis

| Symbol | Candle | Engine invocations |
|---|---|---|
| `QNBE.CA` | 2026-08-03 | **1** |
| `SPIN.CA` | 2026-07-30 | **0** |
| any symbol | missing date | **0** |
| any symbol | invalid date | **0** |
| any symbol | future date | **0** |

The gate runs before the runner, so a blocked symbol never spends an external
request only to be refused afterwards. Blocked symbols return:

```
AI CURRENT ANALYSIS BLOCKED

Expected completed session: 2026-08-03
Actual latest candle: 2026-07-30
Daily freshness: STALE

AI analysis was not invoked because the symbol does not have current daily data.
```

`_discard_outdated_analysis` compares the stored freshness identity rather than
the symbol, so an analysis produced from 2026-07-30 data can no longer be
restored under a 2026-08-03 heading.

## 5. Rubix display and decision price

The typed taxonomy is used verbatim in all three views. A denied overlay renders:

```
Decision price: EODHD daily close
Rubix overlay: not applied — <reason>
```

Display price and decision price are reported separately throughout. A
current-session closing quote reads `RUBIX CURRENT SESSION LAST — NOT LIVE`; a
previous-session quote names its session and states the EODHD close is retained.

A structural test asserts no runtime literal in the new modules is a bare
`FRESH`, `LIVE` or `UPDATED`.

## 6. Mutation checks — all eight bite

| Reverted protection | Result |
|---|---|
| Stock Details daily gate bypassed | 1 failed |
| AI engine called before freshness classification | 5 failed |
| cached AI identity omits the candle session | 1 failed |
| Rubix previous-session treated as live | 1 failed |
| denied overlay enters decision inputs | 4 failed |
| stale symbol may still invoke the AI engine | 5 failed |
| historical snapshot loses its NOT CURRENT label | 1 failed |
| watchlist shows a stale persisted badge | 1 failed |

Two of these initially slipped through because the tests were structural where
they needed to be behavioural: disabling the Stock Details guard left both
source statements in place, and dropping the candle session from the cache
identity was masked because a changed session usually changes the status too.
Both are now covered — one by driving the page and counting tab construction,
the other by comparing two *different stale* sessions that share a status.

## 7. Test totals

| Suite | Result |
|---|---|
| `test_analysis_views_freshness.py` | **51 passed** |
| `test_rubix_quote_freshness.py` | 54 passed |
| `test_per_symbol_daily_freshness.py` | 32 passed |
| Full suite | see final report |

Every evaluation instant is injected; a structural test asserts no test in the
file reads the wall clock.

## 8. Remaining limitations

- **No live UI screenshot proof.** The pages were driven with a Streamlit
  stand-in, not a browser.
- **The scan export schema is now v2.** `scan_results.csv` is an explicit
  compatibility alias of `scan_current_decisions.csv`, with a full
  `scan_coverage_audit.csv` alongside it - see
  `DAILY_SCAN_EXPORT_SCHEMA_VALIDATION.md`. Run History, Compare Runs and the
  Dashboard export buttons still read the archive as before.
- **Stock Details renders rows produced elsewhere.** It re-checks freshness
  before rendering, but it does not recompute a decision; a row that never
  existed cannot be shown at all.
- Historical viewing is always permitted — it is the *labelling* that is
  enforced, never the availability.
