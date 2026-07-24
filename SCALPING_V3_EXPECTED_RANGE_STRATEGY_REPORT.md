# SCALPING V3 — Historical Volatility, High-Volume & Expected-Range Strategy

**Strategy:** `EXPECTED_RANGE_SCALPER` · **Status:** DECISION-SUPPORT / PAPER-ONLY ·
**Long-only** · **DISABLED by default** (`production_enabled=false`,
`paper_enabled=false`, `decision_support_only=true`) · fixed **+2% target / -2% stop**.

This strategy corrects the previous Range Scalper's misunderstanding: stock
selection no longer depends on rebuilding a complete intraday one-minute range.
Selection is **liquidity-first**, computed **before** the session from completed
daily history; live Rubix quotes are used **only** to locate price inside the
pre-computed expected range and to display long-entry scenarios. It is built as a
**separate, isolated package** (`scalping_expected_range/`) beside — and never
touching — every existing strategy.

All figures below come from the real EGX universe (265 symbols) run by
`scripts/run_expected_range_scalper.py`. Historical daily data on the run date was
one completed session behind (latest `2026-07-20`, expected `2026-07-21`), so most
symbols are correctly flagged **DATA_STALE** — the last calculated range is
preserved with a visible warning, never converted to AVOID.

---

## Architecture (isolated package)

```
scalping_expected_range/
  config.py            ExpectedRangeConfig (reads settings.json; flags off)
  liquidity_model.py   Phase 1 — avg/median Volume & Turnover, consistency, trend,
                       stability, and the LIQUIDITY HARD GATE
  volatility_model.py  Phase 2 — ADR%, median range%, ATR%, excursions,
                       range-consistency, 2% frequency, spike/stability classes
  expected_range.py    Phase 3 — conservative/base/high-vol asymmetric bands +
                       optional open-adjusted band
  historical_selector.py  provider-agnostic completed-daily accessor + strict
                       cleaning + provenance; orchestrates the three models
  scoring.py           Phase 5 — cross-sectional percentiles, liquidity-first
                       EXPECTED_RANGE_SCALPING_SCORE, liquidity gate, default rank
  scenario_engine.py   Phase 4/6/7/8 — range position, 2% feasibility after costs,
                       7 independent long-entry scenarios, final advisory
  scanner.py           Phase 9/10/14 — universe scan, views, live monitor,
                       ImmutablePaperSignalStore
  backtest.py          Phase 12/13 — walk-forward selection validation (A) +
                       honest execution-validation scaffold (B)
  settings.json        research defaults (NOT claimed optimal)
```

Dashboard: a new **Expected Range Scalper** page in the SCALPING workspace only
(`dashboard/expected_range_scalper.py`), with the required top cards, liquidity-first
main table, sortable views, and 5 per-symbol tabs (Historical Liquidity, Historical
Volatility, Expected Range, Live Scenarios, Reasons & Warnings).

---

## Selection priority (as required)

The composite score weights liquidity **60 / 100**:

| Component | Weight |
|---|--:|
| Average Volume | 30 |
| Average Turnover | 25 |
| Historical Volatility (ADR) | 20 |
| 2% Target Frequency | 15 |
| Liquidity Consistency | 5 |
| Live Spread / Executability | 5 |

A symbol failing the **liquidity hard gate** (`LIQUIDITY_TOO_LOW`,
`VOLUME_UNRELIABLE`, `DATA_INSUFFICIENT`) is gated to score 0 and excluded from the
candidate views — volatility can never rescue it. The **default ranking is
liquidity-first** (Volume ▸ Turnover ▸ Range ▸ 2% frequency), so a highly volatile
but illiquid stock never outranks a liquid, consistently active one.

---

## The 13 required answers

### 1. Highest consistent Average Volume (20-session)
`ARAB.CA` (495.5M), `CCAP.CA` (118.4M), `OFH.CA` (79.3M), `ASPI.CA` (76.6M),
`BTFH.CA` (71.1M). Median volume is reported alongside the mean so a single
exceptional session cannot promote an illiquid name.

### 2. Highest Average Turnover (EGP, 20-session)
`CCAP.CA` (613.8M), `TMGH.CA` (385.9M), `COMI.CA` (373.6M), `ZMID.CA` (248.5M),
`PHDC.CA` (244.1M).

### 3. High liquidity **and** high daily volatility (composite)
`PRDC.CA` (score 89.8, ADR 7.14%), `AMER.CA` (88.1, 6.40%), `ELSH.CA` (88.1, 5.08%),
`ELKA.CA` (87.5, 6.38%), `ISMQ.CA` (85.9, 4.37%), `ARAB.CA` (85.0, 4.39%). See the
"Best Volume + Volatility" view / `reports/expected_range_top_candidates.csv`.

### 4. Historically provide ≥2% daily range most often
`GGCC.CA`, `ELKA.CA`, `MPCO.CA`, `PRDC.CA`, `ISMQ.CA`, `ELSH.CA` — all at ~100% of
the last 20 sessions ≥2% range. (Reported separately from **upside** ≥2% frequency.)

### 5. Expected range for every qualified stock
Three asymmetric bands per symbol (conservative p25 / base p50 / high-vol p75) from
the historical upside/downside excursion distributions off the previous completed
Close, plus width/confidence/observations/stability — see
`reports/expected_range_scalping_universe.csv`. An optional **open-adjusted** band is
added alongside (never replacing) the pre-session forecast once a reliable session
Open exists.

### 6. Valid current entry scenarios per stock
All seven long-entry scenarios are evaluated **independently** per symbol (Lower-
Range Bounce, Dip & Reclaim, Continuation, Breakout & Retest, Gap-Up-with-Room,
Gap-Down Recovery, No-Chase/Range-Consumed) with entry/±2%/invalidation/room and a
status — see `reports/expected_range_scenarios.csv` (851 rows). The system never
emits a generic BUY without a named scenario.

### 7. Candidates rejected for low liquidity
**94 of 265** symbols rejected by the liquidity hard gate (91 `LIQUIDITY_TOO_LOW`
plus 3 `DATA_INSUFFICIENT`); **171** pass as tradable candidates —
`reports/expected_range_rejections.csv`.

### 8. Does liquidity-first outperform volatility-only? (walk-forward, 30 sessions)
**Nuanced — and this is the honest, important result.**

| Group (ranked on trailing window, measured next session) | Sessions ≥2% range | Avg Volume | Avg Turnover |
|---|--:|--:|--:|
| Top-decile **volatility-only** | **97.1%** | 9.9M | 40.8M |
| Top-decile **liquidity-first (combined)** | 92.8% | **41.8M** | **146.4M** |
| All symbols | 75.3% | 10.0M | 43.0M |
| Bottom-half liquidity-first | 67.2% | 0.9M | 10.2M |

Volatility-only finds slightly more raw ≥2% ranges — **but in names with ~1/4 the
volume and turnover**, i.e. exactly the illiquid, hard-to-execute stocks the user
wants excluded. Liquidity-first gives up ~4 percentage points of raw 2% frequency to
gain **~4× the liquidity/turnover**, which is what actually governs spread, fills and
executability for a scalp. So: liquidity-first does **not** win on raw range count
(and we do not claim it does); it wins on *tradable* 2% opportunities. This validates
the user's thesis that high volatility alone is insufficient.

### 9. Does the top-ranked group provide more consistent 2% opportunities?
**Yes.** Liquidity-first **Top-10 = 95.0%** of sessions ≥2% range, Top-20 = 92.8%,
Top-30 = 91.3%, versus all-symbols 75.3% and bottom-half 67.2% — a clear, monotone
improvement with rank. `reports/expected_range_selection_validation.csv`.

### 10. Which scenario performs best?
**Not yet answerable — honestly deferred.** Per-scenario execution performance
requires genuine chronological intraday/event data (entry on a future observable
event, live Bid/Ask, no future High/Low). Only **8** intraday sessions exist in the
Rubix DB (< the 20 required), so `scenario_execution_status` returns
`DEFERRED_INSUFFICIENT_INTRADAY`. Daily bars cannot reveal the intraday order of High
and Low, so we do **not** fabricate a per-scenario win rate from them. This is
deferred to the Phase 14 paper forward test.

### 11. Did it outperform the existing fixed-2% scanner?
Cannot be claimed yet — the two are different questions. This report establishes
**candidate quality** (a liquidity-first universe that historically delivers ≥2%
ranges far more consistently than the average symbol). A head-to-head *executed*
comparison against the existing fixed-2% scanner needs the same genuine intraday
execution data as Q10 and is deferred to paper forward testing. No performance
superiority is asserted here.

### 12. Is paper forward testing recommended?
**Yes.** The pre-session selection evidence is strong and stable across 30
walk-forward sessions, and execution can only be validated forward. The immutable,
append-only paper store (`ImmutablePaperSignalStore`) is built and tested; it records
each scenario **before** the outcome is known and appends outcomes as separate rows,
never rewriting the original signal. It stays OFF until `paper_enabled` is set.

### 13. Did any existing strategy, threshold or sealed result change?
**No.** Everything new lives in `scalping_expected_range/`, a new dashboard page, a
new script, new tests and new `reports/expected_range_*.csv`. The Swing/Daily engine,
BREAKOUT_SWING, Adaptive Selector, AI/AI-ranking, the fixed-2% Scalping strategy, the
Intraday Range Scalper, the Event-Driven Data Gate, the Rubix collector and all
sealed historical results are untouched. The full regression suite (**298 tests**,
277 prior + 21 new) passes and the Streamlit app imports cleanly.

---

## Honesty & limitations

- **Daily-bar limitation** is respected everywhere: daily High-Low ≥2% is treated as
  *opportunity potential*, never as proof a 2% trade was executable.
- **Data staleness** is disclosed (`DATA_STALE`, latest vs expected session, lag) and
  the last computed range is preserved, never turned into AVOID.
- **Market-wide vs per-symbol**: liquidity/volatility are per-symbol *selection*
  metrics; the strategy does not require complete minute coverage for selection and
  does not penalise normal per-symbol inactivity.
- **Weights are research defaults**, not optimized on the dataset; validation uses
  strict walk-forward (rank on a trailing window, measure the next session).

## Deliverables
`SCALPING_V3_EXPECTED_RANGE_STRATEGY_REPORT.md` · `reports/expected_range_scalping_universe.csv` ·
`reports/expected_range_top_candidates.csv` · `reports/expected_range_scenarios.csv` ·
`reports/expected_range_rejections.csv` · `reports/expected_range_selection_validation.csv` ·
`reports/expected_range_paper_signals.csv` (header-only; paper disabled).

**The strategy is not activated. Real trading is not enabled. It stays isolated until
research and paper evidence are reviewed.**

---

## Completion-audit addendum (see EXPECTED_RANGE_COMPLETION_AUDIT.md)

A pre-paper-mode audit found and fixed three real issues (no production strategy,
weight, +2% target or -2% stop changed):

1. **Completed-session freshness (fixed).** History age was measured against the
   live-quote expected session, so a correct previous-session candle was
   mislabelled `DATA_STALE` during a forming session. New auction/holiday-aware
   logic (`classify_history_freshness`, five statuses) fixes it; **201 of 265
   symbols** were falsely `DATA_STALE` during the 07-21 session and are now `OK`.
2. **Expected-range forecast accuracy (now reported, distinct from selection).**
   Walk-forward over 25,227 symbol-sessions: bands are correctly ordered and the
   **Base p50 band is well-centered** (median High/Low error ≈ 0, ~50% one-sided
   coverage); marginally too narrow for full containment and under-forecasts in
   gap / rising-volatility regimes (open-adjusted band mitigates gaps).
3. **Volume vs tradability (ranking adjusted).** The default tradable ranking was
   pure raw volume, letting penny shares (e.g. `ARAB.CA` at 0.23 EGP) top on share
   count. Average Volume stays the largest score component (30/100), but the default
   tradable rank is now the liquidity-first Combined Score; the raw Highest-Volume
   view is kept separately. **ARAB.CA moves from #1 to #7** — still a valid
   candidate, no longer a penny-share artifact at the top.

**A/B/C semantics** are now explicit everywhere: candidate-selection success (A,
validated) ≠ forecast accuracy (B, reported) ≠ executable performance (C, deferred).
Paper mode remains **disabled** pending human review.
