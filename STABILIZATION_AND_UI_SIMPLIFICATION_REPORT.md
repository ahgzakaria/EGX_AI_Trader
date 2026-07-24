# EGX AI Trader Stabilization and UI Simplification Report

Date: 2026-07-18  
Scope: market-data routing, scan coverage semantics, and presentation only.  
Frozen components: strategy, AI, ranking, indicators, risk, portfolio, backtest, and forward-testing calculations.

## Executive result

The Swing/Daily route was corrected from **Rubix-derived daily OHLCV merged into Yahoo history** to **completed Yahoo-backed local daily history plus a non-mutating Rubix quote overlay**. The current universe now produces 206 analyzed symbols and 59 explicitly classified data failures, instead of returning only 54 rows and silently hiding 211 failures.

No trading threshold changed. An offline Phase 8 replay and an archived Swing decision comparison both matched exactly.

## Root cause of degraded results

`RubixSQLiteProvider.load_history()` aggregated the adapter's short `candles_1m` table into a daily row and `_merge_historical_seed()` appended or replaced the matching Yahoo daily row. That current/incomplete session row was then passed to the daily indicators and frozen decision engine.

Concrete evidence from `AALR.CA` before the fix:

- The merged 2026-07-16 row had `Low = 0.0`.
- `strategy/support.py:support_resistance()` used that zero support in `(current_price - support) / support`.
- The resulting `ZeroDivisionError` was caught by the Scanner's broad exception handler.
- The failed symbol was added only to `failed_symbols.csv`; it was omitted from the returned result list and therefore disappeared from the Dashboard totals.

After the routing fix, the same symbol loaded 1,996 completed daily rows from `local_cache:yahoo`; its 2026-07-16 daily low was 229.01, the decision completed normally, and Rubix Last/Bid/Ask/Volume remained metadata only.

## Exact cause of 54 / 265 coverage

The affected run `RUN_20260718_100210` requested 265 symbols and returned 54. Its 211 omitted symbols were:

| Failure category | Count | Evidence |
|---|---:|---|
| Zero division caused by invalid/incomplete Rubix-derived daily rows | 152 | `division by zero` in `failed_symbols.csv`; first divergence at `strategy/support.py` |
| Yahoo historical data unavailable | 51 | `yahoo: no data found` |
| Fewer than the required 250 daily rows | 8 | `not enough history` |
| Total omitted | 211 | 265 requested minus 54 returned |

The current coverage audit has 265 rows and reports:

| Final status | Count |
|---|---:|
| WATCH | 139 |
| AVOID | 67 |
| DATA_INSUFFICIENT | 59 |
| Successfully analyzed | 206 |

The 59 remaining failures are 48 missing historical datasets and 11 insufficient-lookback datasets. Three symbols that previously returned no Yahoo dataset now return short histories and therefore moved to the more precise insufficient-lookback category. No provider failure is counted as AVOID.

Full evidence: `reports/swing_symbol_coverage_audit.csv` and the immutable run copy under `reports/RUN_20260718_103258/`.

## Provider routing before and after

### Before

```text
Swing Scanner
  -> RubixSQLiteProvider.load_history(1d)
  -> aggregate short/current candles_1m to daily
  -> merge/overwrite Yahoo historical seed
  -> indicators and decision engine
```

### After

```text
Swing Scanner
  -> completed daily OHLCV from local_cache:yahoo
  -> Yahoo download only when historical cache is missing
  -> frozen indicators and decision engine

Rubix SQLite (read only)
  -> latest Last / Bid / Ask / Volume / quote timestamp / spread
  -> DataFrame.attrs market_data overlay only
  -> never appends or overwrites a daily candle
```

Backtest behavior remains Yahoo/archived-data based. Replay still fails closed to immutable archived frames and cannot call Yahoo or Rubix. Scalping still reads Rubix ticks/one-minute bars independently.

## Stable-run comparison

The latest complete pre-primary-Rubix Swing run inspected was `RUN_20260714_113403`:

- 265 requested, 206 analyzed, 59 failed.
- All 206 result rows reported Yahoo as their data source.
- Decisions: 1 BUY, 127 WATCH, 78 AVOID.
- Settings included minimum score 50, minimum confidence 65, minimum RR 1.5, and minimum history 250.

The post-fix current-data run `RUN_20260718_103258` analyzes the same 206-symbol historical universe. Its 0 BUY, 139 WATCH, and 67 AVOID counts use a newer completed candle, so those raw totals are not treated as a regression comparison.

For an identical-data comparison, all 206 archived frames from `RUN_20260714_170430` were re-evaluated with its settings snapshot. Signal, Score, and Confidence produced **0 differences**. Dataset hash: `3fd4cea81e7eb62206ab44ff16d8b7d534247dc2a2bab5342a21a2727db4df3c`.

## Result semantics

Coverage is now recorded independently from the strategy signal:

- `BUY`, `WATCH`, and `AVOID` are emitted only after successful strategy evaluation.
- Missing or short historical data becomes `DATA_INSUFFICIENT`.
- Provider/schema/connection failures become `PROVIDER_FAILED`.
- Evaluation failures that cannot produce a strategy decision become `NOT_ANALYZED`.
- Every requested symbol records historical provider, row count, minimum/maximum dates, required lookback, Rubix quote/minute availability, acceptance, rejection reason, and final status.

## Navigation before and after

### Before

The sidebar mixed Workspaces, Swing, Scalping, Research, and Live groups, with Decision Terminal and Performance Analytics occupying primary workspace positions. Provider diagnostics also occupied twelve large cards above trading results.

### After

The navigation has three groups:

1. **Swing / Daily**: Dashboard, Watchlist, Stock Details.
2. **Scalping**: Dashboard, Opportunities, Active Paper Trades, History, Settings.
3. **Research & System**: Backtest, Run History, Compare Runs, Replay, Performance Analytics, Forward Testing, System Health, Provider Diagnostics.

Swing/Daily is the default, so Research & System stays collapsed until selected. Existing page implementations remain in the project; the change is navigation/presentation only.

## Swing Dashboard behavior after simplification

The primary cards now show BUY, WATCH, AVOID, symbols successfully analyzed, latest completed daily candle, and live quote source. Data failures are explicitly shown separately and never inflate AVOID.

One compact notice distinguishes:

- historical analysis source;
- Rubix live quote overlay;
- latest completed candle;
- current quote timestamp.

Collector status, database status, quote freshness, coverage, and fallback reason moved into a collapsed **Data Status** expander. Detailed JSON evidence moved to **Research & System > Provider Diagnostics**.

## Files changed

- `app.py` — three-workspace navigation only.
- `core/data_provider.py` — strict Swing daily-history route and non-mutating quote overlay.
- `core/live_actionability.py` — operational actionability reads live-overlay metadata, not historical provider identity.
- `core/scanner.py` — complete per-symbol coverage accounting and list-compatible coverage metadata.
- `dashboard/home.py` — simplified primary metrics, data-failure disclosure, dedicated Stock Details page, compact Data Status.
- `dashboard/system_health.py` — separate Provider Diagnostics page.
- `providers/local_cache_provider.py` — read-only cache coverage inspection.
- `providers/rubix_sqlite_provider.py` — read-only `quote_overlay()` and symbol availability; intraday loading remains intact.
- `services/swing_coverage_audit.py` — coverage classification/reporting with no trading logic.
- `tests/test_swing_stabilization.py` — routing, disconnected Rubix, status semantics, and Scalping isolation tests.
- `reports/swing_symbol_coverage_audit.csv` — required 265-symbol audit artifact.

No strategy, AI, indicator, ranking, risk, portfolio, backtest, or forward-testing calculation file was changed in this stabilization.

## Validation results

### Automated tests

- Targeted provider/Swing/Rubix/navigation tests: **37 passed**.
- Full suite: **142 passed in 33.03 seconds**.
- Static `py_compile`: passed for all modified Python modules.
- Streamlit AppTest smoke: **STREAMLIT_SMOKE_OK**.

### Required operational cases

- Rubix quote overlay does not change any OHLCV value or indicator: passed.
- Rubix disconnected with valid historical cache still returns Swing analysis and marks live overlay unavailable: passed.
- Scalping 1-minute route still reads Rubix data directly: passed.
- No data failure is counted as AVOID: passed.
- Coverage audit contains all 265 symbols: passed.
- Archived Swing decisions on identical candles: 206 compared, 0 differences.

### Phase 8 offline Replay

Source: `RUN_20260714_125023`  
Replay: `RUN_20260718_103753`  
Dataset hash: `fafa064f87acf39992f53f3f8bf3b22b09d2ef34fb70b26d53dd932f38704414`

- `metrics_match`: **true**
- `predictions_match`: **true**
- Strategy Only: Return 66.43%, Profit Factor 1.29, Max Drawdown 18.02%, 729 trades.
- AI Ranking Only: Return 72.98%, Profit Factor 1.31, Max Drawdown 16.80%, 731 trades.

## Remaining limitations

- 48 symbols have no Yahoo daily history under their current `.CA` symbol.
- 11 symbols have fewer than the required 250 usable daily rows.
- Rubix quote freshness remains operational evidence only; stale/disconnected states prevent live actionability but do not erase valid end-of-day Swing analysis.
- The current paper-trading tracker logged a pre-existing nullable-AI conversion warning after the coverage scan; it did not alter the scan, coverage report, frozen strategy decisions, or replay metrics and was left outside this controlled data/UI scope.

## Final confirmation

Rubix had been incorrectly replacing completed daily evidence with an incomplete daily aggregation. That behavior is removed from the Swing/Daily route. Swing now uses completed historical OHLCV plus a strictly additive Rubix live quote overlay, while Scalping remains isolated on Rubix intraday data. No threshold or trading behavior was tuned.
