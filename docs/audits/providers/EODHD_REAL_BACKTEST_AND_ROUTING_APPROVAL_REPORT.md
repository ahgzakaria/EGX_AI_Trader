# EODHD Phase 3.1 — Real Backtest, Corporate-Action Reconciliation & Approval Tiers

**Date:** 2026-07-23 · Mode `EODHD_SHADOW` · **Active historical provider: Yahoo
(unchanged)** · **Rubix live (unchanged)** · production disabled · routing **inactive**
(4 tiers, all `approved:false`). No engine/strategy/indicator/threshold/TP-SL change, no
`Close`↔`Adj Close` swap, no provider switch, no frozen-history change, no raw-data
mutation. **561 tests pass.**

New: `core/history_frame_adapter.py`, `tests/fixtures/history_frame_contract.json`,
`dashboard/eodhd_migration_review.py` (read-only UI), `data/eodhd/
historical_symbol_routing_review.json` (inactive tiers). Reports under `reports/eodhd/`:
real_backtest_summary(+trade_differences), corporate_action_reconciliation,
volume_adjustment_event_validation, eodhd_correct_revalidation,
forward_only_routing_simulation, routing_tier_summary.

## The 16 questions

**1. Did the actual frozen backtest engine run with both providers?**
**Yes — genuinely, this time.** The earlier failure was fixed with `history_frame_adapter`,
which reproduces the exact `load_history` contract (DatetimeIndex 'Date' tz-naive, float64
`[Open,High,Low,Close,Adj Close,Volume]`, no NaN, `attrs['market_data']`). The real,
unmodified `BacktestEngine` ran for 10 symbols × 3 windows on both Yahoo and
EODHD-split-adjusted history (0 `ENGINE_CONTRACT_FAILURE`). *(The remaining fix was
passing `start/end` as Timestamps, not strings.)*

**2. Which symbols produced identical signals and trades?** IDENTICAL: **FWRY 1y & 3y,
EAST 1y, SKPC 1y** (4 runs).

**3. Which produced only rounding differences?** ROUNDING_ONLY: **SKPC 3y** (1 run).

**4. Which produced material strategy/backtest changes?**
CORPORATE_ACTION_DIFFERENCE 5, BAR_COVERAGE_DIFFERENCE 2, **MATERIAL_SIGNAL_CHANGE 5**
(SWDY/TMGH/COMI/EFIH), **MATERIAL_BACKTEST_CHANGE 10** (mostly full-history + several 3y).
**So most symbol/windows change** — the crux finding below.

**5. What caused the COMI/EAST/SKPC/EFIH residuals?** Two distinct causes:
- **Bar-coverage difference (dominant):** EODHD carries **more bars than Yahoo** —
  e.g. SWDY 1y: 256 vs 247; full: 3610 vs 2316. Spot-check: **all 10 EODHD-extra dates
  are real EGX trading days Yahoo dropped** (0 weekends/holidays). EODHD is *more
  complete*; the extra bars shift indicator warmup and signals → different trades.
- **Corporate-action classification difference:** for most split events **EODHD raw price
  drops by the ratio (true raw) while Yahoo's raw `Close` does not (Yahoo pre-adjusted)**
  → `SPLIT_PROVIDER_DIFF` (22 events). Several EODHD "splits" are actually bonus
  issues/capital increases where price didn't drop proportionally
  (STOCK_DIVIDEND_BONUS 4, CAPITAL_INCREASE_OR_BONUS 6). Forcing all of them into the
  split engine over-adjusts vs Yahoo — so the engine must be **event-specific**, not
  blanket.

**6. Is the volume-adjustment rule universal or event-specific?**
**Event-specific.** Across 45 events: **13 MULTIPLY_BY_FACTOR** (true splits),
**26 EVENT_SPECIFIC**, **6 KEEP_RAW** (bonuses). A single universal `× split factor` rule
is **not** supported by evidence.

**7. How many of the 14 symbols have full historical confirmation?**
**Only 1 (PRDC = EODHD_HISTORY_CONFIRMED).** 6 are **CURRENT_SCALE_ONLY_CONFIRMED**
(recent matches, deep history diverges), **6 are ADJUSTMENT_DIFFERENCE** with large
deep-history gaps (KZPC mean 2096%, UNIP 5089% — penny stocks, huge cumulative
splits/bonuses), 1 remains MANUAL_REVIEW. The earlier "14 EODHD_CORRECT" was **current
scale only**, exactly as cautioned — not full-history correctness.

**8. Which symbols are Tier A?** **108** TIER_A_FORWARD_SAFE (recent prices validated, no
recent split, no scale issue; historical backtests still stay on Yahoo).

**9. Which are Tier B with no fallback?** **28** TIER_B_FORWARD_EODHD_NO_FALLBACK — **ORAS**
+ **27 Yahoo-missing** symbols. On EODHD failure they return **DATA_UNAVAILABLE**, never
Yahoo.

**10. Which remain historical-review only?** **73** TIER_C_HISTORICAL_REVIEW — symbols with
recent splits / deep-history / bar-coverage differences; forward-shadow only, **must not
replace frozen backtest history**.

**11. Which remain unsupported/manual?** **56** TIER_D — 40 EODHD-unsupported + MEGM + DEIN
+ EGX30ETF + other manual-review symbols.

**12. Is ORAS protected from Yahoo fallback?** **Yes.** `yahoo_forbidden`/no-fallback in
the policy; routing tests assert ORAS returns DATA_UNAVAILABLE on EODHD failure and that
no generic fallback bypasses it. The dry run confirms ORAS never selects Yahoo.

**13. Can a forward-only staged migration be approved?**
**Only cautiously, and not blanket.** Tier A/B are candidates for **forward** scans, but
even forward signals will differ from Yahoo for some symbols because EODHD's more complete
bar coverage shifts signals (quantified in `real_backtest_summary.csv`). Recommendation:
enable **Tier B first** (EODHD-only / ORAS — where Yahoo is missing or proven bad, so
there is no regression), then a small **Tier A pilot** with per-symbol sign-off. **Frozen
historical backtests must stay on Yahoo in every tier.** Nothing is approved here.

**14. Was historical backtest behavior left unchanged?** **Yes** — 0 historical backtests
moved; every tier keeps `historical_backtest_provider: yahoo` (or local); the engine and
baseline are untouched.

**15. Was routing left inactive?** **Yes** — `active:false`, every entry `approved:false`,
no Activate control in the UI.

**16. Is production still disabled?** Yes — `production_enabled=false`,
`automatic_execution=false`, `broker_orders_enabled=false`.

## Corrected headline
EODHD is the **higher-quality source** (more complete coverage — it fills Yahoo's dropped
trading days — correct on ORAS, split-adjustable), **but precisely because it is more
complete and unadjusted, it is not a drop-in for frozen Yahoo backtests**: the real engine
shows material changes for most symbol/windows. The safe path is **forward-only, tiered,
per-symbol**, with historical backtests frozen on Yahoo and an event-specific
corporate-action/volume policy — none of which is activated in this phase.

## Safety
Token never printed/logged (masked fingerprint only); `.env` gitignored; raw provider
data preserved; Yahoo remains active; Rubix unchanged; routing inactive & unapproved;
frozen backtest engine unmodified; no strategy/threshold/TP-SL/paper/production change.
**Stopping — routing not activated, provider not switched, frozen backtests unchanged.**
