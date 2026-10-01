# The Live Path Moves From EODHD to MubasherTrade PRO

**Switched:** 2026-09-24 — `live_history_source: "mubasher"` in
`config/settings.json` and in `DEFAULT_SETTINGS`. Approved by the owner ahead of
the EODHD subscription lapsing in October 2026.

**What changed:** the daily history every live page reads — the Daily Dashboard
scan, Swing Breakout, Breakout Watch, Confirmed Breakout, the portfolio, AI
Analysis, the forward recorders — now comes from MubasherTrade PRO's own daily
record (`data/measured_turnover.db`, imported by `scripts/import_mubasher_local.py`)
for every symbol, through `core.research_router`'s Mubasher branch. Backtests were
already on the frozen Mubasher record and are unaffected. No strategy changed.

## The evidence it rests on

**Data quality** — `scripts/research/mubasher_vs_eodhd.py`, run 2026-09-24:

| | Mubasher | EODHD |
|---|--:|--:|
| active symbols covered | 229 of 229 | 229 of 229 |
| traded sessions missing from the record | 117 | **36,536** |
| where they differ by > 1%, the independent Yahoo record agrees with | **53.8%** | 16.0% |
| signals only one source produced that Yahoo also produces | **78.4%** | 12.6% |
| closes under 1 EGP carrying the third decimal EGX quotes | **85.8%** | 27.2% |
| close × volume within 1% of the exchange's own turnover | **68%** | 50–57% |

Over 2023–26, closes agree within 1% on 93.8% of shared sessions. Neither source
carries a real opening price; the strategy's rule does not read one.

**The strategy that is traded** — `scripts/record_live_source_shadow.py`, which
runs the live rules on both records every session, with the Daily Dashboard's
own decision added on 2026-09-24 and backfilled over 2026-09-01..24:

| rule | both | EODHD only | Mubasher only |
|---|--:|--:|--:|
| Daily Dashboard BUY | 26 | 2 | 4 |
| Breakout Watch | 142 | 5 | 0 |
| Confirmed Breakout | 9 | 2 | 0 |
| Swing Breakout | 8 | 7 | 0 |

Three of the six Daily Dashboard disagreements are GTEX, which EODHD quotes as
0.04 where the exchange prints 0.044; one is UTOP, which EODHD holds too few
sessions of to evaluate at all.

**Nothing on the live path needs EODHD.** With every EODHD request refused and
the token removed, every active symbol was loaded through the scanner's own path
on the Mubasher branch: 221 of 229 loaded, 216 on the current session (the rest
did not trade), and **0 EODHD requests were attempted**. The 8 that failed are
short histories, 7 of them already excluded under EODHD; DEIN, GRCA and UTOP,
which EODHD cannot serve, load on Mubasher. The EODHD client reads its token only
when it sends a request, so the client the scan context still constructs is inert.

## What the switch costs

* **The record is only as fresh as the terminal.** EODHD refreshed itself;
  Mubasher's record moves when MubasherTrade PRO is open (its minute store) and
  when its archive is downloaded. A missed day is filled by the next download.
  `RUN_DAILY.bat` says how many sessions the archive is behind, and the importer
  reads `history.db.tmp` when the terminal leaves a download there
  ([memory of that failure](../../../sector_flow/mubasher_local.py) —
  `history_database`).
* **The universe rebuild needs EODHD's exchange list**
  (`scripts/migrate_eodhd_241_universe.py`). After the subscription ends, a new
  listing is not picked up automatically; the terminal's own symbol master is the
  replacement when one is needed.

## Before the subscription ends

1. Keep `record_live_source_shadow.py` running from the daily click until then:
   it is the only place the two records are still compared.
2. After it ends, take the shadow out of `scripts/run_daily_update.py` — its EODHD
   side will have nothing to read — and keep `data/eodhd_cache/`: the research
   and audit scripts read it offline.
3. To return to EODHD while it exists, set `live_history_source` back to
   `"eodhd"`; nothing else changes.

**2026-10-01: the shadow left the daily click ahead of the end date.** The owner
decided not to renew EODHD. That day the shadow spent an hour re-downloading
every history: EODHD had not yet published the session, and it was serving full
histories at 8–10 s a symbol. Step 1 above is therefore closed early and step 2
is done. `scripts/record_live_source_shadow.py`, `data/research/live_source_shadow.db`
and `data/eodhd_cache/` all remain.
