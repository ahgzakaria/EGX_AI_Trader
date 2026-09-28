# Classic Chart Patterns on EGX

**Measured:** 2026-09-28 · `scripts/research/chart_patterns.py` ·
tests: `tests/test_chart_patterns.py`

**Question:** the project had measured candlestick confirmation, Fibonacci and
measured-move targets, support/resistance scalping and the twenty-day volume
breakout, but never a chart pattern. Do flags, triangles, double bottoms and
head-and-shoulders carry information here?

**Short answer: no.** None of the six survives the rule registered before the
run, and none beats a plain close above the twenty-session high — the breakout
CONFIRMED_VOLUME_BREAKOUT is already built on. The two bearish patterns do not
work as warnings either; the double top flips sign between the eras.

## How it was measured

On the frozen Mubasher record the backtests read (220 symbols, 2016-09 to
2026-09), with `signal_scan`'s yardstick: the forward return over the hold, net
of each symbol's measured round trip, as lift over owning the liquid half of the
universe on the same days, split at 2023-01-01. Pattern definitions are the
textbook ones with parameters fixed in the script's docstring and not tuned; a
swing point is used only from the third session after it, and a test proves no
detector's trigger changes when the series is cut at that trigger.

A bullish pattern survives only with lift > 0, t ≥ 2 and at least 40 events in
both eras **and** lift above the plain breakout's in both; a bearish one only
with lift < 0 and t ≤ −2 in both.

## Result — twenty-session hold (primary)

| pattern | n <2023 | lift | t | n ≥2023 | lift | t | verdict |
|---|--:|--:|--:|--:|--:|--:|---|
| plain 20-day breakout (reference) | 3,990 | **+2.67** | 8.69 | 3,687 | **+1.27** | 3.98 | — |
| bull flag | 31 | +7.56 | 1.00 | 25 | −4.10 | −1.46 | too few events |
| ascending triangle | 121 | −2.26 | −1.89 | 141 | +0.32 | 0.20 | fails |
| double bottom | 132 | −1.76 | −1.40 | 83 | +0.38 | 0.25 | fails |
| inverse head and shoulders | 97 | −0.16 | −0.12 | 95 | −0.07 | −0.06 | fails |
| double top (warning) | 104 | −4.88 | −2.97 | 87 | **+5.27** | 2.80 | flips sign |
| head and shoulders top (warning) | 85 | +0.05 | 0.02 | 80 | +2.61 | 1.31 | fails |

The ten-session hold tells the same story. Every pattern is also rare — 80 to
140 events per era across 220 symbols, against about 4,000 plain breakouts — so
even a pattern that worked would add little to how often the program can act.

**Not done, on purpose:** trying other flag lengths or tolerances until one
passes. With this many free parameters, the search would find a winner by chance;
the definitions were fixed before the result for that reason.

## Two data findings from the run

* **The frozen record has 13 bars with a close of zero** and real volume — five
  on EGREF in 2019–20, and 2025-12-29 and 2026-02-10 on several symbols at once.
  They made the first run's lifts undefined and are treated as missing here. No
  backtest trade was open across one, so no backtest result changes.
* **4.06% of the live record's bars since 2024-09 close outside that day's
  range** — 4,415 bars, concentrated in thinly traded names (EOSB 282, EGREF 249,
  MATD 220, UTOP 208, GTEX 187). EOSB closed at 1.57 on each of its last twelve
  sessions while its high and low were 1.64. This is most likely the exchange's
  own rule — the official close comes from the closing auction, and a name with
  no match there keeps a close nobody traded at that day — rather than a
  corruption. It is not resolved here: it matters because the strategy reads that
  close, and EOSB was a Daily Dashboard BUY on 2026-09-22 to 24.
