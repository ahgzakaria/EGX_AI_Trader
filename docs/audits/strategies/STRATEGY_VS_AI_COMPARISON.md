# Strategy Only vs Strategy + Walk-Forward AI


> **Defensive-asset note (2026-09-10).** A risk claim in this document rests on
> holding EGP cash while out of the market, and that is not a defence.
> [REGIME_DETECTABILITY.md](REGIME_DETECTABILITY.md) measured the same shipped
> configuration in both currencies: a drawdown of −18.27% in pounds and −65.29%
> in dollars, against the market's −66.38%. The protection was the unit of
> account falling, not the position. **Every drawdown, Calmar and Sortino figure
> below overstates the protection it describes**, by an amount not computed here.
> The ranking between configurations may survive; the level does not.

> **Drawdown note (2026-08-29).** Every `MaxDrawdown` in this document was
> measured before `backtesting/equity.py` marked open positions to market,
> so each is a *closed-trade* drawdown and understates the real figure by
> 0.3-2.6 percentage points. The numbers are left as measured; the
> conversion table for every archived run is in
> [DRAWDOWN_WAS_UNDERSTATED.md](DRAWDOWN_WAS_UNDERSTATED.md).


## Scope and integrity checks

This comparison uses only out-of-sample Walk-Forward AI predictions for the
AI-filtered pass.  It covers `2020-08-06` through `2026-06-08` and runs both
modes with the same symbol universe, date range, initial capital, trading
costs, slippage, portfolio limits and strategy settings.  The only changed
decision is the explicit Walk-Forward AI approval gate.

No persisted/global model is permitted in either historical pass.  Missing
out-of-sample predictions are a hard error; no global-model fallback exists.
No strategy rule, feature, AI threshold, entry rule, exit rule or portfolio
setting was optimized or changed for this comparison.

## Side-by-side results

| Metric | Strategy Only | Strategy + Walk-Forward AI |
| --- | ---: | ---: |
| Total signals | 894 | 103 |
| Executed trades | 739 | 101 |
| AI-rejected trades | 0 | 634 |
| Winning trades | 198 | 81 |
| Losing trades | 477 | 17 |
| Win rate | 26.79% | 80.20% |
| Net profit | 54,035.58 | 16,409.72 |
| Return % | 54.04% | 16.41% |
| Profit Factor | 1.23 | 1.72 |
| Maximum Drawdown | 18.59% | 5.81% |
| Expectancy per trade | 73.12 | 162.47 |
| Average win | 1,469.88 | 483.86 |
| Average loss | 496.86 | 1,340.16 |
| Payoff ratio | 2.96 | 0.36 |
| Sharpe Ratio | 0.72 | 0.95 |
| Sortino Ratio | 1.56 | 2.27 |
| Calmar Ratio | 0.41 | 0.46 |
| Recovery Factor | 2.53 | 2.82 |
| Maximum consecutive wins | 5 | 12 |
| Maximum consecutive losses | 16 | 3 |
| Average holding period | 5.99 days | 3.98 days |
| Exposure % | 45.57% | 5.02% |
| Final equity | 154,035.58 | 116,409.72 |

`AI-rejected trades` counts baseline tradable candidates rejected by the AI
gate; it is not simply the difference between the two execution counts, since
portfolio state can differ after a rejection.

## AI filter diagnostics

| Diagnostic | Result |
| --- | ---: |
| Profitable trades rejected by AI | 162 |
| Losing trades rejected by AI | 472 |
| Profit avoided by rejecting losers | 224,283.82 |
| Profit missed by rejecting winners | 266,353.93 |
| Direct rejection value | -42,070.11 |
| Net economic value added by AI | -37,625.86 |
| Acceptance rate | 11.30% |
| Rejection rate | 88.70% |
| Precision among accepted trades | 81.19% |

The filter improves trade quality and risk-adjusted measures, but it rejects
too many profitable trades and gives up 37,625.86 of economic value versus the
strategy-only baseline.

## Breakdown artifacts

- `reports/strategy_vs_ai_trade_comparison.csv` — trade-level match of the
  baseline candidates, AI approval and matched AI-pass trades.
- `reports/strategy_vs_ai_yearly_comparison.csv` — results by calendar year.
- `reports/strategy_vs_ai_regime_comparison.csv` — results by market regime.
- `reports/strategy_vs_ai_symbol_comparison.csv` — results by symbol.
- `reports/strategy_vs_ai_probability_buckets.csv` — result by AI-probability
  bucket.
- `reports/strategy_vs_ai_summary.csv` — source data for the table above.

## Verdict

**B. AI reduces risk but sacrifices too much return.**

The Walk-Forward AI filter reduced maximum drawdown from 18.59% to 5.81% and
improved Profit Factor (1.23 to 1.72), Sharpe (0.72 to 0.95) and Sortino (1.56
to 2.27).  However, net profit fell from 54,035.58 to 16,409.72 and final
equity fell by 37,625.86.  Threshold optimization is intentionally out of
scope for this phase.
