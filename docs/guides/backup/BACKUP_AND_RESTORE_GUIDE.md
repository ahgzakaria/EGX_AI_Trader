# Backup and Restore Guide

## Create a backup

```text
venv\Scripts\python.exe scripts\backup_data.py
```

This creates `backups/BACKUP_YYYYMMDD_HHMMSS/` atomically. It uses SQLite's online backup API for the Rubix and Forward Testing databases and stores experiment metadata in a ZIP. `BACKUP_MANIFEST.json` records checksums, integrity results, sizes, and confirms that no secrets are included.

Missing Rubix is recorded as `MISSING`; it does not cause the valid Forward database backup to be discarded.

## Restore safely

```text
venv\Scripts\python.exe scripts\restore_backup.py --backup backups\BACKUP_ID --destination restored\RESTORE_ID
```

Restore always targets a new directory. Existing destinations and live database overwrite are refused. All checksums are verified before extraction and every restored SQLite database must pass `PRAGMA integrity_check`.

After inspection, an operator may separately schedule a maintenance window to replace a live file. The restore tool intentionally does not perform that destructive step.

## Retention

There is no automatic pruning. Explicit pruning requires both flags:

```text
venv\Scripts\python.exe scripts\backup_data.py --prune --confirm-prune --keep 10
```

Run folders and archived datasets are never pruned by this command.
