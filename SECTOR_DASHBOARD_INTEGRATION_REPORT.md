# Sector Liquidity in the Dashboard — Phase 5

Phases 1–4 produced a database, four CLI scripts and five reports. None of it
was visible in the app. This connects it, and changes one live number.

## The page

`dashboard/sector_flow.py`, registered in the **SWING** workspace as
*Sector Liquidity* (`/sector-liquidity`). Read-only: it places no order and
produces no signal.

| Section | Shows |
| --- | --- |
| Latest complete session | Sector ranking by turnover share, with RVOL, Z, breadth, symbol count |
| Sector strength | The measurement that feeds the live Edge score |
| Rotation | Share per sector over the last 60 complete sessions, as a heatmap |
| Next session | The 5-session baseline forecast, model beside it |
| Rest of today | The intraday blend, when the opening window was observed |
| Data provenance | Universe source, provider mix, sessions kept and excluded |

Three things the page states rather than hides:

* **the baseline sits next to the forecast.** The model column carries the
  walk-forward verdict inline — +0.249 against persistence but +0.013 against
  the 5-session mean, below the usable threshold — so nobody reads the model
  column as the recommendation.
* **excluded sessions are counted.** The provenance panel names how many stored
  sessions failed the coverage guard and why they are overwhelmingly Sundays.
* **turnover is a proxy**, not the exchange's value-traded figure.

## The live change: sector strength

`decision_support.sector_analysis.sector_summary` derived a sector's strength
from the average Edge and Momentum of the scanned symbols inside it. That
strength then fed back into those same symbols' Edge scores through the
`sector_strength: 0.5` weight. The quantity was partly a function of itself: a
sector looked strong because its stocks scored well, and its stocks then scored
better because the sector looked strong.

`sector_flow/strength.py` measures it independently instead — how far the
sector's traded value exceeded **its own** trailing 20-session median (the RVOL
the daily history already computes), rescaled onto [0, 1]:

| RVOL | Strength |
| --- | --- |
| 0.00× | 0.00 |
| 1.00× (a normal session) | 0.50 |
| 2.00× or more | 1.00 |

Nothing in the scan can influence traded value, so the circularity is gone.

**The fallback is deliberate.** A sector with no complete-session measurement
keeps the older derived value rather than losing the factor. Dropping it would
silently change what an Edge score means for some rows and not others. Each row
now carries `SectorStrengthSource` — `LIQUIDITY` or `SCAN_DERIVED` — so which
was used is answerable.

An absent or unreadable `sector_flow.db` returns an empty mapping rather than
raising. Sector evidence is advisory; its absence must never stop a scan from
producing rows.

### What it changes in practice

On 2026-08-26 the measured strengths spread from 0.19 to 0.81, and the ordering
differs from turnover share — Banks lead on strength (RVOL 1.62×) while ranking
third on share, and Health Care & Pharmaceuticals hold 7.4% of turnover on the
weakest strength on the board (RVOL 0.39×). A sector can be large and quiet, or
small and busy; the previous measure could not tell those apart.

`load_latest_strength()` reads only the newest complete session — 31 ms — rather
than the 11 MB history, because the scan path calls it on every run.

## Verification

* `tests/test_sector_strength.py` — 16 tests: the RVOL→strength scale at its
  endpoints, measurement taken from the latest *complete* session, preference
  over the derived value, fallback when a sector is unmeasured, and no raise on
  a missing database or missing table.
* Page verified in a running dashboard: five sections, five tables, zero
  exceptions, live intraday forecast present with 30 opening minutes observed.
* `tests/test_decision_support.py` — 17 tests still pass.

## Running it

```bash
venv/Scripts/python.exe -m streamlit run app.py
```

The page needs `data/sector_flow.db`; without it the page explains how to build
it and the Edge score falls back to the derived value.

## What this is not

Sector strength describes where money is moving, not whether prices will rise.
A sector can lead turnover while falling.
