# Daily Scan Export Schema — Validation

Evidence that schema v2 separates current decisions from full-universe coverage,
publishes atomically, and accounts for every operational symbol exactly once.

**No production scan was run, no collector process touched, no production
database written, and no historical archive modified.**

---

## 1. Schema

`DAILY_SCAN_EXPORT_SCHEMA_VERSION = 2`, persisted in `run_metadata.json` and
carried by both new exports.

| File | Contract |
|---|---|
| `scan_current_decisions.csv` | CURRENT, eligible, decision-calculated rows only |
| `scan_coverage_audit.csv` | exactly one row per operational-universe symbol |
| `scan_results.csv` | **compatibility alias** of the decisions file |

Metadata states the alias explicitly:

```json
"scan_results_semantics": "CURRENT_DECISIONS_ONLY",
"scan_results_compatibility_alias_of": "scan_current_decisions.csv"
```

Archives without `export_schema_version` are `LEGACY_UNKNOWN`. The version is
never inferred from a filename — an archive that happens to contain a
`scan_results.csv` says nothing about what that file means.

## 2. Controlled replay of `RUN_20260804_005327`

Built into a temporary directory from the original archive plus the universe
source. The archive was read only.

| Claim | Result |
|---|---|
| operational universe | **241** |
| archived result rows | **194** |
| CURRENT | **6** |
| excluded among result rows | **188** |
| non-result universe symbols | **47** |
| audit rows | **241** |
| audit rows == universe | ✅ |
| no duplicate symbols | ✅ |
| current coverage (universe denominator) | **2.5 %** |
| loaded-result current percent (separate) | 3.1 % |
| market-wide summary | **blocked**, `INSUFFICIENT_CURRENT_COVERAGE` |
| dominant observed session | **2026-07-30** |
| expected completed session | **2026-08-03** |
| decisions | 0 BUY, 4 WATCH, 2 AVOID = **6** |
| all invariants | ✅ |
| compatibility alias byte-identical to decisions | ✅ |

The six CURRENT symbols: **CIRA.CA, ETRS.CA, FTNS.CA, GBCO.CA, MEPA.CA,
QNBE.CA**.

The 47 non-result symbols carry their real archived outcomes — 16
`EODHD_CACHE_MISS`, 16 `INSUFFICIENT_HISTORY`, 15 `INVALID_HISTORY` — taken
from `failed_symbols.csv`, never invented. 6 + 188 + 47 = 241.

## 3. Cross-file invariants

`validate_archive` is pure and fails closed. It checks audit row count against
the universe, one row per symbol, no duplicates, no invented symbols, decisions
⊆ audit, every decision CURRENT/eligible/calculated with a matching
`SUCCESS_CURRENT` audit outcome, no excluded symbol in decisions, counts
reconciling to the universe, BUY+WATCH+AVOID == decision_count, schema version
present and consistent, session distribution matching dated audit rows, denied
Rubix overlays retaining the EODHD decision price, and coverage percent using
the **operational universe** denominator.

## 4. Atomic publication

Files are staged beside their destination, `fsync`-ed, and moved into place
only after every invariant holds. A half-written archive is worse than none: a
reader cannot distinguish a missing audit from an empty one and would take the
decisions file for the whole universe.

Verified: an invariant violation publishes nothing; a write failure at each of
the three file positions leaves no partial archive; and a failure after a
previous successful publication leaves the earlier archive byte-identical.

## 5. Mutation checks

| Reverted protection | Result |
|---|---|
| stale row admitted into current decisions | 1 failed |
| coverage uses loaded rows as denominator | 9 failed |
| compatibility alias carries excluded rows | 4 failed |
| denied overlay may change the decision price | 1 failed |
| schema version omitted from metadata | 15 failed |
| incomplete archive published as complete | 2 failed |
| schema version inferred from a filename | 3 failed |
| **omitted universe symbol accepted** | **not detected** |

The last one is honest redundancy rather than a gap. Removing the row-count
clause leaves the *missing-symbol* clause, which catches the same violation —
and a shortened audit is still rejected, proven by
`test_an_omitted_universe_symbol_is_rejected`. The count clause is
defence in depth over an already-covered property.

Three of the first-run mutations initially survived for the same reason
(several clauses catch one violation). Isolating tests were added so the
CURRENT, eligibility and decision-calculated clauses are each pinned
independently.

## 6. Test totals

| Suite | Result |
|---|---|
| `test_daily_scan_export_schema.py` | **43 passed** |
| Full suite | **2834 passed, 8 skipped, 0 failed** |

## 7. Scope notes

- **The archive-reading surfaces were wired in a follow-up commit.** Run History, Compare Runs and
  the download controls consume schema v2 through
  `services/daily_scan_archive_reader.py` - see
  `DAILY_SCAN_SCHEMA_V2_READER_VALIDATION.md`. Compare Runs now separates a
  strategy difference from a data-coverage difference and refuses to call a
  coverage-driven decision drop a strategy change.
- No historical archive was migrated. `RUN_20260804_005327` keeps its original
  files, its original hashes and its `INVALID_DATA_PROVENANCE.md` marker, and
  no v2 files were written into it.
- The replay output lives in a temporary directory and in
  `reports/validation/daily_scan_export_replay.json`, never inside the original
  run directory.
