# Portfolio Capacity, Not the Rule, Is What Binds

**Question:** CONFIRMED_VOLUME_BREAKOUT generates 927 trades and the simulator
executed 299 of them. The other 628 were refused for capacity. What did that
cost, and what should the limits be?

**Status:** **shipped 2026-08-29.** `strategy_momentum_breakout/settings.json`
and the `BreakoutConfig` defaults both now read `risk_percent 1.0 /
max_portfolio_risk_percent 15.0 / max_open_positions 15`, so a clean checkout
behaves the same as this machine. Confirmed by an as-configured run: 634 of 927
signals executed, +135.82% at a **marked** drawdown of 11.54% and Sharpe 1.89,
against 299 / +113.12% / 15.2% / 1.28 before. (Closed-trade, the basis this
project used until the same day: 8.79% against 14.22%.)

The trades are generated once and re-simulated under every setting below, so
each row sees an identical signal stream and the only difference is the
portfolio policy. **Nothing about the strategy changed** — the same 927 signals
are on every row.

**Short answer:** the limits as they stood held **five** positions, not ten, and
threw away two thirds of the signal stream at random. Spreading the same total
risk across fifteen positions instead of five raises the return from **+108% to
+133%** *and* cuts the drawdown from **15.2% to 11.5%**, with the worst single
session going from **−10.1% to −5.0%**. Every drawdown in this document is
marked to market.

---

## 1. Three properties of the simulator that decide how to read everything below

**The cap was five, not ten.** `PortfolioSimulator.__init__` computes
`effective_max_positions = min(max_open_positions, floor(max_portfolio_risk_percent
/ risk_percent))`. At the old 2.0 and 10.0 the floor division gives **5**, so
`max_open_positions: 10` was never the binding limit and reading the settings
file did not tell you the cap.

At the shipped 1.0 / 15.0 / 15 both terms are 15. That is deliberate — the two
limits agree, so neither is silently overriding the other — and
`tests/test_confirmed_volume_breakout.py` fails if they ever diverge again.

**Position size is computed from `initial_capital`, never from current equity.**
`_enter` constructs `PositionSizer(self.initial_capital, self.risk_percent)` on
every trade, so a position is the same number of EGP in 2026 as in 2017 and the
equity curve is a sum of fixed-size bets. **Total return is therefore roughly
linear in `risk_percent`** — doubling it roughly doubles both return and
drawdown, which is arithmetic and not an improvement. Every headline in this
document is instead marked to market and reported against its own drawdown.

**The reported drawdown was a closed-trade drawdown** when this sweep was run.
`backtesting/equity.py` stepped the curve one trade at a time in exit-date order
and booked profit only at exit; nothing open was ever marked to market. That
understates the real figure, and it understates it **more the more positions are
held at once** — which is exactly the variable being swept here, so a sweep that
trusted it would conclude diversification is free.

It was fixed the same day this was found, and every drawdown the project
publishes is now marked to market
([DRAWDOWN_WAS_UNDERSTATED.md](DRAWDOWN_WAS_UNDERSTATED.md)). The `closed-trade`
column below is what the summary would have said before that; the `marked`
column is what it says now:

| Cap | Closed-trade DD | Marked to market | Understated by |
|---:|--:|--:|--:|
| 3 | 6.2% | 8.8% | 2.6 pp |
| 5 | 7.5% | 8.0% | 0.5 pp |
| 10 | 9.6% | 11.4% | 1.8 pp |
| 15 | 8.8% | 11.5% | 2.8 pp |
| 20 | 9.9% | 13.0% | 3.0 pp |
| 30 | 9.8% | 12.6% | 2.8 pp |

The conclusion survives the correction, but it had to be checked — and the
correction applied to **every `MaxDrawdown` this project had ever published**,
not only to this sweep. That is written up separately in
[DRAWDOWN_WAS_UNDERSTATED.md](DRAWDOWN_WAS_UNDERSTATED.md), which re-prices all
25 archived runs from their own trade files: understated by a median of 1.55
percentage points, and no shipped decision reverses.

## 2. Capacity is rationing, not selection

When more signals arrive on a day than there is room for, `selection_key` ranks
them, falling through to `score`. This strategy has no score, so
`strategy_momentum_breakout/backtest.py` puts the volume ratio there as an
explicit capacity tie-break. Whether that tie-break picks the better trades had
not been measured:

| At 2% / 10% / 10 | trades | mean | median | win |
|---|--:|--:|--:|--:|
| Taken | 299 | +4.27% | +0.16% | 51% |
| **Refused** | **628** | **+5.45%** | **+1.06%** | 53% |
| Difference | | **−1.18%** | | **t = −0.89** |

The refused trades did **better**. The gap is not significant, and it should not
be acted on as a finding — but it settles the question it was asked: the
tie-break is not selecting, so the 628 refusals are a roughly random two thirds
of the stream rather than its worst two thirds. Raising the cap does not mean
scraping the barrel.

It still holds after the change, which is the check worth having: at
1% / 15% / 15 the 293 trades now refused measure **+5.38%** against the 634
taken at **+4.92%**, t = **−0.34**. Widening the cap did not start scraping a
barrel, and it did not turn the tie-break into a selector either.

## 3. Risk per trade, which also moves the cap

`max_portfolio_risk_percent` fixed at 10%, `max_open_positions` at 10 — the
baseline as it stood **before** this document's change. Sections 3 and 4 sweep
one setting around the other two as configured, so re-running the script now
sweeps around 1.0 / 15.0 / 15 and will not reproduce these two tables. Sections
5 and 7 set all three explicitly and do reproduce.

Their drawdowns are also closed-trade, the basis in force when they were
measured; §5 onward is marked.

| risk | cap | taken | of all | return | closed DD | Calmar | Sharpe |
|---:|---:|---:|---:|--:|--:|--:|--:|
| 0.5% | 10 | 489 | 53% | +42.3% | 5.4% | 0.75 | **1.48** |
| 1.0% | 10 | 489 | 53% | +84.6% | 9.6% | **0.75** | **1.48** |
| 1.25% | 8 | 423 | 46% | +95.1% | 11.7% | 0.67 | 1.39 |
| 1.5% | 6 | 346 | 37% | +98.6% | 12.8% | 0.63 | 1.42 |
| **2.0% (then shipped)** | **5** | **299** | **32%** | +113.1% | 14.2% | 0.63 | 1.28 |
| 2.5% | 4 | 248 | 27% | +115.9% | 14.7% | 0.62 | 1.16 |
| 3.0% | 3 | 190 | 20% | +93.2% | 16.0% | 0.48 | 0.92 |

Return rises with risk because position size does; Sharpe and Calmar fall,
monotonically, from 1.0% upward. Below 1.0% the cap stops moving because
`max_open_positions: 10` takes over.

## 4. Heat, which is the real cap

`risk_percent` fixed at 2%:

| heat | cap | taken | return | closed DD | Sharpe |
|---:|---:|---:|--:|--:|--:|
| 5% | 2 | 134 | +37.4% | 11.9% | 0.66 |
| **10% (then shipped)** | **5** | **299** | +113.1% | 14.2% | 1.28 |
| 15% | 7 | 385 | +151.4% | 15.9% | 1.46 |
| 20% | 10 | 489 | +169.2% | 15.6% | 1.48 |
| 30% | 10 | 489 | +169.2% | 15.6% | 1.48 |

Beyond 20% nothing changes: `max_open_positions: 10` binds instead. Note that
raising heat alone raises the cap **and** the risk taken, which is why §5
separates them.

## 5. The cap on its own, at constant risk per position

Risk fixed at 1% per trade; heat set to exactly 1% × the number of positions, so
every row risks the same amount per trade and differs only in how many it holds
at once:

| positions | taken | of all | return | marked DD | worst day | peak % of equity | cash refusals |
|---:|---:|---:|--:|--:|--:|--:|--:|
| 3 | 191 | 21% | +30.8% | 8.8% | | 38% | 0 |
| 5 | 299 | 32% | +56.6% | 8.0% | | 47% | 0 |
| 8 | 423 | 46% | +76.1% | 11.4% | | 57% | 0 |
| 10 | 489 | 53% | +84.6% | 11.4% | −5.68% | 61% | 0 |
| **15** | **634** | **68%** | **+135.8%** | **11.5%** | **−5.02%** | **82%** | **0** |
| 20 | 740 | 80% | +173.5% | 13.0% | −5.18% | 96% | 1 |
| 30 | 850 | 92% | +204.6% | 12.6% | −6.64% | 100% | 21 |
| 40 | 872 | 94% | +211.0% | 12.6% | | 100% | 35 |

Return rises sevenfold from three positions to thirty while the marked drawdown
moves from 8.8% to 12.6%. That is what diversification is supposed to look like,
and the correlation test in §6 is the reason to believe it here rather than
assume it.

The last three rows are not free. `peak % of equity` is the most of the account
committed at any one moment, against equity as it stood then: at twenty
positions it reaches **96%** and at thirty **100%**, and the simulator starts
refusing signals it cannot fund. A fully-invested account has no cash buffer,
and this backtest cannot price that — it has no gap risk beyond the daily close,
no margin and no forced liquidation.

## 6. The correlation test

If fifteen concurrent positions in one small market were really one bet fifteen
times over, the worst single session would scale with the position count. It
does the opposite:

| setting | positions | worst single day | worst 21 sessions |
|---|---:|--:|--:|
| **shipped: 2% / 10% / 10** | 5 | **−10.10%** | **−11.3%** |
| 1% / 10% / 10 | 10 | −5.68% | −6.6% |
| 1% / 15% / 15 | 15 | **−5.02%** | −6.1% |
| 1% / 20% / 20 | 20 | −5.18% | −6.0% |
| 1% / 30% / 30 | 30 | −6.64% | −5.9% |

**Concentration produces the tail day, not capacity.** Five positions at 2% risk
each lose 10.1% on their worst session; fifteen at 1% lose 5.0%. The shipped
setting is the most dangerous row in the table on the measure that matters most
to somebody watching the account.

## 7. The candidates, like for like

Marked to market daily, so the drawdowns are comparable to each other and to
what an account would have watched:

| setting | cap | taken | of all | return | marked DD | return/DD | worst day | peak % of equity | cash refused |
|---|---:|---:|---:|--:|--:|--:|--:|--:|--:|
| **previous: 2% / 10% / 10** | 5 | 299 | 32% | +107.8% | **15.2%** | 7.1 | **−10.10%** | 85% | 0 |
| 1% / 10% / 10 | 10 | 489 | 53% | +81.9% | 11.4% | 7.2 | −5.68% | 61% | 0 |
| **1% / 15% / 15 (shipped)** | **15** | **634** | **68%** | **+133.2%** | **11.5%** | **11.5** | **−5.02%** | **82%** | **0** |
| 1% / 20% / 20 | 20 | 740 | 80% | +170.9% | 13.0% | 13.2 | −5.18% | 96% | 1 |
| 1% / 30% / 30 | 30 | 850 | 92% | +201.9% | 12.6% | 16.0 | −6.64% | 100% | 21 |
| 1.5% / 22.5% / 15 | 15 | 634 | 68% | +199.4% | 15.0% | 13.3 | −5.98% | 98% | 3 |

## 8. What shipped

**`risk_percent: 1.0`, `max_portfolio_risk_percent: 15.0`,
`max_open_positions: 15`**, in `settings.json` and in the `BreakoutConfig`
defaults. Note that both terms of `min(max_open_positions, floor(heat / risk))`
now equal 15, deliberately: the two limits agree, so neither is silently
overriding the other the way `max_open_positions: 10` was being overridden by a
cap of five.

It dominates the shipped setting on both axes at once, which is rare enough to
be worth stating plainly: **more return (+133% against +108%) and less drawdown
(11.5% against 15.2%)**, with the worst single session halved. It takes 68% of
the signal stream instead of 32%, and §2 is why that matters — the refused ones
were not the bad ones.

It is also the last row with a genuine cash buffer: 82% peak deployment and zero
signals refused for want of funds. Twenty and thirty positions measure better
still, and did not ship, because at 96–100% committed the improvement is bought
with a posture this backtest cannot price.

**What it looks like as configured**, from a run with no overrides: 634 of 927
signals executed against 299 before, profit factor 1.93 against 1.78, Sharpe
1.89 against 1.28, Sortino 5.21 against 2.96. Its reported `MaxDrawdown` is now
**11.54%**, marked to market -- which the sweep here predicted at 11.5% from an
independently written implementation, so the two agree to a rounding. The
median trade goes from +0.16% to **+0.80%** and the best five trades fall from
29.6% of net profit to **18.8%** — the same rule, expressed by a portfolio that
is no longer forced to bet its result on a handful of positions.

The last row of §7 is the alternative worth knowing about: the same fifteen
positions at 1.5% risk each returns +199% instead of +133%, at a 15.0%
drawdown — the shipped drawdown for nearly twice the return. That is a real
choice rather than a ranking, and it is a choice about how much risk to take,
not about capacity.

**None of this changes the strategy.** It changes how much of the strategy the
portfolio is allowed to express, and every row above trades the identical 927
signals.

## Limits

- One dataset, one universe, in-sample. Capacity is not a signal parameter, so
  the overfitting risk is lower than for a threshold — but the correlation
  between EGX names in a real crisis is not in this sample, and 2018–2019 is the
  only bearish stretch it contains.
- The mark-to-market curve values a symbol that did not trade that session at
  cost rather than at a stale price, which slightly damps the drawdown.
- Sizing off `initial_capital` is a property of the simulator, not a
  recommendation. A compounding account would see roughly three times these
  returns and a drawdown around 13.7% at the higher caps.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/capacity_sweep.py
```
