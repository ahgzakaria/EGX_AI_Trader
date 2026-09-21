# What the portfolio's exit rules were worth, first reading

**Measured:** 2026-09-21 · `scripts/research/score_portfolio_recommendations.py`
**Source:** `data/portfolio.db` — 118 logged recommendations, sessions
2026-08-31 to 2026-09-10 · **65 scored, 53 still inside their window**

The page has labelled its liquidity rules "قاعدة غير مُقاسة بعد" since they
shipped on 2026-08-31, and `holdings/store.py` has been logging every
actionable recommendation with the evidence that produced it. This is the first
time that log was read back.

## The measurement, fixed before running

An exit rule earns its place if what it flagged **fell after it spoke**, by
more than the market and by more than the cost of acting:

* **Benchmark:** the median symbol in the measured Mubasher record over the
  same sessions. Scoring against zero credits a rule for a fall everybody had.
  EGX30 was the first choice and could not be used: the terminal's own index
  history ends 2026-09-07 while its stock record reaches 2026-09-20, so no
  recommendation has a full window against it. The index lift is still written
  to the CSV for the rows it covers.
* **Cost:** the measured round trip for that symbol (`core.effective_cost`),
  about 0.6% here.
* **Primary horizon:** 10 sessions, chosen before running — the exit plan's own
  time stop is 20, and half of it is the shortest window over which "the trend
  broke" can be said to have happened rather than to be one day's noise. 1, 5
  and 20 are in the CSV as shape.

## Result

| action | rule | scored | median lift | fell after | median cost | beats its cost |
|---|---|--:|--:|--:|--:|:--:|
| EXIT | LIQUIDITY_LEAVING | 5 | **−5.49%** | 4/5 | 0.64% | yes |
| RAISE_STOP | LIQUIDITY_LEAVING | 6 | **−5.08%** | 4/6 | 0.60% | yes |
| EXIT | TARGET_FINAL_REACHED | 1 | −0.14% | 1/1 | 0.61% | no |
| EXIT | TREND_BREAK | 12 | −0.30% | 6/12 | 0.62% | no |
| EXIT | STOP_BREACHED | 17 | +0.81% | 6/17 | 0.64% | no |
| TRIM | TARGET_PARTIAL_REACHED | 6 | +1.66% | 2/6 | 0.61% | no |
| RAISE_STOP | EXPANSION | 7 | +6.75% | 2/7 | 0.60% | no |
| EXIT | TIME_STOP | 11 | **+6.11%** | 2/11 | 0.63% | no |

A negative lift is the rule being right for an EXIT: the name underperformed
the typical stock over the next ten sessions, so leaving avoided something.

### What it says

* **The two unmeasured liquidity rules are the only ones that paid.** Both
  read −5% against the median symbol, both on 4 of 5 and 4 of 6. That is the
  direction they were built for, on a sample too small to establish anything.
* **The time stop is the expensive one.** Eleven exits, and the names went on
  to beat the median symbol by 6.11%; only 2 of 11 fell. "Twenty sessions
  without reaching the first target" is currently closing positions that then
  work.
* **STOP_BREACHED and TREND_BREAK are close to a coin flip** at this horizon —
  +0.81% and −0.30%, 6 of 17 and 6 of 12. Neither clears its own cost.
* **EXPANSION raising a stop before a further +6.75% is the rule working**, not
  failing: it tightens a stop on a new high and never sells.

### What it cannot say

The log only holds sessions the user opened the page on, so this is 9 trading
sessions, ending 2026-09-10 — the day the live feed was retired and the page
stopped being visited. Every rule here has single-digit or low-teens counts;
nothing is established, and a rule is never pooled with another to raise its n.
53 of the 118 have not completed a 10-session window yet and are marked
PENDING rather than scored early.

This scores the *reading*, not the account: the log records advice, and what
was actually traded is in the same database's `trades` table.

## What to do with it

Re-run it after the log grows — the same command, no arguments. The rule to
revisit first on this evidence is the **time stop**, not the liquidity rules
the page warns about: it is the one the record currently argues against, and
the constraint that the liquidity rules may never realize a loss stays as it is
until their own sample is bigger than five.
