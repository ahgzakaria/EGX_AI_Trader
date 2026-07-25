# TradingView Provider — Existing Architecture Audit (Phase A)

**Purpose:** document the interfaces a TradingView research provider must plug
into, so it slots in without touching production routing. No production code
was changed to produce this document.

---

## 1. `MarketDataProvider` interface ([providers/base_provider.py](../../../providers/base_provider.py))

```python
class MarketDataProvider(ABC):
    name = "base"
    delayed = False
    delay_minutes = 0

    @abstractmethod
    def load_history(self, symbol, period, interval) -> pd.DataFrame: ...
    def health(self) -> dict: ...
```

Every provider must return a `DatetimeIndex` OHLCV frame that survives
`normalize_history(frame, symbol, provider)`, which:
- accepts flexible column aliases (`date/open/high/...`) and a `MultiIndex`,
- coerces a Date/Datetime/Timestamp column into the index,
- requires `Open, High, Low, Close, Volume` (± `Adj Close`),
- sorts, drops duplicate index entries (`keep="last"`), drops all-NaT rows,
- stamps `frame.attrs["market_data"] = {"provider", "symbol", "received_timestamp"}`.

A TradingView provider must produce exactly this contract — same required
columns, same attrs contract — so it is a drop-in candidate wherever a
`MarketDataProvider` is expected, without any engine-side changes.

## 2. Yahoo provider ([providers/yahoo_provider.py](../../../providers/yahoo_provider.py))

Thin wrapper over `yfinance.download`; `delayed=True`, no official real-time
entitlement. This is today's **sole production historical source** for
Swing/Daily and remains so throughout this task.

## 3. Local cache provider ([providers/local_cache_provider.py](../../../providers/local_cache_provider.py))

Owns `data/market_data_cache.sqlite` (`market_data_entries` + `market_data_candles`
tables, keyed by `(provider, symbol, period, interval)`). `load_cached(...,
allow_expired=True)` is what the frozen Swing/Daily route uses to read Yahoo
history without triggering a fresh network call every scan. Any new provider
can reuse this cache under its own `provider` key (e.g. `"tradingview_csv"`)
without touching Yahoo's cached rows.

## 4. `ProviderManager` ([providers/provider_manager.py](../../../providers/provider_manager.py))

Generic "prefer X only if newer than fallback" comparator — compares
`source_latest_timestamp`/`latest_exchange_timestamp` in each frame's
`market_data` attrs and falls back explicitly with a disclosed reason. This is
the mechanism a future `tradingview` route could use analogous to Rubix,
**but it is not being wired in for this task** — TradingView stays fully
outside routing.

## 5. Swing/Daily frozen routing ([core/data_provider.py](../../../core/data_provider.py))

```python
if (requested_name == "rubix" and purpose in {scanner, dashboard, forward_testing}
        and interval == "1d" and isinstance(providers.get("rubix"), RubixSQLiteProvider)):
    finalized = _load_swing_daily_history(...)  # Yahoo history + Rubix quote overlay only
```

This path is **unchanged** by this task. TradingView is not referenced
anywhere in `core/data_provider.py`.

## 6. Rubix live quote overlay + Completed-Daily Bridge (prior work)

- `providers/rubix_sqlite_provider.py` — read-only adapter DB, live quote
  overlay only, never mutates daily history.
- `providers/rubix_daily_aggregator.py` / `rubix_completed_daily_bridge.py` —
  built and shadow-tested previously; **disabled** (`config/settings.json →
  rubix_daily_bridge.enabled: false`) because coverage/volume-reliability
  gates reject all current sessions (see
  [RUBIX_DAILY_CAPABILITY_AUDIT.md](RUBIX_DAILY_CAPABILITY_AUDIT.md),
  [RUBIX_COMPLETED_DAILY_BRIDGE_REPORT.md](RUBIX_COMPLETED_DAILY_BRIDGE_REPORT.md)).
- The TradingView bridge (Phase I of this task) reuses the **same append-only,
  never-overwrite, provenance-tracked pattern** for consistency and review
  familiarity.

## 7. Settings / environment overrides ([config/settings_manager.py](../../../config/settings_manager.py))

`SettingsManager` merges `DEFAULT_SETTINGS` with `config/settings.json`,
additively — new keys are safe to introduce (`_merge_defaults`). Current
routing keys: `scanner_provider="rubix"`, `dashboard_provider="rubix"`,
`forward_testing_provider="rubix"`, `backtest_provider="yahoo"`,
`fallback_provider="yahoo"`. A `tradingview_*` block will be added the same
way (Phase K) — additive, all defaults `false`/`"none"`.

## 8. EGX symbol mapping ([providers/symbol_mapping.py](../../../providers/symbol_mapping.py))

Engine tickers are Yahoo-style (`COMI.CA`). `to_egx_code()` strips `.CA`/`.EGY`
suffixes to the bare EGX code (`COMI`). TradingView's public EGX tickers use
the `EGX:` exchange prefix (e.g. `EGX:COMI`) per its symbol-search convention.
No official TradingView symbol-mapping table is bundled with this repo, so
Phase C's map must be **verified per symbol**, not assumed 1:1.

---

## Conclusion for downstream phases

A TradingView provider can conform to the exact same `MarketDataProvider` /
`normalize_history` contract as Yahoo and Rubix. Nothing in the engine needs
to change to *support* it in principle. What is genuinely uncertain — and
therefore gates every later phase — is **how TradingView data can be obtained
at all without violating the compliance rule** (no scraping, no private
WebSocket, no session/cookie extraction). That question is Phase B.
