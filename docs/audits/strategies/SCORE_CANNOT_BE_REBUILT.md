# The Score Cannot Be Rebuilt From Its Own Components

**Question:** [SCORE_DIAGNOSIS.md](SCORE_DIAGNOSIS.md) measured the total score
at r = −0.032 on 638 realised trades and stopped, saying a rebuild was "real
work and is not attempted here". This is that work. Can any weighting of the
score's five surviving components rank the outcome out of sample?

**Status:** measured, and the answer is no. `strategy/` is unchanged — there is
nothing to reweight. What shipped is presentation: the Daily Dashboard no longer
implies its table is ordered by quality, and the score bar is drawn against the
118 it can actually reach rather than 100.

**Short answer:** weights fitted on 2016–2022 rank 2023–2026 at **ρ = +0.032,
p = 0.43**. The shipped weights manage **+0.012, p = 0.76**. Equal weights,
+0.017. All three are indistinguishable from ordering the candidates at random.

---

## 1. The population, which is the part most easily got wrong

Ranking is measured among the bars that reach the score — those passing the
market regime, the trend, momentum and volume gates, the reward ratio and all
four quality checks — not across the panel. **1,040 bars of 382,646, or 0.27%.**

That distinction decides the answer. A score that separates a clean breakout
from a collapsing penny stock has done nothing: the gates already did that. The
only question is whether it can order the candidates that survive them.

The target is **lift** — the twenty-session net return from the next close,
minus what an average tradeable name did on the same days. A score measured
against raw return would be measuring which month it was.

## 2. In sample it works. Out of sample it does not.

| | n | ρ | p | worst fifth | best fifth | spread |
|---|--:|--:|--:|--:|--:|--:|
| **2016–2022** | | | | | | |
| the shipped total | 436 | **+0.151** | 0.00 | −0.79% | +5.01% | **+5.80%** |
| trend | 436 | +0.186 | 0.00 | −1.64% | +4.84% | +6.47% |
| volume | 436 | −0.041 | 0.39 | +0.82% | −1.59% | −2.40% |
| support | 436 | +0.062 | 0.20 | +2.47% | +1.28% | −1.19% |
| entry | 436 | +0.030 | 0.53 | +1.28% | +1.87% | +0.59% |
| momentum | 436 | +0.162 | 0.00 | −0.65% | +6.94% | +7.58% |
| **2023–2026** | | | | | | |
| the shipped total | 604 | **+0.012** | **0.76** | +1.54% | −0.45% | **−1.99%** |
| trend | 604 | −0.009 | 0.83 | +1.51% | +1.22% | −0.28% |
| volume | 604 | −0.027 | 0.50 | +3.95% | +1.48% | −2.47% |
| support | 604 | +0.035 | 0.39 | +2.54% | +1.94% | −0.60% |
| entry | 604 | +0.082 | 0.04 | +1.22% | +2.01% | +0.78% |
| momentum | 604 | −0.017 | 0.67 | +2.09% | +2.37% | +0.28% |

The two components that carry the in-sample result — trend at +0.186 and
momentum at +0.162 — are **−0.009 and −0.017** in validation. That is what
fitting to one era looks like from the other side of the split.

In validation the shipped score's best fifth *underperforms* its worst fifth by
two points. Not a signal in the wrong direction, at p = 0.76; noise.

## 3. And no reweighting rescues it

Ordinary least squares on the five components, fitted on train only and read on
validation once. Deliberately the simplest thing that could work: if a linear
reweighting of five inputs cannot rank, a more elaborate fit on the same five is
fitting noise more thoroughly.

Fitted weight per point, and what each component is worth at its own maximum:

| Component | weight | at maximum |
|---|--:|--:|
| trend | +0.4508 | +13.52% |
| volume | **−0.2149** | −4.30% |
| support | +0.1159 | +1.74% |
| entry | +0.2976 | +8.33% |
| momentum | +0.5420 | +13.55% |

The fit agrees with [SCORE_DIAGNOSIS.md §5](SCORE_DIAGNOSIS.md) that volume
belongs with a negative sign. And it does not matter:

| | train ρ | train spread | **validation ρ** | **validation p** | validation spread |
|---|--:|--:|--:|--:|--:|
| shipped weights | +0.151 | +5.80% | +0.012 | 0.76 | −1.99% |
| **fitted weights** | +0.253 | +11.56% | **+0.032** | **0.43** | +3.53% |
| equal weights | +0.127 | +5.00% | +0.017 | 0.67 | −0.64% |

Fitting nearly doubles the in-sample correlation and doubles the decile spread.
Out of sample it buys ρ = +0.032 at p = 0.43. **The score cannot be rebuilt from
its own components**, because the components do not carry the information a
rebuild would need.

## 4. Nor is there a replacement among the indicators already computed

Twenty other fields the strategy already calculates, same population, same
target, same split:

| Feature | train ρ | validation ρ | validation p |
|---|--:|--:|--:|
| EMA20_SLOPE | +0.207 | −0.005 | 0.91 |
| RSI | +0.159 | +0.007 | 0.86 |
| ADX | +0.146 | −0.015 | 0.72 |
| EMA50_SLOPE | +0.146 | −0.007 | 0.86 |
| EMA20_DIST | +0.126 | −0.012 | 0.76 |
| MACD_HIST | +0.115 | −0.049 | 0.23 |
| BB_POSITION | +0.099 | +0.053 | 0.19 |
| **DIST_HIGH20** | **−0.051** | **−0.099** | **0.01** |
| ATR_PERCENT | −0.034 | −0.076 | 0.06 |
| OBV_SLOPE | −0.005 | −0.073 | 0.07 |
| rr | +0.040 | +0.020 | 0.62 |

Everything with a strong training correlation collapses. One field agrees in
both eras at p < 0.05: `DIST_HIGH20`, negative in both, meaning candidates
nearer their twenty-day high did better.

**That is a hypothesis, not a finding.** Twenty features were tried and one
false positive per twenty is exactly the expectation at p < 0.05. It is also
604 validation observations. It is recorded because it points the same way as
the one thing in this data that *has* survived both eras — a breakout, measured
independently in [CONFIRMED_VOLUME_BREAKOUT.md](CONFIRMED_VOLUME_BREAKOUT.md) —
and not because this table establishes it.

## 5. What shipped, and what did not

**Not shipped: any change to the score.** There is nothing to reweight, removing
it is a different and much larger decision about what the strategy is, and this
document is not the place to take it.

**Shipped: the page stops implying a ranking it does not have.**

* the results table's order is documented, in the code and on the page, as
  stable rather than as a quality ordering;
* the `Score` column is drawn against **118**, its reachable maximum, instead of
  100. Every score above 100 had been rendering as a full bar, so the strongest
  rows and the merely-strong ones looked identical;
* `Score` and `Confidence` carry the measurement in their column help, including
  that confidence is computed from the same inputs as the score and is therefore
  a second opinion from the same witness.

## 6. What this means for the strategy

The score does three jobs. Two of them are now known to be unsupported:

| Job | Verdict |
|---|---|
| The BUY threshold (`min_score`) | A throttle. It reduces how often the round trip is paid, which is real but bounded — [SELECTION_AND_EXECUTION.md](SELECTION_AND_EXECUTION.md). Raising it was measured harmful. |
| Ranking candidates on the page | Unsupported. ρ = +0.012, p = 0.76. |
| The capacity tie-break in `PortfolioSimulator.selection_key` | Unsupported, and independently confirmed on the other strategy: refused trades measured no worse than taken ones at t = −0.89 ([CAPACITY_IS_THE_CONSTRAINT.md](CAPACITY_IS_THE_CONSTRAINT.md) §2). |

The constructive answer is not better weights. It is the design the evidence
already supports: gates that were each measured to survive both eras, no score
at all, and a strategy that says which condition failed rather than how many
points it scored. That is
[CONFIRMED_VOLUME_BREAKOUT.md](CONFIRMED_VOLUME_BREAKOUT.md), and its validation
lift of +1.92% is the comparison this document exists to make possible.

**What converting would cost is measured separately** in
[COST_OF_DROPPING_THE_SCORE.md](COST_OF_DROPPING_THE_SCORE.md), and the answer
is one trade — for a reason more unflattering than anything above. The shipped
`min_score: 50` sits at the **first percentile** of the population it judges,
refusing 1% of what reaches it. The threshold is not a weak filter; it is barely
connected to the decision.

## Limits

- One dataset, one universe. 436 training and 604 validation observations is
  not many, and low power cuts both ways: it is why §4's survivor is weak
  evidence, and it also means a small true effect in the score could be missed.
  What can be said is that if one exists, it is far too small to rank on.
- The eligible population reproduces the shipped gate stack in vectorised form.
  The components themselves are verified against the real `strategy/` functions
  on 2,000 random checks — which is how an off-by-one in the resistance window
  was caught before any of this was believed.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/score_components.py
venv/Scripts/python.exe scripts/research/score_can_it_rank.py
```
