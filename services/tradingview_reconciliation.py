"""Phases G/H: TradingView three-way reconciliation + freshness audit.

Sources it will use IF PRESENT:
  * CSV files dropped in the configured ``tradingview_csv_directory``
    (Phase E provider), one file per symbol.
  * Records already accepted by the webhook receiver's database
    (Phase D), if ``tradingview_webhook_enabled`` and any real alerts fired.

In THIS environment neither source has real data (no TradingView account, no
user-exported CSV, no live webhook), so every row is honestly reported as
"TradingView Available: No" with the specific reason, rather than fabricated.
Re-running this script after the user supplies real CSVs/webhook data will
populate genuine reconciliation numbers using the exact same code path.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config.settings_manager import settings
from core.symbols import load_symbols
from providers.local_cache_provider import LocalCacheProvider
from providers.rubix_bridge_factory import build_aggregator, rubix_db_path
from providers.rubix_sqlite_provider import RubixSQLiteProvider
from providers.tradingview_csv_provider import TradingViewCsvProvider
from providers.tradingview_webhook_receiver import TradingViewWebhookReceiver
from providers.symbol_mapping import to_rubix_symbol

_BASKET = (
    "COMI.CA", "SWDY.CA", "TMGH.CA", "EAST.CA", "FWRY.CA", "PHDC.CA",
    "TAQA.CA", "VALU.CA", "OIH.CA", "ARAB.CA", "CCAP.CA", "SPIN.CA",
    "CIRA.CA", "ALCN.CA", "SPHT.CA", "GPPL.CA", "MISR.CA", "QNBE.CA",
    "KRDI.CA", "TORA.CA", "VLMRA.CA", "MPCI.CA", "ODIN.CA",
)


def _tv_config():
    cfg = dict(settings.get("tradingview") or {})
    return {
        "csv_directory": cfg.get("tradingview_csv_directory", ""),
        "webhook_enabled": bool(cfg.get("tradingview_webhook_enabled", False)),
        "webhook_secret_configured": bool(cfg.get("tradingview_webhook_secret")),
    }


def _load_tradingview_candles(symbols):
    """Return {symbol: (frame_or_None, reason_if_none)} using whatever real
    TradingView data exists on disk. No network access, no scraping."""

    cfg = _tv_config()
    result = {}
    csv_provider = TradingViewCsvProvider()
    csv_dir = Path(cfg["csv_directory"]) if cfg["csv_directory"] else None

    webhook_candles = pd.DataFrame()
    if cfg["webhook_enabled"]:
        try:
            receiver = TradingViewWebhookReceiver(
                cfg.get("tradingview_webhook_secret") or "unconfigured",
            )
            webhook_candles = receiver.all_candles()
        except Exception:
            webhook_candles = pd.DataFrame()

    for symbol in symbols:
        code = to_rubix_symbol(symbol)
        csv_path = (csv_dir / f"{code}.csv") if csv_dir else None
        if csv_path and csv_path.is_file():
            try:
                outcome = csv_provider.import_csv(csv_path, symbol)
                if outcome.frame is not None and not outcome.frame.empty:
                    result[symbol] = (outcome.frame, None)
                    continue
                result[symbol] = (None, "CSV present but produced no valid completed rows")
                continue
            except Exception as error:
                result[symbol] = (None, f"CSV import failed: {error}")
                continue

        if not webhook_candles.empty:
            rows = webhook_candles[webhook_candles["ticker"].str.upper() == code.upper()]
            if not rows.empty:
                frame = rows.set_index(pd.to_datetime(rows["trading_date"]))[
                    ["open", "high", "low", "close", "volume"]
                ].rename(columns=str.title)
                result[symbol] = (frame, None)
                continue

        reasons = []
        if not (csv_dir and csv_dir.is_dir()):
            reasons.append("no tradingview_csv_directory configured/found")
        else:
            reasons.append(f"no {code}.csv in configured directory")
        if not cfg["webhook_enabled"]:
            reasons.append("webhook disabled")
        else:
            reasons.append("no webhook rows received for this symbol")
        result[symbol] = (None, "; ".join(reasons))
    return result


def run_reconciliation(symbols=_BASKET):
    settings.reload()
    data_cfg = settings.get("data")
    period = data_cfg.get("history_period", "10y")
    interval = data_cfg.get("interval", "1d")
    market_cfg = settings.get("market_data")
    cache = LocalCacheProvider(
        market_cfg.get("cache_path", "data/market_data_cache.sqlite"),
        source_provider=market_cfg.get("cache_source_provider", "rubix"),
    )
    aggregator = build_aggregator()
    rubix_provider = RubixSQLiteProvider(db_path=rubix_db_path())
    tv_candles = _load_tradingview_candles(symbols)
    collected_at = datetime.now(timezone.utc).astimezone().isoformat()

    yahoo_recon_rows = []
    rubix_recon_rows = []
    freshness_rows = []
    audit_rows = []

    for symbol in symbols:
        tv_frame, tv_reason = tv_candles[symbol]
        tv_available = tv_frame is not None and not tv_frame.empty

        try:
            yahoo_frame = cache.load_cached("yahoo", symbol, period, interval, allow_expired=True)
        except Exception:
            yahoo_frame = None
        yahoo_latest = (
            pd.Timestamp(yahoo_frame.index.max()).date().isoformat()
            if yahoo_frame is not None and not yahoo_frame.empty else None
        )

        rubix_results = aggregator.build_completed_daily(symbol, after=None)
        rubix_latest_valid = max(
            (r.trading_date for r in rubix_results if r.valid), default=None
        )

        tv_latest = (
            pd.Timestamp(tv_frame.index.max()).date().isoformat()
            if tv_available else None
        )

        if tv_available and yahoo_frame is not None:
            _reconcile_pair(symbol, tv_frame, yahoo_frame, yahoo_recon_rows, tv_reason=None)
        else:
            yahoo_recon_rows.append(_unavailable_row(
                symbol, tv_reason or "no TradingView data available"
            ))

        if tv_available and rubix_results:
            _reconcile_rubix(symbol, tv_frame, rubix_results, rubix_recon_rows)
        else:
            rubix_recon_rows.append(_unavailable_row(
                symbol, tv_reason or "no TradingView data available", rubix=True
            ))

        sessions_tv_ahead, sessions_yahoo_ahead = _session_deltas(tv_latest, yahoo_latest)
        freshness_rows.append({
            "Symbol": symbol,
            "TradingView Latest Date": tv_latest,
            "Yahoo Latest Date": yahoo_latest,
            "Sessions TV Ahead": sessions_tv_ahead,
            "Sessions Yahoo Ahead": sessions_yahoo_ahead,
            "Missing Session Count": len([r for r in rubix_results if not r.valid]),
            "TradingView Available": "Yes" if tv_available else "No",
            "Reason": tv_reason if not tv_available else "n/a",
        })

        newest_source = _newest_source(tv_latest, yahoo_latest, rubix_latest_valid)
        audit_rows.append({
            "Symbol": symbol,
            "TradingView Latest Completed Date": tv_latest,
            "Yahoo Latest Completed Date": yahoo_latest,
            "Rubix Latest Quote Timestamp": _rubix_quote_timestamp(rubix_provider, symbol),
            "TradingView Delayed/Realtime Disclosed": "Unknown (undisclosed for this account/symbol)",
            "Newest Completed Daily Source": newest_source,
            "Sessions TradingView Ahead of Yahoo": sessions_tv_ahead,
            "Sessions Yahoo Ahead of TradingView": sessions_yahoo_ahead,
            "Exact Collection Timestamp": collected_at,
            "TradingView Available": "Yes" if tv_available else "No",
            "Reason": tv_reason if not tv_available else "n/a",
        })

    _write(
        "reports/tradingview_yahoo_reconciliation.csv", yahoo_recon_rows,
        ["Symbol", "Trading Date", "TradingView Available", "Reason",
         "Open Diff %", "High Diff %", "Low Diff %", "Close Diff %", "Volume Diff %",
         "Yahoo Close", "TradingView Close", "Yahoo Volume", "TradingView Volume"],
    )
    _write(
        "reports/tradingview_rubix_reconciliation.csv", rubix_recon_rows,
        ["Symbol", "Trading Date", "TradingView Available", "Reason",
         "Close Diff %", "Volume Diff %",
         "TradingView Close", "Rubix Close (best-effort)",
         "TradingView Volume", "Rubix Cumulative Quote Volume"],
    )
    _write(
        "reports/tradingview_freshness_comparison.csv", freshness_rows,
        ["Symbol", "TradingView Latest Date", "Yahoo Latest Date",
         "Sessions TV Ahead", "Sessions Yahoo Ahead", "Missing Session Count",
         "TradingView Available", "Reason"],
    )
    _write(
        "reports/tradingview_freshness_audit.csv", audit_rows,
        ["Symbol", "TradingView Latest Completed Date", "Yahoo Latest Completed Date",
         "Rubix Latest Quote Timestamp", "TradingView Delayed/Realtime Disclosed",
         "Newest Completed Daily Source", "Sessions TradingView Ahead of Yahoo",
         "Sessions Yahoo Ahead of TradingView", "Exact Collection Timestamp",
         "TradingView Available", "Reason"],
    )

    tv_newer = sum(1 for r in freshness_rows if (r["Sessions TV Ahead"] or 0) > 0)
    yahoo_newer = sum(1 for r in freshness_rows if (r["Sessions Yahoo Ahead"] or 0) > 0)
    same = sum(
        1 for r in freshness_rows
        if r["TradingView Available"] == "Yes"
        and (r["Sessions TV Ahead"] or 0) == 0 and (r["Sessions Yahoo Ahead"] or 0) == 0
    )
    unavailable = sum(1 for r in freshness_rows if r["TradingView Available"] == "No")

    return {
        "symbols": len(symbols),
        "tradingview_newer_for": tv_newer,
        "yahoo_newer_for": yahoo_newer,
        "same_date_for": same,
        "tradingview_unavailable_for": unavailable,
        "tradingview_delayed_for": 0,  # cannot be determined without real data
        "reports": [
            "reports/tradingview_yahoo_reconciliation.csv",
            "reports/tradingview_rubix_reconciliation.csv",
            "reports/tradingview_freshness_comparison.csv",
            "reports/tradingview_freshness_audit.csv",
        ],
    }


def _pct(tv_value, other_value):
    if other_value in (None, 0) or pd.isna(other_value):
        return None
    return round((float(tv_value) - float(other_value)) / float(other_value) * 100, 4)


def _reconcile_pair(symbol, tv_frame, yahoo_frame, out, tv_reason):
    yahoo_by_date = {pd.Timestamp(i).date(): row for i, row in yahoo_frame.iterrows()}
    for idx, tv_row in tv_frame.iterrows():
        date = pd.Timestamp(idx).date()
        yahoo_row = yahoo_by_date.get(date)
        if yahoo_row is None:
            continue
        out.append({
            "Symbol": symbol, "Trading Date": date.isoformat(),
            "TradingView Available": "Yes", "Reason": "n/a",
            "Open Diff %": _pct(tv_row["Open"], yahoo_row["Open"]),
            "High Diff %": _pct(tv_row["High"], yahoo_row["High"]),
            "Low Diff %": _pct(tv_row["Low"], yahoo_row["Low"]),
            "Close Diff %": _pct(tv_row["Close"], yahoo_row["Close"]),
            "Volume Diff %": _pct(tv_row["Volume"], yahoo_row["Volume"]),
            "Yahoo Close": float(yahoo_row["Close"]), "TradingView Close": float(tv_row["Close"]),
            "Yahoo Volume": float(yahoo_row["Volume"]), "TradingView Volume": float(tv_row["Volume"]),
        })


def _reconcile_rubix(symbol, tv_frame, rubix_results, out):
    rubix_by_date = {r.trading_date: r for r in rubix_results if r.ohlcv is not None}
    for idx, tv_row in tv_frame.iterrows():
        date = pd.Timestamp(idx).date()
        r = rubix_by_date.get(date)
        if r is None:
            continue
        out.append({
            "Symbol": symbol, "Trading Date": date.isoformat(),
            "TradingView Available": "Yes", "Reason": "n/a",
            "Close Diff %": _pct(tv_row["Close"], r.ohlcv["Close"]),
            "Volume Diff %": _pct(tv_row["Volume"], r.ohlcv["Volume"]),
            "TradingView Close": float(tv_row["Close"]), "Rubix Close (best-effort)": r.ohlcv["Close"],
            "TradingView Volume": float(tv_row["Volume"]), "Rubix Cumulative Quote Volume": r.ohlcv["Volume"],
        })


def _unavailable_row(symbol, reason, rubix=False):
    if rubix:
        return {
            "Symbol": symbol, "Trading Date": None, "TradingView Available": "No",
            "Reason": reason, "Close Diff %": None, "Volume Diff %": None,
            "TradingView Close": None, "Rubix Close (best-effort)": None,
            "TradingView Volume": None, "Rubix Cumulative Quote Volume": None,
        }
    return {
        "Symbol": symbol, "Trading Date": None, "TradingView Available": "No",
        "Reason": reason, "Open Diff %": None, "High Diff %": None, "Low Diff %": None,
        "Close Diff %": None, "Volume Diff %": None, "Yahoo Close": None,
        "TradingView Close": None, "Yahoo Volume": None, "TradingView Volume": None,
    }


def _session_deltas(tv_latest, yahoo_latest):
    if not tv_latest or not yahoo_latest:
        return (None, None)
    tv_date = pd.Timestamp(tv_latest)
    yahoo_date = pd.Timestamp(yahoo_latest)
    if tv_date > yahoo_date:
        return (int((tv_date - yahoo_date).days), 0)
    if yahoo_date > tv_date:
        return (0, int((yahoo_date - tv_date).days))
    return (0, 0)


def _newest_source(tv_latest, yahoo_latest, rubix_latest_valid):
    candidates = []
    if tv_latest:
        candidates.append((pd.Timestamp(tv_latest), "TradingView"))
    if yahoo_latest:
        candidates.append((pd.Timestamp(yahoo_latest), "Yahoo"))
    if rubix_latest_valid:
        candidates.append((pd.Timestamp(rubix_latest_valid), "Rubix (validated)"))
    if not candidates:
        return "none available"
    return max(candidates, key=lambda pair: pair[0])[1]


def _rubix_quote_timestamp(rubix_provider, symbol):
    if rubix_provider is None:
        return None
    try:
        overlay = rubix_provider.quote_overlay(symbol)
        return overlay.get("quote_timestamp")
    except Exception:
        return None


def _write(path, rows, fields):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})


if __name__ == "__main__":
    import json

    print(json.dumps(run_reconciliation(), indent=2, default=str))
