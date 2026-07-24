# BUY=0 Regression Audit

Date: 2026-07-19  
Scope: Swing/Daily scanner only  
Audit dataset: `reports/RUN_20260719_191032/dataset`  
Dataset hash: `e42b40d9c8f4dbf4fb4c716c0e25f8842bd4f849b9a5b5bdf15910d41397c221`  
Symbols evaluated: 206

## Executive verdict

`BUY=0` is **not caused by a decision-engine regression, Rubix overlay, AI hard filtering, or Phase 9 decision-support code**. Re-evaluating the exact archived candles with both the current pipeline and the Phase 8 validated settings produces the same per-symbol decisions: **139 WATCH, 67 AVOID, 0 BUY**.

The dominant cause is the frozen Risk gate. After the preceding gates, 135 symbols reach Risk and 113 fail there; only 22 continue. QualityFilter reduces those 22 to 3, and CandleConfirmation removes the remaining 3. This is a valid interaction of existing frozen rules on the completed daily candles, not evidence that the strategy was relaxed or tightened.

One settings drift was confirmed in the current configuration: `quality_min_volume_ratio` was `1.1` while the validated Phase 8/5 snapshot and `settings_manager.py` default are `1.0`. It was restored to `1.0` as an exact baseline restoration. It was not causal: BUY count remained 0 before and after. No trading formula or decision-engine code was changed.

## Evidence produced

- Full symbol-level trace: `reports/buy_signal_full_trace.csv`
- Machine-readable audit summary: `reports/buy_zero_audit_summary.json`
- Reproducible diagnostic runner: `scripts/audit_buy_zero.py`
- Exact archived source run: `reports/RUN_20260719_191032`
- Stable settings reference: `reports/baselines/PHASE5_CURRENT_DATA_V2/settings_snapshot.json`

The trace records ticker, final signal, score, confidence, component scores, RR, entry, stop, targets, support, resistance, ATR, daily close, Rubix quote fields, quote age, every gate boolean, gate order, first failed gate, exact rejection reason, stable/current decisions, EMA alignment, and recent return.

## 1. Effective configuration audit

### Current runtime versus validated stable values

| Setting | Stable | Current after restoration | Result |
|---|---:|---:|---|
| minimum score | 50 | 50 | Match |
| minimum confidence | 65 | 65 | Match |
| minimum RR | 1.5 | 1.5 | Match |
| maximum RR | 100.0 | 100.0 | Match |
| minimum trend | 12 | 12 | Match |
| minimum momentum | 3 | 3 | Match |
| minimum volume | 0 | 0 | Match |
| candle confirmation required | true | true | Match |
| quality filter required | true | true | Match |
| market analyzer required | true | true | Match |
| quality minimum ADX | 20 | 20 | Match |
| quality minimum volume ratio | 1.0 | 1.0 | Restored from 1.1 |
| quality minimum ATR % | 1.5 | 1.5 | Match |
| quality minimum resistance room % | 3.0 | 3.0 | Match |
| AI minimum probability | 60 | 60 | Match; advisory only in scanner |
| scanner provider | provider layer | Rubix | No strategy threshold effect |
| fallback provider | Yahoo | Yahoo | Match |

The archived observed scan used temporarily looser Quality values (`ADX=17`, `ATR%=1.3`, `resistance room=2.5`) and still produced zero BUY. Re-running its exact archived frames with the stable values produced no per-symbol divergence. Therefore those temporary values do not explain BUY=0.

### Override search

- `config/settings.json` is the effective persisted source.
- `config/settings_manager.py` defaults match the stable values after the restoration above.
- No strategy threshold environment-variable override exists.
- No Streamlit `session_state` override of scanner gates was found.
- Launcher settings configure processes/providers only; they do not set strategy gates.
- Provider environment variables configure database paths and freshness, not Score, Confidence, RR, Trend, Momentum, Volume, Quality, Candle, or MarketAnalyzer.
- No migrated/deprecated strategy key was selected in place of the current keys.

### Code comparison

All 14 Python files under `strategy/` plus `indicators/technical.py` are byte-identical to the archived RC1 code used for the stable path. In particular, `strategy/decision_engine.py` and `strategy/entry.py` have no post-validation behavior divergence.

## 2. Full decision trace and gate semantics

The gate order reconstructed directly from `strategy/decision_engine.py` is:

1. MarketAnalyzer
2. MarketRegime
3. Trend
4. Momentum
5. Volume
6. Risk
7. QualityFilter
8. CandleConfirmation
9. Score
10. Confidence

The trace script evaluates this exact order and preserves early-exit semantics. `failed_gate` is the first gate that prevents a BUY; separate columns retain the independent truth value of every gate.

## 3. Gate funnel

Sequential counts show how many symbols remain after each gate:

| Stage | Passed stage / remaining | Failed at stage | % of 206 remaining |
|---|---:|---:|---:|
| Symbols analyzed | 206 | 0 | 100.00% |
| MarketAnalyzer | 206 | 0 | 100.00% |
| MarketRegime | 140 | 66 | 67.96% |
| Trend | 135 | 5 | 65.53% |
| Momentum | 135 | 0 | 65.53% |
| Volume | 135 | 0 | 65.53% |
| Risk | 22 | 113 | 10.68% |
| QualityFilter | 3 | 19 | 1.46% |
| CandleConfirmation | 0 | 3 | 0.00% |
| Score | 0 | 0 | 0.00% |
| Confidence | 0 | 0 | 0.00% |

Independent pass counts, ignoring earlier exits, are MarketAnalyzer 206, MarketRegime 140, Trend 165, Momentum 206, Volume 206, Risk 54, QualityFilter 37, CandleConfirmation 47, Score 165, and Confidence 160.

The first-failure distribution is:

| First failed gate | Symbols |
|---|---:|
| Risk | 113 |
| MarketRegime | 66 |
| QualityFilter | 19 |
| Trend | 5 |
| CandleConfirmation | 3 |

**Largest collapse: Risk.** It removes 113 of the 135 symbols reaching it (83.70%). The final zero is a combination of Risk, then QualityFilter, then CandleConfirmation.

Final decisions: **0 BUY, 139 WATCH, 67 AVOID**.

## 4. Risk gate audit

The exact Risk boolean is implemented in `strategy/decision_engine.py`:

```python
cfg.MIN_RR <= rr <= cfg.MAX_RR
```

With the effective settings, this is `1.5 <= RR <= 100.0`. There is no separate configurable maximum absolute-risk percentage inside this gate; therefore the trace reports `allowed_risk` as the RR interval rather than inventing a nonexistent numeric risk cap.

Entry geometry comes from `strategy/entry.py`:

- `risk = buy_high - stop_loss`
- `reward = target2 - buy_high`
- `RR = reward / risk`

The stop derives from historical support and ATR; target 2 derives from previous resistance plus ATR. All inputs are computed from the completed daily OHLCV frame.

Across all 206 rows, 184 have RR below 1.5. Among the sequentially eligible candidates, 113 are first rejected at Risk. This explains why high scores and attractive trends can still result in WATCH: strength scores do not override an unfavorable frozen entry/stop/target geometry.

### Live quote isolation

- All 206 trace rows had a Rubix quote.
- Rubix `Last` differed from the completed daily close for 204 rows.
- The number of rows where Rubix overlay was used in a strategy calculation is **0**.
- Current Scanner mode loads completed Yahoo/local-cache daily OHLCV first. Rubix values are attached only to `DataFrame.attrs["market_data"]` as operational evidence.
- `core/scanner.py` calls the frozen decision first and appends `actionability_fields(...)` afterward.
- Entry, stop, target, RR, resistance room, ATR, support/resistance, and candle confirmation are unchanged by Rubix Last/Bid/Ask.

The production counterfactual “completed daily close instead of Rubix current quote” is identical to current production behavior and yields 0 BUY.

## 5. Stable versus current comparison

The exact archived candles were evaluated three ways:

1. Observed archived scan result.
2. Current decision pipeline and current effective settings.
3. Current decision pipeline with the validated stable Phase 8 settings snapshot.

All three produced exactly `WATCH=139`, `AVOID=67`, `BUY=0`. Per-symbol divergence count between current and stable settings is **0** for Signal, Score, Confidence, RR, failed gate, and rejection reason.

Therefore there is no “first divergent symbol/file/line”: the compared paths do not diverge. The strategy and indicator files themselves are also byte-identical to RC1.

## 6. Current data audit

Latest completed daily candle distribution in the exact archive:

| Latest date | Symbols |
|---|---:|
| 2026-07-16 | 200 |
| 2026-07-15 | 1 |
| 2026-07-06 | 1 |
| 2026-06-25 | 1 |
| 2025-03-12 | 1 |
| 2024-10-23 | 1 |
| 2021-02-04 | 1 |

The scanner's 206 results are successful-history rows. Coverage failures are tracked separately and are not converted to AVOID. The six stale-tail symbols should remain visible as data-quality debt, but they do not explain the market-wide zero because 200 symbols share the latest completed session.

MarketAnalyzer passed all 206. Its archived EGX index context was SIDEWAYS because the archived index series was insufficient for a stronger index classification, while per-symbol regimes were 104 BULL, 83 SIDEWAYS, and 19 BEAR. MarketRegime caused 66 first failures, but Risk remained the largest collapse.

## 7. Strong-stock cross-check

These are the ten strongest recent-price examples selected mechanically from the same completed daily dataset. They demonstrate that the technical evidence and final decision are internally consistent: the candidates are strong, but their frozen RR geometry fails.

| Ticker | 20-day return | EMA alignment | Trend/Momentum/Volume/Breakout | ADX | Volume ratio | Resistance room | Score / Confidence | RR | First failed gate |
|---|---:|---|---|---:|---:|---:|---|---:|---|
| IBCT.CA | 23.01% | EMA20 > EMA50 > EMA200 | 30 / 15 / 20 / 20 | 57.35 | 2.31 | 9.46% | 103 / 100 | 0.43 | Risk |
| DTPP.CA | 101.56% | EMA20 > EMA50 > EMA200 | 30 / 20 / 20 / 20 | 50.43 | 2.01 | ~0.00% | 102 / 100 | 0.15 | Risk |
| CAED.CA | 61.75% | EMA20 > EMA50 > EMA200 | 30 / 15 / 20 / 20 | 50.25 | 6.03 | 13.28% | 98 / 100 | 0.20 | Risk |
| AMER.CA | 45.86% | EMA20 > EMA50 > EMA200 | 30 / 15 / 20 / 20 | 36.33 | 1.52 | 0.26% | 97 / 100 | 0.05 | Risk |
| BIOC.CA | 47.56% | EMA20 > EMA50 > EMA200 | 30 / 15 / 20 / 20 | 32.84 | 3.65 | ~0.00% | 97 / 100 | -0.22 | Risk |
| ASPI.CA | 9.72% | EMA20 > EMA50 > EMA200 | 30 / 18 / 20 / 20 | 27.55 | 2.97 | 5.71% | 96 / 100 | 0.20 | Risk |
| KWIN.CA | 12.22% | EMA20 > EMA50 > EMA200 | 30 / 13 / 20 / 20 | 26.04 | 6.94 | 6.44% | 96 / 100 | 0.49 | Risk |
| NINH.CA | 24.27% | EMA20 > EMA50 > EMA200 | 30 / 13 / 20 / 20 | 25.46 | 8.56 | 6.17% | 96 / 100 | -0.09 | Risk |
| AIFI.CA | 13.13% | EMA20 > EMA50 > EMA200 | 30 / 13 / 20 / 20 | 27.24 | 1.61 | ~0.00% | 95 / 100 | 0.20 | Risk |
| EDFM.CA | 12.01% | EMA20 > EMA50 > EMA200 | 30 / 13 / 20 / 20 | 29.77 | 2.00 | 0.54% | 95 / 100 | 0.21 | Risk |

Full unrounded values, entry geometry, ATR, spread, quote age, and reasons are in `reports/buy_signal_full_trace.csv`.

## 8. Diagnostic counterfactuals

These calculations were performed in memory only and did not alter production settings or behavior.

| Diagnostic | BUY count | Newly qualified tickers |
|---|---:|---|
| Production current | 0 | — |
| Without Risk | 4 | IBCT.CA, NCCW.CA, EEII.CA, MBEG.CA |
| Without CandleConfirmation | 3 | MENA.CA, EBSC.CA, PHDC.CA |
| Without QualityFilter | 5 | MILS.CA, EGREF.CA, EASB.CA, EOSB.CA, AJWA.CA |
| Without MarketRegime | 1 | SUCE.CA |
| Completed daily close instead of Rubix | 0 | — |
| Before provider overlay | 0 | — |
| Stable Phase 8 settings | 0 | — |

The counterfactuals diagnose gate pressure; they are not recommendations to remove or lower any gate.

## 9. Signal-semantics verification

- AI advisory is not a hard filter in the Swing scanner. `TradingDecisionService.LIVE_ADVISORY` evaluates AI only after a technical BUY and cannot demote it.
- Missing AI probability does not block a technical BUY.
- Optional live actionability fields are appended after the frozen decision and cannot change Signal.
- Data failures are recorded with non-strategy operational statuses and are not converted into AVOID.
- WATCH is assigned by the existing explicit decision branches, not used to conceal a post-hoc provider or AI rejection.
- Gate labels in the trace are derived from the same booleans used by the engine.
- Phase 9 decision support is additive and has no strategy-engine dependency or execution authority.

## 10. Confirmed change

Only one production setting was changed:

```text
config/settings.json
quality_min_volume_ratio: 1.1 -> 1.0
```

Reason: exact restoration to the validated stable snapshot and existing SettingsManager default. Effect: three independent QualityFilter evaluations changed, but no complete candidate passed all other gates. BUY remained **0 before and 0 after**.

No threshold was optimized, no strategy formula was changed, and no provider/AI/ranking/portfolio/indicator code was altered.

## 11. Validation results

| Validation | Result |
|---|---|
| Audit script syntax/import check | PASS |
| Targeted Phase 8/9, provider, overlay, disconnected Rubix, and decision-semantics tests | PASS — 48 tests |
| Full automated suite | PASS — 149 tests |
| Identical-candle indicator and decision comparison | PASS |
| Stable-vs-current per-symbol comparison | PASS — 0 divergences across 206 symbols |
| Rubix overlay isolation | PASS — 0 strategy rows use overlay |
| Disconnected Rubix Swing-history preservation | PASS |
| Phase 8 archived replay | PASS — source `RUN_20260714_125023`, replay `RUN_20260719_201435`, dataset hash matched, metrics matched, Walk-Forward predictions matched |
| Streamlit smoke test | PASS — headless server started on port 8512 and `/_stcore/health` returned `ok`; temporary server stopped |

The full archived replay reproduced Strategy Only at 66.43% return / 18.02% maximum drawdown and AI Ranking Only at 72.98% return / 16.80% maximum drawdown. These values are the metrics sealed inside that specific Phase 8 source run; the replay reported `metrics_match=true` and `predictions_match=true` rather than comparing against a different data vintage.

## Root cause

The exact root cause is **valid rule interaction on the latest completed daily candles**:

1. MarketRegime removes 66 candidates.
2. Of the 135 remaining after Trend/Momentum/Volume, Risk rejects 113 because RR is outside `1.5..100.0`—almost always below 1.5.
3. QualityFilter reduces the remaining 22 to 3.
4. CandleConfirmation rejects those last 3.

The zero is therefore mathematically reproducible under the frozen strategy. The visibly strong market and high component scores do not imply that the strategy's entry/stop/target RR condition must pass.

## Remaining technical debt (not changed in this audit)

- Six successful-history symbols have older final candles and should remain visibly flagged.
- The support/resistance and target geometry is conservative for fast breakouts; changing it would be a strategy change and requires a separate explicitly approved research phase.
- A UI gate-funnel view would make the cause of zero BUY immediately visible, but UI work was outside this urgent regression fix.
