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

## Step 3 — Spread-filtered universe

**Why.** The one lever with headroom. Restricting to names with a spread of 0.3%
or less moves friction from 0.760% to 0.550%, against a drift of 0.9630% — a
margin of **+0.413%** per round trip where the current margin is **+0.0008%**.
At 0.2% the margin is +0.460%.

**Cost.** It also removes 71% of the trades, which is a real loss of
diversification and of sample size.

**Done when.** The trade-off between margin per trade and trade count is
measured across thresholds, out of sample.

## Step 4 — The eleven-setting divergence

`DEFAULT_SETTINGS` and the running `config/settings.json` describe two different
strategies, not one with drifted parameters
([MIN_RR_AS_RISK_CONTROL.md §6](MIN_RR_AS_RISK_CONTROL.md)). Everything measured
here is calibrated against the running file.

**This is not mine to decide.** It is a choice about what the product is. What
can be done without deciding is to measure the code-default configuration under
the same corrected instrument, so the choice is made against evidence rather than
against whichever file someone opened first.

---

## What this plan does not claim

None of these steps creates an edge. Steps 1 and 2 make the numbers trustworthy;
step 3 is the only one with a plausible path to a positive expectancy, and it
buys that by trading far less.

If step 3 fails, the honest conclusion is available and worth stating: EGX at
this cost structure does not support swing trading at this frequency. That is a
result, not a failure to find one.
