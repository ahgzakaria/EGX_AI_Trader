# The Trailing-Stop Recommendation, Withdrawn

**Supersedes the recommendation in
[SWING_IMPROVEMENT_LEVERS.md](SWING_IMPROVEMENT_LEVERS.md).** That document
recommended disabling the trailing stop as the single largest measured
improvement. Measured again with a correct cost model and pinned configuration,
**it does not survive.**

**Status:** two isolated runs, manifests in `reports/experiments/`. No config or
strategy shipped.

---

## 1. The result

Both runs: sealed engine, candle gate on, `min_rr` 1.5, corrected costs
(0.964% round trip), configuration pinned against the concurrent dashboard.

| | Trailing **on** (baseline) | Trailing **off** |
|---|--:|--:|
| Trades | 638 | 401 |
| Win rate | 20.53% | 34.66% |
| **Avg per trade** | **-0.0047%** | **-0.1005%** |
| Median per trade | -1.400% | -0.270% |
| Profit factor | 0.94 | 0.97 |
| Total return | -14.27% | -9.67% |
| **Max drawdown** | **51.15%** | **56.44%** |
| **Sharpe** | **-0.01** | **-0.06** |
| Avg hold | 6.13 | 21.38 |
| **Years positive** | **3 of 10** | **4 of 10** |

Disabling the trailing stop makes per-trade expectancy **twenty times worse**,
drawdown five points deeper, and Sharpe worse. It improves total return and
profit factor, both marginally, and both while still losing money.

Year by year it is better in **exactly 5 of 10** — a coin flip, and the same
answer the manual split gave before the cost fix:

| Year | Trail on | Trail off | Delta |
|---|--:|--:|--:|
| 2017 | -0.090% | +3.558% | **+3.647%** |
| 2018 | -1.308% | -3.654% | **-2.347%** |
| 2019 | -1.488% | -4.405% | **-2.917%** |
| 2020 | -0.386% | +3.877% | **+4.263%** |
| 2021 | +0.612% | -2.305% | **-2.917%** |
| 2022 | -0.614% | +0.342% | +0.956% |
| 2023 | +0.279% | -0.577% | -0.856% |
| 2024 | -0.390% | -0.943% | -0.553% |
| 2025 | +1.442% | +2.829% | +1.387% |
| 2026 | -0.203% | -0.066% | +0.137% |

## 2. Why the earlier number was wrong

The withdrawn finding claimed -0.046% → +0.317% per trade. Both figures came
from the old cost model with a **flat 0.161% subtracted by hand**. That
subtraction assumed a cost change alters trade *outcomes* only.

It does not. It alters which trades are *taken*: a higher entry fill buys fewer
shares, which changes capital allocation, which changes selection. Charging the
real cost inside the engine moved the baseline from 736 trades to 638 and the
trailing-off run from 437 to 401 — different populations, not the same trades
priced differently.

Every number in the withdrawn document that used the manual correction is
unreliable for the same reason. The correction is not additive.

## 3. What is actually true about the trailing stop

The exit-mix observation stands and is worth keeping. The trail does end 80% of
trades at a small loss, and trades that reach their targets do return double
digits. What was wrong was the conclusion that removing it therefore helps.

Removing it changes the *shape* of the distribution, not its sign:

| | Trail on | Trail off |
|---|--:|--:|
| Win rate | 20.53% | 34.66% |
| Median trade | -1.400% | -0.270% |

More frequent small wins, rarer but larger losses. A healthier-looking
distribution that earns less. The trail was not destroying an edge — it was
reshaping a distribution that has no edge either way.

## 4. Where this leaves the strategy

**Both configurations lose money after real costs**, at profit factor 0.94 and
0.97, negative Sharpe, and drawdowns above 50%. There is no version of the exit
rule tested here that makes the strategy profitable.

That moves the problem upstream. The scoring system already measured as
non-predictive — inversely related to profit across every run — and that is a
larger defect than any exit parameter. Phase 3 was going to be drawdown work on
top of a working edge; there is no working edge to protect yet.

## 5. What Phase 0 was worth

The cost correction and the isolation harness were the first two steps of this
plan and looked like overhead. They:

- reversed the headline recommendation of this investigation;
- caught that `run_backtest` re-reads `settings.json` at start, so the concurrent
  dashboard's configuration had been landing inside measurements (the pin
  intercepted 2 reloads in each run above);
- and made both runs above reproducible, which the artifact they replaced was
  not.

The recommendation would have shipped. It was config-only, it touched no sealed
file, and it was wrong.

## Limits

- Both runs share one dataset and one universe.
- The corrected cost model charges the full quoted spread on every round trip,
  which is harsh for a limit-entry strategy. With the trail removed the exit mix
  changes substantially, so this assumption deserves revisiting before any
  further exit work.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/isolated_backtest.py --label baseline \
  --set strategy.require_candle_confirmation=true --set backtest.trailing_enabled=true
venv/Scripts/python.exe scripts/research/isolated_backtest.py --label trail_off \
  --set strategy.require_candle_confirmation=true --set backtest.trailing_enabled=false
```
