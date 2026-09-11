# Mubasher vs EODHD: can one daily history feed the live program?

**Date:** 2026-09-11
**Question:** could MubasherTrade PRO's own daily record replace EODHD, so the
program runs on one source?
**Answer:** data quality does not stand in the way. On the measures that decide
a breakout signal, Mubasher is at least as good as EODHD and better in most of
them. What stands in the way is operational: it is a desktop terminal's file,
refreshed by hand, on one machine. §7 lists what a switch would require. Nothing
in the program has been changed.

## Scope

- **What is compared:** the live path's source. The scanner, the watchlist and
  the dashboard read EODHD's split-adjusted history plus a Mubasher tail
  (`core.research_router`). Every backtest in the repository runs on the frozen
  Yahoo snapshot (`core.data_provider`, purpose `backtest`), and nothing here
  touches it.
- **Who is compared:** the 230 active symbols from `core.universe`. The 11
  registered EODHD aliases in `data/universe/symbol_aliases.csv` are excluded,
  so no company is counted twice. Where Mubasher files a company under another
  ticker, its own symbol master resolves it by ISIN: AIND→AIHC, ALRA→AIFI,
  MATD→MMAT.
- **How:** entirely offline. Mubasher comes from `history.db`, read-only and
  immutable. EODHD comes from the client's response cache, split-adjusted by
  `providers.eodhd_adjustment.adjust` with the same operational-volume policy
  the router serves. The shared window is 2016-01-01 to each symbol's last
  shared session.
- **Third source:** the frozen Yahoo research panel (191 symbols, 2016-07-19 to
  2026-07-22), used to settle disagreements. It is not a copy of Mubasher: it
  sides with EODHD on LUTS (94% against 0%) and MASR (100% against 40%).

## Summary

| | Mubasher | EODHD |
|---|---|---|
| Active symbols covered | **230 / 230** (3 by ISIN) | 230 / 230 |
| History starts earlier | **156** symbols | 45 |
| Traded sessions the other has and this one lacks | **117** | 37,392 |
| Close within 1% of Yahoo, 2016–22 | **73.13%** | 57.90% |
| Close within 1% of Yahoo, 2023–26 | 71.65% | 71.53% |
| When the two differ by >1%, Yahoo matches | **52.4%** | 15.9% |
| Signals only this source produced, reproduced by Yahoo | **78.5%** of 251 | 13.7% of 226 |
| Discontinuities where this series departs from Yahoo | 109 | **36** |
| Closes under 1 EGP carrying the third decimal | **86.0%** | 25.4% |
| Real opening price in the daily record | no (99.9% = prior close) | no (89.4% = prior close) |
| Corporate-action events | in the record (`SACT`) | separate endpoints |
| Fresh without a person | 14-session minute store | yes, a day late |
| Access | desktop terminal on this machine | API |

## 1. Coverage and identity

- **Neither source misses an active company.** EODHD's 241-code listing holds
  11 second codes. Five copy a live ticker's series, four are pre-rename
  tickers, and two have no market data of their own. They are registered as
  aliases.
- **EODHD keeps pre-rename history under the old code; Mubasher keeps it under
  the live ticker.** That accounts for much of the 37,392 traded sessions EODHD
  lacks:

  | symbol | traded sessions missing in EODHD |
  |---|--:|
  | MASR | 1,850 |
  | COPR | 1,521 |
  | ASPI | 1,397 (its history sits under the alias PIOH) |
  | ODIN | 887 |
  | OIH | 501 |

  OIH's gaps are not a rename. Measured separately on 2026-09-11, all 687
  sessions it lacks across EODHD's own span fall on a Sunday.
- **Mubasher history ending early is a dead or unpriced symbol, not a stale
  file.** Eight active symbols end before the file's 2026-09-07. Each has a
  median EODHD turnover of 0 over its last 20 rows (SIMO, MEGM, NDRL, ICLE,
  DEIN, GPPL, MATD, SPHT).

## 2. Agreement on shared sessions

| field | era | within 0.5% | within 1% |
|---|---|--:|--:|
| close vs EODHD-adjusted | 2016–22 | 59.10% | 61.54% |
| close vs EODHD-adjusted | 2023–26 | 89.51% | **92.87%** |
| daily return | all | 92.73% | **96.30%** |

Returns agree far better than levels. Most disagreement is a **price-basis
offset** — the two back-adjust for different corporate actions — not a
different market.

**Over what the live rule reads** (each symbol's last 251 shared sessions,
`warmup_bars`):

- 153 of 229 agree within 1% on every session.
- 196 agree on at least 95% of sessions.
- 11 agree on fewer than half.

## 3. Who is right when they disagree

Where the two closes differ by more than 1% (82,256 sessions), the Yahoo panel
matches:

| | share |
|---|--:|
| Mubasher only | **52.4%** |
| EODHD only | 15.9% |
| both | 0.1% |
| neither | 31.6% |

The examples are EODHD adjustment factors that are simply wrong:

| symbol | what EODHD did | Mubasher and Yahoo |
|---|---|---|
| MPCO (2017) | factor 66 | agree with each other |
| MEPA (2016) | factor 5.6 | agree with each other |
| EHDR (2021) | "adjusted" a correct raw price 0.70 to 3.50 | agree with each other |
| NINH (2026-05) | a different factor on the same split date as Mubasher | agree with each other |

**The consequence for signals.** `CONFIRMED_VOLUME_BREAKOUT` as shipped, run on
each source:

- **Same symbol, same day:** 83.3% of EODHD's signals overall, 86.3% from 2023.
- **Where the sources disagree, the Yahoo panel settles it:**

| signals produced by | count | Yahoo fires the same day |
|---|--:|--:|
| both | 1,078 | 89.1% |
| **Mubasher only** | 251 | **78.5%** |
| **EODHD only** | 226 | **13.7%** |

A signal only EODHD sees is, six times in seven, an artefact of EODHD's
series.

## 4. Discontinuities: Mubasher's weakness

A basis step is a persistent change of more than 2% in Mubasher ÷ EODHD, holding
for 20 matched sessions either side. There are 349 steps across 106 symbols.

- **Recorded actions explain few of them.** 314 sit where neither source
  records a corporate action within five sessions.
- **Checked against Yahoo** over the same ±5 sessions, the series that moved
  was:

  | series that moved | steps |
  |---|--:|
  | Mubasher | **109** |
  | EODHD | 36 |
  | neither | 84 |
  | both | 2 |
  | no Yahoo bars at both ends | 118 |

- **The Mubasher steps cluster on 2021-09-01 to 2021-09-08.** 2021-09-06 alone
  carries 16; 36 fall in that week. For example, EHDR jumps 0.672 → 2.91 on
  2021-09-06 in Mubasher while EODHD raw and Yahoo stay near 0.61. Only 10 of
  344 Mubasher equity tables begin on that date, so it is not a table re-keying.
  The cause is not established.

So the two sources fail differently:

| | typical fault | effect on the rule |
|---|---|---|
| EODHD | a wrong factor held over years | a level error, which ratios inside a window partly survive |
| Mubasher | a jump on one day | a false breakout or ATR spike wherever the jump is in the lookback |

The rule's PriceIntegrity guard catches jumps of 30% or more. It does **not**
catch a 5–10% bonus step.

**The 11 live-window disagreements, attributed:**

| symbol | live agreement | what differs | fault |
|---|--:|---|---|
| GTEX | 0.0% | Mubasher jumps 873% (2025-08-28), no event recorded | Mubasher |
| MEGM | 0.0% | both carry a 2013 jump; no traded bar since 2023 | dead symbol |
| LUTS | 2.8% | Mubasher jumps 358.7%; Yahoo matches EODHD 94%, Mubasher 0% | Mubasher |
| ICLE | 6.4% | Mubasher jumps 59.5% (2021-09-28) inside a thin symbol's long window | Mubasher |
| BONY | 8.8% | Mubasher adjusts a 2026-08-06 bonus (0.909); EODHD does not | EODHD |
| HBCO | 16.3% | EODHD jumps 43.3% (2026-07-09); Mubasher 2.1% | EODHD |
| PHGC | 17.1% | EODHD jumps 91.6%; Mubasher 32.7% after adjusting a rights issue | EODHD |
| NINH | 29.9% | same-day split, different factors; Yahoo 97% Mubasher, 22% EODHD | EODHD |
| CFGH | 37.5% | sub-pound price at two decimals; Yahoo 100% Mubasher, 43% EODHD | EODHD |
| ARAB | 39.4% | EODHD split 2026-04-01; Yahoo matches neither well (35% / 15%) | unresolved |
| MASR | 47.8% | Yahoo matches EODHD 100%, Mubasher 40% | Mubasher |

## 5. What each record carries

- **No real opening price in either daily record.** The open equals the prior
  close on 99.9% of Mubasher sessions and 89.4% of EODHD's. The only true EGX
  open on this machine is the first bar of Mubasher's minute store, for its
  rolling 14 sessions (`sector_flow/mubasher_local.py`).
- **Price precision.** EGX quotes sub-pound shares to 0.001. Over 26,927
  sessions of closes under 1 EGP since 2023 (61 symbols):
  - 86.0% of Mubasher's closes carry a third decimal, against 25.4% of EODHD's.
  - The two differ by 0.0005 or more on 72.7%.

  EODHD rounds these, e.g. CFGH 0.12 where Mubasher and Yahoo have 0.115.
- **Corporate actions.** Mubasher's `SACT` column records events as
  `type~factor`: 2,227 on the routed symbols. The types were decoded from the
  close ratio on each event date, not from documentation:

  | type | count | price on the event date | reading |
  |---|--:|---|---|
  | 2 | 457 | median ratio 1.000 | split or bonus shares, adjusted |
  | 6 | 1,624 | median ratio 0.958 | cash dividend, not adjusted |
  | 3 | 207 | 14.5% of events move more than 20% | rights issue, not adjusted |
  | 1 | 126 | — | undetermined |
  | 5 | 22 | — | undetermined |

  EODHD serves splits and dividends from separate endpoints. EODHD's cached
  dividends could not reproduce Yahoo's adjustment, most likely because bonus
  shares are missing (2026-09-07 finding). Mubasher's type 2 records bonus
  shares. Whether it rescues that adjustment is **untested**.
- **Volume** against the exchange turnover Mubasher records (absolute
  difference, median):

  | check | median | within 1% |
  |---|--:|--:|
  | Mubasher close × volume | 0.404% | 68.22% |
  | EODHD raw close × raw volume | 0.667% | 57.41% |
  | EODHD adjusted close × served volume | 1.073% | 48.96% |

  The turnover is Mubasher's own column, so this is internal consistency for
  Mubasher and a cross-check only for EODHD. Mubasher adjusts volume by the same
  factor as price, so price × volume survives an adjustment.

## 6. Freshness and operation

- **`history.db` moves only when someone downloads history in the terminal.**
  On this run it held 2026-09-07. `scripts/run_daily_update.py` says so plainly:
  step one of the day is "open MubasherTrade PRO and download history (you, by
  hand)".
- **The minute store updates itself while the terminal runs** and keeps 14
  sessions. The import fills the sessions `history.db` lacks from it. The
  measured store holds **2026-09-10**, with 09-08 to 09-10 from minutes.
- **A close built from minutes is official only when the auction printed.**
  Confirmed closes run 186/221, 183/219 and 182/219 on those three sessions,
  against 220/220 from `history.db`. About one minute-store close in six is the
  last continuous trade rather than the official close.
- **EODHD held 2026-09-10 for 224 of 230** and needs nobody. It publishes a
  completed session a day late (`core/research_router.py`).
- **Mubasher requires the terminal installed and logged in, on this machine.**
  There is no API. A second machine, or a day the terminal is not opened, has
  no Mubasher data at all.

## 7. What a switch would require

Not done, and listed so the decision is priced:

1. **The router.** EODHD appears in 112 Python files. Routing tiers,
   split-adjustment policy, operational-volume policy and the bridge tail are
   all built around it.
2. **Retroactive history.** Mubasher back-adjusts on each download, so a past
   close can change. Everything would have to re-read full history rather than
   append. Anything that stores a price — a position's entry, a forward-test
   record — would need the raw price kept beside it, and Mubasher offers no raw
   series.
3. **A guard for small steps.** PriceIntegrity catches 30% jumps. Mubasher's
   known faults include steps well under that (§4).
4. **The download routine.** A missed day degrades to minute-store closes, about
   one in six unofficial. A missed fortnight leaves a gap.
5. **Universe membership.** EODHD's exchange listing decides it today.
   Mubasher's symbol master (313 CASE rows) carries ticker, ISIN, currency,
   sector and `IS_TRADABLE`. It lists 78 equities not in the universe, and has
   no listing or delisting history.

## Corrections to what this project believed

- **"Mubasher is missing 11 universe symbols."** It was missing none. The 11 are
  EODHD second codes (now registered aliases) or companies Mubasher files under
  another ticker, resolvable by ISIN.
- **"Mubasher carries no corporate-action data."** It does, in `SACT` (§5).
- **"`history.db` refreshes symbols erratically."** The symbols it holds only to
  an old date are dead or unpriced (§1). The whole file moves when history is
  downloaded.

## Limits

- **The Yahoo panel is one more vendor, not ground truth.** It ends 2026-07-22,
  and 31.6% of the disagreements match neither source.
- **The step detector's thresholds** (2%, 20 sessions, ±5 sessions) are
  presentation choices. The attribution uses the same window, and the
  2021-09-06 cluster's cause is open.
- **VWAP is populated on only 429 shared Mubasher rows,** so the exact
  turnover check is thin. The close-based one carries the section.
- **USD-quoted symbols** report turnover in EGP (MOIL, VLMR), which inflates
  their turnover differences.
- **`SACT` types 1 and 5 are undecoded,** and types 2/3/6 are read from price
  behaviour, not from documentation.

## Reproduce

```
venv/Scripts/python.exe scripts/research/mubasher_vs_eodhd.py
```

It writes `reports/data_sources/mubasher_vs_eodhd_by_symbol.csv`,
`mubasher_vs_eodhd_basis_steps.csv` and `mubasher_vs_eodhd_signals.csv`. It
needs MubasherTrade PRO's data on this machine and the EODHD response cache,
and makes no network call.
