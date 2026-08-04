# Daily Scan Export Schema — Audit

What `scan_results.csv` means today, who reads it, and why a versioned split is
needed.

**Research only.** No indicator, score, threshold or decision rule changed.

---

## 1. What is written today

`core/scanner.py` (end of `scan_symbols`):

| File | Written from | Contains |
|---|---|---|
| `scan_results.csv` | `experiment.save_records("scan_results.csv", results)` | one row per **decision** |
| `failed_symbols.csv` | `failures` | symbols that raised or were excluded |
| `swing_symbol_coverage_audit.csv` | `write_coverage_report(coverage)` | per-symbol coverage evidence |
| `summary.csv` | counts | Stocks / BUY / WATCH / AVOID |
| `run_metadata.json` | `ExperimentRun` | run-level fields, **no schema version** |

`save_records` drops `Data` and `AIFeatures` and JSON-encodes nested values.

## 2. The semantics changed under the previous commits

Before the per-symbol freshness gate, `results` held one row per *successfully
loaded* symbol, so `scan_results.csv` was close to "the analysed universe".

Since `0845a9f` the gate excludes stale, missing-date, invalid-date,
future-date and insufficient-history symbols **before** the decision engine.
`results` therefore already contains **current decisions only**.

**This is the central finding.** The file's meaning has already narrowed, and
nothing in the archive says so. A consumer written against the old meaning now
silently sees a smaller universe with no signal that the definition moved —
exactly the class of error the freshness project exists to stop.

`RUN_20260804_005327` predates the gate and still carries the old mixed
semantics: 194 rows, of which only 6 were current.

## 3. Known consumers

| Consumer | Reads | Needs |
|---|---|---|
| `strategy_selector/market_classifier.from_scan_results` | rows | **decisions** |
| `strategy_selector/selector.py` | via classifier | decisions |
| `scripts/audit_buy_zero.py` | `scan_results.csv` | decisions |
| `scripts/run_phase10_breakout.py` | `scan_results.csv` | decisions |
| `scripts/run_phase11_selector.py` | `scan_results.csv` | decisions |
| Run History / Compare Runs | run dirs | **both** — decisions and coverage |
| Dashboard export buttons | in-memory results | decisions |
| Forward testing (`process_scan`) | `results` in memory | decisions |

Every file-based consumer found wants **decisions**, not the universe. That
makes "current decisions only" the right final semantics for
`scan_results.csv`, and means no consumer has to change to keep working.

What is missing is the other half: nothing records *why* the other 235 symbols
are absent. `failed_symbols.csv` and `swing_symbol_coverage_audit.csv` cover
parts of it, but neither accounts for the whole operational universe in one
place with typed freshness outcomes.

## 4. Compatibility risks

| Risk | Assessment |
|---|---|
| a consumer assuming one row per universe symbol | none found among file readers; Run History/Compare Runs infer counts and would misread a coverage drop as a strategy change |
| a consumer assuming BUY+WATCH+AVOID spans the market | `market_classifier` aggregates rows into a market classification — with partial coverage that is a data-quality question, and is why the market-wide gate exists |
| legacy archives without a schema version | must be treated as `LEGACY_UNKNOWN`, never assumed to be v2 |
| `INVALID_DATA_PROVENANCE.md` | must override normal decision-use presentation regardless of schema |

## 5. Selected migration approach

**Additive, versioned, no rewrite of history.**

- `DAILY_SCAN_EXPORT_SCHEMA_VERSION = 2`, persisted in run metadata and in both
  new exports.
- `scan_current_decisions.csv` — CURRENT, eligible, decision-calculated rows only.
- `scan_coverage_audit.csv` — exactly one row per operational-universe symbol,
  with its typed outcome and exclusion reason.
- `scan_results.csv` stays, as an explicit **compatibility alias** of
  `scan_current_decisions.csv`, with metadata stating
  `scan_results_semantics = "CURRENT_DECISIONS_ONLY"` and
  `scan_results_compatibility_alias_of = "scan_current_decisions.csv"`.
  Every file-based consumer keeps working unchanged, and the meaning is now
  written down instead of implied.
- Archives without `export_schema_version` are `LEGACY_UNKNOWN`: their
  `scan_results.csv` is not assumed to be either contract, and readers must not
  fabricate full-universe coverage from them.

Publication is atomic: both exports and the metadata are validated against
cross-file invariants and published together, or the run is marked failed and
the previous archive is left untouched.
