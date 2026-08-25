# The Swing Strategy Under the Probe

**Question:** the gap probe established that on EGX friction is a fixed toll per
round trip while return accrues per night, and that the daily `open` field
cannot be trusted. Both findings point at the swing strategy. This applies them.

**Status:** research probe only. `scripts/research/probe_swing_strategy.py` is
read-only over `reports/backtest_results.csv`, `data/market_data_cache.sqlite`
and `data/rubix_live_market.db`. No strategy, threshold, config or sealed result
was changed.

**Short answer:** the strategy survives the toll question, loses about a third of
its edge to the friction question, and is standing on a daily `open` field that
is fabricated — which does not corrupt its entries, but does mean its required
candle confirmation is not measuring candles.

---

## 1. Holding profile — it clears the toll

684 trades, 2020 → 2026. Mean hold 6.22 sessions, median 4.

| Hold | Trades | Mean | Median | Win rate |
|---|--:|--:|--:|--:|
| 1–2 sessions | 237 | -1.364% | -1.470% | 12.2% |
| 3–5 | 218 | -0.867% | -1.415% | 13.3% |
| 6–10 | 118 | +2.326% | -0.455% | 44.9% |
| 11–20 | 80 | +3.790% | +1.920% | 66.2% |
| 20+ | 31 | +9.577% | +9.620% | 93.5% |

**This table must not be read as "hold longer and earn more."** Holding period
here is an *outcome*, not a decision: 531 of 684 exits (78%) are trailing stops,
which terminate losers early by construction. Short holds are losing trades
because losing trades get stopped out, not the other way round.

What it does say is that the strategy's mean hold sits well past the ~3-night
break-even the gap probe measured, so unlike the range scalp it is not paying the
toll faster than the accrual can cover it. On the toll question, swing is on the
right side of the line.

Its expectancy has the same shape as the gap trade, though: **mean +0.530%,
median -1.110%, positive on 28.2%.** A positive mean over a negative median means
a right tail is carrying the result. That is a real characteristic of the
strategy, not a defect, but it means the average trade is a losing trade and the
equity curve depends on the tail continuing to show up.

## 2. Friction — understated by about a sixth of a percent

The backtest charges `commission` on both legs plus slippage:

| | Round trip |
|---|--:|
| Modelled commission (0.3% × entry + 0.3% × exit) | 0.603% |
| Modelled slippage (0.05% per side) | 0.100% |
| **Modelled total** | **0.703%** |
| Real broker schedule (contract note, both sides) | 0.364% |
| Measured spread, trade-weighted over the 170 traded names with live quotes | 0.500% |
| **Measured total** | **0.864%** |
| **Understatement** | **+0.161%** |

The commission is charged too high and the spread too low, and they nearly
cancel — but not quite. Re-pricing:

- per-trade edge **+0.530% → +0.369%**, a **30% reduction**
- portfolio net profit **75,669 → ~50,000 EGP**, total return **75.7% → 50.0%**

Still profitable, and by a clear margin. But a third of the reported edge is
paying for a spread the backtest did not charge. Spreads are measured in August
2026 and applied to trades from 2020 on, so treat this as indicative rather than
as a restatement of the backtest.

## 3. The `open` field is fabricated

The gap probe found 67.1% of EODHD seed opens equal to the previous close. In the
cache the backtest actually reads it is far worse:

| Provider | Transitions | `open` == previous `close` |
|---|--:|--:|
| yahoo | 492,988 | **97.4%** |
| rubix (daily) | 11,536 | **96.3%** |

And it is not even clipped into the bar it belongs to: **132,033 of 504,747 daily
bars (26.2%) have an `open` outside their own `[low, high]`** — impossible bars.
COMI is typical: every open equals the prior close, and on 2026-07-19 the open of
135.80 sits above that day's high of 135.50.

The open is carried forward, not observed. Anything reading it is reading a
constant.

Also worth splitting by volume: zero-volume bars are **0.00%** impossible, since
they are trivially `O=H=L=C`. **Traded bars are 31.87%.** All of the corruption
lands on the sessions a strategy actually acts on.

### What this does *not* break

The swing entry path is safe. With `execution_delay_bars = 0` — the default
everywhere in `services/backtest_service.py` — `entry_manager` fills at
`buy_high`, a resting limit, and never reads `open`. Exits use close, target and
stop. The headline swing backtest is insulated **at the fill**.

Three other execution paths are not:

- `services/ranking_robustness.py:250` and `core/ai_pullback_research.py:486`
  pass `execution_delay_bars = 1`, and that branch fills at
  `float(data.open[execution_index])`.
- `strategy_breakout/breakout_backtest.py:149` fills at
  `frame["Open"].iloc[entry_index]` **unconditionally** — there is no `buy_high`
  branch on that path at all — with `entry_delay_bars` defaulting to 1
  (`breakout_strategy.py:88`). Worse, the RR guard immediately below it at `:158`
  is annotated *"A gap can invalidate yesterday's geometry"* and is fed
  `actual_entry` derived from that open. With `open ≡ previous close` there is no
  gap by construction, so the guard can never fire for the reason it was written,
  and admits trades it exists to reject.

Those results should be treated as unreliable until the field is fixed.

## 4. What it does break: candle confirmation

`strategy.require_candle_confirmation` is `true`, so signals are gated on
`candle_score`, and `strategy/candles.py` reads `Open` in every pattern it tests.
With `Open ≡ previous Close` the patterns stop being candle geometry:

| Pattern | Cited in 684 trades | What it actually tests now |
|---|--:|---|
| Doji | **455 (66.5%)** | today closed near yesterday's close |
| Morning Star | 181 (26.5%) | a comparison of lagged returns |
| Hammer | 51 (7.5%) | wicks measured against a fake body |
| Bullish Engulfing | 5 (0.7%) | needs `Open < prev Close`; unreachable when equal |
| Bullish Harami | 0 | needs `Open > prev Close`; unreachable from the other side |

**Two of the five patterns are structurally dead, not merely distorted.**
Engulfing at `candles.py:22` requires `last["Open"] < prev["Close"]` and Harami
at `:69` requires `last["Open"] > prev["Close"]`; with the two quantities
identical, both reduce to `x < x` and `x > x`. Engulfing's 5 citations in 684 are
not counter-evidence — they are the residue of the 2.6% of bars where the
carry-forward did not happen.

`Hammer` is worse than distorted: it computes
`lower = min(Close, Open) - Low`, which goes **negative** on the third of traded
bars where the fabricated open sits below the bar's own low.

A parallel sweep of `candle_score` over 148,240 sessions of the cache puts the
firing rates at Doji 19.85%, Morning Star 2.13%, Hammer 1.40%, Harami 0.014%,
Engulfing 0.005% — Doji alone is 85% of all non-zero candle score.

The most-cited confirmation in the entire record — Doji, on two thirds of trades
— is really "the close barely moved from yesterday." The detector still produces
a number, and that number is part of a strategy with a positive expectancy, so it
is not obviously harmful. But it is not measuring what it is named for, and
nobody reading `Morning Star` in a signal's reasons is being told the truth.

## 5. Verdict

- **On the gap probe's central question, swing passes.** Mean hold 6.22 sessions
  against a ~3-night break-even. It is not the range scalp.
- **Its edge is roughly a third smaller than reported** once the real spread is
  charged. It stays clearly positive.
- **Its entries are sound; its confirmation layer is not.** Fixing the `open`
  field will change which signals fire, so the backtest will need re-running.
  That re-run **cannot currently be performed**: every historical open in the
  cache is fabricated, so there is nothing to correct toward;
  `rubix_live_market.db` only reaches back to 2026-07-01; and the EODHD seed is
  no substitute, since its opens are carried forward too and merely clipped into
  range so the impossible-bar test cannot see it. The honest status of these
  numbers is **cannot currently be validated**, not *provisional*.
- **Three other execution paths enter at the fabricated open** — the two
  `execution_delay_bars = 1` callers and the entire breakout backtest — and
  should be re-run once a real source exists.

## Limits

- Spreads come from 16 sessions in August 2026 and are applied to six years of
  trades.
- The holding-bucket table is confounded by the exit rule and is reported here
  only to place the mean hold against the break-even, not to argue for longer
  holds.
- The candle finding says the detector is mislabelled. It does not establish that
  removing or fixing it would improve results — that needs a re-run.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/probe_swing_strategy.py
```
