# The Daily Dashboard Strategy: Capacity, and What the Live Record Says

**Measured:** 2026-09-24. Nothing in `strategy/` was changed or read differently.

**Question:** the live record of the Daily Dashboard's BUYs (55 closed, PF about
1.0, median trade −1.47%) is far below the backtest's PF 1.54. Is something
broken, is capacity binding the way it bound CONFIRMED_VOLUME_BREAKOUT, and is
there a change the evidence supports?

**Short answer:** nothing is broken and capacity is not binding. The live record
is what this strategy's own backtest has produced over its last twelve months.
No change to the rule or its portfolio policy survives measurement.

---

## 1. Capacity is not the constraint

`scripts/research/daily_capacity_sweep.py`. The shipped 2% / 10% / 10 settings
give `min(10, floor(10 / 2)) = 5` positions — the same arithmetic
[CAPACITY_IS_THE_CONSTRAINT.md](CAPACITY_IS_THE_CONSTRAINT.md) found in the
breakout strategy, fixed there and never measured here. Trades generated once by
the frozen engine (662 candidates, 229 symbols) and re-simulated under each
policy, fixed before running:

| policy | positions | taken | PF | return | max DD | Sharpe | Calmar |
|---|--:|--:|--:|--:|--:|--:|--:|
| **A shipped** 2.0 / 10 / 10 | 5 | 609 | 1.54 | +142.71% | 17.97% | **0.81** | 0.57 |
| B same budget 1.0 / 10 / 10 | 10 | 654 | 1.49 | +73.67% | 11.58% | 0.78 | 0.54 |
| C same budget 0.67 / 10 / 15 | 14 | 662 | 1.51 | +51.66% | 8.06% | 0.78 | 0.58 |
| D breakout policy 1.0 / 15 / 15 | 15 | 654 | 1.49 | +73.67% | 11.58% | 0.78 | 0.54 |

Market benchmark over the window: **+411%**.

The shipped policy already takes **92%** of signals; the breakout strategy was
taking 32%. Every alternative lowers Sharpe and lowers net profit in the
≥2023 era (52,696 → 50,458 EGP), so by the rule registered beforehand none is
better. The lower drawdowns are the lower risk per trade, not an improvement.

## 2. The live record is not a live problem

Per trade, net of each symbol's measured costs (`backtesting.costs.TradingCosts`),
the 662 backtest trades against the 55 closed live decisions
(`reports/daily_dashboard_forward/buy_outcomes.csv`):

| | n | win | mean | median | PF |
|---|--:|--:|--:|--:|--:|
| backtest, all | 662 | 36% | +0.46% | −0.49% | 1.37 |
| **backtest, last 12 months** | 162 | 26% | +0.06% | −1.06% | **1.04** |
| live | 55 | 29% | −0.05% | −1.47% | 0.98 |

Drawing 55 trades from the backtest 20,000 times, a PF this low occurs **24.5%**
of the time, and a mean this low 23.4%: the live profit factor is ordinary
variance. The live **median** is not — only 0.1% of draws are that bad — but the
backtest's own last twelve months sit close to it. The typical live trade is the
typical recent trade.

## 3. It is not decay; it is the shape

| year | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| trades | 38 | 28 | 30 | 43 | 51 | 43 | 93 | 112 | 147 | 77 |
| PF | 3.25 | 1.30 | **0.34** | 1.89 | 1.26 | **0.87** | 2.02 | **0.91** | 1.29 | 1.39 |
| win | 50% | 50% | 27% | 53% | 43% | 28% | 39% | 37% | 25% | 34% |
| median | +0.01% | +0.10% | −0.42% | +0.25% | −0.19% | −0.91% | 0.00% | −0.52% | −0.99% | −0.71% |

Three losing years in ten, and 2025–26 are not among them. What has changed is
the distribution: the win rate fell from about half to about a third and the
median trade from flat to −0.7% to −1.0%, so the result rests on fewer, larger
winners. **The top 10 of 662 trades are 80% of all net return.**

## What follows

* **No change to the rule is supported.** Capacity (§1), loosening or removing
  the trail ([exit sweep](../../../scripts/research/exit_rule_sweep.py)), the
  EMA20 entry gate (its live split contradicts its backtest), and reweighting the
  score ([DAILY_STRATEGY_DIAGNOSIS.md](DAILY_STRATEGY_DIAGNOSIS.md) §1) have each
  been measured and each fails.
* **Skipping trades is the expensive mistake here.** With 80% of the return in
  ten trades, a discretionary filter that drops a few of them costs more than
  every small loss it avoids.
* **The one rule this data measured as real** is a twenty-day breakout
  confirmed by volume — [CONFIRMED_VOLUME_BREAKOUT.md](CONFIRMED_VOLUME_BREAKOUT.md).
  Its forward record has six signals, the first resolving at the end of
  September 2026. That record, not another variant of this rule, is what can
  justify changing what the Daily Dashboard shows.
