# Why the Score Does Not Rank

**Question:** net profit does not improve with score. Which part of the score is
responsible?

**Status:** diagnosis only. Read-only, measured on the corrected isolated
baseline (`reports/experiments/20260826_070634_baseline_corrected_costs`, 638
trades). Nothing changed. A fix would touch `strategy/decision_engine.py`, which
is sealed — but the components themselves are **not** sealed, which matters for
what a fix would cost.

**Short answer:** the score is a sum of seven parts. One can never be non-zero,
one is at its ceiling for four trades in five, one is degenerate from the
fabricated `Open`, two are noise, and the only one carrying a measurable signal
points backwards.

---

## 0. First, a correction

Earlier in this investigation I said the scoring system is **inversely** related
to profit, based on decile tables that sloped downward. That was too strong.

Measured properly: **r = -0.032, t = -0.81.** That is *no signal*, not inversion.
The downward-sloping deciles were noise in coarse buckets, and I read a trend
into them. The correct statement is that the score carries no information about
the outcome — which is bad enough, and is a different claim.

The practical consequence is unchanged: **`min_score` is not a useful lever**,
because raising a threshold on a field with no signal selects nothing.

## 1. The components

| Component | r | t | Verdict |
|---|--:|--:|---|
| `volume_score` | **-0.090** | **-2.27** | **inverted** |
| `trend_score` | +0.057 | +1.43 | no signal |
| `candle_score` | -0.024 | -0.61 | no signal |
| `momentum_score` | +0.004 | +0.09 | no signal |
| `breakout_score` | — | — | **constant** |
| unrecorded (support + entry) | +0.012 | +0.29 | no signal |
| **`score` (total)** | **-0.032** | **-0.81** | **no signal** |

And their distributions, which explain the table above better than the
correlations do:

| Component | Distinct values | At its maximum | At zero |
|---|--:|--:|--:|
| `breakout_score` | **1** | 100% | **100%** |
| `volume_score` | 4 | 38.4% | 0% |
| `trend_score` | 6 | **78.1%** | 0% |
| `candle_score` | 8 | 0.2% | 0% |
| `momentum_score` | 11 | 8.2% | 0% |

The total ranges 52 to 104 with a mean of 77 and a standard deviation of 9.9.
Most of a 100-point scale is never used, and most of the variation that does
exist comes from components that do not predict.

## 2. `breakout_score` can never fire

It is **0.000 for all 638 trades** — a single distinct value across the whole
record. It is summed into the score, into the confidence, and into the reasons,
and it is always zero.

This is not a weak signal. It is a structural contradiction between two gates:

- `breakout_score` rewards `Close` above the 20-bar high, or within 2% of it.
- `quality_filter` requires **at least 3% of room below resistance**
  (`quality_min_resistance_room: 3.0`), and `require_quality_filter` is `true`.

A bar cannot be within 2% of resistance and more than 3% below it. Measured over
10,373 real bars across five liquid symbols:

| | Bars | Share |
|---|--:|--:|
| `breakout_score > 0` | 1,803 | 17.4% |
| Quality room ≥ 3% | 7,975 | 76.9% |
| **Both at once** | **119** | **1.15%** |

Independence would predict 13.4%. Observed is **1.15%** — twelve times rarer.
The two are not merely uncorrelated; they are near-exclusive by construction. Add
the remaining gates and the survivors are zero.

**Twenty of the score's points are unreachable.** The strategy has been ranking
on an 80-point scale while believing it had 100, and one of its seven opinions
has never once been consulted.

## 3. `trend_score` cannot discriminate

**78.1% of trades sit at its maximum of 30**, across only six distinct values. For
four trades in five it is a constant, so it cannot separate them. Its weakly
positive correlation comes entirely from the fifth.

That is a saturation problem, not a weighting problem — reweighting a constant
changes nothing.

## 4. `candle_score` is the fabricated `Open` again

63% of trades carry exactly 5.0, which is the "Doji" branch —
`|Close - Open| ≤ 10% of range` where `Open` is the previous close, so it fires
on any day that closed near yesterday's close. Documented in
[SWING_STRATEGY_PROBE.md §4](SWING_STRATEGY_PROBE.md). It is degenerate rather
than inverted: r = -0.024.

## 5. The one signal points backwards

`volume_score` is the only component whose correlation clears significance, at
**r = -0.090, t = -2.27**, and it is negative: higher volume score, worse trade.
Its own input agrees — `volume_ratio` sits at r = -0.053.

Meanwhile the only field with a positive signal is one the score does not
contain: **`atr_percent`, r = +0.080, t = +2.03.** More volatile trades did
better.

**Caveat, and it matters:** about twenty fields were tested. At t ≈ 2 one false
positive is expected by chance, so both of these are marginal and neither should
be acted on from this run alone. They are directions to test, not findings.

## 6. What a fix would cost

The seal covers `strategy/decision_engine.py` — the aggregator. It does **not**
cover `strategy/breakout.py`, `trend.py`, `volume.py`, `support.py`, `entry.py`,
`momentum.py` or `candles.py`. The components are free; only their summation is
sealed.

So:

- **Removing a component from the score requires breaking the seal** (it is
  summed in `decision_engine.py`).
- **Repairing a component does not.** Fixing `trend_score`'s saturation, or
  rescaling `volume_score`, is unsealed work.
- **`quality_min_resistance_room` is config.** Lowering it would let
  `breakout_score` fire — but that changes what the strategy *is*, from "buy with
  room to run" to "buy breakouts", and those are opposite philosophies. The
  contradiction should be resolved deliberately, not by tuning a number.

## 7. Recommendation

1. **Decide what the strategy is.** The breakout component and the quality filter
   encode incompatible theses. One of them should go. This is a design decision,
   not a measurement.
2. **Do not touch `min_score`.** No signal means no threshold helps.
3. **Fix saturation before weights.** A component at its ceiling for 78% of trades
   cannot be reweighted into usefulness.
4. **Treat `atr_percent` as a hypothesis**, not a finding, and test it properly.
5. Nothing here makes the strategy profitable. Both exit configurations lose after
   real costs ([TRAILING_STOP_VERDICT.md](TRAILING_STOP_VERDICT.md)); this
   explains why selection was never going to rescue them.

## Limits

- One run, one universe, in-sample.
- Multiple comparisons, as noted in §5.
- `support` and `entry` contributions are not recorded per trade and were
  measured only as the residual of the total.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/diagnose_score.py \
  reports/experiments/20260826_070634_baseline_corrected_costs
```
