# EGX AI Trader — Complete Regression Audit

## Executive conclusion

**Root cause confidence: 99%.** The validated Phase 5 result has not regressed.
It was reproduced exactly, without changing code, strategy, thresholds, model
features or runtime settings:

| Reproduction | Return | Net Profit | Profit Factor | Max Drawdown | Trades | Period |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Strategy Only | 65.81% | 65,811.22 | 1.28 | 17.91% | 730 | 2020-08-06 to 2026-06-08 |
| AI Ranking Only | 71.62% | 71,618.87 | 1.30 | 16.48% | 736 | 2020-08-06 to 2026-06-08 |

The reported “current” result is not the same experiment. The standard
`STRATEGY_ONLY` dashboard button bypasses Walk-Forward context and runs the
entire available history. The latest persisted standard report covers
2017-06-05 through 2026-07-05 (`3,317` days), whereas Phase 5 resets the
portfolio to 100,000 at the first valid OOS date and covers `2,132` days.

The latest persisted full-history report is actually worse than the figures in
the request: return `-22.47%`, Profit Factor `0.93`, drawdown `71.33%`, and 377
executed trades. No current report artifact contains exactly `47% / 1.17 /
34%`; those values were an earlier dashboard run that has since been
overwritten. The mechanism is nevertheless proven: it is a different date
window and portfolio path, not a Phase 5 strategy regression.

## 1. Settings audit

### Runtime settings versus validated Phase 5

**Changed runtime values: none.** `config/settings.json` currently matches the
validated Phase 5 configuration:

- Data: `10y`, `1d`, minimum 250 bars.
- Strategy: score 50, confidence 65, RR 1.5, trend 12, momentum 3, volume 0.
- Candle confirmation, market analyzer and quality filter: enabled.
- Quality: ADX 20, volume ratio 1.0, ATR% 1.5, resistance room 3.0.
- Backtest: TARGET2, 20 holding days, breakeven enabled, partial exit 50%,
  trailing EMA20 enabled at 2 ATR.
- Risk: 2%, overlap enabled, requested max positions 10, portfolio heat 10%.
  Heat makes effective capacity `floor(10/2) = 5`.
- Capital/costs: 100,000, commission 0.003, slippage 0.0005.
- AI: enabled, hard-filter threshold 60%, five Walk-Forward folds.
- Configured default mode: `STRATEGY_ONLY`, exactly as Phase 5 required.
- Ranking formula and all overlay bands match Phase 5.

`dashboard/settings.py` reads these values and writes the same fields. The file
timestamp changed when Save Settings was pressed, but the values did not.

### Important latent reset mismatch

`settings_manager.DEFAULT_SETTINGS` is not the validated configuration. It is
used only when the file is missing or Reset Settings is pressed, so it is not
the cause of the current reproduction. Its differences are:

| Setting | Phase 5 runtime | Code default |
| --- | ---: | ---: |
| min_score | 50 | 65 |
| min_confidence | 65 | 80 |
| min_rr | 1.5 | 2.0 |
| min_trend | 12 | 25 |
| min_momentum | 3 | 5 |
| min_volume | 0 | 5 |
| candle confirmation | true | false |
| market analyzer | true | false |
| quality filter | true | false |
| exit mode | TARGET2 | TARGET1 |
| partial exit | true | false |
| trailing stop | true | false |
| allow overlap | true | false |
| AI enabled | true | false |
| AI minimum probability | 60 | 70 |

This is technical debt and a future reproducibility risk, not an active
regression.

## 2. Strategy audit

No strategy, indicator, entry or exit implementation file was modified after
the Phase 5 validated run. The behavioral diff is also zero: the current code
reproduced both Phase 5 summaries exactly.

| Area | Current owner | Phase 5 comparison |
| --- | --- | --- |
| Entry conditions / wait | `EntryManager.find_entry` | unchanged |
| Exit / breakeven / partial / trailing | `ExitManager.manage` | unchanged |
| Stop loss / targets / RR | `strategy/decision_engine.py` | unchanged |
| EMA and technical indicators | `indicators/technical.py` | unchanged |
| Trend / momentum / volume | unified decision engine modules | unchanged |
| Candle confirmation | decision engine + settings | unchanged |
| Market regime | `strategy/market_regime.py` | unchanged |
| Market analyzer | `strategy/market_analyzer.py` | unchanged |
| Quality filter | `strategy/quality_filter.py` | unchanged |

There is therefore no strategy diff to explain the lower dashboard number.

## 3. Data audit

Current source and read-only live audit:

- `data/symbols.csv`: 265 unique symbols; no duplicate symbols.
- Successfully loaded: 206.
- Failed: 59 (51 unavailable/no data; 8 below the 250-bar minimum).
- Successful history: earliest 2016-07-10, latest 2026-07-09.
- Bars per successful symbol: minimum 263, median 2,170, maximum 2,342.
- Duplicate date rows after loader validation: 0.
- Missing OHLCV rows after loader validation: 0.
- Loader drops invalid dates, sorts dates, removes duplicate indices and
  rejects missing OHLCV before indicators are calculated.

The 59 failed symbols are:

`ACAP, ACRO, ACTF, ADRI, AIDC, AIHC, ALEX, APPC, BONY, CPME, CRST, DCRC,
DGTZ, DIFC, EGX30ETF, EITP, ESAC, FCMD, GGRN, GOCO, GOUR, GPIM, GPPL, GTEX,
GTHE, HBCO, HCFI, ICLE, IEEC, IRAX, KASABF, KORA, KRDI, MEGM, MISR, NAPR,
NARE, NBKE, NCGC, ORAS, PACH, PHGC, QNBE, RMTV, SIMO, SMPP, SNFI, SPHT,
TANM, TAQA, TORA, TWSA, TYCN, UBEE, UPMS, UTOP, VALU, VERT, VLMRA` (`.CA`).

The exact Phase 5 reproduction encountered the same 59 failures and still
reproduced the validated metrics exactly. Data failures are therefore not the
root cause. However, raw Yahoo data is not snapshot-persisted, so a future
provider revision could affect reproducibility.

`reports/failed_symbols.csv` is stale (timestamp 2026-07-10, 177 rows) and must
not be interpreted as the failure set of the latest run.

## 4. AI audit

### Why AIProbability is None

`TradingDecisionService.evaluate` deliberately initializes probability to
`None` and returns before inference when the technical signal is not BUY.
`STRATEGY_ONLY` also never asks AI to predict, including for BUY rows. Thus
`None` means **not applicable / intentionally skipped**, not model failure.

Fresh live scan classification over the 206 successfully loaded symbols:

| Category | Count |
| --- | ---: |
| Technical WATCH (prediction skipped) | 122 |
| Technical AVOID (prediction skipped) | 81 |
| Technical BUY, model prediction completed | 3 |
| Model unavailable | 0 |
| Inference failures | 0 |
| Missing Walk-Forward folds | 0 |
| Unexpected missing predictions | 0 |

The only three technical BUY candidates were fully predicted:

| Symbol | Date | Probability | Live result | Reason |
| --- | --- | ---: | --- | --- |
| CAED.CA | 2026-07-09 | 16.9% | WATCH | below live 60% hard filter |
| EALR.CA | 2026-07-09 | 24.8% | WATCH | below live 60% hard filter |
| EBSC.CA | 2026-07-09 | 27.2% | WATCH | below live 60% hard filter |

This is the exact reason the dashboard showed BUY = 0. The scanner is hardcoded
to `LIVE_MODEL`, whose `_apply_prediction` is a hard gate. It does not use the
selected historical `AI_RANKING_ONLY` mode. The Phase 5 ranking overlay never
rejects a technical BUY.

No actual feature was missing. `rr` is not a candle column, but it is injected
from the technical decision immediately before every real inference. The 203
skipped WATCH/AVOID rows never construct a feature vector, so feature-missing
analysis is not applicable to them.

Historical validation is clean:

- Reproduced raw AI Ranking trades: 894.
- AI Ranking trades with `AIProbability=None`: 0.
- Strict missing-prediction mode was enabled; any missing fold/date would have
  failed the run loudly.
- Reproduced OOS predictions: 895; accuracy 80.45%, F1 50.70%, ROC-AUC 68.42%.

The persisted global model was retrained after Phase 5 (2026-07-11 10:43), on
841 samples through 2026-06-11. This can change live scanner probabilities,
but it cannot change the historical Ranking result because historical modes
use fold-local models only.

## 5. Backtest audit

All Phase 5 mechanics remain intact:

- Strategy Only disables AI completely.
- AI Ranking uses strict Walk-Forward OOS predictions and never loads global AI.
- Position risk remains 2%; effective capacity remains 5 due to heat.
- Commission and slippage match Phase 5.
- `TradeBuilder` still records signal date, regime, probability, rank and size.
- Daily portfolio selection still collects all same-date entries before
  selection.
- Tie breakers remain: final rank, AI probability, strategy score, confidence,
  RR, alphabetical symbol.

The regression is an orchestration mismatch:

```text
Dashboard Run Backtest + STRATEGY_ONLY
  -> full 10-year BacktestEngine pass
  -> portfolio starts in 2017
  -> no OOS coverage restriction

Phase 5 comparison
  -> full pass only to create chronological labels
  -> derive first eligible OOS date (2020-08-06)
  -> rerun both modes on 2020-08-06..2026-06-08
  -> each comparison portfolio starts with 100,000 at OOS start
```

The earlier years change accumulated losses, drawdown, available cash and
which later candidates pass capital constraints. That is why drawdown and
Profit Factor cannot be compared across the two runs.

## 6. Pipeline audit — complete trade traces

### Winning trade: ETEL.CA

1. Signal generation on 2020-08-10: BUY, score 75, confidence 95, RR 1.74,
   SIDEWAYS regime; all configured technical checks passed.
2. Fold-local AI probability: 13.9%; ranking score 47.34. Ranking Only retains
   the valid technical BUY despite the low probability.
3. Daily portfolio selection: selected; final size 2,048 shares.
4. Trade creation/entry: 2020-08-11 at 13.0965; stop 12.12, targets 14.10/14.78.
5. Exit: Partial+Target2 on 2020-09-07 at effective 14.4328.
6. Portfolio profit: +2,580.48.

### Losing trade: SUGR.CA

1. Signal on 2020-08-06: BUY, score 56, confidence 65, RR 2.33, SIDEWAYS.
2. OOS probability 50.6%; rank 52.85; Ranking Only retains it.
3. Portfolio selected 2,118 shares.
4. Entry 2020-08-09 at 8.1141; stop 7.17, targets 9.35/10.30.
5. Trailing-stop exit 2020-08-11 at 8.0757.
6. Portfolio result: -190.62.

### Rejected trade: NINH.CA

1. Signal on 2020-09-02: BUY, score 85, confidence 95, RR 1.83, SIDEWAYS.
2. OOS probability 31.1%; rank 57.63; AI did not reject it.
3. Hypothetical entry/exit path was built (entry 3.0415; trailing-stop loss).
4. Daily portfolio simulator assigned zero shares and rejected it for
   `Capital` after higher-priority/current holdings consumed capacity/cash.
5. Executed portfolio profit: 0. This is a portfolio rejection, not an AI or
   strategy rejection.

## 7. Files modified after Phase 5

| File / function | Modification | Expected effect | Possible metric impact |
| --- | --- | --- | --- |
| `services/backtest_service.py::_run_pass_with_progress` | UI progress events | Show symbol progress | None; same engine loop. Full-window behavior already existed. |
| `dashboard/settings.py` | Display progress and mode selector | UI only | None unless user selects a different mode/settings and saves. |
| `ai/models/trading_model.pkl` + metadata | Live model retrained | New live probabilities | High for Scan Market; none for historical Ranking. |
| `reports/feature_importance.csv` | Rewritten by training | Diagnostic only | None. |
| `core/scanner.py::_numeric_sort_value` | Safely sort `None` | Prevent UI crash | None. |
| `dashboard/home.py` | Sort/display None as N/A | UI only | None. |
| `dashboard/stock_details.py` | Display None as N/A | UI only | None. |
| `config/settings.json` | Re-saved at 11:41 | Same values | None. |
| `reports/backtest_*` | Overwritten by a full-history Strategy run | Latest report provenance changed | High if compared to Phase 5 files. |
| `reports/ai_walk_forward_*` | Overwritten by training/reproduction runs | Different report provenance | High for interpretation, not trading logic. |

No entry, exit, indicator, strategy, ranking, sizing or portfolio-selection
file changed after the validated Phase 5 execution.

### Ranked regression suspects

1. **Different runner/date window and portfolio reset — 99% confidence.** Exact
   Phase 5 reproduction matches; latest standard report uses 3,317 versus 2,132
   days.
2. **Live scanner uses hard-filter LIVE_MODEL, not Ranking Only — 100% for BUY=0.**
   All three technical BUYs were downgraded by probabilities below 60%.
3. **Mutable report files mixed between runs — 95%.** Files have no run ID or
   configuration/date manifest and are overwritten by backtest/training.
4. **Post-Phase-5 global-model retraining — 90% for changed live probabilities,
   0% for historical Ranking metrics.**
5. **Provider availability (59 failures) — low for this discrepancy.** The exact
   baseline reproduced with the same failures.

## 8. Reproducibility result

Phase 5 **can currently be reproduced exactly**. The premise that it cannot be
reproduced is false under the current code, settings and downloaded dataset.

The configuration did not cause the apparent regression. The dashboard result
was generated by a different execution path:

- selected/default mode `STRATEGY_ONLY`,
- full ten-year interval,
- portfolio initialized before the OOS comparison start,
- mutable report later compared against an OOS Phase 5 report.

## 9. Exact explanations requested

- **Why BUY dropped to zero:** Live Scan found 3 technical BUYs; the global
  live hard filter scored all below 60% and downgraded them to WATCH. Ranking
  Only was never used by Scan Market.
- **Why AIProbability became None:** inference is intentionally skipped for 203
  technical WATCH/AVOID rows, and Strategy Only intentionally disables AI.
  True missing predictions/model failures: zero.
- **Why drawdown doubled:** the compared run includes pre-OOS years and carries
  their equity/cash path into later periods; Phase 5 starts a fresh 100,000
  portfolio at 2020-08-06. Latest persisted full-window DD is 71.33%, not 34%.
- **Why Profit Factor dropped:** the full window includes materially losing
  pre-OOS trades and changes later executions through capital constraints.
  Latest persisted full-window PF is 0.93, not 1.17.
- **Why the reported 47% cannot be tied to an artifact:** report files are
  overwritten without run identifiers; no current CSV contains that number.
- **Why Phase 5 cannot currently be reproduced:** it can. The exact validated
  figures were reproduced during this audit. The observed difference is an
  apples-to-oranges runner/window/mode comparison.

No strategy, AI threshold, feature, ranking formula, cost, exit or portfolio
setting was changed as part of this audit.
