# Phase 8 — Production Hardening and Data Reproducibility

## Scope

Phase 8 changed software-engineering infrastructure only. Strategy rules, indicators, AI model logic and thresholds, Walk-Forward chronology, ranking, entries, exits, sizing, portfolio constraints, risk, market regime, Backtest calculations, and Forward Testing calculations were not modified.

## Architecture changes

1. **Run-owned datasets:** the provider boundary passively archives raw and normalized OHLCV, symbols, failures, metadata, and AI prediction evidence into an atomic run directory. Replay uses lossless NPZ; gzip CSV is inspection-only.
2. **Offline replay:** archived runs can be replayed with provider access disabled and exact metric/prediction verification.
3. **Integrity and immutability:** completed runs receive dataset and run checksums, read-only evidence, and separate annotations.
4. **Rubix supervision:** a bounded-restart process supervises the independent collector, publishes redacted structured health, performs database integrity/WAL checks, and guarantees graceful child cleanup.
5. **Backups:** Rubix, Forward Testing, and experiment metadata receive checksummed atomic backups and isolated verified restore.
6. **Unified health:** CLI and Dashboard report provider, databases, coverage, freshness, disk, backup, experiment/replay readiness, and the safety state.
7. **Release candidate:** transparent Windows source packaging and setup replace ad-hoc environment creation.

## New files

- `services/dataset_archive.py`, `services/run_replay.py`, `services/backup_manager.py`, `services/system_health.py`
- `scripts/replay_run.py`, `scripts/verify_run_integrity.py`, `scripts/rubix_collector_supervisor.py`
- `scripts/backup_data.py`, `scripts/restore_backup.py`, `scripts/system_health.py`
- `scripts/establish_phase5_baselines.py`, `scripts/setup_windows.bat`, `scripts/create_release.py`
- `scripts/recover_interrupted_runs.py`
- `dashboard/system_health.py`
- `tests/test_phase8_hardening.py`
- Phase 8 policy and operations documents

## Modified files

- `core/data_provider.py`: passive raw/normalized capture and strict offline replay routing.
- `services/experiment_tracking.py`: dataset lifecycle, atomic CSV writes, seals, read-only completion, safe cancellation.
- `services/backtest_service.py`: cancellation closes dataset staging; calculations unchanged.
- `services/ranking_robustness.py`: wraps existing calculations in the same archived experiment lifecycle.
- `core/scanner.py`: imports Pandas required by its existing report writer; signal logic unchanged.
- `scripts/launch_rubix_production.py` and `scripts/start_rubix_production.bat`: supervisor workflow and Retry/Yahoo/Cancel choices.
- `scripts/validate_phase6_regression.py`: wraps existing validation in an archived research run; calculation calls unchanged.
- `config/settings_manager.py` and `config/settings.json`: engineering retention/backup settings only.
- `app.py`: Replay Run and System Health pages.

## Baselines

`PHASE5_VALIDATED_BASELINE` is explicitly `LEGACY_METRICS_ONLY`: Strategy Only approximately 65.81% return / 17.91% drawdown and AI Ranking Only approximately 71.62% / 16.48%. The original Yahoo candle snapshot is unavailable, so exact replay is impossible.

`PHASE5_CURRENT_DATA_V2` is generated separately from the current Yahoo cache with a complete lossless dataset archive. Coverage is 2020-08-09 through 2026-06-09. Strategy Only produced 729 trades, 66.43% return, Profit Factor 1.29, and 18.02% maximum drawdown. AI Ranking Only produced 731 trades, 72.98% return, Profit Factor 1.31, and 16.80% maximum drawdown. Dataset hash: `fafa064f87acf39992f53f3f8bf3b22b09d2ef34fb70b26d53dd932f38704414`. It must not be described as the original Phase 5 dataset.

## Validation

- Static Python import/syntax checks: passed.
- Phase 8 targeted tests: passed.
- Full automated suite: **92 passed**.
- Unified health smoke test: passed; current machine state is `RESEARCH_ONLY` because the Rubix production database is absent.
- Forward Testing database integrity: `ok`.
- Market cache integrity: `ok`.
- Backup creation and isolated restore drill: passed.
- Streamlit smoke test: HTTP 200 on an isolated port; temporary process stopped.
- Run integrity: 207 normalized symbol files verified with no checksum failures (`RUN_20260714_125023`).
- A CSV-only replay diagnostic correctly failed after one-ULP float drift; that archive is preserved as `NONREPLAYABLE` and excluded from the UI.
- Lossless offline replay: passed in `RUN_20260714_131113`; `metrics_match=true` and `predictions_match=true`, with the same dataset hash and no provider fallback.
- Crash recovery: two orphaned 44-hour `RUNNING` records were audited and explicitly recovered to `CANCELLED`; new runs record their process ID and recovery never touches a live process.
- Final System Health: `RESEARCH_ONLY`, Rubix database missing, Yahoo cache research-only, Forward database healthy, latest backup completed, zero orphaned running runs, and two lossless replay-ready runs.
- Transparent `EGX_AI_Trader_RC1` source release built successfully; no `.db`, `.sqlite`, or `.exe` files are bundled.
- Windows setup installs the locked direct dependency versions, validates imports, and offers an optional desktop shortcut without storing secrets.
- RC1 was tested from inside the release directory: **92 passed**; the folder was then rebuilt clean to remove test caches and generated runtime settings.

## Production decision

Phase 8 provides production-grade evidence capture, recovery, and operability. It does **not** approve real-money trading. Promotion requires a fresh Rubix collector session with adequate symbol coverage, a successful current backup/restore drill, and an accepted forward-testing period.
