# Rubix Market Data Source Migration

Date: 2026-07-13 (Africa/Cairo)

## Scope and frozen-engine guarantee

This change replaces only the live market-data source boundary. No strategy,
AI, walk-forward, ranking, indicator, entry, exit, portfolio, risk, backtest,
forward-evaluation calculation, or experiment-tracking calculation was
modified.

The resulting route is:

```text
Independent Rubix adapter (writer/network owner)
                  |
                  v
        adapter-owned SQLite database
                  | mode=ro
                  v
          RubixSQLiteProvider
                  |
                  v
            ProviderManager
          /        |         \
     Scanner   Dashboard   Forward Testing

Historical Backtest --------------------> YahooProvider
```

EGX AI Trader contains no Rubix WebSocket, HTTP, browser, authentication,
cookie, token, login, scraping, Selenium, Playwright, or DevTools code.

## Provider architecture

### Existing interface

`providers.base_provider.MarketDataProvider` remains the single provider
contract. Every frame crosses the existing strict `normalize_history` boundary
and returns the unchanged engine schema:

- timezone-normalized `DatetimeIndex`;
- `Open`;
- `High`;
- `Low`;
- `Close`;
- optional `Adj Close`;
- `Volume`.

### RubixSQLiteProvider

`providers/rubix_sqlite_provider.py`:

- opens only `file:...?...mode=ro` SQLite connections;
- validates `quotes`, `candles_1m`, and `feed_metrics`;
- validates the documented Rubix columns;
- maps engine symbols such as `COMI.CA` to Rubix `COMI` centrally;
- reads quote timestamps per symbol;
- reads adapter-generated one-minute OHLCV;
- aggregates those received one-minute bars into a Cairo-session daily overlay;
- uses Yahoo history only as the warm-up required by the frozen indicators;
- never fills missing minutes or fabricates ticks;
- never creates, updates, or deletes adapter rows.

### ProviderManager

`providers/provider_manager.py` owns source selection. Scanner, Dashboard, and
Forward Testing do not compare or load providers themselves.

Selection policy:

1. Load the configured Rubix SQLite symbol.
2. Load the Yahoo comparison frame through the existing cache/provider path.
3. Compare the Rubix market timestamp with Yahoo's latest candle timestamp.
4. Use Rubix only when Rubix is newer.
5. If Rubix is stale but still newer, use Rubix and display a prominent STALE
   warning. Staleness is never hidden.
6. If Rubix is unavailable, malformed, missing the symbol, or not newer, use
   Yahoo and persist the exact fallback reason in DataFrame metadata.

This avoids the invalid policy of rejecting a newer stopped-session feed in
favor of older Yahoo data merely because a wall-clock freshness limit elapsed.

## Routing and configuration

Effective defaults:

| Purpose | Provider |
|---|---|
| Scanner | Rubix |
| Dashboard | Rubix |
| Forward Testing | Rubix |
| Historical Backtest | Yahoo |
| Fallback | Yahoo |

`config/settings.json` contains:

```json
{
  "scanner_provider": "rubix",
  "dashboard_provider": "rubix",
  "forward_testing_provider": "rubix",
  "backtest_provider": "yahoo",
  "fallback_provider": "yahoo"
}
```

The current local deployment points to the validated database:

```text
C:\Users\ahgza\OneDrive\Documents\Scrapping\reports\rubix_live_market_definitive.db
```

For a different production adapter output, set `RUBIX_DB_PATH`; the environment
value takes precedence over the local settings file. `RUBIX_STALE_AFTER_MINUTES`
controls the open-session warning threshold and defaults to five minutes.

The only application-owned market cache is
`data/market_data_cache.sqlite`. Adapter tables remain external and read-only.

## Dashboard disclosure

The Dashboard now displays:

- Current Provider;
- Freshness;
- Last Update;
- Fallback Status;
- Database Status;
- compared-source update timestamp;
- exact fallback reason;
- a prominent STALE warning when the selected newest Rubix snapshot is stale.

The UI explicitly states that data is consumed through read-only SQLite and
that EGX AI Trader performs no Rubix network connection or authentication.

## Actual operational validation

Read-only health check against the validated database:

| Field | Observed value |
|---|---|
| Database status | `READ_ONLY_OK` |
| Schema valid | Yes |
| Symbols | 5 |
| Quote rows | 4,759 |
| Latest Rubix market timestamp | 2026-07-13 08:57:05 UTC / 11:57:05 Cairo |
| Latest Rubix receipt | 2026-07-13 08:57:04.228887 UTC |
| Current health during test | `RUBIX_STALE` |
| Stale disclosure | Visible and retained in metadata |

COMI comparison:

| Source | Latest timestamp |
|---|---|
| Rubix | 2026-07-13 08:57:05 UTC |
| Yahoo | 2026-07-12 daily candle |

ProviderManager selected Rubix for COMI because its timestamp was newer, while
retaining `STALE` status. The routed frame contained 2,339 bars and ended on
2026-07-13.

Explicit fallback test for `KWIN.CA`:

- Rubix result: symbol absent from the five-symbol validation database;
- effective provider: Yahoo;
- fallback active: Yes;
- exact reason: `Rubix SQLite has no data for KWIN`;
- Dashboard remained operational.

The production adapter must subscribe to the complete application symbol list
for broad Rubix coverage. Missing symbols intentionally continue through Yahoo.

## Read-only proof

Automated tests recorded adapter table names and quote-row counts, loaded Rubix
health and OHLCV, then re-read the database. Table names and row counts were
identical. No trading records were created in the adapter database.

## Validation and regression results

- Targeted Rubix/provider/Forward Testing tests: 22 passed.
- Full project suite: 73 passed.
- Syntax/import compilation: passed.
- Identical OHLCV -> identical normalized values: passed.
- Identical OHLCV -> identical indicators: passed.
- Identical OHLCV -> identical final strategy decision: passed.
- Forward Testing missing-frame loads use `purpose="forward_testing"`: passed.
- Rubix missing/unavailable/schema/stale/fallback tests: passed.
- Adapter read-only immutability test: passed.

Phase 5 full reproduction after migration:

| Mode | Trades | Net Profit | Return | Profit Factor | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| Strategy Only | 730 | 65,811.22 | 65.81% | 1.28 | 17.91% |
| AI Ranking Only | 736 | 71,618.87 | 71.62% | 1.30 | 16.48% |

OOS coverage remained exactly 2020-08-06 through 2026-06-08. No validated
trading metric changed.

A separate Streamlit server-process smoke launch could not be started by the
execution environment because its GUI/process approval quota was exhausted.
The Dashboard module compiled and imported successfully and the complete test
suite passed; no code workaround was used to bypass that restriction.

## Files added

- `providers/rubix_sqlite_provider.py`
- `providers/provider_manager.py`
- `scripts/rubix_health.py`
- `RUBIX_MARKET_DATA_MIGRATION.md`

## Files modified

- `providers/symbol_mapping.py`
- `providers/local_cache_provider.py`
- `providers/__init__.py`
- `core/data_provider.py`
- `config/settings.json`
- `config/settings_manager.py`
- `dashboard/home.py`
- `forward_testing/service.py`
- `tests/test_market_data_providers.py`
- `tests/test_forward_testing.py`

## Known limitations

1. The currently configured validated database contains only COMI, SWDY, TMGH,
   EAST, and FWRY. Other symbols correctly fall back to Yahoo until the adapter
   subscribes to them.
2. Current Rubix candles are quote-derived one-minute bars, not official
   exchange time-and-sales candles.
3. The validated database is presently stale because its adapter capture ended.
   Start the independent adapter against the configured production database to
   restore fresh updates.
4. Rubix is an undocumented/session-coupled external feed. This consumer does
   not attempt to repair, authenticate, or reconnect it.
5. Yahoo remains the historical warm-up and frozen Backtest source. This is
   intentional and required for reproducibility.
