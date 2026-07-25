# EGX AI Trader — Architecture Audit

## Outcome

All application entry points now use one final trading-decision service:

```text
market data -> indicators -> technical decision engine -> optional AI gate
            -> final BUY / WATCH / AVOID -> scanner / watchlist / backtest
```

The technical engine remains the owner of Score, Confidence, Risk/Reward,
Trend, Momentum, Volume, candle confirmation, market analysis, quality,
ATR and resistance-room checks. `TradingDecisionService` is the only layer
allowed to add the optional AI-probability gate to a technical BUY.

## Files modified by this audit

- `strategy/trading_decision.py` — new final-decision service and shared
  rejection-reason registry.
- `strategy/decision_engine.py`, `strategy/config.py`, `strategy/filter.py`,
  `strategy/market_regime.py`, `strategy/market_analyzer.py` — centralised
  configurable strategy rules.
- `core/scanner.py`, `core/symbols.py`, `core/data_loader.py`,
  `core/watchlist.py` — shared symbol loading and safer data boundaries.
- `backtesting/engine.py`, `services/backtest_service.py`, `backtest.py` —
  shared decision service and a single backtest orchestration path.
- `config/settings_manager.py`, `config/settings.json`,
  `dashboard/settings.py` — resilient settings defaults and preservation of
  hidden central settings when the UI saves.
- `dashboard/home.py` — safe handling of an empty filtered result.
- `tests/test_trading_decision.py` — AI-gate consistency coverage.

Removed unused `config.py` and `strategy/strategy_result.py`.

## Bugs found and fixed

1. **AI filtering differed by entry point.** Scanner downgraded low-AI BUY
   signals to WATCH while BacktestEngine used the technical result directly.
   Both now call `TradingDecisionService`.
2. **Duplicate backtest implementations.** `backtest.py` and the dashboard
   service had independent orchestration. The CLI is now only a presentation
   wrapper around `services.backtest_service.run_backtest`.
3. **Symbol loading differed and could throw KeyError.** Scanner accepted a
   CSV or list while backtest/optimizer directly indexed `Ticker`. All now
   use `core.symbols.load_symbols`, which validates the source and cleans
   duplicates/empty values.
4. **A filtered dashboard could crash.** Selecting a signal with zero rows
   produced an empty select box and could reach `next(...)`. The page now
   returns with an informative message.
5. **Partial settings could cause KeyError or reset hidden values.** Settings
   now merge safely with defaults, and the settings page preserves central
   fields it does not expose.
6. **Market-data assumptions were implicit.** Missing OHLCV/Date columns now
   produce clear validation errors. History period, interval and minimum bars
   are central settings.
7. **Market-regime and WATCH thresholds were hard-coded.** Their existing
   values are now in `settings.json`, so all consumers use one source.

## Consistency guarantees

- Dashboard Scan Market and Watchlist both call `core.scanner.scan_symbols`.
- Scanner, paper-trade recording and BacktestEngine receive the same final
  BUY/WATCH/AVOID signal from `TradingDecisionService`.
- Backtest service, command-line backtest and optimizer construct the same
  BacktestEngine and use the same settings-backed strategy.
- Portfolio simulation is intentionally downstream: it does not decide an
  entry signal; it applies capital, overlap and portfolio-heat constraints
  after a final BUY has been generated.

## Verification performed

- `python -m unittest discover -s tests -v`: 9 tests passed after Phase 2.
- Python compilation passed for all application packages.
- `git diff --check` reported no whitespace errors.

## Phase 2 — Historical AI integrity

- Added an explicit `STRATEGY_ONLY` backtest mode. It cannot construct or use
  the persisted global AI model.
- Added `WALK_FORWARD_AI` mode. Each chronological fold trains a new pipeline
  using only labels with `exit_date < test_start`; median imputation is fitted
  on that fold's training rows only.
- Added per-fold dates, class distribution, accuracy, precision, recall, F1,
  ROC-AUC, confusion matrices and prediction-level reports.
- The former global-model historical AI metrics are invalid and are documented
  as discarded in [`AI_WALK_FORWARD_REPORT.md`](../strategies/AI_WALK_FORWARD_REPORT.md).

## Remaining technical debt

1. **Walk-Forward source coverage.** The first pass labels only strategy
   entries that were actually simulated. An AI rejection can expose later
   same-symbol candidate signals absent from that first-pass training set.
   A future event-level label store should capture every candidate signal.
2. **Provider reliability.** Yahoo Finance has unavailable/delisted symbols.
   The application isolates those failures and reports them, but production
   use should add a market-data provider abstraction, retry policy and local
   cache.
3. **Configuration UX.** Market-regime and data-history settings are
   centralised but not yet exposed as dashboard controls, to preserve the
   existing UI and avoid accidental strategy changes.
4. **Automated coverage.** Unit coverage now protects the final AI gate,
   exits and one-class models. Integration tests with deterministic local
   OHLCV fixtures should be added for scanner/backtest/paper-trading parity.

## Phase 3 — Comparative historical validation

- The historical AI pass uses only fold-local, out-of-sample predictions and
  fails rather than falling back if a prediction is unavailable.
- The feature boundary between indicator rows (`RSI`, `EMA20_DIST`, etc.) and
  model features (`rsi`, `ema20_dist`, etc.) is now explicitly normalised in
  `HistoricalAIFilter`.  This fixed an issue that could have rejected valid
  candidates as if all model inputs were missing.
- `services/comparison_reporting.py` now produces traceable trade-level,
  yearly, regime, symbol and probability-bucket CSVs from the same backtest
  configuration.
- Comparative outcome: the AI filter lowered drawdown from 18.59% to 5.81%,
  but lowered net profit from 54,035.58 to 16,409.72.  The present verdict is
  **B — AI reduces risk but sacrifices too much return**.

### Additional technical debt

5. **Comparison runtime.** The full market-wide Walk-Forward comparison is
   intentionally conservative and can take several minutes.  Batch feature
   inference and a durable local OHLCV cache should be added before scheduled
   production reporting.

## Phase 3 files and verification

Files added or changed specifically for the comparative validation:

- `ai/walk_forward.py`
- `tests/test_walk_forward.py`
- `services/backtest_service.py`
- `services/comparison_reporting.py`
- `backtesting/engine.py`
- `backtesting/trade.py`
- `backtesting/builders/trade_builder.py`
- `core/data_loader.py`
- [`AI_WALK_FORWARD_REPORT.md`](../strategies/AI_WALK_FORWARD_REPORT.md)
- [`STRATEGY_VS_AI_COMPARISON.md`](../strategies/STRATEGY_VS_AI_COMPARISON.md)
- generated `reports/strategy_vs_ai_*.csv` and `reports/ai_walk_forward_*.csv`

Verification after the final comparison:

- All 12 automated tests passed.
- Python compilation completed successfully for all application packages.
- `git diff --check` completed with no whitespace errors (only existing Windows
  line-ending notices).

## Phase 4 — Calibrated AI risk overlay

- Added five explicit historical modes: `STRATEGY_ONLY`, `AI_HARD_FILTER`,
  `AI_POSITION_SIZING`, `AI_RANKING_ONLY` and `AI_HYBRID`.
- The technical decision remains the sole owner of entry validity. The overlay
  can only preserve it, change position size, rank a same-day capacity tie, or
  apply the configured hard/emergency safety rejection.
- Historical overlays use `HistoricalAIFilter` exclusively with strict
  out-of-sample prediction coverage; no persisted global model can enter the
  historical decision path.
- `PortfolioSimulator` applies the final multiplier to shares and risk, while
  ranking only changes same-day entry order for the Ranking/Hybrid modes.
  Strategy Only retains its original stable chronology.
- The Phase 4 full comparison recommends **D — AI Ranking Only**: 71.62%
  return versus 54.04% Strategy Only, with 16.48% versus 18.59% drawdown and
  +13,626.53 net economic value. This does not alter the default setting.

### Phase 4 files

- `ai/risk_overlay.py`
- `strategy/trading_decision.py`
- `backtesting/trade.py`
- `backtesting/builders/trade_builder.py`
- `backtesting/engine.py`
- `portfolio/portfolio_simulator.py`
- `services/backtest_service.py`
- `services/risk_overlay_reporting.py`
- `config/settings.json`, `config/settings_manager.py`
- `dashboard/settings.py`
- `tests/test_ai_risk_overlay.py`
- [`AI_RISK_OVERLAY_COMPARISON.md`](../strategies/AI_RISK_OVERLAY_COMPARISON.md)
- `reports/ai_risk_overlay_summary.csv`, `reports/ai_risk_overlay_trades.csv`

## Phase 5 — Ranking robustness and deterministic portfolio selection

- `PortfolioSimulator` now processes exits before entries and ranks every
  same-day entry batch as a group. It no longer inherits symbol-file order.
- Deterministic tie breakers are rank, AI probability, strategy score,
  confidence, Risk/Reward and alphabetical symbol. Reversed and seeded-shuffle
  runs are asserted to have identical trade signatures and metrics.
- Added segmented reports for year/quarter, regime, liquidity and volatility;
  sector analysis is unavailable because the supplied symbol source has no
  sector metadata.
- AI Ranking Only remains **optional**, not the default: it improves the full
  deterministic period but is mixed by quarter and loses its edge when the top
  ten baseline trades are removed.

### Phase 5 files

- `portfolio/portfolio_simulator.py`
- `backtesting/managers/entry_manager.py`
- `backtesting/engine.py`
- `services/backtest_service.py`
- `services/ranking_robustness.py`
- `tests/test_portfolio_determinism.py`
- `tests/test_ranking_robustness.py`
- [`AI_RANKING_ROBUSTNESS_REPORT.md`](../strategies/AI_RANKING_ROBUSTNESS_REPORT.md)
- generated `reports/ai_ranking_*.csv`
