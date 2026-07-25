"""Build a transparent, inspectable EGX_AI_Trader_RC1 source release."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORIES = (
    "ai", "backtesting", "config", "core", "dashboard", "forward_testing",
    "indicators", "portfolio", "providers", "services", "strategy", "scalping",
    "decision_support", "scripts", "tests",
)
ROOT_FILES = (
    "app.py", "backtest.py", "pytest.ini", "requirements.txt", "requirements-lock.txt", "requirements-dev.txt",
    "PHASE8_PRODUCTION_HARDENING_REPORT.md", "DATA_REPRODUCIBILITY_POLICY.md",
    "RUBIX_OPERATIONS_GUIDE.md", "BACKUP_AND_RESTORE_GUIDE.md", "RELEASE_RC1_GUIDE.md",
    "SCALPING_MODULE_IMPLEMENTATION_REPORT.md",
    "PHASE9_DECISION_SUPPORT_REPORT.md",
)
EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", "venv", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log"}


def allowed(path: Path) -> bool:
    return not any(part in EXCLUDED_NAMES for part in path.parts) and path.suffix.lower() not in EXCLUDED_SUFFIXES


def build_release(destination: Path, replace=False) -> Path:
    destination = destination.resolve()
    if destination.exists():
        if not replace:
            raise FileExistsError(f"Release already exists: {destination}")
        shutil.rmtree(destination)
    staging = destination.with_name(destination.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        for directory in SOURCE_DIRECTORIES:
            source_root = PROJECT_ROOT / directory
            if not source_root.exists():
                continue
            for source in source_root.rglob("*"):
                relative = source.relative_to(PROJECT_ROOT)
                if source.is_file() and allowed(relative):
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        for filename in ROOT_FILES:
            source = PROJECT_ROOT / filename
            if source.is_file():
                shutil.copy2(source, staging / filename)
        for directory in ("data", "logs", "reports", "backups"):
            (staging / directory).mkdir(exist_ok=True)
            (staging / directory / ".gitkeep").write_text("", encoding="utf-8")

        settings = json.loads((PROJECT_ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        settings["market_data"]["rubix_db_path"] = "data/rubix_live_market.db"
        settings["market_data"]["tickerchart_adapter_path"] = ""
        settings["market_data"]["tickerchart_db_path"] = ""
        settings["market_data"]["tickerchart_local_url"] = ""
        (staging / "config" / "settings.example.json").write_text(
            json.dumps(settings, indent=2), encoding="utf-8"
        )
        # Runtime settings are intentionally not shipped; setup copies the
        # non-secret template on the target machine.
        (staging / "config" / "settings.json").unlink(missing_ok=True)
        manifest = {
            "release": "EGX_AI_Trader_RC1", "transparent_source": True,
            "opaque_executable": False, "contains_secrets": False,
            "files": sorted(
                path.relative_to(staging).as_posix()
                for path in staging.rglob("*") if path.is_file()
            ),
        }
        (staging / "RELEASE_MANIFEST.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        os.replace(staging, destination)
        return destination
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--destination", default=str(PROJECT_ROOT / "EGX_AI_Trader_RC1")
    )
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    print(build_release(Path(args.destination), args.replace))


if __name__ == "__main__":
    main()
