# ORB + First Pullback — Data Readiness

Audit date: 2026-08-02

Method: read-only SQLite URI against `F:\EGX_AI_Trader\data\rubix_live_market.db`; schema and aggregate counts only. No quote payload was exported and no database was written.

## Readiness verdict

| Capability | Verdict | Reason |
|---|---|---|
| EODHD D-1 universe/context | Ready with existing controls | 241 ACTIVE symbols and completed-session/frozen-watchlist infrastructure exist |
| Explicit Rubix mapping | Partial | 225 of 241 active symbols have verified subscriptions; 16 are explicitly unmapped |
| Live price and quote freshness | Available | Quote timestamps, receive timestamps, last price and collector health fields exist |
| Best bid/ask and spread | Available per observed row | `bid` and `ask` exist; still validate positive/unlocked/current values per evaluation |
| 1m OHLCV | Available but historically uneven | Stored 1m bars exist; past session density varies materially |
| Deterministic 5m bars | Not yet implemented | Can be derived from valid completed 1m bars with strict gaps/session rules |
| Turnover | Unavailable | Neither quote nor candle schema stores turnover/value traded |
| Trade count | Unavailable | Candle `updates` is collector update count, not verified trades |
| True VWAP | Not possible from present schema | No trade-price × trade-size stream, turnover, or VWAP numerator |
| OHLCV weighted-price proxy | Technically calculable, not true VWAP | Existing code uses typical price × bar volume; it must not be labelled true VWAP |
| True time-of-day RVOL | Not research-ready | Too few and too uneven completed intraday sessions for a stable per-symbol median baseline |
| Historical ORB backtest | Insufficient | Only a short, heterogeneous July/August window; no credible performance claim is allowed |

Strategy validation status must begin as `INSUFFICIENT_INTRADAY_HISTORY` in Shadow/Paper/Research Only.

## Authoritative universe and mapping

Read-only execution of the repository loaders produced:

| Measure | Count |
|---|---:|
| Universe records, including inactive/history | 317 |
| ACTIVE EODHD universe | 241 |
| Verified Rubix subscriptions for active symbols | 225 |
| Active symbols without verified Rubix mapping | 16 |
| Invalid universe requests | 0 |
| Subscription batches at default batch size | 3 |

The ORB pre-session selector starts from exactly the 241 ACTIVE records. It must not guess mappings for the 16 unmapped records and must emit `RUBIX_MAPPING_UNAVAILABLE`. Rubix rows for 265 distinct stored tickers include non-active/history symbols and are not an authority for current eligibility.

## Rubix tables and fields actually present

### `quotes`

| Field | SQLite type | Meaning/capability |
|---|---|---|
| `id` | INTEGER PK | Collector row identity |
| `ticker` | TEXT NOT NULL | Suffix-free Rubix storage ticker |
| `last_price` | REAL | Latest observed price |
| `bid` | REAL | Best bid when valid |
| `ask` | REAL | Best ask when valid |
| `volume` | REAL | Cumulative observed volume; not per-trade size |
| `market_timestamp` | TEXT NOT NULL | Market/feed timestamp |
| `received_at` | TEXT NOT NULL | Collector receive timestamp |
| `exchange` | TEXT | Exchange identifier |
| `sequence` | INTEGER | Source sequence when supplied |
| `change_percent` | REAL | Feed change percentage |
| `has_feed_timestamp` | INTEGER NOT NULL | Timestamp provenance flag |

Not present: bid size, ask size, turnover, trade size, trade id, transaction count, VWAP, VWAP numerator.

### `candles_1m`

| Field | SQLite type | Meaning/capability |
|---|---|---|
| `ticker` | TEXT NOT NULL, PK part | Storage ticker |
| `minute` | TEXT NOT NULL, PK part | UTC minute timestamp |
| `open`, `high`, `low`, `close` | REAL NOT NULL | One-minute OHLC |
| `volume` | REAL NOT NULL | Stored minute volume |
| `updates` | INTEGER NOT NULL | Collector updates used to form the candle, not verified trades |

Not present: turnover, trade count, first/last event ids, VWAP numerator, bar VWAP, data-quality flag, finalized flag. Completion therefore has to be inferred from aware wall clock plus session boundary, never from a `finalized` column.

### `feed_metrics`

`id`, `observed_at`, `event`, `ticker`, `value`, and `detail` exist. This can support collector-health evidence, but `detail` is free-form telemetry, not market data and must not be interpreted as volume, spread, VWAP, or transactions.

## Aggregate physical history observed

Raw database snapshot:

| Measure | Value |
|---|---:|
| `candles_1m` rows | 241,542 |
| Distinct candle tickers | 265 |
| Distinct UTC date labels | 14 |
| Candle range | 2026-07-01 00:00 UTC to 2026-08-02 11:30 UTC |
| Null/negative candle volume rows | 0 / 0 |
| `quotes` rows | 2,844,581 |
| Distinct quote tickers | 265 |
| Distinct quote UTC date labels | 14 |
| Quote range | 2026-07-01 00:00 UTC to 2026-08-02 11:30:23 UTC |
| Null price / volume / bid-or-ask rows | 0 / 0 / 0 at table aggregate |

The 2026-07-01 rows are a one-symbol sentinel/diagnostic timestamp, not a usable session. Filtering strictly to the continuous Cairo window (`07:00–11:15 UTC`, equivalent to `10:00–14:15 Cairo` for the audited dates) found 13 date labels with at least one candle:

| Session date | Continuous 1m rows | Symbols | Min / avg / max bars per observed symbol |
|---|---:|---:|---:|
| 2026-07-14 | 479 | 244 | 1 / 1.96 / 3 |
| 2026-07-15 | 20 | 20 | 1 / 1.00 / 1 |
| 2026-07-16 | 14,396 | 245 | 1 / 58.76 / 77 |
| 2026-07-19 | 15,873 | 245 | 1 / 64.79 / 79 |
| 2026-07-20 | 4,651 | 265 | 1 / 17.55 / 24 |
| 2026-07-21 | 12,956 | 245 | 1 / 52.88 / 67 |
| 2026-07-22 | 14,234 | 265 | 1 / 53.71 / 71 |
| 2026-07-26 | 14,372 | 245 | 1 / 58.66 / 70 |
| 2026-07-27 | 12,902 | 265 | 1 / 48.69 / 63 |
| 2026-07-28 | 13,459 | 244 | 1 / 55.16 / 67 |
| 2026-07-29 | 14,158 | 263 | 1 / 53.83 / 72 |
| 2026-07-30 | 13,116 | 243 | 1 / 53.98 / 65 |
| 2026-08-02 | 49,593 | 224 | 1 / 221.40 / 255 |

Interpretation:

- The store has 13 continuous-window date labels beyond the sentinel, not 13 uniformly complete sessions.
- July 14, July 15 and July 20 are plainly partial.
- July 16–30 remain sparse relative to the 255 continuous-session minute slots; sparse bars may reflect update-driven emission, halts/no trades, or collection gaps and must be distinguished before gap filling.
- August 2 reaches 255 bars for at least one symbol and has much higher mean density, but one dense session does not establish a historical baseline.
- Raw rows outside the continuous interval include pre-open, auction/post-close, diagnostics or delayed collection and must never enter ORB calculations.

The current database is suitable for live Shadow observation and deterministic replay of specifically validated sessions. It is not yet sufficient for a broad historical performance claim.

## Volume semantics and quality

`quotes.volume` is treated elsewhere in the repository as cumulative quote volume. `candles_1m.volume` is summed bar volume. `providers/rubix_daily_aggregator.py` explicitly notes historical bar-volume undercounting and compares summed candles with cumulative quote volume; its existing default reliability gate is 90%.

In the audited continuous-window aggregates, early sessions showed severe or inconsistent ratios, while later dates commonly reached 1.0 for symbols with nonzero cumulative volume. This indicates a collector/schema behavior change or changing completeness and means the data cannot be assumed stationary across the whole archive. Some ratios were null where cumulative volume was zero; those are unavailable, not zero RVOL.

Before Phase 5, Shadow collection must persist per-symbol/session:

- expected versus observed minute slots;
- first/last market and receive timestamps;
- cumulative quote volume monotonicity/reset checks;
- summed bar volume versus cumulative volume reliability;
- valid update count and zero/no-trade intervals;
- mapping/config/schema versions.

No missing volume may be fabricated. A zero is valid only when the source proves a genuine zero-volume completed interval; absence of a row is not automatically zero.

## True VWAP verdict

**True VWAP is not currently derivable.** The mathematical requirement is `sum(executed_price × executed_size) / sum(executed_size)`. The present database has snapshot last price plus cumulative volume and minute OHLC plus stored volume, but no executed-trade stream, turnover, or price-volume numerator.

The existing `_minute_vwap` calculation in `scalping_expected_range/live_readiness.py` computes:

```text
sum(((high + low + close) / 3) × minute_volume) / sum(minute_volume)
```

That is an OHLCV weighted typical-price proxy. It is deterministic but not true VWAP. The ORB engine must expose `TRUE_VWAP_INPUTS_UNAVAILABLE` unless the collector later supplies a verified turnover/VWAP numerator or deduplicated trade prints. It must not silently rename the proxy as VWAP.

If a later review explicitly approves the proxy for research context, it must have a separate field and label such as `OHLCV_WEIGHTED_PRICE_PROXY`, a source-quality flag, and no substitution for a required true-VWAP gate.

## Time-of-day RVOL verdict

The desired metric is:

```text
current same-session cumulative volume through minute T
-------------------------------------------------------
median historical same-session cumulative volume through minute T
```

The live numerator can potentially use validated same-session Rubix cumulative volume. The denominator is **not ready** because the archive has too few heterogeneous sessions and insufficient per-symbol/minute completeness.

Phase 2 configuration should default to:

- `volume_evidence_mode = TRUE_TIME_OF_DAY_RVOL_ONLY`
- `minimum_rvol_history_sessions = 20` completed, quality-approved sessions per symbol/time bucket (a design minimum, not a profitability threshold)
- `allow_provisional_volume_proxy = false`

Until the minimum is met, return `INSUFFICIENT_INTRADAY_HISTORY`, expose the actual sample count, and label volume state `UNAVAILABLE`. Do not divide by full-day EODHD average and call it time-of-day RVOL. A provisional proxy can be added only after explicit approval and must remain `PROVISIONAL_VOLUME_PROXY`, never `TRUE_TIME_OF_DAY_RVOL`.

## 5-minute bar feasibility

5m OHLCV can be aggregated from completed valid 1m records, but only after Phase 2 defines:

- half-open aligned Cairo buckets;
- exact handling for absent minute slots versus verified zero-trade minutes;
- ordered timestamps and duplicate/conflict rejection;
- minimum coverage per 5m bucket;
- exclusion of a partially completed bucket;
- session reset and auction exclusion;
- propagation of volume capability/quality rather than invented values.

A 5m bucket with insufficient required input must be unavailable; it cannot be forward-filled and used as momentum or reclaim confirmation.

## Fail-closed requirement matrix

| Requirement | Missing/invalid result |
|---|---|
| Verified Rubix mapping | `RUBIX_MAPPING_UNAVAILABLE` |
| Healthy collector/current quote | `STALE_LIVE_DATA` or `COLLECTOR_UNHEALTHY` |
| Positive current bid/ask for required spread gate | `SPREAD_UNAVAILABLE` |
| Spread above limit | `SPREAD_TOO_WIDE` |
| Sufficient update evidence | `INSUFFICIENT_OBSERVED_UPDATES` |
| Valid 15-minute OR inputs | `OPENING_RANGE_NOT_COMPLETE` |
| Required live turnover | `TURNOVER_UNAVAILABLE` |
| Required true VWAP | `TRUE_VWAP_INPUTS_UNAVAILABLE` |
| Required true TOD RVOL | `INSUFFICIENT_INTRADAY_HISTORY` or `RVOL_UNAVAILABLE` |
| Trade count | `TRADE_COUNT_UNAVAILABLE` |
| Completed confirmation bar | `PARTIAL_BAR_NOT_COMPLETE` |

## Data collection required before calibration

1. Continue external Rubix collection without changing the collector database contract in this task.
2. Build a separate read-only-derived ORB research store with quality metadata and source fingerprints.
3. Accumulate at least the configured per-symbol/minute sample minimum, then review coverage distribution rather than just calendar count.
4. Establish volume semantics/version boundaries and exclude incompatible sessions from baselines.
5. Obtain verified turnover or trade-level price/size inputs before enabling a true VWAP gate.
6. Backtest only timestamped intraday bars with conservative ordering, fees, spread/slippage, partial exits and chronological equity.

No daily OHLC reconstruction can determine breakout/pullback/stop/target order and therefore cannot validate this strategy.
