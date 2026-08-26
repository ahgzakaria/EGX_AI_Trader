"""Run a backtest that cannot be contaminated, and that records its own parents.

Two incidents motivated this, and they are the same incident twice.

`reports/backtest_results.csv` claimed 684 trades over 2,190 days for +75.67%.
Re-running the unmodified sealed engine gave 736 trades over 3,298 days for
-1.78%. The artifact was produced under conditions that no longer existed and
were recorded nowhere, because a results file does not say what made it. It was
almost used as a baseline.

Then, mid-investigation, `config/settings.json` changed underneath a run and
`reports/` gained a result nobody in the session had produced -- the Streamlit
dashboard on port 8501 writes both. A measurement was in flight against a
configuration it had not chosen.

So this module gives a run three things it never had:

* **A pinned configuration.** `SettingsManager` holds its data in memory from
  import, so a concurrent process editing the file does not leak in -- but
  `run_backtest` calls `settings.reload()` as its very first act, which re-reads
  the file and is exactly how a dashboard edit lands inside a measurement. For
  the duration of a run, `reload()` restores the pin instead of the file and
  `save()` writes nothing; both are counted in the manifest. Overrides apply to
  the in-memory copy only, so the dashboard keeps whatever the user set.
* **Isolated output.** The service writes to `reports/` and that is not changed,
  because `backtest.py` is explicit that the service owns backtest behaviour and
  the CLI must not diverge from the dashboard. Instead the previous contents are
  restored afterwards and the produced files are copied into a run directory of
  their own.
* **A manifest.** Effective config, overrides, git commit, data fingerprint, and
  before/after hashes of every output -- so a concurrent write during the run is
  visible rather than silent.

Nothing here changes trading behaviour. It changes what is knowable about a run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Invoked as `python scripts/research/isolated_backtest.py`, so the repository
# root is not on the path and `services` would not import.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REPORTS = ROOT / "reports"
EXPERIMENTS = REPORTS / "experiments"

#: What the service writes and what therefore has to be captured and restored.
OUTPUTS = (
    "backtest_results.csv",
    "backtest_statistics.csv",
    "equity_curve.csv",
    "symbol_statistics.csv",
)

#: Sections whose values decide what a backtest measures.
RECORDED_SECTIONS = ("strategy", "backtest", "data", "ai")


def _hash(path: Path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _git_state():
    def run(*args):
        try:
            return subprocess.run(
                args, cwd=ROOT, capture_output=True, text=True, timeout=30
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return {
        "commit": run("git", "rev-parse", "HEAD"),
        "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(run("git", "status", "--porcelain")),
    }


def _data_fingerprint():
    """Enough about the inputs to notice when they are not the same inputs."""
    fingerprint = {}
    for name in ("market_data_cache.sqlite", "rubix_live_market.db"):
        path = ROOT / "data" / name
        if path.exists():
            stat = path.stat()
            fingerprint[name] = {
                "bytes": stat.st_size,
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat(),
            }
    return fingerprint


def _period_stability(results_csv: Path):
    """Per-year expectancy, so an aggregate cannot hide an unstable effect.

    Disabling the trailing stop looked like a decisive win in aggregate --
    -0.046% to +0.317% per trade. Split by entry year it helped in five years
    and hurt in five, with the aggregate carried by 2017, 2020 and 2025. That
    check was done by hand, after the recommendation had already been made. It
    belongs in the run.
    """
    import csv as _csv
    import statistics as _stats
    from collections import defaultdict

    if not results_csv.exists():
        return None
    buckets = defaultdict(list)
    with results_csv.open(encoding="utf-8-sig") as handle:
        for row in _csv.DictReader(handle):
            year = (row.get("entry_date") or "")[:4]
            try:
                profit = float(row["profit_percent"])
            except (KeyError, TypeError, ValueError):
                continue
            if year:
                buckets[year].append(profit)
    if not buckets:
        return None
    out = {}
    for year in sorted(buckets):
        values = buckets[year]
        out[year] = {
            "trades": len(values),
            "avg_profit_percent": round(_stats.mean(values), 4),
            "median_profit_percent": round(_stats.median(values), 4),
            "win_rate": round(
                100.0 * sum(1 for v in values if v > 0) / len(values), 2),
        }
    positive = sum(1 for y in out.values() if y["avg_profit_percent"] > 0)
    out["_summary"] = {
        "years": len(out),
        "years_positive": positive,
        "years_negative": len(out) - positive,
    }
    return out


def _assign(data: dict, dotted: str, value):
    """Set `section.key` (or `section.key.sub`) on the in-memory settings."""
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            raise KeyError(f"no settings section '{part}' in '{dotted}'")
        node = node[part]
    leaf = parts[-1]
    if leaf not in node:
        raise KeyError(f"no setting '{leaf}' in '{dotted}'")
    before = node[leaf]
    node[leaf] = value
    return before


def _coerce(text: str):
    lowered = text.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


class IsolatedBacktest:
    """A backtest whose configuration and outputs belong to it alone."""

    def __init__(self, label: str, overrides: dict | None = None):
        self.label = label
        self.overrides = overrides or {}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.run_dir = EXPERIMENTS / f"{stamp}_{label}"
        self.manifest = {}
        self.intercepted = {"save": 0, "reload": 0}
        self._saved_settings = None
        self._saved_outputs = {}
        self._frozen = {}

    # -- configuration -------------------------------------------------

    def _pin_settings(self):
        from config.settings_manager import settings

        self._saved_settings = deepcopy(settings.data)
        applied = {}
        for dotted, value in self.overrides.items():
            applied[dotted] = {"from": _assign(settings.data, dotted, value),
                               "to": value}

        # `services.backtest_service.run_backtest` calls `settings.reload()` as
        # its first act, so every backtest re-reads the file at start. That is
        # precisely how a dashboard edit lands inside a measurement. Refusing the
        # call would break a legitimate one, so both doors become recording
        # no-ops instead: the pin survives, the file is never written, and the
        # manifest says how often each was attempted.
        self._frozen = {"save": settings.save, "reload": settings.reload}
        self.intercepted = {"save": 0, "reload": 0}
        pinned = deepcopy(settings.data)

        def pinned_save(*_args, **_kwargs):
            self.intercepted["save"] += 1

        def pinned_reload(*_args, **_kwargs):
            self.intercepted["reload"] += 1
            settings.data = deepcopy(pinned)
            return settings.data

        settings.save = pinned_save
        settings.reload = pinned_reload
        return applied

    def _restore_settings(self):
        from config.settings_manager import settings

        if self._frozen:
            settings.save = self._frozen["save"]
            settings.reload = self._frozen["reload"]
        if self._saved_settings is not None:
            settings.data = self._saved_settings

    # -- outputs -------------------------------------------------------

    def _capture_outputs(self):
        REPORTS.mkdir(exist_ok=True)
        for name in OUTPUTS:
            path = REPORTS / name
            self._saved_outputs[name] = path.read_bytes() if path.exists() else None
        return {name: _hash(REPORTS / name) for name in OUTPUTS}

    def _collect_and_restore(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        produced = {}
        for name in OUTPUTS:
            path = REPORTS / name
            if path.exists():
                shutil.copy2(path, self.run_dir / name)
                produced[name] = _hash(self.run_dir / name)
            # Put back exactly what was there before this run.
            saved = self._saved_outputs.get(name)
            if saved is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(saved)
        return produced

    # -- the run -------------------------------------------------------

    def run(self):
        from services.backtest_service import run_backtest

        started = datetime.now(timezone.utc)
        before = self._capture_outputs()
        applied = self._pin_settings()
        from config.settings_manager import settings
        effective = {
            section: deepcopy(settings.get(section))
            for section in RECORDED_SECTIONS
        }

        final, error = None, None
        try:
            for update in run_backtest():
                if update["type"] == "finished":
                    final = update
        except Exception as exc:                    # noqa: BLE001 - recorded
            error = f"{type(exc).__name__}: {exc}"
        finally:
            self._restore_settings()

        produced = self._collect_and_restore()
        finished = datetime.now(timezone.utc)

        self.manifest = {
            "label": self.label,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "elapsed_seconds": (finished - started).total_seconds(),
            "git": _git_state(),
            "overrides": applied,
            "settings_calls_intercepted": dict(self.intercepted),
            "effective_config": effective,
            "data_fingerprint": _data_fingerprint(),
            "outputs_before_run": before,
            "outputs_produced": produced,
            "period_stability": _period_stability(
                self.run_dir / "backtest_results.csv"),
            "summary": (final or {}).get("summary"),
            "symbols": (final or {}).get("symbols"),
            "signals": (final or {}).get("signals"),
            "trades": (final or {}).get("trades"),
            "error": error,
        }
        (self.run_dir / "manifest.json").write_text(
            json.dumps(self.manifest, indent=2, default=str), encoding="utf-8"
        )
        return self.manifest


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run a backtest with pinned config and isolated outputs.")
    ap.add_argument("--label", required=True,
                    help="short name for the run directory, e.g. 'baseline'")
    ap.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                    help="override a setting, e.g. --set backtest.trailing_enabled=false")
    args = ap.parse_args()

    overrides = {}
    for item in args.set:
        if "=" not in item:
            raise SystemExit(f"--set expects PATH=VALUE, got {item!r}")
        path, _, raw = item.partition("=")
        overrides[path.strip()] = _coerce(raw)

    run = IsolatedBacktest(args.label, overrides)
    manifest = run.run()

    print(f"\nrun directory: {run.run_dir.relative_to(ROOT)}")
    if manifest["overrides"]:
        print("overrides:")
        for path, change in manifest["overrides"].items():
            print(f"  {path}: {change['from']!r} -> {change['to']!r}")
    else:
        print("overrides: none (as-configured)")

    changed = [
        name for name in OUTPUTS
        if manifest["outputs_before_run"].get(name)
        and manifest["outputs_before_run"][name] == manifest["outputs_produced"].get(name)
    ]
    if changed:
        print(f"WARNING: identical to pre-run content, run may not have "
              f"written: {', '.join(changed)}")

    if manifest["error"]:
        print(f"ERROR: {manifest['error']}")
        return
    intercepted = manifest.get("settings_calls_intercepted", {})
    if intercepted.get("reload"):
        print(f"note: {intercepted['reload']} settings reload(s) intercepted; "
              "the pin held")
    summary = manifest["summary"] or {}
    for key in ("Trades", "WinRate", "ProfitFactor", "TotalReturn",
                "MaxDrawdown", "AverageProfitPercent", "AverageHoldingDays",
                "SharpeRatio", "BacktestDays"):
        if key in summary:
            print(f"  {key:22}: {summary[key]}")

    stability = manifest.get("period_stability")
    if stability:
        overall = stability.pop("_summary", {})
        print(f"\n  per-year expectancy  ({overall.get('years_positive')} of "
              f"{overall.get('years')} years positive)")
        print(f"  {'year':>8}{'n':>6}{'avg':>10}{'median':>10}{'win%':>8}")
        for year, row in stability.items():
            print(f"  {year:>8}{row['trades']:>6}"
                  f"{row['avg_profit_percent']:>+9.3f}%"
                  f"{row['median_profit_percent']:>+9.3f}%"
                  f"{row['win_rate']:>7.1f}%")


if __name__ == "__main__":
    main()
