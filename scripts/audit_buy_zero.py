"""Reproducible, read-only BUY=0 regression audit.

The script evaluates an archived scanner candle set with the current and
Phase-8 settings.  It never calls a provider, writes settings, or mutates the
trading engine.  Its only output is audit evidence under ``reports``.
"""

from __future__ import annotations

from copy import deepcopy
import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from indicators.technical import calculate_indicators
from services.dataset_archive import load_archived_frames
from strategy.breakout import breakout_score
from strategy.candles import candle_score
from strategy.entry import entry_signal
from strategy.filter import market_filter
from strategy.momentum import momentum_score
from strategy.quality_filter import evaluate as evaluate_quality
from strategy.support import support_resistance
from strategy.trend import trend_score
from strategy.volume import volume_score


LATEST_SCAN = PROJECT / "reports" / "RUN_20260719_191032"
STABLE_SETTINGS = (
    PROJECT / "reports" / "baselines" / "PHASE5_CURRENT_DATA_V2"
    / "settings_snapshot.json"
)
TRACE_PATH = PROJECT / "reports" / "buy_signal_full_trace.csv"
SUMMARY_PATH = PROJECT / "reports" / "buy_zero_audit_summary.json"


def _settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _cfg(settings_value: dict) -> SimpleNamespace:
    value = settings_value["strategy"]
    return SimpleNamespace(
        MIN_SCORE=value.get("min_score", 65),
        MIN_CONFIDENCE=value.get("min_confidence", 80),
        MIN_RR=value.get("min_rr", 2.0),
        MIN_TREND=value.get("min_trend", 25),
        MIN_MOMENTUM=value.get("min_momentum", 5),
        MIN_VOLUME=value.get("min_volume", 5),
        WATCH_SCORE=value.get("watch_score", 60),
        WATCH_CONFIDENCE=value.get("watch_confidence", 65),
        MARKET_TREND_ADX=value.get("market_trend_adx", 25),
        MARKET_WEAK_TREND_ADX=value.get("market_weak_trend_adx", 18),
        MAX_RR=value.get("max_rr", 100.0),
        REQUIRE_CANDLE_CONFIRMATION=value.get("require_candle_confirmation", False),
        REQUIRE_MARKET_ANALYZER=value.get("require_market_analyzer", False),
        REQUIRE_QUALITY_FILTER=value.get("require_quality_filter", False),
        QUALITY_MIN_ADX=value.get("quality_min_adx", 20),
        QUALITY_MIN_VOLUME_RATIO=value.get("quality_min_volume_ratio", 1.0),
        QUALITY_MIN_ATR_PERCENT=value.get("quality_min_atr_percent", 1.5),
        QUALITY_MIN_RESISTANCE_ROOM=value.get("quality_min_resistance_room", 3.0),
    )


def _bool_label(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _details(frame: pd.DataFrame, cfg: SimpleNamespace) -> dict:
    i = len(frame) - 1
    last = frame.iloc[i]
    market = market_filter(frame, i, cfg)
    trend = trend_score(frame, i)
    momentum = momentum_score(frame, i)
    volume = volume_score(frame, i)
    support = support_resistance(frame, i)
    entry = entry_signal(frame, i)
    candles = candle_score(frame, i)
    breakout = breakout_score(frame, i)
    quality = evaluate_quality(frame, i, support["resistance"], float(last["Close"]), cfg)

    score = sum((
        trend["score"], volume["score"], support["score"], entry["score"],
        momentum["score"], candles["score"], breakout["score"],
    ))
    confidence = min(sum((
        trend["confidence"], volume["confidence"], momentum["confidence"],
        candles["confidence"] + breakout["confidence"],
        entry["confidence"] + support["confidence"],
    )), 100)
    rr = float(entry["RR"])
    gates = {
        "MarketAnalyzer": True,  # Archived scan records SIDEWAYS/PASS for EGX30.
        "MarketRegime": bool(market["passed"]),
        "Trend": trend["score"] >= cfg.MIN_TREND,
        "Momentum": momentum["score"] >= cfg.MIN_MOMENTUM,
        "Volume": volume["score"] >= cfg.MIN_VOLUME,
        "Risk": cfg.MIN_RR <= rr <= cfg.MAX_RR,
        "QualityFilter": (not cfg.REQUIRE_QUALITY_FILTER or quality["Passed"]),
        "CandleConfirmation": (
            not cfg.REQUIRE_CANDLE_CONFIRMATION or candles["score"] > 0
        ),
        "Score": score >= cfg.MIN_SCORE,
        "Confidence": confidence >= cfg.MIN_CONFIDENCE,
    }
    required = [
        "MarketAnalyzer", "MarketRegime", "Trend", "Momentum", "Volume",
        "Risk", "QualityFilter", "CandleConfirmation", "Score", "Confidence",
    ]
    # The production engine exits immediately when the per-symbol market
    # regime fails; component values remain available here only for audit.
    if not gates["MarketAnalyzer"] or not gates["MarketRegime"]:
        signal = "AVOID"
    else:
        signal = "BUY" if all(gates[name] for name in required) else (
            "WATCH" if score >= cfg.WATCH_SCORE and confidence >= cfg.WATCH_CONFIDENCE
            else "WATCH" if score >= 40 else "AVOID"
        )
    attribution_order = [
        "Score", "Confidence", "Risk", "Trend", "Momentum", "Volume",
        "QualityFilter", "CandleConfirmation",
    ]
    failed = next((name for name in required if not gates[name]), None)
    reject = (
        "MarketFilter" if not gates["MarketRegime"]
        else next((name for name in attribution_order if not gates[name]), None)
    )
    price = float(last["Close"])
    resistance = float(support["resistance"])
    resistance_room = (
        (resistance - price) / price * 100 if resistance > 0 and price > 0 else 999.0
    )
    risk_amount = float(entry["BuyHigh"]) - float(entry["StopLoss"])
    return {
        "signal": signal, "score": score, "confidence": confidence,
        "trend": trend["score"], "momentum": momentum["score"],
        "volume": volume["score"], "candles": candles["score"],
        "breakout": breakout["score"], "support": support["support"],
        "resistance": resistance, "entry_low": entry["BuyLow"],
        "entry_high": entry["BuyHigh"], "stop": entry["StopLoss"],
        "target1": entry["Target1"], "target2": entry["Target2"],
        "rr": rr, "risk_amount": risk_amount,
        "risk_percent": risk_amount / float(entry["BuyHigh"]) * 100
        if float(entry["BuyHigh"]) else None,
        "atr": float(last["ATR"]), "atr_percent": float(last["ATR_PERCENT"]),
        "adx": float(last["ADX"]), "volume_ratio": float(last["VOLUME_RATIO"]),
        "resistance_room": resistance_room, "gates": gates,
        "failed_gate": failed, "reject_reason": reject,
        "quality_checks": quality["Checks"],
        "market_regime": market["regime"],
        "ema20": float(last["EMA20"]), "ema50": float(last["EMA50"]),
        "ema200": float(last["EMA200"]),
        "ema_alignment": (
            "EMA20 > EMA50 > EMA200"
            if last["EMA20"] > last["EMA50"] > last["EMA200"]
            else "PARTIAL / NOT ALIGNED"
        ),
        "return_20d_percent": (
            (float(last["Close"]) / float(frame["Close"].iloc[max(0, i - 20)]) - 1) * 100
            if float(frame["Close"].iloc[max(0, i - 20)]) else None
        ),
    }


def _counterfactual(rows: list[dict], excluded: set[str]) -> int:
    required = [
        "MarketAnalyzer", "MarketRegime", "Trend", "Momentum", "Volume",
        "Risk", "QualityFilter", "CandleConfirmation", "Score", "Confidence",
    ]
    return sum(
        all(row["current"]["gates"][name] for name in required if name not in excluded)
        for row in rows
    )


def _funnel(rows: list[dict]) -> list[dict]:
    order = [
        "MarketAnalyzer", "MarketRegime", "Trend", "Momentum", "Volume",
        "Risk", "QualityFilter", "CandleConfirmation", "Score", "Confidence",
    ]
    remaining = list(rows)
    result = [{"gate": "SymbolsAnalyzed", "passed": len(remaining), "failed": 0}]
    total = len(rows)
    for name in order:
        passed = [row for row in remaining if row["current"]["gates"][name]]
        result.append({
            "gate": name, "passed": len(passed), "failed": len(remaining) - len(passed),
            "percent_of_total": round(len(passed) / total * 100, 2) if total else 0,
        })
        remaining = passed
    return result


def main() -> None:
    observed_settings = _settings(LATEST_SCAN / "settings_snapshot.json")
    current_settings = _settings(PROJECT / "config" / "settings.json")
    stable_settings = _settings(STABLE_SETTINGS)
    observed = pd.read_csv(LATEST_SCAN / "scan_results.csv").set_index("Ticker")
    frames, manifest = load_archived_frames(LATEST_SCAN)
    current_cfg, stable_cfg = _cfg(current_settings), _cfg(stable_settings)
    rows = []
    trace = []

    for ticker, saved in observed.iterrows():
        raw = frames[ticker]
        metadata = deepcopy(raw.attrs.get("market_data", {}))
        frame = calculate_indicators(raw.copy())
        frame.attrs["market_data"] = metadata
        current = _details(frame, current_cfg)
        stable = _details(frame, stable_cfg)
        saved_trace = ast.literal_eval(saved["DecisionTrace"])
        rows.append({"ticker": ticker, "current": current, "stable": stable})
        trace.append({
            "ticker": ticker,
            "final_signal": current["signal"],
            "observed_run_signal": saved["Signal"],
            "stable_signal": stable["signal"],
            "score": current["score"], "confidence": current["confidence"],
            "trend_score": current["trend"], "momentum_score": current["momentum"],
            "volume_score": current["volume"], "candle_score": current["candles"],
            "breakout_score": current["breakout"], "rr": current["rr"],
            "entry_low": current["entry_low"], "entry_high": current["entry_high"],
            "stop": current["stop"], "target1": current["target1"],
            "target2": current["target2"], "calculated_risk": current["risk_amount"],
            "calculated_risk_percent": current["risk_percent"],
            "allowed_risk": "N/A - gate is MIN_RR <= RR <= MAX_RR",
            "support": current["support"], "resistance": current["resistance"],
            "resistance_room_percent": current["resistance_room"],
            "atr": current["atr"], "atr_percent": current["atr_percent"],
            "adx": current["adx"], "volume_ratio": current["volume_ratio"],
            "ema20": current["ema20"], "ema50": current["ema50"],
            "ema200": current["ema200"], "ema_alignment": current["ema_alignment"],
            "return_20d_percent": current["return_20d_percent"],
            "current_live_quote": saved.get("LiveQuoteLast"),
            "current_live_bid": saved.get("LiveQuoteBid"),
            "current_live_ask": saved.get("LiveQuoteAsk"),
            "live_spread_percent": saved.get("LiveQuoteSpreadPercent"),
            "live_quote_age_seconds": saved.get("DataAgeSeconds"),
            "last_completed_daily_close": float(frame["Close"].iloc[-1]),
            "last_completed_daily_candle": saved.get("LatestCompletedCandle"),
            "live_quote_used_in_strategy": False,
            "failed_gate": current["failed_gate"],
            "rejection_reason": current["reject_reason"],
            "gate_execution_order": (
                "MarketAnalyzer > MarketRegime > component evaluation > "
                "Trend/Momentum/Volume/Risk/Quality/Candle > Score > Confidence"
            ),
            "risk_boolean_expression": (
                f"{current_cfg.MIN_RR} <= {current['rr']} <= {current_cfg.MAX_RR}"
            ),
            **{f"gate_{name.lower()}": _bool_label(value)
               for name, value in current["gates"].items()},
            **{f"observed_trace_{name.lower()}": value
               for name, value in saved_trace.items()},
        })

    trace_frame = pd.DataFrame(trace).sort_values(
        ["score", "confidence", "ticker"], ascending=[False, False, True]
    )
    trace_frame.to_csv(TRACE_PATH, index=False)

    stable_vs_current = []
    comparison_fields = ("signal", "score", "confidence", "rr", "failed_gate")
    for row in rows:
        changed = {
            field: {"stable": row["stable"][field], "current": row["current"][field]}
            for field in comparison_fields
            if row["stable"][field] != row["current"][field]
        }
        gate_changes = {
            gate: {"stable": row["stable"]["gates"][gate], "current": value}
            for gate, value in row["current"]["gates"].items()
            if row["stable"]["gates"][gate] != value
        }
        if changed or gate_changes:
            stable_vs_current.append({
                "ticker": row["ticker"], "fields": changed, "gates": gate_changes,
            })

    strong = trace_frame[
        (trace_frame["gate_marketregime"] == "PASS")
        & (trace_frame["trend_score"] >= current_cfg.MIN_TREND)
        & (trace_frame["momentum_score"] >= current_cfg.MIN_MOMENTUM)
        & (trace_frame["volume_score"] >= current_cfg.MIN_VOLUME)
    ].head(10)
    strong_records = strong[[
        "ticker", "final_signal", "last_completed_daily_close", "return_20d_percent",
        "ema_alignment", "ema20", "ema50", "ema200", "trend_score",
        "momentum_score", "volume_score", "breakout_score", "adx",
        "volume_ratio", "resistance_room_percent", "score", "confidence", "rr",
        "failed_gate", "rejection_reason",
    ]].to_dict("records")

    current_strategy = current_settings["strategy"]
    observed_strategy = observed_settings["strategy"]
    stable_strategy = stable_settings["strategy"]
    keys = sorted(set(current_strategy) | set(observed_strategy) | set(stable_strategy))
    setting_comparison = [
        {
            "setting": key,
            "observed_run": observed_strategy.get(key),
            "current_runtime": current_strategy.get(key),
            "stable_phase8": stable_strategy.get(key),
        }
        for key in keys
        if not (
            observed_strategy.get(key) == current_strategy.get(key)
            == stable_strategy.get(key)
        )
    ]

    summary = {
        "source_run": LATEST_SCAN.name,
        "dataset_hash": manifest.get("dataset_hash"),
        "symbols": len(rows),
        "latest_completed_candle_distribution": (
            trace_frame["last_completed_daily_candle"].value_counts(dropna=False).to_dict()
        ),
        "current_signal_counts": trace_frame["final_signal"].value_counts().to_dict(),
        "observed_signal_counts": trace_frame["observed_run_signal"].value_counts().to_dict(),
        "stable_signal_counts": trace_frame["stable_signal"].value_counts().to_dict(),
        "funnel": _funnel(rows),
        "independent_gate_pass_counts": {
            name: sum(row["current"]["gates"][name] for row in rows)
            for name in rows[0]["current"]["gates"]
        },
        "first_failed_gate_counts": trace_frame["failed_gate"].value_counts(dropna=False).to_dict(),
        "attributed_rejection_counts": trace_frame["rejection_reason"].value_counts(dropna=False).to_dict(),
        "counterfactual_buy_counts": {
            "production_current": _counterfactual(rows, set()),
            "without_risk": _counterfactual(rows, {"Risk"}),
            "without_candle_confirmation": _counterfactual(rows, {"CandleConfirmation"}),
            "without_quality_filter": _counterfactual(rows, {"QualityFilter"}),
            "without_market_regime": _counterfactual(rows, {"MarketRegime"}),
            "completed_daily_close_instead_of_rubix": _counterfactual(rows, set()),
            "before_provider_overlay": _counterfactual(rows, set()),
            "stable_phase8_settings": sum(
                all(value for value in row["stable"]["gates"].values()) for row in rows
            ),
        },
        "counterfactual_buy_tickers": {
            "without_risk": [row["ticker"] for row in rows if all(
                value for name, value in row["current"]["gates"].items()
                if name != "Risk"
            )],
            "without_candle_confirmation": [row["ticker"] for row in rows if all(
                value for name, value in row["current"]["gates"].items()
                if name != "CandleConfirmation"
            )],
            "without_quality_filter": [row["ticker"] for row in rows if all(
                value for name, value in row["current"]["gates"].items()
                if name != "QualityFilter"
            )],
            "without_market_regime": [row["ticker"] for row in rows if all(
                value for name, value in row["current"]["gates"].items()
                if name != "MarketRegime"
            )],
        },
        "settings_differences": setting_comparison,
        "stable_vs_current_divergences": stable_vs_current,
        "strong_stock_examples": strong_records,
        "live_overlay": {
            "rows_with_quote": int(trace_frame["current_live_quote"].notna().sum()),
            "rows_where_quote_differs_from_daily_close": int((
                (trace_frame["current_live_quote"] - trace_frame["last_completed_daily_close"]).abs()
                > 1e-12
            ).sum()),
            "rows_where_overlay_used_in_strategy": 0,
        },
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
