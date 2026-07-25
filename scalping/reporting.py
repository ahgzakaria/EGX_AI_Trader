"""Metrics and group reports for paper/intraday scalping only."""

from __future__ import annotations

import math
import pandas as pd


def scalping_metrics(trades):
    frame = pd.DataFrame(list(trades or []))
    if frame.empty:
        return {
            "total_trades": 0, "gross_win_rate": 0.0, "net_win_rate": 0.0,
            "gross_profit": 0.0, "net_profit": 0.0, "profit_factor": 0.0,
            "expectancy": 0.0, "maximum_drawdown": 0.0,
            "average_trade_duration_minutes": 0.0, "target_hit_rate": 0.0,
            "stop_hit_rate": 0.0, "session_close_exits": 0,
            "ambiguous_bars": 0, "average_spread_cost": 0.0,
            "average_slippage": 0.0,
        }
    pnl = pd.to_numeric(frame["realized_pnl"], errors="coerce").fillna(0.0)
    gross = pd.to_numeric(frame["gross_pnl"], errors="coerce").fillna(0.0)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    equity = pnl.cumsum()
    drawdown = equity.cummax() - equity
    gross_profit = float(gross.sum())
    net_profit = float(pnl.sum())
    profit_factor = (
        float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0
        else (math.inf if len(wins) else 0.0)
    )
    return {
        "total_trades": int(len(frame)),
        "gross_win_rate": float((gross > 0).mean() * 100),
        "net_win_rate": float((pnl > 0).mean() * 100),
        "gross_profit": gross_profit, "net_profit": net_profit,
        "profit_factor": profit_factor, "expectancy": float(pnl.mean()),
        "maximum_drawdown": float(drawdown.max()),
        "average_trade_duration_minutes": float(pd.to_numeric(frame["duration_minutes"], errors="coerce").mean()),
        "target_hit_rate": float((frame["exit_status"] == "TARGET_HIT").mean() * 100),
        "stop_hit_rate": float((frame["exit_status"] == "STOP_HIT").mean() * 100),
        "session_close_exits": int((frame["exit_status"] == "SESSION_CLOSE").sum()),
        "ambiguous_bars": int(frame["ambiguous_same_bar"].fillna(False).astype(bool).sum()),
        "average_spread_cost": float(pd.to_numeric(frame["spread_cost"], errors="coerce").mean()),
        "average_slippage": float(pd.to_numeric(frame["slippage_cost"], errors="coerce").mean()),
    }


def grouped_results(trades, column):
    frame = pd.DataFrame(list(trades or []))
    if frame.empty or column not in frame:
        return pd.DataFrame()
    rows = []
    for value, group in frame.groupby(column, dropna=False):
        rows.append({column: value, **scalping_metrics(group.to_dict("records"))})
    return pd.DataFrame(rows)
