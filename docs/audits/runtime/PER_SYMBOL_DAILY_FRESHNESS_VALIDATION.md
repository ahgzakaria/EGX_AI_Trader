# Per-Symbol Daily Data Freshness — Validation

Evidence that the per-symbol gate admits exactly the current symbols and
excludes the rest, replayed against real archived data.

**No production scan was run for this validation.** The archived run is read
only; nothing is rewritten, no provider is contacted, no cache is written, no
Rubix process is touched and no scheduled task is triggered.

---

## 1. Replay of `RUN_20260804_005327`

The 194 archived rows were replayed through the new freshness layer with the
expected completed session `2026-08-03`.

| Claim | Result |
|---|---|
| rows in archive | 194 |
| rows classified | **194** |
| all rows accounted for | ✅ |
| `CURRENT` (eligible) | **6** |
| `STALE` (excluded) | **188** |
| other exclusions | 0 |
| current + excluded = total | ✅ |
| current tickers | `CIRA.CA, ETRS.CA, FTNS.CA, GBCO.CA, MEPA.CA, QNBE.CA` |
| decisions from current symbols | **6** |
| decisions excluded | 188 |
| decision total ≤ current count | ✅ |
| dominant observed session | **2026-07-30** |
| expected session | **2026-08-03** |
| market-wide allowed | **false** |
| market regime label | `INSUFFICIENT_CURRENT_COVERAGE` |

Coverage panel:

```
Expected completed EGX session : 2026-08-03
Total operational universe      : 241
History loaded                  : 194
Current symbols                 : 6
Stale symbols                   : 188
Unavailable/invalid             : 0
Current coverage                : 2.5%
Coverage threshold              : 60.0% (minimum_daily_market_coverage_percent)
```

Observed session distribution, reported honestly rather than reduced to one
date:

```
2026-08-03 : 6
2026-07-30 : 184
2026-07-27 : 1
2026-07-09 : 1
2026-07-06 : 1
2014-03-03 : 1
```

Operator messages produced:

```
PARTIAL DAILY DATA COVERAGE

Only symbols with candles from 2026-08-03 were analyzed.
Stale symbols were excluded from BUY/WATCH/AVOID decisions.
```

```
MARKET-WIDE SUMMARY BLOCKED

Current daily-data coverage is 6/241 (2.5%).
Symbol-level results are available only for current symbols.
```

Trading-session distances observed among the stale rows: `2, 5, 17, 20, 3240`
— counted in trading sessions, so the 07-30 → 08-03 gap reads as **2**, not 4.

## 2. Test coverage

`tests/test_per_symbol_daily_freshness.py` — **32 tests**:

- **Per-symbol** — current/stale/missing/invalid/future/insufficient-history
  classification; the expected session never becomes the actual session; an
  unavailable calendar makes nothing current; provenance fields survive onto
  the audit row; trading-session distance across the EGX Sunday.
- **Mixed-session scan** — the exact 6/184/1/1/1/1 fixture: only 6 survive, all
  194 are represented in the audit outcome, every exclusion carries a reason,
  decisions are capped at 6, the dominant session stays 2026-07-30 and the
  expected stays 2026-08-03.
- **Market coverage** — low coverage blocks market-wide conclusions and yields
  `INSUFFICIENT_CURRENT_COVERAGE` with no BULL/BEAR/SIDEWAYS; adequate coverage
  allows them; symbol-level results survive a market-wide block; the threshold
  is documented as data quality, not strategy; broken or out-of-range settings
  fall back to the default.
- **Gate placement** — asserted structurally that the freshness check precedes
  both `calculate_indicators` and `decision_service.evaluate`, and that the
  ineligible path leaves the loop; the actual date is read from the final
  accepted candle row and from nothing else.
- **Replay** — the archived run reproduces 194 / 6 / 188 and the exact six
  current tickers, and remains marked invalid.
- **Boundaries** — no Yahoo reachable, thresholds unchanged, no database,
  process or network access in the freshness layer.

## 3. Suite totals

| Suite | Result |
|---|---|
| `test_per_symbol_daily_freshness.py` | 32 passed |
| `test_daily_candle_provenance.py` | 20 passed, 1 skipped |
| Full suite | **2676 passed, 8 skipped, 1 failed** |

The single failure at the time was
`test_orb_full_shadow_run_controls.py::test_a_once_run_against_a_past_session_yields_no_lane_a_rows`,
which was pre-existing and unrelated - it failed identically on untouched
`main`. It has since been resolved: the test never represented a valid
invariant (its hard-coded date was a *future* session before that date), and it
was replaced by deterministic contract tests. See the ORB run-qualification
commit.

## 4. Regression boundaries confirmed

- The frozen golden ranking in `test_stale_target_consistency.py` is unchanged
  once the fixture states its own expected session — provenance remains
  additive and the ranking is identical.
- Strategy thresholds, indicators, scoring and BUY/WATCH/AVOID rules untouched.
- Provider selection untouched. No Yahoo anywhere in the freshness path.
- No production cache deleted, no database written, no Rubix process started or
  stopped, no scheduled task triggered.

## 5. What this validation does not cover

- **No live production scan was performed.** The gate is proven by replaying
  real archived rows and by structural assertions on the scan loop, not by a
  fresh 241-symbol run.
- **The UI surfaces are now wired.** The Dashboard coverage panel, exclusion
  table and market-wide block landed with this change; the shared Rubix
  quote-freshness taxonomy is complete (see
  `RUBIX_QUOTE_FRESHNESS_TAXONOMY_VALIDATION.md`); and Stock Details, Watchlist
  and AI Analysis now apply the shared contracts through
  `services/analysis_freshness_service.py` (see
  `ANALYSIS_VIEWS_FRESHNESS_INTEGRATION_VALIDATION.md`).
- **The split current-vs-audit CSV export is implemented** as schema v2, and
  Run History, Compare Runs and the Dashboard export controls consume it - see
  `DAILY_SCAN_EXPORT_SCHEMA_VALIDATION.md` and
  `DAILY_SCAN_SCHEMA_V2_READER_VALIDATION.md`.
