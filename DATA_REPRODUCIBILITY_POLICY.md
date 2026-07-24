# EGX AI Trader Data Reproducibility Policy

## Purpose

Every Phase 8 Scan, Backtest, Walk-Forward, comparison, robustness, or research run must retain the exact market input that reached the frozen engine. A report without its inputs is evidence, but it is not replayable evidence.

## Dataset contract

Each new run stages data under `reports/RUN_ID/dataset.staging/`. Only a successful run may atomically publish `reports/RUN_ID/dataset/`. Failed or cancelled work retains an `INTERRUPTED` state and must never look complete.

The completed dataset contains:

- provider-returned raw OHLCV per symbol;
- normalized OHLCV actually passed to the engine;
- ordered symbol list and failures;
- provider metadata, interval, coverage, receipt and exchange timestamps;
- a Walk-Forward prediction snapshot when AI was evaluated;
- `MANIFEST.json` with per-file checksums and deterministic `dataset_hash`.

Each frame is stored twice: a lossless compressed NumPy (`.npz`) artifact used by replay, and a human-readable gzip CSV used for inspection. The NPZ contract uses no pickle/object payloads and preserves exact float bytes, column dtypes, datetime units, timezone, and index. `DATASET_HASH` is calculated from exact normalized ndarray bytes in symbol order; it does not depend on a future provider response.

CSV alone is not replay-authoritative. A Phase 8 diagnostic proved that parsing a 17-digit CSV can move a price by one IEEE-754 ULP (about `2.8e-14`), which was sufficient to change boundary decisions. Early CSV-only archives are therefore excluded from Replay Run and retained only as diagnostic evidence.

## Replay rule

Replay must use `scripts/replay_run.py --run-id RUN_ID` or the Dashboard **Replay Run** page. While replay is active, provider access and Yahoo fallback are impossible. A missing symbol, altered file, missing prediction snapshot, or metric difference causes a hard failure.

## Immutability

After status becomes `COMPLETED`, run evidence is sealed by `RUN_INTEGRITY.json` and marked read-only. Optional annotations live in `notes.json`/`notes.md`, outside the integrity seal. Verify with:

```text
venv\Scripts\python.exe scripts\verify_run_integrity.py --run-id RUN_ID
```

Retention is `NO_AUTOMATIC_DELETION`. Deletion is an explicit Run History operation. Backup pruning is also explicit and confirmed.

## Phase 5 baselines

- `PHASE5_VALIDATED_BASELINE`: status `LEGACY_METRICS_ONLY`. The original Yahoo candle bytes were never archived and cannot be reconstructed honestly.
- `PHASE5_CURRENT_DATA_V2`: a separate current-data baseline created from a completed Phase 8 validation run, with an archived dataset and current results.

The two baselines must never be merged or described as numerically equivalent.
