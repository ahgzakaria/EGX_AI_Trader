"""Write Scalping V2 range-scanner reports from a real scan (isolated)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from scalping.range_scanner import RangeScanner


def run_and_write(symbols=None):
    scanner = RangeScanner()
    result = scanner.scan(symbols=symbols)
    universe = result["universe"]
    opportunities = result["opportunities"]

    Path("reports").mkdir(exist_ok=True)

    universe_cols = [
        "Symbol", "SessionDate", "DataStatus", "DataReason",
        "ADR_PERCENT", "ATR_PERCENT", "SESSION_RANGE_PERCENT", "CURRENT_MOVE_PERCENT",
        "REALIZED_VOL_PERCENT", "TURNOVER_EGP", "VOLUME", "SPREAD_PERCENT",
        "QuoteUpdates", "QuoteAgeSec", "CoverageRatio", "ValidBarCount",
        "ADR_PCTL", "ATR_PCTL", "SESSION_RANGE_PCTL", "TURNOVER_PCTL",
        "VOLUME_PCTL", "QUOTE_UPDATE_PCTL", "SPREAD_PCTL",
        "VolatilityScore", "SessionRangeScore", "LiquidityScore", "TurnoverScore",
        "SpreadScore", "DataCoverageScore", "QuoteActivityScore",
        "VOLATILITY_RANK", "LIQUIDITY_RANK", "RANGE_QUALITY_SCORE",
        "SCALPING_SUITABILITY_SCORE", "Rejected", "RejectionReason",
    ]
    universe.reindex(columns=universe_cols).to_csv(
        "reports/scalping_range_universe.csv", index=False, encoding="utf-8")

    rejections = universe[universe["Rejected"]].copy()
    rejections[["Symbol", "DataStatus", "SPREAD_PERCENT", "TURNOVER_EGP",
                "CoverageRatio", "QuoteAgeSec", "QuoteUpdates", "RejectionReason"]].to_csv(
        "reports/scalping_range_rejections.csv", index=False, encoding="utf-8")

    if not opportunities.empty:
        opportunities.to_csv(
            "reports/scalping_range_opportunities.csv", index=False, encoding="utf-8")
    else:
        pd.DataFrame(columns=["Symbol", "Status", "Reason"]).to_csv(
            "reports/scalping_range_opportunities.csv", index=False, encoding="utf-8")

    return result["summary"], result["session_date"], result["health"]


if __name__ == "__main__":
    import json

    summary, session_date, health = run_and_write()
    print("session_date:", session_date)
    print("rubix freshness:", health.get("freshness"), "collector:", health.get("collector_status"))
    print(json.dumps(summary, indent=2, default=str))
