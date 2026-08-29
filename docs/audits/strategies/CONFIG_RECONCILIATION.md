# The Two Strategies, Reconciled

> **Drawdown note (2026-08-29).** Every `MaxDrawdown` in this document was
> measured before `backtesting/equity.py` marked open positions to market,
> so each is a *closed-trade* drawdown and understates the real figure by
> 0.3-2.6 percentage points. The numbers are left as measured; the
> conversion table for every archived run is in
> [DRAWDOWN_WAS_UNDERSTATED.md](DRAWDOWN_WAS_UNDERSTATED.md).


**Question:** `DEFAULT_SETTINGS` and the running `config/settings.json` disagreed
on eleven settings — not a drifted parameter but two different strategies. Which
one is the strategy?

**Status:** measured, and the defaults changed to match the running file.
Step 4 of [WORK_PLAN.md](WORK_PLAN.md), and the last open item in it.

**Short answer:** the running configuration wins by a wide margin, so the code
defaults were wrong, not the user's file.

---

## 1. What disagreed

| Setting | Running file | Code default |
|---|--:|--:|
| `min_score` | 50 | 65 |
| `min_confidence` | 65 | 80 |
| `min_trend` | 12 | 25 |
| `min_momentum` | 3 | 5 |
| `min_volume` | 0 | 5 |
| `require_market_analyzer` | True | False |
| `require_quality_filter` | True | False |
| `quality_min_adx` | 21 | 20 |
| `exit_mode` | TARGET2 | TARGET1 |
| `partial_exit` | True | False |
| `allow_overlapping_trades` | True | False |

Lower thresholds with both gates **on**, against higher thresholds with both
gates **off**. Opposite designs, not a tuning gap.

## 2. Measured against each other

Same corrected cost model, same data, configuration pinned, everything else
identical.

| | Running file | Code defaults |
|---|--:|--:|
| Trades | 484 | 584 |
| Win rate | 40.0% | 34.76% |
| **Profit factor** | **1.29** | **0.88** |
| **Total return** | **+60.08%** | **-36.23%** |
| **Max drawdown** | **16.17%** | 46.30% |
| Avg per trade | +0.38% | -0.09% |
| Sharpe | 0.55 | -0.16 |
| **Years positive** | **7/10** | **3/10** |

Better on every column. The gap is not marginal: **+60% against -36%.**

## 3. Why the higher thresholds lose

They filter on a score that carries no signal — `r = -0.032, t = -0.81` against
outcome ([SCORE_DIAGNOSIS.md](SCORE_DIAGNOSIS.md)). Raising a threshold on a
field with no information removes trades without improving the ones that remain.

The trade count moves the *other* way — 584 against 484 — because opening both
gates admits more than the higher thresholds exclude. So the losing
configuration trades **more often** and **worse**: exactly the pattern every
throttle in this investigation has shown, where trade count and quality move
independently.

`exit_mode: TARGET1` compounds it. Paired with `min_rr 3.0`, which selects
distant targets, exiting at the first one captures a small part of the move the
ratio was computed on. Win rate is *higher* at 34.76% — earlier exits win more
often — while profit factor falls to 0.88, because the wins are smaller than the
losses.

## 4. What changed

`DEFAULT_SETTINGS` now carries the running configuration's values for all
eleven, with the measurement in a comment beside them. `config/settings.json` is
unchanged: it was already right.

Pinned by `tests/test_default_settings_are_measured.py`, which asserts the
**values** rather than agreement with `config/settings.json`. That file is meant
to be edited, and a test asserting the two match would fail the moment anyone
tuned anything — which teaches people to ignore the test.

## 5. What this does not settle

- **Two configurations were compared, not seventeen.** A third could beat both.
  Nothing here searched for one, and searching would be fitting.
- **In-sample.** Unlike `min_rr`, this comparison has had no walk-forward.
- **`min_rr 3.0` was calibrated in the running context**, so this result is
  partly self-confirming: the winning configuration is the one the shipped risk
  control was tuned inside.

What it does settle is the question it was asked: there is no reason to prefer
the code defaults, and they are no longer a silent alternative waiting for a
clean checkout to pick them up.
