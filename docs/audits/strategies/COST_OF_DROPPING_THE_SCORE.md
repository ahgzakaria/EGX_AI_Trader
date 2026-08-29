# What Converting to Gates Would Cost: Almost Nothing, For an Unflattering Reason

**Question:** [SCORE_CANNOT_BE_REBUILT.md](SCORE_CANNOT_BE_REBUILT.md) showed no
weighting of the score's components ranks out of sample. So what would it cost
to drop `score >= min_score` and `confidence >= min_confidence` from the BUY
condition and keep only the gates?

**Status:** measured. **Nothing is converted** — this is the cost, not the
change. `strategy/` and `config/settings.json` are untouched.

**Short answer:** the conversion costs one trade. 457 → 456, +53.99% → +51.70%,
Sharpe 0.57 → 0.54 — noise in every direction. And the reason is not that the
score is harmless. It is that **the two thresholds already refuse almost
nothing**: 22 of 1,040 candidates, 2%.

---

## 1. The thresholds are calibrated against a population they never see

Across the whole panel the score's median is **49**, and half of all bars fall
below 50. Against that, `min_score: 50` reads like a serious filter.

But the score is only consulted after the market regime, trend, momentum, volume,
reward-ratio and four quality checks have already run. Among the 1,040 bars that
actually reach it:

| | minimum | 1st pct | 5th | 10th | 25th | median | gate | refused |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| **score** (max 118) | 40 | 50 | 55 | 58 | 65 | **72** | 50 | **1.0%** |
| **confidence** (max 100) | 52 | 59 | 70 | 75 | 83 | **93** | 65 | **2.0%** |

`min_score: 50` sits at the **first percentile** of the population it judges. A
threshold that looks like a 50% filter is acting as a 1% one, because the other
gates have already pushed everything that reaches it far above the line.

This is the missing half of two earlier findings. `min_score` is not a useful
lever ([SCORE_DIAGNOSIS.md](SCORE_DIAGNOSIS.md)) and raising it measured harmful
([SWING_IMPROVEMENT_LEVERS.md §4](SWING_IMPROVEMENT_LEVERS.md)) — not only
because the score carries no signal, but because at its shipped value it is
barely connected to the decision at all. To refuse a quarter of the candidates it
would have to read **65**; to refuse half, **72**.

## 2. The sweep

Lift is the twenty-session net return from the next close, minus what an average
tradeable name did on the same days. `sum lift` is count × mean: what a portfolio
taking every survivor would collect, in percentage points.

| score gate | kept | of all | lift | 2016–2022 | 2023–2026 | sum lift |
|---|--:|--:|--:|--:|--:|--:|
| ≥ 0 | 1,040 | 100% | +1.10% | +0.60% | +1.47% | 11.5 |
| ≥ 40 | 1,040 | 100% | +1.10% | +0.60% | +1.47% | 11.5 |
| **≥ 50 (shipped)** | 1,030 | 99% | +1.15% | +0.68% | +1.49% | **11.9** |
| ≥ 60 | 893 | 86% | +1.13% | +0.98% | +1.22% | 10.1 |
| ≥ 70 | 615 | 59% | +1.43% | **+2.86%** | **+0.82%** | 8.8 |
| ≥ 80 | 286 | 28% | +2.01% | **+6.90%** | **+0.90%** | 5.8 |

Two things to read here. Mean lift rises with the threshold **in training and
not in validation** — at ≥80 the training figure is +6.90% and the validation
figure +0.90%, which is the same in-sample fitting
[SCORE_CANNOT_BE_REBUILT.md](SCORE_CANNOT_BE_REBUILT.md) measured directly. And
`sum lift` **falls monotonically** above the shipped value: raising the threshold
throws away more total edge than it concentrates.

Confidence behaves the same way and refuses even less.

## 3. Are the refused candidates actually worse?

This is the whole question. A throttle that removes trades at random costs only
the round trip on them; one that removes the bad ones is doing real work.

| | n | mean lift | median lift |
|---|--:|--:|--:|
| kept by 50 / 65 | 1,018 | **+1.16%** | −2.25% |
| refused | **22** | **−1.38%** | −4.51% |
| difference | | **+2.54%** | |

t = **+0.96**, p = **0.35**. By era: 2016–2022 t = +0.51, p = 0.62; 2023–2026
t = +0.88, p = 0.43.

The refused candidates do look worse, and on twenty-two observations that is
what noise looks like. It is the most that can be said for the thresholds, and
it is not enough to call them a filter.

## 4. The conversion, in the engine

Both thresholds set to zero, everything else as shipped, isolated run:

| | shipped (50 / 65) | gates only (0 / 0) |
|---|--:|--:|
| Trades | 457 | **456** |
| Win rate | 39.82% | 40.57% |
| Profit factor | 1.28 | 1.27 |
| Total return | +53.99% | **+51.70%** |
| Max drawdown | 18.56% | 19.47% |
| Sharpe | 0.57 | 0.54 |
| Average per trade | +0.41% | +0.39% |
| Years positive | 6 of 10 | 6 of 10 |

**One trade.** Removing a threshold cost a trade rather than adding one, which
is path dependence: an extra earlier signal opens a position that blocks a later
one in the same symbol. Everything else moves by less than the difference
between neighbouring configurations in every sweep this project has run.

## 5. So what does the conversion actually cost?

Not the numbers. What it costs is three things the score currently supplies, and
none of them is a measurement question:

| What the score does | What removing it costs |
|---|---|
| The BUY threshold | **Nothing measurable.** §4. |
| The WATCH / AVOID tiers and the star rating | The whole tiering. `score >= watch_score`, `score >= 40`, and the 5/4/3/2-star ladder all read off it. A gates-only strategy has BUY and not-BUY, and a reason string for why. |
| The capacity tie-break in `PortfolioSimulator.selection_key` | Nothing measurable, and independently confirmed on the other strategy: refused trades measured no worse than taken ones, t = −0.89 ([CAPACITY_IS_THE_CONSTRAINT.md §2](CAPACITY_IS_THE_CONSTRAINT.md)). Something still has to break ties; it just should not be described as quality. |

The second row is the real decision, and it is a decision about the product
rather than about the data. A four-tier star rating carries a promise the
underlying number cannot keep — that a 5-star row is better than a 3-star row —
and that promise is measured at ρ = +0.012, p = 0.76 in validation. Whether to
keep an ordering that reads well and means nothing is a judgement, and it is not
mine to make.

## 6. What this does not say

It does not say the strategy would be *fine* without the score. The strategy has
no out-of-sample selection edge with or without it
([DAILY_STRATEGY_DIAGNOSIS.md §1](DAILY_STRATEGY_DIAGNOSIS.md)), and removing a
threshold that was doing nothing does not change that. The conversion is cheap
because the thing being removed was already inert — which is an argument for
honesty in the interface, not an argument that the strategy is one change away
from working.

## Limits

- One dataset, one universe, 1,040 candidate bars and 456 realised trades.
- The eligible population reproduces the shipped gate stack in vectorised form;
  its components are verified against the real `strategy/` functions on 2,000
  random checks.
- Lift is measured over a fixed twenty-session hold from the next close, which
  is not the strategy's own exit. It is the right basis for comparing *which
  candidates* are better and the wrong one for predicting the portfolio result;
  §4 is there for that.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/cost_of_dropping_the_score.py
venv/Scripts/python.exe scripts/research/isolated_backtest.py --label gates_only \
  --set strategy.min_score=0 --set strategy.min_confidence=0
```
