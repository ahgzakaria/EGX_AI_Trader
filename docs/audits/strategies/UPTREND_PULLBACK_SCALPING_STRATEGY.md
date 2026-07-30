# UPTREND_PULLBACK_SCALPING — historical selection core

Strategy identity: **`UPTREND_PULLBACK_SCALPING_V1`**
Package: `scalping_uptrend_pullback/`
Status: research-only core. No UI, no orders, no paper execution, no Rubix
dependency.

This is the second scalping strategy. It is completely separate from **Stable
Range-Bound Scalping** (`scalping_expected_range/`) and from the Swing/Daily
engine: separate package, separate typed config, separate frozen-watchlist
database, separate strategy identity. Nothing is shared but the EODHD provider
modules.

## What it selects

A short-term uptrend that has pulled back onto a support zone — not a breakout,
not a range fade. The trend definition is deliberately fast:

- `EMA5` and `EMA10` only. `ShortTermTrendConfig` refuses a slow EMA above 15
  periods, so an EMA20/EMA50-style trend definition cannot be configured in.
- Slopes are percent-of-price per session over `slope_lookback_sessions`.
- Trend *age* is counted over a wide window (`trend_age_window`, default 30) so
  a pullback deep enough to dip EMA5 under EMA10 does not erase the fact that
  the trend exists.

## Data policy

| Rule | Where it is enforced |
| --- | --- |
| Completed EODHD Daily OHLCV only | `analyze_uptrend_pullback` rejects any frame whose provenance is not `EODHD_DAILY` → `PROVENANCE_REJECTED` |
| D-1 cutoff or earlier | `data_cutoff` is mandatory; `_clean_completed_daily` drops every later row |
| No current incomplete candle | `Complete`/`IsComplete` flags are honoured and dropped rows never reach scoring |
| No Yahoo | The loader calls EODHD directly and sets `yahoo_network_used: False`; there is no fallback path |
| No Rubix in historical ranking | The selection API accepts no live quote, spread, RVOL or movement field |
| Frozen before the session | `FrozenUptrendWatchlistService.prepare_for_session` claims one deterministic identity, then publishes atomically |

Rubix stays read-only and is not required by this core.

## Uptrend definition (all configurable)

1. `EMA5 > EMA10`
2. `EMA5` slope ≥ `minimum_fast_slope_percent_per_session`
3. `EMA10` slope ≥ `minimum_slow_slope_percent_per_session`
4. Last close not structurally below `EMA10` (outside the tolerance band), and
   at most `maximum_closes_below_slow_ema` recent closes breaching it
5. `EMA5 > EMA10` for at least `minimum_trend_sessions` of the last
   `trend_age_window` sessions
6. Recent confirmed swing highs and swing lows are not *clearly* descending.
   Absence of confirmed pivots is disclosed
   (`INSUFFICIENT_CONFIRMED_SWING_PIVOTS`) but is never treated as evidence of
   a descending structure
7. Liquidity and history minimums are met

All of it is arithmetic on completed bars. No AI-generated number enters the
selector.

## Support zone

The zone is confluence between:

- the `EMA10` area (band of `ema_zone_half_width_percent`),
- recent confirmed daily swing lows (pivot radius, both sides required),
- the highest prior consolidation shelf floor, marked `PRIOR_BREAKOUT_LEVEL`
  when a later close proved the breakout, otherwise
  `PRIOR_CONSOLIDATION_LEVEL`.

Levels are chained into clusters while neighbouring levels stay within
`cluster_tolerance_percent` and the whole zone stays inside
`maximum_zone_width_percent`. The governing cluster is the highest one at or
below the last close; if price has fallen under every level, the lowest cluster
is used so the broken-support state can be reported instead of silently
claiming no support exists.

Each zone exposes lower / upper / centre, source types, contributing levels,
strength (0-100), touch count, reaction frequency, and an invalidation level.

**Honest naming.** Daily OHLC cannot reveal whether the session high or low came
first, so nothing claims an intraday reaction at the level:

- `support_touch_proxy_count`
- `support_reaction_proxy_count` / `support_reaction_proxy_frequency`
- `first_touch_order_available = False` (a member row with this set true is
  rejected by the watchlist writer and by the SQL schema)

## Typed states

Exactly one state per evaluated symbol, resolved in this order:

```
DATA_UNAVAILABLE → PROVENANCE_REJECTED → INSUFFICIENT_HISTORY
→ INSUFFICIENT_LIQUIDITY → EMA_ALIGNMENT_FAILED → EMA5_SLOPE_FAILED
→ EMA10_SLOPE_FAILED → TREND_STRUCTURE_FAILED → SUPPORT_NOT_CONFIRMED
→ SUPPORT_BROKEN → UPTREND_PULLBACK_TOO_DEEP → INSUFFICIENT_UPSIDE
→ UPTREND_NEAR_SUPPORT | UPTREND_WAIT_FOR_PULLBACK | UPTREND_EXTENDED_NO_CHASE
```

Proximity is the distance from the last completed close to the nearest edge of
the zone, in percent of that edge. A close inside the zone is exactly `0.0`; a
close below the zone is negative.

| Band (configurable) | State |
| --- | --- |
| `0%` – `near_support_maximum_percent` (3%) | `UPTREND_NEAR_SUPPORT` |
| `>3%` – `wait_for_pullback_maximum_percent` (6%) | `UPTREND_WAIT_FOR_PULLBACK` |
| `>6%` | `UPTREND_EXTENDED_NO_CHASE` |

A close below `invalidation_level` (zone lower minus
`invalidation_buffer_percent`) rejects the candidate.

Only states listed in `eligible_states` (default: `UPTREND_NEAR_SUPPORT`) are
eligible.

## Practical-upside gate

Sitting on support is not enough. A candidate must also have somewhere to go:

- Available upside is measured from the latest completed close to the **first
  valid historical research target above it** — the nearest confirmed daily
  swing high, falling back to the lookback-window high.
- `UpsideRiskConfig.minimum_upside_percent` (default **2.5%**) is a hard
  eligibility gate. Below it the symbol becomes `INSUFFICIENT_UPSIDE`.
- A target at or below the close yields no upside at all and is rejected the
  same way. **No farther target is ever fabricated to clear the gate** — the
  observed level is reported as-is, and the symbol simply fails.
- The gate compares the same rounded percentage that is reported, so a
  candidate is never rejected on a figure the caller cannot see. `2.5%` exactly
  passes; `2.4999%` does not.
- `INSUFFICIENT_UPSIDE` deliberately precedes all three proximity states, so a
  thin-upside symbol is reported as such and is never silently reclassified as
  `UPTREND_WAIT_FOR_PULLBACK`.

Rejected symbols keep their score, rank and full diagnostics in the snapshot;
they are only excluded from `candidate_symbols` and from the published frozen
list. `_validate_member_rows` additionally refuses to publish any member whose
research target is missing or not above its own close.

## Score

Deterministic 0-100 *Uptrend Pullback Scalping Score*:

| Weight | Component |
| --- | --- |
| 30% | short-term trend quality (EMA spread band, both slopes, persistence, discipline, higher-low ratio) |
| 30% | support confluence and proximity |
| 20% | pullback quality (depth band, orderliness, volume contraction) |
| 10% | liquidity / executability |
| 10% | available upside versus invalidation risk |

The score ranks; it never overrides a hard gate. An extended symbol can carry a
higher trend-quality score than the selected candidate and still be ineligible,
and a symbol scoring above 60 is still rejected outright when its upside is
below `minimum_upside_percent`.

## Frozen watchlist

`data/uptrend_pullback_watchlists.db` (override with
`UPTREND_PULLBACK_WATCHLIST_DB`). Its own schema, tables prefixed
`uptrend_watchlist_*`, and `strategy_identity` on every header and member row —
enforced by a SQL trigger, so a Range-Bound snapshot can never be read as an
Uptrend-Pullback one.

- Identity key = strategy identity + schema version + target session + D-1
  cutoff + provider + lookback + minimum sessions + metric version + config
  version + source fingerprint + universe fingerprint + candidate limit.
- The cutoff must be strictly before the target session.
- Headers start `GENERATING`; published headers and all member rows are
  immutable (`RAISE(ABORT)` triggers on UPDATE and DELETE).
- Re-preparing the same session with the same input reuses the same frozen
  record rather than rewriting it.
- **Top 20 is a maximum, not a quota.** When four symbols qualify, four are
  published.

## Not in this core

Live Rubix confirmation, any UI surface, paper recording, and backtesting are
out of scope here and are not stubbed. The dashboard is owned by a separate
branch; nothing in this package imports Streamlit or touches `app.py`,
`dashboard/`, or shared CSS.

## Tests

- `tests/test_uptrend_pullback_selection.py` — every typed state, each gate,
  configurability of the bands, cutoff and provenance policy, determinism,
  ranking, and the "top 20 is a maximum" rule.
- `tests/test_uptrend_pullback_frozen_watchlist.py` — identity namespacing,
  D-1 cutoff, publication contract, SQL-enforced immutability, snapshot reuse,
  and read-side validation.
