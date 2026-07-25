"""Run the reproducible Phase 10 Classic/BREAKOUT_SWING comparison.

The script reads immutable Phase 8 candles.  It never changes provider
routing, frozen strategy settings, or any production default.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtesting.config import load as load_backtest_config
from core.symbols import load_symbols
from indicators.technical import calculate_indicators
from services.backtest_service import _portfolio_result, _run_pass
from services.dataset_archive import load_archived_frames, replay_dataset
from strategy.trading_decision import TradingDecisionService
from strategy_breakout.breakout_backtest import (
    BreakoutBacktester,
    combine_candidate_books,
)
from strategy_breakout.breakout_strategy import (
    BreakoutSwingStrategy,
    load_breakout_config,
)


DEFAULT_BASELINE = PROJECT_ROOT / "reports" / "RUN_20260714_125023"
DEFAULT_CURRENT_SCAN = PROJECT_ROOT / "reports" / "RUN_20260719_191032"
OUTPUT_DIR = PROJECT_ROOT / "reports"
START_DATE = "2020-08-09"
END_DATE = "2026-06-09"


def _prepared_frames(frames):
    prepared = {}
    failures = []
    for symbol, frame in frames.items():
        if len(frame) < 250:
            failures.append({
                "Symbol": symbol,
                "Error": f"insufficient history ({len(frame)} bars; minimum 250)",
            })
            continue
        try:
            prepared[symbol] = calculate_indicators(frame.copy())
        except Exception as error:
            failures.append({"Symbol": symbol, "Error": str(error)})
    return prepared, failures


def _summary_row(mode, result):
    return {"Mode": mode, **dict(result["summary"])}


def _trade_row(mode, trade):
    return {
        "Mode": mode,
        "Symbol": trade.symbol,
        "SignalDate": getattr(trade, "signal_date", ""),
        "EntryDate": trade.entry_date,
        "ExitDate": trade.exit_date,
        "EntryPrice": trade.entry_price,
        "ExitPrice": trade.exit_price,
        "StopLoss": trade.stop_loss,
        "Target1": trade.target1,
        "Target2": trade.target2,
        "RR": trade.rr,
        "Score": trade.score,
        "Confidence": trade.confidence,
        "Regime": getattr(trade, "regime", ""),
        "Result": trade.result,
        "ExitReason": trade.exit_reason,
        "ProfitPerShare": trade.profit,
        "Shares": getattr(trade, "shares", 0),
        "PortfolioProfit": getattr(trade, "portfolio_profit", 0),
        "HoldingDays": getattr(trade, "holding_days", 0),
        "StrategyTag": getattr(trade, "ai_mode", mode),
    }


def _validate_classic(classic_result, baseline_path):
    baseline = pd.read_csv(baseline_path)
    baseline = baseline[baseline["Mode"] == "STRATEGY_ONLY"].iloc[0]
    differences = []
    for key, actual in classic_result["summary"].items():
        if key not in baseline.index:
            continue
        expected = baseline[key]
        if pd.isna(expected) and pd.isna(actual):
            continue
        if isinstance(actual, (int, float)):
            if abs(float(actual) - float(expected)) > 1e-8:
                differences.append({"Metric": key, "Expected": expected, "Actual": actual})
        elif str(actual) != str(expected):
            differences.append({"Metric": key, "Expected": expected, "Actual": actual})
    if differences:
        raise RuntimeError(
            "Frozen Classic metrics changed; Phase 10 stopped: "
            + json.dumps(differences[:10], default=str)
        )
    return {key: baseline[key] for key in baseline.index}


def _current_scan_comparison(run_dir, strategy):
    frames, manifest = load_archived_frames(run_dir)
    rows = []
    for symbol in sorted(frames):
        try:
            frame = calculate_indicators(frames[symbol].copy())
            breakout = strategy.evaluate(frame, len(frame) - 1)
            rows.append({
                "Ticker": symbol,
                "BreakoutDecision": breakout["Signal"],
                "BreakoutRR": breakout["RR"],
                "BreakoutScore": breakout["Score"],
                "BreakoutConfidence": breakout["Confidence"],
                "BreakoutEdgeScore": breakout["EdgeScore"],
                "BreakoutEntry": breakout["Entry"],
                "BreakoutStopLoss": breakout["StopLoss"],
                "BreakoutTarget1": breakout["Target1"],
                "BreakoutTarget2": breakout["Target2"],
                "BreakoutSetup": " | ".join(breakout["SetupTypes"]),
                "BreakoutRejectReason": breakout["RejectReason"],
            })
        except Exception as error:
            rows.append({"Ticker": symbol, "BreakoutDecision": "ERROR", "Error": str(error)})
    frame = pd.DataFrame(rows)
    classic_path = Path(run_dir) / "scan_results.csv"
    if classic_path.is_file():
        classic = pd.read_csv(classic_path, usecols=["Ticker", "Signal", "RR", "Score", "Confidence"])
        classic = classic.rename(columns={
            "Signal": "ClassicDecision", "RR": "ClassicRR",
            "Score": "ClassicScore", "Confidence": "ClassicConfidence",
        })
        frame = classic.merge(frame, on="Ticker", how="outer")
    frame["BUYOverlap"] = (
        (frame.get("ClassicDecision") == "BUY")
        & (frame.get("BreakoutDecision") == "BUY")
    )
    frame.attrs["dataset_hash"] = manifest.get("dataset_hash")
    return frame


def _regime_rows(classic, breakout, combined):
    rows = []
    for mode, result in (
        ("CLASSIC_STRATEGY", classic),
        ("BREAKOUT_SWING", breakout),
        ("COMBINED_STRATEGIES", combined),
    ):
        groups = {}
        for trade in result["executed"]:
            groups.setdefault(getattr(trade, "regime", "UNKNOWN") or "UNKNOWN", []).append(trade)
        for regime, trades in sorted(groups.items()):
            profits = [float(getattr(trade, "portfolio_profit", 0)) for trade in trades]
            gross_profit = sum(value for value in profits if value > 0)
            gross_loss = abs(sum(value for value in profits if value < 0))
            rows.append({
                "Mode": mode,
                "Regime": regime,
                "Trades": len(trades),
                "WinRate": round(sum(value > 0 for value in profits) / len(trades) * 100, 2),
                "NetProfit": round(sum(profits), 2),
                "ProfitFactor": round(gross_profit / gross_loss, 2) if gross_loss else 0,
            })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-run", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--current-scan-run", type=Path, default=DEFAULT_CURRENT_SCAN)
    args = parser.parse_args()

    baseline_run = args.baseline_run.resolve()
    frames, manifest = load_archived_frames(baseline_run)
    symbols = load_symbols(PROJECT_ROOT / "data" / "symbols.csv")
    portfolio_config = load_backtest_config()
    breakout_config = load_breakout_config()

    # Classic is executed through its unchanged engine against the sealed
    # frames, then compared field-by-field with the validated Phase 8 result.
    with replay_dataset(frames):
        classic_candidates, classic_failures, _, _ = _run_pass(
            symbols,
            TradingDecisionService(mode=TradingDecisionService.STRATEGY_ONLY),
            pd.Timestamp(START_DATE),
            pd.Timestamp(END_DATE),
        )
    classic_result = _portfolio_result(
        classic_candidates, portfolio_config, "CLASSIC_STRATEGY"
    )
    classic_result["failures"] = classic_failures
    _validate_classic(
        classic_result, baseline_run / "phase5_current_data_v2.csv"
    )

    prepared_frames, preparation_failures = _prepared_frames(frames)
    breakout_result = BreakoutBacktester(
        breakout_config, portfolio_config
    ).run(prepared_frames, START_DATE, END_DATE)
    breakout_result["failures"] = [
        *preparation_failures, *breakout_result["failures"]
    ]
    combined_result = combine_candidate_books(
        deepcopy(classic_candidates), breakout_result, portfolio_config
    )

    summary = pd.DataFrame([
        _summary_row("CLASSIC_STRATEGY", classic_result),
        _summary_row("BREAKOUT_SWING", breakout_result),
        _summary_row("COMBINED_STRATEGIES", combined_result),
    ])
    summary.to_csv(OUTPUT_DIR / "phase10_breakout_summary.csv", index=False)

    trades = []
    for mode, result in (
        ("CLASSIC_STRATEGY", classic_result),
        ("BREAKOUT_SWING", breakout_result),
        ("COMBINED_STRATEGIES", combined_result),
    ):
        trades.extend(_trade_row(mode, trade) for trade in result["executed"])
    pd.DataFrame(trades).to_csv(
        OUTPUT_DIR / "phase10_breakout_trades.csv", index=False
    )
    pd.DataFrame(_regime_rows(classic_result, breakout_result, combined_result)).to_csv(
        OUTPUT_DIR / "phase10_breakout_regime.csv", index=False
    )
    # Sector metadata is not present in the sealed Phase 8 archive.  Preserve
    # this limitation explicitly instead of guessing sector membership.
    pd.DataFrame([{
        "Sector": "UNKNOWN",
        "Status": "SECTOR_REFERENCE_DATA_NOT_AVAILABLE",
        "BreakoutTrades": len(breakout_result["executed"]),
    }]).to_csv(OUTPUT_DIR / "phase10_breakout_sector.csv", index=False)

    current = _current_scan_comparison(
        args.current_scan_run.resolve(), BreakoutSwingStrategy(breakout_config)
    )
    current.to_csv(OUTPUT_DIR / "phase10_breakout_current_scan.csv", index=False)

    classic_keys = {
        (trade.symbol, str(getattr(trade, "signal_date", "")))
        for trade in classic_candidates
    }
    breakout_keys = {
        (record.symbol, record.signal_date) for record in breakout_result["records"]
    }
    diagnostics = {
        "baseline_run": baseline_run.name,
        "baseline_dataset_hash": manifest.get("dataset_hash"),
        "date_range": {"start": START_DATE, "end": END_DATE},
        "classic_metrics_identical": True,
        "classic_failures": len(classic_failures),
        "breakout_failures": breakout_result["failures"],
        "historical_classic_buy_candidates": len(classic_candidates),
        "historical_breakout_buy_candidates": len(breakout_result["records"]),
        "historical_buy_overlap": len(classic_keys & breakout_keys),
        "current_breakout_buy": int((current["BreakoutDecision"] == "BUY").sum()),
        "current_classic_buy": int((current.get("ClassicDecision") == "BUY").sum()),
        "current_buy_overlap": int(current["BUYOverlap"].fillna(False).sum()),
        "breakout_failure_rate": breakout_result["summary"]["BreakoutFailureRate"],
        "false_breakout_rate": breakout_result["summary"]["FalseBreakoutRate"],
        "decision_support_only": True,
        "enabled_by_default": breakout_config.enabled_by_default,
    }
    (OUTPUT_DIR / "phase10_breakout_diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(summary.to_string(index=False))
    print(json.dumps(diagnostics, indent=2, default=str))


if __name__ == "__main__":
    main()
