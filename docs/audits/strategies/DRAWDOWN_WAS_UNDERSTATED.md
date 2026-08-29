# Every Drawdown This Project Published Was Understated

**Question:** `backtesting/equity.py` booked profit only when a trade closed and
never valued an open position. How wrong was every `MaxDrawdown` this project
has reported, and did any decision rest on the wrong number?

**Status:** **fixed and shipped 2026-08-29.** `EquityCurve` now builds a daily
curve marking open positions to market when it is given prices, reports the
closed-trade figure beside it as `MaxDrawdownClosedTrades`, and states which one
`MaxDrawdown` came from in `DrawdownBasis`. Twelve new tests; suite 3,734
passing.

**Short answer:** understated by a median of **1.55 percentage points** across
the 25 archived runs, worst **2.61**. The shipped Daily Dashboard strategy goes
from **16.17% to 18.27%**; CONFIRMED_VOLUME_BREAKOUT from 8.79% to **11.54%**.
**No shipped decision reverses** — the `min_rr 3.0` sweep, which is the one
choice this project made explicitly on a drawdown, keeps its ranking.

---

## 1. The defect

`EquityCurve.curve()` stepped once per trade, in exit-date order, adding
`portfolio_profit`:

```python
for trade in self.trades:
    equity += getattr(trade, self.profit_field)
```

Nothing else moved it. A portfolio 30% underwater across fifteen open positions
produced a flat line until those positions closed. The curve was the *realised*
part of an equity path, and the drawdown read off it was a closed-trade
drawdown.

It matters most where it was least visible. The understatement grows with **how
many positions are held at once and for how long**, because a market-wide fall
reaches a closed-trade curve only as positions close — spread over the following
weeks, netted against whatever opened meanwhile.

That is not a coincidence about which runs are affected. It is the property that
made the bug dangerous: it was found while sweeping portfolio capacity
([CAPACITY_IS_THE_CONSTRAINT.md](CAPACITY_IS_THE_CONSTRAINT.md)), where it had
produced the conclusion that holding thirty positions was nearly as safe as
holding three.

## 2. Every archived run, re-priced from its own trade file

Each run directory holds `backtest_results.csv` with `shares`, `entry_price`,
`entry_date` and `exit_date` per executed trade — everything a marked curve
needs. So none of these had to be re-run.

**The reconstruction validates itself:** the recomputed closed-trade drawdown
equals the published one **to the cent on all 25 archived runs**. What follows is
therefore a like-for-like comparison, not two different measurements.

| Run | trades | published | marked | under by |
|---|--:|--:|--:|--:|
| baseline_corrected_costs | 638 | 51.15% | 51.67% | 0.52 pp |
| trailing_off_corrected | 401 | 56.44% | 56.83% | 0.39 pp |
| breakout_thesis | 621 | 56.76% | 57.70% | 0.94 pp |
| candle_fully_removed | 1,201 | 58.89% | 60.15% | 1.26 pp |
| throttle_rr2.0 | 1,014 | 40.79% | 42.59% | 1.80 pp |
| throttle_rr2.5 | 580 | 46.56% | 48.99% | **2.43 pp** |
| throttle_rr3.0 | 468 | 17.62% | 19.74% | 2.12 pp |
| rebaseline_per_symbol_costs | 485 | 16.13% | 18.23% | 2.10 pp |
| persym_rr1.5 | 1,317 | 54.69% | 56.24% | 1.55 pp |
| persym_rr2.5 | 595 | 43.67% | 46.28% | **2.61 pp** |
| persym_rr3.0 | 485 | 16.13% | 18.23% | 2.10 pp |
| spread0.3 | 142 | 26.63% | 26.89% | 0.26 pp |
| **conservative_unmeasured (shipped)** | **484** | **16.17%** | **18.27%** | **2.10 pp** |
| **reward_guard (shipped)** | **484** | **16.17%** | **18.27%** | **2.10 pp** |
| ship_candidate_rr3_trailing_off | 244 | 53.05% | 52.89% | −0.16 pp |

Median **+1.55 pp**, worst **+2.61 pp**, and one run marginally *lower* — which
is possible, because a marked curve can also show a higher interim peak.

Reproduce: `scripts/research/remark_published_drawdowns.py`.

## 3. The one decision that rested on a drawdown

`min_rr 3.0` shipped explicitly and only as a risk control:

> Justified on drawdown and loss-streak length, **not** on return.
> — [MIN_RR_AS_RISK_CONTROL.md](MIN_RR_AS_RISK_CONTROL.md) §5

Its case was that the drawdown response across the sweep was monotone and large
while the return response was noise. If the old measure ranked those settings
wrongly, the shipped setting is wrong. Re-marked:

| `min_rr` | trades | published DD | marked DD | return |
|---:|--:|--:|--:|--:|
| 1.5 | 1,317 | 54.69% | 56.24% | −34.79% |
| 2.0 | 1,054 | 39.54% | 41.38% | +32.07% |
| 2.5 | 595 | 43.67% | 46.28% | −15.19% |
| **3.0** | **485** | **16.13%** | **18.23%** | **+60.55%** |

**The ranking is unchanged and the argument survives.** `min_rr 3.0` still has
by far the shallowest drawdown, still by a factor of two and a half, and the
same blip at 2.5 that the original document already reported. Nothing shipped on
this needs revisiting.

That is the useful outcome of the check, not a formality — the reason to run it
was that the understatement grows with concurrency, and the low-`min_rr` runs
hold far more trades, so the bias plausibly ran in the direction that would have
flattered the shipped choice. It did (1.55 pp against 2.10 pp) and it was
nowhere near large enough to matter.

## 4. What changed in the code

* **`backtesting/prices.py`** (new): daily closes for the traded symbols, from
  the same `load_history(purpose="backtest")` the engine reads. A symbol it
  cannot load is left out rather than failing the run.
* **`backtesting/equity.py`**: `daily_curve()` marks open positions;
  `max_drawdown()` and `max_drawdown_amount()` use it when prices are present;
  `closed_trade_max_drawdown()` keeps the old figure computable; `basis()` says
  which was used.
* **`backtesting/statistics.py`**: takes `prices`, and the summary gains
  `MaxDrawdownClosedTrades` and `DrawdownBasis`. `CalmarRatio` and
  `RecoveryFactor` now divide by the same drawdown that is reported — they were
  always consistent, and stay consistent, with whichever basis is in force.
* **`backtesting/report.py`**: the saved `equity_curve.csv` becomes one row per
  session with a `Date` column. The `Equity` column keeps its name and meaning,
  so the dashboard chart that reads it is unaffected.
* **`services/backtest_service.py`** and
  **`strategy_momentum_breakout/runner.py`**: build the price frame once and
  pass it through.

**Nothing silently switched.** Without prices the behaviour is exactly what it
was and `DrawdownBasis` reads `CLOSED_TRADE`. This project has one cautionary
example of the alternative — `require_market_analyzer`, a gate switched on in
the settings, unable to fire, reporting `PASS` on every bar for years
([DAILY_STRATEGY_DIAGNOSIS.md §7](DAILY_STRATEGY_DIAGNOSIS.md)) — and a metric
that quietly changed meaning between two runs of the same code would be the same
failure wearing different clothes.

## 5. What this does **not** fix

* **Intraday drawdown is still invisible.** The mark is a daily close, so a
  position that fell 20% inside a session and recovered by the bell does not
  appear. The figure is still a floor, just a much closer one.
* **The historical documents are not rewritten.** Fifteen files in this
  directory quote a drawdown measured the old way. They are records of what was
  measured when they were written, and editing their numbers without re-running
  their experiments would be worse than leaving them. §2 is the conversion
  table; the affected documents now carry a pointer to it.
* **`FinalCapital`, `TotalReturn` and `CAGR` are unchanged**, deliberately.
  Every position is closed by the end of a backtest, so they are the same
  quantity either way, and keeping them on the realised sum means they cannot
  drift with a price frame.

## Limits

- The re-priced runs use today's price cache, not the cache as it stood when
  each run was made. The exact reproduction of every closed-trade figure in §2
  is strong evidence the trade records are intact, but the marked figures assume
  the price history has not been revised.
- Symbols the provider can no longer serve are carried at cost, which biases the
  marked drawdown *down*. The corrected numbers remain a floor.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/remark_published_drawdowns.py
venv/Scripts/python.exe scripts/research/isolated_backtest.py --label drawdown_marked
venv/Scripts/python.exe -m strategy_momentum_breakout.runner
```
