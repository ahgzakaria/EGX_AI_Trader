# Daily Scalping Module Implementation Report

## Scope and isolation

The daily scalping module is a new, optional package under `scalping/`. It does not import or mutate the frozen daily strategy, AI, ranking, portfolio, indicator, backtest, forward-testing, or experiment-decision code. Its settings live in the independent `scalping` section and its records live in `data/scalping.db`.

Production defaults are deliberately safe:

- `enabled = false`
- `mode = PAPER_ONLY`
- `take_profit_percent = 2.0`
- `stop_loss_percent = 2.0`
- `require_rubix_fresh = true`
- `allow_yahoo_actionable = false`
- `close_at_session_end = true`

No broker integration or real-money order placement exists.

## Architecture

- `config.py`: immutable independent configuration, session cutoffs, and configurable tick-size bands.
- `models.py`: setup, signal, fill, position, exit, and daily-risk domain records.
- `decision.py`: Rubix/freshness/Bid/Ask/volume/spread/session actionability gate.
- `setup_detector.py`: Momentum Breakout, Opening Range Breakout, and VWAP Reclaim/First Pullback detectors.
- `entry_engine.py`: Ask-side paper fills, slippage, and fill-relative fixed TP/SL.
- `exit_engine.py`: Bid-side exits, conservative same-bar ambiguity, and mandatory session close.
- `risk_engine.py`: account-risk sizing, exposure limits, daily loss and consecutive-loss kill switches, heat, capacity, and revenge-entry cooldown.
- `database.py`: dedicated WAL SQLite store with UUIDs, uniqueness constraints, immutable signal/fill/exit triggers, recovery, alerts, risk state, and session summaries.
- `scanner.py`: read-only Rubix SQLite scanner and resume-safe open-position review.
- `paper_portfolio.py`: paper fills and lifecycle persistence only.
- `backtest_engine.py`: separate minute/tick-aware conservative event loop; daily OHLCV is explicitly rejected.
- `reporting.py`: total and grouped setup/time/symbol metrics.

## Fixed 2% TP/SL and execution assumptions

Levels are calculated from the actual entry fill, never from the signal price:

```text
target = actual_entry_fill * 1.02
stop   = actual_entry_fill * 0.98
```

The paper entry starts at Ask and applies configured buy-side slippage. Exits start at Bid and apply configured sell-side slippage. Entry and exit commission are included in net P&L. Signal price, requested price, actual fill, target, stop, exit fill, gross return, costs, slippage, and net return are recorded separately.

When a minute bar touches both target and stop and tick order is unavailable, the stop is resolved first and `ambiguous_same_bar` is recorded. Tick replay may supersede that assumption only when chronological tick evidence exists.

Tick-size bands are configuration, not trading optimisation. The shipped bands support 0.001 below EGP 2 and 0.01 otherwise. The official EGX site was not accessible during implementation, so operations must verify the current exchange/instrument tick table before any future live-use decision. This limitation does not affect paper-only status.

## Data and actionability

Actionable opportunities require all of the following:

- requested/effective provider is Rubix;
- Rubix state is fresh;
- collector is connected;
- quote age is within the configured limit;
- the EGX session is open and entry cutoff has not been reached;
- Bid, Ask, and volume exist;
- spread and liquidity pass the conservative gate;
- one of the three technical setups qualifies.

Stale, missing, Yahoo fallback, daily fallback, disconnected-collector, or market-closed data is analysis-only and non-actionable. Yahoo daily candles can never create an actionable scalping BUY.

## Risk and no-overnight controls

The fixed price stop is independent of account sizing. Quantity is the minimum of cash-risk sizing, per-symbol exposure, and available liquidity when size exists. New entries stop after daily loss, maximum trades, maximum positions, maximum heat, or consecutive-loss limits. Averaging down and martingale behavior are absent. A symbol cooldown blocks immediate revenge re-entry.

New entries stop at 14:10 Cairo by default. Every remaining paper position is forced closed at 14:25 Cairo. On restart, open positions are restored from `data/scalping.db` and evaluated before new setups; a prior-date position is never allowed to remain open.

## Database schema

The dedicated database contains:

- `signals`
- `entry_attempts`
- `fills`
- `open_positions`
- `exits`
- `rejected_opportunities`
- `daily_risk_state`
- `session_summaries`
- `alerts`

WAL, foreign keys, busy timeout, UUID primary keys, duplicate keys, transactions, and integrity checks are enabled. The Rubix database is opened read-only and is never used for scalping records.

## UI changes

A separate `SCALPING` navigation group contains:

- Scalping Dashboard
- Live Opportunities
- Active Trades
- Scalping Paper Portfolio
- Scalping Backtest
- Scalping History
- Scalping Settings

The pages disclose Paper-only mode, Rubix health, market phase, quote time/age, P&L, risk remaining, positions, opportunities, rejections, fills, exits, and persistent alerts. Settings save only the isolated `scalping` section.

## Tests and validation

Implemented tests cover:

- exact fill-relative +2% target and -2% stop;
- tick-size rounding;
- actual-fill rather than signal-price levels;
- Ask entry and Bid exit;
- commission, slippage, gross and net return;
- stale, Yahoo, and disconnected-collector rejection;
- mandatory session close and overnight guard;
- conservative same-bar ambiguity;
- daily-loss and consecutive-loss kill switches;
- account-risk position sizing;
- duplicate signal prevention;
- WAL database recovery and integrity;
- persistent fill/exit lifecycle;
- rejection of daily OHLCV by the scalping backtest;
- disabled/PAPER_ONLY defaults and unchanged frozen settings;
- Streamlit navigation icons and headless startup.

Validation results at implementation time:

- Scalping targeted tests: 13 module tests passed (14 including navigation), including an executable one-minute backtest path.
- Final complete regression suite: 109 tests passed.
- Streamlit headless smoke: `STREAMLIT_SMOKE_OK`.
- Phase 8 replay `RUN_20260714_152458`: dataset hash `fafa064f87acf39992f53f3f8bf3b22b09d2ef34fb70b26d53dd932f38704414`, `metrics_match=true`, and `predictions_match=true`.
- Reproduced Strategy Only: 729 trades, 66.43% return, 1.29 Profit Factor, 18.02% maximum drawdown.
- Reproduced AI Ranking Only: 731 trades, 72.98% return, 1.31 Profit Factor, 16.80% maximum drawdown.

The replay proves that the isolated module did not change the existing validated trading decisions or metrics.

## Known limitations and paper-testing workflow

- Existing Rubix SQLite retention may not yet contain 30 complete market sessions of minute/tick history. The backtest reports insufficient coverage rather than substituting daily candles.
- Bid/Ask size is used when available; otherwise liquidity checks rely on observable volume and conservative spread assumptions.
- Exchange holidays require operational/calendar confirmation.
- The current tick-size table must be verified against an accessible official EGX source before any future live-use review.
- AI does not hard-filter scalping setups. Optional ranking is intentionally deferred.
- Run at least 30 fresh EGX market sessions in forward paper mode, review each setup independently, and make a separate governance decision afterward. This implementation does not approve automated real-money trading.

## Files added

- all files under `scalping/` listed in the architecture section;
- `dashboard/scalping.py`;
- `tests/test_scalping_module.py`;
- `scripts/streamlit_smoke.py`;
- this report.

## Files modified

- `app.py`: additive SCALPING navigation group only.
- `config/settings.json`: independent disabled scalping defaults.
- `config/settings_manager.py`: backward-compatible scalping defaults.
- `scripts/create_release.py`: include the isolated package/report in RC1.

No existing strategy, AI, indicator, ranking, daily portfolio, daily entry/exit, backtest calculation, or validated threshold was modified.
