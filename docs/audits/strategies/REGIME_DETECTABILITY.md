# The Regime Is Detectable. It Does Not Help Enough.

**Question:**
[BENCHMARK_RISK_AND_REGIME.md](BENCHMARK_RISK_AND_REGIME.md) found one slice
where the shipped strategy wins with an adequate sample — the BEAR regime, 97
trades, losing 44.50% where the market lost 91.73% — but measured it against
labels that are model output. Is the edge detectable at the time, and does the
overlay have anywhere to stand when it is off?

**Status:** measurement. No strategy module changed, no parameter tuned. The
findings of `48582b3` and `6e3ea10` stand.

**Short answer:** the regime **is** detectable causally. A proxy index below its
own 200-day average flags the labelled BEAR with **89.5% precision at a one-day
entry lag**, built only from data each day already had. Gating on it improves the
strategy in pounds — Calmar **0.30 → 0.61**, drawdown **−18.27% → −10.99%** — and
it beats a random gate of the same duty cycle at the **82nd to 91st percentile**.

**And every gated variant fails the bar.** The bar was USD Calmar above the
buy-and-hold basket's **+0.10**. The best of twenty gated configurations reaches
**−0.04**. Holding dollars rather than pounds while gated off is the single
largest improvement available and still does not clear it. The constraint is not
the signal.

---

## 1. Task G — causal detectors from the panel

The proxy is an equal-weight daily index of the liquid half of the panel, chosen
on traded value in the twelve months **before** the window opens. Detector 1 was
recomputed on half the history and agreed with itself on every overlapping date:
**causality check PASS**.

Agreement with `phase11_market_regimes.csv` is a diagnostic. The labels are the
thing under suspicion, so nothing here was tuned toward them.

| detector | flagged | % of days | overlap | precision | recall | entry lag | exit lag |
|---|--:|--:|--:|--:|--:|--:|--:|
| **1. proxy below its 200d average** | 617 | 29.0% | 102 | **89.5%** | 27.1% | **+1 d** | −2 d |
| 2a. proxy drawdown > 10% | 1,023 | 48.1% | 199 | 51.0% | 52.8% | −4 d | −2 d |
| 2b. proxy drawdown > 15% | 813 | 38.3% | 153 | 66.2% | 40.6% | −3 d | −2 d |
| 2c. proxy drawdown > 20% | 600 | 28.2% | 84 | 71.8% | 22.3% | −18 d | −15 d |
| 3a. breadth > 50% below own 200d | 176 | 8.3% | 3 | 37.5% | 0.8% | +3 d | +4 d |
| 3b. breadth > 60% | 168 | 7.9% | 1 | 25.0% | 0.3% | +3 d | +3 d |
| 3c. breadth > 70% | 84 | 4.0% | 1 | 25.0% | 0.3% | +3 d | +3 d |
| 4a. realized vol above 80th pct | 390 | 18.4% | 66 | 36.5% | 17.5% | +3 d | +3 d |
| 4b. realized vol above 90th pct | 227 | 10.7% | 38 | 40.4% | 10.1% | +5 d | 0 d |
| 5. value-weighted proxy below 200d | 602 | 28.3% | 81 | 69.8% | 21.5% | +1 d | 0 d |

**Detector 1 is the finding of this section.** Nine times in ten, a day it flags
is a day the label calls BEAR, and it gets there one session late. The
`^CASE30` outage that [DAILY_STRATEGY_DIAGNOSIS §7](DAILY_STRATEGY_DIAGNOSIS.md)
recorded is not what was preventing regime detection — the panel was always
sufficient.

Breadth is not. All three breadth thresholds overlap the labelled BEAR on one to
three days out of 1,241; whatever they measure, it is not this.

## 2. Task H — gating the shipped run, both off-states

The panel holds 191 symbols and **every one is an EGX common share**, so there is
no defensive instrument in this project's data. The two off-states available are
EGP cash, which earns nothing, and USD cash, which is not an instrument the
project models — it is the exchange rate, assumed accessible at no cost, and
labelled as an assumption wherever it appears.

A trade is suppressed when its entry date is flagged; positions already open run
to their exits. A gate that also liquidates is a different strategy.

| line | trades | EGP return | EGP DD | **EGP Calmar** | USD return | USD DD | **USD Calmar** |
|---|--:|--:|--:|--:|--:|--:|--:|
| **(a) ex-ante top-30 basket, held** | — | +385.02% | −65.67% | 0.30 | **+74.31%** | −61.38% | **+0.10** |
| (b) ungated strategy | 484 | +61.80% | −18.27% | 0.30 | −41.85% | −65.29% | −0.09 |
| 1. below 200d \| EGP cash | 448 | +67.62% | −12.32% | 0.49 | −39.76% | −64.89% | −0.09 |
| 1. below 200d \| USD cash | 448 | +87.49% | −19.29% | 0.38 | −32.62% | −57.33% | −0.08 |
| **2a. drawdown > 10% \| EGP cash** | 371 | +78.27% | **−10.99%** | **0.61** | −35.93% | −62.40% | −0.08 |
| **2a. drawdown > 10% \| USD cash** | 371 | **+141.93%** | −17.14% | **0.61** | **−13.06%** | **−41.83%** | **−0.04** |
| 2b. drawdown > 15% \| EGP cash | 402 | +69.22% | −12.43% | 0.49 | −39.19% | −64.24% | −0.08 |
| 2c. drawdown > 20% \| USD cash | 425 | +49.68% | −19.70% | 0.24 | −46.21% | −64.13% | −0.10 |
| 3b. breadth > 60% \| EGP cash | 472 | +68.18% | −12.68% | 0.47 | −39.56% | −65.54% | −0.08 |
| 4b. vol above 90th \| USD cash | 451 | +84.62% | −12.10% | **0.59** | −33.65% | −61.87% | −0.07 |
| 5. value-weighted \| EGP cash | 438 | +67.91% | −12.24% | 0.49 | −39.66% | −64.70% | −0.09 |

Full table, all twenty variants: `reports/audits/strategies/regime_gated_overlay.csv`.
No cell is below 30 trades.

**In pounds the gate works.** The best variant doubles Calmar, cuts the drawdown
by nearly half, and raises the return while taking 113 fewer trades.

**In dollars not one variant is positive**, and the best of twenty reaches −0.04
against a bar of +0.10. Holding dollars while gated off is worth a great deal —
2a goes from +78.27% to +141.93% in EGP and from −35.93% to −13.06% in USD, and
its USD drawdown falls from −62.40% to −41.83% — and it is still not enough.

## 3. Task H(c) — against a random gate of the same duty cycle

100 iterations each, same fraction of days off, same costs.

| detector | duty cycle off | real USD Calmar | random median | random best | percentile of real | beats random | clears the bar |
|---|--:|--:|--:|--:|--:|:-:|:-:|
| 1. proxy below 200d | 29.0% | −0.09 | −0.110 | −0.07 | **82.0** | yes | **no** |
| 2b. drawdown > 15% | 38.3% | −0.08 | −0.110 | −0.07 | **91.0** | yes | **no** |
| 3b. breadth > 60% | 7.9% | −0.08 | −0.095 | −0.08 | **88.0** | yes | **no** |

The gate is **not** a random reduction in exposure. It sits in the top fifth of
its own null distribution on every detector tested, and detector 2b at the 91st
percentile. That is real detection.

It is also insufficient. The random gate's *best* draw out of 100 reaches −0.07,
and the buy-and-hold bar is +0.10 — **the entire null distribution and the real
gate are on the same side of the bar, and it is the wrong side.** No gate of this
duty cycle on this strategy can reach the benchmark, because the shortfall is not
noise being removed. It is upside never captured.

## Limits

- One dataset, one universe, one strategy configuration.
- The proxy's liquid half is fixed at window open rather than re-selected
  annually; a rolling membership would be more faithful and is not what was run.
- USD cash is an exchange rate, not an instrument this project models. No
  interest, no spread, no access constraint is charged against it. It flatters
  the USD-cash variants and they still fail.
- The gate suppresses entries only. A gate that liquidates open positions is a
  different strategy and was not tested.
- Detector agreement is measured against labels that cover 2020-08 to 2026-06,
  5.8 of the 8.9 years.
- Egyptian CPI is still absent. USD carries the currency effect and is not a real
  return.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/regime_detectability.py
```

---

## Which of the three

**The third: the detector works, and the USD advantage disappears anyway. The
constraint is the currency and the forfeited upside, not the signal — and it is
not fixable by more strategy work.**

Not the first. The overlay does not clear the bar that was set before the result
was known: USD Calmar above +0.10. Twenty variants, best −0.04.

Not the second. The advantage is **not** hindsight. A detector reading only the
past flags the labelled BEAR with 89.5% precision at a one-day lag, and gating on
it beats a random gate of identical duty cycle at the 82nd to 91st percentile.
Recording outcome 1 from the previous document would be wrong: the regime is
detectable, and this document is the evidence.

The third option as written says "every available off-state is EGP cash". That
turned out to be almost true and worth stating precisely: the panel contains 191
EGX equities and nothing else, and the only alternative available anywhere in
this project's data is the exchange rate itself. **Holding dollars while gated
off is the largest single improvement measured in this entire investigation** —
it nearly doubles the EGP return and cuts the USD drawdown by twenty points — and
it moves USD Calmar from −0.08 to −0.04 against a bar of +0.10.

So the finding is one step stronger than the option anticipated. It is not only
that the off-state is bad. It is that **fixing the off-state entirely does not
close the gap**, because the strategy captured 57.67% of a 1,213.59% WEAK_BULL
and 87.82% of an 864.09% BULL ([BENCHMARK_RISK_AND_REGIME
§4](BENCHMARK_RISK_AND_REGIME.md)). A gate can only remove losses. It cannot
create the upside that was never taken, and the shortfall is upside.

**What this closes.** Three documents asked whether any configuration of this
work beats owning the market. The answer is no on return (0 of 34), no on
risk-adjusted return in dollars (best −0.04 against +0.10), and no under a
detector that demonstrably works. The remaining question is not a strategy
question. It is whether an EGP-denominated equity system is the right object at
all when the unit itself lost 64% against the dollar over the window — and that
is answered by choosing a different objective, not a different rule.

## Followed up — and terminated

The one remaining diagnosis, that the exit was cutting winners short, was tested
in [EXIT_WIDTH_UNDER_A_WORKING_GATE.md](EXIT_WIDTH_UNDER_A_WORKING_GATE.md) and
failed. Widening the trailing stop to disabled moved upside capture from 6.1% to
6.5%, and the best variant reached USD Calmar -0.04 against the +0.10 bar.

**That was the final strategy experiment. The program is terminated** and the
whole investigation is consolidated in
[INVESTIGATION_SUMMARY.md](INVESTIGATION_SUMMARY.md).

**Still unspent, and now unnecessary as specified:** the rotation experiment's
random-selection control ([EXPOSURE_VS_DRIFT.md](EXPOSURE_VS_DRIFT.md)). An
always-invested rotation was the remaining hope for capturing the drift the
gated forms give up. It should not be run against an EGP benchmark; if it is run
at all, the bar is the same USD Calmar of +0.10 that everything else failed.
