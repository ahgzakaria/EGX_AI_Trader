# TickerChart Operational Integration

Date: 2026-07-13 (Africa/Cairo)

## Executive status

The EGX AI Trader side of the operational bridge is implemented and tested.
The existing TickerChart collector is consumed through its SQLite output in
strict read-only mode. The private WebSocket protocol, authentication boundary,
and reconnect logic remain owned by the external adapter.

The external collector is **not running at the time of this report**. No
TickerChart SQLite database exists and neither `TICKERCHART_ADAPTER_PATH` nor
`TICKERCHART_DB_PATH` is configured in the current application process.
Consequently, the truthful current application state is:

- Requested provider: `tickerchart`
- Operational state: `TICKERCHART_UNAVAILABLE`
- Actual provider: `yahoo`
- Fallback active: `Yes`
- Exact reason: `TICKERCHART_DB_PATH is not configured`

TickerChart must not be described as actively supplying EGX AI Trader until the
external command below is running and its database contains a recent quote.

## Adapter inspected

External location inspected:

`C:\Users\ahgza\OneDrive\Documents\Scrapping\tickerchart_adapter`

Files reviewed completely:

- `adapter.py`
- `protocol.py`
- `storage.py`
- `README.md`
- `TICKERCHART_PROTOCOL.md`

The adapter is a read-only quote collector. It has no login client, HTTP service,
or public Python consumer API. It writes normalized quotes, 1-minute/5-minute
quote-derived candles, and connection metrics to SQLite. It explicitly declares
the EGX entitlement as delayed by 15 minutes.

## Chosen integration method

Chosen method: **C. Read the adapter's SQLite output**.

Reasons:

1. Direct package import was rejected because the adapter is an executable
   collector, not a versioned public package or history-reader API.
2. A localhost service does not exist in the audited adapter.
3. SQLite is the adapter's documented persistence contract and supports a
   read-only URI connection without executing or duplicating protocol code.
4. No HTML scraping, cookie export, login automation, protocol copying, or
   private endpoint reimplementation was added to EGX AI Trader.

## Data flow

Scanner/Dashboard daily data follows this route:

1. Yahoo supplies the historical warm-up required by the frozen indicators
   (minimum 250 bars).
2. The TickerChart SQLite quote supplies the most recent delayed session OHLCV
   from the normalized quote's raw `open`, `high`, `low`, `lasttradeprice`, and
   cumulative `volume` fields.
3. The TickerChart session day replaces a same-date Yahoo row, or appends a newer
   day.
4. The combined frame crosses the existing strict OHLCV normalizer.

This is deliberately disclosed in frame metadata as
`historical_seed_provider=yahoo` and `tickerchart_overlay_active=true`.
TickerChart is the operational/latest-candle source; Yahoo remains the
historical warm-up source. Backtest continues to request Yahoo directly and does
not traverse the TickerChart route.

Intraday requests read the adapter's quote-derived 1-minute or 5-minute candles
directly. Other adapter intervals fail explicitly and invoke the configured
Yahoo fallback rather than inventing candles.

## Central symbol mapping

All mapping is centralized in `providers/symbol_mapping.py`:

| Engine | EGX code | Adapter storage/subscription |
|---|---|---|
| `COMI.CA` | `COMI` | `COMI.EGY` |
| `SWDY.CA` | `SWDY` | `SWDY.EGY` |
| `FWRY.CA` | `FWRY` | `FWRY.EGY` |

No Scanner, Dashboard, strategy, or indicator file performs its own suffix
conversion.

## Configuration

Source code contains no user-specific absolute path. Configure these environment
variables in the process that runs the adapter/application:

- `TICKERCHART_ADAPTER_PATH`: existing adapter folder
- `TICKERCHART_DB_PATH`: SQLite output shared read-only with EGX AI Trader
- `TICKERCHART_STREAMER_URL`: current dynamically assigned signed-session
  `wss://.../ws/` URL; used only by the external collector launcher
- `TICKERCHART_LOCAL_URL`: reserved for a future documented localhost service

Equivalent empty deployment keys exist in `config/settings.json`. Environment
variables take effect when those keys are empty.

## Exact startup procedure

The dynamically assigned streamer URL must be copied from the legitimate signed
TickerChart session's Network > WS view. The audited adapter intentionally does
not extract cookies or discover/login to the account automatically.

Adapter terminal:

```powershell
cd D:\EGX_AI_Trader
$env:TICKERCHART_ADAPTER_PATH = 'C:\Users\ahgza\OneDrive\Documents\Scrapping\tickerchart_adapter'
$env:TICKERCHART_DB_PATH = 'D:\EGX_AI_Trader\data\tickerchart_quotes.sqlite3'
$env:TICKERCHART_STREAMER_URL = 'wss://<current-assigned-tickerchart-host>/ws/'
.\scripts\start_tickerchart_adapter.ps1
```

The launcher reads `data/symbols.csv`, converts every `.CA` symbol centrally to
`.EGY`, and starts the existing external `adapter.py`. It does not copy protocol
code. The project's Python environment already contains `websockets` 16.0 and
is the launcher's default interpreter.

Application terminal (same path/DB variables, no WebSocket URL needed):

```powershell
cd D:\EGX_AI_Trader
$env:TICKERCHART_ADAPTER_PATH = 'C:\Users\ahgza\OneDrive\Documents\Scrapping\tickerchart_adapter'
$env:TICKERCHART_DB_PATH = 'D:\EGX_AI_Trader\data\tickerchart_quotes.sqlite3'
.\venv\Scripts\python.exe -m streamlit run app.py
```

Operational health command:

```powershell
.\venv\Scripts\python.exe -m scripts.tickerchart_health --symbols COMI.CA,SWDY.CA,FWRY.CA
```

## Health and operational states

The provider validates all of the following before claiming activity:

- configured database path exists;
- SQLite opens read-only;
- `quotes`, `candles`, and `metrics` tables exist;
- at least one quote exists;
- latest exchange and receipt timestamps parse correctly;
- available symbols can be enumerated;
- the latest receipt is within `tickerchart_stale_after_minutes` (default 1440).

States implemented:

- `TICKERCHART_ACTIVE`: the collector database is reachable with recent data;
- `TICKERCHART_DELAYED`: operational data is present and explicitly delayed 15 minutes;
- `TICKERCHART_STALE`: the newest receipt exceeds the configured age;
- `TICKERCHART_UNAVAILABLE`: configuration, database, schema, or quote evidence is missing;
- `YAHOO_FALLBACK`: the requested TickerChart route failed and Yahoo is the actual provider.

An empty/configured database can never produce a false active state.

## Dashboard behavior

The Dashboard always retains the 15-minute TickerChart disclosure and displays:

- actual/effective provider;
- delayed/fallback status;
- declared delay;
- latest exchange timestamp;
- latest received timestamp;
- fallback Yes/No;
- operational/connection state;
- exact fallback reason.

Streamlit smoke validation on port 8510 passed. With the current external
collector unavailable, the rendered values were:

- Data Provider: `Yahoo`
- Data Status: `Fallback Active`
- Fallback: `YES`
- Provider status: `YAHOO_FALLBACK`
- Reason: `TICKERCHART_DB_PATH is not configured`

The Dashboard remained stable. The temporary server was stopped after testing.

## Symbols and timestamps observed

Real application smoke test (current configuration):

| Symbol | Actual provider | Latest candle | Fallback reason |
|---|---|---|---|
| `COMI.CA` | Yahoo | 2026-07-09 | TICKERCHART_DB_PATH is not configured |
| `SWDY.CA` | Yahoo | 2026-07-09 | TICKERCHART_DB_PATH is not configured |
| `FWRY.CA` | Yahoo | 2026-07-09 | TICKERCHART_DB_PATH is not configured |

The signed TickerChart page separately displayed delayed FWRY data dated
2026-07-12 at 14:29:25, but this was not claimed as adapter output because no
SQLite collector was running.

The automated SQLite integration fixture covered COMI, SWDY, and FWRY together
and proved active/delayed health, schema normalization, timestamp propagation,
and no fallback when a recent valid adapter database exists. Fixture timestamps
are test evidence, not live-market observations.

## Fallback test

The requested deliberate-disconnect behavior is covered by both the real current
state (database absent) and automated tests:

1. TickerChart read fails with a typed, exact reason.
2. Actual provider becomes Yahoo.
3. `fallback_active=true` and state becomes `YAHOO_FALLBACK`.
4. COMI, SWDY, and FWRY return valid Yahoo frames.
5. Dashboard remains operational and discloses the failure.

## Files added

- `providers/symbol_mapping.py`
- `scripts/start_tickerchart_adapter.ps1`
- `scripts/tickerchart_health.py`
- `TICKERCHART_OPERATIONAL_INTEGRATION.md`

## Files modified

- `providers/tickerchart_provider.py`
- `core/data_provider.py`
- `providers/local_cache_provider.py`
- `config/settings.json`
- `config/settings_manager.py`
- `dashboard/home.py`
- `tests/test_market_data_providers.py`

No strategy, AI, ranking, portfolio, indicator, entry, exit, risk, forward-test,
or backtest calculation was modified.

## Tests and regression results

- Provider/adapter targeted tests: 11 passed.
- Full automated suite: 53 passed.
- Static Python syntax/import compilation: passed.
- Streamlit smoke test: passed.
- Real three-symbol fallback smoke test: passed.
- Read-only adapter discovery: covered.
- Symbol mapping: covered.
- Schema normalization and malformed schema: covered.
- Stale data: covered.
- Missing symbol: covered.
- Explicit fallback/no false active status: covered.

Phase 5 exact reproduction after the integration:

| Mode | Trades | Net profit | Return | Profit Factor | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| Strategy Only | 730 | 65,811.22 | 65.81% | 1.28 | 17.91% |
| AI Ranking Only | 736 | 71,618.87 | 71.62% | 1.30 | 16.48% |

Coverage remained 2020-08-06 through 2026-06-08. Validated metrics did not
change.

## Known limitations

1. The external adapter requires a current dynamically assigned WebSocket URL;
   EGX AI Trader does not and must not automate login/cookie extraction.
2. The adapter is quote-only and reconstructs only 1/5-minute candles. Daily
   operational OHLCV is taken from complete quote fields and merged onto Yahoo
   historical warm-up data.
3. Quote-derived intraday volume starts at zero after collector startup until a
   second cumulative-volume observation arrives, as documented by the adapter.
4. TickerChart is a private, undocumented interface with no established SLA and
   remains explicitly 15 minutes delayed.
5. Current live TickerChart Scanner proof is pending external collector startup;
   the application correctly remains on Yahoo fallback until then.
