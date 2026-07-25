# Expected Range Scalper — Completion Audit (before paper mode)

**Verdict: three real defects found and fixed; strategy is materially more correct
but paper mode stays OFF pending your review.** No production strategy, weight,
fixed +2% target or -2% stop was changed. Flags remain
`production_enabled=false`, `paper_enabled=false`, `decision_support_only=true`.
Full regression: all tests pass.

Scope covered: (1) completed-session freshness, (2) expected-range forecast
calibration, (3) raw Volume vs true tradability, plus score sensitivity, worked
examples and report semantics.

---

## Phase 1 — completed-session freshness (FIXED)

**Defect:** the selector measured history age against the *live-quote* expected
session (`expected_latest_session_date`, which returns **today** while the market
is open). During the 2026-07-21 session, data ending 2026-07-20 was therefore
mislabelled `DATA_STALE`, even though 07-20 is the correct latest **completed**
daily session until 07-21's session and auction finish and the provider publishes
the candle.

**Fix:** new auction-aware, holiday-aware calendar logic in
[core/egx_session.py](core/egx_session.py):
`expected_latest_completed_session(now, provider_finalized, holidays)`,
`last_completed_exchange_session(...)` and `classify_history_freshness(...)`,
returning five explicit statuses — `HISTORY_CURRENT`,
`TODAY_CANDLE_NOT_YET_COMPLETE`, `PROVIDER_FINALIZATION_PENDING`, `HISTORY_STALE`,
`HISTORY_UNAVAILABLE` — with EGX phases continuous 10:00–14:15, auction 14:15–14:25,
Fri/Sat + configured holidays non-trading. The selector
([historical_selector.py](scalping_expected_range/historical_selector.py)) now
treats CURRENT / TODAY_NOT_COMPLETE / PROVIDER_PENDING as usable (not stale) and
flags only genuinely lagging history as `DATA_STALE`. This is disclosure-only and
does not touch the Swing/Daily provider path (`trading_session_lag` is unchanged).

**Effect (measured, anchored to 2026-07-21 11:00 Cairo):** of 265 symbols,
**201 flip from false `DATA_STALE` to `OK` / `TODAY_CANDLE_NOT_YET_COMPLETE`**; only
10 are genuinely stale (data older than 07-20), plus 51 MISSING and 3
DATA_INSUFFICIENT. Tests cover all nine required contexts (pre-open, continuous,
auction, post-14:25 with/without a finalized candle, Sunday-after-Thursday, Friday,
Saturday, configured holiday) — `tests/test_expected_range_freshness.py`.

## Phase 2 — expected-range forecast calibration (REPORTED)

Strict walk-forward (forecast frozen from prior sessions only), **25,227
symbol-session observations** across 40 test sessions
(`reports/expected_range_forecast_calibration.csv`,
`reports/expected_range_forecast_by_regime.csv`).

| Band | Full-range coverage | High coverage | Low coverage | Median High err | Median Low err | Avg pred width | Avg actual width |
|---|--:|--:|--:|--:|--:|--:|--:|
| Conservative (p25) | 2.05% | 29.3% | 30.6% | −0.64% | +0.67% | 1.54% | 3.68% |
| **Base (p50)** | 14.66% | **50.4%** | **50.8%** | **0.00%** | −0.01% | 3.11% | 3.68% |
| High-Vol (p75) | 47.56% | 72.4% | 72.6% | +1.01% | −0.94% | 5.51% | 3.68% |

**The bands are calibrated and clearly distinct**, ordered exactly as intended
(conservative narrowest/lowest coverage → high-vol widest/highest). One-sided
coverage matches the percentile construction almost perfectly: the p50 Base band
covers ~50% of Highs and Lows, the p75 band ~72%. Full-range (both bounds at once)
containment is naturally lower (Base 14.7%, High-Vol 47.6%).

Regime breakdown (Base band): well-behaved and honest —
- **Gap sessions breach the pre-session band on the gap side**: GAP_UP → High
  coverage 11.8% / Low 85.3%; GAP_DOWN → High 80.2% / Low 17.8%. Expected, since
  the band is built off the *previous* Close — this is exactly why the
  **open-adjusted** recalibration exists (added alongside, never replacing, the
  pre-session band).
- **Rising-volatility periods under-forecast** (full-range 5.3%, actual width
  5.03% vs predicted 2.91%); declining-vol over-covers (23.2%). Percentile bands
  trail regime shifts — a documented limitation, not a blocker.

## Phase 3 — target-room validation (REPORTED, logical)

Opportunity-potential only (daily bars never prove execution). Fraction with ≥2%
room to the Base Expected High, by predeclared entry location:

| Entry location | ≥2% room to Exp High | Target exceeds range | RANGE_CONSUMED |
|---|--:|--:|--:|
| Lower zone | 79.0% | 21.0% | 21.0% |
| Midpoint | 16.0% | 84.0% | 84.0% |
| Upper-middle | 1.8% | 98.2% | 98.2% |
| Upper zone | 0.0% | 100% | 100% |

Monotone and correct — the higher the entry inside the range, the less room and
the more `RANGE_CONSUMED` / `TARGET_ROOM_INSUFFICIENT`. NO_CHASE behaves logically.

## Phase 4 — Volume vs tradability (AUDITED; ranking adjusted)

`reports/expected_range_volume_tradability_audit.csv` reports, per candidate, avg/
median Volume & Turnover, consistency, **approximate average traded price**, spread,
low-volume-session count, and **single-session contribution** to both Volume and
Turnover, with five separate ranks (Raw Volume, Turnover, Volume Consistency,
Tradability, Combined Scalping).

**Finding:** the previous **default tradable ranking was pure raw-Average-Volume**,
which let extremely low-priced penny shares top the list on share count alone.
`ARAB.CA` — 495.5M avg shares but **0.23 EGP average traded price** — sat at #1
purely on that. Average Volume remains the **largest single score component (30/100,
unchanged)**, but the **default tradable ranking now uses the liquidity-first
Combined Scalping Score** (Turnover 25 + consistency temper the volume component),
and the dedicated **Highest-Average-Volume view is preserved separately** for the
user who wants the raw list. This is the "distinguish the volume view from the final
tradable ranking" the audit required — no weight, target or stop changed.

## Phase 5 — score sensitivity (AUDITED; no weights changed)

`reports/expected_range_score_sensitivity.csv`:

| Check | Result |
|---|---|
| Liquidity-gated symbols rescued by volatility | **0** (volatility can never rescue failing liquidity) |
| Top-10 (by score) with below-median turnover | **0** |
| Top-20 resting on a single dominant session (>40% window volume) | **0** |
| Median symbol enters Top-10 after a 5× raw-volume spike | **No** (robust to one exceptional session) |
| Top-10 score concentration | 8.8% of total (not over-concentrated) |
| Score-Top10 vs raw-Volume-Top10 overlap | 1 symbol (score and raw volume rank very differently) |

The weighting is structurally sound; no change recommended at this time.

## Phase 6 — worked examples (`reports/expected_range_worked_examples.csv`)

| Role | Symbol | Avg Vol | Avg Turnover | Avg Price | ADR% | Status | Score | Tradable |
|---|---|--:|--:|--:|--:|---|--:|:--:|
| Highest raw volume | ARAB.CA | 495.5M | 115.6M | **0.23** | 4.39 | LIQUIDITY_VALID | 85.1 | Yes (**rank 7**) |
| Highest turnover | CCAP.CA | 118.4M | 613.8M | 5.18 | 2.93 | LIQUIDITY_VALID | 80 | Yes |
| Highest combined score | PRDC.CA | 14.4M | 123.0M | 8.54 | 7.14 | LIQUIDITY_VALID | 90 | Yes (**rank 1**) |
| High-vol but rejected | SEIG.CA | 91.9k | 22.7M | 247 | 6.63 | LIQUIDITY_TOO_LOW | 0 | No |
| Liquid but low-vol | OIH.CA | 53.0M | 75.2M | 1.42 | 1.32 | LIQUIDITY_VALID | 58.7 | Yes |

**ARAB.CA specifically:** it passes the liquidity hard gate (115.6M average
turnover, ADR 4.39%) and is a genuine tradable candidate — but its former #1 rank
was a **penny-share artifact** (0.23 EGP × huge share count). Under the corrected
score-first tradable ranking it is **#7**, behind better-balanced names
(PRDC/AMER/ELSH/ELKA/ISMQ/GGCC). It is neither wrongly rejected nor wrongly crowned.

## Phase 7 — report semantics

All reports and the dashboard now separate: **A. Candidate-selection success** (a
completed daily range ≥2% — validated), **B. Expected-range forecast accuracy** (High/
Low inside the band — reported here, not implied by A), and **C. Executable scenario
performance** (needs intraday/event data — deferred). The dashboard carries an
explicit "what this page does and does NOT claim" panel.

---

## Final questions

1. **Was 2026-07-20 correctly current during the 2026-07-21 session?** **Yes** —
   now classified `HISTORY_CURRENT` / `TODAY_CANDLE_NOT_YET_COMPLETE`, not stale.
2. **How many symbols were falsely labelled DATA_STALE?** **201** (of 265) during
   the 07-21 session under the old logic; now `OK`. Only 10 are genuinely stale.
3. **Coverage rate of each band?** Conservative 2.05% full / 29–31% one-sided;
   Base 14.66% full / ~50% one-sided; High-Vol 47.56% full / ~72% one-sided.
4. **Is the Base range well calibrated?** **Yes** — median High/Low error ≈ 0.00%
   and ~50% one-sided coverage exactly match its p50 construction.
5. **Systematically too wide or too narrow?** Marginally **too narrow** for full
   containment (avg predicted width 3.11% vs actual 3.68%); it also under-forecasts
   in rising-volatility and gap sessions. Not biased directionally.
6. **Does liquidity-first still perform strongly?** **Yes** — Top-10 delivered a
   ≥2% range in 95.0% of walk-forward sessions vs 75.3% market-wide, at ~4× the
   turnover of the volatility-only decile.
7. **Does raw Volume unfairly dominate low-priced shares?** It **did** in the old
   default ranking; **fixed** — the tradable default is now score-first and the raw
   Highest-Volume view is separate. Penny shares are flagged in the tradability audit.
8. **Does ARAB.CA remain a top tradable candidate after the full hard gate?**
   **Yes, but no longer #1** — it passes the gate and ranks #7 by the corrected
   liquidity-first score; its previous #1 was a penny-share artifact.
9. **Are candidate-selection success and range-forecast accuracy clearly
   separated?** **Yes** — Phase 7 relabelled all reports and the dashboard (A/B/C).
10. **Is paper recording ready to enable?** **Not yet** — freshness is fixed,
    calibration is reported, liquidity/tradability audit passes and no look-ahead
    was found; execution validation stays deferred (only 8 intraday sessions). Enable
    paper only after you review this audit. Flags remain OFF.
11. **Did any frozen strategy, target or stop change?** **No.** Only the isolated
    `scalping_expected_range/` package, its reports, dashboard page and tests changed.

## Paper-mode gate status

| Precondition | Status |
|---|---|
| Completed-session freshness correct | ✅ fixed + tested |
| Base Expected Range calibration reported | ✅ 25,227 obs |
| Liquidity ranking audit passes | ✅ score-first default + tradability audit |
| No look-ahead | ✅ walk-forward, forecast frozen before each session |
| Live quote / scenario timestamps reliable | ✅ quote-age + freshness disclosed |
| All regression tests pass | ✅ |
| Execution-performance validation | ⏸ deferred (insufficient intraday sessions) — no win rates fabricated |

**Recommendation:** proceed to paper forward testing **after human review**; keep
`paper_enabled=false` and `production_enabled=false` until then.
