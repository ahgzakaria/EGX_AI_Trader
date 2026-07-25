"""Reproducible Phase 11 adaptive-selector research run."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtesting.config import load as load_backtest_config
from core.symbols import load_symbols
from indicators.technical import calculate_indicators
from services.backtest_service import _portfolio_result, _run_pass
from services.dataset_archive import load_archived_frames, replay_dataset
from strategy.trading_decision import TradingDecisionService
from strategy_breakout.breakout_backtest import BreakoutBacktester
from strategy_breakout.breakout_strategy import BreakoutSwingStrategy, load_breakout_config
from strategy_selector.market_classifier import MarketClassifier
from strategy_selector.selector import AdaptiveStrategySelector, load_selector_settings
from strategy_selector.selector_backtest import AdaptiveSelectorBacktester, robustness_rows
from strategy_selector.selector_report import save_selector_artifacts
from strategy_selector.selector_score import WalkForwardPerformanceLedger


BASELINE_RUN = ROOT / "reports" / "RUN_20260714_125023"
CURRENT_SCAN_RUN = ROOT / "reports" / "RUN_20260719_191032"
START = pd.Timestamp("2020-08-09")
END = pd.Timestamp("2026-06-09")


def _prepare_frames(frames):
    prepared, failures = {}, []
    for symbol, frame in frames.items():
        if len(frame) < 250:
            failures.append({"Symbol": symbol, "Error": f"insufficient history: {len(frame)}"})
            continue
        try:
            prepared[symbol] = calculate_indicators(frame.copy())
        except Exception as error:
            failures.append({"Symbol": symbol, "Error": str(error)})
    return prepared, failures


def _assert_summary(result, baseline_row, label):
    differences = []
    for key, actual in result["summary"].items():
        if key not in baseline_row.index or pd.isna(baseline_row[key]):
            continue
        expected = baseline_row[key]
        try:
            matches = abs(float(actual) - float(expected)) <= 1e-8
        except (TypeError, ValueError):
            matches = str(actual) == str(expected)
        if not matches:
            differences.append({"Metric": key, "Expected": expected, "Actual": actual})
    if differences:
        raise RuntimeError(f"Frozen {label} changed: {differences[:10]}")


def _by_signal_date(trades):
    result = defaultdict(list)
    for trade in trades:
        result[pd.Timestamp(trade.signal_date).normalize()].append(trade)
    return result


def _market_rows_by_date(frames, signal_dates, gap_threshold):
    """Extract market features once from already-calculated indicator frames."""

    wanted = set(pd.Timestamp(value).normalize() for value in signal_dates)
    rows = defaultdict(list)
    for symbol, frame in frames.items():
        if frame.empty:
            continue
        normalized_index = pd.DatetimeIndex(frame.index).normalize()
        close = frame["Close"].astype(float)
        previous_close = close.shift(1)
        change = (close / previous_close - 1) * 100
        gap = ((frame["Open"].astype(float) / previous_close - 1).abs() * 100)
        prior_high = frame["High"].shift(1).rolling(252, min_periods=20).max()
        prior_low = frame["Low"].shift(1).rolling(252, min_periods=20).min()
        atr_percent = frame["ATR_PERCENT"].astype(float)
        prior_atr_median = atr_percent.shift(1).rolling(60, min_periods=20).median()
        expansion = (atr_percent / prior_atr_median).replace([float("inf"), float("-inf")], 1).fillna(1)
        for position, timestamp in enumerate(normalized_index):
            if timestamp not in wanted:
                continue
            row = frame.iloc[position]
            rows[timestamp].append({
                "symbol": symbol,
                "change_percent": float(change.iloc[position]) if pd.notna(change.iloc[position]) else 0.0,
                "ema_aligned": bool(row["EMA20"] > row["EMA50"] > row["EMA200"]),
                "adx": float(row["ADX"]),
                "atr_percent": float(row["ATR_PERCENT"]),
                "volume_ratio": float(row["VOLUME_RATIO"]),
                "large_gap": bool(pd.notna(gap.iloc[position]) and gap.iloc[position] >= gap_threshold),
                "new_high": bool(pd.notna(prior_high.iloc[position]) and row["Close"] > prior_high.iloc[position]),
                "new_low": bool(pd.notna(prior_low.iloc[position]) and row["Close"] < prior_low.iloc[position]),
                "volatility_expansion": float(expansion.iloc[position]),
            })
    return rows


def _classifications(prepared, classic_candidates, breakout_candidates, settings):
    classic_by_date = _by_signal_date(classic_candidates)
    breakout_by_date = _by_signal_date(breakout_candidates)
    dates = sorted(set(classic_by_date) | set(breakout_by_date))
    market_rows = _market_rows_by_date(
        prepared, dates, float(settings["gap_threshold_percent"])
    )
    classifier = MarketClassifier(settings)
    classifications = {}
    for date in dates:
        classifications[date] = classifier.from_historical_rows(
            date,
            market_rows.get(date, []),
            classic_by_date.get(date, []),
            breakout_by_date.get(date, []),
            # The sealed Phase 8 CASE30 frame contains one row only.  The
            # classifier records its cross-sectional proxy explicitly.
            index_row=None,
        )
    return classifications


def _classification_records(classifications):
    rows = []
    for date, value in sorted(classifications.items()):
        snapshot = value.snapshot
        rows.append({
            "Date": date.date().isoformat(),
            "Regime": value.regime,
            "Confidence": value.confidence,
            "Reasons": " | ".join(value.reasons),
            "RobustnessTags": " | ".join(value.robustness_tags),
            **{f"Feature_{key}": item for key, item in snapshot.__dict__.items()},
        })
    return rows


def _ensure_robustness_coverage(rows, modes):
    required = (
        "BULL", "SIDEWAYS", "BEAR", "HIGH_VOLATILITY", "LOW_VOLATILITY",
        "ELECTION_PERIOD", "STRONG_RALLY", "SHARP_CORRECTION",
    )
    existing = {(row["Mode"], row["Condition"]) for row in rows}
    for mode in modes:
        for condition in required:
            if (mode, condition) not in existing:
                rows.append({
                    "Mode": mode, "Condition": condition, "Trades": 0,
                    "WinRate": 0, "NetProfit": 0, "ProfitFactor": 0,
                    "Expectancy": 0,
                })
    return rows


def _current_scan(settings, performance, run_dir):
    frames, _ = load_archived_frames(run_dir)
    classic = pd.read_csv(Path(run_dir) / "scan_results.csv")
    breakout_strategy = BreakoutSwingStrategy(load_breakout_config())
    rows = []
    for record in classic.to_dict("records"):
        symbol = record["Ticker"]
        frame = frames.get(symbol)
        if frame is None or len(frame) < 250:
            continue
        frame = calculate_indicators(frame.copy())
        breakout = breakout_strategy.evaluate(frame, len(frame) - 1)
        row = dict(record)
        row.update({
            "ClassicDecision": record["Signal"],
            "ClassicRR": record["RR"],
            "BreakoutDecision": breakout["Signal"],
            "BreakoutRR": breakout["RR"],
            "BreakoutScore": breakout["Score"],
            "BreakoutConfidence": breakout["Confidence"],
            "BreakoutEdgeScore": breakout["EdgeScore"],
            "Data": frame,
            "AIFeatures": frame.iloc[-1],
        })
        rows.append(row)
    ledger = WalkForwardPerformanceLedger(settings, performance)
    selector = AdaptiveStrategySelector(settings, ledger)
    classification = selector.classify_scan(rows)
    output = []
    for row in rows:
        selected = selector.select_symbol(
            {
                "Signal": row["ClassicDecision"], "Score": row["Score"],
                "Confidence": row["Confidence"], "RR": row["ClassicRR"],
            },
            {
                "Signal": row["BreakoutDecision"], "Score": row["BreakoutScore"],
                "Confidence": row["BreakoutConfidence"], "RR": row["BreakoutRR"],
                "EdgeScore": row["BreakoutEdgeScore"],
            },
            classification,
        )
        output.append({
            "Ticker": row["Ticker"],
            "ClassicDecision": row["ClassicDecision"],
            "ClassicRR": row["ClassicRR"],
            "BreakoutDecision": row["BreakoutDecision"],
            "BreakoutRR": row["BreakoutRR"],
            **selected,
        })
    return classification, pd.DataFrame(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-run", type=Path, default=BASELINE_RUN)
    parser.add_argument("--current-scan-run", type=Path, default=CURRENT_SCAN_RUN)
    args = parser.parse_args()

    settings = load_selector_settings()
    portfolio_config = load_backtest_config()
    frames, manifest = load_archived_frames(args.baseline_run.resolve())
    prepared, preparation_failures = _prepare_frames(frames)
    symbols = load_symbols(ROOT / "data" / "symbols.csv")

    with replay_dataset(frames):
        classic_candidates, classic_failures, _, _ = _run_pass(
            symbols,
            TradingDecisionService(mode=TradingDecisionService.STRATEGY_ONLY),
            START,
            END,
        )
    classic_result = _portfolio_result(
        classic_candidates, portfolio_config, "CLASSIC_STRATEGY"
    )
    classic_result["failures"] = classic_failures

    breakout_result = BreakoutBacktester(
        load_breakout_config(), portfolio_config
    ).run(prepared, START, END)
    breakout_result["failures"] = [
        *preparation_failures, *breakout_result.get("failures", [])
    ]

    phase8 = pd.read_csv(args.baseline_run / "phase5_current_data_v2.csv")
    _assert_summary(
        classic_result, phase8[phase8["Mode"] == "STRATEGY_ONLY"].iloc[0], "Classic"
    )
    phase10 = pd.read_csv(ROOT / "reports" / "phase10_breakout_summary.csv")
    _assert_summary(
        breakout_result, phase10[phase10["Mode"] == "BREAKOUT_SWING"].iloc[0],
        "BREAKOUT_SWING",
    )

    classifications = _classifications(
        prepared, classic_candidates, breakout_result["candidates"], settings
    )
    adaptive = AdaptiveSelectorBacktester(settings, portfolio_config).run(
        classic_candidates, breakout_result["candidates"], classifications
    )
    results = {
        "CLASSIC_STRATEGY": classic_result,
        "BREAKOUT_SWING": breakout_result,
        "ADAPTIVE_SELECTOR": adaptive,
    }
    summary = [{"Mode": mode, **result["summary"]} for mode, result in results.items()]
    robustness = _ensure_robustness_coverage(
        robustness_rows(results, classifications), results.keys()
    )
    market_records = _classification_records(classifications)
    save_selector_artifacts(
        ROOT / "reports", summary, adaptive["decisions"], market_records,
        robustness, adaptive["performance"],
    )

    # Selected false-breakout diagnostics are joined without modifying the
    # now-frozen BREAKOUT_SWING trade objects.
    record_map = {
        (record.symbol, record.signal_date, record.entry_date, record.exit_date): record
        for record in breakout_result["records"]
    }
    adaptive_breakouts = [
        trade for trade in adaptive["executed"]
        if getattr(trade, "selector_preferred_strategy", "") == "BREAKOUT_SWING"
    ]
    adaptive_records = [
        record_map.get((trade.symbol, trade.signal_date, trade.entry_date, trade.exit_date))
        for trade in adaptive_breakouts
    ]
    adaptive_records = [record for record in adaptive_records if record is not None]
    adaptive_false_rate = round(
        sum(record.false_breakout for record in adaptive_records) / len(adaptive_records) * 100,
        2,
    ) if adaptive_records else 0.0

    current_classification, current = _current_scan(
        settings, adaptive["performance"], args.current_scan_run.resolve()
    )
    current.to_csv(ROOT / "reports" / "phase11_current_scan.csv", index=False)
    buys = current[current["FinalRecommendation"].isin(["BUY_CLASSIC", "BUY_BREAKOUT"])]
    preferred_counts = current["PreferredStrategy"].value_counts().to_dict()
    current_preferred = (
        max(preferred_counts, key=preferred_counts.get) if preferred_counts else "NONE"
    )
    chronology_valid = all(
        bool(row.get("ChronologyValid")) for row in adaptive["decisions"]
    )
    diagnostics = {
        "baseline_run": args.baseline_run.name,
        "dataset_hash": manifest.get("dataset_hash"),
        "date_range": {"start": str(START.date()), "end": str(END.date())},
        "classic_identical": True,
        "breakout_identical": True,
        "chronology_valid": chronology_valid,
        "adaptive_selected_breakout_trades": len(adaptive_records),
        "adaptive_false_breakout_rate": adaptive_false_rate,
        "standalone_breakout_false_breakout_rate": breakout_result["summary"]["FalseBreakoutRate"],
        "current_market_regime": current_classification.regime,
        "current_market_confidence": current_classification.confidence,
        "current_market_reason": list(current_classification.reasons),
        "current_preferred_strategy": current_preferred,
        "current_preferred_counts": preferred_counts,
        "current_adaptive_buys": len(buys),
        "current_buy_classic": int((current["FinalRecommendation"] == "BUY_CLASSIC").sum()),
        "current_buy_breakout": int((current["FinalRecommendation"] == "BUY_BREAKOUT").sum()),
        "decision_support_only": True,
        "enabled_by_default": False,
    }
    (ROOT / "reports" / "phase11_selector_diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(pd.DataFrame(summary).to_string(index=False))
    print(json.dumps(diagnostics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
