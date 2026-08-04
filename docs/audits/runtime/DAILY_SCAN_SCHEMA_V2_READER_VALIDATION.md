# Daily Scan Schema v2 — Reader and UI Validation

Evidence that Run History, Compare Runs and the download controls read schema-v2
archives with their correct semantics, and that legacy and invalid archives are
never presented as more than they are.

**No production scan was run, no production archive was rewritten, no collector
process was touched and no production database was written.**



> **Metadata ownership.** The export metadata described here lives in the
> `daily_scan_export` section of one composed `run_metadata.json`, written
> only through `services/run_metadata_service.py`. See
> [DAILY_SCAN_METADATA_OWNERSHIP_AUDIT.md](DAILY_SCAN_METADATA_OWNERSHIP_AUDIT.md)
> for why a second writer used to erase it.

---

## 1. Central reader

`services/daily_scan_archive_reader.py` is the single entry point. Pages call it
instead of parsing files, because parsing per page is how readers drift — one
would treat `scan_results.csv` as the universe, another as decisions.

Version comes from `run_metadata.export_schema_version` and **never** from a
filename. Typed states:

| Status | Meaning |
|---|---|
| `SCHEMA_V2_VALID` | required files present, alias matches, all invariants pass |
| `SCHEMA_V2_INVALID` | declared v2 but validation or the alias check failed |
| `LEGACY_V1` / `LEGACY_UNKNOWN` | no declared version |
| `INVALID_DATA_PROVENANCE` | marker present — overrides everything |
| `INCOMPLETE_ARCHIVE` | a required v2 artifact is missing |
| `UNSUPPORTED_FUTURE_SCHEMA` | declares a version this build does not understand |

For v2 it re-runs `validate_archive` and additionally checks that
`scan_results.csv` genuinely equals `scan_current_decisions.csv`. Any failure
returns `SCHEMA_V2_INVALID` with the exact violations — decisions are not shown
from an archive that failed validation.

## 2. Legacy behaviour

Coverage accessors return **`None`** rather than a number for any non-v2
archive: `operational_universe_count`, `current_count`, `current_coverage_percent`
and `market_wide_summary_allowed`. A row count is never converted into coverage.
The panel states this and shows no percentage.

```
LEGACY ARCHIVE — COVERAGE SEMANTICS UNKNOWN

This run predates the versioned current-decision and full-coverage export
contract. Decision rows may be displayed for historical inspection, but
full-universe coverage cannot be reconstructed safely.
```

## 3. Invalid-provenance behaviour

An `INVALID_DATA_PROVENANCE.md` marker classifies the archive as
`INVALID_DATA_PROVENANCE` regardless of its declared schema, sets
`decision_use_allowed = False`, surfaces `NOT FOR DECISION USE`, and **blocks
comparison entirely**.

Verified against the real `RUN_20260804_005327`: status
`INVALID_DATA_PROVENANCE`, decision use refused, universe count `None`, and no
schema-v2 file exists in that directory.

## 4. Run History

For a valid v2 archive:

```
CURRENT DAILY COVERAGE
6/241 (2.5%)

DECISIONS AMONG CURRENT SYMBOLS
BUY 1 · WATCH 4 · AVOID 1

MARKET-WIDE SUMMARY BLOCKED
Reason: INSUFFICIENT_CURRENT_COVERAGE
```

The loaded-result ratio appears as a separately labelled secondary metric, with
help text stating the headline uses the operational universe. The decision
breakdown is explicitly captioned as *not* the whole-market distribution.

## 5. Compare Runs — two separate sections

Decisions always come from `scan_current_decisions.csv`; coverage from
`scan_coverage_audit.csv`. Mixing them is how a provider outage becomes a false
finding about the model.

**Fixture B — same strategy, lower coverage** (6 current → 3 current over the
same 241-symbol universe):

| Claim | Result |
|---|---|
| decision count A / B | 6 / 3 |
| CURRENT in both | 3 |
| like-for-like decision changes | **0** |
| coverage caveat shown | ✅ |
| disappearance reasons | all `DATA_COVERAGE_CHANGE` |

So a halved decision count produces **no** strategy-deterioration claim. The
caveat reads:

```
DECISION COUNTS ARE NOT DIRECTLY COMPARABLE

Current-data coverage changed from 6/241 (2.5%) to 3/241 (1.2%).
Part or all of the decision-count difference may be caused by data availability
rather than strategy behaviour.
```

A genuine decision flip on an unchanged-coverage pair *is* reported as one, with
no caveat. Disappearances are classified `DATA_COVERAGE_CHANGE`,
`DECISION_CHANGE`, `UNIVERSE_CHANGE`, `PROVIDER_FAILURE` or
`LEGACY_SEMANTICS_UNKNOWN`.

`strategy_claim_allowed` is False when the CURRENT-in-both set is empty — with
no like-for-like subset, no statement about strategy can be made at all.

## 6. Downloads

Three clearly-named controls with counts beside them:

- **Download Current Decisions** — "Contains only CURRENT symbols for which a
  decision was calculated."
- **Download Full Coverage Audit** — "Contains every operational-universe symbol
  and its data-freshness/outcome status. **Not opportunities.**"
- **Download Compatibility Results** — "Compatibility alias — CURRENT decisions
  only."

Disabled entirely for any non-`SCHEMA_V2_VALID` archive, with the missing or
invalid artifacts named. Every path goes through `resolve_artifact`, which uses
only the basename and re-checks containment after resolution, so traversal and
symlink escapes are refused — a download control must never become a way to
read an env file.

## 7. Cache safety

`archive_cache_identity` hashes the resolved run path plus the size and mtime of
`run_metadata.json`, both v2 exports, the compatibility alias **and the invalid
marker**. A run that has just been marked invalid cannot keep serving its
previous valid presentation, and two runs sharing a name in different
directories never share an identity.

## 8. Mutation checks — all eight bite

| Reverted protection | Result |
|---|---|
| Compare Runs reads decisions from the coverage audit | 5 failed |
| coverage warning removed | 1 failed |
| common-CURRENT intersection replaced by union | 4 failed |
| legacy archive assigned fabricated coverage | 1 failed |
| invalid-provenance marker ignored | 2 failed |
| download path may escape the run directory | 4 failed |
| reader cache identity omits the invalid marker | 1 failed |
| Dashboard labels the audit as current decisions | 1 failed |

## 9. Test totals

| Suite | Result |
|---|---|
| `test_daily_scan_archive_reader.py` | **45 passed** |
| `test_daily_scan_export_schema.py` | 43 passed |
| Full suite | see final report |

## 10. Remaining limitations

- **No browser proof.** The panels are asserted on their rendering contract and
  the reader is exercised against real fixtures; the pages were not driven in a
  live Streamlit session.
- **Compare Runs offers scan comparison alongside the existing backtest
  comparison** rather than replacing it; the backtest path is untouched.
- **Legacy archives are classified from evidence, not migrated.** No historical
  archive was rewritten, and `LEGACY_V1` versus `LEGACY_UNKNOWN` is decided only
  by whether a compatibility file is present.
- The reader loads whole CSVs into memory; for the current universe size that is
  immaterial, but it is not a streaming reader.
