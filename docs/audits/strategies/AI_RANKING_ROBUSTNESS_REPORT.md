# AI Ranking Only — Robustness and Production Validation

> **Drawdown note (2026-08-29).** Every `MaxDrawdown` in this document was
> measured before `backtesting/equity.py` marked open positions to market,
> so each is a *closed-trade* drawdown and understates the real figure by
> 0.3-2.6 percentage points. The numbers are left as measured; the
> conversion table for every archived run is in
> [DRAWDOWN_WAS_UNDERSTATED.md](DRAWDOWN_WAS_UNDERSTATED.md).


## Scope and integrity

Phase 5 did not change the strategy, AI model, features, thresholds, exits,
costs, portfolio heat or ranking formula. `STRATEGY_ONLY` remains the default
setting. Every AI prediction remains a strict out-of-sample Walk-Forward
prediction; no global model is loaded in a historical run.

The portfolio simulator now collects all candidates for a given entry date,
processes exits first, and fills capacity from the ordered daily batch. The
deterministic selection order is:

1. Final AI ranking score
2. AI probability
3. Strategy score
4. Confidence
5. Risk/Reward
6. Symbol alphabetically

Reversed input order and five seeded shuffles (`7`, `11`, `29`, `101`,
`20260711`) produced identical trade signatures and metrics for both modes.

## Base out-of-sample result after deterministic daily selection

| Metric | Strategy Only | AI Ranking Only |
| --- | ---: | ---: |
| Return | 65.81% | **71.62%** |
| Net profit | 65,811.22 | **71,618.87** |
| Profit Factor | 1.28 | **1.30** |
| Maximum Drawdown | 17.91% | **16.48%** |
| Sharpe | 0.78 | **0.86** |
| Sortino | 1.73 | **1.91** |
| Calmar | 0.51 | **0.59** |
| Expectancy | 90.15 | **97.31** |
| Trade count | 730 | 736 |
| Win rate | 26.30% | **27.45%** |
| Final equity | 165,811.22 | **171,618.87** |

The AI ranking advantage is `+5,807.65` net profit with a `1.43` percentage
point lower drawdown. It is therefore not caused by the earlier symbol-order
behaviour.

## Period consistency

- Yearly: AI Ranking Only had higher net profit in **5 of 7 years (71.4%)**.
- Quarterly: it was higher in **8 of 24 quarters (33.3%)**, lower in 9, and
  identical in 7. This is mixed short-horizon consistency, despite the
  positive full-period result.
- Regime: it improved SIDEWAYS profit (14,502.85 vs 6,603.33), but slightly
  reduced BULL profit (57,116.02 vs 59,207.89).
- Liquidity/volatility: it was strongest in low-liquidity and medium/high
  volatility buckets; it only marginally improved the medium-liquidity bucket.
- Sector: no sector column exists in `data/symbols.csv`, so sector analysis
  was correctly marked unavailable rather than inferred.

## Capacity and stress tests

| Scenario | Strategy return / DD | AI Ranking return / DD | Result |
| --- | --- | --- | --- |
| Capacity 3 | 30.08% / 19.96% | **36.49% / 15.20%** | AI better |
| Capacity 5 | 65.81% / 17.91% | **71.62% / 16.48%** | AI better |
| Capacity 8 requested | 65.81% / 17.91% | **71.62% / 16.48%** | Heat limit keeps effective capacity at 5 |
| Commission +25% | 42.11% / 20.71% | **42.50% / 20.92%** | Small AI edge; slightly higher DD |
| Commission +50% | 9.88% / 22.89% | **21.21% / 21.41%** | AI better |
| Slippage +25% | 52.93% / 18.41% | **70.71% / 16.91%** | AI better |
| Slippage +50% | 46.94% / 19.46% | **69.38% / 17.22%** | AI better |
| One-bar delayed execution | 72.25% / 10.59% | **85.03% / 10.02%** | AI better |
| Remove top 5 Strategy-Only symbols | 16.17% / 20.03% | **20.22% / 20.05%** | AI better, but both weaken |
| Remove top 10 Strategy-Only trades | **24.52% / 20.99%** | 14.92% / 21.55% | AI loses |

Cost/slippage rows reprice the same executed path with increased execution
frictions. The delayed-execution row is stricter: it re-runs entry and exit
simulation from the next bar's open.

## Bootstrap confidence intervals

1,000 reproducible bootstrap samples were run with fixed seeds. These intervals
resample completed trades and therefore quantify trade-return uncertainty, not
market-regime or serial-correlation uncertainty.

| Mode | Return 95% interval | Profit Factor 95% interval |
| --- | --- | --- |
| Strategy Only | 4.90% to 133.02% | 1.02 to 1.60 |
| AI Ranking Only | 10.17% to 132.60% | 1.04 to 1.59 |

The lower return bound is better for AI Ranking Only, but the intervals overlap
substantially. This supports cautious use rather than a production-default
promotion.

## Verdict

**B. Keep it optional pending forward testing.**

The result is deterministic, survives symbol-order changes, beats Strategy
Only across the tested capacity settings, and remains favourable under most
friction and delay tests. However, quarter-level consistency is mixed and
removing the top 10 baseline trades reverses the advantage. Promote it only
after a new live/forward out-of-sample period confirms the improvement.

## Generated data

- `reports/ai_ranking_yearly.csv` — year and quarter comparisons.
- `reports/ai_ranking_regime.csv` — market regime, liquidity and volatility
  buckets.
- `reports/ai_ranking_capacity.csv` — requested capacities 3, 5 and 8.
- `reports/ai_ranking_stress_tests.csv` — friction, delay and concentration
  tests.
- `reports/ai_ranking_bootstrap.csv` — 1,000 fixed-seed samples per mode.
