# Research scripts

The measurements behind the strategy decisions taken on 2026-08-18. They are
here so the numbers in the engine's comments can be re-derived rather than
trusted, and so the next change starts from evidence instead of from the last
person's intuition.

All read `data/frozen_eodhd_seed/` (241 symbols, 559,483 daily bars) and write
nothing. Run from the project root:

```
venv/Scripts/python.exe scripts/research/<name>.py
```

| script | question it answers |
| --- | --- |
| `egx_daily_range.py` | How far does an EGX stock move in a day, and how often is that enough to pay for a round trip? |
| `holding_period.py` | How long must a position be held before the 0.80% cost stops dominating the move? |
| `exit_rules.py` | Given the same entry, which exit rule survives costs? |
| `breakout_features.py` | Which breakout features predict anything out of sample? |
| `breakout_volume_bands.py` | Is the signal the breakout, or the volume behind it? |

## What they found

**Intraday round trips start 0.72% behind.** Liquid EGX names drift +0.078%
between open and close. The round trip costs 0.80%. The cost is more than ten
times the entire average move, and no exit rule tested closes that gap — six
were tried, under both the pessimistic and optimistic resolution of daily
bars, and all six lose.

**Cost stops dominating at ten days.** It is 1030% of the average move
intraday, 178% at five days, 91% at ten, and 46% at twenty.

**The signal is volume, not the breakout.** A breakout on 1.0–1.5x average
volume returns −0.11% over twenty days in validation, worse than not trading
at all (+2.91%). Above 2.5x it returns +5.73% with a 59% win rate, consistent
across 5, 10 and 20-day holds and across both eras.

**Half the hand-assigned weights failed out of sample.**
`consolidation_breakout` carried 15 points and reversed sign;
`higher_high_breakout` carried 10 and collapsed; EMA alignment carried 10 and
went to zero.

## Reading them honestly

Two things bound every number above, and both are stated in each script:

* **Daily bars cannot say whether the high came before the low.** Anywhere
  that matters, results are reported under both resolutions rather than one
  being presented as conservative.
* **Entry at the open stands in for "a trigger fired".** The exit-rule study
  compares exits holding entry constant. It is not a backtest of any engine.

One bad row — `ZMID 2022-10-04`, printed with a high and close of 1,000,000
against a 6.81 open — was enough to make "hold to close" average +129% per
trade across 112,000 trades before it was caught. Every script now rejects
rows whose range exceeds what the exchange's own ±20% limit permits.
