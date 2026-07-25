"""Lightweight, immutable experiment tracking for scans and backtests.

The tracker deliberately wraps the trading engine instead of participating in
its calculations.  It records inputs and copies outputs after a run, which
keeps strategy behaviour bit-for-bit independent from experiment management.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from copy import deepcopy
from datetime import date, datetime, timezone
from importlib import metadata as package_metadata
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from config.settings_manager import settings
from services.dataset_archive import (
    DatasetArchive,
    activate_archive,
    deactivate_archive,
    sha256_file,
)


REPORTS_ROOT = Path("reports")
RUN_PREFIX = "RUN_"
SCHEMA_VERSION = 1
DEFAULT_RANDOM_SEED = 42
_RUN_ID_LOCK = threading.Lock()


def _json_value(value: Any) -> Any:
    """Convert pandas/numpy/datetime values into stable JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except (TypeError, ValueError):
            pass
    if pd.isna(value):
        return None
    return str(value)


def _atomic_json(path: Path, payload: dict) -> None:
    """Write metadata atomically so interrupted runs never leave invalid JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_value(payload), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _combined_version(paths: Iterable[Path]) -> str:
    """Create a short content version without depending on Git availability."""
    digest = hashlib.sha256()
    found = False
    for path in sorted(paths, key=lambda item: str(item).lower()):
        if path.is_file():
            found = True
            digest.update(str(path).replace("\\", "/").encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16] if found else "unavailable"


def _git_hash() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _installed_packages() -> dict[str, str]:
    """Record exact Python dependencies without invoking a package manager."""
    packages = {}
    try:
        for distribution in package_metadata.distributions():
            name = distribution.metadata.get("Name")
            if name:
                packages[name] = distribution.version
    except Exception:
        # Environment capture must never make a valid trading run fail.
        return {}
    return dict(sorted(packages.items(), key=lambda item: item[0].lower()))


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _report_state() -> dict[str, tuple[int, int]]:
    """Snapshot legacy artifacts so only files produced by this run are copied."""
    state = {}
    if not REPORTS_ROOT.exists():
        return state
    for path in REPORTS_ROOT.iterdir():
        if path.is_file():
            stat = path.stat()
            state[path.name] = (stat.st_mtime_ns, stat.st_size)
    return state


def _next_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    base = f"{RUN_PREFIX}{now:%Y%m%d_%H%M%S}"
    with _RUN_ID_LOCK:
        candidate = base
        suffix = 1
        while (REPORTS_ROOT / candidate).exists():
            candidate = f"{base}_{suffix:02d}"
            suffix += 1
        return candidate


class ExperimentRun:
    """Lifecycle and artifact manager for one immutable experiment run."""

    def __init__(
        self, run_type: str, mode: str, symbols: Iterable[str] = (),
        settings_snapshot: dict | None = None,
    ):
        if settings_snapshot is None:
            settings.reload()
            settings_snapshot = settings.data
        self.settings_snapshot = deepcopy(settings_snapshot)
        self.symbols = list(symbols)
        self.run_id = _next_run_id()
        self.run_dir = REPORTS_ROOT / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.started_at = datetime.now(timezone.utc).astimezone()
        self._started_clock = time.perf_counter()
        self._legacy_state = _report_state()
        self.metadata = self._initial_metadata(run_type, mode)
        self._write_snapshots()
        # Data capture is passive and starts only after immutable configuration
        # snapshots exist.  The trading engine never reads from this object.
        self.dataset_archive = DatasetArchive(
            self.run_dir, self.settings_snapshot, self.symbols
        )
        self._archive_token = activate_archive(self.dataset_archive)
        self._archive_closed = False

    def _initial_metadata(self, run_type: str, mode: str) -> dict:
        backtest = self.settings_snapshot.get("backtest", {})
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "run_type": str(run_type).upper(),
            "status": "RUNNING",
            "created_at": self.started_at.isoformat(),
            "completed_at": None,
            "execution_time_seconds": None,
            "mode": mode,
            "strategy": "UNIFIED_TRADING_DECISION",
            "ai_mode": (
                mode
                if str(run_type).upper() == "BACKTEST"
                else "LIVE_ADVISORY" if mode == "LIVE_SCAN" else mode
            ),
            "walk_forward_status": "NOT_REQUIRED" if mode == "STRATEGY_ONLY" else "PENDING",
            "date_range": {"start": None, "end": None},
            "capital": backtest.get("initial_capital"),
            "commission": backtest.get("commission"),
            "slippage": backtest.get("slippage"),
            "random_seed": DEFAULT_RANDOM_SEED,
            "number_of_symbols": len(self.symbols),
            "successful_symbols": None,
            "failed_symbols": None,
            "failed_symbol_details": [],
            "git_hash": _git_hash(),
            "process_id": os.getpid(),
            "versions": self._versions(),
            "metrics": {},
            "artifacts": [],
            "error": None,
        }

    def _versions(self) -> dict:
        model = Path("ai/models/trading_model.pkl")
        return {
            "ai_model_version": _sha256(model)[:16] if _sha256(model) else "unavailable",
            "walk_forward_version": _combined_version([Path("ai/walk_forward.py")]),
            "dataset_version": _combined_version([
                Path("data/symbols.csv"), Path("core/data_loader.py"),
            ]),
            "feature_version": _combined_version([
                Path("ai/features.py"), Path("ai/dataset.py"),
                Path("indicators/technical.py"),
            ]),
        }

    def _write_snapshots(self) -> None:
        _atomic_json(self.run_dir / "settings_snapshot.json", self.settings_snapshot)
        _atomic_json(self.run_dir / "environment.json", {
            "python_version": sys.version,
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "packages": _installed_packages(),
        })
        model_metadata = _load_json(Path("ai/models/trading_model.metadata.json"))
        _atomic_json(self.run_dir / "model_info.json", {
            "model_path": "ai/models/trading_model.pkl",
            "model_sha256": _sha256(Path("ai/models/trading_model.pkl")),
            "metadata": model_metadata,
            **self.metadata["versions"],
        })
        _atomic_json(self.run_dir / "run_metadata.json", self.metadata)

    def save_dataframe(self, filename: str, frame: pd.DataFrame) -> Path:
        """Persist a run-owned CSV; existing artifacts are never overwritten."""
        destination = self.run_dir / Path(filename).name
        if destination.exists():
            raise FileExistsError(f"Run artifact already exists: {destination.name}")
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        frame.to_csv(temporary, index=False, encoding="utf-8-sig")
        os.replace(temporary, destination)
        return destination

    def save_records(self, filename: str, records: Iterable[dict]) -> Path:
        safe = []
        for record in records:
            safe.append({
                key: (
                    json.dumps(_json_value(value), ensure_ascii=False)
                    if isinstance(value, (dict, list, tuple, set))
                    else _json_value(value)
                )
                for key, value in record.items()
                if key not in {"Data", "AIFeatures"}
            })
        return self.save_dataframe(filename, pd.DataFrame(safe))

    def save_backtest_artifacts(self, result: dict) -> None:
        """Save canonical reports directly from the completed in-memory result."""
        trades = pd.DataFrame([trade.__dict__ for trade in result.get("executed", [])])
        self.save_dataframe("trade_log.csv", trades)
        self.save_dataframe("summary.csv", pd.DataFrame([result.get("summary", {})]))

        initial = float(self.metadata.get("capital") or 0)
        equity_rows = [{"Date": None, "Equity": initial}]
        equity = initial
        if not trades.empty and "exit_date" in trades and "portfolio_profit" in trades:
            ordered = trades.copy()
            ordered["exit_date"] = pd.to_datetime(ordered["exit_date"], errors="coerce")
            ordered = ordered.sort_values("exit_date")
            for _, trade in ordered.iterrows():
                equity += float(trade.get("portfolio_profit", 0) or 0)
                equity_rows.append({
                    "Date": trade["exit_date"], "Equity": round(equity, 2),
                })
            daily = ordered.groupby(ordered["exit_date"].dt.date, dropna=True)[
                "portfolio_profit"
            ].sum().reset_index(name="Profit")
            daily["ReturnPercent"] = daily["Profit"] / initial * 100 if initial else 0
            self.save_dataframe("daily_returns.csv", daily)
            yearly = ordered.groupby(ordered["exit_date"].dt.year, dropna=True)[
                "portfolio_profit"
            ].agg(["count", "sum"]).reset_index()
            yearly.columns = ["Year", "Trades", "NetProfit"]
            yearly["ReturnPercent"] = yearly["NetProfit"] / initial * 100 if initial else 0
            self.save_dataframe("yearly_metrics.csv", yearly)
            start = ordered["entry_date"].min() if "entry_date" in ordered else None
            end = ordered["exit_date"].max()
            self.metadata["date_range"] = {
                "start": _json_value(start), "end": _json_value(end),
            }
        else:
            self.save_dataframe("daily_returns.csv", pd.DataFrame(
                columns=["exit_date", "Profit", "ReturnPercent"]
            ))
            self.save_dataframe("yearly_metrics.csv", pd.DataFrame(
                columns=["Year", "Trades", "NetProfit", "ReturnPercent"]
            ))
        self.save_dataframe("equity_curve.csv", pd.DataFrame(equity_rows))

    def _capture_legacy_reports(self) -> None:
        """Copy reports emitted by unchanged legacy writers into this run."""
        for source in REPORTS_ROOT.iterdir():
            if not source.is_file():
                continue
            stat = source.stat()
            current = (stat.st_mtime_ns, stat.st_size)
            if self._legacy_state.get(source.name) == current:
                continue
            destination = self.run_dir / source.name
            if not destination.exists():
                shutil.copy2(source, destination)

    def complete(
        self,
        metrics: dict | None = None,
        failures: Iterable[dict] = (),
        successful_symbols: int | None = None,
        walk_forward_status: str | None = None,
        extra_metadata: dict | None = None,
    ) -> dict:
        self._capture_legacy_reports()
        failure_rows = list(failures or [])
        prediction_path = self.run_dir / "ai_walk_forward_predictions.csv"
        predictions = None
        if prediction_path.is_file():
            try:
                predictions = pd.read_csv(prediction_path)
            except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
                predictions = None
        dataset_manifest = self.dataset_archive.finalize(
            failure_rows, ai_predictions=predictions
        )
        self._close_archive()
        self.metadata.update({
            "status": "COMPLETED",
            "completed_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "execution_time_seconds": round(time.perf_counter() - self._started_clock, 3),
            "successful_symbols": successful_symbols,
            "failed_symbols": len(failure_rows),
            "failed_symbol_details": failure_rows,
            "metrics": _json_value(metrics or {}),
            "dataset_hash": dataset_manifest.get("dataset_hash"),
            "dataset_archive_status": dataset_manifest.get("status"),
            "replay_ready": bool(dataset_manifest.get("symbols_archived")),
        })
        if walk_forward_status:
            self.metadata["walk_forward_status"] = walk_forward_status
        if extra_metadata:
            self.metadata.update(_json_value(extra_metadata))
        self.metadata["artifacts"] = self._artifact_names()
        self._write_readme()
        self.metadata["artifacts"] = self._artifact_names()
        _atomic_json(self.run_dir / "run_metadata.json", self.metadata)
        self._write_run_integrity()
        self._make_completed_read_only()
        return deepcopy(self.metadata)

    def fail(self, error: Exception | str) -> None:
        if not self._archive_closed:
            self.dataset_archive.abort(str(error))
            self._close_archive()
        self._capture_legacy_reports()
        self.metadata.update({
            "status": "FAILED",
            "completed_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "execution_time_seconds": round(time.perf_counter() - self._started_clock, 3),
            "error": str(error),
        })
        self._write_readme()
        self.metadata["artifacts"] = sorted(
            path.name for path in self.run_dir.iterdir() if path.is_file()
        )
        _atomic_json(self.run_dir / "run_metadata.json", self.metadata)

    def cancel(self, reason="Run cancelled") -> None:
        """Close capture cleanly while preserving an interrupted staging area."""
        if not self._archive_closed:
            self.dataset_archive.abort(reason)
            self._close_archive()
        RunRepository.mark_interrupted(self.run_id, reason)

    def _close_archive(self) -> None:
        if not self._archive_closed:
            deactivate_archive(self._archive_token)
            self._archive_closed = True

    def _artifact_names(self) -> list[str]:
        return sorted(
            path.relative_to(self.run_dir).as_posix()
            for path in self.run_dir.rglob("*") if path.is_file()
        )

    def _write_run_integrity(self) -> None:
        """Seal every completed artifact except mutable external annotations."""
        excluded = {"RUN_INTEGRITY.json", "notes.json", "notes.md"}
        files = []
        for path in sorted(self.run_dir.rglob("*")):
            if not path.is_file() or path.name in excluded:
                continue
            files.append({
                "path": path.relative_to(self.run_dir).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            })
        _atomic_json(self.run_dir / "RUN_INTEGRITY.json", {
            "schema_version": 1,
            "run_id": self.run_id,
            "sealed_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "files": files,
        })

    def _make_completed_read_only(self) -> None:
        """Protect evidence from accidental edits; explicit deletion remains possible."""
        for path in self.run_dir.rglob("*"):
            if path.is_file() and path.name not in {"notes.json", "notes.md"}:
                try:
                    path.chmod(0o444)
                except OSError:
                    pass

    def _write_readme(self) -> None:
        metrics = self.metadata.get("metrics", {})
        lines = [
            f"# Experiment {self.run_id}", "",
            f"- Type: {self.metadata['run_type']}",
            f"- Status: {self.metadata['status']}",
            f"- Created: {self.metadata['created_at']}",
            f"- Mode: {self.metadata['mode']}",
            f"- AI mode: {self.metadata['ai_mode']}",
            f"- Walk-Forward: {self.metadata['walk_forward_status']}",
            f"- Symbols: {self.metadata['number_of_symbols']}",
            f"- Execution seconds: {self.metadata['execution_time_seconds']}",
            "", "## Results", "",
        ]
        if metrics:
            lines.extend(f"- {key}: {value}" for key, value in metrics.items())
        else:
            lines.append("No result metrics were produced.")
        lines.extend([
            "", "## Reproduction", "",
            "Use `settings_snapshot.json`, `environment.json`, `model_info.json`, "
            "`run_metadata.json`, and the `dataset/` manifest to reconstruct this experiment.",
            f"Dataset hash: `{self.metadata.get('dataset_hash') or 'unavailable'}`.",
            f"Replay ready: `{self.metadata.get('replay_ready', False)}`.", "",
            "Legacy root reports may change on later runs; files in this folder are immutable.",
        ])
        (self.run_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


class RunRepository:
    """Read-only history/compare operations plus explicitly requested lifecycle actions."""

    @staticmethod
    def list_runs() -> list[dict]:
        runs = []
        if not REPORTS_ROOT.exists():
            return runs
        for directory in REPORTS_ROOT.iterdir():
            if not directory.is_dir() or not directory.name.startswith(RUN_PREFIX):
                continue
            metadata = _load_json(directory / "run_metadata.json")
            if metadata:
                runs.append(metadata)
        return sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True)

    @staticmethod
    def get(run_id: str) -> dict:
        directory = RunRepository._safe_run_dir(run_id)
        metadata = _load_json(directory / "run_metadata.json")
        if not metadata:
            raise FileNotFoundError(f"Run metadata unavailable: {run_id}")
        return metadata

    @staticmethod
    def compare(first_id: str, second_id: str) -> dict:
        """Return comparable metrics and time-series without recalculating trades."""
        identifiers = [first_id, second_id]
        metadata = [RunRepository.get(run_id) for run_id in identifiers]
        metric_keys = [
            ("Return", "TotalReturn"), ("Drawdown", "MaxDrawdown"),
            ("Profit Factor", "ProfitFactor"), ("Sharpe", "SharpeRatio"),
            ("Sortino", "SortinoRatio"), ("Calmar", "CalmarRatio"),
            ("Trades", "Trades"), ("Win Rate", "WinRate"),
            ("Rejected Trades", "RejectedTrades"),
            ("AI Acceptance Rate", "AIAcceptanceRate"),
        ]
        rows = []
        for label, key in metric_keys:
            row = {"Metric": label}
            for run_id, item in zip(identifiers, metadata):
                row[run_id] = item.get("metrics", {}).get(key)
            rows.append(row)
        return {
            "metrics": pd.DataFrame(rows),
            "equity": RunRepository._combined_csv(identifiers, "equity_curve.csv"),
            "monthly": RunRepository._period_returns(identifiers, "M"),
            "yearly": RunRepository._period_returns(identifiers, "Y"),
        }

    @staticmethod
    def export_zip(run_id: str) -> bytes:
        directory = RunRepository._safe_run_dir(run_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=f"{run_id}/{path.relative_to(directory)}")
        return buffer.getvalue()

    @staticmethod
    def delete(run_id: str) -> None:
        """Delete one validated RUN directory; traversal and root deletion are refused."""
        directory = RunRepository._safe_run_dir(run_id)
        for path in directory.rglob("*"):
            if path.is_file():
                try:
                    path.chmod(0o666)
                except OSError:
                    pass
        shutil.rmtree(directory)

    @staticmethod
    def mark_interrupted(run_id: str, reason="Backtest cancelled from Streamlit") -> dict:
        """Finalize a RUNNING experiment after Streamlit interrupted its script."""
        directory = RunRepository._safe_run_dir(run_id)
        metadata = _load_json(directory / "run_metadata.json")
        if metadata.get("status") != "RUNNING":
            return metadata
        metadata.update({
            "status": "CANCELLED",
            "completed_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "error": str(reason),
        })
        staging = directory / "dataset.staging"
        if staging.is_dir():
            _atomic_json(staging / "ARCHIVE_STATE.json", {
                "status": "INTERRUPTED",
                "reason": str(reason),
                "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
            })
        metadata["artifacts"] = sorted(
            path.name for path in directory.iterdir() if path.is_file()
        )
        _atomic_json(directory / "run_metadata.json", metadata)
        return metadata

    @staticmethod
    def recover_stale_runs(age_hours=6, apply=False) -> list[dict]:
        """Identify orphaned RUNNING records; mutation requires explicit apply."""
        now = datetime.now(timezone.utc)
        recovered = []
        for metadata in RunRepository.list_runs():
            if metadata.get("status") != "RUNNING":
                continue
            try:
                created = datetime.fromisoformat(str(metadata.get("created_at")))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age = (now - created.astimezone(timezone.utc)).total_seconds() / 3600
            except (TypeError, ValueError):
                age = float("inf")
            pid = metadata.get("process_id")
            alive = False
            if pid:
                try:
                    os.kill(int(pid), 0)
                    alive = True
                except (OSError, ValueError):
                    alive = False
            if age < float(age_hours) or alive:
                continue
            record = {
                "run_id": metadata.get("run_id"), "age_hours": round(age, 2),
                "process_id": pid, "process_alive": alive,
                "action": "RECOVERED" if apply else "WOULD_RECOVER",
            }
            if apply:
                RunRepository.mark_interrupted(
                    metadata["run_id"], "Recovered orphaned RUNNING state after application restart"
                )
            recovered.append(record)
        return recovered

    @staticmethod
    def _safe_run_dir(run_id: str) -> Path:
        if not run_id.startswith(RUN_PREFIX) or Path(run_id).name != run_id:
            raise ValueError("Invalid Run ID")
        root = REPORTS_ROOT.resolve()
        directory = (REPORTS_ROOT / run_id).resolve()
        if directory.parent != root or not directory.is_dir():
            raise FileNotFoundError(f"Run not found: {run_id}")
        return directory

    @staticmethod
    def _combined_csv(run_ids: list[str], filename: str) -> pd.DataFrame:
        frames = []
        for run_id in run_ids:
            path = RunRepository._safe_run_dir(run_id) / filename
            if not path.exists():
                continue
            frame = pd.read_csv(path)
            frame["RunID"] = run_id
            frames.append(frame)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    @staticmethod
    def _period_returns(run_ids: list[str], frequency: str) -> pd.DataFrame:
        rows = []
        for run_id in run_ids:
            metadata = RunRepository.get(run_id)
            capital = float(metadata.get("capital") or 0)
            path = RunRepository._safe_run_dir(run_id) / "trade_log.csv"
            if not path.exists():
                continue
            frame = pd.read_csv(path)
            if frame.empty or "exit_date" not in frame or "portfolio_profit" not in frame:
                continue
            dates = pd.to_datetime(frame["exit_date"], errors="coerce")
            periods = dates.dt.to_period(frequency).astype(str)
            grouped = frame.assign(Period=periods).groupby("Period")["portfolio_profit"].sum()
            rows.extend({
                "RunID": run_id,
                "Period": period,
                "NetProfit": value,
                "ReturnPercent": value / capital * 100 if capital else None,
            } for period, value in grouped.items())
        return pd.DataFrame(rows)
