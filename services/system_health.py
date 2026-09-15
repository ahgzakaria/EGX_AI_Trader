"""Unified, read-only operational health snapshot for production readiness."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3

from config.settings_manager import settings
from core.live_feed import history_source_label, retired_provider_health
from services.experiment_tracking import REPORTS_ROOT, RunRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sqlite_health(path: Path, *, deep: bool = False) -> dict:
    """Whether one database can be opened and read, or the full integrity scan.

    ``deep=False`` reads the schema and one page of the catalogue: enough to
    catch a file that is missing, locked, truncated or not a database at all,
    and it is over in milliseconds.

    ``deep=True`` is ``PRAGMA integrity_check``, which walks every page. The
    Rubix store on this machine is 7.7 GB and that walk alone measured **140
    seconds**; it ran on every render of the System Health page, which is the
    page somebody opens when something is already wrong.

    Measured 2026-09-12, whole call: 328s before, of which ~170s was the
    integrity checks across the three databases and ~144s was
    ``provider_health`` reading the Rubix store. Neither runs on a default
    render now: the store was retired on 2026-09-10 and is not opened.

    The returned ``check`` field names which one ran, so nothing can read a
    quick probe as a clean integrity scan.
    """
    if not path.is_file():
        return {"path": str(path), "status": "MISSING", "bytes": 0, "check": "none"}
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            if deep:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            else:
                connection.execute("PRAGMA schema_version").fetchone()
                connection.execute(
                    "SELECT name FROM sqlite_master LIMIT 1").fetchone()
                integrity = "not checked"
        return {
            "path": str(path),
            "status": "HEALTHY" if (integrity == "ok" or not deep) else "FAILED",
            "integrity": integrity,
            "check": "integrity" if deep else "quick",
            "bytes": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(),
        }
    except sqlite3.Error as error:
        return {"path": str(path), "status": "FAILED", "error": str(error),
                "check": "integrity" if deep else "quick",
                "bytes": path.stat().st_size}


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _experiment_health() -> dict:
    runs = RunRepository.list_runs()
    completed = [run for run in runs if run.get("status") == "COMPLETED"]
    replay_ready = []
    for run in completed:
        if not run.get("replay_ready"):
            continue
        directory = REPORTS_ROOT / str(run.get("run_id"))
        manifest = _read_json(directory / "dataset" / "MANIFEST.json")
        notes = _read_json(directory / "notes.json")
        if manifest.get("format") == "npz" and notes.get("metric_match") is not False:
            replay_ready.append(run)
    running = [run for run in runs if run.get("status") == "RUNNING"]
    scans = [run for run in completed if run.get("run_type") == "SCAN"]
    return {
        "total_runs": len(runs), "completed_runs": len(completed),
        "running_runs": len(running), "replay_ready_runs": len(replay_ready),
        "archived_dataset_coverage_pct": round(len(replay_ready) / len(completed) * 100, 2) if completed else 0,
        "last_run_id": runs[0].get("run_id") if runs else None,
        "last_run_at": runs[0].get("created_at") if runs else None,
        "last_completed_scan_id": scans[0].get("run_id") if scans else None,
        "last_completed_scan_at": scans[0].get("completed_at") if scans else None,
    }


def _latest_backup() -> dict:
    backups = sorted((PROJECT_ROOT / "backups").glob("BACKUP_*"), reverse=True)
    if not backups:
        return {"status": "MISSING", "path": None, "created_at": None}
    latest = backups[0]
    manifest = _read_json(latest / "BACKUP_MANIFEST.json")
    return {
        "status": manifest.get("status", "UNKNOWN"), "path": str(latest),
        "created_at": manifest.get("created_at"),
    }


def collect_system_health(streamlit_running=False, *, deep=False) -> dict:
    """The operational picture. ``deep`` walks every page of every database.

    Quick by default. The deep scan is minutes on a multi-gigabyte store and
    this is called on every render of a page read during an incident; the
    caller asks for it explicitly, and every database record says which check
    it got.
    """
    settings.reload()
    cfg = settings.get("market_data")
    # The Rubix feed was retired on 2026-09-10. Its 7.7 GB database is not opened
    # here any more, for a quick check or an integrity scan: nothing uses it.
    live = retired_provider_health()
    forward_db = _sqlite_health(PROJECT_ROOT / "data" / "forward_testing.db", deep=deep)
    cache_db = _sqlite_health(PROJECT_ROOT / cfg.get("cache_path", "data/market_data_cache.sqlite"), deep=deep)
    disk = shutil.disk_usage(PROJECT_ROOT)
    disk_free_pct = round(disk.free / disk.total * 100, 2) if disk.total else 0
    experiments = _experiment_health()
    backup = _latest_backup()

    # HEALTHY and DEGRADED were verdicts on the Rubix feed's freshness. With no
    # live feed a working system is a research system on completed daily closes.
    if forward_db.get("status") == "FAILED" or disk_free_pct < 1:
        state = "FAILED"
    elif cache_db.get("status") == "HEALTHY":
        state = "RESEARCH_ONLY"
    else:
        state = "NOT_READY"

    return {
        "state": state,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "safety": {
            "real_money_approved": False,
            "yahoo_live_actionable": False,
            "message": "No live feed: prices are completed session closes. Real-money readiness is not approved.",
        },
        "provider": {
            "requested": "eodhd_plus_mubasher",
            "actual": history_source_label(["eodhd_plus_mubasher"]),
            "fallback_active": False,
            "live_quotes": live,
        },
        "yahoo": {"status": "CACHE_AVAILABLE" if cache_db.get("status") == "HEALTHY" else "UNAVAILABLE", "live": False},
        "market_cache": cache_db,
        "forward_testing_database": forward_db,
        "experiment_tracking": experiments,
        "replay_readiness": {
            "ready_runs": experiments["replay_ready_runs"],
            "coverage_pct": experiments["archived_dataset_coverage_pct"],
        },
        "disk": {
            "total_bytes": disk.total, "free_bytes": disk.free,
            "free_percent": disk_free_pct,
        },
        "backup": backup,
        "streamlit": {
            "status": "RUNNING_IN_CURRENT_PROCESS" if streamlit_running else "NOT_CONFIRMED"
        },
    }
