# A Test of the 200-Day Average: Is It an Opportunity?

**Question:** after a decline, is a liquid stock that falls back to its rising
200-day average a buying opportunity on EGX?

**Status:** measured on the measured record from 2014-01-01 to 2026-09. The test
is shown in the 200-day tab of the T+0 Radar page (`t0_radar/sma200.py`).

**Short answer:** on its own, no: it is a coin flip against the typical stock.
It becomes one only when two things hold together. The market is in a broad
sell-off, and the name closes **on or above** its average.

In that case the name beat the median liquid stock over the next 20 sessions
in **20 of 27** independent episodes, by **+1.5%** at the median, and reached
5% over its average before falling 5% under it **81%** of the time. A test
that closed **below** the average did the opposite: 5 of 13 episodes, **−1.8%**,
and it broke first 64% of the time.

---

## The test

On one session, all of these hold:

- the 200-day simple average is rising, above where it was 20 sessions earlier;
- the name came from above: within the previous 60 sessions it closed at least
  10% over the average;
- it is at the average now: the low reached within 2% above it, and the close
  is no more than 3% below it;
- it is liquid: at least 5M EGP a session over the last 20, with a session
  without a trade counted as zero;
- it made no move past EGX's ±20% limit in 200 sessions. Such a move is an
  unadjusted split or bonus issue, and it distorts the average.

Only the first session of a test counts, and a test re-arms after 20 sessions.
A **broad sell-off** is a session on which at least 75% of liquid names closed
below their own 20-day average.

## What followed

**Lift** is the stock's 20-session return minus the median liquid stock's
return over the same sessions. That is `core.measured_benchmark`'s definition
of the market a holder faces.

Tests that a single sell-off produces arrive together, so they are grouped into
episodes: runs of tests no more than 10 sessions apart. Each episode counts as
one independent observation.

| Group | Tests | Episodes | Episodes that beat the market | Lift 20d median | Bounce first | Break first |
|---|--:|--:|--:|--:|--:|--:|
| every test | 810 | 54 | 24 | +0.16% | 68% | 24% |
| closed on or above | 683 | 56 | 27 | +0.26% | 75% | 18% |
| closed below | 127 | 44 | 21 | −0.01% | 33% | 57% |
| **sell-off, closed on or above** | **210** | **27** | **20** | **+1.52%** | **81%** | **16%** |
| sell-off, closed below | 44 | 13 | 5 | −1.82% | 32% | 64% |
| … on or above, 2014–2019 | 19 | 5 | 5 | +2.93% | 79% | 16% |
| … on or above, 2020–2026 | 191 | 22 | 15 | +1.41% | 81% | 16% |
| normal market, closed on or above | 473 | 61 | 31 | 0.00% | 72% | 18% |

**Bounce first** means the close reached 5% above the average before it closed
5% below it, within 20 sessions. **Break first** is the reverse.

## What would have to be true for this to be wrong

- **The split was chosen after looking.** The first table separated sell-offs
  from normal markets, and closes above from closes below. Crossing the two came
  after seeing them. The result holds in both eras, but 27 episodes is evidence,
  not proof.
- **Lift is not return.** The median own return in the good group was +5.5%
  over 20 sessions. Much of that is the 2023–2026 rise of the whole market.
- **Cost.** A round trip costs about 0.7–0.85% (`core.effective_cost`), which
  takes the +1.5% median lift to roughly +0.7%.
- **Dividends are not adjusted** in the record, so an ex-dividend drop can look
  like a pullback.
- **Early years had few liquid names.** 86 tests from sessions with fewer than
  30 liquid names had no market to compare with, and they are left out.

## Reproduce

```
venv/Scripts/python.exe scripts/research/sma200_pullback.py
```
