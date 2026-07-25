# Scalping Backtest Data Source Migration

## Outcome

The Scalping Backtest page now uses the existing Rubix SQLite database as its
default source. Daily CSV upload is no longer required. CSV upload remains
available as an optional source, and local archived CSV sessions can be selected
from a historical archive folder.

## Architecture

```text
External Rubix Collector
        ↓ writes
rubix_live_market.db / candles_1m
        ↓ SQLite mode=ro + PRAGMA query_only
RubixScalpingDataSource
        ↓ unchanged OHLCV DataFrames
ScalpingBacktest
```

The data source never creates, updates, or deletes adapter records. It uses the
centralized symbol mapping and converts UTC candle timestamps to Cairo time
before passing frames to the existing engine.

## Page behavior

- Default source: **Rubix Live Database (Recommended)**.
- Displays database path and read-only status.
- Lists stored session dates instead of assuming today's date has data.
- Lists only symbols stored for the selected session.
- Preselects COMI, SWDY, EAST, FWRY, and TMGH when available.
- Displays the frozen strategy values: TP, SL, market start, last entry, and
  force exit. They cannot be edited on this page.
- Preserves CSV Files as an explicit optional source.
- Supports local historical CSV files under `data/scalping_archive` or another
  selected local archive folder.
- Persists the latest displayed result in the existing Streamlit session state.

## Important evidence limitation

The adapter's `candles_1m` table stores OHLCV but not historical Bid/Ask for
every minute. When Bid/Ask columns are absent, the existing conservative spread
assumption in `ScalpingBacktest` is used. No spread, cost, TP, SL, entry, exit,
or setup rule was changed.

## Files

- Added `scalping/data_sources.py`.
- Modified `dashboard/scalping.py`.
- Modified `tests/test_scalping_module.py`.
- Added `SCALPING_BACKTEST_DATA_SOURCE_REPORT.md`.

## Validation

- Current database opened read-only successfully.
- Stored sessions observed: **1** (`2026-07-14`).
- Stored symbols observed: **265**.
- Stored minute bars observed: **1,743**.
- Direct UI execution from SQLite: **SCALPING_SQLITE_RUN_UI_OK**.
- Targeted scalping tests: **16 passed**.
- Full project regression suite: **138 passed**.
- Streamlit smoke: **STREAMLIT_SMOKE_OK**.
- An automated equivalence test proves SQLite-loaded candles and identical
  direct DataFrames produce identical trades, metrics, and limitations.

## Regression statement

No strategy, AI, ranking, indicator, portfolio, risk, daily backtest,
forward-testing, or experiment-tracking logic was modified. Only the Scalping
Backtest evidence source and its UI were changed.
