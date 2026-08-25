# The AI Layer Against the Fabricated `open`

**Question:** the swing probe found that the daily `open` field in
`data/market_data_cache.sqlite` is carried forward from the previous close in
97.4% of bars, and lies outside its own `[low, high]` in 26.2% of them. How far
does that reach into the AI layer?

**Status:** read-only audit. No model was retrained, no report regenerated, no
config changed. Measurements reproduce via
`scripts/research/probe_swing_strategy.py`.

**Short answer:** the model's inputs are clean, its training population is not,
and the pullback research built around it is priced entirely on the fabricated
field.

---

## 1. Clean — the model's features never touch `open`

The 22 features in [`ai/dataset.py`](../../../ai/dataset.py) are computed by
[`indicators/technical.py`](../../../indicators/technical.py), which reads only
`Close`, `High` and `Low`. ATR and ADX included — both use the
high/low/previous-close form, not the open. The `ai/` package itself never reads
the column at all; its only `open` is the builtin, for files.

`candle_score` — the field the fabrication actually corrupts — is **not** among
the features. Confirmed against both the `FEATURES` list and
`reports/feature_importance.csv`, whose 22 rows carry no candle term.

So the fabrication does not enter the model's inputs. That matters: the model's
architecture and feature engineering are sound, and the problem is upstream of
them.

## 2. Compromised — the training population

`ai/models/trading_model.metadata.json` records `samples: 684` with a class split
of 504 / 180. That is exactly the backtest trade record: the same 684 rows, the
same 180 wins.

Those 684 trades are the ones that passed `strategy.require_candle_confirmation`,
a gate computed from the fabricated open (see
[SWING_STRATEGY_PROBE.md §4](SWING_STRATEGY_PROBE.md)). So the model has learned
what predicts success *among the trades a mislabelled filter admitted*, not among
the trades the strategy would take if the filter measured what it is named for.

Fixing the open changes which signals pass the gate, which changes the training
population. **The model needs retraining, not merely a backtest re-run.**

And that retraining is currently **blocked, not merely pending**. Every
historical open in the cache is fabricated, so there is nothing to correct
toward; `data/rubix_live_market.db` only reaches back to 2026-07-01; and the
EODHD seed is no shortcut, since its opens are carried forward too and merely
clipped into range. Until a source of real historical opens exists, the correct
status is *cannot currently be validated*, not *provisional*.

For context on what is at stake, its walk-forward record:

| Metric | Value |
|---|--:|
| ROC AUC | 0.630 |
| Precision | 0.661 |
| Base rate (positives / predictions) | 24.7% |
| Recall | 0.277 |
| F1 | 0.390 |

Precision well above the base rate means it does carry real signal; recall of
0.277 means it catches about a quarter of the winners. Neither number is
trustworthy as a forward estimate until the population question is settled.

## 3. Broken — `core/ai_pullback_research.py`

This module prices every trade on the fabricated field, at four separate points.

| Location | Problem |
|---|---|
| [`:180`](../../../core/ai_pullback_research.py) | `entry = float(frame["Open"].iloc[entry_position])`. The docstring says "Entry is the next session open." In this cache that value **is the previous close 97.3% of the time**. It is not entering at the open; it is entering at yesterday's close. |
| `:180` | **26.2% of those entry prices lie outside the entry bar's own `[low, high]`** — 12.7% above the high, 13.5% below the low. And that headline understates the damage here: the corruption falls entirely on bars that traded. Split by volume, zero-volume bars are **0.00%** impossible (they are trivially `O=H=L=C`) while **traded bars are 31.87%**. A trade only ever fills on a traded bar, so nearly a third of this module's entries are at a price that never existed. |
| `:185` | The admission gate `stop < entry < target_1` is evaluated on that same fabricated entry, so **sample selection is corrupted too**, not only the pricing. |
| `:208`, `:214` | `exit_price = min(opened, stop)`. With `opened` fabricated, a stop can be filled below the bar's own low. |
| `:486` | Runs with `execution_delay_bars=1`, the `entry_manager` branch that fills at `data.open` rather than at the `buy_high` limit. |

[`core/ai_pullback_calibration.py:364`](../../../core/ai_pullback_calibration.py)
compounds it independently: it assumes 30 bps of total transaction cost, against
the 86.4 bps measured on the traded names.

This is not dormant research. It is rendered to the user by
[`dashboard/ai_stock_analysis.py:611`](../../../dashboard/ai_stock_analysis.py)
as a "research pullback zone".

**Its results are not salvageable by adjusting the cost assumption or the
holding rule. The entry price, the exit price and the sample selection all rest
on a number that is not an opening price.**

## 4. Affected — the narrative layer

[`core/ai_analysis_evidence.py:251`](../../../core/ai_analysis_evidence.py) and
`:630`, and
[`core/ai_narrative_prompt.py:92`](../../../core/ai_narrative_prompt.py), pass
`Open` into the layer that writes commentary. Any AI-written text citing an
opening price is citing a carried-forward number. Lower severity than §3 — it
misinforms rather than miscounts — but it is user-facing.

## 5. Unaffected

[`strategy/market_analyzer.py:65`](../../../strategy/market_analyzer.py) selects
`Open` in its column list but computes its EMAs on `Close` alone. The column is
selected and never used. No effect.

## 6. Order of work

1. **Rebuild the cache before anything else.** A parallel audit
   (`EOD_OPEN_FIELD_DEFECT_AUDIT.md`) traced the corruption to a legacy artifact
   rather than a live bug — `providers/rubix_daily_aggregator.py:233` builds the
   daily open correctly from the first traded minute, and the normalise/store
   path is clean, yet the stored rows are carried forward. Nothing downstream can
   be fixed while the data underneath it is fabricated.
2. **`ai_pullback_research` next.** It is the only place where the fabrication
   reaches a price a user sees, and it is corrupted in entry, exit and selection
   simultaneously.
3. **Retrain after the gate is fixed**, not before — the training population is
   downstream of the candle gate.
4. **Feature engineering needs no change.** The inputs were never affected. A
   parallel sweep confirms the same for `indicators/`, `optimization/`,
   `decision_support/`, `portfolio/` and `forward_testing/`.

## Limits

- This audit traces reachability, not harm. It establishes that the pullback
  research is priced on a fabricated field; it does not establish what its
  results would be with a real one.
- The precision and recall above are reported from the existing metadata and are
  not re-derived here.
- Nothing here is a recommendation to trade.

## Reproduce

```
venv/Scripts/python.exe scripts/research/probe_swing_strategy.py
```
