# T+0 Radar: How Accurate Is the Forecast?

**Question:** the T+0 Radar (`t0_radar/`) lists liquid names after each close for
a trader who places every order himself. How well does it say which names will
move the most in the next session, and does anything on it say which way they will
move?

**Status:** measured on a replay of the measured record. The radar was changed
on the strength of it: v1 → v2.

**Short answer:** the size forecast is strong and stable. Ranked by the mean range
of the last five sessions, the first ten moved **5.1%** high-to-low in the next
session, against **2.8%** for the rest of the list. **75%** of them moved more
than five times their own round-trip cost, and **96%** more than three times.

On direction, nothing on the radar says anything. The first ten closed up **42%**
of the time, against **45%** for every candidate, and the mean change was the same
(+0.14%).

---

## Method

`scripts/research/t0_radar_accuracy.py` replays the radar after the close of
every session from 2024-09-08 to 2026-09-29, which is 500 sessions. It applies the
same gates to the same record and ranks each candidate list. It then scores each
ranking against the next session, keeping each half of the window apart.

Before reading anything, check that the replay is the radar. On 2026-09-30 its
ranking matches `t0_radar.radar.build()` name for name: rank correlation 1.000,
largest gap 5e-15.

**The outcome** is the next session's high-to-low range, as a percent of the
radar session's close. It counts only if the name traded on that very next session.

**Accuracy** has three measures:

- the rank correlation between the ranking and that outcome, per session, averaged;
- the median next range of the first ten against the rest;
- how often the first ten moved a multiple of their own cost.

## Which ranking

| Ranking | Rank corr, older half | Rank corr, recent half | Top 10 next range | Rest |
|---|--:|--:|--:|--:|
| v1: 0–100 score (room, turnover, expansion, consistency) | 0.455 | 0.430 | 4.57% | 2.89% |
| room only (typical range / cost) | 0.405 | 0.323 | 4.42% | 2.90% |
| typical range, 20 sessions | 0.511 | 0.418 | 4.60% | 2.89% |
| last session's range | 0.511 | 0.484 | 4.86% | 2.84% |
| **v2: mean range, last 5 sessions** | **0.586** | **0.531** | **5.13%** | **2.82%** |
| relative turnover | 0.171 | 0.175 | 3.92% | 2.97% |
| mean range last 5 / cost | 0.491 | 0.460 | 4.89% | 2.84% |
| v1 with room read from the last 5 | 0.444 | 0.433 | 4.60% | 2.88% |

The older half runs 2024-09-08 to 2025-09-18 and the recent half 2025-09-21 to
2026-09-29, 250 sessions each. The last two columns cover the whole window.

The five-session mean is first in **both** halves separately. Every t-statistic
in the table exceeds 14, so the order is not noise. Two adjustments lowered it:
dividing by the cost, and blending in turnover or range expansion.

The cost still matters, but as a gate rather than a weight. A name is listed only
when its measured round trip is known and its typical range covers that round
trip three times.

## The forecast

A name that ranks first has had an unusually wide week, and it narrows the next
session. Next range divided by the five-session mean:

| | q10 | q25 | median | q75 | q90 |
|---|--:|--:|--:|--:|--:|
| first ten | 0.424 | 0.545 | 0.735 | 1.015 | 1.385 |
| every candidate | 0.500 | 0.650 | 0.880 | 1.215 | 1.676 |

The first ten's median ratio was 0.766 in the older half and 0.697 in the recent
one. The radar's forecast is the five-session mean times the median ratio. Its
band is the q25 to q75 ratios, so the next session lands inside it about half the
time.

## Direction

The same rank correlation, taken against next close over this close:

| Reading | Rank corr | Top 10 closed up | Top 10 mean change |
|---|--:|--:|--:|
| v2 ranking | −0.071 | 42.1% | +0.143% |
| v1 score | −0.056 | 43.3% | +0.153% |
| last session's return | −0.022 | 43.6% | +0.241% |
| close position in its range | −0.022 | 44.0% | +0.193% |
| distance from SMA20 | −0.020 | 45.0% | +0.306% |
| relative turnover | −0.035 | 43.2% | +0.101% |
| every candidate | — | 44.9% | +0.143% |

None of these is a forecast of direction. The wide names close down slightly more
often, with the same mean change: their days are larger in both directions.

## What would have to be true for this to be wrong

- **Cost is applied backwards.** Each name's round trip is the one banked through
  2026-09-30, applied to every past session. Cost is a property of the name
  (r = 0.836 between halves of its sessions), but a 2024 cost was not this one.
  This affects who passes the gates, not the ranking among them.
- **No traded open before the minute store's window.** The daily record's open is
  the previous close, so the replay measures high to low and close to close. What
  a same-session trader can capture from the open is graded only going forward, in
  `data/t0_radar_forward.db`.
- **Range is not profit.** A 5% range is room, not a 5% gain. Nothing here says
  where in that range an order would fill.

## Reproduce

```
venv/Scripts/python.exe scripts/research/t0_radar_accuracy.py
```
