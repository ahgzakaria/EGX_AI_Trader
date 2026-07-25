# Phase 7 Implementation Report

## Executive Summary

Phase 7 introduces a persistent, production-oriented Forward Testing and Paper
Trading subsystem around the frozen EGX AI Trader engine.

The implementation does not modify trading rules, indicators, AI thresholds,
ranking, portfolio sizing, market-regime detection, entries, exits, or existing
report formats. Forward Testing consumes completed scanner decisions and stores
them as immutable evidence.

Validation result: **PASS — 32 automated tests passed.**

## Architecture

### Persistent database

`forward_testing/database.py` creates `data/forward_testing.db` automatically on
the first Forward Testing access. SQLite is configured with:

- WAL journaling
- full synchronous durability
- foreign-key enforcement
- 30-second busy timeout
- explicit `BEGIN IMMEDIATE` transactions
- indexed operational queries

The following entities are persisted:

- `live_sessions`
- `signals`
- `signal_evaluations`
- `signal_events`
- `alerts`
- `paper_positions`
- `paper_events`
- `portfolio_snapshots`

Signals and evaluations have database triggers that reject updates and deletes.
Events, alerts, and snapshots are append-only. `paper_positions` is a current
state projection; every state transition is preserved independently in
`paper_events`.

### Identity and duplicate prevention

Every signal has a deterministic immutable UUID derived from:

```text
ticker | source candle date | signal type
```

The database also enforces a unique deduplication key. Repeating a Scan,
restarting Streamlit, or retrying a session cannot create a duplicate signal.

Each Phase 6 Run ID is linked to:

- its live session
- every newly recorded signal
- the portfolio snapshot
- report copies saved inside the Run folder

### Timestamp model

Each signal stores two separate time concepts:

- `signal_date`: date of the last source candle used by the frozen scanner
- `signal_time` / `recorded_at`: real execution time of the live scan

Evaluations, alerts, signal events, paper events, sessions, and portfolio
snapshots all have explicit timestamps.

## Forward-Testing Workflow

Every Live Scan follows this chronological sequence:

1. Phase 6 creates a unique Run ID and snapshots the environment/settings.
2. The frozen scanner evaluates only the latest available candle.
3. Existing pending signals are evaluated using candles strictly later than
   their immutable signal date.
4. Existing paper positions are updated using the existing `ExitManager` and
   `TradingCosts` implementations.
5. Current scanner results are stored permanently.
6. New BUY signals create pending paper-entry candidates.
7. Alerts and a portfolio snapshot are appended.
8. Daily, week-to-date, and month-to-date reports are generated.
9. Forward metadata and reports are attached to the Phase 6 Run.

This ordering makes it impossible for a newly recorded signal to be evaluated
using its own signal candle as a future candle.

## Signal Storage

Every scanner result — BUY, WATCH, or AVOID — stores:

- immutable UUID
- date, execution time, and full recorded timestamp
- ticker, signal type, and price
- score, confidence, and Risk/Reward
- AI probability
- market regime
- scanner ranking
- reasons
- every value from the final indicator row as JSON
- settings SHA-256
- Phase 6 Run ID
- buy range, stop, Target 1, and Target 2

The SHA-256 is calculated from the effective merged runtime settings, not only
the raw JSON file.

## Pending Signals and Outcome Evaluation

BUY signals remain pending until future candles are available. Immutable
evaluations are appended at:

- 1 trading candle
- 3 trading candles
- 5 trading candles
- 10 trading candles
- 20 trading candles

Each horizon records:

- Return
- Maximum Favorable Excursion (MFE)
- Maximum Adverse Excursion (MAE)
- Target hit
- Stop hit
- Expired
- Still open
- evaluation timestamp and last included candle date

When a daily candle touches both stop and target, the stop is evaluated first.
This is the same conservative OHLC ambiguity rule used by the frozen
`ExitManager`.

Outcome reports calculate:

- Win Rate
- Average Return
- Median Return
- Expectancy
- Average Holding Days
- Average MAE
- Average MFE

## Paper Portfolio

The paper portfolio provides:

- virtual initial capital from the existing Backtest settings
- pending entries
- open positions
- closed trades
- cash
- marked-to-market equity
- exposure
- current drawdown
- open risk
- sector allocation (`Unknown` until sector reference data is available)
- durable state across application restarts

Entry waiting, entry costs, risk percentage, capital limits, maximum positions,
portfolio heat, overlapping-symbol rules, exits, trailing stops, partial exits,
and timeouts use the existing frozen project implementations and settings.

Daily entry candidates are ordered by the scanner's existing rank and ticker
tie-breaker. Phase 7 does not create a new ranking formula.

## Alerts

Immutable, deduplicated alerts are generated for:

- New BUY
- Target reached
- Stop reached
- Signal expired
- Position closed

Alerts survive restarts and are visible from the Forward Testing page.

## Reports

Forward reports are stored under `reports/forward_testing/` and copied into the
corresponding Phase 6 Run directory.

Generated reports include:

- `DAILY_REPORT_YYYYMMDD.md`
- `WEEKLY_REPORT_YYYY_WNN_ASOF_YYYYMMDD.md`
- `MONTHLY_REPORT_YYYYMM_ASOF_YYYYMMDD.md`
- Run-level `signal_outcomes.csv`

Files use exclusive creation and are never rewritten. Weekly and monthly
reports are period-to-date versions identified by their `ASOF` date, preventing
an early-period report from being silently replaced or mistaken for a final
period report.

Daily reports contain:

- new signals
- new BUY signals
- closed trades
- portfolio cash/equity/exposure/drawdown/open risk
- sector allocation
- outcome performance
- AI coverage and average probability
- generated alerts

## Dashboard Additions

### Live Scanner

The existing Scan button is now explicitly labelled **Live Scan Market** and
can run once per Streamlit application session. Database uniqueness provides a
second layer of duplicate protection after application restarts.

### Forward Testing page

- Pending Signals
- Closed Signals
- Signal Outcome report
- Daily Performance
- Alerts
- Equity Curve
- Open Risk
- Exposure
- Rolling Win Rate
- Rolling Expectancy
- Rolling Drawdown

### Paper Portfolio page

- Cash and equity
- Open and pending positions
- Closed trades
- Exposure and open risk
- Current drawdown
- Sector allocation
- Equity, risk, exposure, and drawdown charts

## New Files

- `forward_testing/__init__.py`
- `forward_testing/database.py`
- `forward_testing/service.py`
- `forward_testing/reporting.py`
- `dashboard/forward_testing.py`
- `tests/test_forward_testing.py`
- `PHASE7_IMPLEMENTATION_REPORT.md`

## Modified Files

- `core/scanner.py`
  - sends completed scanner results to Forward Testing
  - attaches Forward session metadata to the Phase 6 Run
  - fails loudly if permanent Forward storage fails
- `dashboard/home.py`
  - labels the Live Scan mode
  - prevents repeated scans in the same Streamlit session
- `app.py`
  - adds Forward Testing and Paper Portfolio navigation

No trading-engine, indicator, AI, ranking, sizing, regime, entry, exit, or
existing-report implementation was changed.

## Automated Validation

Five new Phase 7 tests verify:

1. deterministic UUIDs and duplicate prevention
2. database-level signal immutability
3. strict exclusion of the signal candle from future evaluation
4. exact 1/3/5/10/20-candle horizon creation
5. idempotent resume without duplicate evaluations
6. conservative Stop-before-Target handling
7. use of existing paper position sizing
8. Phase 6 Run linkage
9. immutable Daily Report copies
10. persistence across new service instances

Phase 7 tests:

```text
5 passed
```

Complete project regression suite:

```text
32 passed in 7.84s
```

The existing decision, AI leakage, Walk-Forward, overlay, ranking,
deterministic-order, portfolio, and exit tests remain green.

## Backward Compatibility

- `data/paper_trades.csv` and `PaperTradingTracker` remain available.
- Existing report files and writers were not changed.
- Phase 6 Run History remains the experiment source of truth.
- Scanner result dictionaries retain all previous fields; Phase 7 only consumes
  them after calculation.
- Strategy Only remains the production Backtest default.

## Operational Notes

- The first Forward Testing page visit or Live Scan creates the SQLite database.
- Database and report directories should be included in production backups.
- Forward results become statistically meaningful only after sufficient new,
  unseen market sessions; Phase 7 performs no threshold tuning or optimisation.
- A failed storage/reporting session is marked `FAILED` in both Phase 6 and the
  Forward database instead of silently continuing.

## Future Roadmap

1. Add a maintained ticker-to-sector reference file when authoritative EGX
   sector data becomes available.
2. Add optional email/Telegram/webhook delivery backed by the existing durable
   alert table; delivery retries must remain separate from alert creation.
3. Add scheduled market-session execution through an external scheduler while
   retaining the same idempotent service entry point.
4. Add encrypted off-machine backups for the SQLite database and Run folders.
5. Add database integrity checks and periodic restore drills.
6. Define a minimum Forward Testing sample and calendar duration before any
   production-capital decision.

## Final Validation Result

**PASS — Phase 7 is implemented as a production-quality, restart-safe,
append-only Forward Testing layer without changing the frozen trading engine.**
