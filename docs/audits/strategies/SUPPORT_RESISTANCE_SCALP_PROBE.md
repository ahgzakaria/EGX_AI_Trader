# Support/Resistance Range Scalping on EGX — Measurement Probe

**Question asked:** in EGX stocks that swing hard between a support and a
resistance level, does buying near support and selling near resistance, inside
one session, make money?

**Status:** research probe only. Nothing was enabled, no strategy, threshold or
config was changed. The probe
(`scripts/research/probe_support_resistance_scalp.py`) is read-only against
`data/rubix_live_market.db` and writes only `reports/range_scalp_probe_*.csv`.

**Short answer:** the idea is now *measurable* for the first time — and measured,
it loses. The entry moment carries a small real signal, but it is the same size
as the round-trip friction, and it does not hold up across the sample.

---

## 1. The blocker from the V2 report is gone

`SCALPING_V2_RANGE_STRATEGY_REPORT.md` refused to test this idea because no EGX
session had a single symbol at 60% one-minute coverage. That is no longer true.

| Window | Sessions | Median coverage | Symbols ≥ 60% |
|---|--:|--:|--:|
| 2026-07-14 → 07-30 | 12 | 0.4% – 27% | **0** |
| 2026-08-02 → 08-25 | 16 | **~98%** | **182 – 203** |

Something in the collector changed on 2026-08-02. Candles are genuine, not
forward-filled padding: of 831,228 session minutes, 587,827 carry positive
volume and only 1,804 have a single update. Two dates (08-17, 08-20) hold one
minute each and are excluded by the coverage gate.

Spreads also read far better than V2 reported, because V2 measured them after
the close on stale quotes. Measured *inside* the session: universe median
**0.40%**, with 146 of 217 symbols at ≤ 0.5%.

## 2. What was simulated

Universe: median daily turnover ≥ 5M EGP **and** median in-session spread
≤ 0.5% → **141 symbols**, **2,255 symbol-sessions** across 16 sessions.

Per symbol per session:

- Support/resistance = low/high of the **first 60 minutes only**. The trading
  window (11:00–14:25 Cairo) is disjoint from it, so no level is ever built from
  a bar the trade could have used.
- The session must actually *oscillate*: price must cross the range midpoint at
  least twice in the opening hour, and the range must be 1.5%–12% wide.
- Buy when a minute's low enters the bottom 20% of the range; **fill at the next
  minute's open**, never at the signal bar's close.
- Sell at 15% below resistance; stop 40% of a range-width below support; forced
  flat 5 minutes before the close. A minute touching both target and stop is
  scored as the stop.
- Costs: the real broker schedule in `config/settings.json`
  (0.1819%/side + 4 EGP/order) plus half the measured per-symbol-per-day spread
  on each side.

## 3. Result

```
trades 1,717   win rate 19.0%   avg net -0.903%/trade   16 of 16 sessions negative
exits: TARGET 234 | STOP 1,046 | TIME 437
```

Support broke **4.5 times more often** than resistance was reached. Nine
parameter combinations were swept (range width 1.5/2.5/4.0%, stop 0.25/0.40/0.75
range-widths); **every one is net-negative**.

## 4. The arithmetic that kills it

| Item | Value |
|---|--:|
| Median opening-hour range of a traded name | 2.36% |
| Capturable slice (entry 20% up, exit 15% down) | 1.53% |
| Broker round trip | 0.364% |
| Spread round trip (median 0.34% on traded names) | 0.34% |
| Order fees on a 20,000 EGP ticket | 0.04% |
| **Total friction** | **0.75%** |
| Friction as a share of the capturable move | **49%** |
| Win rate needed to break even at observed payoffs | **59.9%** |
| Win rate observed | **19.0%** |

Half the move is gone before the trade is right. For friction to fall to a
tolerable 20% of the capture, the range would have to be **≥ 5.7% wide** — and
only **12.3%** of liquid EGX symbol-sessions offer that in the opening hour
(p50 = 2.13%, p90 = 6.36%).

## 5. What is *not* wrong with the idea

The absolute loss is not proof the concept is noise, because the measured window
fell: buying any of these symbols at the trading-window open and holding to the
close lost **-0.95%** on average, and drift was negative in 11 of 16 sessions.
A long-only strategy inherits that.

So the probe also asks a drift-neutral question: **does the support touch beat
entering the same symbol, the same day, for the same holding time, at a random
minute** — both sides paying identical costs?

| Configuration | Trades | Edge vs random entry | t |
|---|--:|--:|--:|
| width ≥ 1.5%, stop 0.40 | 1,717 | +0.107% | +3.32 |
| width ≥ 2.5%, stop 0.75 | 624 | +0.251% | +3.88 |
| width ≥ 4.0%, stop 0.75 | 235 | **+0.434%** | +3.12 |

The edge is positive in **14 of 16 sessions** and for **56 of 94** symbols with
at least 10 trades. So the level does mean something: a touch of the opening
hour's low is a measurably better moment to buy than an arbitrary minute, and
the effect grows with range width.

**But it does not survive scrutiny.** Split-half on the base configuration:

| Half | Trades | Edge | t |
|---|--:|--:|--:|
| First 8 sessions | 551 | +0.160% | +3.42 |
| Last 8 sessions | 727 | +0.066% | +1.51 |

The edge halves and loses significance. And even the best-case +0.434% is
roughly the size of the 0.75% friction it would have to pay — it does not clear
its own costs, let alone leave a profit.

## 6. The constraint nothing in the repo can answer yet

Everything above assumes the position can be sold the same session. On EGX that
is only true for securities on the approved intraday-trading (T+0) list.
`data/universe/egx_intraday_eligibility.csv` **ships empty**, and
`core/sector_context.py` correctly resolves every symbol to `UNKNOWN` rather
than assuming eligibility. Until that list is populated from the official
source, the tradable universe for *any* EGX scalping strategy is unknown, and
the 141-symbol universe used here is an upper bound, not a candidate list.

## 7. Re-run on a rising market

The first pass could be dismissed as "you measured a falling market". So the
probe was re-run with regime filters. The answer is sharper than expected.

### 7.1 There is no rising *intraday* window in the data

Quote history in `rubix_live_market.db` begins 2026-07-14 and the dense candle
era begins 2026-08-02, so the only testable ground is those 16 sessions. July
cannot be rescued: the raw quotes themselves only touch ~69 of 270 minutes per
symbol, so rebuilding candles from them adds no minutes.

And within those 16 sessions, the market *did* rise — but not where a scalper
can reach it. Decomposing the median symbol's day:

| Segment | Cumulative over 16 sessions |
|---|--:|
| Overnight gap / opening auction | **+5.36%** |
| Opening hour (10:00–11:00) | -0.82% |
| **Rest of session — the scalping window** | **-6.38%** |
| Full session, open to close | -6.37% |

The entire advance happened while the market was closed. A long-only same-session
strategy is structurally confined to the segment that bled.

### 7.2 Filtering for rising sessions, without look-ahead

Session breadth = share of the universe whose opening hour closed above its open,
known at 11:00 Cairo, before any trade. Five of sixteen sessions clear 50%.

| Run | Sessions | Trades | Win rate | Avg gross | Avg net | Benchmark |
|---|--:|--:|--:|--:|--:|--:|
| All sessions | 16 | 1,717 | 19.0% | -0.500% | -0.903% | -0.949% |
| Rising breadth | 5 | 349 | **26.9%** | **-0.273%** | -0.677% | -0.164% |
| Rising breadth + rising symbol | 5 | 140 | 26.4% | -0.288% | **-0.692%** | **+0.020%** |

The filter helps, and the improvement is real: win rate climbs from 19% to 27%
and gross loss halves. It is still not enough — and the third row is the
decisive one, because there the benchmark is **+0.02%**, i.e. market drift has
been neutralised. In a flat market the strategy still loses **0.692% per trade**,
which is almost exactly the 0.75% friction. Its timing edge in that condition is
+0.306% — real, significant (t = 3.23), and less than half of what it must pay.

### 7.3 The one genuinely rising session

Ranking sessions by *realised* trading-window drift — look-ahead, so a diagnostic
upper bound, not a strategy — **exactly one of the sixteen** was positive:

| 2026-08-02 | Value |
|---|--:|
| Buy-and-hold the same names over the same window | **+0.666%** |
| Range scalp, 40 trades | **-0.525%** |
| Win rate | 30.0% |

On the single rising intraday session available, doing nothing but holding beat
the range scalp by 1.19 points.

Pushing to the widest, most favourable settings inside the drift-neutral filter
(range ≥ 4–5.7%, wide stop) drives average gross to roughly zero and leaves net
at -0.41% to -0.43%, i.e. pure cost — but on 8 to 16 trades, which is too few to
conclude anything from. The reliable sample is the 140-trade row above.

## 8. Verdict

- **Do not build it as specified.** In 16 dense sessions and 9 parameter
  settings it lost in all of them, and the friction/range arithmetic explains why
  independently of the sample.
- **The measurable part is worth keeping.** The support-touch entry does beat
  random timing, and the effect scales with range width. That is an argument for
  using support as a *timing filter on a position you were going to take anyway*,
  not as a standalone round trip.
- **A rising market does not rescue it** (§7). Filtering to rising sessions
  neutralises the drift and the residual loss is then simply the friction. On the
  one genuinely rising intraday session, holding beat scalping by 1.19 points.
- **The sample is small.** 16 sessions is the whole dense era; only 5 clear the
  rising-breadth filter and only 1 rose intraday. This refutes the strategy as
  stated on the evidence available; it is not a verdict on every regime. Keep
  collecting dense sessions and re-run when a genuinely rising *intraday* stretch
  exists.
- **Populate the T+0 list first.** Without it, none of this is actionable.

## Reproduce

```
venv/Scripts/python.exe scripts/research/probe_support_resistance_scalp.py --tag base
venv/Scripts/python.exe scripts/research/probe_support_resistance_scalp.py --min-width 4.0 --stop-frac 0.75 --tag wide
venv/Scripts/python.exe scripts/research/probe_support_resistance_scalp.py --regime up --tag regime_up
venv/Scripts/python.exe scripts/research/probe_support_resistance_scalp.py --regime up --symbol-regime up --tag both_up
venv/Scripts/python.exe scripts/research/probe_support_resistance_scalp.py --oracle up --tag oracle_up
```
