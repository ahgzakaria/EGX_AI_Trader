# Intraday Sector Liquidity — Phase 4

Builds on Phases 1–3 (`SECTOR_LIQUIDITY_FLOW_REPORT.md`,
`SECTOR_LIQUIDITY_FORECAST_REPORT.md`).

Phase 3 found that next-*day* sector share is not predictable beyond a 5-session
mean. Phase 4 asks the nearer question: given the opening window, where does the
**rest of today's** turnover go?

**Answer: an even blend of the opening 30 minutes and the previous daily session
beats both of its inputs, cutting MAE 10.8% below the daily baseline.** No model
is involved. The sample is 16 sessions, so this is measured, not established.

---

## What is predicted

Each sector's share of turnover from **07:30 UTC to the close** (10:30–14:30
Africa/Cairo) — the session after the opening window.

The target excludes the opening window deliberately. The opening window is part
of the full session, so scoring an opening-based forecast against the full day
is partly circular and inflates it: the same predictor scores rank 0.933 against
full-day and 0.910 against rest-of-day.

## Results — 16 complete sessions

| Predictor | Rank corr. | Top-3 | MAE |
| --- | --- | --- | --- |
| Opening 30 minutes alone | 0.9172 | 0.7500 | 0.019867 |
| Previous daily session alone | 0.9183 | 0.8125 | 0.017549 |
| **Even blend of the two** | **0.9347** | 0.8125 | **0.015656** |

The opening window **alone is not better than yesterday** — it is worse on both
MAE and top-3. The two together beat either alone. The information is
complementary, not redundant.

### The optimum is flat

| Opening weight | Rank corr. | Top-3 | MAE |
| --- | --- | --- | --- |
| 0.0 (previous only) | 0.9183 | 0.8125 | 0.017549 |
| 0.3 | 0.9305 | **0.8333** | **0.015597** |
| **0.5 (default)** | **0.9347** | 0.8125 | 0.015656 |
| 0.7 | 0.9284 | 0.7500 | 0.016782 |
| 1.0 (opening only) | 0.9172 | 0.7500 | 0.019867 |

0.3 and 0.5 are indistinguishable on MAE (0.015597 vs 0.015656). The default
stays at **0.5 because it was chosen before the sweep was run** — picking 0.3
afterwards would be selection on the evaluation set. The flatness matters more
than the peak: the result does not sit on a knife edge.

## The session guard

The collector runs on a desktop that is not always switched on. Missing and
half-captured sessions are the normal condition, not an incident, so a session
is admitted on measured coverage:

* at least 200 of the session's ~271 minutes observed,
* still being observed at 11:25 UTC or later,
* at least 20 of the opening window's 30 minutes present.

**16 of 28 observed sessions pass.** What the guard rejects:

| Session | Why |
| --- | --- |
| 2026-07-14 … 07-30 (11 days) | Collector sampled sparsely — 87–169 session minutes, and only ~15 opening minutes |
| 2026-08-16 | Machine switched off mid-session; last minute 10:17 |
| 2026-08-17, 08-20 | Machine off; only 11:25–11:30 captured |

A first pass at this analysis used a crude `rows > 5000` filter, which admitted
2026-08-16 and the July sessions. That inflated the blend's measured advantage
to 12.6%; on properly guarded sessions it is 10.8%. The guard is the finding as
much as the blend is.

`forecast_rest_of_day()` deliberately does **not** require a complete session —
it is meant to run while the session is still open, and needs only the opening
window. It reports how many opening minutes it actually saw.

## Modules

| Module | Responsibility |
| --- | --- |
| `sector_flow/intraday.py` | Session guard, shares, blend, scoring, live forecast |
| `scripts/intraday_sector_flow.py` | CLI: coverage table, scores, weight sweep, forecast |
| `tests/test_sector_intraday.py` | 24 tests |

Turnover uses the same `(High+Low+Close)/3 × Volume` proxy as the daily history,
so intraday and daily shares mean the same thing and can be blended without a
unit mismatch.

"Yesterday" comes from the **daily** panel rather than the previous intraday
session, because the daily panel exists for every trading day — including the
ones the collector missed.

## Running it

```bash
venv/Scripts/python.exe scripts/intraday_sector_flow.py
```

## Limits

* **16 scored sessions.** Small enough that the ranking of the three predictors
  is more trustworthy than any individual number.
* Intraday history begins 2026-07-01, only accumulates forward, and cannot be
  backfilled from the daily feed. Clean sessions arrive one per trading day the
  machine is running.
* The blend is fixed, not fitted. When the sample reaches a few hundred sessions
  it becomes reasonable to ask whether a learned model beats it — measured, as
  in Phase 3, against this blend rather than against persistence.

## What this is not

Not investment advice and not a price forecast. It describes where trading value
is expected to concentrate for the remainder of the session. A sector can lead
turnover while falling.
