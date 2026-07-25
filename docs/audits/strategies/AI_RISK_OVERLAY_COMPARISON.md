# AI Risk Overlay Comparison

## Scope and historical integrity

This Phase 4 comparison ran all five modes over the same symbols, out-of-sample
period (`2020-08-06` to `2026-06-08`), initial capital, transaction costs,
slippage, portfolio limits and technical strategy settings.  No entry rule,
exit rule, technical indicator, feature, threshold or portfolio setting was
optimised during this phase.

Every historical AI decision came from the fold-local Walk-Forward model that
was available on that date.  The comparison fails if an out-of-sample
prediction is missing; it never loads or falls back to the persisted global
model. `STRATEGY_ONLY` remains the configured default production mode.

## Configured overlay policy

The values below are configuration defaults, not values selected after seeing
these results.

| Probability band | Position-size multiplier |
| --- | ---: |
| >= 75% | 100% |
| >= 60% and < 75% | 75% |
| >= 45% and < 60% | 50% |
| < 45% | 25% (configurable to reject instead) |

Hard filtering keeps the existing 60% minimum probability.  Hybrid uses the
same sizing bands and ranking, but rejects only probabilities below the
configured 20% emergency threshold. Ranking combines strategy quality (40%),
AI probability (35%), Risk/Reward (15%), confidence (5%) and market regime
(5%) only when capacity is constrained.

## Comparative results

| Metric | Strategy Only | AI Hard Filter | AI Position Sizing | AI Ranking Only | AI Hybrid |
| --- | ---: | ---: | ---: | ---: | ---: |
| Net Profit | 54,035.58 | 16,409.72 | 31,374.97 | **71,618.87** | 32,898.68 |
| Return % | 54.04% | 16.41% | 31.37% | **71.62%** | 32.90% |
| Profit Factor | 1.23 | **1.72** | 1.39 | 1.30 | 1.56 |
| Maximum Drawdown | 18.59% | 5.81% | 6.16% | 16.48% | **5.40%** |
| Sharpe | 0.72 | **0.95** | 0.74 | 0.86 | 0.81 |
| Sortino | 1.56 | **2.27** | 1.61 | 1.91 | 1.78 |
| Calmar | 0.41 | 0.46 | 0.78 | 0.59 | **0.92** |
| Recovery Factor | 2.53 | 2.82 | 4.97 | 3.90 | **6.09** |
| Expectancy / trade | 73.12 | **162.47** | 40.12 | 97.31 | 62.78 |
| Trade count | 739 | 101 | 782 | 736 | 524 |
| Win rate | 26.79% | **80.20%** | 27.11% | 27.45% | 33.40% |
| Average win | 1,469.88 | 483.86 | 532.38 | **1,538.49** | 522.75 |
| Average loss | 496.86 | 1,340.16 | 162.01 | 509.93 | 190.20 |
| Exposure | 45.57% | 5.02% | 16.30% | 45.71% | 12.48% |
| Maximum consecutive losses | 16 | **3** | 15 | 13 | 17 |
| Final equity | 154,035.58 | 116,409.72 | 131,374.97 | **171,618.87** | 132,898.68 |
| Profit missed due to AI | 0.00 | 266,564.73 | 199,065.03 | **4,266.01** | 229,966.04 |
| Losses avoided due to AI | 0.00 | **224,283.82** | 169,420.31 | 17,892.54 | 197,365.05 |
| Net economic value added by AI | 0.00 | -42,280.91 | -29,644.72 | **13,626.53** | -32,600.99 |
| Return / Drawdown | 2.9069 | 2.8244 | 5.0925 | 4.3459 | **6.0926** |
| Profit per unit of drawdown | 2,906.70 | 2,824.39 | 5,093.34 | 4,345.81 | **6,092.35** |

`Profit missed` and `losses avoided` are calculated by matching each
Strategy-Only executed candidate with the corresponding mode's executed
portfolio result.  They include sizing effects as well as decisions displaced
by constrained portfolio capacity.

## Interpretation

- **AI Hard Filter** remains a useful risk-control benchmark but gives up too
  much return and has negative economic value.
- **AI Position Sizing** sharply reduces drawdown but also reduces net profit
  by 22,660.61 versus Strategy Only.
- **AI Hybrid** produces the best drawdown-based ratios, but its emergency
  rejection and reduced sizing still destroy 32,600.99 of economic value.
- **AI Ranking Only** is the only overlay that improves both net profit
  (+17,583.29), final equity and drawdown versus Strategy Only. It also raises
  Sharpe, Sortino, Calmar, Recovery Factor and reduces maximum consecutive
  losses (16 to 13), without turning AI into an entry gate.

## Recommendation

**D. AI Ranking Only.**

It has the best demonstrated balance in this comparison: higher return and
economic value than Strategy Only, a lower maximum drawdown, and improved
risk-adjusted stability. It is selected for those combined properties, not
because it has the highest Profit Factor (it does not). Strategy Only remains
the default configuration until this result is independently reproduced on a
new forward period.

## Generated audit artifacts

- `reports/ai_risk_overlay_summary.csv` — source data for every metric above.
- `reports/ai_risk_overlay_trades.csv` — one row per technical BUY candidate
  per mode, including `AIProbability`, `AIPositionMultiplier`, `AIRank`,
  `AIRejectionReason`, `FinalPositionSize`, execution state and portfolio
  rejection reason.
