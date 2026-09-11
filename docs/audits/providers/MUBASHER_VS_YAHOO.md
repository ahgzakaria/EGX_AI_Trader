# Why Yahoo is still here, and how it compares with Mubasher

**Date:** 2026-09-11

**Question:** why does the program still keep Yahoo, and is it better data than
MubasherTrade PRO's own daily record?

**Answer:** Yahoo is kept as a **record**, not as a source.

- **It is the input every backtest and strategy finding here was measured on.**
- **It bootstraps seven active symbols EODHD cannot serve.**
- **It feeds no live daily candle.**

As data it is not the better of the two. Mubasher reproduces 92.5% of the
backtest's signals, and where only one of them fires, a third source backs
Mubasher's signal about twice as often as Yahoo's (69.7% against 32.6%). Nor is
the snapshot what the code calls it: it is **neither frozen nor complete** (§2).

Nothing in the program has been changed.

## 1. What Yahoo does today

| role | where | live? | state |
|---|---|---|---|
| **Backtest and research input** (`LEGACY_BACKTEST_V1`) | `data/market_data_cache.sqlite`, purpose `backtest`, no network | no | 218 symbols, 493,172 daily rows from 2016-07-18 |
| **Seed** for symbols EODHD does not support | `data/frozen_yahoo_seed` (35 CSVs) + Mubasher tail | **yes**, 7 active symbols | Tier D: ARAB, CFGH, DEIN, MEGM, NAHO, TRTO, UNIP |
| Audit reference | `data/yahoo_cache` (201 one-year CSVs, fetched 2026-08-26) | no | read by `audit_eodhd_full_universe.py`, `resolve_eodhd_manual_queue.py` |
| Refresh | task `EGX_AI_Trader_YahooCacheRefresh` | — | **Disabled**. Last run 2026-07-24 (206 advanced, 52 failed, newest session 2026-07-22). Not declared in `verify_scheduled_tasks.ps1`. |

- **Scanner, dashboard and forward testing route to `rubix`.** Their daily
  candles go through `core.research_router` (EODHD plus Mubasher), never Yahoo.
- **Two network paths are still configured.** `fallback_provider` is `yahoo`.
  `yahoo_history_seed` (`core/data_provider.py`) is the daily warm-up loader
  given to the Rubix and TickerChart providers; on a missing or expired cache
  entry it downloads from Yahoo and stores the result.
- **The logs record no provider fallback.**
- **Six active Tier D symbols have no seed and are unreadable today:** ACGC,
  JUFO, LUTS, MTIE, NCCW, OCPH.

## 2. The snapshot is neither frozen nor complete

**Not frozen.**

- 204 symbols end on 2026-07-22. Six end later, and each entry was rewritten
  after the freeze:

  | symbols | extra rows | rewritten |
  |---|--:|---|
  | ABUK, HRHO | +24 | 2026-08-31 22:24 |
  | ACGC, COMI, JUFO, NCCW | +31 | 2026-09-07 20:17 |

- **The mechanism.** Apart from the disabled refresh script, the only code that
  writes Yahoo rows is `yahoo_history_seed`. `LocalCacheProvider.store` deletes
  the symbol's entire series and re-inserts the download, so an expired entry
  reached through the Rubix warm-up path replaces history wholesale. The
  mechanism is identified from the code; no log line names the caller.
- **No past result has moved yet.** All six rewritten symbols, and two controls,
  still have closes up to 2026-07-22 identical to the research panel built
  2026-08-29 (100%). Nothing prevents a later rewrite from changing them, and
  backtests load with `allow_expired=True`, so these six already reach further
  than the rest.

**Not complete.**

- Composition: 197 active symbols, 19 inactive, 1 registered alias, and `^CASE30`.
- **33 active symbols have no snapshot at all, so every backtest silently leaves
  them out:** ACAP, ACTF, AIDC, AIND, ALRA, AMII, BONY, CPME, CRST, DGTZ, GGRN,
  GOUR, GPIM, GTEX, HBCO, KASABF, KORA, KRDI, MATD, NAPR, NARE, NDRL, PHGC,
  QNBE, TANM, TAQA, TWSA, TYCN, UBEE, UTOP, VALU, VLMR, VLMRA.
- Eight snapshot symbols end before the freeze (SUCE on 2023-03-08 through IEEC
  and SIMO on 2026-07-16).

## 3. Coverage

- **Mubasher carries 217 of the 218 snapshot symbols.** The one it lacks is
  `^CASE30`, an index.
- **Its record starts before the snapshot for 203 of 217.** The snapshot is a
  ten-year window by construction.
- **It carries all 230 active symbols**, including the 33 the snapshot lacks
  ([MUBASHER_VS_EODHD_DAILY_HISTORY.md](MUBASHER_VS_EODHD_DAILY_HISTORY.md) §1).

## 4. Agreement on shared sessions

| field | era | within 0.5% | within 1% |
|---|---|--:|--:|
| close vs Yahoo `Close` | 2016–22 | 66.01% | 69.27% |
| close vs Yahoo `Close` | 2023–26 | 69.92% | 70.50% |
| close vs Yahoo `Adj Close` | all | 33.21% | 34.70% |
| high | all | 67.04% | 69.23% |
| low | all | 67.02% | 69.20% |
| volume | all | 73.98% | 74.72% |
| daily return | all | 90.69% | **93.61%** |

- **Returns agree far better than levels.** As with EODHD, most disagreement is a
  price-basis offset, not a different market.
- **Mubasher is not dividend-adjusted.** It tracks Yahoo's `Close`, not its
  `Adj Close`.
- **Per symbol:** only 3 of 214 are within 1% on every shared session, 101 on at
  least 95%, and 57 on fewer than half. Lowest: LUTS, MEGM, MTIE (0.0%), MHOT
  0.8%, NCCW 1.1%, ASPI 1.5%.

## 5. Who is right when they disagree

EODHD is the third source here. It has its own faults (see the EODHD audit), so
this is a tie-break, not ground truth.

**Signals.** The shipped `CONFIRMED_VOLUME_BREAKOUT` rule run on both, cleaned
the way the backtest loader cleans (NaN rows and zero-volume bars dropped), from
each symbol's Yahoo warm-up to its last shared session:

- **Totals:** Yahoo 1,303 signals, Mubasher 1,402, across 206 symbols.
- **Same symbol, same day: 1,205.** That is **92.5% of Yahoo's** signals and
  85.9% of Mubasher's.
- **Where only one fires, EODHD fires the same day on:**

| signals produced by | count | EODHD fires the same day |
|---|--:|--:|
| both | 1,151 | 83.8% |
| **Mubasher only** | 178 | **69.7%** |
| **Yahoo only** | 92 | **32.6%** |

**Price-basis steps.** A persistent change of more than 2% in Mubasher ÷ Yahoo,
holding 20 sessions either side: 687 steps across 111 symbols, 114 of them
within 14 days of a Mubasher corporate action. Checked against EODHD over the
same ±5 sessions, the series that moved was:

| series that moved | steps |
|---|--:|
| **Yahoo** | **172** |
| Mubasher | 106 |
| neither | 115 |
| both | 4 |
| no EODHD bars at both ends | 290 |

They cluster in early September 2021 (2021-09-05 carries 12), the same cluster
the EODHD comparison found. That is consistent with a Mubasher fault in that
week; its cause is still open.

**Missing sessions.** Inside the shared span, Yahoo lacks **1,091** sessions
Mubasher traded (SUCE alone 255). Mubasher lacks **43**.

## 6. Opening price and precision

- **Neither has a real daily open.** Since 2024, Yahoo's open equals the previous
  close on 98.2% of sessions, and Mubasher's on 99.7%.
- **Below 1 EGP, where EGX quotes to 0.001** (25,169 sessions since 2023):
  - 85.4% of Mubasher's closes carry a third decimal, against 78.2% of Yahoo's.
  - The two differ by 0.0005 or more on 46.9%.

## 7. The seeds, and where they meet the live tail

- **Every seed is a copy of the snapshot.** All 35 have closes identical to it
  on 100% of shared sessions.
- **Mubasher, set against them over the whole seed span,** agrees within 1% on
  anywhere from 0.8% of sessions (MHOT) to 99.5% (TRTO).

**The seam.** The live series is the seed with a Mubasher tail appended after
it, so the question is whether the join adds a return the market never had:

- **For every active seeded symbol with a tail except DEIN, it does not.** The
  return across the join equals Mubasher's own return that day, and Mubasher
  equals the seed over the last 20 sessions before it (median ratio 1.0000).
  The older disagreements are history, not a jump at the join.
- **DEIN is the exception.** Mubasher sits at 0.9095 of the seed with no recorded
  corporate action. Only 2 tail bars were appended, and the name barely trades.
- **MEGM has no tail;** it has not traded since 2023.

## What this means

1. **Yahoo's remaining value is reproducibility.** Every strategy number in
   `docs/audits/strategies/` was measured on this snapshot. Retiring it does not
   make those numbers wrong; it makes them impossible to re-derive exactly.
2. **That value only holds if the snapshot really is frozen.** Today it is not
   (§2). The write path through `yahoo_history_seed` can replace a symbol's
   history on any cache miss.
3. **As a live source Yahoo has no role Mubasher cannot take.** The seven active
   seeded symbols are all in Mubasher, the join is clean, and six more Tier D
   symbols that have no seed at all are in Mubasher too.
4. **As data, Mubasher is the stronger record.** It covers more (all 230 active
   symbols against 197), misses fewer sessions (43 against 1,091), and carries
   the signals a third source confirms more often. Its weakness is the
   discontinuities of early September 2021.

## Limits

- **EODHD as the third source** has its own adjustment faults, documented in the
  EODHD audit. "Neither" and "no EODHD bars" together are 405 of 687 steps.
- **The research panel** used to check for rewrites was built on 2026-08-29. A
  rewrite that changed history before that date would not show in this check.
- **The two rewrites** are attributed to `yahoo_history_seed` by elimination in
  the code, not by a log line naming the caller.
- **Precision** is measured on stored floats rounded to six places. Yahoo stores
  single precision.

## Reproduce

```
venv/Scripts/python.exe scripts/research/mubasher_vs_yahoo.py
```

It writes `reports/data_sources/mubasher_vs_yahoo_by_symbol.csv`,
`mubasher_vs_yahoo_basis_steps.csv` and `mubasher_vs_yahoo_signals.csv`. It
reads the local Yahoo cache, MubasherTrade PRO's data and the EODHD response
cache, and makes no network call.
