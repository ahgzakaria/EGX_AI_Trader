# Removing Candle Confirmation — Measured

**Decision taken:** remove candle confirmation from the swing strategy, on the
grounds that a confirmation layer measuring something other than its own name is
worse than its absence
([SWING_STRATEGY_PROBE.md §4](SWING_STRATEGY_PROBE.md)).

**Status:** implemented in the working tree, **not committed**. The change edits
`strategy/decision_engine.py`, a file sealed in
`strategy_selector/frozen_strategy_manifest.json`, so committing it requires
re-cutting a release seal. That decision is held pending this measurement.

**Short answer:** the change is neutral at trade level and expensive at portfolio
level. And the measurement surfaced something larger — the sealed backtest result
does not reproduce, and under current data the strategy has negative expectancy
once real friction is charged, in *both* configurations.

---

## 1. The sealed record does not reproduce

`reports/backtest_results.csv` as it stood — itself an uncommitted working-tree
artifact — reports 684 trades over 2,190 days for a 75.67% return at profit
factor 1.34. Re-running the **unmodified sealed engine** with the candle gate
**on**, against current data and settings, gives something else entirely:

| | Sealed artifact | Same engine, re-run today |
|---|--:|--:|
| Trades | 684 | 736 |
| Backtest days | 2,190 | 3,298 |
| Total return | **+75.67%** | **-1.78%** |
| Profit factor | 1.34 | 0.99 |
| Max drawdown | 16.58% | 35.04% |
| CAGR | 9.85 | -0.20 |

The span alone — 2,190 against 3,298 days — rules out any strategy change as the
cause. The saved artifact was produced under conditions that no longer exist and
are not recorded anywhere, because the file was never committed. **It should not
be used as a baseline for anything.** Every comparison below is against the
re-run, not against it.

## 2. What removing candle confirmation actually did

Both runs: same data, same settings, same day. Only `require_candle_confirmation`
and the score/confidence/reasons contribution differ.

| | Candle gate ON | Candles removed | Delta |
|---|--:|--:|--:|
| Trades | 736 | **1,446** | +710 |
| Symbols | 178 | 187 | +9 |
| Win rate | 25.95% | 25.24% | -0.71pp |
| **Avg profit/trade** | **+0.115%** | **+0.117%** | **+0.002pp** |
| Median profit/trade | -1.210% | -1.355% | -0.145pp |
| Avg score | 76.80 | 70.50 | -6.29 |
| Portfolio profit | -1,775 | +13,708 | +15,483 |
| **Max drawdown** | **35.04%** | **51.51%** | **+16.5pp** |

**Per-trade quality is unchanged.** +0.002 percentage points is noise. Win rate
and median both move slightly the wrong way.

The portfolio profit improves only because the trade count nearly doubled at a
barely-positive expectancy, deploying more capital for the same edge — and it
bought that with **half again as much drawdown**.

Only 315 trades appear in both runs; 421 present before are absent after, and
1,131 are new. A removed gate can only add signals, so the churn is the portfolio
simulator's capital and position limits selecting differently from a doubled
candidate pool. Portfolio-level figures are therefore a weak read here; the
per-trade line is the honest one.

### What the gate was really doing

It was not filtering for quality — per-trade quality is identical without it. It
was halving the trade count. **Candle confirmation was acting as a throttle, not
a filter**, and the fabricated `Open` it ran on means the throttle was closing on
an arbitrary criterion. Removing it removes the fiction; it also removes the
throttle, and nothing was put in its place.

## 3. The finding that outranks both

Applying the friction understatement measured in
[SWING_STRATEGY_PROBE.md §2](SWING_STRATEGY_PROBE.md) — 0.161% per round trip,
the gap between the modelled 0.703% and the measured 0.864% — to each run:

| | As backtested | With real friction | Profitable share |
|---|--:|--:|--:|
| Candle gate ON | +0.115% | **-0.046%** | 24.9% |
| Candles removed | +0.117% | **-0.044%** | 24.1% |

**Both configurations have negative expectancy once the spread the backtest never
charged is charged.** The choice between them is a choice between two negative
numbers that differ by two thousandths of a percentage point.

This does not argue against the removal — the correctness case for it never
depended on performance. It argues that neither configuration is currently a
strategy worth running, and that the question of what to do about candle
confirmation is much smaller than the question of whether the edge exists.

## 4. Recommendation

- **The removal is defensible on its own terms.** It costs nothing in trade
  quality and deletes a layer that was reporting `Morning Star` for a comparison
  of lagged returns.
- **It should not ship alone.** Removing the throttle without replacing it
  doubles exposure for no edge and raises drawdown by 16.5 points. If it ships,
  something has to bound trade count — `min_score` is the obvious lever, since
  average score fell 6.29 points when the component was removed and the threshold
  did not move to compensate.
- **Re-cutting the release seal is not justified by this result.** A neutral
  change is a weak reason to break a control that exists to catch exactly this,
  especially with the archived snapshot outside the repository.
- **The real work is upstream of all of it:** the backtest charges too little
  friction, and its result is not currently reproducible.

## Limits

- Both runs use the fabricated `Open`, which the strategy's entries never read
  but its indicators' surrounding context does.
- The friction correction applies an August-2026 spread to trades from 2017 on.
- The portfolio-level comparison is confounded by capital allocation, as above.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe backtest.py
venv/Scripts/python.exe scripts/research/compare_backtest_runs.py <before.csv> <after.csv>
```

Note that `backtest.py:55` raises `KeyError: 'successful'` after the results are
written — a pre-existing defect in the command-line presentation, not the engine.
