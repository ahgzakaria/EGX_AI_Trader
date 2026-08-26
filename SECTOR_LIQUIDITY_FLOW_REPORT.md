# Sector Liquidity Flow — Phases 1 & 2

Goal: analyse each EGX sector's share of daily liquidity, as the foundation for a
next-session liquidity-rotation forecast (Phase 3). Phases 1 and 2 build the
classification and the historical record. **No forecasting is included here.**

Phases 1 and 2 are descriptive and additive. They introduce no strategy, no
signal, and no trading decision, and they do not modify Scanner, Backtest, or
any frozen engine file.

---

## Phase 1 — Sector map

`scripts/build_sector_map.py` joins two authorities and transcribes the result:
the EGX active-listing workbook (`EGX_Active_Stocks_By_Sector_Market_Cap.xlsx`,
official EGX sector mapped to the 18-sector taxonomy) is the **sector**
authority; `core.universe` is the **symbol** authority.

**Outputs**

| File | Contents |
| --- | --- |
| `data/sectors.csv` | 232 tickers x 18 sectors, engine `.CA` convention, with `CompanyName`, `ISIN`, `MarketCapEGP`, `MatchedBy` |
| `reports/sector_map_reconciliation.csv` | every universe symbol the workbook does not classify, with the reason |

### The join is on ISIN first, ticker second

EGX tickers are reused and renamed, so ISIN is the stable key — but neither key
alone is sufficient:

| Match key | Active symbols matched |
| --- | --- |
| ISIN only | 226 |
| Ticker only | 220 |
| **Either** | **232** |

ISIN decides when both match. `MatchedBy` records which key produced each row,
so the 6 ticker-only matches can be reviewed.

**Coverage: 232 / 241 active symbols (96.3%).** The 9 unmapped:

* **2 non-equity instruments** — `EGREF`, `KASABF`, excluded by the workbook's own
  Exceptions sheet.
* **7 absent from the workbook** — `FAITA`, `FTNS`, `HBCO`, `NULL`, `SEIGA`,
  `UTOP`, `VLMR`.

`NULL` is a literal ticker in `data/universe/egx_universe.csv` for "Fitness
Prime", carrying no ISIN and `UNVERIFIED_NO_FEED_OBSERVATION`. It looks like an
artifact of the EODHD exchange-symbol list rather than a real security. It is
reported, not silently dropped.

Unmapped symbols stay `Unknown` and are excluded from sector aggregates. Nothing
is guessed: `decision_support/sector_analysis.py` was written "without inventing
unavailable classifications" and that contract is preserved.

### A correction worth recording

The first version of this map was built from `data/symbols.csv` — the **retired**
265-symbol universe, archived by the EODHD 241 migration. It reported 83.8%
coverage, and roughly 40 of its "unmapped, presumed delisted" symbols were simply
not operational any more. `core.symbols.load_symbols` refuses that file outright,
and a test now pins the map to `core.universe`
(`test_sector_map_is_built_from_the_operational_universe`).

**Side effect (intended):** `config/settings.json` has always pointed at
`data/sectors.csv`, but the file never existed, so `_apply_sector_strength` in
`decision_support/service.py` was a no-op and the `sector_strength: 0.5` edge
weight contributed nothing. Shipping the map activates that existing path. The
17 decision-support regression tests still pass.

---

## Phase 2 — Sector liquidity history

| Module | Responsibility |
| --- | --- |
| `sector_flow/history.py` | Pure computation. No I/O, no provider access. |
| `sector_flow/builder.py` | Loads the universe, isolates failures, persists with provenance. |
| `scripts/build_sector_flow.py` | CLI + coverage report. |

Market data is read **only** through `core.data_provider.load_history`, so the
current-research routing (EODHD / validated local + Rubix Daily Bridge) and its
provenance metadata apply exactly as they do for Scanner and Dashboard. Symbols
come from `core.universe.active_engine_symbols()`, and the universe path is
recorded in each build's provenance row.

**Latest build:** 198 of 232 classified symbols loaded (34 unavailable, 9
unclassified) across **5,844 sessions x 18 sectors, 2001-08-14 → 2026-08-26**.
Providers: 186 eodhd, 10 local_plus_rubix, 2 eodhd_plus_rubix. 4,816 sessions
pass the coverage guard.

**Per (session, sector):** `Turnover`, `TurnoverShare`, `MarketTurnover`,
`MarketSymbols`, `Symbols`, `Advancers`, `Decliners`, `Breadth`, `MeanReturn`,
`TurnoverZ`, `RVOL`, `ShareChange`, `SharePrev`, `ShareRank`, `SessionCoverage`.

Stored in `data/sector_flow.db` (`sector_daily`), with one `sector_flow_builds`
row per build recording purpose, provider mix, and coverage.

### Three decisions that constrain Phase 3

**1. Turnover is a stated proxy, not the exchange's figure.**
EGX reports value traded from intraday VWAP; the daily candle contract carries
only OHLCV. Turnover is therefore `(High + Low + Close) / 3 x Volume`, chosen as
the closest available VWAP approximation and named as a proxy in the module
docstring. `method="close"` is available for comparison.

**2. Every baseline is strictly trailing.**
`TurnoverZ` and `RVOL` use `.shift(1).rolling(20)`, so a session is scored
against the 20 sessions *before* it, never a window containing itself. Verified
by `test_baselines_exclude_the_session_they_score`, which recomputes both from
raw arrays.

**3. Incomplete sessions are flagged, never ranked.**
`SessionCoverage` = reporting symbols / trailing median. Sessions below 60% are
retained but excluded by default from `latest_snapshot()` and
`rotation_matrix()`. This guard was added after the first build ranked an
in-progress session; see below.

---

## Findings from the first universe build

### 1. An in-progress session was ranked as if complete

The first build's newest session (2026-08-26, the build date) had **14 symbols
reporting out of ~194**. Sector shares computed over 7% of the panel were
presented as a sector ranking; every `RVOL` sat near 0.13, which is the
signature of a partial day rather than a quiet one.

Fixed by the `SessionCoverage` guard. The build now reports the latest
*complete* session and states explicitly when the newest stored session was
skipped.

The guard was later observed working in both directions: rebuilt after the
2026-08-26 session closed, that same date passed the coverage check with a full
panel and became the latest complete session. The guard rejects a partial day,
not a particular date.

### 2. EODHD is missing Sunday bars for ~40% of EGX symbols before 2026

Sunday is a full EGX trading day. The coverage guard flagged roughly one session
in five, every year, which is far too regular to be a listing effect. The cause:

| Quarter | Median Sunday panel | Median other-day panel | Ratio |
| --- | --- | --- | --- |
| 2023Q1 | 89 | 168 | 0.53 |
| 2024Q3 | 100 | 183 | 0.55 |
| 2025Q4 | 109 | 194 | 0.56 |
| 2026Q1 | 192 | 194 | **0.99** |
| 2026Q3 | 193 | 193 | **1.00** |

Confirmed at the source, not inferred from aggregates. Across the 231 symbols
whose freshly-fetched EODHD range spans Sep–Dec 2025 (17 Sundays):

* **136 symbols** have all 17 Sundays
* **92 symbols** have **zero** Sundays

It is binary per symbol and present in the newest fetches, so it is a vendor
data gap, not a stale-cache artifact. It appears to have been corrected from
roughly 2026Q1 onward.

**Consequence for Phase 3:** any model trained on pre-2026 history would learn a
spurious day-of-week pattern, and sector shares on ~20% of historical sessions
are computed over a biased ~55% panel. The affected sessions are already
flagged by `SessionCoverage`; Phase 3 must train on complete sessions only.
Of 5,844 stored sessions, **1,029 fall below the 60% coverage threshold**,
leaving 4,815 usable — and the excluded ones are overwhelmingly Sundays, so
excluding them removes one weekday from the pre-2026 record rather than a random
sample of days.

---

## Verification

* `tests/test_sector_flow.py` — **23 tests**, covering the turnover proxy,
  per-sector (not cross-frame) feature computation, trailing-baseline
  correctness against manual arrays, share normalisation, the coverage guard,
  ISIN-over-ticker precedence, the guard against the retired universe, the
  reconciliation split, and persistence round-trip.
* `tests/test_decision_support.py` — 17 tests still pass with the sector map
  present.
* Full suite: 3 failures, all pre-existing on this branch and untouched by this
  work (two frozen-manifest hash mismatches for `strategy/config.py`, and one
  date-dependent regression whose 30-session window has since moved).

## Running it

```bash
venv/Scripts/python.exe scripts/build_sector_map.py
```

```bash
venv/Scripts/python.exe scripts/build_sector_flow.py
```

The sector-flow build takes ~35 min (full universe, 10y, through the Rubix Daily
Bridge). Progress is logged every 25 symbols.

## Not included

Phase 3 (next-session liquidity forecast) and Phase 4 (intraday early signal)
are not implemented. When Phase 3 is built it must be benchmarked against
persistence (tomorrow = today) and a 5-day mean before any output is trusted:
turnover is strongly autocorrelated, so a model can look accurate while merely
repeating yesterday.

Nothing here is investment advice. `TurnoverShare` describes where trading
activity was, not where price is going.
