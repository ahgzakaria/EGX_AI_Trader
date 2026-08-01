"""Run the AI pullback evaluator from existing EODHD cache files only.

No provider client is constructed and no network fallback exists in this script.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ai_pullback_research import (
    evaluate_pullback_history,
    save_pullback_research_report,
)
from core.history_frame_adapter import to_load_history_frame
from core.universe import active_universe
from providers.eodhd_adjustment import adjust
from providers.eodhd_volume_adjustment import resolve_operational_volume


DEFAULT_CACHE = ROOT / "data" / "eodhd_cache"
DEFAULT_OUTPUT = ROOT / "reports" / "audits" / "strategies" / "pullback_entry"
EOD_PATTERN = re.compile(r"^eod_(?P<symbol>.+)\.EGX\.[^.]+\.json$", re.I)
SPLIT_PATTERN = re.compile(r"^splits_(?P<symbol>.+)\.EGX\.[^.]+\.json$", re.I)


def _payload(path: Path):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, 0.0
    return raw.get("data"), float(raw.get("_fetched_epoch", 0.0) or 0.0)


def _latest_by_symbol(cache_dir: Path, pattern, prefix: str):
    selected = {}
    for path in cache_dir.glob(f"{prefix}_*.json"):
        match = pattern.match(path.name)
        if not match:
            continue
        data, fetched = _payload(path)
        symbol = match.group("symbol").upper()
        if data is not None and (symbol not in selected or fetched > selected[symbol][0]):
            selected[symbol] = (fetched, path, data)
    return selected


def _history(symbol, eod_entry, split_entry, tail_bars):
    _, eod_path, rows = eod_entry
    canonical = pd.DataFrame({
        "Date": pd.to_datetime([row.get("date") for row in rows], errors="coerce").date,
        "Open": [row.get("open") for row in rows],
        "High": [row.get("high") for row in rows],
        "Low": [row.get("low") for row in rows],
        "Close": [row.get("close") for row in rows],
        "Volume": [row.get("volume", 0) for row in rows],
    }).dropna(subset=["Date", "Open", "High", "Low", "Close"])
    splits = split_entry[2] if split_entry is not None else []
    adjusted = adjust(canonical, splits).frame
    served_volume, volume_meta = resolve_operational_volume(
        symbol, adjusted["Date"], adjusted["Raw Volume"], splits)
    adjusted = adjusted.copy()
    adjusted["Volume"] = served_volume.to_numpy()
    frame = to_load_history_frame(adjusted, symbol=symbol, purpose="current_research")
    frame = frame.tail(max(100, int(tail_bars))).copy()
    frame.attrs.setdefault("market_data", {})
    frame.attrs["market_data"].update({
        "provider": "eodhd",
        "data_domain": "CURRENT_RESEARCH_V2",
        "volume_safe_for_lookback": bool(
            volume_meta.get("volume_safe_for_lookback", True)),
        "yahoo_network_used": False,
        "offline_cache_file": eod_path.name,
        "latest_completed_session": frame.index[-1].date().isoformat(),
        "history_sufficient": len(frame) >= 80,
    })
    return frame


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-symbols", type=int, default=20)
    parser.add_argument("--symbol-offset", type=int, default=0)
    parser.add_argument("--tail-bars", type=int, default=220)
    args = parser.parse_args(argv)
    eod = _latest_by_symbol(args.cache_dir, EOD_PATTERN, "eod")
    splits = _latest_by_symbol(args.cache_dir, SPLIT_PATTERN, "splits")
    requested_universe = tuple(sorted(record.canonical_symbol for record in active_universe()))
    offset = max(0, int(args.symbol_offset))
    limit = max(1, int(args.max_symbols))
    selected = requested_universe[offset:offset + limit]
    histories = {}
    skipped = {}
    for symbol in selected:
        if symbol not in eod:
            skipped[symbol] = "NO_EODHD_CACHE"
            continue
        try:
            histories[symbol] = _history(
                symbol, eod[symbol], splits.get(symbol), args.tail_bars)
            if len(histories[symbol]) < 80:
                histories.pop(symbol, None)
                skipped[symbol] = "INSUFFICIENT_COMPLETED_HISTORY"
        except (ValueError, TypeError, KeyError) as error:
            skipped[symbol] = f"CACHE_NORMALIZATION_FAILED:{type(error).__name__}"
    report = evaluate_pullback_history(histories)
    report = replace(report, run_metadata={
        "offline_cache_only": True,
        "network_allowed": False,
        "selection": "deterministic alphabetical active-universe slice",
        "requested_universe_size": len(requested_universe),
        "requested_symbols": list(selected),
        "requested_max_symbols": limit,
        "symbol_offset": offset,
        "loaded_symbols": len(histories),
        "skipped_symbols": skipped,
        "tail_bars_per_symbol": int(args.tail_bars),
    })
    json_path, csv_path = save_pullback_research_report(report, args.output_dir)
    print(json.dumps({
        "report": str(json_path),
        "observations": str(csv_path),
        "summary": report.summary,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
