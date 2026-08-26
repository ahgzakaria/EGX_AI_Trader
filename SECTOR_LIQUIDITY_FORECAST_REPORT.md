# Sector Liquidity Forecast — Phase 3

Builds on Phases 1 & 2 (see `SECTOR_LIQUIDITY_FLOW_REPORT.md`).

**Headline: the learned model does not reliably beat a 5-session moving
average.** It beats persistence comfortably (+0.249 skill), but persistence is
the weaker baseline, and the margin over the stronger one is +0.013 — below the
+0.02 threshold this module treats as usable. The shipped default forecaster is
therefore the 5-session mean, and the model is returned beside it for
comparison, not in place of it.

---

## What is predicted

Each sector's **share of the next session's market turnover** — where trading
value concentrates, not which way any price moves.

Share rather than absolute turnover: shares are self-normalising, so a
market-wide quiet or busy day cannot masquerade as a rotation signal.

`t+1` means the next *retained* session. Sessions failing the Phase 2 coverage
guard are dropped, and the sessions either side of a dropped one are joined —
the target is never silently shifted onto a partial day.

## Modules

| Module | Responsibility |
| --- | --- |
| `sector_flow/forecast.py` | Feature engineering, model, walk-forward, baselines, verdict |
| `scripts/validate_sector_forecast.py` | CLI: validates every target mode, writes reports, prints the forecast |
| `tests/test_sector_forecast.py` | 20 tests |

`engineer_features()` is the single feature path used by both training and
inference. Training and serving cannot drift apart, because there is only one
implementation to drift.

## Leakage controls

* Folds are split on **sessions**, never on rows — every sector of a session
  falls on the same side of the boundary (`test_folds_never_split_a_session…`).
* `TimeSeriesSplit` over ordered sessions; `train_end < test_start` asserted per
  fold.
* All features inherit Phase 2's strictly trailing baselines.
* The model is refit from scratch inside every fold.

---

## Results — walk-forward, 5 folds

Dataset: **61,533 rows, 4,808 sessions, 2001-08-14 → 2026-08-25**, built from
the 241-symbol operational universe. Scored on 60,212 out-of-sample predictions
across 4,005 sessions.

| Metric | Model | Persistence | Mean-5 |
| --- | --- | --- | --- |
| MAE | 0.021413 | 0.023428 | **0.021104** |
| Rank correlation | **0.9022** | 0.9014 | — |
| Top-3 hit rate | **0.8430** | 0.8165 | — |

| Skill score | Value |
| --- | --- |
| vs persistence | **+0.249** |
| vs mean-5 | **+0.013** |
| vs best baseline | **+0.013** |

**Mean-5 still beats the model on MAE.** The model wins narrowly on rank
correlation and clearly on top-3 hit rate (+2.7pp), but +0.013 skill against the
strongest baseline is below the +0.02 line and is reported as MARGINAL, not as
a working forecaster.

An earlier run of the same code against the **retired** 265-symbol universe
scored +0.003 against mean-5. Correcting the universe (Phase 1) roughly
quadrupled the margin without changing the verdict — worth recording, because it
shows the ceiling here is a data problem before it is a model problem.

### Per fold

| Fold | Test window | Rows | MAE | MAE persistence | Skill vs persistence |
| --- | --- | --- | --- | --- | --- |
Skill against persistence is stable at roughly +0.30 across folds (per-fold rows
are written to `reports/sector_forecast_folds_level.csv`). That
stability is real but not useful: it measures how much better than "tomorrow =
today" a smoother is, and a 5-session mean captures nearly all of it for free.

### Variants tested

Measured on the retired universe unless noted; the level/residual pair was
re-measured on the corrected universe and moved together.

| Variant | Skill vs mean-5 |
| --- | --- |
| All history, level target (corrected universe) | **+0.013** |
| All history, residual target (corrected universe) | −0.012 |
| All history, residual target (learn mean-5's error) | −0.011 |
| All history, + sector identity as a categorical feature | −0.004 |
| 2026 only (149 sessions, post Sunday-fix) | −0.117 |
| 2022+, + sector identity | +0.027 |

Only the 2022+ window clears the +0.02 threshold, and that window was chosen
**after** seeing the results, so it is selection on the test set and not
evidence. It is recorded here as a lead to confirm out-of-sample, not as a
result. `residual` mode is retained in the code but is not the default, since it
scored worse.

---

## Why the honest verdict is built into the code

An early version of `_verdict()` scored only against persistence and returned
`SKILL: +0.240` — a genuinely misleading headline for a model that a moving
average outperforms on MAE. `_verdict()` now scores against **whichever naive
baseline is hardest to beat**, and explicitly calls out the case where the model
beats the weak baseline but not the strong one:

> MARGINAL: skill +0.013 over the best naive baseline is too small to rely on.
> Treat as unproven. It does beat persistence (+0.249), but persistence is the
> weaker baseline and that margin is mostly smoothing.

Four tests pin this behaviour, including
`test_skill_against_best_baseline_never_exceeds_the_weaker_one`.

## Verification

* `tests/test_sector_forecast.py` — 20 tests: target alignment against the next
  retained session, session-blocked folds, share normalisation, skill-score
  correctness at the 0 and 1 endpoints, and verdict wording at each threshold.
* `tests/test_sector_flow.py` — 23 tests (Phases 1 & 2) still pass.

## Running it

```bash
venv/Scripts/python.exe scripts/validate_sector_forecast.py
```

## What this is not

Not investment advice, and not a price forecast. `TurnoverShare` describes where
trading activity is expected to concentrate. A sector can lead turnover while
falling.

## Where to go next

The result argues against tuning this model further — the ceiling on daily
sector-share prediction from daily bars alone looks close to the mean-5
baseline. The more promising direction is Phase 4: **intraday**. The first 30
minutes of `candles_1m` in `rubix_live_market.db` carry information the daily
panel cannot, and "where is liquidity going *today*" is answerable in a way
"where will it go tomorrow" appears not to be.
