# EODHD Phase 3 — Split Normalization, Manual-Queue Resolution & Routing Dry Run

**Date:** 2026-07-23 · Mode `EODHD_SHADOW` · **Active historical provider: Yahoo
(unchanged)** · **Rubix live (unchanged)** · production disabled · routing policy
**inactive** (no entry approved). Audit only — no strategy/indicator/backtest/threshold/
TP-SL change, no `Close`↔`Adj Close` swap, no provider switch, no raw-data mutation.
**550 tests pass.**

New code: `providers/eodhd_adjustment.py` (split engine), `providers/eodhd_routing.py`
(routing + failure logic). New data: `data/eodhd/corporate_actions/*.json` (450 raw
files), `data/eodhd/historical_symbol_routing.json` (inactive). Reports under
`reports/eodhd/`: corporate_action_inventory, split_adjustment_validation,
volume_adjustment_validation, corporate_action_discrepancies,
backtest_immutability_summary, backtest_trade_differences, manual_queue_resolution,
routing_dry_run(+summary).

## Part 1 — three explicit series
- **RAW_UNADJUSTED** — EODHD provider OHLCV, untouched (kept as `Raw *`).
- **SPLIT_ADJUSTED** — adjusted for stock splits only (candidate to match Yahoo `Close`).
- **TOTAL_RETURN_ADJUSTED** — split + dividend adjusted (EODHD `adjusted_close`); never a
  strategy-`Close` substitute.
No ambiguous `adjusted=True` — every series names its exact transformation.

## The 15 questions

**1. Can EODHD reproduce the current split-adjusted historical behavior?**
**Approximately, and exactly for recent windows.** The engine's SPLIT_ADJUSTED `Close`
matches Yahoo `Close` **exactly (0.0%) on the current date for 10/11 corp-action
symbols**, and slashes full-history error (SWDY 469%→0.08%, FWRY 94%→0.09%, ABUK
641%→0.07%, TMGH 0.29%→0.11%). A **deep-history residual remains for some** (COMI 8.7%,
EAST 22%, HRHO 6.5%, SKPC 31%, EFIH 25% mean) because EODHD and Yahoo **classify certain
capital actions (bonus issues) as splits differently** — so it is not bit-identical
across all history.

**2. Is EODHD `adjusted_close` appropriate for strategy `Close`?**
**No.** `adjusted_close` is TOTAL_RETURN (split **and** dividend) adjusted; the project's
`Close` is split-adjusted, dividend-**un**adjusted. Using it would silently change the
price level the strategy consumes. It is retained only as the separate
TOTAL_RETURN_ADJUSTED series.

**3. How must OHLC be transformed around splits?**
Each row is divided by the **cumulative product of split ratios with ex-date strictly
after that row's date**; every OHLC value in a row uses the **same** factor, so
`Low ≤ Open,Close ≤ High` is preserved. A bar on/after an ex-date is already post-split.
Deterministic, idempotent, no rounding until presentation.

**4. How must Volume be transformed?**
**Raw Volume × the same split factor** (share count rises on a forward split), applied
per row with the identical factor used for prices. Validated directionally against Yahoo
(`volume_adjustment_validation.csv`); Volume remains otherwise unadjusted (no dividend
effect).

**5. Did any frozen backtest materially change?**
Input-equivalence immutability (the engine is a deterministic function of the series;
the direct engine-injection run was not forced to avoid touching the frozen baseline):
recent windows for clean symbols → **ROUNDING_ONLY** (SWDY, FWRY, TMGH, ABUK, ETEL,
HRHO); symbols with **recent splits** → **CORPORATE_ACTION_DIFFERENCE** at the split
ex-date (COMI, EAST, SKPC, EFIH show 13–35% single-day diffs where the providers time/
round the split differently); **ORAS → MATERIAL_STRATEGY_CHANGE** (Yahoo is broken, so
switching *fixes* it). So: **forward paper and split-free recent backtests are
effectively immutable; backtests crossing a recent split ex-date would change.**

**6. How many of the 15 review symbols were resolved?**
**14 of 15 resolved as EODHD_CORRECT** (EODHD matches the live Rubix close; the >0.5%
flags were penny-stock rounding over a year), **1 as YAHOO_CORRECT** (TRTO — EODHD off on
a 3-piastre stock, Yahoo matches Rubix). Plus **ORAS = EODHD_CORRECT** (Rubix confirms).

**7. Final status of MEGM.**
**SYMBOL_NOT_LIQUID / INSUFFICIENT_EVIDENCE.** Rubix carries no valid MEGM price (0.0), so
EODHD 43.92 vs Yahoo 12.54 cannot be adjudicated → **MANUAL_REVIEW hold**, no routing.

**8. How many symbols qualify for proposed EODHD_PRIMARY?**
**195 EODHD_PRIMARY + 27 EODHD_ONLY = 222** proposed for EODHD (the 27 are Yahoo-missing,
EODHD-only). All `approved: false`.

**9. How many still require Yahoo?**
**40 YAHOO_ONLY** (39 EODHD-unsupported coverage gaps + TRTO). Plus the 194 EODHD_PRIMARY
symbols keep Yahoo as an **approved fallback**.

**10. Which symbols must never use Yahoo (data known bad)?**
**ORAS** — Yahoo `ORAS.CA` is proven stale/mis-scaled (10×, zero volume); the policy sets
`yahoo_forbidden: true`, and the dry-run confirms ORAS returns DATA_UNAVAILABLE (never
Yahoo) on EODHD failure.

**11. Expected daily API usage.**
Proposed routing → **≈222 EODHD EOD calls + 40 Yahoo calls per day**, then **~100% cache
hits intraday** (persistent disk cache). Negligible vs the 100,000/day limit.

**12. Can per-symbol routing be safely enabled next?**
**Partially, staged — not blanket.** Safe to enable **forward** EODHD_PRIMARY for the
validated clean symbols + ORAS + EODHD_ONLY (Yahoo-missing), because recent SPLIT_ADJUSTED
matches Yahoo exactly. **Do not** repoint historical **backtests** to EODHD for symbols
flagged CORPORATE_ACTION_DIFFERENCE without first deciding the bonus-vs-split
reconciliation (or keeping those backtests on Yahoo). MEGM/DEIN stay MANUAL_REVIEW. This
requires the deliberate wiring step, which this phase does not perform.

**13. Was the active provider left as Yahoo?** Yes — `active_historical: yahoo`,
`provider_mode: EODHD_SHADOW`.

**14. Was Rubix unchanged?** Yes — used only as read-only adjudicator; no code/config
touched.

**15. Is production still disabled?** Yes — `production_enabled=false`,
`automatic_execution=false`, `broker_orders_enabled=false`.

## Corporate-action inventory
225 mapped symbols → **2,118 actions (795 splits + 1,323 dividends), 0 invalid split
ratios**; raw responses cached (never overwritten) under `data/eodhd/corporate_actions/`.

## Routing dry run (inactive)
262 universe symbols routed in simulation: **222 EODHD / 40 Yahoo / 2 MANUAL_REVIEW /
1 EXCLUDED**. On simulated EODHD failure: **194 fall back to Yahoo**, **28 return
DATA_UNAVAILABLE** (27 EODHD-only + ORAS), **ORAS never falls back to Yahoo**. No empty
frame or zero-substitution is ever returned. `policy_active=false`, `provider_switched=
false`.

## Safety
Token never printed/logged (masked fingerprint only); `.env` gitignored; raw provider
data preserved; Yahoo remains the active historical provider; Rubix unchanged; routing
policy inactive and unapproved; no strategy/indicator/backtest/threshold/TP-SL/paper/
production change. **Stopping — the routing policy is not enabled and the provider is not
switched.**
