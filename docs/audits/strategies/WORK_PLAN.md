# Work Plan — After the Break-Even Finding

The strategy captures +0.9630% of drift and pays 0.9638% to capture it. Every
other effect measured is noise around that equality
([SELECTION_AND_EXECUTION.md](SELECTION_AND_EXECUTION.md)). Three things remain
worth doing. This is the order and the reason for it.

---

## Ordering principle

**Fix the instrument before measuring with it.** Phase 0 of the previous plan
reversed this investigation's headline recommendation and caught a concurrent
process editing config mid-run. The same rule applies again: step 1 changes how
every cost is computed, so anything validated before it would be stale the moment
it lands.

That is why walk-forward — the most urgent item on its own terms — comes second,
not first.

---

## Step 1 — Per-symbol spread in the cost model

**Why.** The model charges a flat 0.5% to every symbol. Real spreads on the
traded names run from 0.04% to over 1%. So the current cost is simultaneously too
harsh on liquid names and too generous on illiquid ones, and no universe filter
can be evaluated while every symbol costs the same.

**What.** A measured spread table, and `TradingCosts` that reads it by symbol
with the configured flat rate as fallback.

**Cost.** `backtesting/engine.py` constructs `TradingCosts()` without a symbol
and is a sealed release file. Third seal cut of this effort; the first was
provably neutral, the second was not and said so.

**Done when.** The shipped configuration is re-measured under per-symbol costs
and the result is recorded — whether or not it still holds.

## Step 2 — Walk-forward the shipped configuration

**Why.** `min_rr 3.0` with the trailing stop is entirely in-sample. 84% of its
profit comes from 5 of 468 trades. It shipped as a *risk* control on a monotone
drawdown response, which is defensible, but nothing about its return has been
validated out of sample.

**What.** Rank on a trailing window, measure on the next, across the full
history. The per-year stability table already in the harness is a weaker version
of this; this is the real one.

**Done when.** Either the risk improvement survives out of sample, or it does
not and the shipped setting is revisited.

## Step 3 — Spread-filtered universe — **DONE, and the answer is no**

**The premise was wrong.** The headroom calculation held drift constant at
+0.9630% while cutting friction. Drift is *not* independent of spread: wider,
thinner names carry enough extra drift to cover their extra cost. That is the
liquidity premium, and the calculation assumed it away.

Measured, tightening the filter makes things worse, monotonically:

| Filter | Trades | Profit factor | Return | Drawdown | Avg/trade | Sharpe |
|---|--:|--:|--:|--:|--:|--:|
| none | 485 | 1.29 | +60.55% | 16.13% | +0.38% | 0.55 |
| ≤ 1.0% | 468 | 1.39 | +75.67% | 14.07% | +0.50% | 0.70 |
| ≤ 0.5% | 347 | 1.33 | +48.02% | 14.68% | +0.43% | 0.52 |
| ≤ 0.3% | 142 | 1.24 | +14.73% | 26.63% | +0.34% | 0.32 |

The `≤ 1.0%` row looks like a win and is not one. It differs from no filter by
**23 trades**, and removing the worst *three* of those leaves the remaining
twenty summing to **+2.3%** — positive. A structural rule resting on three
observations is not a rule.

Costs are already charged per symbol, so a wide-spread name already pays for
itself. The filter does not save a mispriced cost; it just removes names, and
the names it removes were paying their way.

**Shipped instead: a pricing fix.** Symbols too thinly quoted to measure were
being charged the universe median, 0.500%. They quote thinner than the thinnest
*measured* name — under 50 quotes against 241 — and thin quoting predicts a wide
spread: across the 217 measured symbols the correlation between quote count and
spread is **-0.445**, the least-quoted quartile sitting at a **0.652%** median
against **0.188%** for the most-quoted. They now pay **0.927%**, the 90th
percentile of the measured distribution.

Effect on the shipped configuration: profit factor 1.29, return +60.08% against
+60.55%, drawdown 16.17%. Almost nothing, because only six trades were affected
— which is the right size for a correctness fix that was never going to move a
headline.

The universe filter stays in the code, defaulted **off**, because measuring it
was worth doing and re-measuring it later will be too.

## Step 4 — The eleven-setting divergence — **DONE**

`DEFAULT_SETTINGS` and the running `config/settings.json` describe two different
strategies, not one with drifted parameters
([MIN_RR_AS_RISK_CONTROL.md §6](MIN_RR_AS_RISK_CONTROL.md)). Everything measured
here is calibrated against the running file.

Measured under the corrected instrument, and it was not close. The running
configuration returns **+60.08%** at profit factor **1.29** with a **16.17%**
drawdown, seven of ten years positive. The code defaults return **-36.23%** at
**0.88** with **46.30%**, three of ten.

The defaults were wrong, so the defaults changed. `config/settings.json` is
untouched — it was already right. Pinned by
`tests/test_default_settings_are_measured.py`, which asserts the values rather
than agreement with the settings file, since that file is meant to be edited and
a test demanding they match would fail on the first legitimate tweak.

Full write-up: [CONFIG_RECONCILIATION.md](CONFIG_RECONCILIATION.md).

---

## What this plan does not claim

None of these steps creates an edge. Steps 1 and 2 make the numbers trustworthy;
step 3 is the only one with a plausible path to a positive expectancy, and it
buys that by trading far less.

Step 3 has now failed, and the conclusion it was set up to reach is available:
**there is no cost lever left**. Costs are charged per symbol and correctly, the
cheap names are not systematically better after their own costs, and the one
filter that looked promising rests on three trades.

All four steps are now done — two positive, two negative. What remains is the
finding that has been true since
[SELECTION_AND_EXECUTION.md](SELECTION_AND_EXECUTION.md): the strategy clears
its costs by about a sixth of a percent per round trip, entirely from the drift
of names its gates select for being in an uptrend, and its entry timing adds
nothing measurable (t = +0.65). That is a real but thin result, and no parameter
in this system has been shown to improve on it.
