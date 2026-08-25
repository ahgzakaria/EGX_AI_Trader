# What Can Actually Be Improved

**Question:** the swing strategy has negative expectancy once real friction is
charged. What, measurably, would fix it?

**Status:** investigation. Every run below was performed and then **reverted** —
`config/settings.json` and `reports/` are back to their as-found state, and
nothing is recommended into production without a decision. Raw outputs are kept
in the session scratchpad.

**Short answer:** one config flag. Disabling the trailing stop moves the strategy
from **-0.046%** to **+0.317%** per trade after real friction, and it is the only
finding here that is not an in-sample artifact.

---

## 1. The result

All runs use the **unmodified sealed engine**, same data, same day. Only
`config/settings.json` differs. Per-trade figures carry the 0.161% friction
correction from [SWING_STRATEGY_PROBE.md §2](SWING_STRATEGY_PROBE.md).

| | Base | Trailing **off** | Trailing off + `min_rr` 2.0 |
|---|--:|--:|--:|
| Trades | 736 | 437 | 283 |
| Win rate | 25.95% | **38.67%** | 36.75% |
| **Net per trade, real friction** | **-0.046%** | **+0.317%** | **+0.725%** |
| Median per trade | negative | **+0.389%** | -0.861% |
| Total return | -1.78% | **+53.68%** | +33.77% |
| Profit factor | 0.99 | **1.16** | 1.13 |
| Max drawdown | 35.04% | 49.31% | **41.95%** |
| Sharpe | 0.19 | 0.31 | **0.47** |
| Sortino | 0.39 | 0.48 | **0.81** |
| Exposure | 28.58% | 54.09% | 39.65% |
| Avg hold | 5.97 | **20.52** | 19.88 |

## 2. Why the trailing stop was the leak

On the base run, `TrailingStop` accounts for **587 of 736 exits — 80% — at
-1.707% and a 10.4% win rate.** Every other exit reason is strongly positive:

| Exit | n | Avg net | Win rate |
|---|--:|--:|--:|
| Partial+Target2 | 54 | +13.377% | 100.0% |
| Partial+Timeout | 10 | +9.550% | 100.0% |
| Partial+BreakEven | 59 | +6.290% | 98.3% |
| StopLoss | 26 | -8.498% | 0.0% |
| **TrailingStop** | **587** | **-1.707%** | **10.4%** |

The strategy was picking well enough to reach its targets. The trailing stop was
killing four trades in five on the way there.

This is not a free win. With the trail removed, real stop-outs rise from 26 to
139 at -10.551% — the trail *was* limiting individual losses. It simply cost far
more than it saved.

**It also confirms the gap probe mechanically.** Friction is a toll per round
trip; return accrues per night. The trail forced ~6-session holds; removing it
gives ~20.5. That is the same arithmetic that killed the range scalp, now
measured on 437 swing trades.

## 3. The cost, stated plainly

Removing the trail raises maximum drawdown from **35.04% to 49.31%** and nearly
doubles exposure. Higher return, bought with more volatility and capital tied up
three times longer. `min_rr` 2.0 buys much of that back — drawdown 41.95%, Sharpe
0.47 against 0.31 — at the price of a third of the total return.

Note the distribution difference: trailing-off alone has a **positive median**
(+0.389%, 50.8% of trades profitable), while `min_rr` 2.0 has a higher mean but a
**negative median** (-0.861%). The second configuration earns more per trade and
depends more on the tail. Trailing-off alone is the healthier distribution;
`min_rr` 2.0 is the better risk-adjusted number. That is a genuine choice, not a
ranking.

## 4. The scoring system does not rank

Net profit by score decile, base run:

| Score | n | Avg net |
|---|--:|--:|
| 50 | 26 | **+1.015%** |
| 60 | 140 | +0.295% |
| 70 | 267 | +0.045% |
| 80 | 223 | -0.337% |
| 90 | 72 | **-0.575%** |

Monotone **downward**. It reproduces in all four runs performed — sometimes
inverted, sometimes merely noise, never predictive.

**Consequence: raising `min_score` makes results worse, not better.** An earlier
recommendation in this session to raise it as a volume throttle was wrong and is
withdrawn. Rebuilding or removing the score is real work and is not attempted
here.

## 5. What is *not* established

A post-hoc filter stack on the trailing-off run — `RR ≥ 2`, `score < 80`,
`hold > 5` — yields +3.174% per trade over 99 trades. **Do not use this number.**
It is in-sample selection on the same data that suggested the filters.

The one filter that was converted into a prospective test is `min_rr`, because it
changes signal generation rather than filtering results. Post-hoc it promised
+1.506%; run properly it delivered **+0.725%**. That gap is the size of the
in-sample bias, and it is why the rest of the stack is not reported as a finding.

## 6. Recommendation

1. **Disable the trailing stop.** `backtest.trailing_enabled: false`. Config
   only, no sealed file touched, and the single largest measured effect: negative
   to positive expectancy after real friction.
2. **Consider `min_rr` 2.0** if drawdown matters more than total return. Config
   only. Better Sharpe, Sortino and drawdown; a third less return and a negative
   median.
3. **Do not raise `min_score`.** Measured harmful.
4. **Fix the backtest's friction** before trusting any of these numbers further.
   At 0.161% understated per round trip it is the difference between a positive
   and a negative headline.
5. **Candle confirmation** is separate and unchanged by this: still neutral,
   still fiction, still awaiting the seal decision
   ([CANDLE_CONFIRMATION_REMOVAL.md](CANDLE_CONFIRMATION_REMOVAL.md)).

## Limits

- One dataset, one universe, no walk-forward. These are in-sample configuration
  comparisons, not validated parameter choices. `min_rr` 2.0 in particular should
  be walk-forward tested before it ships.
- The friction correction applies an August-2026 spread to trades from 2017 on.
- Drawdowns of 42-49% are large regardless of which configuration wins.
- Nothing here is a recommendation to trade.

## Reproduce

Set the flags in `config/settings.json`, then:

```
venv/Scripts/python.exe backtest.py
```
