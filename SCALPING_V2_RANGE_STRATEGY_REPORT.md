# Scalping V2 — Volatility & Range Scanner: Report

**Status:** Machinery **built, tested, and run on real data**. Strategy **NOT
approved / disabled** (`enabled: false`, `PAPER_ONLY`).
**Decisive gate result: Rubix intraday 1-minute data is far too sparse to
support reliable range detection today — 0 of 265 symbols pass the coverage
gate.** This is exactly the scenario the task's CRITICAL DATA WARNING
anticipated. No result was fabricated; every number below comes from a real
read-only scan of the live Rubix DB and the Yahoo daily cache.
**Isolation:** No Swing/Daily, Breakout, Adaptive, AI, indicator, RR, backtest,
replay, or the preserved fixed-2% scalping strategy was modified.

---

## What was built (new, isolated files)

| Component | File | Status |
|---|---|---|
| Range config (research defaults) | `scalping/range_scalper_settings.json`, `scalping/range_config.py` | ✅ built, tested |
| Intraday data-quality audit | `reports/scalping_range_data_quality.csv` | ✅ real data |
| Volatility/liquidity universe scanner + percentiles + suitability score + hard rejections | `scalping/range_scanner.py` | ✅ real data |
| Range detector + classification (Phase 2) | `scalping/range_detector.py` | ✅ built, tested |
| Long-only entry model + no-chase + dynamic stop/3-targets + cost-adjusted net RR (Phase 3/5) | `scalping/range_entry.py` | ✅ built, tested |
| Report writer | `scalping/range_reporting.py` | ✅ built |
| Unit tests | `tests/test_scalping_range_v2.py` | ✅ 11 passed |

**Reports produced from the real scan:**
`reports/scalping_range_data_quality.csv`, `scalping_range_universe.csv`,
`scalping_range_rejections.csv`, `scalping_range_opportunities.csv`.

**The old fixed +2%/-2% scalping strategy is fully preserved** — its config
(`config/settings.json → scalping`), `scalping/config.py`, `scalping/scanner.py`,
and all its modules are untouched. V2 is a parallel workspace.

---

## The decisive data gate (real Rubix data)

Intraday coverage = valid 1-minute bars ÷ 270 session minutes (10:00–14:30 Cairo).

| Session | Symbols | Median coverage | Max coverage | % ≥ 60% (gate) |
|---|---:|---:|---:|---:|
| 2026-07-20 (latest completed) | 265 | **8.2%** | 9.3% | **0%** |
| 2026-07-19 (fuller session) | 265 | 37.0% | 45.9% | **0%** |
| 2026-07-16 (fuller session) | 265 | 35.6% | 45.6% | **0%** |

**No recent session — not even the fullest — has a single symbol reaching 60%
intraday coverage.** The best-covered symbol on the latest session had 25 valid
bars out of 270. Range detection (session high/low, opening range, VWAP,
support/resistance) cannot be trusted at 8–37% coverage: the "session high/low"
would be sampled from a fraction of the minutes and would routinely be wrong —
precisely what the task forbids ("do not report a false session High or Low from
sparse data as reliable").

Scanner summary on real data (session 2026-07-20):
```
active_symbols: 265
valid_intraday_data (coverage >= 60%): 0
data_insufficient: 265
hard_rejected: 265
ready_opportunities: 0
```

---

## Answers to the report questions

**1. How many EGX symbols have reliable intraday data?**
**0 of 265** under a conservative 60% coverage gate (median coverage 8% on the
latest session, 37% on the best recent session). All 265 are marked
`DATA_INSUFFICIENT` — never `AVOID`.

**2. Which symbols have the highest tradable volatility?**
By historical ADR% (median 20-session, from Yahoo daily) — usable as *context*
even though live intraday data is insufficient:

| Symbol | ADR% | ATR% | Turnover (EGP) | Spread% | Note |
|---|---:|---:|---:|---:|---|
| AMES.CA | 11.9 | 9.1 | 5.0M | n/a | high vol, spread unavailable |
| GTWL.CA | 10.5 | 11.5 | 13.3M | 0.39 | high vol **and** tight spread + liquid |
| RKAZ.CA | 7.1 | 7.1 | 0.32M | 0.00 | low turnover |
| PRDC.CA | 5.6 | 5.5 | 31.6M | 2.11 | liquid but wide spread |
| GIHD.CA | 5.3 | 5.8 | 9.3M | 0.19 | good vol/liquidity/spread combo |
| GGCC.CA | 5.2 | 5.8 | 6.7M | 0.23 | good combo |

High volatility alone is **not** treated as sufficient — the suitability score
gates it by liquidity, spread, and data coverage (see below). Full ranking in
`reports/scalping_range_universe.csv`.

**3. Which symbols were rejected because of low liquidity?**
**102** symbols failed the minimum-turnover filter (< 500,000 EGP session
turnover). Full list in `reports/scalping_range_rejections.csv` (filter on
`LOW_TURNOVER`).

**4. Which symbols were rejected because of wide spreads?**
**173** symbols had a bid/ask spread above 0.6%. Universe-wide the spread median
is **1.03%** and only **70 of 265** symbols quote at ≤ 0.6% — EGX spreads are a
material obstacle for intraday scalping independent of the coverage problem.

**5. Which symbols have clean ranges / ready opportunities?**
**None today.** With 0 data-sufficient symbols, the range detector and entry
model produced **0 `RANGE_BUY_READY`** opportunities; all 265 resolve to
`DATA_INSUFFICIENT`. The classification/entry logic is proven correct on
synthetic bars (11 passing unit tests covering CLEAN_RANGE, TRENDING_UP,
lower-range bounce, no-chase rule, cost-adjusted net RR, and no-look-ahead), so
it will produce real classifications the moment coverage is sufficient.

**Additional rejection tallies (real):** `SPARSE_COVERAGE` 253, `SPREAD_TOO_WIDE`
173, `LOW_TURNOVER` 102, `TOO_FEW_UPDATES` 44, `INTRADAY_VALID_CANDLES_MISSING`
12, `INVALID_PRICE` 12. (`STALE_QUOTE` fired for all 265 because this scan ran
**after** the session close — quotes were hours old; that specific filter is a
timing artifact of off-session scanning, not a data-quality defect, and would
not fire during a live session.)

---

## Suitability scoring (built, explainable)

`SCALPING_SUITABILITY_SCORE` (0–100) is a weighted blend of separate,
individually-reported components — Volatility, Session Range, Liquidity,
Turnover, Spread, Data Coverage, Quote Activity — **multiplied by a data-quality
gate** so a sparse-data symbol can never score high on volatility alone. On
current data every score collapses toward 0 via the data gate, which is the
correct behavior. Components are all in `reports/scalping_range_universe.csv`.

---

## What is deliberately NOT built yet (honest scope)

Because the data gate fails for every symbol, the following would have no valid
input today and are **not** built in this pass (building them now would either
produce empty/fabricated output or require denser data first):

- **Phase 8 — intraday range backtest.** A no-look-ahead backtest needs dense
  minute bars to reconstruct the evolving session range; at 8–37% coverage there
  are no reliable ranges to test against. Deferred until coverage improves.
- **Phase 9 — live paper forward-test recorder.** Requires a live session with
  sufficient coverage to record real pre-outcome opportunities. Deferred.
- **Phase 6 — RANGE SCALPER dashboard page.** Would currently render "0
  opportunities, 265 DATA_INSUFFICIENT". Deferred until there is something real
  to show. The existing scalping dashboard (`dashboard/scalping.py`) is untouched.

These are the honest gating decisions, not silent omissions.

---

## Path to approval

The strategy **cannot be approved** until intraday data density is proven during
real sessions. Concretely:

1. **Densify Rubix 1-minute capture** via live forward collection (the collector
   must record far more of the 270 session minutes per symbol — target ≥ 60%,
   ideally ≥ 80%).
2. **Re-run** `reports/scalping_range_data_quality.csv` and confirm a meaningful
   set of symbols clears the coverage gate.
3. Only then build Phase 8 (backtest), Phase 9 (forward test), and the Phase 6
   dashboard, and evaluate real opportunities.
4. Even with dense data, EGX spreads (median ~1%) will keep the tradable
   universe small — expect a short list (e.g. GTWL, GIHD, GGCC-type names that
   combine volatility, liquidity, and tight spread), not the whole market.

Until then: **disabled, paper-only, decision-support research.** No production
scalping behavior changed; the frozen Swing/Daily, Breakout, Adaptive, and
fixed-2% scalping paths are byte-for-byte unchanged.
