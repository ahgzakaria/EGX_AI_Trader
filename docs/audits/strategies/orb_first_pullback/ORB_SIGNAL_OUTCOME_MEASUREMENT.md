# ORB — Signal Outcome Measurement

**Research Only. Production execution disabled.** `ENTRY_READY_RESEARCH` is a
research candidate, never an order. This layer reads price history and writes
numbers to a separate database. It produces no signal, places no order, and
reaches no conclusion about the strategy.

## What was missing

Three live sessions produced 34 research candidates. The shadow evidence
records that each one reached `ENTRY_READY_RESEARCH`, at what time, on what
opening range, with what fingerprint — and then stops. Nothing anywhere says
what the price subsequently did.

That gap has a specific consequence: **no statement about whether these
signals were any good is currently supportable by anything except
impression.** This layer closes the measurement gap and nothing else. It
deliberately stops short of interpretation.

## What is measured

For each signal, forward from detection to the continuous close:

| Field | Meaning |
|---|---|
| `entry_price` | first collector quote at or after detection with `last_price > 0` |
| `entry_quote_market_timestamp_utc`, `entry_lag_seconds` | which quote, and how far after detection |
| `maximum_favorable_excursion_*` | best price reached, absolute / percent / seconds to reach |
| `maximum_adverse_excursion_*` | worst price reached, **signed** (`<= 0`) |
| `session_end_*` | last price inside the window, against entry |
| `observation_count`, `price_change_count` | how much evidence the window actually holds |
| `maximum_observation_gap_seconds`, `tail_gap_seconds` | where the window is thin |
| `measurement_quality` + `measurement_reasons_json` | whether the numbers can carry weight |
| `entry_ready_episode_count` | how many times the signal flickered in and out |
| `tp_sl_status` | always `TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED` |

The adverse excursion keeps its sign. An unsigned "drawdown of 2.91" reads as
a gain often enough to be worth the extra character.

## Four refusals

**No reconstructed stop or target.** `strategy_config.py` carries a stop and
target *rule* — `stop_atr_buffer 0.25`, `minimum_reward_risk 1.5`,
`target_1_r_multiple 1.0`, `target_2_r_multiple 2.0` — but the per-signal
values were never persisted for these 34 signals: `orb_research_setups`,
`orb_candidates`, `orb_breakouts`, `orb_pullbacks` and `orb_reclaims` are all
empty in every session database. Recomputing levels now from today's
configuration would produce numbers that look like evidence of what the
strategy would have done, and are not. Every outcome therefore carries
`TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED`, and **no R multiple is reported at
all** — there is no field for one.

**No substituted entry price.** If the collector holds no quote at or after
detection, the outcome is `NO_FUTURE_DATA`. The last prior quote is not
reused and the opening-range high is not used as a proxy.

**No interpolation across holes.** A window containing a gap is labelled
`PRICE_DATA_GAP`. The excursions are still reported — they are real
observations — but the label travels with them into the store.

**No Lane B signals.** Only the live `FOLLOW` lane is measured. A
reconstruction found a candidate the market never saw live; measuring it would
answer a different question than the one being asked.

## Contracts that the numbers depend on

**Chronology is `market_timestamp`.** `received_at` records when the collector
saw a row, not when the exchange printed it; ordering a price path by it would
be ordering by collection luck.

**The window ends at the continuous close, 14:15 Cairo.** The closing auction
runs to 14:25 and the collector keeps polling past it — 2026-08-06 alone holds
24,400 rows inside the auction window and 12,016 after it. An auction print is
a different price-formation mechanism, and none of these candidates could have
been managed through one.

*This boundary has a trap in it.* `market_timestamp` is stored as text in UTC
(`2026-08-06T11:15:00+00:00`), so a bound written as `14:15:00+03:00` compares
**lexically**, not chronologically: `'…T14:15:00+03:00'` sorts *above*
`'…T14:20:00+00:00'`, silently admitting auction rows ten minutes past the
boundary. The first pass of this investigation hit exactly that and reported
last observations at 14:30. `read_observations` now widens the SQL range by a
day on each side and applies the exact bound in Python on parsed timestamps,
so the trap cannot be re-entered by a caller who forgets it.

**A signal is `(session_date, canonical_ticker)`.** `candidate_identity`
cannot serve as the key — it is a per-evaluation hash, distinct on every row,
so grouping by it would report one signal per evaluation cycle.

**Re-entry is one signal, not several.** 17 of the 34 leave
`ENTRY_READY_RESEARCH` and return, always via `BREAKOUT_REJECTED_STALE`: the
same setup flickering on evidence staleness, not a new setup. On 2026-08-11
MIPH did it six times. The signal is anchored at its earliest observation and
`entry_ready_episode_count` keeps the flicker visible rather than discarding
it.

| episodes | 1 | 2 | 3 | 4 | 6 |
|---|---|---|---|---|---|
| signals | 17 | 10 | 3 | 2 | 2 |

## The `PRICE_DATA_GAP` rule

A window is `PRICE_DATA_GAP` when any intra-window gap exceeds **300 s**, or
the last observation is more than 300 s before the close, or it holds fewer
than 2 observations.

The budget comes from the collector's cadence, not from the outcomes it
classifies. Across all 217 tickers between 10:15 and 14:15 on the three
sessions:

| | 2026-08-06 | 2026-08-10 | 2026-08-11 |
|---|---|---|---|
| p99 gap | 43 s | 39 s | 34 s |
| p99.9 gap | 143 s | 128 s | 114 s |

The widest gap inside any of the 34 measurement windows, across all three
sessions, was 237 s. 300 s sits above every regular cadence gap observed, so
the rule flags genuine discontinuity rather than ordinary thin trading.

**On this dataset it flags nothing.** All 34 windows are
`MEASUREMENT_COMPLETE`. That is a fact about how densely Rubix polled these
symbols, stated plainly rather than a threshold tuned until something tripped.
The rule is exercised by synthetic fixtures in
`tests/test_orb_signal_outcomes.py`, not by production data, and that is
recorded here so nobody later mistakes "never fired" for "tested".

## Measured results

34 signals, all `MEASUREMENT_COMPLETE`, all with a live entry quote. Entry lag
ranged 0.0–36.5 s (median 4.4 s); windows held 1,161–16,633 observations with
45–1,692 price changes each.

| session | signals | quality |
|---|---|---|
| 2026-08-06 | 12 | 12 `MEASUREMENT_COMPLETE` |
| 2026-08-10 | 10 | 10 `MEASUREMENT_COMPLETE` |
| 2026-08-11 | 12 | 12 `MEASUREMENT_COMPLETE` |

Per-signal figures, entry to 14:15 Cairo:

| session | symbol | detect (UTC) | entry | MFE % | MAE % | close % |
|---|---|---|---|---|---|---|
| 2026-08-06 | CLHO | 07:36:33 | 17.900 | 0.22 | −2.91 | −1.45 |
| 2026-08-06 | ACTF | 07:41:43 | 2.800 | 0.36 | −1.43 | −1.07 |
| 2026-08-06 | ARAB | 07:41:43 | 0.245 | 0.00 | −2.04 | −1.22 |
| 2026-08-06 | MEPA | 07:46:38 | 1.880 | 1.60 | −1.06 | 0.00 |
| 2026-08-06 | EGTS | 07:51:36 | 18.010 | 0.89 | −1.50 | 0.11 |
| 2026-08-06 | CRST | 08:01:39 | 2.160 | 7.87 | −0.93 | 7.87 |
| 2026-08-06 | EASB | 08:01:39 | 7.350 | 0.00 | −2.86 | −2.72 |
| 2026-08-06 | VALU | 08:11:34 | 11.930 | 0.00 | −1.59 | −1.51 |
| 2026-08-06 | EGAS | 08:26:31 | 59.250 | 4.64 | −0.59 | −0.35 |
| 2026-08-06 | EFIC | 08:51:49 | 206.590 | 8.91 | −2.46 | 0.68 |
| 2026-08-06 | SVCE | 09:06:34 | 9.450 | 0.53 | −2.65 | −1.90 |
| 2026-08-06 | DSCW | 09:21:40 | 2.060 | 2.91 | 0.00 | 1.46 |
| 2026-08-10 | EGTS | 07:31:42 | 18.400 | 1.63 | −1.74 | −0.54 |
| 2026-08-10 | EALR | 07:36:35 | 382.000 | 2.62 | −1.57 | 0.79 |
| 2026-08-10 | ETEL | 07:36:35 | 111.900 | 0.09 | −3.48 | −2.38 |
| 2026-08-10 | IRON | 07:41:45 | 32.620 | 0.00 | −2.88 | −2.51 |
| 2026-08-10 | TMGH | 07:41:45 | 99.580 | 0.02 | −1.24 | −1.09 |
| 2026-08-10 | VALU | 08:11:47 | 11.830 | 0.00 | −1.94 | −1.18 |
| 2026-08-10 | ORAS | 08:16:41 | 722.000 | 0.14 | −0.68 | −0.35 |
| 2026-08-10 | OCPH | 08:26:52 | 244.000 | 18.82 | −0.82 | 18.82 |
| 2026-08-10 | EFIC | 08:46:35 | 214.000 | 0.21 | −3.50 | −1.26 |
| 2026-08-10 | HRHO | 09:06:44 | 27.500 | 0.00 | −1.42 | −0.98 |
| 2026-08-11 | EGAL | 07:36:43 | 306.000 | 10.46 | −0.32 | 6.86 |
| 2026-08-11 | MIPH | 07:36:43 | 826.750 | 0.00 | −5.41 | −5.28 |
| 2026-08-11 | ALUM | 07:41:40 | 25.970 | 14.36 | −0.04 | 10.63 |
| 2026-08-11 | EGCH | 07:41:40 | 14.290 | 0.14 | −1.61 | −1.33 |
| 2026-08-11 | ELKA | 07:41:40 | 1.720 | 2.33 | −1.16 | 0.58 |
| 2026-08-11 | PHGC | 07:41:40 | 0.091 | 0.00 | −2.20 | −2.20 |
| 2026-08-11 | AMOC | 07:51:46 | 9.410 | 1.49 | −0.53 | 0.53 |
| 2026-08-11 | CCAP | 08:11:32 | 5.210 | 0.77 | −0.38 | 0.19 |
| 2026-08-11 | LCSW | 08:16:32 | 34.750 | 1.44 | −1.99 | 0.72 |
| 2026-08-11 | MFPC | 08:36:45 | 37.200 | 1.61 | −0.40 | 1.32 |
| 2026-08-11 | KABO | 09:21:44 | 8.660 | 1.27 | −1.27 | −0.58 |
| 2026-08-11 | OCDI | 09:46:35 | 32.140 | 12.01 | −0.84 | 8.90 |

**These are measurements. They are not a verdict.** Three sessions and 34
observations is not a sample that can settle whether this strategy works, and
the missing stop and target mean the numbers above cannot be turned into an
outcome per trade without inventing the rule that would have closed it. What
this table supports is the *next* question — not an answer to the last one.

## What this layer touches

| | |
|---|---|
| Reads | shadow session databases (`mode=ro&immutable=1`), `rubix_live_market.db` (`mode=ro`, `PRAGMA query_only=ON`) |
| Writes | `data/research/orb_signal_outcomes/orb_signal_outcomes.db` only |
| Does not touch | ORB strategy logic, thresholds, signal qualification, Lane A/B logic, timing logic, Rubix collection, Daily Scan, EODHD, scheduling |

The collector database is opened `mode=ro` and **never** `immutable=1`: Rubix
is usually still writing, and telling SQLite a live file is immutable is how a
reader ends up parsing a page it was promised would never change.
`OutcomeStore` additionally refuses to open any name in
`PROTECTED_DATABASE_NAMES`, so a mistyped `--output-db` cannot land on a
production database.

Writes are idempotent on `(session_date, canonical_ticker, lane_a_run_id)`;
re-measuring a session replaces its rows rather than accumulating a second
opinion beside the first. Each run records its own source paths, session
boundary and config fingerprint in `orb_signal_outcome_runs`.

## Running it

```bash
python scripts/run_orb_signal_outcomes.py --shadow-dir data/research/orb_full_shadow --rubix-db data/rubix_live_market.db
```

`--dry-run` measures and prints without writing. `--json` emits the summary.
`--session YYYY-MM-DD` and `--shadow-db PATH` are repeatable.

## Files

| File | Role |
|---|---|
| `scalping_orb/performance/signal_outcomes.py` | discovery, price reading, measurement |
| `scalping_orb/performance/outcome_store.py` | schema and idempotent persistence |
| `scripts/run_orb_signal_outcomes.py` | CLI |
| `tests/test_orb_signal_outcomes.py` | 27 tests |
