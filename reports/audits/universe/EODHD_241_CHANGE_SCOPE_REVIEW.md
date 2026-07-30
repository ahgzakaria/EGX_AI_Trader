# EODHD 241 Universe Migration — Change-Scope Review

Every path reported by `git status` at review time, classified, with the reason
it had to change. Nothing was reverted, staged, committed or pushed.

**Totals** — 69 modified, 1 deleted, 8 untracked additions (plus one unrelated
untracked directory). Of these, **3 files carry a pre-existing local change that
is NOT part of this migration** and are called out in §7.

Legend for the "Scope" column:

- `exact` — only the lines this migration required
- `broad` — larger, but every line is migration work
- `foreign` — contains work that predates this migration

---

## 1. Authoritative universe data and model

The single source of truth and the tooling that produces it.

| File | Scope | Why it changed |
|---|---|---|
| `core/universe.py` *(new)* | exact | The authoritative loader: parses `egx_universe.csv`, validates it, and exposes membership, display labels, explicit Rubix mappings and provenance. Raises `UniverseUnavailable` rather than falling back. Also hosts `read_symbol_frame` (the NA-safe CSV reader that keeps the literal ticker `NULL` alive) and `_is_missing`. |
| `data/universe/egx_universe.csv` *(new)* | exact | The one operational universe: 241 active records plus 76 archived ones, each with full company name, `.EGX` symbol, exchange, currency, instrument type, ISIN and explicit Rubix mapping. |
| `data/universe/snapshots/*.json` *(new)* | exact | Timestamped raw EODHD responses (active + delisted) captured at migration time for audit and reproducibility. |
| `data/universe/archive/legacy_symbols_265.csv` *(new)* | exact | Byte copy of the retired list, kept for audit only. Not on any runtime path. |
| `data/symbols.csv` *(deleted)* | exact | The retired 265-symbol universe. Removed from the runtime tree so no straggler can silently read it; archived above. |
| `scripts/migrate_eodhd_241_universe.py` *(new)* | exact | Fetches the official list through the authenticated EODHD client, validates it, writes the snapshot, rebuilds the universe and the audit. `--from-snapshot` rebuilds without re-spending API budget. |

## 2. Runtime universe consumers

Each of these previously hard-coded `"data/symbols.csv"`. All now read
`core.symbols.SYMBOL_SOURCE` / `core.universe.UNIVERSE_SOURCE`. Unless noted,
the change is a one-line constant swap plus its import.

| File | Scope | Why it changed |
|---|---|---|
| `core/symbols.py` | broad | The loader boundary. Routes membership to the authoritative universe, refuses the retired path with an explicit error, enriches `ApprovedSymbol` with the company name and `eodhd_symbol`, and reads through `read_symbol_frame`. |
| `core/data_provider.py` | exact | `expected_symbols` for the Rubix provider. |
| `core/scan_job_manager.py` | exact | Default scan source and workspace key. |
| `core/daily_bridge/rubix_daily_builder.py` | exact | Daily-bridge universe. |
| `core/research_router.py` | exact | Reconciliation map is keyed by symbol — switched to the NA-safe reader so a `NULL` row cannot become the string `"nan"`. |
| `main.py` | exact | CLI scan entry point. |
| `optimization/evaluator.py` | exact | Optimizer universe. |
| `services/backtest_service.py` | exact | Backtest universe (2 sites). |
| `services/ranking_robustness.py` | exact | Ranking-robustness universe. |
| `services/rubix_daily_shadow.py` | exact | Shadow-run universe. |
| `services/experiment_tracking.py` | exact | `dataset_version` fingerprint now hashes the authoritative universe. |
| `services/run_replay.py` | exact | Archived-run symbol lists read NA-safely so a recorded `NULL` replays correctly. |
| `scalping/scanner.py`, `scalping/range_scanner.py`, `scalping/event_range_scanner.py` | exact | Scanner universes. |
| `scalping_expected_range/scanner.py`, `backtest.py`, `calibration.py` | exact | Expected-range universes. |
| `scalping_expected_range/frozen_watchlist.py` | exact | **Removed a second competing universe.** `validated_eodhd_symbols()` derived a 225-symbol list from `historical_symbol_routing.json`; it now returns the authoritative active list. The manifest keeps its per-symbol provider routing, untouched. |
| `scalping_uptrend_pullback/frozen_watchlist.py` | exact | Same competing-source removal for the Uptrend Pullback selector. |
| `core/paper_trading.py` | — | *Not changed.* Its reader was reviewed for the `NULL` hazard; it reads a fixed trades file whose symbol column is `.CA`-suffixed and therefore never an NA token. |

### Scripts and launchers

| File | Scope | Why it changed |
|---|---|---|
| `scripts/launch_egx_ai_trader.py` | exact | Preflight file check and adapter symbol load; now filters to ACTIVE rows. |
| `scripts/rubix_collector_supervisor.py` | exact | `--symbols` default. |
| `scripts/rubix_health.py`, `run_daily_bridge_validation.py`, `run_eodhd_capability_audit.py`, `audit_eodhd_phase1.py`, `validate_phase6_regression.py`, `run_phase10_breakout.py`, `run_phase11_selector.py` | exact | Live audits and research runners. |
| `scripts/audit_eodhd_symbol_mapping.py`, `audit_eodhd_reconciliation.py` | exact | Naive line-parsers replaced with `active_symbols()`; the new file is multi-column. |
| `scripts/build_current_research_migration_manifest.py`, `build_current_research_correctness_reports.py`, `simulate_eodhd_symbol_routing.py` | exact | These reproduce ARCHIVED historical reports, so they were pointed at `ARCHIVED_LEGACY_SOURCE` deliberately — repointing them at the new universe would falsify the reports they regenerate. |
| `scripts/start_tickerchart_adapter.ps1` | exact | Symbols file path plus an ACTIVE-row filter and `canonical_symbol` column. |
| `scripts/refresh_yahoo_cache.py` | — | *Deliberately not changed.* It still names the retired path, so it now fails loudly with the retired-source error. It is a Yahoo maintenance script and must not be operational. |

## 3. UI ticker/name display

| File | Scope | Why it changed |
|---|---|---|
| `dashboard/formatting.py` | exact | New shared contract: `SYMBOL_COLUMN`/`NAME_COLUMN` headers, `symbol_option_label`, `company_name`, `symbol_ticker`, `with_company_name_column`. |
| `dashboard/home.py` | exact | Daily Dashboard tables gain a separate name column; Stock Details picker and the three advanced-research tables show ticker + name. |
| `dashboard/watchlist.py` | exact | Both watchlist tables enriched. |
| `dashboard/stock_details.py` | exact | Header renders `TICKER — Name`. |
| `dashboard/scalping.py` | broad | Range-Bound, Live Monitor, live-readiness and scan-results tables gain the name column; detail and paper-opportunity selectors gain labels; the hard-coded `241 / 265` caption became a live `_universe_caption()`; NA-safe report reader; Arabic/English messaging for `RUBIX_MAPPING_UNAVAILABLE`. |
| `dashboard/uptrend_pullback.py` | exact | Primary, live and rejection tables gain the name column; detail selector gains a label; Arabic/English messaging for the new state. |
| `dashboard/opportunities.py` | exact | Four opportunity tables plus the blocked-symbol list gain the name column; NA-safe report reader. |
| `dashboard/expected_range_scalper.py` | exact | Main table and the history-lag table gain the name column. |
| `dashboard/decision_support.py` | exact | Table enrichment plus labelled pin/inspect selectors. |
| `dashboard/ai_stock_analysis.py` | exact | Analysis-history table gains a company column. |
| `dashboard/forward_testing.py` | exact | Open and closed paper positions gain a name column on display only. |
| `dashboard/run_history.py` | exact | Saved-run CSV artifacts are enriched on read; NA-safe reader keeps `NULL` intact. |
| `dashboard/settings.py` | exact | Backtest configuration states the universe it will run over. |
| `dashboard/eodhd_migration_review.py` | exact | Removed a hard-coded `/ 265`; NA-safe reader. |
| `decision_support/sector_analysis.py` | exact | Sector map is keyed by symbol — NA-safe reader. |

## 4. Rubix explicit mapping and subscription

| File | Scope | Why it changed |
|---|---|---|
| `providers/rubix_subscription.py` | exact | Keys now come from the universe record's verified `rubix_symbol`, never suffix substitution. Adds `unmapped` (reported gap, not a fatal error, so the collector still starts) and `exit_monitoring` (a removed ticker with an open position stays subscribed for exit only). |
| `scalping_expected_range/live_readiness.py` | exact | Adds the `RUBIX_MAPPING_UNAVAILABLE` state and `_has_rubix_mapping`; only mapped symbols are queried; an unmapped candidate is retained with an explicit reason instead of being reported as a collector failure. |
| `scalping_uptrend_pullback/live_readiness.py` | exact | Same contract for the Uptrend Pullback engine. |

## 5. Migration audit and reports

| File | Scope | Why it changed |
|---|---|---|
| `reports/audits/universe/*.csv`, `EODHD_241_UNIVERSE_MIGRATION.md`, this file | exact | Required migration deliverables. |
| `docs/guides/launcher/ONE_CLICK_LAUNCHER_REPORT.md` | exact | Described the launcher reading `data/symbols.csv`; that statement became false. |

Historical audit documents under `docs/audits/**` that cite 265 were left alone
on purpose: they record what was measured at the time and must not be rewritten.

## 6. Tests

| File | Scope | Why it changed |
|---|---|---|
| `tests/test_eodhd_241_universe.py` *(new)* | exact | Universe contract: count, names, uniqueness, `.EGX`, formatter, selectors, removed-symbol rules, no old fallback. |
| `tests/test_null_ticker_safety.py` *(new)* | exact | The literal `NULL` ticker end-to-end. |
| `tests/test_rubix_unmapped_symbols.py` *(new)* | exact | Mapped vs unmapped candidates, mixed plans, no guessed keys, no quote inheritance, UI wording. |
| `tests/test_universe_identifier_isolation.py` *(new)* | exact | Separation of canonical / `.EGX` / `.CA` / Rubix identifiers, and the fail-closed matrix. |
| `tests/test_universe_ui_and_exports.py` *(new)* | exact | Page-by-page column and dropdown contract; export name integrity. |
| `tests/test_ai_stock_analysis_symbol_picker.py` | exact | 265 → 241; the retired CSV carried Arabic names for only 2 of 265 rows, so the Arabic-name search test became an authoritative-name search test. |
| `tests/test_clean_dashboard_ui.py`, `test_clean_scalping_ui.py`, `test_uptrend_pullback_ui_integration.py` | exact | Tables are now 8 columns (7 trader columns + the separate name column). |
| `tests/test_frozen_historical_watchlist.py` | exact | Selector universe is the authoritative 241, not the manifest's 225. |
| `tests/test_one_click_launcher.py` | exact | Preflight fixture uses the new universe path. |
| `tests/test_rubix_production_rollout.py` | exact | Rejection reason is now "not in the authoritative universe". |
| `tests/test_scan_job_manager.py`, `test_scan_terminal_handoff.py` | exact | Workspace keys are anchored on the authoritative source. |

## 7. Pre-existing unrelated local changes — NOT part of this migration

These were already modified in the working tree before the migration began.

| File | Status |
|---|---|
| `services/rubix_auth_assistant.py` | **Foreign — untouched by this migration.** Contains the Rubix authentication-freshness fix. Verified: zero occurrences of `UNIVERSE_SOURCE`, `core.universe`, `read_symbol_frame`, `company_name` or `NAME_COLUMN`. Not edited at any point. |
| `tests/test_rubix_auth_assistant.py` | **Foreign — untouched.** Same verification. |
| `data/paper_trades.csv` | **Foreign — untouched.** Recorded trade data. |
| `docs/audits/strategies/SCALPING_MULTI_SESSION_VERDICT.md` | **Foreign — untouched.** |
| `.codex/` *(untracked)* | **Foreign.** A separate worktree tree; not read or written. |

### `scripts/launch_rubix_production.py` — the one mixed file

This file carries **both** the pre-existing auth-freshness fix **and** 5 lines of
migration work. The migration's contribution is exactly:

```
 94: +from core.universe import UNIVERSE_SOURCE
268:  "--symbols", str(PROJECT_ROOT / UNIVERSE_SOURCE),
397:  expected_symbols=load_symbols(PROJECT_ROOT / UNIVERSE_SOURCE),
577:  PROJECT_ROOT / UNIVERSE_SOURCE,
1312: PROJECT_ROOT / UNIVERSE_SOURCE,
1315: paths=(str(PROJECT_ROOT / UNIVERSE_SOURCE),),
```

Every other hunk (`PreflightItem`, `auth_preflight_items`,
`format_auth_diagnostics`, `_auth_selection_revision`,
`_replace_auth_preflight_rows`, the revision-scoped `auth_inspection` key and the
`display_status` rendering) is the pre-existing auth fix and was **not**
semantically altered. The file had to be touched because it referenced the now
deleted `data/symbols.csv` in five places and would otherwise fail to launch.

## Flagged for your attention

1. **`scripts/launch_rubix_production.py` mixes two changes.** Unavoidable — the
   file would be broken otherwise — but it means the auth fix and the migration
   cannot be committed as cleanly separated commits without an interactive
   stage of that file.
2. **`scripts/refresh_yahoo_cache.py` now raises on use.** Intentional, but it is
   a behaviour change to a script this migration otherwise had no reason to
   affect. Reverting it would require reintroducing the retired path.
3. **`core/research_router.py` and `decision_support/sector_analysis.py`** are
   outside the migration's obvious blast radius. They were changed only because
   they key data by symbol and would have corrupted the ticker `NULL`.

Nothing else was found that is unrelated or unnecessarily broad.
