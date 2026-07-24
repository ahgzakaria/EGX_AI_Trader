"""Phase F/G: shadow comparison + Yahoo/Rubix reconciliation runner.

Shadow mode builds the merged (Yahoo + validated-newer-Rubix) history and
compares it against the existing Yahoo-only history WITHOUT feeding anything to
production decisions.  It reports indicator/decision differences and writes:

* ``reports/rubix_daily_shadow_comparison.csv``
* ``reports/rubix_yahoo_daily_reconciliation.csv``

All strategy access is read-only evaluation of the frozen engine.  Nothing here
changes routing, indicators, thresholds, or any persisted trading artifact.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from config.settings_manager import settings
from core.data_provider import provider_purpose
from core.symbols import load_symbols
from indicators.technical import calculate_indicators
from providers.base_provider import ProviderError
from providers.local_cache_provider import LocalCacheProvider
from providers.rubix_bridge_factory import build_bridge
from providers.rubix_bridge_provenance import BridgeProvenanceStore
from strategy.trading_decision import TradingDecisionService

# Last-row indicator columns worth diffing when a session is appended.
_INDICATOR_COLUMNS = ("EMA20", "EMA50", "EMA200", "RSI", "ATR", "ADX")
_SHADOW_CSV = "reports/rubix_daily_shadow_comparison.csv"
_RECON_CSV = "reports/rubix_yahoo_daily_reconciliation.csv"


def _yahoo_only_frame(cache, symbol, period, interval):
    return cache.load_cached("yahoo", symbol, period, interval, allow_expired=True)


def _decision(df):
    """Return frozen-strategy Signal/Score/RR for the last completed bar."""

    service = TradingDecisionService(mode=TradingDecisionService.STRATEGY_ONLY)
    frame = calculate_indicators(df)
    index = len(frame) - 1
    result = service.evaluate(frame, index)
    return {
        "Signal": result.get("Signal"),
        "Score": result.get("Score"),
        "RR": result.get("RR"),
        "frame": frame,
    }


def _indicator_diffs(yahoo_frame, merged_frame):
    diffs = []
    try:
        y = calculate_indicators(yahoo_frame).iloc[-1]
        m = calculate_indicators(merged_frame).iloc[-1]
    except Exception as error:  # pragma: no cover - defensive only
        return f"indicator_error:{error}"
    for column in _INDICATOR_COLUMNS:
        if column in y.index and column in m.index:
            yv, mv = y[column], m[column]
            if pd.notna(yv) and pd.notna(mv) and abs(float(yv) - float(mv)) > 1e-9:
                diffs.append(f"{column}:{float(yv):.4f}->{float(mv):.4f}")
    return " | ".join(diffs) if diffs else "none"


def run_shadow(symbols_source="data/symbols.csv", *, limit=None, record_provenance=True):
    settings.reload()
    data_cfg = settings.get("data")
    period = data_cfg.get("history_period", "10y")
    interval = data_cfg.get("interval", "1d")
    market_cfg = settings.get("market_data")
    cache = LocalCacheProvider(
        market_cfg.get("cache_path", "data/market_data_cache.sqlite"),
        source_provider=market_cfg.get("cache_source_provider", "rubix"),
    )
    bridge = build_bridge()
    symbols = load_symbols(symbols_source)
    if limit:
        symbols = symbols[:limit]

    shadow_rows = []
    recon_rows = []
    provenance_rows = []
    counters = {"appended": 0, "decision_changed": 0, "missing_yahoo": 0}

    with provider_purpose("scanner"):
        for symbol in symbols:
            try:
                yahoo_frame = _yahoo_only_frame(cache, symbol, period, interval)
            except ProviderError:
                counters["missing_yahoo"] += 1
                shadow_rows.append(_missing_row(symbol))
                continue

            result = bridge.merge(symbol, yahoo_frame)
            provenance_rows.extend(result.provenance_rows)
            recon_rows.extend(bridge.reconcile(symbol, yahoo_frame))

            yahoo_dec = _safe_decision(yahoo_frame)
            if result.sessions_appended == 0:
                merged_dec = yahoo_dec  # merged frame is identical to Yahoo.
            else:
                counters["appended"] += 1
                merged_dec = _safe_decision(result.frame)

            changed = (
                yahoo_dec.get("Signal") != merged_dec.get("Signal")
                or yahoo_dec.get("Score") != merged_dec.get("Score")
                or yahoo_dec.get("RR") != merged_dec.get("RR")
            )
            if changed:
                counters["decision_changed"] += 1

            appended_reason = _appended_reason(result)
            shadow_rows.append({
                "Symbol": symbol,
                "Yahoo Latest Date": result.yahoo_latest_date,
                "Rubix Latest Completed Date": _rubix_latest_valid(result),
                "Sessions Appended": result.sessions_appended,
                "Rubix Candle Valid": "Yes" if result.sessions_appended else "No",
                "Validation Failure Reason": appended_reason,
                "Yahoo-only Signal": yahoo_dec.get("Signal"),
                "Merged-history Signal": merged_dec.get("Signal"),
                "Yahoo-only Score": yahoo_dec.get("Score"),
                "Merged-history Score": merged_dec.get("Score"),
                "Yahoo-only RR": yahoo_dec.get("RR"),
                "Merged-history RR": merged_dec.get("RR"),
                "Indicator Differences": (
                    "none" if result.sessions_appended == 0
                    else _indicator_diffs(yahoo_frame, result.frame)
                ),
                "Decision Changed": "Yes" if changed else "No",
            })

    _write_csv(_SHADOW_CSV, shadow_rows)
    _write_recon_csv(_RECON_CSV, recon_rows)
    if record_provenance and provenance_rows:
        BridgeProvenanceStore().record(provenance_rows, run_id="shadow")

    return {
        "symbols": len(symbols),
        "shadow_report": _SHADOW_CSV,
        "reconciliation_report": _RECON_CSV,
        "reconciliation_rows": len(recon_rows),
        **counters,
    }


def _safe_decision(df):
    try:
        return _decision(df)
    except Exception as error:  # pragma: no cover - defensive only
        return {"Signal": f"ERROR:{error}", "Score": None, "RR": None}


def _rubix_latest_valid(result):
    valid_dates = [
        row["trading_date"] for row in result.provenance_rows
        if row.get("valid") and row.get("source") == "RUBIX_COMPLETED_DAILY"
    ]
    return max(valid_dates) if valid_dates else None


def _appended_reason(result):
    if result.sessions_appended:
        return "n/a (valid sessions appended)"
    reasons = sorted({
        row.get("reason") for row in result.provenance_rows if row.get("reason")
    })
    if reasons:
        return " ; ".join(reasons)
    if not result.rubix_available:
        return "Rubix unavailable"
    return "no newer completed Rubix session than Yahoo"


def _missing_row(symbol):
    return {
        "Symbol": symbol, "Yahoo Latest Date": None,
        "Rubix Latest Completed Date": None, "Sessions Appended": 0,
        "Rubix Candle Valid": "No",
        "Validation Failure Reason": "no cached Yahoo history (would download live in a real scan)",
        "Yahoo-only Signal": None, "Merged-history Signal": None,
        "Yahoo-only Score": None, "Merged-history Score": None,
        "Yahoo-only RR": None, "Merged-history RR": None,
        "Indicator Differences": "none", "Decision Changed": "No",
    }


def _write_csv(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else ["Symbol"]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_recon_csv(path, records):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "Symbol", "Trading Date", "Rubix Valid", "Reason",
        "Open Diff %", "High Diff %", "Low Diff %", "Close Diff %", "Volume Diff %",
        "Yahoo Close", "Rubix Close", "Yahoo Volume", "Rubix Volume",
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({
                "Symbol": record.symbol,
                "Trading Date": record.trading_date,
                "Rubix Valid": "Yes" if record.rubix_valid else "No",
                "Reason": record.reason,
                "Open Diff %": record.open_diff_pct,
                "High Diff %": record.high_diff_pct,
                "Low Diff %": record.low_diff_pct,
                "Close Diff %": record.close_diff_pct,
                "Volume Diff %": record.volume_diff_pct,
                "Yahoo Close": record.yahoo.get("Close"),
                "Rubix Close": record.rubix.get("Close"),
                "Yahoo Volume": record.yahoo.get("Volume"),
                "Rubix Volume": record.rubix.get("Volume"),
            })


if __name__ == "__main__":
    import json
    import sys

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps(run_shadow(limit=limit), indent=2, default=str))
