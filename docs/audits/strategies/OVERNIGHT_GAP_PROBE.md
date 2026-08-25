# The EGX Overnight Gap — Measurement

**Question:** the scalping probe found that over 16 dense sessions the median
symbol gained ~5.4% overnight and lost ~6.4% during the session. Is the
overnight leg real, is it executable, and does it survive costs?

**Status:** research probe only. `scripts/research/probe_overnight_gap.py` is
read-only and writes `reports/overnight_gap_*.csv`. No strategy, threshold or
config was changed.

**Short answer:** the gap is real, executable and remarkably consistent — and on
a one-night hold it is almost exactly cancelled by the round trip. Its value
only appears when the same round trip is spread over several nights.

---

## 1. First: the daily `open` field cannot answer this

The obvious approach is to split the daily return into
`open/prev_close` and `close/open` over the full EODHD history. That does not
work, and the reason matters for the rest of the project.

Over 237,238 liquid symbol-sessions (2016-01-04 → 2026-08-17) in
`data/frozen_eodhd_seed`:

- **67.1% of opens are exactly equal to the previous close.**
- Cross-checked against real traded minutes on the 2,203 overlapping
  symbol-sessions, the EOD `open` sits on average **-0.712%** below the first
  actually-traded minute (median -0.379%; 80.3% differ by more than 0.1%).
- The EOD `close`, by contrast, matches the last traded minute closely:
  mean -0.038%, median 0.000%, only 18.5% differ by more than 0.1%.

So the `open` field is largely carried forward, not observed. Any gap statistic
computed from it is an artifact — it reports a mean gap of +0.078% and pushes
the real overnight move into the "intraday" leg. Long-history verification of
anything gap-related is **not currently possible** with this feed.

The measurement below therefore uses real traded minutes from
`data/rubix_live_market.db`, which limits it to the 16 dense sessions.

## 2. The opening print is genuine and executable

Before trusting a 10:00 price, it has to be a real cross rather than an
indicative print:

| Check | Result |
|---|--:|
| Median volume in the opening minute | **8,236** |
| Median volume in a typical minute | 669 |
| Opening minutes with zero volume | 3.2% |
| Median quote updates in the opening minute | 30 |

Twelve times a normal minute's volume. It is the opening auction cross, and it
trades.

It also does not immediately reverse — which would be the signature of a stale
or indicative print:

| Return after the open | Mean | Median |
|---|--:|--:|
| First 5 minutes | +0.006% | +0.000% |
| First 15 minutes | +0.053% | +0.000% |
| First 30 minutes | +0.092% | +0.000% |
| First 60 minutes | +0.116% | -0.084% |

The gap sticks.

## 3. The gap itself

2,891 symbol-transitions over 16 sessions, close of T to open of T+1:

| Leg | Mean | Median | Positive | t |
|---|--:|--:|--:|--:|
| **Overnight gap** | **+0.663%** | **+0.370%** | **72.6%** | **+15.7** |
| Next session, open to close | -0.029% | -0.418% | 39.1% | -0.4 |

And it is positive on **every one of the 13 measurable transitions**, with per-day
means from +0.23% to +0.93% and 68–86% of symbols gapping up each night.

That is the whole story of this window in one line: the market's return arrived
overnight, and the session gave it back.

## 4. But one night does not pay for itself

Cost to capture a single gap: 0.364% broker round trip plus the per-symbol
spread crossed twice (median ~0.40%) — about **0.76%**.

| | Mean | Median | Net-positive |
|---|--:|--:|--:|
| Gap, raw | +0.663% | +0.370% | 72.6% |
| Gap, net of one round trip | **+0.095%** | **-0.150%** | **39.7%** |

The mean survives; the median does not. A positive mean with a negative median
means a right tail is carrying the result — most nights lose a little and a few
pay for them. That is not a base to build on.

## 5. Where the value actually is

Friction is a **fixed toll per round trip**. The gap is a **per-session accrual**.
So holding across more nights divides the toll without re-paying it:

| Nights held | n | Raw mean | Raw median | Net mean | Net median | Net-positive |
|---:|--:|--:|--:|--:|--:|--:|
| 1 | 2,891 | +0.662% | +0.370% | +0.095% | -0.150% | 39.7% |
| 2 | 2,688 | +1.388% | +0.483% | +0.821% | -0.069% | 48.5% |
| **3** | 2,494 | +2.132% | +0.838% | +1.566% | **+0.245%** | **53.1%** |
| 5 | 2,097 | +3.649% | +1.444% | +3.086% | +0.945% | 58.0% |
| 8 | 1,526 | +6.353% | +2.801% | +5.796% | +2.254% | 61.9% |

Break-even on the median lands at about **three nights**.

**These windows overlap**, so the rows are not independent observations and the
proportions are optimistic. The longer holds also increasingly just measure "the
market rose over three weeks" rather than any gap effect. The structural point,
however, does not depend on the regime: *the toll is per trip, the accrual is per
night, so trip count is the lever.*

## 6. What this says about the scalping idea

It explains the earlier refutation
([SUPPORT_RESISTANCE_SCALP_PROBE.md](SUPPORT_RESISTANCE_SCALP_PROBE.md))
mechanically rather than statistically. A same-session strategy:

- is confined to the open-to-close leg, which in this window averaged **-0.029%**
  and had a median of **-0.418%**;
- pays the full 0.76% toll every single day;
- and structurally cannot touch the +0.663% that arrives while it is flat.

Trading more often is not a neutral choice on EGX. It is a decision to pay the
toll more times for the part of the day that historically did not pay.

## 7. Limits

- **16 sessions, one window, one direction.** Gaps were positive on all 13
  transitions because the market was recovering through August 2026. This
  measures that the gap *was* the vehicle of the advance, not that gaps are
  reliably positive.
- **No long-history check is possible** until the `open` field problem in §1 is
  resolved or a second source is used.
- **Settlement is unverified.** Holding overnight is only trivially possible if
  the position can be sold when intended. EGX same-session selling requires the
  T+0 list, and `data/universe/egx_intraday_eligibility.csv` is still empty. For
  multi-night holds this is less binding than for scalping, but it is not
  established.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/probe_overnight_gap.py --source minute
venv/Scripts/python.exe scripts/research/probe_overnight_gap.py --source daily
```
