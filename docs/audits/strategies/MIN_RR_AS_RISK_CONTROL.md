# `min_rr` — A Risk Lever, Not a Return Lever

**Question:** removing the trailing stop and the candle gate removed two
accidental throttles ([TRAILING_STOP_VERDICT.md](TRAILING_STOP_VERDICT.md),
[CANDLE_REMOVAL_MEASURED.md](CANDLE_REMOVAL_MEASURED.md)). A deliberate one is
needed. Is `min_rr` it?

**Status:** **shipped.** `min_rr: 3.0` and `trailing_enabled: true`, in both
`config/settings.json` and `DEFAULT_SETTINGS`. An as-configured run with no
overrides reproduces the measured run byte-for-byte across all four output
files — see §5.

**Short answer:** yes, but only as a risk control. Its effect on drawdown is
monotone, large and mechanically explained. Its effect on return is not, and
should not be quoted.

---

## 1. The sweep

Current code (breakout removed, candles removed), corrected costs, trailing stop
on, candle gate off, configuration pinned.

| `min_rr` | Trades | Win rate | Profit factor | Total return | Max drawdown | Sharpe | Years + |
|---:|--:|--:|--:|--:|--:|--:|--:|
| 1.5 | 1,201 | 19.98% | 0.91 | **-36.94%** | 58.89% | -0.14 | 6/10 |
| 2.0 | 1,014 | 24.65% | 1.06 | **+23.41%** | 40.79% | 0.21 | 4/10 |
| 2.5 | 580 | 29.66% | 0.88 | **-30.89%** | 46.56% | -0.22 | 4/10 |
| 3.0 | 468 | 36.75% | **1.19** | **+40.36%** | **17.62%** | **0.45** | 6/10 |

Return goes negative, positive, negative, positive. **A real lever does not do
that.** If reward/risk measured trade quality, 2.5 could not be worse than both
of its neighbours. On this evidence alone the +40.36% is not a finding.

## 2. But the risk side is monotone

| `min_rr` | Risk per share | Avg notional | Exposure | Max consecutive losses | Max drawdown |
|---:|--:|--:|--:|--:|--:|
| 1.5 | **12.90%** | 19,416 | 40.7% | **22** | 58.89% |
| 2.0 | 9.97% | 24,717 | 39.3% | 16 | 40.79% |
| 2.5 | 8.45% | 28,445 | 23.0% | 14 | 46.56% |
| 3.0 | **6.85%** | 35,293 | 22.4% | **13** | **17.62%** |

Risk per share falls monotonically. Position notional rises monotonically.
Exposure falls. **Maximum consecutive losses falls monotonically, 22 to 13.**
Drawdown falls, with one blip at 2.5.

The mechanism is arithmetic, not luck. Reward/risk is
`(target - entry) / (entry - stop)`. Demanding a higher ratio selects setups with
a **tighter stop relative to the target**. A tighter stop means a smaller loss
whenever the trade is stopped, and with risk-based sizing it also means more
shares on a smaller per-share risk. Fewer, larger, tighter positions — losing
less on each failure.

**So the return bounced because return is the noisiest thing being measured. The
risk profile, which is the least noisy, responded smoothly and substantially.**

## 3. What is not being claimed

`min_rr 3.0` returned +40.36%. That number should not be repeated, for three
reasons:

- The sweep is non-monotone, as above.
- **84% of the total profit comes from 5 trades.** The top 20 of 468 contribute
  250% of it, meaning the other 448 lose money in aggregate.
- The median trade is **-0.845%**. Most trades still lose.

In fairness, that shape is not peculiar to this setting. Every configuration
tested has a negative median, and `rr 3.0` has the *least* negative one and the
*least* tail concentration of the profitable configurations:

| | Median trade | Top 20 share of profit |
|---|--:|--:|
| `rr 2.0` | -1.330% | 433% |
| `rr 3.0` | **-0.845%** | **250%** |

The strategy is a lottery in every version. This is the least extreme lottery,
not a departure from lotteries.

## 4. The trailing stop had to ship with it

The sweep ran with `trailing_enabled: true`, but the live config had it
**false**. Shipping `min_rr 3.0` onto that would have been an unmeasured
combination, so it was measured:

| At `min_rr 3.0` | Trailing **on** | Trailing **off** |
|---|--:|--:|
| Trades | 468 | 244 |
| Profit factor | **1.19** | 0.89 |
| Total return | **+40.36%** | **-32.52%** |
| Max drawdown | **17.62%** | 53.05% |
| Avg per trade | **+0.32%** | **-0.75%** |
| Avg holding | 4.52 days | **17.09 days** |

At `min_rr 1.5` disabling the trail barely mattered — -0.0047% against -0.1005%
([TRAILING_STOP_VERDICT.md](TRAILING_STOP_VERDICT.md)). At 3.0 it is the
difference between +40.36% and -32.52%.

The mechanism is in the holding column. Without the trail, positions run to the
holding cap at 17.09 days instead of 4.52, bleeding against the tight stops that
a high reward ratio selects.

**The effect does not transfer between configurations.** Had the earlier verdict
been carried over instead of re-measured, this would have shipped the losing
side.

## 5. Shipped, and verified against what was measured

`min_rr: 3.0` and `trailing_enabled: true`, in `config/settings.json` and in
`DEFAULT_SETTINGS` so a clean checkout behaves the same.

A confirming run with **no overrides**, reading the live configuration as it
stands, produces `backtest_results.csv`, `backtest_statistics.csv`,
`equity_curve.csv` and `symbol_statistics.csv` **byte-identical** to the run the
decision was made on. What ships is what was measured.

Justified on drawdown and loss-streak length, **not** on return.

The distinction matters. Saying "this returns 40%" sells an in-sample point.
What is defensible is that maximum drawdown falls from 58.89% to 17.62% and the
worst losing streak from 22 trades to 13, through a mechanism that reads off the
definition of reward/risk and responds smoothly across the sweep.

It does not create an edge. Nothing measured in this investigation does — the
entry date carries no information at t = +0.65, and gross drift equals trading
costs to four decimal places
([SELECTION_AND_EXECUTION.md](SELECTION_AND_EXECUTION.md)).

## 6. Left open deliberately

`DEFAULT_SETTINGS` and the running `config/settings.json` disagree on **eleven
settings** — `min_score` 65 vs 50, `min_confidence` 80 vs 65, `min_trend` 25 vs
12, `min_momentum` 5 vs 3, `min_volume` 5 vs 0, `require_market_analyzer` and
`require_quality_filter` both False vs True, `quality_min_adx` 20 vs 21,
`exit_mode` TARGET1 vs TARGET2, `partial_exit` False vs True,
`allow_overlapping_trades` False vs True.

These are not tuning differences; they are two different strategies. Everything
in this investigation was measured against the running file, which is correct
because that is what executes — but `min_rr 3.0` is calibrated in *that* context.
A clean checkout would pair it with closed gates and higher thresholds, a
combination nobody has measured, and §4 is the demonstration that such
combinations do not inherit their parts' results.

Not reconciled here. Choosing which of the two is the strategy is a decision
about the product, not a defect to fix.

## Limits

- One dataset, one universe, in-sample, four settings tried.
- 468 trades over roughly nine years is about 52 a year; the drawdown estimate
  rests on a handful of episodes.
- No walk-forward. Before this ships as a return claim it would need one; as a
  risk claim the monotone response across four settings is the argument.
- Nothing here is a recommendation to trade.

## Reproduce

```
for rr in 1.5 2.0 2.5 3.0; do
  venv/Scripts/python.exe scripts/research/isolated_backtest.py --label rr$rr \
    --set strategy.min_rr=$rr --set strategy.require_candle_confirmation=false \
    --set backtest.trailing_enabled=true
done
```
