# Scalping Live Entry Readiness — Phase 3B

**Status:** implemented and validated for deterministic research-only decision
support

**Branch:** `fix/scalping-historical-volatility-selection`

**Live metric version:** `LIVE_ENTRY_READINESS_V1`

**Live config version:** `LIVE_ENTRY_READINESS_CONFIG_V1`

**Validated watchlist:** `FHW-384cf17b13194e43370bfabc`

**Validated target / historical cutoff:** 2026-07-28 / 2026-07-27

## Scope and stop condition

Phase 3B adds a read-only Live Entry Readiness Engine for the displayed members
of an immutable READY historical watchlist. It does not select securities,
change historical rank or score, write a live signal history, record a paper
trade, mutate a portfolio, connect to a broker, or submit an order.

The engine is long-entry research only. It returns explicit readiness and
blocking states rather than `BUY` or `NO BUY`. Lower-excursion history is used
only for risk disclosure and long-thesis invalidation context.

Phase 3B stops before persistent paper-trade recording, notifications, Archive
Format changes, production execution and automatic execution.

## Three-layer separation

### A. Frozen historical selection

- source: EODHD Daily;
- maximum input for target D: D-1 or earlier;
- 60-session primary profile and 30-session recent confirmation;
- immutable candidate membership, rank, score and explanation;
- complete 225-symbol validated ranking population;
- separate configurable Top-N.

The Phase 3B module does not import or call the EODHD loader.

### B. Live entry readiness

- source: read-only Rubix SQLite;
- scope: displayed frozen Top-N only;
- mutable quote, continuous-session range, opening range, VWAP proxy, spread,
  liquidity, no-chase and trigger state;
- no persistence and no writer;
- deterministic result at a fixed watchlist, database snapshot and evaluation
  timestamp.

### C. Historical intraday enrichment

Status remains `NOT_READY`. Seven structurally useful replay sessions do not
satisfy the accepted 20-valid-session enrichment gate. No Phase 3B weight,
threshold or selector feature uses layer C.

## Strict universe disclosure

The dashboard states:

| Dimension | Current operational value |
|---|---:|
| Historical universe | **225 validated symbols** |
| Endpoint coverage | **241 / 265** |
| Watchlist ranking population | **225 validated symbols** |
| Endpoint-unavailable, inactive or non-equity remainder | **24** |

The 16 direct-endpoint additions are not silently included in the operational
ranking population. They require the same history, provenance, volume-quality
and selector validation first.

## Read-only Rubix field audit

The configured store was queried through SQLite `mode=ro` with
`PRAGMA query_only=ON`. No table, column, index, collector, launcher,
supervisor or feed process was changed.

Configured database:

```text
D:\EGX_AI_Trader\data\rubix_live_market.db
```

Observed schema:

```text
quotes:
id, ticker, last_price, bid, ask, volume, market_timestamp, received_at,
exchange, sequence, change_percent, has_feed_timestamp

candles_1m:
ticker, minute, open, high, low, close, volume, updates
```

The only quote index is `(ticker, market_timestamp)`. The minute table's
primary key is `(ticker, minute)`. Phase 3B uses both directly and never wraps
`ticker` in `UPPER()`.

### Field classifications

| Requested field | Classification | Evidence and Phase 3B behavior |
|---|---|---|
| Last price | `AVAILABLE_AND_VALIDATED` | `quotes.last_price`; positive-row validation is mandatory. On 2026-07-28 continuous data, 156,388 / 157,097 rows were positive. |
| Previous close | `DERIVABLE` | No explicit column. It can be inferred from positive last price plus `change_percent`; this is disclosed and is not a trigger gate. |
| Session open | `DERIVABLE` | First positive continuous minute candle, accepted only when it begins no later than 10:02 Cairo. Otherwise readiness fails closed. |
| Session high / low | `DERIVABLE` | Maximum/minimum of validated minute candles strictly before 14:15. Sparse observation is disclosed. |
| Cumulative volume | `AVAILABLE_AND_VALIDATED` | `quotes.volume`; all 157,097 observed continuous rows were nonnegative. It is not summed across quotes. |
| Cumulative traded value | `UNAVAILABLE` | No quote or candle field. Result remains `None`; no value is fabricated. |
| Trade count | `UNAVAILABLE` | `updates` is collector updates per minute, not exchange trade count. Result remains `None`. |
| Best bid / ask | `AVAILABLE_AND_VALIDATED` per row | `quotes.bid` / `quotes.ask`; 156,026 / 157,097 continuous rows had positive non-crossed quotes. Missing spread is optional; crossed/inverted values are `UNTRUSTWORTHY` and block readiness. |
| Bid / ask quantity | `UNAVAILABLE` | No schema columns. |
| Quote timestamp | `AVAILABLE_AND_VALIDATED` | The feed quote timestamp is `market_timestamp`; there is no distinct third quote-time field. |
| Market timestamp | `AVAILABLE_AND_VALIDATED` | Present on every audited quote; used for market age and chronological cutoff. |
| Receive timestamp | `AVAILABLE_AND_VALIDATED` | Present on every audited quote; replay also requires `received_at <= evaluated_at` to prevent receive-time lookahead. |
| Minute candles | `AVAILABLE_BUT_SPARSE` | Valid OHLC rows exist, but connection gaps are market-wide. 13,303 / 13,459 candles on 2026-07-28 continuous data had positive OHLC. |
| Candle volume | `AVAILABLE_AND_VALIDATED` | `candles_1m.volume` is per-minute volume, not cumulative; all 13,459 audited rows were nonnegative. |
| Candle traded value | `UNAVAILABLE` | No column. |
| VWAP | `DERIVABLE` with sparse-data disclosure | Volume-weighted minute typical price can be derived from validated OHLCV. No exact trade-price/traded-value VWAP is available. If usable volume is absent, VWAP is unavailable and its score weight is reallocated. |

Latest after-hours repeated snapshots were found with crossed or implausible
bid/ask values. The engine avoids that source-quality failure by capping all
quote and candle input at 14:15 and validating the bid/ask pair for every
evaluation.

## Seven-session structural evidence

Continuous-session rows are filtered to 10:00–14:15 Cairo. These sessions are
used only for field validation, functional replay and latency measurement:

| Session | Continuous minute rows | Symbols observed |
|---|---:|---:|
| 2026-07-19 | 15,873 | 245 |
| 2026-07-20 | 4,651 | 265 |
| 2026-07-21 | 12,956 | 245 |
| 2026-07-22 | 14,234 | 265 |
| 2026-07-26 | 14,372 | 245 |
| 2026-07-27 | 12,902 | 265 |
| 2026-07-28 | 13,459 | 244 |

These counts do not establish full-session per-symbol coverage. Earlier
connection-uptime audits remain controlling: the feed has long shared gaps.
The 20-session intraday enrichment gate therefore remains unchanged.

## Immutable result model

`LiveEntryReadinessResult` is a frozen dataclass. It contains:

- watchlist, target session, symbol, frozen historical rank, evaluated time,
  Rubix cutoff and live versions;
- copied historical range, zone, stability and liquidity references;
- current quote, continuous high/low/range, change from open, opening range,
  derived VWAP, spread, quote age and available cumulative volume;
- typed hard-gate results and trigger results;
- optional 0–100 readiness score;
- live state, range-consumption proxy, no-chase reason, entry zone, target,
  stop, expected costs/net reward, invalidation and deterministic explanation;
- explicit data-quality, opening-range and VWAP states.

It has no update, insert, order, quantity, position or portfolio method. It is
never written into `watchlist_headers` or `watchlist_members`.

## Session phases

All phase decisions use Africa/Cairo and the shared EGX holiday calendar:

| Phase | Cairo interval | Entry behavior |
|---|---|---|
| `PRE_OPEN` | before 10:00 | `PRE_OPEN_WAIT`; no Rubix query is required |
| `CONTINUOUS_TRADING` | 10:00 through before 14:15 | hard gates and live structure may be evaluated |
| `CLOSING_AUCTION` | 14:15 through before 14:25 | `CLOSING_AUCTION_NO_NEW_ENTRY` |
| `POST_CLOSE` | 14:25 onward | `SESSION_CLOSED` |

Friday, Saturday and confirmed holidays are closed.

Every minute query is bounded by:

```text
10:00 Cairo <= minute < min(completed evaluation minute, 14:15 Cairo)
```

Every quote query is also capped before 14:15 and requires:

```text
market_timestamp < continuous cutoff
received_at <= evaluated_at
```

The current continuous high, low and range therefore freeze at 14:15. Auction
minutes cannot alter them. The official auction close, normally established
around 14:23–14:25, is never treated as a new scalping entry.

## Hard gates

A numerical score can lead to `ENTRY_READY_RESEARCH_ONLY` only after:

1. a READY frozen watchlist exists;
2. the symbol is a displayed frozen candidate;
3. the target date matches;
4. the session is continuous trading;
5. a positive Rubix last price exists;
6. market-timestamp age is at most 120 seconds;
7. a trustworthy session-open bar begins by 10:02;
8. validated continuous high/low exist;
9. at least 12 continuous bars and 8 of the 15 opening minutes are present;
10. available bid/ask is non-crossed and spread is at most 0.60%;
11. cumulative volume is at least 100,000 shares;
12. the long opportunity is not invalidated;
13. the move is not extended;
14. the projected target is not outside the typical upper historical zone.

Missing data never becomes zero readiness. It returns a typed blocker.

## Opening-range and trigger rules

The configured opening range is the first 15 minutes. Before 10:15, the state
is `OPENING_RANGE_FORMING`.

Afterward:

- opening high/low come only from validated continuous minute candles;
- a candle close must exceed opening high by 0.05% for a breakout observation;
- two qualifying closes are required for confirmation;
- a later candle must return within 0.20% of the opening high and hold within
  the same tolerance for a retest;
- quote-only movement cannot claim a breakout;
- one breakout candle is `BREAKOUT_UNCONFIRMED`;
- a confirmed breakout without retest is
  `PULLBACK_CONFIRMATION_REQUIRED`;
- no breakout near the boundary is `ENTRY_TRIGGER_FORMING`;
- no breakout away from the boundary is
  `HISTORICAL_CANDIDATE_WAITING`.

## VWAP methodology

When positive validated minute volume exists:

```text
minute typical price = (high + low + close) / 3
derived session VWAP =
  sum(minute typical price × minute volume) / sum(minute volume)
```

The state is `VWAP_DERIVED_FROM_MINUTE_OHLCV`, not exchange-exact VWAP.
Sparse observation remains visible in data quality.

When usable volume does not exist:

- state: `VWAP_UNAVAILABLE`;
- no simple moving average is substituted;
- the VWAP component is removed;
- remaining component weights are normalized for that evaluation.

## Readiness score

Weights are centralized and validated to total exactly 100%:

| Component | Weight |
|---|---:|
| Trigger and market structure | 35% |
| VWAP/retest quality | 15% |
| Indicative remaining movement | 25% |
| Liquidity/executability | 15% |
| Quote freshness/data quality | 10% |

Historical score is not included in this live score. It selected the candidate
already and remains visible separately.

Component outline:

- structure: 100 for confirmed breakout/retest, 70 for confirmed breakout,
  45 for unconfirmed breakout, 25 while waiting;
- VWAP: maximum when price is modestly above VWAP, with continuous distance
  penalty;
- remaining movement: `100 × max(0, 1 - range consumption)`;
- liquidity: continuous scaling from the minimum volume gate toward five times
  the threshold;
- freshness: `100 × max(0, 1 - quote age / 120 seconds)`.

The weighted result is rounded to two decimals. The initial research readiness
threshold is 70. A qualifying state is named
`ENTRY_READY_RESEARCH_ONLY`, never `BUY`.

## Historical range-consumption proxy

```text
historical full-session range consumption =
  current continuous-session range / historical median Daily range
```

Labels:

- below 75%: movement capacity not yet exhausted;
- 75% through below 100%: indicative movement limited;
- 100% or more: historical full-session range consumed.

This is not exact remaining continuous-session capacity and is not a forecast.
EODHD Daily candles may contain closing-auction effects, while the live
numerator deliberately excludes the auction. The engine never assumes a stock
must complete its median historical range.

## No-chase rule

`MOVE_EXTENDED_DO_NOT_CHASE` is a hard safety state. It is returned when any
severe condition is met:

```text
max(0, change from open) / typical upper excursion >= 0.90

or

continuous range / historical median Daily range >= 1.00

or

distance above derived VWAP > 1.50%
```

Safety classification precedes the breakout completeness gate. Sparse
structure can never create readiness, but it does not hide a clearly exhausted
move.

The real replay example was RREI at 10:45 Cairo:

- change from open: +4.805%;
- historical full-session range consumption: 190.9%;
- quote age: 89 seconds;
- state: `MOVE_EXTENDED_DO_NOT_CHASE`.

## Target, stop and cost feasibility

Research defaults:

- gross target: +2%;
- gross stop: -2%;
- estimated fixed fee allowance: 0.10%;
- observed valid spread is added to cost allowance.

For a ready entry:

```text
target = entry × 1.02
stop = entry × 0.98
expected net reward % = 2.00% - spread % - 0.10%
```

The projected target must not exceed:

```text
session open ×
  (1 + typical upper excursion × 1.10 / 100)
```

Failure returns `TARGET_OUTSIDE_TYPICAL_ZONE`. No order is produced.

## Batch query design

One refresh:

1. opens one SQLite connection with `mode=ro`, `query_only=ON` and a bounded
   2-second busy timeout;
2. begins one consistent read transaction;
3. runs one scanner-level quote statement containing one bounded
   `(ticker, market_timestamp)` index probe per Top-N ticker;
4. runs one indexed `(ticker, minute)` candle query for the Top-N;
5. rolls the read transaction back and closes the connection.

There is no connection per field or symbol, process pool, provider worker,
database write or collector schema/index change.

### Controlled performance

The optimized chronological replay performed 51 evaluations:

| Metric | Result |
|---|---:|
| Symbols per refresh | 20 |
| Connections per active refresh | 1 |
| Queries per active refresh | 2 |
| Median total evaluation | **15.319 ms** |
| Maximum total evaluation | **25.245 ms** |
| Fixed 14:08 replay under `tracemalloc` | 47.419 ms query / 60.421 ms total |
| Peak traced Python allocation | 714,459 bytes |

The trace-enabled fixed sample includes instrumentation overhead. The ordinary
51-point series is the responsiveness measurement.

## Controlled real replay

The EODHD cache-only watchlist build:

- used 225 validated symbols;
- made 447 cache reads and **zero live EODHD calls**;
- disclosed missing EOD documents for DEIN, MEGM and TRTO;
- reproduced 179 hard-eligible members;
- reproduced the accepted Top 20 and exact watchlist ID;
- wrote only an isolated feature-worktree research database;
- removed the database and report after evidence capture.

Monitored symbols:

```text
RAYA ACAMD EGTS CCRS ZMID OCDI PRCL AREH EGCH CCAP
VALU CRST RREI MASR NIPH AMER PHDC EEII GBCO POUL
```

Real chronological examples:

| Cairo time | Symbol | State | Evidence |
|---|---|---|---|
| 09:55 | RAYA | `PRE_OPEN_WAIT` | continuous session not started; zero DB queries |
| 10:10 | RAYA | `OPENING_RANGE_FORMING` | configured 15-minute window incomplete |
| 10:20 | RAYA | `LIVE_DATA_STALE` | market timestamp age 462 seconds |
| 10:30 | RAYA | `LIVE_DATA_UNAVAILABLE` | trustworthy 10:00 open unavailable |
| 10:30 | EGTS | `SPREAD_TOO_WIDE` | 0.842% versus 0.600% limit |
| 10:30 | VALU | `LIQUIDITY_INSUFFICIENT` | cumulative volume below gate |
| 10:45 | RREI | `MOVE_EXTENDED_DO_NOT_CHASE` | +4.805%; 190.9% range consumption |
| 14:20 | RAYA | `CLOSING_AUCTION_NO_NEW_ENTRY` | continuous range frozen at 14:15 |
| 14:30 | RAYA | `SESSION_CLOSED` | no new entry |

No real `ENTRY_READY_RESEARCH_ONLY` state was claimed. The sparse opening
structure and shared feed gaps correctly blocked it. Synthetic deterministic
fixtures validate:

- waiting;
- trigger forming;
- unconfirmed breakout;
- confirmed breakout awaiting pullback;
- entry-ready research only;
- extended;
- invalidated;
- target outside zone;
- wide/crossed spread;
- insufficient liquidity;
- stale/unavailable data.

Those fixtures prove state transitions and calculations, not profitability or
target hit rate. Thresholds were not optimized from seven sessions.

## No-lookahead and invariance

Validation proved:

- D live values never enter or alter D's frozen watchlist;
- historical member tuple and scores were unchanged after all live updates;
- the isolated watchlist database hash was unchanged across live refreshes;
- only displayed candidates were passed to the Rubix reader;
- a non-candidate +5% fixture remained absent;
- changing one candidate quote changed only that candidate result;
- repeated refresh and a second engine/reader observer produced identical
  results and Rubix cutoffs;
- all observers retained `FHW-384cf17b13194e43370bfabc`;
- EODHD cache reads during live refresh: **0**;
- EODHD live calls during the entire controlled validation: **0**;
- the Phase 3B module has no Yahoo provider or fallback;
- the Phase 3B module has no broker, paper-trade or portfolio dependency.

## Dashboard behavior

A separate panel appears immediately after the unchanged frozen historical
panel:

```text
LIVE ENTRY MONITOR
مراقبة جاهزية الدخول اللحظية
```

It uses a 15-second Streamlit fragment. Only the live fragment reruns; EODHD
selection, watchlist generation and persistence do not.

The panel displays:

- frozen historical rank;
- current live readiness score/state;
- current price and change from open;
- continuous range and historical full-session range consumption;
- opening-range and VWAP states;
- spread and quote age;
- available cumulative volume;
- honest `None` for unavailable traded value/trade count;
- entry zone, target, stop, invalidation and no-chase warning;
- data quality and evaluated timestamp;
- per-refresh connection, query and latency metrics.

Sorting supports readiness, state, freshness and frozen rank. It never changes
the historical table order.

Each symbol has deterministic historical-selection and live-assessment
reasons. No AI prose is generated.

If no READY watchlist exists, the panel displays `WATCHLIST_NOT_READY` and
points to the explicit historical workflow. It does not scan the market,
legacy ERS, Range Scanner or current movers.

Labels remain:

```text
Historical Rank: FROZEN
Live Readiness: CURRENT SESSION
Production: DISABLED
```

## Automated validation

| Suite | Result |
|---|---:|
| New live-readiness and UI-isolation tests | **43 passed** |
| Phase 3A + Phase 3B focused tests | **79 passed** |
| Required scoped regression set | **363 passed, 7 skipped** |
| Complete repository suite | **1529 passed, 7 skipped in 85.02s** |
| Streamlit spare-port smoke | health 200; root 200 |
| Final branch `git diff --check` | passed |

The seven skips are unchanged environmental cache prerequisites:

- six parametrized volume-correction cases for COMI, EAST, SWDY, KZPC, UNIP
  and ORAS lack the required local EODHD cache document;
- one KZPC event-window case lacks the required local cache document.

They predate Phase 3B and are not live-readiness skips.

The Streamlit smoke:

- used spare port 8767;
- used blank EODHD credentials;
- injected an operating-system temporary watchlist path;
- returned HTTP 200 / `ok` from `/_stcore/health`;
- returned HTTP 200 and 1,522 bytes from `/`;
- stopped only its verified process tree;
- left zero listeners on 8767;
- left the existing port 8501 listener and PID 26988 untouched.

Full-suite generated worktree caches, runtime locks and launcher log were
removed after validation.

## Data safety

The two pre-existing main-worktree modifications remain uncommitted and retain
their accepted hashes:

| File | Git blob | SHA-256 |
|---|---|---|
| `data/paper_trades.csv` | `323bc8b6c8547720d30e38ba3cab024ad55bb080` | `09d3b7f66f6e32a8bc7dd9fc0e21007899388cec2ab57976b3de38c90ddfa68d` |
| `docs/audits/strategies/SCALPING_MULTI_SESSION_VERDICT.md` | `640d9c0e98154d75e983916d6b04480e7cdc2b3d` | `37b4b58231937ce69da3360c1f8e1d03b784389f41fbd3630fc6b677b80a7eed` |

No real decision-support, paper, forward-testing, production archive,
broker/order or portfolio store was written.

## Remaining limitations

- Rubix capture remains connection-limited and minute structure is sparse.
- Opening-range readiness is therefore commonly unavailable after the window;
  the gate is not weakened to generate ready states.
- Cumulative traded value, exchange trade count and quote sizes are absent.
- Derived minute OHLCV VWAP is not exchange-exact and may cover only observed
  feed intervals.
- Daily historical zones may include auction effects; live continuous range
  intentionally does not.
- `change_percent` permits an indicative previous-close derivation but no
  explicit previous-close column exists.
- No persistent live signal history or cross-refresh state machine exists.
- No profitability, target-hit or threshold-optimization claim is supported by
  seven sessions.
- Multi-observer consistency assumes every observer reads the same Rubix
  SQLite file and fixed evaluation cutoff.

## Boundary before paper-trade recording

A future phase may consume `ENTRY_READY_RESEARCH_ONLY` results in a separate
paper-evidence workflow only after explicit approval. It must preserve:

- frozen historical candidate membership, rank and score;
- immutable watchlist tables;
- Phase 3B data-quality and blocking states;
- research-only versus production separation.

It must define its own idempotent signal identity, timing, fill assumptions,
cost model, outcome finalization and storage safety. Phase 3B creates none of
those records and stops at read-only live assessment.
