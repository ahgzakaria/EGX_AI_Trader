# Exposure, Drift, and What the Benchmark Says

**Question:** three documents in this repo report the same pattern without
naming it — every reduction in market exposure destroys return. If exposure is
the dominant axis, return should track it across completed runs, and no
low-exposure configuration should beat buy-and-hold. Is that what the record
shows?

**Status:** measurement. No strategy module was changed and no parameter was
tuned. Task 1's benchmark block (`38e7417`) is reused, not rebuilt.

**Short answer:** exposure is not the axis, and the axis it is not is the wrong
question. **Nothing in this repository beats buy-and-hold — 0 of 34
configurations**, at exposures from 7.8% to 125%, losing by between 425 and 728
percentage points. Within the daily strategy's own runs return is *mildly
negative* in exposure, not positive. And restated in dollars the direction
reverses: buy-and-hold **preserved purchasing power** (+132.78% USD) while the
shipped strategy **lost 42% of it**.

---

## 1. The decomposition — 31 archived runs and 3 module modes

Every run in `reports/experiments/` re-priced from its own trade file. Drawdowns
are marked to market for all 31, including the 25 whose published figure was a
closed-trade number.

Both benchmarks pay costs the way the strategy does: one round trip per name at
that symbol's own measured spread from `TradingCosts(symbol=...)`, mean 0.923%
across the traded names. Buy-and-hold is not rebalanced, so it pays that once
and nothing after — stated rather than assumed.

| run | return | CAGR | exposure | peak deployed | marked DD | Sharpe | CAGR/exp | BH own names | vs own |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| baseline_corrected_costs | +68.62% | 8.38 | 76.13% | 153.7% | 39.21% | 0.48 | 0.110 | +735.36% | **−666.74** |
| spread1.0 | +75.67% | 6.53 | 22.39% | 163.3% | 16.21% | 0.70 | **0.292** | +598.61% | −522.94 |
| spread0.5 | +48.02% | 4.50 | 16.10% | 128.0% | 16.22% | 0.52 | 0.280 | +617.05% | −569.03 |
| **reward_guard (shipped)** | **+60.08%** | **5.43** | **23.59%** | 157.8% | **18.27%** | 0.55 | 0.230 | +586.38% | −526.30 |
| spread0.3 | +14.73% | 1.57 | 7.80% | 114.7% | 26.89% | 0.32 | 0.201 | +440.42% | −425.69 |
| code_defaults_config | −36.23% | −4.90 | 25.78% | 104.9% | 46.85% | −0.16 | −0.190 | +608.46% | −644.69 |

Full table: `reports/audits/strategies/exposure_vs_drift.csv` (31 rows).
That directory is git-ignored like the rest of `reports/`, so the data
regenerates from the reproduce commands below and this document — which is
the tracked artifact — carries the numbers it rests on.

**The module outside `reports/experiments/`**, from
`reports/phase10_breakout_trades.csv`:

| mode | trades | window | return | CAGR | exposure | CAGR/exp | BH own names | vs own |
|---|--:|---|--:|--:|--:|--:|--:|--:|
| BREAKOUT_SWING | 843 | 2020-08 → 2026-07 | +161.25% | 17.64 | 124.99% | 0.141 | +585.61% | **−424.36** |
| CLASSIC_STRATEGY | 729 | 2020-08 → 2026-06 | +66.43% | 9.13 | 45.46% | 0.201 | +544.56% | −478.13 |
| COMBINED_STRATEGIES | 746 | 2020-08 → 2026-07 | +10.62% | 1.72 | 85.20% | 0.020 | +582.91% | −572.29 |

### Reconciliation with the published record

The shipped run reproduces `DAILY_STRATEGY_DIAGNOSIS` §2 exactly, which is the
check that makes the rest of this table readable:

| | this measurement | published |
|---|--:|--:|
| window | 2017-08-03 → 2026-06-28, 8.9 yrs | 8.9 yrs |
| strategy | +60.08%, CAGR 5.43% | +60.08%, CAGR 5.43% |
| exposure | 23.59% | 23.6% |
| marked drawdown | 18.27% | 18.27% (§10) |
| median name, market-wide | **+319.47%** | **+320.6%** |

## 2. The three questions

**1. Does any run beat buy-and-hold of its own traded names?**
No. 0 of 31 archived runs and 0 of 3 module modes. The best gap is −425.69
percentage points and the worst is −727.64. Against the full universe, also 0
of 34.

**2. Is return monotone in average exposure?**
No, and the sign is the other way. Across the 31 archived runs, Pearson
r = **−0.098** and Spearman rho = **−0.372** between average exposure and total
return. **This is a description of 31 points, not a law**, and the points are
not independent: they are one strategy family under different parameters, four
of them returning the identical +60.08%, so the effective sample is nearer a
dozen. The honest statement is that exposure does not order these runs — not
that lower exposure pays.

**3. Is the momentum-breakout advantage explained by exposure?**
Yes, entirely, and it is worse than that — efficiency *falls* as exposure rises:

| | exposure | CAGR | CAGR per unit of exposure |
|---|--:|--:|--:|
| daily, shipped | 23.59% | 5.43 | **0.230** |
| CLASSIC_STRATEGY | 45.46% | 9.13 | 0.201 |
| BREAKOUT_SWING | 124.99% | 17.64 | **0.141** |

BREAKOUT_SWING earns three times the daily strategy's CAGR from five times the
exposure. Its advantage is bought, not selected, and none of it survives the
benchmark: −424.36 points against holding the same names.

**A note on what `ExposurePercent` is.** It is capital-weighted, not
time-in-market, and it exceeds 100% — BREAKOUT_SWING sits at 125%. Separately,
**23 of 31 archived runs peak above 100% of equity deployed** (median 112%,
maximum 163%), so "23.6% exposure" describes an account that was periodically
committed at more than one and a half times its own equity. The two numbers
measure different things and neither means the account was mostly in cash.

## 3. In dollars

`EGP=X`, daily, over the shipped window. EGP per USD went **17.819 → 49.493**: a
pound at the end buys **64.0% less** than at the start.

| | EGP total | USD total | USD CAGR |
|---|--:|--:|--:|
| daily strategy, shipped | +60.08% | **−42.37%** | **−6.00%** |
| best archived run (spread1.0) | +75.67% | −36.75% | −5.02% |
| worst archived run | −36.23% | −77.04% | −15.24% |
| buy-and-hold, names the strategy traded | +586.38% | **+147.11%** | +10.70% |
| buy-and-hold, full universe equal weight | +546.57% | **+132.78%** | +9.96% |
| buy-and-hold, median name (§2's figure) | +319.47% | +51.02% | +4.74% |
| survivorship worst case, full universe | +404.06% | +81.47% | +6.92% |

**Did buying and holding EGX preserve purchasing power? Yes.** Equal-weight
more than doubled in dollars; even the median single name gained 51%; even the
survivorship worst case gained 81%. **The strategy did not.** Every archived
configuration lost dollar value — the best of them by 37%, the shipped one by
42%.

This is the finding that changes what the rest of the repository means. A
profit factor of 1.29 and +60.08% describe a decade in which the account's real
value fell by more than a third.

## 4. Survivorship, bounded rather than chased

`eodhd_egx_delisted_20260730T203942Z.json` names 65 delisted symbols and carries
no prices. 11 have prices in hand; the other **54 are bounded, not measured**.

| assumption | full-universe equal-weight buy-and-hold |
|---|--:|
| as reported (survivors only) | +546.57% |
| neutral: each delisted name at the surviving median | +496.52% |
| **worst case: each delisted name to −100%** | **+404.06%** |

**Does the gap survive the worst case? Yes, with room.** Writing off every one
of the 54 unpriced names entirely still leaves buy-and-hold at +404.06% against
the shipped strategy's +60.08% — a gap of 344 points, and +81.47% against
−42.37% in dollars. **Survivorship is settled and needs no further work.** The
bias is real, it is smaller than the gap by an order of magnitude, and chasing
54 delisted price series would not change a conclusion this size.

## Limits

- One dataset, one universe. The 31 archived runs are one strategy family under
  different parameters, not 31 independent experiments.
- Egyptian CPI is **not** in this document. The exchange rate carries most of
  the currency effect and needs no external source; a real-EGP restatement
  would need a CPI series this repository does not have, and the USD figures
  are not a substitute for one.
- Buy-and-hold here is not rebalanced and pays one round trip per name. A
  rebalanced equal-weight basket returns more and would owe turnover costs
  nobody would pay; that version is reported in `BacktestStatistics` as
  `BenchmarkEqualWeightReturn` and is explicitly untradable.
- The worst case in §4 is deliberately harsher than reality: a delisting is not
  always a wipeout and some of the 65 are mergers.
- `peak_deployed` is reconstructed from trade files at `shares × entry_price`,
  not read from a simulator field.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/exposure_vs_drift.py
venv/Scripts/python.exe scripts/research/exposure_usd_and_survivorship.py
```

---

## What this decides about the rotation experiment

Of the three possible outcomes, the third occurred: **nothing beats
buy-and-hold at any exposure.**

Not the first — exposure does not explain the ranking, it is mildly negative
within the family and the highest-exposure module is the least efficient per
unit of it. Not the second — the momentum breakout's advantage is entirely
exposure and vanishes against its own benchmark by 424 points.

That does not by itself kill the rotation experiment, and it does change what
would count as success. An always-invested rotation captures the drift **by
construction**, so it will beat every run in §1 and that will mean nothing. The
bar is buy-and-hold of the same universe over the same window — +546.57% EGP,
+132.78% USD — and the honest prior after this document is that a rotation
built on a +1.36% lift will land near it rather than above it.

**The objective should be reconsidered before more strategy work.** The
question this repository has been answering is "which rule trades best". The
measurement says the rule that trades best still loses to not trading, and
loses money outright in dollars. The question worth answering next is whether
any active configuration clears buy-and-hold **after costs and in real terms** —
and if the answer is no, the useful product of this work is the benchmark, the
cost model and the liquidity work, not a strategy.

## Followed up

The risk side of this comparison, the regime decomposition and an investable
benchmark are measured in
[BENCHMARK_RISK_AND_REGIME.md](BENCHMARK_RISK_AND_REGIME.md). In short: the
strategy's drawdown is a third of the benchmark's and worth nothing once
risk-adjusted or restated in dollars, and the only slice it wins with an
adequate sample is the BEAR regime, where it lost 44.50% against the market's
91.73%.

**Carried forward for the rotation experiment when it is authorised:** it needs
a **random-selection control** holding the same N names with the same turnover
and the same costs. An always-invested system captures market drift whether or
not its signal works, so a result of "+280% against +320% buy-and-hold" cannot
be read as signal or noise without knowing what random selection returned under
identical constraints. Without that control the experiment cannot answer its
own question.
