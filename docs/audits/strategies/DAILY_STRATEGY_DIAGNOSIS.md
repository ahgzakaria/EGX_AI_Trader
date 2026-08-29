# What Is Wrong With the Daily Dashboard Strategy

**Question:** the strategy on the Daily Dashboard has been repaired in pieces —
a dead scoring component removed, candle patterns removed, a negative-reward
guard added. What is still wrong with it, measured rather than read off the
code?

**Status:** diagnosis, and then four of its findings were fixed — see
§10, added 2026-08-29. The measurements below are as first taken, against the
shipped strategy before those fixes.

Originally: nothing in `strategy/` was changed. Every figure below is
computed over the engine's own inputs — `load_history(purpose="backtest")` plus
`calculate_indicators`, 191 symbols, 382,646 bars, 2016-07-19 to 2026-07-22 — or
over the shipped backtest's own trade file
(`reports/experiments/20260827_193224_reward_guard`, 484 trades).

**Short answer:** it selects nothing. Its own gate stack, measured directly,
lifts +1.91% over owning the same universe in 2016-2022 and **−0.22% in
2023-2026**. Everything else below explains why.

---

## 1. The selection has no out-of-sample edge

The strategy's own gates, applied as a stack to every bar in the liquid half of
the universe, against a twenty-bar forward return net of each symbol's measured
round trip:

| Rule | train n | lift | valid n | lift |
|---|--:|--:|--:|--:|
| `above 200d & EMA20>EMA50 & ADX≥21 & vol ratio≥1 & ATR%≥1.5` | 10,268 | **+1.91%** | 12,137 | **−0.22%** |

"Lift" is against owning an average name in the same pool on the same days, so
the market's own drift — which in EGP terms is enormous — cancels.

For contrast, from the same scan of twenty conditions, the only ones positive in
both eras:

| Rule | train lift | valid lift | bad years |
|---|--:|--:|--:|
| 20-day breakout + volume ≥ 2.5× | +3.13% | +1.36% | 1/10 |
| 20-day breakout | +4.12% | +0.70% | 2/10 |
| ADX ≥ 25 | +0.77% | +0.28% | 3/10 |
| calmest third by ATR% | +0.11% | +0.25% | 4/10 |

Reproduce: `scripts/research/signal_scan.py`.

This is the same conclusion
[SELECTION_AND_EXECUTION.md](SELECTION_AND_EXECUTION.md) reached from the other
direction (entry-day selection at t = +0.65), now measured on the gates
themselves rather than on the realised trades.

## 2. It earns a third of what doing nothing earned

| Over 2017-08-03 → 2026-06-28 (8.9 years) | |
|---|--:|
| The strategy | **+60.08%**, CAGR **5.43%**, exposure 23.6% |
| Median EGX name, bought and held | **+320.6%**, CAGR **17.51%** |

Both figures carry the same survivorship inflation — the panel is what the
provider still serves, so delisted names are absent from both — and both are
nominal EGP across a decade in which the currency lost most of its value. That
is exactly why they are quoted together: the comparison is fair even though
neither number is a real return.

No summary the project produces contains a benchmark. `BacktestStatistics`
reports Sharpe, Sortino, Calmar and CAGR, and not one line saying what the same
capital would have done sitting still.

## 3. Two definitions of "resistance", used at the same moment

`strategy/entry.py` computes the highest high of the previous twenty bars
**excluding today**, and places `Target1` and `Target2` off it.
`strategy/support.py` computes the highest high of the previous twenty bars
**including today**, and `quality_filter.py` measures the 3% of required room
off *that*.

| | |
|---|--:|
| Bars where the two disagree | **22.4%** |
| Bars passing the 3% room gate on `res_incl` | 83.7% |
| …of those, room to the **actual target** is under 3% | 3.2% |
| …of those, the actual target is **below the entry** | 1.5% |

The last row is the defect fixed in commit `7bfd643` — but it was fixed by
refusing the signal, not by reconciling the two definitions, so the other 3.2%
still pass a room test against a level the trade is not aiming at.

## 4. "Confirmed Breakout" is worth eight points and almost cannot happen

`entry_signal` awards 8 points and the reason string "Confirmed Breakout" for a
close above the twenty-bar high on 1.2× volume. `quality_filter` demands 3% of
room below resistance, and `require_quality_filter` is `true`.

| | Share of bars |
|---|--:|
| Scores "Confirmed Breakout" | 4.22% |
| Passes the 3% room gate | 83.66% |
| Independence would predict both | 3.53% |
| **Observed both at once** | **1.07%** |

This is the same structural contradiction that
[SCORE_DIAGNOSIS.md §2](SCORE_DIAGNOSIS.md) found in `breakout_score`, which was
removed from the total for exactly this reason. It was removed in one place and
left in the other.

## 5. "Near Support" is scored twice, sixteen points, two thresholds

| | Fires on |
|---|--:|
| `support.py` "Near Support" (+10, within 3%) | 16.8% |
| `entry.py` "Near Support" (+6, within 1 ATR) | 24.4% |
| Both at once | 15.6% |

Sixteen points of a ~118-point scale for one idea, expressed two ways that agree
almost always. And on **10.8%** of bars the twenty-day low *is* today's low — so
the +10 is awarded for the stock having just fallen, and the stop is placed
under a low the market made hours earlier.

## 6. The scale is not the scale it is calibrated against

After the two removals the reachable maximum is 118, not 100:

| Component | Max |
|---|--:|
| trend | 30 |
| volume | 20 |
| support | 15 |
| entry | 28 |
| momentum | 25 |
| **total** | **118** |

`min_score` is 50 — 42% of the scale. And `min_volume` is `0`, so the Volume
gate cannot reject anything, while `volume_score` is the one component measured
with a significant correlation to the outcome and it is **negative** (r = −0.090,
[SCORE_DIAGNOSIS.md §5](SCORE_DIAGNOSIS.md)).

## 7. `require_market_analyzer` is on, and cannot fire

`config/settings.json` sets `require_market_analyzer: true`. The gate reads
`^CASE30` through `strategy/market_analyzer.py`. Asked for a date in the middle
of the backtest window:

```
backtest  -> Passed=True SIDEWAYS  "insufficient EGX30 history yet"
scanner   -> Passed=True SIDEWAYS  "unavailable (DATA_UNAVAILABLE ... EODHD unsupported) - defaulting to allow"
dashboard -> Passed=True SIDEWAYS  "unavailable (DATA_UNAVAILABLE ... EODHD unsupported) - defaulting to allow"
```

The backtest cache holds **one bar** of `^CASE30`; the live providers do not
serve it at all. The gate defaults to allow on every bar, in every mode. It is
switched on in the settings UI, it appears as `MarketAnalyzer: PASS` in every
decision trace, and it has never rejected anything.

Failing open is the right default for a network problem. Failing open silently,
for years, while reporting `PASS`, is not.

## 8. Prices are rounded to two decimals on a market that trades in piastres

`entry.py` rounds `BuyHigh`, `StopLoss`, `Target1` and `Target2` with
`round(x, 2)`.

| Price band | Symbols | Mean distortion from `round(x, 2)` |
|---|--:|--:|
| under 1 EGP | 16 | **2.31% of price** |
| 1–5 EGP | 32 | 0.22% |
| 5–20 EGP | 50 | 0.05% |
| over 20 EGP | 93 | 0.01% |

`RR` is a quotient of two rounded differences, so the error does not cancel — it
compounds. `strategy_breakout/settings.json` already uses three decimals.

## 9. The exit is where the money goes

| Exit reason | n | Mean | Median |
|---|--:|--:|--:|
| **TrailingStop** | **390** | **−0.47%** | −0.68% |
| StopLoss | 43 | −5.39% | −4.83% |
| Partial+Target2 | 30 | **+15.73%** | +15.38% |
| Partial+BreakEven | 18 | +6.63% | +5.83% |
| Partial+Timeout | 2 | +4.25% | +4.25% |
| Timeout | 1 | −0.79% | −0.79% |

**Eighty-one percent of trades end on the trailing stop at a small loss.** Thirty
of 484 reach `Target2` and carry the result.

This is not new — [SWING_IMPROVEMENT_LEVERS.md §2](SWING_IMPROVEMENT_LEVERS.md)
saw it and [TRAILING_STOP_VERDICT.md](TRAILING_STOP_VERDICT.md) correctly
withdrew the conclusion that removing the trail helps. Both are right. Removing
the trail from *this* strategy is worse; the trail is nonetheless the mechanism
by which the strategy fails to keep what it selects. Measured independently on a
different rule, stop distance responds monotonically: tighter is worse, every
time, in both eras
([CONFIRMED_VOLUME_BREAKOUT.md](CONFIRMED_VOLUME_BREAKOUT.md) §4).

## 10. It is a lottery, and the record says so

| | |
|---|--:|
| Top 1 trade | 25.3% of net profit |
| Top 5 trades | **108.4%** of net profit |
| Top 20 trades | 203.0% |
| Median trade | **−0.640%** |
| Share of trades profitable | 36.0% |

The other 464 trades lose money in aggregate. [MIN_RR_AS_RISK_CONTROL.md
§3](MIN_RR_AS_RISK_CONTROL.md) said this plainly about the shipped setting and it
is still true of the shipped run.

---

## What follows

1. **The score cannot be repaired by reweighting.** It has no out-of-sample
   signal at the gate level (§1), and its components are saturated, dead or
   inverted ([SCORE_DIAGNOSIS.md](SCORE_DIAGNOSIS.md)). Still true, still not
   attempted.
2. **Three defects are cheap to fix and independent of that**: the two
   resistance definitions (§3), the two-decimal rounding (§8), and the dead
   market gate (§7). None of them is why the strategy underperforms; all three
   make it harder to reason about. **Done — §10.**
3. **A benchmark belongs in `BacktestStatistics`.** A strategy returning 5.43%
   a year in a market that returned 17.5% should not be reportable as a profit
   factor of 1.29 without that line beside it. **Done — §10.**
4. **The one thing that measured as real in this data is a twenty-day breakout
   confirmed by volume** (§1). That is what
   [CONFIRMED_VOLUME_BREAKOUT.md](CONFIRMED_VOLUME_BREAKOUT.md) builds on.

## 10. What was fixed, and what each fix cost — 2026-08-29

Four of the findings above are now repaired. Each was measured on its own
against the shipped baseline, and the cost is reported whether or not it
flatters the change.

### The gate that could not fire (§7) — provably neutral

`analyze` now returns `Available`, and the decision trace reports
**`UNAVAILABLE`** instead of `PASS` when there is no index to ask. The
explanation it had been producing all along — and which was discarded unless
the gate failed, the case that never happens — now reaches the row.

The verdict changes; the decision does not. A missing index still allows the
trade, because refusing every trade over a data outage is worse than allowing
them. An isolated run gives **484 trades, every column except `reasons`
identical and every summary field identical** to the baseline.

### Two definitions of resistance (§3) — free

`support.py` now excludes today, matching `entry.py`, so one level is computed
once and used by the targets, by the quality gate's room test, and by the
reported figure.

Cost: **nothing**. An isolated run is identical to one without it, trade for
trade and reason for reason. Bars where the two definitions disagree are bars
where today made a new high, so the close sits near the top of its range and
the 3% room gate refuses them either way. The contradiction was real and free
to remove, which is the cheapest kind of fix and the easiest to keep putting
off.

### Two-decimal prices (§8) — the one that cost something

`PRICE_PRECISION = 3` in `entry.py`. `RR` stays at two decimals, being a ratio
rather than a quote.

This one is **not** neutral, and the headline gets worse:

| | shipped | 3-decimal prices |
|---|--:|--:|
| Trades | 484 | **457** |
| Total return | +60.08% | **+53.99%** |
| Profit factor | 1.29 | 1.28 |
| Max drawdown | 18.27% | 18.56% |
| Sharpe | 0.55 | **0.57** |
| Average per trade | +0.38% | **+0.41%** |
| Years positive | 7 of 10 | **6 of 10** |

Sixty-six trades leave and thirty-nine arrive; forty-three of the sixty-six had
an entry under 5 EGP, which is where the rounding did its damage.

**It ships anyway, and the argument is not the numbers.** Two decimals invents
prices this exchange does not quote for the names it most affects: across the
universe's own history only **24%** of closes under 1 EGP sit exactly on two
decimals against **71%** on three. The six points came from sixty-six trades
entered, stopped and targeted at levels that were not real. That is a bug
removed, not an edge — and the same figures are equally consistent with noise,
since profit factor, Sharpe and per-trade expectancy move in the other
direction.

What would make this a mistake is if the 3-decimal run were worse out of
sample too. Nothing here can answer that, and nothing here claims to.

### No benchmark in the summary — additive

`BacktestStatistics` now reports `BenchmarkReturn`, `BenchmarkCAGR`,
`BenchmarkSymbols` and `ExcessReturn`: buy and hold the median name this run
actually traded, over the same window. `None` where it cannot be computed,
never `0` — an unmeasured benchmark is not a flat market, the same rule the
annualised statistics already follow.

### The seal

`strategy/decision_engine.py` and `strategy/market_analyzer.py` are in
`strategy_selector/frozen_strategy_manifest.json`, and the manifest was re-cut
for those two. The cut covers the analyzer change only, which is the provably
neutral one; `entry.py` and `support.py` are not sealed. Eleven tests pin all
four fixes.

## Limits

- One dataset, one universe, in-sample except where a 2023-01-01 split is
  stated.
- Survivorship is uncorrected and inflates §2 on both sides of the comparison.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/panel.py
venv/Scripts/python.exe scripts/research/diagnose_daily_strategy.py
venv/Scripts/python.exe scripts/research/signal_scan.py
```
