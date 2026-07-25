# Phase 11 — Adaptive Strategy Selector

## Verdict

**PASS for an independent, chronological decision-support selector.**

**NOT APPROVED as the default execution strategy.** The selector improved Profit
Factor, drawdown, expectancy, and the observed false-breakout rate, but it did
not improve return, Sharpe, or Sortino versus both frozen strategies. Its
configuration therefore remains `enabled_by_default: false` and every output is
marked `DecisionSupportOnly: true`.

No threshold or parameter was optimized after seeing the results.

## Frozen-engine guarantee

The selector is downstream of both strategies. It consumes their final fields
and never calls Classic, `BREAKOUT_SWING`, or an indicator calculator. A frozen
SHA-256 manifest covers the Classic engine, indicators, portfolio/backtest
components, and every Phase 10 Breakout file.

The Phase 11 historical run reproduced the sealed inputs exactly:

- Baseline Run: `RUN_20260714_125023`
- Dataset SHA-256: `fafa064f87acf39992f53f3f8bf3b22b09d2ef34fb70b26d53dd932f38704414`
- Period: 2020-08-09 through 2026-06-09
- Classic identical: **Yes**
- Breakout identical: **Yes**
- Selector chronology valid: **Yes; 0 invalid decisions**
- Earliest decision used no completed outcome.
- Latest decision date was 2026-06-09 and its latest training exit was
  2026-06-08.

## Architecture

```text
Frozen Classic output ─┐
                       ├─> MarketClassifier ─> WalkForwardPerformanceLedger
Frozen Breakout output ┘                              │
Market/index/breadth evidence ────────────────────────┤
                                                      v
                                      AdaptiveStrategySelector
                                                      │
                         BUY_CLASSIC / BUY_BREAKOUT / WATCH / AVOID / NO_TRADE
                                                      │
                                      Decision support only
```

The independent package contains:

- `strategy_selector/market_classifier.py`: explainable session regimes and
  robustness tags.
- `strategy_selector/selector_score.py`: expanding, regime-specific Beta
  probabilities plus a 0–100 Strategy Edge Score.
- `strategy_selector/selector.py`: per-symbol comparison and explanation.
- `strategy_selector/selector_backtest.py`: chronological selection and the
  unchanged portfolio simulator/statistics consumers.
- `strategy_selector/selector_report.py`: reproducible CSV/JSON artifacts.
- `strategy_selector/selector_settings.json`: declared, non-optimized selector
  policy; disabled by default.

## No-look-ahead design

For a decision on date `D`, the learning ledger receives a strategy outcome
only when `trade.exit_date < D`. Same-day exits are not visible. Preprocessing
does not fit on the full period; there is no global strategy mapping and no
future/global fallback for sparse regimes. Sparse regimes use only the declared
neutral Beta prior.

The final probability snapshot saved for a current scan is produced only after
the last historical decision. It never feeds back into the historical replay.

## Market classifier

Supported regimes are `STRONG_BULL`, `BULL`, `WEAK_BULL`, `SIDEWAYS`,
`HIGH_VOLATILITY`, `BEAR`, `PANIC`, and `UNKNOWN`. Each classification stores
its reasons and the complete input snapshot.

The classifier consumes final strategy values and already-calculated market
evidence: CASE30/proxy trend, EMA alignment, ADX, ATR, index momentum/volume,
breadth, advancers/decliners, new highs/lows, breakout frequency, average RR,
volume ratio, gap frequency, trend/momentum/confidence/edge summaries, and
volatility expansion. Sector strength is preserved as an optional field but was
unavailable in the sealed dataset.

The sealed Phase 8 archive contains only one usable CASE30 row, so historical
sessions explicitly record `CASE30 history unavailable; cross-sectional proxy
used`. This lowers classifier confidence and is not hidden or imputed.

## Strategy selection method

There is no fixed rule such as “Bull always means Breakout.” For each regime and
strategy, an expanding ledger estimates:

```text
success_probability = (prior_successes + completed_wins)
                    / (prior_successes + prior_failures
                       + completed_wins + completed_losses)
```

The Strategy Edge Score combines that walk-forward probability with the
strategy's already-final output quality. The selector does not change either
strategy score, RR, entry, exit, position size, or risk. A recommendation is
issued only when the learned probability, probability advantage, sample count,
and selector confidence satisfy the predeclared settings. Bear/Panic produce
`NO_TRADE`; High Volatility is `WATCH_ONLY`.

## Historical comparison

All modes used identical dates, capital, fees, slippage, portfolio capacity,
position sizing, and exit simulation.

| Metric | Classic | BREAKOUT_SWING | Adaptive Selector |
|---|---:|---:|---:|
| Trades | 729 | 843 | 82 |
| Win rate | 26.34% | 45.08% | 47.56% |
| Net profit | 66,427.21 | 161,251.71 | 23,099.10 |
| Return | 66.43% | 161.25% | 23.10% |
| Profit Factor | 1.29 | 1.20 | **1.32** |
| Maximum Drawdown | 18.02% | 46.22% | **8.71%** |
| Sharpe | **0.79** | 0.67 | 0.52 |
| Sortino | **1.76** | 1.05 | 0.89 |
| Expectancy / trade | 91.12 | 191.28 | **281.70** |
| Exposure | 45.46% | 124.99% | 10.50% |
| Final capital | 166,427.21 | 261,251.71 | 123,099.10 |
| False breakout rate | N/A | 30.37% | **28.00%** on selected Breakout trades |

The selector generated 210 candidate BUY recommendations (203 Breakout and 7
Classic); the unchanged portfolio simulator executed 82. It produced 4,368
`WATCH` and 1,212 `NO_TRADE` decisions.

The result is defensive rather than superior: its small exposure explains much
of the lower drawdown, while the 23.10% return and weaker risk-adjusted ratios
fail the stated requirement to outperform both strategies.

## Robustness by regime/condition

Adaptive executed trades only in permitted regimes:

| Regime / condition | Trades | Win rate | Net profit | PF | Expectancy |
|---|---:|---:|---:|---:|---:|
| WEAK_BULL | 27 | 51.85% | 14,962.91 | 1.66 | 554.18 |
| STRONG_BULL | 25 | 52.00% | 8,853.17 | 1.36 | 354.13 |
| BULL | 20 | 40.00% | 2,901.88 | 1.22 | 145.09 |
| SIDEWAYS | 10 | 30.00% | -3,618.86 | 0.69 | -361.89 |
| LOW_VOLATILITY tag | 4 | 25.00% | -2,548.43 | 0.49 | -637.11 |
| BEAR | 0 | — | 0 | — | — |
| HIGH_VOLATILITY | 0 | — | 0 | — | — |
| Election period | 0 | — | 0 | — | — |
| Sharp correction | 0 | — | 0 | — | — |

The best observed adaptive regime was `WEAK_BULL`; `SIDEWAYS` and the
low-volatility subset were unprofitable. Zero-trade groups are reported as zero
coverage, not as successful robustness results. No regime demonstrated complete
Adaptive outperformance over **both** frozen strategies across return, Profit
Factor, Sharpe/Sortino, and expectancy. `WEAK_BULL` had the strongest Adaptive
expectancy, but its Profit Factor (1.66) remained below Classic (1.73).

## Learned probability snapshot by regime

These are descriptive post-run probabilities for a later current scan. They
were not available to earlier historical decisions.

| Regime | Classic | Breakout | Learned aggregate preference | Execution policy |
|---|---:|---:|---|---|
| STRONG_BULL | 27.27% | 58.75% | Breakout | Eligible if per-symbol evidence confirms |
| BULL | 29.45% | 51.80% | Breakout | Eligible if per-symbol evidence confirms |
| WEAK_BULL | 31.08% | 44.68% | Breakout | Conservative/per-symbol comparison |
| SIDEWAYS | 25.42% | 50.73% | Breakout | Conservative/per-symbol comparison |
| HIGH_VOLATILITY | 28.00% | 43.75% | Breakout | WATCH_ONLY |
| BEAR | 26.24% | 46.63% | Breakout | NO_TRADE |
| PANIC / UNKNOWN | neutral prior when unseen | neutral prior when unseen | None | NO_TRADE/conservative |

The final choice is still per symbol because final output quality is combined
with the learned probability; the table is not a hardcoded regime map.

## Current archived scan

The reproducible current-scan evidence uses `RUN_20260719_191032`:

- Market regime: **WEAK_BULL**
- Regime confidence: **65.0%**
- Explanation: positive conditions lack broad/strong confirmation
- Overall per-symbol preferred count: **Classic 110, Breakout 96**
- Current preferred strategy: **Classic**
- Adaptive BUY opportunities: **0**
- `BUY_CLASSIC`: 0
- `BUY_BREAKOUT`: 0

This does not rewrite the frozen Classic or Breakout decisions. It is a separate
advisory panel.

## Direct answers

1. **Did Classic change?** No. Its complete Phase 11 replay is identical to the
   sealed baseline: 729 trades, 66.43% return, PF 1.29, DD 18.02%.
2. **Did Breakout change?** No. It is identical to Phase 10: 843 trades,
   161.25% return, PF 1.20, DD 46.22%.
3. **Did Replay remain identical?** Yes for the frozen strategy inputs and
   dataset; the formal archived-run replay result is recorded in Validation.
4. **Did Adaptive outperform Classic?** No overall. PF, drawdown, win rate, and
   expectancy improved; return, Sharpe, Sortino, and total profit did not.
5. **Did Adaptive outperform Breakout?** No overall. PF, drawdown, expectancy,
   and false-breakout rate improved; return, Sharpe, Sortino, and profit did not.
6. **In which regimes?** It did not conclusively outperform both strategies in
   any regime. It was profitable in WEAK_BULL, STRONG_BULL, and BULL;
   unprofitable in SIDEWAYS and the low-volatility subset; and had no exposure
   in Bear, high-volatility, election, or sharp-correction groups.
7. **Which strategy is preferred for each regime?** The post-run aggregate
   probability favors Breakout in every observed regime, with Bear blocked and
   High Volatility restricted to WATCH. Actual selections remain per-symbol and
   may prefer Classic when its final output quality is higher.
8. **Current market regime today?** WEAK_BULL in the archived reproducible scan.
9. **Current preferred strategy today?** Classic by per-symbol majority
   (110 versus 96), not by a hardcoded mapping.
10. **Current Adaptive BUY opportunities?** Zero.

## Validation

- Targeted Phase 11 tests: **8 passed**
- Full regression suite: **167 passed**
- Required 149+ test floor: **passed**
- Streamlit headless smoke: **passed (`STREAMLIT_SMOKE_OK`)**
- Classic frozen replay check inside Phase 11: **identical**
- Breakout frozen replay check inside Phase 11: **identical**
- Walk-forward chronology: **0 invalid decisions**
- Current archived scan: **completed**
- Formal Phase 8 Replay: **passed** as `RUN_20260719_234548`
  - Source: `RUN_20260714_125023`
  - `metrics_match: true`
  - `predictions_match: true`
  - Dataset hash unchanged:
    `fafa064f87acf39992f53f3f8bf3b22b09d2ef34fb70b26d53dd932f38704414`
  - Strategy Only: 729 trades, 66.43% return, PF 1.29, DD 18.02%
  - AI Ranking Only: 731 trades, 72.98% return, PF 1.31, DD 16.80%

## Files added

- `strategy_selector/__init__.py`
- `strategy_selector/market_classifier.py`
- `strategy_selector/selector.py`
- `strategy_selector/selector_score.py`
- `strategy_selector/selector_backtest.py`
- `strategy_selector/selector_report.py`
- `strategy_selector/selector_settings.json`
- `strategy_selector/frozen_strategy_manifest.json`
- `scripts/run_phase11_selector.py`
- `tests/test_adaptive_selector.py`
- `reports/phase11_selector_summary.csv`
- `reports/phase11_selector_decisions.csv`
- `reports/phase11_market_regimes.csv`
- `reports/phase11_selector_robustness.csv`
- `reports/phase11_selector_probabilities.json`
- `reports/phase11_current_scan.csv`
- `reports/phase11_selector_diagnostics.json`

## Files modified

- `core/scanner.py`: attaches independent advisory fields after both frozen
  strategies finish; canonical `Signal` is unchanged.
- `dashboard/home.py`: displays the Adaptive Strategy panel and per-symbol
  explanations without replacing either strategy.

## Known limitations and next step

- Historical CASE30 coverage in the sealed archive is insufficient, so the
  disclosed cross-sectional proxy is used.
- Sector strength is unavailable in the sealed dataset.
- Adaptive has only 82 executed trades and no Bear/high-volatility exposure;
  zero coverage is not proof of robustness.
- The success criteria were only partially met. Do not tune Phase 11 on the same
  history. Keep it disabled and collect forward evidence before reconsidering
  activation.

**Production recommendation: retain Classic/Breakout exactly as-is and keep the
Adaptive Selector as an optional, visible Decision Support panel only.**
