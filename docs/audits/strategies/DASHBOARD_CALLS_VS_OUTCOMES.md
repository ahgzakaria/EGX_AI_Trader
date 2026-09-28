# Do the Daily Dashboard's Calls Predict Anything? The Live Record

**Measured:** 2026-09-28 · `scripts/research/dashboard_calls_vs_outcomes.py`

**Why:** the owner's observation that stocks rise and fall with no relation to
what the dashboard predicted. The backtest research had already found its
selection carries no out-of-sample edge ([DAILY_STRATEGY_DIAGNOSIS.md](DAILY_STRATEGY_DIAGNOSIS.md)
§1). This asks the live record: every call in `data/forward_testing.db` since
2026-07-06 — 9,870 calls, 55 sessions, one per symbol per session — scored by
the close ten sessions later against the median symbol over the same sessions.

## Result — ten sessions later (five tells the same story)

| group | n | mean lift | median lift | beat the market |
|---|--:|--:|--:|--:|
| **BUY** | 54 | +1.66% | **−2.06%** | **44%** |
| WATCH | 5,138 | +3.17% | +0.31% | 52% |
| AVOID | 2,772 | +1.42% | −0.46% | 46% |
| WATCH, lowest score fifth | 1,028 | +2.18% | +0.64% | 54% |
| WATCH, highest score fifth | 1,028 | +5.10% | +0.63% | 53% |
| **plain 20-day breakout** | 380 | +5.10% | **+0.96%** | **55%** |

Rank correlation of the score with the outcome: **+0.038**.

* **BUY did worst of the three calls**, by median and by share beating the
  market — below the stocks it said to avoid.
* **The score does not rank.** Its highest and lowest fifths have the same
  median; only the mean differs, carried by a few outliers.
* **The plain breakout was the best group in the live period**, as it was over
  ten years of backtest ([CHART_PATTERNS.md](CHART_PATTERNS.md): +2.67% and
  +1.27% lift in the two eras, t 8.7 and 4.0). This period is data neither
  measurement was chosen on.

Medians and hit rates are read rather than means: the distribution is skewed,
and a mean lift against the median symbol is inflated by it. Fifty-five sessions
in one strong market, with overlapping windows, establish direction, not size.
