"""Run and merge cache-only Pullback calibration batches.

This research utility deliberately has no provider client and no network fallback.
It reads completed EODHD cache files, writes compressed audit intermediates, and
can merge deterministic batches into the required calibration reports.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ai_pullback_calibration import (
    merge_research_reports,
    sensitivity_result_row,
    write_calibration_reports,
)
from core.ai_pullback_config import DEFAULT_PULLBACK_RESEARCH_CONFIG
from core.ai_pullback_research import (
    PullbackResearchReport,
    _aggregate,
    evaluate_pullback_history,
)
from core.universe import active_universe
from scripts import run_ai_pullback_research as cache_reader


DEFAULT_OUTPUT = (
    ROOT / "reports" / "audits" / "strategies" / "pullback_entry" / "calibration")

OFAT_VARIANTS = {
    "default": {},
    "default_tail_250": {},
    "pivot_radius_1": {"pivot_radius": 1},
    "pivot_radius_3": {"pivot_radius": 3},
    "impulse_atr_1_5": {"minimum_impulse_strength_atr": 1.5},
    "impulse_atr_2_5": {"minimum_impulse_strength_atr": 2.5},
    "retracement_max_50": {"healthy_retracement_maximum_percent": 50.0},
    "retracement_max_70": {"healthy_retracement_maximum_percent": 70.0},
    "healthy_atr_2_5": {"healthy_pullback_maximum_atr": 2.5},
    "healthy_atr_3_5": {"healthy_pullback_maximum_atr": 3.5},
    "support_tolerance_0_75": {"support_cluster_tolerance_atr": 0.75},
    "support_tolerance_1_0": {"support_cluster_tolerance_atr": 1.0},
    "volume_ratio_1_0": {"contracting_volume_ratio": 1.0},
    "volume_ratio_1_15": {"contracting_volume_ratio": 1.15},
    "rr_1_25": {"minimum_reward_risk": 1.25},
    "rr_1_75": {"minimum_reward_risk": 1.75},
    "combo_pivot1_rr1_25": {
        "pivot_radius": 1, "minimum_reward_risk": 1.25},
    "combo_pivot1_impulse1_5": {
        "pivot_radius": 1, "minimum_impulse_strength_atr": 1.5},
    "combo_pivot3_rr1_75": {
        "pivot_radius": 3, "minimum_reward_risk": 1.75},
}


def _write_gzip_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, default=str,
                  separators=(",", ":"))
        handle.write("\n")


def _read_gzip_json(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _report_from_dict(raw):
    raw = dict(raw)
    raw["universe_snapshot"] = tuple(raw.get("universe_snapshot", ()))
    raw["data_sources"] = tuple(tuple(item) for item in raw.get("data_sources", ()))
    for key in (
        "observations", "unmanaged_forward_returns", "managed_trades",
        "gate_pass_rates", "rejection_counts",
    ):
        raw[key] = tuple(raw.get(key, ()))
    report = PullbackResearchReport(**raw)
    horizons = tuple(report.configuration.get("research_horizons", (3, 5, 10, 20)))
    return replace(report, summary=_aggregate(
        report.observations,
        horizons,
        report.unmanaged_forward_returns,
        report.managed_trades,
    ))


def _load_histories(cache_dir, selected, tail_bars):
    eod = cache_reader._latest_by_symbol(
        cache_dir, cache_reader.EOD_PATTERN, "eod")
    splits = cache_reader._latest_by_symbol(
        cache_dir, cache_reader.SPLIT_PATTERN, "splits")
    histories = {}
    skipped = {}
    for symbol in selected:
        if symbol not in eod:
            skipped[symbol] = "NO_EODHD_CACHE"
            continue
        try:
            frame = cache_reader._history(
                symbol, eod[symbol], splits.get(symbol), tail_bars)
        except (ValueError, TypeError, KeyError) as error:
            skipped[symbol] = f"CACHE_NORMALIZATION_FAILED:{type(error).__name__}"
            continue
        if len(frame) < DEFAULT_PULLBACK_RESEARCH_CONFIG.minimum_history_bars:
            skipped[symbol] = "INSUFFICIENT_COMPLETED_HISTORY"
            continue
        histories[symbol] = frame
    return histories, skipped


def _configuration(variant, overrides_json):
    if variant not in OFAT_VARIANTS:
        raise ValueError(f"unknown variant: {variant}")
    changes = dict(OFAT_VARIANTS[variant])
    if overrides_json:
        changes.update(json.loads(overrides_json))
    return replace(DEFAULT_PULLBACK_RESEARCH_CONFIG, **changes)


def evaluate_command(args):
    universe = tuple(sorted(record.canonical_symbol for record in active_universe()))
    offset = max(0, int(args.symbol_offset))
    limit = max(1, int(args.max_symbols))
    selected = universe[offset:offset + limit]
    histories, skipped = _load_histories(args.cache_dir, selected, args.tail_bars)
    config = _configuration(args.variant, args.overrides_json)
    report = evaluate_pullback_history(histories, config=config)
    metadata = {
        "offline_cache_only": True,
        "network_allowed": False,
        "yahoo_allowed": False,
        "selection": "deterministic alphabetical active-universe slice",
        "requested_universe_size": len(universe),
        "requested_symbols": list(selected),
        "symbols_requested": len(selected),
        "loaded_symbol_names": sorted(histories),
        "symbols_with_sufficient_data": len(histories),
        "symbols_skipped": skipped,
        "symbol_offset": offset,
        "requested_max_symbols": limit,
        "tail_bars_per_symbol": int(args.tail_bars),
        "variant": args.variant,
        "variant_overrides": asdict(config),
    }
    report = replace(report, run_metadata=metadata)
    name = f"{args.variant}_offset_{offset:03d}_limit_{limit:03d}.json.gz"
    report_path = args.output_dir / "batches" / args.variant / name
    _write_gzip_json(report_path, asdict(report))
    row = sensitivity_result_row(args.variant, report, category=args.category)
    row_path = report_path.with_suffix("").with_suffix(".row.json")
    row_path.write_text(json.dumps(row, sort_keys=True, default=str) + "\n", "utf-8")
    print(json.dumps({
        "report": str(report_path),
        "row": str(row_path),
        "summary": report.summary,
        "metadata": metadata,
    }, ensure_ascii=False))
    return 0


def merge_command(args):
    batch_paths = tuple(sorted((args.output_dir / "batches" / args.base_variant).glob(
        "*.json.gz")))
    if not batch_paths:
        raise FileNotFoundError(f"no base batches for {args.base_variant}")
    reports = tuple(_report_from_dict(_read_gzip_json(path)) for path in batch_paths)
    requested_symbols = []
    skipped = {}
    for report in reports:
        requested_symbols.extend(report.run_metadata.get("requested_symbols", ()))
        skipped.update(report.run_metadata.get("symbols_skipped", {}))
    metadata = {
        "offline_cache_only": True,
        "network_allowed": False,
        "yahoo_allowed": False,
        "batch_files": [path.name for path in batch_paths],
        "symbols_requested": len(set(requested_symbols)),
        "requested_symbols": sorted(set(requested_symbols)),
        "symbols_with_sufficient_data": len({
            symbol for report in reports for symbol in report.universe_snapshot}),
        "symbols_skipped": skipped,
        "deterministic_batch_merge": True,
    }
    merged = merge_research_reports(reports, run_metadata=metadata)
    sensitivity_rows = []
    for report_path in sorted((args.output_dir / "batches").glob("*/*.json.gz")):
        if report_path.parent.name == args.base_variant:
            continue
        variant_report = _report_from_dict(_read_gzip_json(report_path))
        row_path = report_path.with_suffix("").with_suffix(".row.json")
        saved_row = (json.loads(row_path.read_text("utf-8"))
                     if row_path.exists() else {})
        variant = variant_report.run_metadata.get(
            "variant", saved_row.get("variant", report_path.parent.name))
        category = saved_row.get("category", "OFAT")
        sensitivity_rows.append(sensitivity_result_row(
            variant, variant_report, category=category))
    default_full = sensitivity_result_row(
        "default_full_history", merged, category="EXPANDED_BASELINE")
    sensitivity_rows.insert(0, default_full)
    paths = write_calibration_reports(
        merged, sensitivity_rows, output_dir=args.output_dir,
        verdict_notes=args.verdict_notes)
    _write_gzip_json(args.output_dir / "pullback_calibration_full_report.json.gz",
                     asdict(merged))
    print(json.dumps({
        "batch_count": len(batch_paths),
        "summary": merged.summary,
        "metadata": metadata,
        "reports": {key: str(value) for key, value in paths.items()},
        "sensitivity_variants": len(sensitivity_rows),
    }, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--cache-dir", type=Path, default=cache_reader.DEFAULT_CACHE)
    evaluate.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    evaluate.add_argument("--variant", default="default")
    evaluate.add_argument("--category", default="OFAT")
    evaluate.add_argument("--overrides-json")
    evaluate.add_argument("--symbol-offset", type=int, default=0)
    evaluate.add_argument("--max-symbols", type=int, default=241)
    evaluate.add_argument("--tail-bars", type=int, default=5000)
    merge = subparsers.add_parser("merge")
    merge.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    merge.add_argument("--base-variant", default="default")
    merge.add_argument("--verdict-notes")
    args = parser.parse_args(argv)
    if args.command == "evaluate":
        return evaluate_command(args)
    return merge_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
