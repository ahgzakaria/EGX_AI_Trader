# Market Data Provider Migration

**Date:** 2026-07-13  
**Scope:** Market-data acquisition and disclosure only  
**Trading engine status:** Frozen and unchanged

## Executive summary

EGX AI Trader now routes all market history through one provider entry point,
`core/data_provider.py`. Scanner and Dashboard request TickerChart by default;
historical Backtest requests Yahoo directly; Yahoo is the configured fallback.

TickerChart is always declared **delayed by 15 minutes** and is never presented
as live or real-time. The integration accepts only an authorized structured
adapter. It does not scrape TickerChart HTML or reverse-engineer its web client.

At validation time `TICKERCHART_ADAPTER_URL` was not configured on this machine.
The operational Dashboard route therefore requested TickerChart, logged the
configuration failure, returned Yahoo data, and exposed `Fallback Active` to the
UI. This is intentional fail-safe behavior, not a silent provider substitution.

## Architecture

```text
Scanner / Dashboard                Historical Backtest
        |                                  |
        | purpose=dashboard/scanner        | purpose=backtest
        v                                  v
                 core/data_provider.py
                 (only routing entry point)
                           |
             +-------------+-------------+
             |             |             |
        TickerChart       Yahoo       Local SQLite
        delayed 15m       fallback     TTL cache
             |             |             |
             +------ strict OHLCV schema-+
                           |
                  Existing indicators and
                    frozen trading logic
```

Provider metadata is stored in `DataFrame.attrs["market_data"]`. No provider
columns are added to the candle table and no existing Scanner/Backtest report
schema is changed.

## Files added

- `providers/__init__.py`
- `providers/base_provider.py`
- `providers/yahoo_provider.py`
- `providers/tickerchart_provider.py`
- `providers/local_cache_provider.py`
- `core/data_provider.py`
- `tests/test_market_data_providers.py`
- `MARKET_DATA_PROVIDER_MIGRATION.md`

## Files modified

- `.gitignore`
- `config/settings.json`
- `config/settings_manager.py`
- `core/data_loader.py`
- `core/scanner.py`
- `core/paper_trading.py`
- `backtesting/engine.py`
- `strategy/market_analyzer.py`
- `dashboard/home.py`
- `dashboard/settings.py`

`core/data_loader.py` remains only as a compatibility shim. It contains no
provider implementation and imports no `yfinance`.

## Provider routing

The following settings are active:

```json
{
  "scanner_provider": "tickerchart",
  "dashboard_provider": "tickerchart",
  "backtest_provider": "yahoo",
  "fallback_provider": "yahoo"
}
```

Nested EGX30 market-analyzer reads follow the same purpose as the caller. A
Dashboard decision therefore requests Dashboard data, while every historical
decision requests Backtest data. The market analyzer's existing no-volume
filter behavior remains preserved for index candles.

## Standard candle contract

All providers are normalized and validated before use:

- DatetimeIndex named `Date`
- Open
- High
- Low
- Close
- Adj Close when supplied by the source
- Volume

The validator sorts timestamps, rejects invalid timestamps, removes duplicate
timestamps by keeping the newest source record, converts numeric fields without
inventing values, and raises typed errors for missing required columns. Cairo
exchange wall time is preserved for timezone-aware TickerChart timestamps.

The legacy engine cleanup remains identical: required OHLCV rows must be
non-null, stock volume must be positive, and configured minimum history must be
available. No resampling, forward filling, indicator calculation, or price
adjustment was added.

## TickerChart adapter

TickerChart symbol mapping follows the verified application convention:

- `COMI.CA` -> `COMI.EGY`
- `^CASE30` -> `EGX30.EGY`

The provider expects an authorized adapter at `TICKERCHART_ADAPTER_URL` with:

```text
GET {base_url}/history?symbol=COMI.EGY&period=10y&interval=1d
```

Expected JSON contains a `data` list of timestamp/open/high/low/close/volume
records and optional metadata. Retry, timeout, reconnect-per-request, symbol
mapping, strict schema checks, and incomplete-history errors are implemented.

The web application is not scraped. Until the approved structured adapter URL
is configured, TickerChart health reports `unconfigured` and Yahoo fallback is
shown to the user.

## Fallback logic

1. Resolve the requested provider from purpose-specific settings.
2. Read a fresh SQLite cache entry when available.
3. Request the configured provider when cache is absent or expired.
4. Validate schema and minimum usable history.
5. On configuration, connection, timeout, missing-symbol, incomplete-history,
   or schema failure:
   - log the exact provider failure;
   - request Yahoo;
   - attach `fallback_active=true` and the reason;
   - keep the Dashboard running.
6. Backtest requests Yahoo directly, so TickerChart failure cannot affect its
   candle selection or historical decisions.

No fallback is silent.

## SQLite cache

Runtime cache: `data/market_data_cache.sqlite` (ignored by Git).

The cache key includes provider, symbol, period, and interval. It stores source
timestamp and normalized OHLCV rows plus provider metadata.

- Daily/weekly/monthly TTL: 1 day
- Intraday TTL: 5 minutes

A cached COMI Dashboard fallback read completed in approximately 0.02–0.05
seconds during validation and was marked `cache_hit=true`.

## Dashboard disclosure

The Dashboard always displays:

- Data Provider
- Data Status
- Declared Delay
- Latest Exchange Timestamp
- Latest Received Timestamp
- Fallback YES/NO
- Requested provider and provider health/status
- Fallback reason when active

TickerChart is labeled `DELAYED BY 15 MINUTES — never real-time`. The former
user-facing `Live Market` and `Run Live Market Scan` wording was replaced with
neutral market-data wording. Trading decisions and ranking remain untouched.

## Provider comparison

| Provider | Intended role | Delay disclosure | Failure behavior |
|---|---|---|---|
| TickerChart | Scanner/Dashboard requested default | Fixed 15 minutes | Explicit Yahoo fallback |
| Yahoo | Backtest default and fallback | Provider-dependent/EOD | Typed failure; no hidden source |
| Local Cache | Repeated reads | Preserves source metadata | Cache miss/expiry returns to source route |

TickerChart was chosen for operational freshness because entitlement validation
showed a completed EGX session that Yahoo had not yet published, despite the
15-minute delay.

## Tests and validation

Provider tests cover:

- Yahoo downloader isolation
- TickerChart symbol mapping and structured payload
- TickerChart 15-minute metadata
- schema normalization
- missing required columns
- missing symbols
- connection/timeouts and retry exhaustion
- SQLite cache roundtrip and expiry
- Dashboard fallback
- purpose-based Backtest provider selection
- identical indicator output for identical candles

Results before final documentation:

- Provider tests: **8 passed**
- Full suite: **50 passed**
- Python syntax/import compilation: passed
- Streamlit smoke test: passed
- Actual COMI routing check:
  - Dashboard requested TickerChart
  - TickerChart status: unconfigured
  - effective provider: Yahoo
  - fallback active: true
  - Backtest requested/effective provider: Yahoo

## Backtest regression

The complete locked Phase 5 OOS validation was rerun for 2020-08-06 through
2026-06-08.

| Mode | Trades | Net Profit | Return | Profit Factor | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| Strategy Only | 730 | 65,811.22 | 65.81% | 1.28 | 17.91% |
| AI Ranking Only | 736 | 71,618.87 | 71.62% | 1.30 | 16.48% |

**Exact confirmation: validated Phase 5 Backtest metrics did not change.**

## Forward Testing and reporting

Forward Testing receives the same Scanner result dictionaries and candle
DataFrames as before. Provider metadata remains in DataFrame attributes and is
not added to existing trade, signal, Experiment Tracking, or report schemas.
No Forward Testing or Experiment Tracking implementation was modified.

## Known limitations

1. The authorized TickerChart structured adapter URL is not configured in this
   environment; Scanner/Dashboard currently disclose Yahoo fallback.
2. The verified TickerChart account is delayed by 15 minutes and lacks Time &
   Sales and Market Depth entitlement.
3. TickerChart web UI data is not used because browser scraping is intentionally
   prohibited.
4. Yahoo delay is not a guaranteed number and is displayed as provider-dependent.
5. Changing adapter environment configuration requires provider-object reset or
   application restart.

## Trading-logic confirmation

No strategy rule, AI model, Walk-Forward fold, threshold, score, rank, position
size, portfolio rule, indicator formula, entry, exit, risk rule, cost, slippage,
or backtest statistic was changed.
