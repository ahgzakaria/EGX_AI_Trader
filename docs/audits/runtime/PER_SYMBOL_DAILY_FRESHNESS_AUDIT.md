# Per-Symbol Daily Data Freshness — Audit

Why a single global date was misleading, why blocking the whole scan was too
coarse, and what replaced both.

**Research only.** No indicator, score, reward/risk requirement or
BUY/WATCH/AVOID rule changed.

---

## 1. The EODHD partial-coverage finding

On 2026-08-04 the Daily Dashboard headlined **"Latest completed candle
2026-08-03"** above a table of Thursday **2026-07-30** closes.

A bounded, authenticated, **no-cache** EODHD probe (token redacted, nothing
written to any cache) settled it:

| Symbol | Verdict | Dates returned |
|---|---|---|
| SPIN, ORHD, ACAP, ABUK | `RAW_STALE` | 07-28, 07-29, **07-30** — no 08-02, no 08-03 |
| QNBE, CIRA | `RAW_CURRENT` | …07-30, 08-02, **08-03** |

**EODHD's EGX coverage was partial**, symbol by symbol. Nothing had been
relabelled: for each symbol the raw API response, the raw cache file and the
scan row carried the same date *and* the same OHLCV — SPIN c=15.64,
ORHD c=39.31, ACAP c=8.88, ABUK c=73.00.

Observed distribution over the 194 analysed rows of `RUN_20260804_005327`:

| Session | Rows |
|---|---:|
| 2026-08-03 | 6 |
| 2026-07-30 | **184** |
| 2026-07-27 | 1 |
| 2026-07-09 | 1 |
| 2026-07-06 | 1 |
| 2014-03-03 | 1 |

## 2. Why the global maximum was misleading

The banner published `max(session_dates)`. Six current rows therefore named the
session for a table that was 184/194 one session behind. The aggregate was
false even though every individual row was honest — a headline is a claim about
what is underneath it.

## 3. Why global blocking was too coarse

The first fix blocked the whole scan whenever sessions were mixed. That is
safe, and it was the right immediate move, but it throws away information: on
that morning **six symbols were genuinely current** and could legitimately have
been analysed. Blocking them protects nothing and hides real opportunity.

Freshness is not a property of the run. It is a property of each symbol,
because the provider publishes per symbol.

## 4. The per-symbol eligibility contract

`core/daily_data_guard.py` classifies each symbol from **its own final accepted
candle row**:

| Status | Meaning | Eligible? | Scan outcome |
|---|---|---|---|
| `CURRENT` | actual == expected completed session | **yes** | `SUCCESS_CURRENT` |
| `STALE` | actual < expected | no | `SKIPPED_STALE_DAILY_DATA` |
| `MISSING_DATE` | no date on the accepted history | no | `SKIPPED_MISSING_DAILY_DATE` |
| `INVALID_DATE` | date present, uninterpretable | no | `SKIPPED_MISSING_DAILY_DATE` |
| `FUTURE_DATE` | actual > expected — provenance error | no | `SKIPPED_FUTURE_DAILY_DATE` |
| `SESSION_MISMATCH` | no authoritative expectation available | no | `SKIPPED_STALE_DAILY_DATA` |
| `INSUFFICIENT_HISTORY` | too few bars, independent of date | no | `INSUFFICIENT_HISTORY` |

Each result carries: symbol, expected session, actual session,
**trading-sessions behind**, status, eligibility, source provider, source mode,
candle identity, exclusion reason and the latest OHLCV where available.

Forbidden as inputs, by construction and by test: file modification time, cache
refresh timestamp, scan run date, and the expected session standing in for the
actual one.

**Distance is counted in trading sessions, not calendar days.** EGX trades
Sunday–Thursday, so 2026-07-30 → 2026-08-03 is four calendar days but **two**
trading sessions. Calendar distance would overstate staleness every weekend.

## 5. Where the gate sits

In `core/scanner.py`, immediately after history loads and **before**
`calculate_indicators` and `decision_service.evaluate`:

```
load_history  →  freshness gate  →  [CURRENT only]  →  indicators  →  decision
                        │
                        └── otherwise: typed outcome + exclusion reason, `continue`
```

A stale symbol therefore never has a current score computed and then hidden.
Because ineligible symbols never reach the decision engine, no stale row can
appear in any decision table, ranking, opportunity card or export — the cap on
BUY/WATCH/AVOID counts is structural rather than a filter applied afterwards.

`scan_symbols(..., expected_session=...)` lets an offline or fixture-driven
scan state its own expectation. Production leaves it unset so the exchange
calendar decides. This exists because "current" means current *relative to the
data being scanned*: deriving today's session for a frame that legitimately
ends earlier would exclude everything and say nothing.

## 6. The market-wide coverage contract

Symbol-level decisions and market-level claims are different questions.

- **Symbol level** — every `CURRENT` symbol may be analysed and ranked, however
  thin overall coverage is. A current symbol is current regardless of what the
  rest of the exchange is doing.
- **Market level** — regime, breadth, "top market opportunity" and market-wide
  distributions require enough of the market to be represented.

The gate is `minimum_daily_market_coverage_percent`, **default 60%**.

It is a **data-quality** setting, not a strategy threshold: it changes no
indicator, score, reward/risk requirement or decision rule, and no symbol-level
result depends on it. The default was chosen against observed runs rather than
invented — healthy scans here analyse ~194/241 (~80%), while the incident
morning had 6/241 (2.5%) current. 60% sits comfortably below a normal run, far
above a partial-provider morning, and high enough that a market claim needs
most of the market behind it.

Below the threshold the market regime renders as
`INSUFFICIENT_CURRENT_COVERAGE` — never BULL, BEAR or SIDEWAYS — and the
operator sees:

```
MARKET-WIDE SUMMARY BLOCKED

Current daily-data coverage is 6/241 (2.5%).
Symbol-level results are available only for current symbols.
```

## 7. The invalid run stays invalid

`RUN_20260804_005327` is preserved byte-for-byte and keeps its
`INVALID_DATA_PROVENANCE.md` marker.

Its **individual source rows were date-honest** — each row's date matched its
own OHLCV at every layer. What was not honest was the **run-level decision
product**: stale symbols were counted in the decision distribution and a
market-level headline was drawn from a mixed set. Six current rows do not make
the run valid, and it is not retroactively re-blessed.

## 8. Limits and remaining provider risk

- **Partial coverage can recur at any time.** The gate makes it visible and
  safe; it cannot make EODHD publish faster.
- **A whole-exchange lag looks like near-zero coverage.** That is correct
  behaviour — symbol-level results simply become few — but it means a normal
  pre-publication morning will show a blocked market summary until the provider
  catches up.
- **The expected session comes from the exchange calendar.** A wrong holiday
  entry would mis-set the expectation for every symbol at once. The calendar is
  fail-closed: without it, nothing is called current.
- **Freshness is judged on the daily candle only.** Intraday and live-quote
  freshness are separate questions handled elsewhere.
- The gate does not attempt to repair or backfill data. It decides eligibility.
- **Direct analysis views** (Stock Details, Watchlist, AI Analysis) now apply
  the same per-symbol contract through `services/analysis_freshness_service.py`
  rather than reimplementing it - see
  `ANALYSIS_VIEWS_FRESHNESS_INTEGRATION_VALIDATION.md`. The split
  current-vs-audit CSV export is implemented as schema v2 - see
  `DAILY_SCAN_EXPORT_SCHEMA_VALIDATION.md`; the archive-reading surfaces
  (Run History, Compare Runs, Dashboard export buttons) are not yet wired to
  the new files.
