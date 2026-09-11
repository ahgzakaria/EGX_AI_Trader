# Structural targets on CONFIRMED_VOLUME_BREAKOUT

**Date:** 2026-09-11
**Status:** both targets FAIL the pre-registered bar in both eras. The strategy
keeps no target. The program, reopened for this test only, is closed again.

## Why this was run

The strategy program was terminated in
[INVESTIGATION_SUMMARY.md](INVESTIGATION_SUMMARY.md). The owner asked for one
more look at profit targets as technical analysis sets them — Fibonacci and the
measured move — and the program was reopened for exactly that.

There was also a gap in the record. [CONFIRMED_VOLUME_BREAKOUT.md](CONFIRMED_VOLUME_BREAKOUT.md)
§4 says "targets were tested and are worse than useless" and quotes 2R and 3R.
Those rows come from `scripts/research/breakout_exits.py`, which ran the
*candidate* of that time — cross-sectional ranks, a 2 ATR stop, a forty-bar
cap — not the rule as shipped. **No target of any kind had been measured on the
shipped rule, and no chart-placed target had been measured anywhere.** The
measured move and weekly-resistance models in `strategy_breakout/breakout_targets.py`
were implemented in Phase 10 and never audited.

## Pre-registration

Fixed in the script's docstring before the first run. Nothing was tuned after
it and no third target was tried.

The base is the range the breakout cleared: the `breakout_window` (20) bars
before the signal bar — the same bars `signal.measure` reads `PriorHigh` from.

| | level |
|---|---|
| height | `PriorHigh` − lowest Low of those bars |
| **MEASURED_MOVE** | `PriorHigh` + 1.000 × height |
| **FIB_161.8** | base low + 1.618 × height, i.e. `PriorHigh` + 0.618 × height |

The Fibonacci level is the 161.8% extension drawn from the base low to the base
high, as charting packages draw it. It sits **below** the measured move.

Everything else is the shipped rule, walked exactly as
`shipped_rule_evidence.py` walks it: entry at the close after the signal, stop
under the base with a 0.3 ATR buffer, no trail, twenty sessions, each symbol's
own round trip.

- **Stop before target within a bar** — when a bar touches both, the loss is taken.
- **The target fills at the level**, never at a better gap price (the open in
  this data is carried forward from the previous close on 96–98% of bars).
- **A target already at or below the entry close** is taken at the close of the
  first bar after entry, unless the stop is hit on that bar first. Counted
  separately as `reached_at_entry`.
- The walker was checked against `breakout_exits.simulate` on all 167 symbols
  with signals: with no target it reproduces the shipped harness trade for trade.

**The bar:** mean lift per trade must beat the shipped no-target rule in **both**
eras, split at 2023-01-01. Lift is net return minus the average eligible name's
return over the same twenty sessions, net of the average round trip.

## Result

190 symbols, mean round trip 1.39%.

| variant | trades | lift 2016–22 | lift 2023–26 | net 2016–22 | net 2023–26 | median bars | net per 20 bars held | verdict |
|---|--:|--:|--:|--:|--:|--:|--:|---|
| **no target (shipped)** | 953 | **+3.85%** | **+1.92%** | +4.77% | +4.78% | 20 | **+5.09%** | — |
| measured move | 1,000 | −0.11% | −2.70% | +0.78% | +0.07% | 9 | +0.47% | **FAIL** |
| Fibonacci 161.8% | 1,033 | −1.18% | −3.26% | −0.21% | −0.54% | 3 | −1.24% | **FAIL** |

Both targets turn a positive lift negative in both eras. Freeing capital sooner
does not rescue them: measured per twenty bars actually held, the shipped rule
earns +5.09% and the targets +0.47% and −1.24%.

### Exit reasons

| variant | target | reached at entry | timeout | stop |
|---|--:|--:|--:|--:|
| no target | — | — | 865 | 88 |
| measured move | 474 | 111 | 342 | 73 |
| Fibonacci 161.8% | 539 | 235 | 202 | 57 |

The median target sat 9.01% above entry for the measured move (IQR 4.48–13.98)
and 4.19% for Fibonacci (IQR 0.34–7.90). On 235 Fibonacci trades the breakout
close had already passed the level before the entry could be taken.

## Why they fail — the same trades, both ways

Every trade a target closed was matched to the same signal under the shipped
rule:

| | trades | target's net | shipped rule's net on the same trades |
|---|--:|--:|--:|
| measured move, hit the target | 451 | +6.97% | **+13.35%** |
| measured move, passed at entry | 108 | −1.07% | **+12.99%** |
| Fibonacci, hit the target | 495 | +3.81% | **+9.58%** |
| Fibonacci, passed at entry | 224 | −0.93% | **+9.00%** |

The trades that reach a target are the strongest trades the rule takes, and the
target sells them early. Holding them for the full twenty sessions earned about
twice as much. That holds for the trades that genuinely hit the level, not just
for the ones the entry-fill rule closed, so **the fill rule does not decide the
result.**

The target does help on some trades — the shipped rule did worse on 236 of the
559 measured-move matches and 314 of the 719 Fibonacci matches, the ones that gave
the move back. It loses more on the rest than it saves on those.

### Why a target feels better while earning less

| | win share | median trade | mean net 2016–22 / 2023–26 |
|---|--:|--:|--:|
| no target | 49% | −0.20% | **+4.77% / +4.78%** |
| measured move | 55% | +1.14% | +0.78% / +0.07% |
| Fibonacci 161.8% | 53% | +0.62% | −0.21% / −0.54% |

A target makes more trades close green and lifts the median. It does that by
cutting the right tail, and this rule's mean lives in that tail: the ten largest
shipped trades are 39.5% of all its net return. That is the same finding §4 of
the strategy audit stated for stop multiples, now shown for chart-placed levels
on the rule as shipped.

## What this does not say

- It does not test a **partial** exit (sell part at the level, hold the rest).
  That was not pre-registered and was not run.
- It does not test Fibonacci levels drawn on any swing other than the twenty-bar
  base, or any ratio other than 161.8%. Trying others after this result would be
  a search, not a test.
- It says nothing about targets on intraday tracks, which were outside the
  terminated family.

## Reproduce

```
venv/Scripts/python.exe scripts/research/structural_targets.py
```
