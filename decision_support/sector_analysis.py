"""Optional sector grouping without inventing unavailable classifications."""

from __future__ import annotations

from pathlib import Path
import pandas as pd


def load_sector_map(path):
    source = Path(path)
    if not source.is_file():
        return {}
    frame = pd.read_csv(source)
    symbol_column = next((name for name in ("Ticker", "Symbol", "ticker", "symbol") if name in frame), None)
    sector_column = next((name for name in ("Sector", "sector") if name in frame), None)
    if not symbol_column or not sector_column:
        return {}
    return {
        str(row[symbol_column]).upper(): str(row[sector_column]).strip() or "Unknown"
        for _, row in frame.iterrows()
    }


def sector_summary(rows):
    frame = pd.DataFrame(rows or [])
    if frame.empty or "Sector" not in frame:
        return pd.DataFrame()
    grouped = frame.groupby("Sector", dropna=False).agg(
        Opportunities=("Ticker", "count"),
        AverageEdge=("EdgeScore", "mean"),
        AverageMomentum=("MomentumScore", "mean"),
        AverageLiquidity=("LiquidityScore", "mean"),
        Actionable=("QualityGate", lambda values: int((values == "MEETS_CRITERIA").sum())),
    ).reset_index()
    grouped["SectorStrength"] = (
        grouped["AverageEdge"].fillna(0) / 10.0 * 0.7
        + grouped["AverageMomentum"].fillna(0) * 0.3
    ).clip(0, 1)
    return grouped.sort_values(
        ["SectorStrength", "AverageEdge", "Sector"], ascending=[False, False, True]
    ).reset_index(drop=True)
