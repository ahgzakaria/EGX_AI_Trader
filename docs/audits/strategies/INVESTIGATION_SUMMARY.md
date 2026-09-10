# What This Investigation Asked, Measured, and Concluded

**For a reader who has seen none of the other documents.** Every number below is
measured and every source document is linked. Nothing here is a recommendation
to trade.

**The question:** does any configuration of the trading system in this
repository beat simply owning the Egyptian market, after costs?

**The answer: no.** Zero of thirty-four configurations beat buy-and-hold of the
names they themselves traded. The final experiment, which had a bar set before
it ran, missed it. **The strategy program is terminated. No further
configuration will be tested.**

Two things survive the verdict and are worth keeping. They are in §6.

---

## 1. Where it started

The system reports a profit factor of 1.29 and +60.08% over 8.9 years, and no
summary it produced contained a benchmark
([DAILY_STRATEGY_DIAGNOSIS §2](DAILY_STRATEGY_DIAGNOSIS.md)). The median EGX name
simply bought and held returned **+320.6%** over the same window.

The investigation began by asking whether that gap had an explanation.

## 2. The hypothesis that was falsified first

Three documents had reported the same pattern without naming it: **every
reduction in market exposure destroys return**. The daily strategy sits at 23.6%
exposure and returns +60.08%; the momentum-breakout module reaches 125% exposure
and returns +161.25%. If exposure were the axis, return should track it.

It does not. Across 31 archived runs, return against average exposure is
**Pearson −0.098, Spearman −0.372** — mildly negative
([EXPOSURE_VS_DRIFT.md](EXPOSURE_VS_DRIFT.md) §2). That is 31 points from one
strategy family, four of them the identical +60.08%, so it is a description and
not a law. Exposure does not order these runs.

The breakout module's higher return is bought with exposure and nothing else.
CAGR per unit of exposure **falls** the whole way: 0.230 daily, 0.201 classic,
0.141 breakout swing.

## 3. What was measured, in order

| # | Question | Answer | Document |
|---|---|---|---|
| 1 | Does any run beat buy-and-hold of its own traded names? | **0 of 34**, losing by 425 to 728 points | [EXPOSURE_VS_DRIFT](EXPOSURE_VS_DRIFT.md) |
| 2 | Does exposure explain return? | No, Pearson −0.098 | [EXPOSURE_VS_DRIFT](EXPOSURE_VS_DRIFT.md) |
| 3 | Does survivorship bias explain the gap? | No. Writing all 54 unpriced delisted names to −100% still leaves buy-and-hold at **+404.06%** against +60.08% | [EXPOSURE_VS_DRIFT](EXPOSURE_VS_DRIFT.md) §4 |
| 4 | In dollars? | Buy-and-hold **+132.78%**; the strategy **−41.85%** | [EXPOSURE_VS_DRIFT](EXPOSURE_VS_DRIFT.md) §3 |
| 5 | Does the strategy at least win on risk? | Drawdown yes (−18.27% vs −51.35%), risk-adjusted no (Calmar 0.30 vs 0.47), and in USD the advantage vanishes (−65.29% vs −66.38%) | [BENCHMARK_RISK_AND_REGIME](BENCHMARK_RISK_AND_REGIME.md) |
| 6 | Is there any slice where it wins? | One: **BEAR**, 97 trades, −44.50% against the market's −91.73% | [BENCHMARK_RISK_AND_REGIME](BENCHMARK_RISK_AND_REGIME.md) §4 |
| 7 | Against a benchmark somebody could have bought? | Return gap survives easily; **Calmar ties** at 0.30 | [BENCHMARK_RISK_AND_REGIME](BENCHMARK_RISK_AND_REGIME.md) §5 |
| 8 | Is the BEAR advantage detectable in real time, or hindsight? | **Detectable.** 89.5% precision, one session lag, split-half causality PASS | [REGIME_DETECTABILITY](REGIME_DETECTABILITY.md) §1 |
| 9 | Does gating on it clear the bar? | No. Best of twenty variants **−0.04** against **+0.10** | [REGIME_DETECTABILITY](REGIME_DETECTABILITY.md) §2 |
| 10 | Is the deficit the exit cutting winners short? | **No.** Upside capture is 6.1% with the trailing stop and 6.5% without it | [EXIT_WIDTH_UNDER_A_WORKING_GATE](EXIT_WIDTH_UNDER_A_WORKING_GATE.md) §3 |

## 4. The final experiment and its bar

The diagnosis had narrowed to one thing: the deficit is **upside never
captured** — 57.67% against WEAK_BULL's 1,213.59%, 87.82% against BULL's
864.09%. Three prior measurements pointed at the trailing stop: 81% of trades
exit on it at a mean −0.47%, tighter stops are monotonically worse in both eras,
and the causal gate now handles the downside the trail was there for.

So the trail was swept from its shipped setting to progressively wider ATR
multiples to disabled entirely, under the gate, with the hard stop untouched.

**The bar, set before the result:** in USD, over the full window, beat the
ex-ante buy-and-hold basket on Calmar (**+0.10**) and not be matched by a random
gate of the same duty cycle.

**Result: −0.04. Missed.** And the premise failed before the bar did — widening
the exit moved upside capture from 6.1% to 6.5%. Removing the trailing stop
completely bought four tenths of a percentage point. The trail was never the
constraint: a system holding a few names for a few weeks, in the market a
quarter of the year, cannot capture a move it is mostly not holding.

The random control is worth stating plainly: the real gate reached the **81st
percentile** of 100 random gates and beat their median, so the detection is
real — and **the best random draw reached +0.100, matching the bar the real gate
missed**.

## 5. Termination

**The strategy program is terminated as of this document.** The final experiment
was declared final before it ran, it missed a bar set before it ran, and no
further configuration will be tested.

What is not being claimed: that the system is badly built, or that some untried
parameter would rescue it. The costs are charged per symbol and correctly, the
drawdowns are marked to market, the capacity limits were measured and fixed, and
the one rule that survived out of sample is a real +1.36% lift. None of that was
enough, and the reason is arithmetic rather than craft — over this window the
market returned 23% a year and the system captured about 6% of the up moves.

## 6. What survives, independent of the verdict

**A causal regime detector.** A proxy index built from the panel — the liquid
half by traded value in the year before the window — flags the labelled BEAR
regime at **89.5% precision with a one-session entry lag**, reading only data
each day already had. Recomputed on half the history it agrees with itself on
every overlapping date. It needs no `^CASE30`, which the providers do not serve
and which had been treated as the reason regime detection was impossible
([DAILY_STRATEGY_DIAGNOSIS §7](DAILY_STRATEGY_DIAGNOSIS.md)). It is a reusable
component and it works. `scripts/research/regime_detectability.py`.

**EGP cash is not a defensive asset.** The system's drawdown is a third of the
market's in pounds and identical to it in dollars — **−65.29% against −66.38%**.
The apparent defence came from sitting in a currency that fell 64% against the
dollar over the window. This invalidates the reported risk profile of *any*
EGP-denominated system in this repository that defends by going to cash, not
only the one measured here. Holding dollars while defensive is the single
largest improvement measured anywhere in this investigation — EGP return +78% to
+142%, USD drawdown −62% to −42% — and it was still not enough to clear the bar,
which is itself the finding.

## 7. The limit that conditions everything

**2016–2026 contains one dominant regime: the post-float nominal repricing.**
The EGP went from 17.8 to 49.5 per dollar; the equal-weight market returned
+546.57% in pounds and +132.78% in dollars. Every conclusion in every document
here is conditioned on that single episode.

A system that is not fully invested cannot participate in a step repricing, so
"buy-and-hold wins" may be a statement about this decade rather than about
markets. **There is no more data.** The panel is 191 symbols and 382,646 bars
from 2016-07-19 to 2026-07-22, delisted names have no prices, and no index
history exists. This is a limit on the conclusions, not a task for a future
session.

## Other limits

- One dataset, one universe. The 31 archived runs are one strategy family under
  different parameters, not 31 independent experiments.
- Regime labels are a model output covering 5.8 of the 8.9 years.
- Egyptian CPI is absent throughout. USD carries the currency effect and is not
  a real-return series.
- USD cash as an off-state is charged no interest, spread or access constraint.
- Buy-and-hold pays one round trip per name and is never rebalanced. Dividends
  are excluded on both sides, consistently.

## Reproduce

```
venv/Scripts/python.exe scripts/research/exposure_vs_drift.py
venv/Scripts/python.exe scripts/research/exposure_usd_and_survivorship.py
venv/Scripts/python.exe scripts/research/benchmark_risk_profile.py
venv/Scripts/python.exe scripts/research/regime_detectability.py
venv/Scripts/python.exe scripts/research/exit_width_under_gate.py
```

## The documents, in order

1. [EXPOSURE_VS_DRIFT.md](EXPOSURE_VS_DRIFT.md) — 0 of 34, exposure falsified, survivorship bounded, USD
2. [BENCHMARK_RISK_AND_REGIME.md](BENCHMARK_RISK_AND_REGIME.md) — the benchmark's own risk, by year and regime, an investable benchmark
3. [REGIME_DETECTABILITY.md](REGIME_DETECTABILITY.md) — the causal detector, the gated overlay, the random control
4. [EXIT_WIDTH_UNDER_A_WORKING_GATE.md](EXIT_WIDTH_UNDER_A_WORKING_GATE.md) — the final experiment
