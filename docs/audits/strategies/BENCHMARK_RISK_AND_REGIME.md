# The Benchmark's Own Risk, and the One Regime Where the Strategy Wins

**Question:** [EXPOSURE_VS_DRIFT.md](EXPOSURE_VS_DRIFT.md) compared return and
nothing else, and concluded the objective needs reconsidering. That conclusion
means *abandon* if buy-and-hold also wins on risk, and *re-specify* if it does
not. So: give the benchmarks the identical treatment the strategies get, and
look for any slice where the strategy wins.

**Status:** measurement. No strategy module changed, no parameter tuned. The
findings of `48582b3` stand and are not re-litigated.

**Short answer:** the strategy's drawdown really is a third of the benchmark's —
**−18.27% against −51.35%** — and it is worth nothing. Risk-adjusted it still
loses (Calmar 0.30 against 0.47, Sharpe 0.52 against 1.33), it spent **longer**
underwater (1,448 days against 882), and **in dollars the drawdown advantage
disappears entirely**: −65.29% against −66.38%. The defensive profile is an
artifact of measuring in a currency that fell.

One thing does survive. In the **BEAR** regime, 377 sessions and 97 trades, the
strategy lost **−44.50%** where the market lost **−91.73%**. That is the only
slice with an adequate sample where it wins, and it wins by losing less.

---

## 1. Task D — both sides, same axes, EGP

| line | return | CAGR | max DD | longest DD | worst day | worst 21 | Sharpe | Sortino | **Calmar** |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| **STRATEGY shipped** | +61.80% | 5.55% | **−18.27%** | **1,448 d** | **−6.79%** | **−9.96%** | 0.52 | 0.62 | **0.30** |
| (a) equal-weight universe, held | +547.66% | 23.35% | −51.24% | 880 d | −8.28% | −29.73% | 1.29 | 1.38 | 0.46 |
| (b) median EGX name, held (ex-post) | +320.59% | 17.51% | −53.62% | 1,976 d | −14.68% | −25.68% | 0.69 | 0.57 | 0.33 |
| (c) the names this run traded, held | +587.42% | 24.18% | −51.35% | 882 d | −8.20% | −29.73% | 1.33 | 1.45 | **0.47** |

The strategy is genuinely the calmer instrument on every raw risk measure: a
third of the drawdown, a fifth of the worst 21 sessions, a smaller worst day.

**And it loses on every risk-adjusted one.** Calmar 0.30 against 0.47, Sharpe
0.52 against 1.33, Sortino 0.62 against 1.45. It bought a third of the drawdown
with a tenth of the return, which is not a trade worth making. It was also
underwater **566 days longer** than the thing it was protecting against.

The curve here returns +61.80% against the published +60.08%; the published
figure is the run's own realised total and this is the marked daily curve
rebuilt from its trade file, so they differ by the mark on the last open
positions. Both are reported rather than reconciled away.

## 2. Task D — the same, in USD

| line | return | CAGR | max DD | longest DD | worst day | worst 21 | Sharpe | Calmar |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| **STRATEGY shipped** | **−41.85%** | −5.91% | **−65.29%** | 1,748 d | −37.22% | −39.26% | −0.21 | **−0.09** |
| (a) equal-weight universe, held | +132.76% | 9.96% | −65.58% | 1,767 d | −37.82% | −43.36% | 0.55 | 0.15 |
| (b) median EGX name, held | +51.15% | 4.75% | −60.35% | 2,351 d | −37.67% | −41.11% | 0.32 | 0.08 |
| (c) the names this run traded, held | +147.05% | 10.70% | −66.38% | 1,767 d | −37.58% | −42.95% | 0.58 | 0.16 |

**The drawdown advantage does not exist in dollars.** −65.29% against −66.38% is
a rounding, not a defence. The gap in EGP came from holding cash in a currency
that was falling — cash is not a safe asset when the unit is the risk.

**The one-line answer to Task D:** no. The strategy does not deliver a
materially better Calmar than the thing that beat it on return — 0.30 against
0.47 in EGP, and −0.09 against 0.16 in dollars. Its lower drawdown is real in
EGP, worthless in USD, and does not survive being divided by the return it cost.

## 3. Task E — by calendar year

Strategy against the equal-weight universe held. `too small` marks fewer than 30
entries in the slice.

| year | strategy | benchmark | strat DD | bench DD | beats on return | better DD | trades | too small |
|---|--:|--:|--:|--:|:-:|:-:|--:|:-:|
| 2017 | +11.32% | +20.12% | −3.79% | −6.16% | no | yes | 24 | **yes** |
| 2018 | +1.62% | −12.40% | −4.71% | −28.21% | **yes** | yes | 28 | **yes** |
| 2019 | −4.59% | −7.18% | −10.92% | −22.27% | **yes** | yes | 28 | **yes** |
| 2020 | +1.55% | +51.03% | −8.88% | −25.18% | no | yes | 37 | no |
| 2021 | −0.46% | +8.76% | −6.32% | −40.38% | no | yes | 38 | no |
| 2022 | −1.32% | +5.89% | −9.22% | −27.01% | no | yes | 34 | no |
| 2023 | +17.80% | +61.32% | −12.86% | −11.54% | no | no | 68 | no |
| 2024 | +2.05% | +17.44% | −10.76% | −24.59% | no | yes | 71 | no |
| 2025 | +16.18% | +63.87% | −11.49% | −6.09% | no | no | 111 | no |
| 2026 | +5.67% | +14.08% | −6.46% | −9.25% | no | yes | 45 | no |

It beats on return in **two years of ten**, 2018 and 2019, and **both are marked
too small** at 28 trades each. It has the better drawdown in **eight of ten**,
which is the same defensive profile §1 measured and §2 dissolved.

## 4. Task E — by regime

`reports/phase11_market_regimes.csv` covers **2020-08-09 → 2026-06-09**, 1,241
sessions — not the whole window. Regime days are not contiguous, so daily
returns inside each label are compounded rather than taken endpoint to endpoint.

| regime | sessions | trades | strategy | benchmark | beats | too small |
|---|--:|--:|--:|--:|:-:|:-:|
| **BEAR** | 377 | **97** | **−44.50%** | **−91.73%** | **yes** | no |
| HIGH_VOLATILITY | 25 | 6 | +2.71% | −0.61% | yes | **yes** |
| SIDEWAYS | 95 | 25 | −6.28% | +5.42% | no | **yes** |
| STRONG_BULL | 15 | 2 | +6.75% | +28.34% | no | **yes** |
| BULL | 223 | 78 | +87.82% | +864.09% | no | no |
| WEAK_BULL | 506 | 165 | +57.67% | +1,213.59% | no | no |

**BEAR is the finding.** 97 trades, above the threshold, and the strategy lost
47 percentage points less than the market. HIGH_VOLATILITY also wins and is 6
trades, which carries nothing.

Read plainly: this system is not a return generator that underperforms. It is a
**drawdown reducer that gives up almost all of the upside** — it captured 57.67%
of a 1,213.59% WEAK_BULL and 87.82% of an 864.09% BULL, and in exchange it
halved the BEAR.

## 5. Task F — a benchmark somebody could have bought

Top N by median traded value in the **twelve months before** the window opens.
No forward information. Bought once at the open, never rebalanced, paying each
symbol's own measured round trip on entry.

| basket | names | EGP total | EGP CAGR | max DD | Calmar | USD total | USD CAGR | worst-case EGP |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| ex-ante top 10 | 10 | +297.00% | 16.75% | −67.94% | **0.25** | +42.68% | 4.07% | +209.50% |
| ex-ante top 20 | 20 | +284.20% | 16.33% | −66.08% | **0.25** | +38.07% | 3.69% | +199.52% |
| ex-ante top 30 | 30 | +385.02% | 19.41% | −65.67% | **0.30** | +74.31% | 6.44% | +278.12% |
| **strategy, for comparison** | — | **+61.80%** | **5.55%** | **−18.27%** | **0.30** | **−41.85%** | **−5.91%** | — |

The worst-case column writes 22.0% of each basket to −100%, the delisting rate
established in [EXPOSURE_VS_DRIFT.md](EXPOSURE_VS_DRIFT.md) §4 (54 of 245 listed
names have no prices). It is a bound, not a measurement: the basket is chosen
from names that survived, so the selection itself cannot see the delisted ones.

**Does the gap survive an investable benchmark? On return, yes and by a
distance** — the worst basket under the worst delisting assumption still
returned +199.52% against +61.80%, and +38.07% in dollars against −41.85%.

**On Calmar it does not.** The strategy's 0.30 matches the top-30 basket exactly
and beats top-10 and top-20 at 0.25. Against the untradable equal-weight index
its Calmar looked poor; against the benchmark a person could actually have
executed, it is competitive. That is the strongest thing in this document in the
strategy's favour, and it is a tie rather than a win.

**EGX30:** no index history from any provider
([DAILY_STRATEGY_DIAGNOSIS §7](DAILY_STRATEGY_DIAGNOSIS.md)). Reported as None,
not approximated.

## Limits

- Regime labels cover 5.8 of the 8.9 years and are themselves a model output,
  not an observation.
- 2018 and 2019, the only two years the strategy wins on return, carry 28 trades
  each and are marked too small. The BEAR regime slice carries 97 and is not.
- The ex-ante baskets are selected from names that survived to be in the panel;
  the delisting haircut is applied afterwards as a bound and cannot correct the
  selection itself.
- Egyptian CPI is still absent. USD carries the currency effect; it is not a
  real-return series.
- Buy-and-hold pays one round trip per name and is never rebalanced. Dividends
  are excluded on both sides, consistently.
- One dataset, one universe. Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/benchmark_risk_profile.py
```

---

## Which of the three

**The third: the strategies win in identifiable regimes, so the product is a
regime-conditional overlay rather than a standalone system.**

Not the first. "Buy-and-hold wins on return and on risk" is true on every
aggregate measure and it is not the whole record: there is a slice with 97
trades where the strategy lost half what the market lost, and against an
investable benchmark its Calmar ties. Declaring the objective dead would
discard that.

Not the second either. The strategy does **not** win clearly on drawdown or
Calmar once the numbers are risk-adjusted or restated in dollars — Calmar 0.30
against 0.47, and a USD drawdown identical to the benchmark's. Re-specifying the
objective as "risk-adjusted return" would fail on its own terms.

What the evidence actually supports is narrow and should be stated narrowly.
This system reduces drawdown in falling markets and forfeits most of the upside
in rising ones. Over a window that was overwhelmingly rising, that is a losing
trade, and no amount of parameter work changes the arithmetic. It is only worth
anything as an **overlay conditioned on a regime nobody in this repository can
identify in advance** — the regime labels are a model output, the market gate
that was supposed to detect regimes has never fired
([DAILY_STRATEGY_DIAGNOSIS §7](DAILY_STRATEGY_DIAGNOSIS.md)), and a
regime-conditional product whose regime detector does not work is not a product.

So the honest next step is not more strategy work and not abandonment. It is to
establish whether the BEAR advantage can be detected **at the time** rather than
in labels assigned afterwards. If it cannot, the first outcome applies after all
and should be recorded as such.

**Carried forward, still unspent:** the rotation experiment's random-selection
control, recorded in [EXPOSURE_VS_DRIFT.md](EXPOSURE_VS_DRIFT.md).
