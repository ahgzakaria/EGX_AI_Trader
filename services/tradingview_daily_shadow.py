"""Phase J: Yahoo-only vs Yahoo+validated-TradingView shadow decision comparison.

Never feeds TradingView into production decisions -- this runs the frozen
strategy twice (once per frame) purely for comparison and writes
reports/tradingview_shadow_decision_comparison.csv. In this environment there
is no real TradingView data (no CSV export, no live webhook), so every row
will honestly show "Data Valid: No" with the specific reason. Re-running after
the user supplies real data populates genuine comparisons via the same code.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from config.settings_manager import settings
from core.data_provider import provider_purpose
from indicators.technical import calculate_indicators
from providers.base_provider import ProviderError
from providers.local_cache_provider import LocalCacheProvider
from providers.tradingview_completed_daily_bridge import TradingViewCompletedDailyBridge
from services.tradingview_reconciliation import _load_tradingview_candles, _BASKET
from strategy.trading_decision import TradingDecisionService

_GATE_ORDER = (
    "MarketAnalyzer", "MarketRegime", "Trend", "Momentum", "Volume",
    "Risk", "QualityFilter", "CandleConfirmation",
)
_OUT = "reports/tradingview_shadow_decision_comparison.csv"


def _evaluate(df):
    service = TradingDecisionService(mode=TradingDecisionService.STRATEGY_ONLY)
    frame = calculate_indicators(df)
    index = len(frame) - 1
    result = service.evaluate(frame, index)
    last = frame.iloc[index]
    return result, last


def _first_changed_gate(yahoo_trace, merged_trace):
    for gate in _GATE_ORDER:
        if yahoo_trace.get(gate) != merged_trace.get(gate):
            return gate
    return "none"


def _safe_diff(a, b):
    try:
        if pd.isna(a) or pd.isna(b):
            return None
        return round(float(a) - float(b), 6)
    except (TypeError, ValueError):
        return None


def run_shadow_decision_comparison(symbols=_BASKET):
    settings.reload()
    data_cfg = settings.get("data")
    period = data_cfg.get("history_period", "10y")
    interval = data_cfg.get("interval", "1d")
    market_cfg = settings.get("market_data")
    cache = LocalCacheProvider(
        market_cfg.get("cache_path", "data/market_data_cache.sqlite"),
        source_provider=market_cfg.get("cache_source_provider", "rubix"),
    )
    bridge = TradingViewCompletedDailyBridge()
    tv_candles = _load_tradingview_candles(symbols)

    rows = []
    counters = {"decision_changed": 0, "data_valid": 0}

    with provider_purpose("scanner"):
        for symbol in symbols:
            tv_frame, tv_reason = tv_candles[symbol]
            try:
                yahoo_frame = cache.load_cached("yahoo", symbol, period, interval, allow_expired=True)
            except ProviderError:
                rows.append(_missing_yahoo_row(symbol, tv_reason))
                continue

            method = "tradingview_csv" if tv_frame is not None else "none"
            result = bridge.merge(symbol, yahoo_frame, tv_frame, tv_method=method)
            if result.data_valid:
                counters["data_valid"] += 1

            try:
                yahoo_decision, yahoo_last = _evaluate(yahoo_frame)
            except Exception as error:
                yahoo_decision, yahoo_last = {"Signal": f"ERROR:{error}"}, None

            if result.sessions_appended == 0:
                merged_decision, merged_last = yahoo_decision, yahoo_last
            else:
                try:
                    merged_decision, merged_last = _evaluate(result.frame)
                except Exception as error:
                    merged_decision, merged_last = {"Signal": f"ERROR:{error}"}, None

            changed = (
                yahoo_decision.get("Signal") != merged_decision.get("Signal")
                or yahoo_decision.get("Score") != merged_decision.get("Score")
                or yahoo_decision.get("RR") != merged_decision.get("RR")
            )
            if changed:
                counters["decision_changed"] += 1

            yahoo_trace = yahoo_decision.get("DecisionTrace", {})
            merged_trace = merged_decision.get("DecisionTrace", {})

            rows.append({
                "Symbol": symbol,
                "Yahoo Latest Date": result.yahoo_latest_date,
                "TradingView Latest Completed Date": (
                    pd.Timestamp(tv_frame.index.max()).date().isoformat()
                    if tv_frame is not None and not tv_frame.empty else None
                ),
                "Sessions Appended": result.sessions_appended,
                "TradingView Method": method,
                "Data Valid": "Yes" if result.data_valid else "No",
                "Rejection Reason": result.rejection_reason or tv_reason,
                "Yahoo Signal": yahoo_decision.get("Signal"),
                "Shadow Signal": merged_decision.get("Signal"),
                "Yahoo Score": yahoo_decision.get("Score"),
                "Shadow Score": merged_decision.get("Score"),
                "Yahoo RR": yahoo_decision.get("RR"),
                "Shadow RR": merged_decision.get("RR"),
                "Trend Difference": _safe_diff(
                    merged_decision.get("Trend"), yahoo_decision.get("Trend")
                ),
                "Momentum Difference": _safe_diff(
                    merged_decision.get("Momentum"), yahoo_decision.get("Momentum")
                ),
                "ATR Difference": _safe_diff(
                    merged_last["ATR"] if merged_last is not None and "ATR" in merged_last else None,
                    yahoo_last["ATR"] if yahoo_last is not None and "ATR" in yahoo_last else None,
                ),
                "Support Difference": _safe_diff(
                    merged_decision.get("Support"), yahoo_decision.get("Support")
                ),
                "Resistance Difference": _safe_diff(
                    merged_decision.get("Resistance"), yahoo_decision.get("Resistance")
                ),
                "Decision Changed": "Yes" if changed else "No",
                "First Changed Gate": _first_changed_gate(yahoo_trace, merged_trace),
            })

    _write(_OUT, rows)
    return {
        "symbols": len(symbols),
        "report": _OUT,
        "tradingview_data_valid_for": counters["data_valid"],
        "decision_changed": counters["decision_changed"],
    }


def _missing_yahoo_row(symbol, tv_reason):
    return {
        "Symbol": symbol, "Yahoo Latest Date": None,
        "TradingView Latest Completed Date": None, "Sessions Appended": 0,
        "TradingView Method": "none", "Data Valid": "No",
        "Rejection Reason": f"no cached Yahoo history; {tv_reason}",
        "Yahoo Signal": None, "Shadow Signal": None, "Yahoo Score": None,
        "Shadow Score": None, "Yahoo RR": None, "Shadow RR": None,
        "Trend Difference": None, "Momentum Difference": None, "ATR Difference": None,
        "Support Difference": None, "Resistance Difference": None,
        "Decision Changed": "No", "First Changed Gate": "none",
    }


def _write(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else ["Symbol"]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    import json

    print(json.dumps(run_shadow_decision_comparison(), indent=2, default=str))
