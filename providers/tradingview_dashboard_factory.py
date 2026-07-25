"""Phase L support: read-only TradingView Research panel data for the dashboard.

Reads configuration and the latest generated reports (if any exist) -- it
never re-runs the reconciliation/shadow scripts on a page render, and it never
touches the TradingView website. Production is always reported as disabled
here; this panel is display-only and never influences Classic, Breakout, or
Adaptive output.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from config.settings_manager import settings

_FRESHNESS_AUDIT = "reports/tradingview_freshness_audit.csv"
_SHADOW_REPORT = "reports/tradingview_shadow_decision_comparison.csv"


def tradingview_research_disclosure():
    cfg = dict(settings.get("tradingview") or {})
    csv_dir = cfg.get("tradingview_csv_directory", "")
    webhook_on = bool(cfg.get("tradingview_webhook_enabled", False))

    if csv_dir:
        method = "CSV"
    elif webhook_on:
        method = "Official Webhook"
    else:
        method = "Unavailable"

    audit = _read_csv(_FRESHNESS_AUDIT)
    shadow = _read_csv(_SHADOW_REPORT)

    if audit is None:
        access_status = "Not yet run (no reports/tradingview_freshness_audit.csv)"
        available_count = 0
        total = 0
        latest_tv = None
        latest_yahoo = None
        freshness_advantage = "Unknown"
        reason_summary = "No reconciliation has been run in this environment"
    else:
        total = len(audit)
        available_count = int((audit["TradingView Available"] == "Yes").sum())
        access_status = (
            f"{available_count}/{total} basket symbols have real TradingView data"
        )
        latest_tv = _first_non_null(audit, "TradingView Latest Completed Date")
        latest_yahoo = _first_non_null(audit, "Yahoo Latest Completed Date")
        tv_ahead = int(pd.to_numeric(
            audit["Sessions TradingView Ahead of Yahoo"], errors="coerce"
        ).fillna(0).gt(0).sum())
        yahoo_ahead = int(pd.to_numeric(
            audit["Sessions Yahoo Ahead of TradingView"], errors="coerce"
        ).fillna(0).gt(0).sum())
        if available_count == 0:
            freshness_advantage = "N/A -- no real TradingView data ingested"
        else:
            freshness_advantage = f"TradingView ahead for {tv_ahead}, Yahoo ahead for {yahoo_ahead}"
        reasons = sorted({
            r for r in audit.loc[audit["TradingView Available"] == "No", "Reason"].dropna()
        })
        reason_summary = "; ".join(reasons[:3]) if reasons else "n/a"

    if shadow is None:
        shadow_status = "Not yet run"
    else:
        changed = int((shadow["Decision Changed"] == "Yes").sum())
        shadow_status = f"{changed}/{len(shadow)} basket symbols would change decision"

    return {
        "method": method,
        "access_status": access_status,
        "symbol_mapping_reference": (
            "docs/audits/providers/TRADINGVIEW_TEST_BASKET_AND_SYMBOL_MAP.md"
        ),
        "latest_completed_tradingview_candle": latest_tv or "Not available",
        "latest_yahoo_candle": latest_yahoo or "Not available",
        "freshness_advantage": freshness_advantage,
        "tradingview_data_status": access_status,
        "reconciliation_status": (
            "Not yet performed" if audit is None
            else f"{available_count} of {total} symbols reconciled with real data"
        ),
        "shadow_decision_status": shadow_status,
        "production_enabled": "No",
        "current_session_excluded": "Yes",
        "failure_or_limitation_reason": reason_summary,
    }


def _read_csv(path):
    file = Path(path)
    if not file.is_file():
        return None
    try:
        frame = pd.read_csv(file)
        return frame if not frame.empty else None
    except Exception:
        return None


def _first_non_null(frame, column):
    if column not in frame.columns:
        return None
    series = frame[column].dropna()
    return str(series.iloc[0]) if not series.empty else None
