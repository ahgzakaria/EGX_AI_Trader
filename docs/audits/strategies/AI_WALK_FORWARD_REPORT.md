# AI Walk-Forward Validation Report

## Status

The historical AI pipeline now uses chronological Walk-Forward validation.
For every test fold, training rows are limited to trades whose **exit date is
strictly earlier** than the first prediction date in that fold. A global model
is never loaded by the standard historical backtest.

## Small validation run

Source: current `reports/backtest_results.csv`  
Method: 2 chronological folds, median imputer + Random Forest fitted anew per
training fold.

| Metric | Result |
| --- | ---: |
| Out-of-sample predictions | 1,242 |
| Accuracy | 81.40% |
| Precision | 77.83% |
| Recall | 47.28% |
| F1 | 58.82% |
| ROC-AUC | 75.55% |
| Confusion matrix | `[[846, 47], [184, 165]]` |
| Test class distribution (0 / 1) | 893 / 349 |

## Fold chronology

| Fold | Train rows | Test rows | Latest train exit | First test prediction | Accuracy | F1 | ROC-AUC |
| ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |
| 1 | 615 | 621 | 2020-10-20 | 2020-10-21 | 80.84% | 63.83% | 78.05% |
| 2 | 1,239 | 621 | 2023-08-03 | 2023-08-06 | 81.96% | 51.72% | 72.30% |

The date ordering in both folds proves the key anti-leakage invariant:
`latest train exit < first test prediction`.

Detailed generated data is available in:

- `reports/ai_walk_forward_folds.csv`
- `reports/ai_walk_forward_predictions.csv`
- `reports/ai_walk_forward_summary.csv`

## Backtest modes

### Strategy Only

This is the default and the standard performance baseline. It always disables
AI inference, even when a live model exists on disk. It is the only valid
historical backtest mode without an explicit Walk-Forward process.

### Strategy + Walk-Forward AI Filter

This mode first produces strategy-only labels, builds chronological models,
then runs a second historical pass. A model is applied only during its assigned
out-of-sample fold. Dates before the first eligible fold are labeled `WARMUP`
and retain their technical strategy decision. The final run reports strategy
and AI-filtered Profit Factor, Drawdown and rejected-trade count side by side.

## Validity of previous results

The previously displayed **71.67% AI accuracy must be discarded** as a model
validation result. Its earlier train/test procedure sorted by entry date but
did not prove that a training label had exited before the prediction period.

Any previous **AI-filtered backtest result must also be discarded** because a
single globally trained model could have been applied to dates before that
model could have existed. Strategy-only results remain useful only when they
were produced without the global AI filter.

The figures in this report are leakage-safe.  Phase 3 subsequently regenerated+the Strategy Only and Walk-Forward AI passes end-to-end from the unified+pipeline; its comparative results are recorded below.

## Phase 3 — Out-of-sample strategy comparison

The comparison was re-run end-to-end after fixing a feature-name boundary
between live indicator rows and the Walk-Forward feature set.  The historical
filter now normalises feature names before inference, and tests cover the
mapping plus prediction coverage between chronological folds.

For the same settings and the out-of-sample period `2020-08-06` to
`2026-06-08`, Strategy Only returned **54.04%** with **18.59%** maximum
drawdown.  Strategy + Walk-Forward AI returned **16.41%** with **5.81%**
maximum drawdown.  The AI pass improved Profit Factor (1.72 vs 1.23), Sharpe
(0.95 vs 0.72), and precision among accepted candidates (81.19%), but its net
economic value relative to Strategy Only was **-37,625.86**.

The phase verdict is **B — AI reduces risk but sacrifices too much return**.
See `STRATEGY_VS_AI_COMPARISON.md` and the `reports/strategy_vs_ai_*.csv`
artifacts for the trade, year, regime, symbol and probability-bucket details.
