# CONFIRMED_VOLUME_BREAKOUT — A Second Swing Strategy, Built From Measurement

**Question:** the Daily Dashboard strategy's gate stack has no out-of-sample
edge ([DAILY_STRATEGY_DIAGNOSIS.md](DAILY_STRATEGY_DIAGNOSIS.md) §1). Built from
scratch against everything that diagnosis found, what does this market actually
support?

**Status:** built, backtested, tested, and on the dashboard as its own page at
`/confirmed-breakout`, defaulted to decision-support. `strategy/` is untouched
and the Daily Dashboard strategy is unchanged. Portfolio limits set to
**1.0 / 15.0 / 15** on 2026-08-29 from
[CAPACITY_IS_THE_CONSTRAINT.md](CAPACITY_IS_THE_CONSTRAINT.md); every figure in
§9 is measured at those limits.

**Short answer:** one trigger survives both eras — a twenty-day breakout
confirmed by volume — and three things added to it survive too. Against the
Daily Dashboard strategy: profit factor **1.93 against 1.29**, Sharpe **1.89
against 0.55**, drawdown **11.5% against 18.3%**, return **+136% against +60%**.
Both drawdowns are marked to market; the numbers those strategies' own earlier
audits quote are closed-trade and about two points lower
([DRAWDOWN_WAS_UNDERSTATED.md](DRAWDOWN_WAS_UNDERSTATED.md)).

**And the thing that is not being claimed:** over the same window the median EGX
name returned **+403%** simply held. This rule selects better than the one
beside it. It does not beat owning the market.

**And none of it is out-of-sample.** Forward testing started on 2026-08-29 and
is the only evidence here that will ever be —
[FORWARD_TESTING_STARTED.md](FORWARD_TESTING_STARTED.md). It is one session long
today and will be worth reading in most of a year.

---

## 0. The two honest caveats, first

**This is a re-derivation, not a discovery.** `services/swing_breakout.py` —
already in this repository, already on the Swing Breakout page — was derived from
the same market and reached a close relative of the same rule: a twenty-day
breakout on 2.5× volume, in a liquid name above its own 200-day average, held
twenty sessions. That was found *after* this work had independently landed on
the same trigger. Two independent passes agreeing is stronger evidence than
either pass alone, and §7 measures what this rule adds over that one.

**The benchmark is brutal and stays in every table.** In nominal EGP across
2016–2026 the Egyptian market compounded enormously. A strategy in cash 70% of
the time cannot compete with that on total return and this one does not. Every
figure below is therefore reported as **lift** — the return over owning an
average liquid name on the *same days*, for the *same holding length*, net of
that symbol's own round trip.

---

## 1. What the data supports, and what it does not

Twenty conditions, over 191 symbols and 382,646 bars, split at 2023-01-01,
twenty-bar hold, net of each symbol's measured round trip, lift over the
top-half-by-turnover pool:

| Rule | train lift | valid lift | bad years |
|---|--:|--:|--:|
| **20-day breakout + volume ≥ 2.5×** | **+3.13%** | **+1.36%** | **1/10** |
| 60-day breakout | +5.45% | +0.35% | 1/10 |
| within 5% of the 52-week high | +3.51% | −0.27% | 4/10 |
| price above its 200d | +0.43% | −0.11% | 4/10 |
| 12-1 momentum, top third | −0.25% | +0.37% | 3/10 |
| RSI 45–65 | −0.20% | −0.27% | 4/10 |
| MACD > signal > 0 | +1.32% | +0.05% | 2/10 |
| **the Daily Dashboard's own gate stack** | **+1.91%** | **−0.22%** | 3/10 |

Almost everything strong in 2016–2022 is zero or negative in 2023–2026. The
breakout is the exception, and it is the exception at both twenty and forty bars.

Reproduce: `scripts/research/signal_scan.py`.

## 2. The clock starts at the next close, and that costs 1.3 points

The signal is a close. It is knowable only after the close, so the first price
anybody can pay is the next session's.

| Group | next-day return |
|---|--:|
| every bar | +0.132% |
| 20-day breakout | +0.854% |
| **20-day breakout + volume ≥ 2.5×** | **+1.064%** |
| 20-day breakout + volume ≥ 4× | +1.230% |

The day after is a continuation, not a give-back — which means you buy *after*
it, and the lift falls accordingly:

| Measurement | train lift | valid lift |
|---|--:|--:|
| hold 20, from the signal close | +3.13% | +1.36% |
| **hold 20, from the NEXT close** | **+1.86%** | **+1.29%** |
| hold 40, from the signal close | +3.03% | +0.21% |
| hold 40, from the NEXT close | +1.60% | +0.14% |

Two decisions fall out. The clock starts at the next close, everywhere below. And
the hold is twenty bars, not forty: the edge decays inside the second month.

The open is never read, here or in the strategy. In this project's data it is
carried forward from the previous close on 96–98% of bars and is not a price
anybody traded at. `tests/test_confirmed_volume_breakout.py` pins that by
corrupting it and asserting nothing changes.

Reproduce: `scripts/research/entry_day_decay.py`.

## 3. What earns a place on top of the trigger

Filters added one at a time — never stacked, so a result belongs to an idea
rather than to a combination nobody can interpret:

| Added to `20d breakout + volume ≥ 2.5×` | train lift | valid lift | bad years |
|---|--:|--:|--:|
| (nothing) | +3.13% | +1.36% | 1/10 |
| **close in the top 25% of the bar** | **+5.68%** | **+1.93%** | 1/10 |
| **ATR% below the universe median** | **+3.47%** | **+2.49%** | 1/10 |
| market above its own 200d | +4.65% | +1.36% | 1/10 |
| 12-1 momentum top half | +3.24% | +2.79% | 2/10 |
| within 10% of the 52-week high | +5.22% | +1.37% | 1/10 |
| top quartile turnover | +2.84% | +1.25% | 2/10 |
| not extended (<8% over EMA20) | −0.14% | +2.02% | 3/10 |

Two improve **both** eras with a mechanism you can state in a sentence: the
breakout has to hold into the close rather than fade, and the name should not
already be wild. Those are kept.

Reproduce: `scripts/research/breakout_study.py`.

## 4. The stop, and why it is deliberately wide

Stop distance swept against **the rule as shipped**, everything else fixed:

| Stop | risk per share | train lift | valid lift | median trade | bad years |
|---|--:|--:|--:|--:|--:|
| 1.5 ATR | 5.5% | +1.48% | **−1.36%** | −5.66% | 4/10 |
| 2.5 ATR | 9.2% | +3.09% | **−0.47%** | −3.12% | 4/10 |
| 4 ATR | 14.7% | +3.27% | +1.41% | −0.57% | 3/10 |
| **under the 20-bar base − 0.3 ATR** | **20.4%** | **+3.85%** | **+1.92%** | −0.20% | **2/10** |
| none at all | — | +4.01% | +2.57% | +0.43% | 0/10 |

Monotone, in both eras, on the median as well as the mean. **A stop inside this
market's daily noise is a fee, not a protection.**

The same finding is visible in the Daily Dashboard strategy from the other side:
its EMA20 trailing stop ends **390 of 484 trades** at an average of −0.47%
([DAILY_STRATEGY_DIAGNOSIS.md §9](DAILY_STRATEGY_DIAGNOSIS.md)).

A stop is nonetheless kept, for two reasons. "None at all" is not a risk policy —
it is an undefined loss on a single position, and no backtest of 191 surviving
symbols can price the one that goes to zero. And the difference between the base
stop and no stop is inside the noise. Risk is controlled by **position size**,
which falls automatically as the stop widens, so a wide stop does not mean a
large loss.

Targets were tested and are worse than useless: a 2R target gives +0.96%/+0.67%,
a 3R target +3.32%/+1.28%, against +5.04%/+3.34% for letting the clock run. So
there is no target, which also means there is no target to place below the entry.

Reproduce: `scripts/research/breakout_exits.py`,
`scripts/research/shipped_rule_evidence.py`.

## 5. Every threshold is a plateau, and the holding cap is a genuine peak

Roughly fifteen filters, five stops and five holding caps were compared with both
eras visible. At that many comparisons a rule that wins on one exact set of
thresholds and collapses one notch either side is a coincidence with good
manners. So each threshold was moved on its own, against **the rule as shipped**:

| Sweep | | | | | |
|---|--:|--:|--:|--:|--:|
| breakout base (bars) | 10 | 15 | **20** | 30 | 40 |
| valid lift | +1.60 | +1.82 | **+1.92** | +1.54 | +2.06 |
| volume ratio | 1.5 | 2.0 | **2.5** | 3.0 | 4.0 |
| valid lift | +1.01 | +1.25 | **+1.92** | +1.41 | +2.45 |
| close position | 0.5 | 0.6 | **0.7** | 0.8 | 0.9 |
| valid lift | +1.60 | +1.97 | **+1.92** | +0.74 | +0.97 |
| calm window (bars) | 120 | **250** | 500 | | |
| valid lift | +1.15 | **+1.92** | +1.95 | | |
| turnover floor (M EGP) | 1 | **2** | 5 | 10 | |
| valid lift | +1.89 | **+1.92** | +1.65 | +0.91 | |
| holding cap (bars) | 10 | 15 | **20** | 25 | 30 |
| valid lift | +1.10 | +1.22 | **+1.92** | +0.92 | +0.41 |

Every sweep stays positive in both eras across its whole range. The holding cap
is the one with an interior maximum, and it sits on the shipped value.

### A correction, because it changes what a reader would conclude

An earlier version of this section reported the holding cap rising monotonically
— +2.33% at twenty bars against +2.70% at twenty-five and +3.04% at thirty — and
said that twenty-five and thirty had been passed over as "the top of a sweep".
**That was a measurement error, not caution.** The benchmark was built once at a
twenty-bar horizon and reused for every cap, so a forty-bar trade was being
compared against a twenty-bar benchmark and credited with the market's own extra
drift.

Rebuilt at each cap, the sweep peaks at twenty and decays after it — down to
**−0.25% at forty bars**, which is the same decay §2 measured on the raw trigger.
The two now agree, where before they contradicted each other and the
contradiction was not chased. Fixed in `shipped_rule_evidence.py`,
`breakout_robustness.py` and `breakout_candidate.py`.

Below twenty bars there is a mechanism for the fall too: the drift does not cover
the ~1.4% round trip, the same arithmetic that retired this project's intraday
strategies.

**Walk-forward**, judging each year only by whether the rule had been working
through the end of the prior year:

| Year | signals | lift up to then | that year's lift |
|---|--:|--:|--:|
| 2019 | 12 | +3.11% | +3.77% |
| 2020 | 25 | +3.33% | +5.32% |
| 2021 | 73 | +4.15% | +9.86% |
| 2022 | 108 | +7.26% | **−0.21%** |
| 2023 | 137 | +3.92% | +0.38% |
| 2024 | 169 | +2.64% | +1.86% |
| 2025 | 262 | +2.40% | +3.59% |
| 2026 | 143 | +2.79% | **−0.07%** |
| **pooled** | **929** | | **+2.41%** |

Two of eight years are slightly negative. The rule is not a machine.

Reproduce: `scripts/research/shipped_rule_evidence.py`.

## 6. The rule

1. **Liquidity** — 20-day average turnover ≥ 2,000,000 EGP.
2. **Price integrity** — no session in the last 20 bars moved more than ±30%.
3. **Long-term trend** — close above its own EMA200.
4. **Calm** — ATR% at or below the name's own 250-bar median ATR%.
5. **Breakout** — close above the highest high of the previous 20 bars,
   excluding today.
6. **Volume** — at least 2.5× its own 20-day average volume.
7. **Close position** — the close in the top 30% of the day's range.

**Entry** at the next session's close. **Stop** at the 20-bar low − 0.3 ATR,
fixed. **Exit** on the stop or after 20 sessions. **No target, no trailing stop,
no score, no confidence percentage.**

Rules 3 and 4 are self-referential and rule 1 is absolute, so nothing needs a
cross-section at scan time. That was a deliberate simplification and it was
measured, not assumed: an equal-weighted breadth index built from the universe
was tried as a market-regime gate and did **not** earn its place — it removed 68
of 971 trades and left the validation lift unchanged at +2.33%. `Close > EMA200`
measured better (+2.57%), costs nothing, and is readable off the symbol's own
chart.

That decision also avoided depending on `^CASE30`, which this project cannot
reach: the Daily Dashboard's `require_market_analyzer` gate is switched **on** in
`config/settings.json` and silently defaults to "allow" on every bar, in backtest
and live alike, because the series has one bar
([DAILY_STRATEGY_DIAGNOSIS.md §7](DAILY_STRATEGY_DIAGNOSIS.md)).

## 7. Against the strategy this project already ships beside it

Both run through the same simulator, same costs, same entry convention, same
benchmark:

| Rule | trades | train lift | valid lift | median | win | PF | bad years |
|---|--:|--:|--:|--:|--:|--:|--:|
| Swing Breakout, as it ships | 575 | +0.29% | +1.92% | +0.12% | 50% | 1.74 | 2/10 |
| the same, plus a base stop | 578 | +0.51% | +1.26% | −0.51% | 49% | 1.63 | 3/10 |
| **CONFIRMED_VOLUME_BREAKOUT** | **884** | **+4.37%** | **+2.45%** | +0.17% | 50% | **2.13** | **0/10** |

That table comes from `against_swing_breakout.py`, which reconstructs both rules
in the research harness. The shipped module measured directly
(`shipped_rule_evidence.py`, which imports `strategy_momentum_breakout` rather
than restating it) gives **953 trades, +3.85% / +1.92%, PF 2.04, 2 of 10 years
with negative lift** — the same conclusion, slightly less flattering, and it is
the one quoted everywhere else in this document. Where two of my own measurements
disagree, the one that reads the shipped code wins.

And the three additions, put on top of Swing Breakout's own rule:

| Added | trades | train lift | valid lift |
|---|--:|--:|--:|
| + close in the top 30% of the bar | 366 | −0.01% | +1.30% |
| + calm vs its own past year | 373 | +0.14% | +3.21% |
| + price-integrity guard | 571 | +0.29% | +2.06% |
| + all three | 229 | +1.35% | +3.24% |

Swing Breakout's own write-up quotes +3.90%/+5.22%. That measurement starts the
clock at the signal's own close and benchmarks against the whole universe rather
than the liquid half; §2 is the size of the first difference. This is a
difference in measurement convention, not a dispute about the trigger.

Reproduce: `scripts/research/against_swing_breakout.py`.

## 8. The price-limit guard, which changed the result more than any parameter

This project backtests on **unadjusted** prices — `Close` differs from
`Adj Close` on 24 of 40 sampled symbols. So a split arrives as a collapse and a
reverse split as a spike. EGX's daily price limit is ±10% on most listings; 99
sessions of 382,646 move more than ±30%, and none of them is a market move.

Untreated, they were the entire tail of the record in both directions:

- five trades closed as stops at −30.6%, −36.6%, −49.4%, −53.0% and **−67.8%**,
  the last reported as the strategy's largest loser;
- and the largest **winner**, +78,117 EGP on EHDR.CA, sat across 2025-10-21, a
  single session printing **+394.9%**.

Removing only the losses would have been marking one's own homework, so the guard
is symmetric. It costs the headline more than it saves:

| | untreated | with the guard |
|---|--:|--:|
| Total return | +157.73% | **+113.12%** |
| Best trade | +78,117 | +10,678 |
| Worst trade | −18,231 | −3,801 |
| Sharpe | 0.86 | **1.28** |
| Max drawdown | 16.59% | **14.22%** |

The Daily Dashboard strategy is nearly clean of this by accident: at 4.5-day
holds only 1 of its 484 trades spans such a session, worth −0.0% of its profit.
The exposure is a function of holding period, which is why it matters here and
not there.

## 9. The portfolio result

Same universe, same backtest provider, same per-symbol spread table, same
`PortfolioSimulator`, same `BacktestStatistics`:

| | Daily Dashboard | CONFIRMED_VOLUME_BREAKOUT |
|---|--:|--:|
| Trades | 484 | 634 |
| Win rate | 39.88% | **52.05%** |
| Profit factor | 1.29 | **1.93** |
| Total return | +60.08% | **+135.82%** |
| CAGR | +5.43% | **+10.16%** |
| **Max drawdown** (marked to market) | **18.27%** | **11.54%** |
| the same, closed-trade only | 16.17% | 8.79% |
| Sharpe | 0.55 | **1.89** |
| Sortino | 1.27 | **5.21** |
| Calmar | 0.30 | **0.88** |
| Average per trade | +0.38% | **+4.60%** |
| Average hold | 4.5 days | 29.8 days |
| Max consecutive losses | 10 | 12 |
| Exposure | 23.59% | 32.20% |
| Trades per year | 54.4 | 71.5 |

The two columns share everything except the portfolio policy: the Daily
Dashboard strategy runs at 2% risk with a five-position cap and this one at 1%
with fifteen, because that is what was measured for each
([CAPACITY_IS_THE_CONSTRAINT.md](CAPACITY_IS_THE_CONSTRAINT.md)). Held at the
Daily Dashboard's own 2%/10%/10 this strategy returns **+113.12%** at a 14.22%
drawdown and Sharpe 1.28 — still ahead on every line, by less. Both readings are
above; neither is hidden.

Per year, the new strategy's executed trades:

| Year | trades | avg % | median % | win rate | net EGP |
|---|--:|--:|--:|--:|--:|
| 2017 | 10 | +7.29 | +7.05 | 70.0% | +3,431 |
| 2018 | 14 | +0.02 | −3.24 | 28.6% | **−1,741** |
| 2019 | 12 | −1.02 | −1.31 | 50.0% | **−1,687** |
| 2020 | 25 | +3.91 | −0.29 | 48.0% | +1,567 |
| 2021 | 72 | +10.65 | +1.93 | 54.2% | +26,322 |
| 2022 | 72 | +4.32 | +3.24 | 56.9% | +12,453 |
| 2023 | 103 | +4.72 | +0.39 | 51.5% | +30,277 |
| 2024 | 108 | +5.38 | +1.78 | 55.6% | +21,274 |
| 2025 | 162 | +2.75 | −0.67 | 46.9% | +21,232 |
| 2026 | 56 | +6.66 | +2.07 | 57.1% | +22,693 |

Two losing years of ten, 2018 and 2019 — the only bearish stretch in the window
— costing 3,428 EGP between them.

**And the distribution is not a lottery**, which is the sharpest difference from
the strategy it sits beside:

| | Daily Dashboard | this rule |
|---|--:|--:|
| Median trade | **−0.640%** | **+0.80%** |
| Share of trades profitable | 36.0% | **52.1%** |
| Best 5 trades, as a share of net profit | **108.4%** | **18.8%** |

The Daily Dashboard strategy's other 479 trades lose money in aggregate. This
one's other 629 do not. Exit mix: 573 reach the holding cap, 55 are stopped, 6
close on a corporate action.

**Capacity still binds, and it is the largest remaining constraint.** 927 signals
become 634 executed trades; 293 are refused because fifteen positions are already
open. That is down from 628 refusals under the original 2%/10%/10, which produced
a cap of **five** — `max_portfolio_risk_percent / risk_percent`, not the
`max_open_positions: 10` the file appeared to say.

Swept in [CAPACITY_IS_THE_CONSTRAINT.md](CAPACITY_IS_THE_CONSTRAINT.md) and
changed on 2026-08-29, because 1.0/15.0/15 dominated the old setting on both
axes at once — more return **and** less drawdown — with the worst single session
halved from −10.1% to −5.0%. Twenty and thirty positions measure better still and
were not taken: at 96–100% of equity committed there is no cash buffer, and this
backtest cannot price that.

That work also found something applying to **every drawdown this project had
published, for both strategies**: `backtesting/equity.py` booked profit only when
a trade closed and never marked open positions to market, so every `MaxDrawdown`
was a closed-trade drawdown, understating by more the more positions were held.
It is fixed, all 25 archived runs are re-priced, and no shipped decision
reverses -- [DRAWDOWN_WAS_UNDERSTATED.md](DRAWDOWN_WAS_UNDERSTATED.md). The
drawdowns in the table above are the corrected ones, which is why the Daily
Dashboard column reads 18.27% where its own audits say 16.17%.

Reproduce: `venv/Scripts/python.exe -m strategy_momentum_breakout.runner`, and
`scripts/research/compare_strategies.py` for the table above.

## 10. What was tried and rejected

| Idea | Verdict |
|---|---|
| Market-regime gate from a breadth index | Removed 68 of 971 trades, validation lift unchanged at +2.33%. Not kept. |
| `^CASE30` regime gate | The series has one bar. Impossible, and already silently dead in the neighbouring strategy. |
| Cross-sectional ATR% rank | Works (+2.12% valid) but the self-referential form is better (+2.33%) and needs no universe. |
| Cross-sectional turnover rank | Same: an absolute EGP floor measures as well and is simpler. |
| 12-1 momentum, top half | +2.79% valid but 2 bad years and it needs the cross-section. Left out; it is what Swing Breakout uses instead. |
| Targets at 2R / 3R | Much worse. Capping the upside removes the result. |
| Trailing stops (chandelier, EMA20, EMA50) | All worse than a fixed wide stop plus the clock. |
| Close-based exits (under EMA20 / EMA50) | +1.11% to +1.43% validation lift, below the base stop's. |
| Holding 25 or 30 bars | Looked better until the benchmark was matched to the holding length; then twenty is the peak. See §5. |

## Limits

- One dataset, one universe, ~190 symbols that **still exist**. Delisted names
  are absent from the strategy and from every benchmark alike, which flatters
  both and makes the *lift* the only figure worth quoting.
- The 2,000,000 EGP turnover floor is nominal, so it is progressively easier to
  clear across a decade in which the currency lost most of its value. The floor
  sweep (§5) shows the result is not created by it.
- The filter set was chosen with both eras visible. §5 is the defence, not a
  denial.
- Costs charge an August-2026 spread to trades from 2017 onward.
- Dividends are not credited: prices are unadjusted, so a dividend paid during a
  hold is lost. That understates every result here.
- Nothing in this document is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/panel.py
venv/Scripts/python.exe scripts/research/signal_scan.py
venv/Scripts/python.exe scripts/research/entry_day_decay.py
venv/Scripts/python.exe scripts/research/breakout_study.py
venv/Scripts/python.exe scripts/research/breakout_exits.py
venv/Scripts/python.exe scripts/research/breakout_candidate.py
venv/Scripts/python.exe scripts/research/breakout_selfreferential.py
venv/Scripts/python.exe scripts/research/breakout_robustness.py
venv/Scripts/python.exe scripts/research/against_swing_breakout.py
venv/Scripts/python.exe scripts/research/compare_strategies.py
venv/Scripts/python.exe -m strategy_momentum_breakout.runner
```
