# Widening the Exit Does Not Recover the Upside

**Question:** the deficit this investigation ended on is upside never captured,
not losses taken. With a causal gate limiting the downside at 89.5% precision,
is the trailing stop now redundant — and does widening or removing it recover
the 1,194% the market made while the strategy made 60%?

**Status:** measurement, and **the final strategy experiment**. Six isolated
backtest runs, no strategy module changed, no parameter tuned toward a result.

**Short answer:** no, and the premise was wrong. Widening the exit **does not
raise upside capture** — it sits at 6.1% of WEAK_BULL at the shipped setting and
6.5% with the trailing stop removed entirely. One and a half percentage points
across the whole sweep, against a hypothesis that predicted movement toward
100%. Every variant misses the bar; the best reaches USD Calmar **−0.04**
against **+0.10**.

**The program terminates here.** No further configuration is tested.

---

## 1. What was swept

`isolated_backtest.py`, one run per variant, pinned config and full manifest.
The trailing logic only ever raises a stop, so the initial hard stop is untouched
in every variant and "disabled" means the hard stop alone.

| variant | trailing_enabled | trailing_mode | trailing_atr |
|---|:-:|---|--:|
| baseline (shipped) | true | EMA20 | — |
| ATR 2.0 | true | ATR | 2.0 |
| ATR 3.0 | true | ATR | 3.0 |
| ATR 4.0 | true | ATR | 4.0 |
| ATR 6.0 | true | ATR | 6.0 |
| disabled | false | — | — |

**ATR 6.0 and disabled are identical, trade for trade.** At six ATRs the trail
never binds, which is the check that the sweep actually reached "no trail"
rather than stopping short of it.

The gate is the best-measured variant from
[REGIME_DETECTABILITY.md](REGIME_DETECTABILITY.md): proxy drawdown beyond 10%,
off 48.1% of days, account in USD while off.

## 2. The sweep

| variant | trades | win % | median trade | best-5 share | EGP return | EGP DD | EGP Calmar | USD return | USD DD | **USD Calmar** | clears bar |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|:-:|
| **(a) ex-ante top-30 basket** | — | — | — | — | +385.02% | −65.67% | 0.30 | +74.31% | −61.38% | **+0.10** | — |
| baseline EMA20 \| **gated** | 356 | 34.8 | −0.71% | 64.2% | **+130.63%** | **−19.02%** | **0.52** | −17.12% | −46.48% | **−0.04** | **no** |
| baseline EMA20 \| gate off | 457 | 36.3 | −0.68% | 81.1% | +55.64% | −18.56% | 0.27 | −44.07% | −68.17% | −0.09 | no |
| ATR 2.0 \| gated | 166 | 34.3 | −4.71% | 538.7% | +50.61% | −28.66% | 0.16 | −45.87% | −57.45% | −0.12 | no |
| ATR 3.0 \| gated | 166 | 36.1 | −4.33% | 119.0% | +88.93% | −27.02% | 0.27 | −32.10% | −52.24% | −0.08 | no |
| ATR 4.0 \| gated | 164 | 36.0 | −4.33% | 129.1% | +80.27% | −27.02% | 0.25 | −35.22% | −54.12% | −0.09 | no |
| ATR 6.0 \| gated | 164 | 36.0 | −4.38% | 133.3% | +79.05% | −27.02% | 0.25 | −35.65% | −54.12% | −0.09 | no |
| **disabled \| gated** | 164 | 36.0 | −4.38% | 133.3% | +79.05% | −27.02% | 0.25 | −35.65% | −54.12% | −0.09 | **no** |

Ungated rows for every ATR variant are in
`reports/audits/strategies/exit_width_sweep.csv`; all are worse still, between
−0.05 and −0.17 USD Calmar. No cell is under 30 trades.

**Widening makes it worse, not better.** The shipped EMA20 trail is the best row
in the table on every axis — highest EGP return, smallest EGP drawdown, highest
EGP Calmar, best USD Calmar. Every widening step loses ground and the neighbours
around each setting move together, so this is a slope rather than one unlucky
cell.

The median trade collapses from **−0.71% to −4.71%** as soon as the trail widens,
and the trade count halves from 356 to 166 because positions held longer occupy
the portfolio's capacity. ATR 2.0's best five trades account for **538.7%** of
its net profit — the rest of the book loses more than four times over.

## 3. Upside capture — the metric that produced the hypothesis

Capture is the variant's compounded return inside a regime as a share of the
benchmark's over the same days.

| variant (gated) | WEAK_BULL return | benchmark | **capture** | BULL return | benchmark | **capture** |
|---|--:|--:|--:|--:|--:|--:|
| **(a) buy-and-hold basket** | +1,194.81% | +1,194.81% | **100.0%** | +1,019.38% | +1,019.38% | **100.0%** |
| baseline EMA20 | +72.37% | +1,194.81% | **6.1%** | +60.79% | +1,019.38% | **6.0%** |
| ATR 2.0 | +73.74% | +1,194.81% | 6.2% | +43.72% | +1,019.38% | 4.3% |
| ATR 3.0 | +89.72% | +1,194.81% | 7.5% | +54.53% | +1,019.38% | 5.3% |
| ATR 4.0 | +77.21% | +1,194.81% | 6.5% | +66.40% | +1,019.38% | 6.5% |
| ATR 6.0 | +77.21% | +1,194.81% | 6.5% | +66.40% | +1,019.38% | 6.5% |
| **disabled** | +77.21% | +1,194.81% | **6.5%** | +66.40% | +1,019.38% | **6.5%** |

**This is the answer, and it arrived before the bar did.** The hypothesis was
that a trailing stop cutting winners short is what costs the upside, so widening
it should push capture toward 100%. Capture moves from 6.1% to 6.5%. Removing
the trail **entirely** — no trail at all, hard stop only — buys four tenths of a
percentage point.

The document that framed this said the sweep would have answered the question if
widening did not raise capture. It did not. The trail was never what was
withholding the upside: a system holding a handful of names for a few weeks at a
time, in the market a quarter of the year, cannot capture a 1,194% move it is
mostly not holding. The exit width is not the binding constraint and never was.

## 4. Control (c) — the random gate

100 iterations, same 48.1% duty cycle, same costs, applied to the best variant.

| | USD Calmar |
|---|--:|
| real gate | **−0.04** |
| random median | −0.090 |
| random best of 100 | **+0.100** |
| real gate's percentile | **81st** |

The gate is real detection — it beats the random median and sits in the top
fifth. And **the best of 100 random gates reached +0.100, matching the bar the
real gate missed.** A coin flip with the right duty cycle got there once in a
hundred tries; the detector did not get there at all.

## 5. The bar

Set before the result and not moved: over the full window, in USD, beat the
ex-ante buy-and-hold basket on Calmar (**+0.10**) and not be matched by the
random-gate distribution.

**Best variant: −0.04. MISSED.**

It is missed by every one of the twelve gated and ungated cells, and it is
missed in the same direction and by a similar margin as every previous attempt
in this investigation.

## Limits

- One dataset, one universe, one strategy family.
- 2016–2026 contains one dominant regime, the post-float nominal repricing.
  Every conclusion is conditioned on it and there is no more data.
- The gate suppresses entries only; a gate that liquidates open positions is a
  different strategy and was not tested.
- USD cash as an off-state is charged no interest, no spread and no access
  constraint, so those rows are flattered and still fail.
- Regime labels cover 2020-08 to 2026-06, 5.8 of the 8.9 years.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/isolated_backtest.py --label exitsweep_ema20_baseline
venv/Scripts/python.exe scripts/research/isolated_backtest.py --label exitsweep_atr3 --set backtest.trailing_mode=ATR --set backtest.trailing_atr=3.0
venv/Scripts/python.exe scripts/research/isolated_backtest.py --label exitsweep_notrail --set backtest.trailing_enabled=false
venv/Scripts/python.exe scripts/research/exit_width_under_gate.py
```

---

## Termination

**This was the final strategy experiment and it missed the bar. The program
terminates. No further configuration will be tested.**

The premise was reasonable and it was wrong, which is the useful part. Removing
the trailing stop had been tested before, without a gate, and the conclusion was
correctly withdrawn. Testing it with a working gate was a genuinely different
experiment and it produced a genuinely different failure: not "the trail is load
bearing" but "the trail was never the constraint". Capture is 6.1% with it and
6.5% without it.

What that leaves is stated in
[INVESTIGATION_SUMMARY.md](INVESTIGATION_SUMMARY.md): two components survive the
verdict and are worth keeping, and the objective as specified does not.
