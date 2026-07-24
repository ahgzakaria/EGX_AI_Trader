"""Paper-outcome analytics; no inference from unclosed recommendations."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import pandas as pd


def performance_analytics(path="data/scalping.db"):
    source = Path(path)
    if not source.is_file():
        return {"trades": pd.DataFrame(), "by_setup": pd.DataFrame(), "by_hour": pd.DataFrame(), "by_weekday": pd.DataFrame()}
    uri = f"file:{source.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            trades = pd.read_sql_query(
                """SELECT p.ticker,p.setup,p.opened_at,e.exited_at,e.status,
                          e.realized_pnl,e.net_return_percent
                   FROM exits e JOIN open_positions p ON p.position_id=e.position_id""",
                connection,
            )
    except (sqlite3.Error, pd.errors.DatabaseError):
        trades = pd.DataFrame()
    if trades.empty:
        return {"trades": trades, "by_setup": pd.DataFrame(), "by_hour": pd.DataFrame(), "by_weekday": pd.DataFrame()}
    opened = pd.to_datetime(trades["opened_at"], errors="coerce")
    trades["Hour"] = opened.dt.hour
    trades["Weekday"] = opened.dt.day_name()
    trades["HoldingMinutes"] = (
        pd.to_datetime(trades["exited_at"], errors="coerce") - opened
    ).dt.total_seconds() / 60.0

    def group(column):
        return trades.groupby(column, dropna=False).agg(
            Trades=("ticker", "count"),
            WinRate=("realized_pnl", lambda values: float((values > 0).mean() * 100)),
            NetProfit=("realized_pnl", "sum"),
            AverageReturn=("net_return_percent", "mean"),
            AverageHoldingMinutes=("HoldingMinutes", "mean"),
        ).reset_index().sort_values(["WinRate", "NetProfit"], ascending=False)
    return {
        "trades": trades, "by_setup": group("setup"),
        "by_hour": group("Hour"), "by_weekday": group("Weekday"),
    }
