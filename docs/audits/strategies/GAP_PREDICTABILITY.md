# The Overnight Gap Is Predictable

**Question:** the gap is the strongest reading in this project — +0.663% at
t = +15.7 ([OVERNIGHT_GAP_PROBE.md](OVERNIGHT_GAP_PROBE.md)) — but capturing it
indiscriminately nets **-0.107%** after costs, because most nights lose a
little. Does anything observable **at the close** say which symbols will gap
further?

**Status:** measured. Read-only. Nothing shipped, nothing traded.

**Short answer:** yes, strongly, and the effect is cross-sectional rather than
market timing. Selecting the top fifth by the session's own return turns
-0.107% into **+0.550%** net, and adding liquidity filters takes it to
**+0.913%** on 59.3% of nights.

This is the first thing measured in this investigation that clears its costs by
a margin rather than by a rounding error.

---

## 1. The predictors

All computed from the session that has already closed. No predictor uses a price
from the session it predicts.

| Predictor | r | t |
|---|--:|--:|
| **Intraday return** | **+0.209** | **+11.88** |
| **Session range %** | **+0.186** | **+10.49** |
| Turnover | +0.109 | +6.07 |
| Volume | +0.068 | +3.81 |
| Close position in range | +0.058 | +3.21 |
| Quoted spread | -0.043 | -2.39 |

Six predictors against one outcome, so a lone t of 2 is expected by chance;
treat below ~3.1 as noise, which puts the spread reading out. The top two are
nowhere near that boundary. For scale, **the largest |t| found anywhere inside
the strategy itself was 2.3**.

By quintile of intraday return, the next morning's gap runs +0.301%, +0.501%,
+0.546%, +0.603%, **+1.338%**.

## 2. It is selection, not market timing

The obvious alternative explanation: the market rose today, so it opens higher
tomorrow, and the "predictor" is only detecting a good day. That would be timing,
not stock selection, and would give no advantage within a day.

Subtracting each session's own cross-sectional mean gap tests it directly:

| Predictor | Raw t | Day-demeaned t |
|---|--:|--:|
| Intraday return | +11.88 | **+11.93** |
| Session range % | +10.49 | +10.30 |
| Turnover | +6.07 | +6.01 |
| Volume | +3.81 | +3.91 |
| Close position | +3.21 | +3.23 |

**Nothing changes.** The effect survives demeaning intact, so it is entirely
cross-sectional: within the same session, the stocks that rose more gap up more
the next morning. Momentum continuing across the overnight boundary.

## 3. The economics

Costs charged per symbol: 0.3638% commission plus that symbol's own measured
spread.

| Selection | n | Gap | **Net** | Net-positive | Cross-sectional excess |
|---|--:|--:|--:|--:|--:|
| All symbols | 3,084 | +0.664% | **-0.107%** | 32.6% | 0.000% |
| Top 20% by intraday return | 610 | +1.351% | **+0.550%** | 45.4% | +0.687% |
| …and spread ≤ 0.3% | 208 | +1.442% | **+0.878%** | 57.2% | +0.787% |
| …and turnover ≥ 20M | 499 | +1.476% | +0.738% | 50.5% | +0.812% |
| **…both filters** | **199** | **+1.476%** | **+0.913%** | **59.3%** | **+0.823%** |

Twelve of sixteen sessions are net-positive on the top-quintile selection.

Note the last column. The **excess** is what remains after removing each day's
average gap, so it is the part attributable to choosing rather than to the
market being up. At +0.823% it is larger than the whole round-trip cost.

## 4. What would have to be true for this to be wrong

- **Sixteen sessions, one month.** This is the single largest weakness and no
  amount of care fixes it from the data available.
- **Every session's mean gap was positive**, +0.176% to +0.923%. The selection
  effect is measured net of that, but the *level* of profit contains it. In a
  market with negative gaps the selection would still work and the level would
  fall.
- **It cannot be checked against history.** The daily `open` field is fabricated
  in every source this project has
  ([CACHE_REBUILD_FEASIBILITY.md](CACHE_REBUILD_FEASIBILITY.md)), so gaps are
  unmeasurable before 2026-08-02. The data defect logged weeks ago is now the
  thing blocking verification of the best signal found.
- **No execution modelling.** These are close-to-open moves. Buying at the close
  and selling into the open is assumed, not simulated; auction mechanics, partial
  fills and the T+0 eligibility question are all unaddressed.

## 5. Why this is worth forward-testing and the swing strategy is not

The swing strategy produces about 54 trades a year, so out-of-sample evidence
takes years and a season of it still cannot separate a tail-driven edge from
luck.

The gap produces about **38 observations per session** — roughly **800 a
month**. A single month of forward recording is a larger out-of-sample sample
than the entire nine-year swing backtest.

That is a difference in kind, not degree. It makes the gap the only hypothesis
in this project that can be settled on a timescale worth waiting for.

## Reproduce

```
venv/Scripts/python.exe scripts/research/probe_gap_predictability.py
```
