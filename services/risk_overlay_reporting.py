"""Traceable reports for the Phase 4 AI risk-overlay comparison."""

from pathlib import Path

import pandas as pd

from strategy.trading_decision import TradingDecisionService


MODE_ORDER = (
    TradingDecisionService.STRATEGY_ONLY,
    TradingDecisionService.AI_HARD_FILTER,
    TradingDecisionService.AI_POSITION_SIZING,
    TradingDecisionService.AI_RANKING_ONLY,
    TradingDecisionService.AI_HYBRID,
)


def create_risk_overlay_reports(results, coverage_start, coverage_end):
    """Persist comparable summaries and one auditable row per technical BUY."""
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    baseline = results[TradingDecisionService.STRATEGY_ONLY]
    summary_rows = [
        _summary_row(mode, results[mode], baseline, coverage_start, coverage_end)
        for mode in MODE_ORDER
    ]
    summary = pd.DataFrame(summary_rows)
    trades = _trade_rows(results)
    summary_path = reports / "ai_risk_overlay_summary.csv"
    trades_path = reports / "ai_risk_overlay_trades.csv"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    trades.to_csv(trades_path, index=False, encoding="utf-8-sig")
    return {
        "summary": summary,
        "trades": trades,
        "summary_path": str(summary_path),
        "trades_path": str(trades_path),
    }


def _summary_row(mode, result, baseline, coverage_start, coverage_end):
    summary = result["summary"]
    economics = _economic_delta(baseline["executed"], result["executed"])
    drawdown = float(summary["MaxDrawdown"])
    return {
        "Mode": mode,
        "CoverageStart": pd.Timestamp(coverage_start).date().isoformat(),
        "CoverageEnd": pd.Timestamp(coverage_end).date().isoformat(),
        "NetProfit": summary["NetProfit"],
        "ReturnPercent": summary["TotalReturn"],
        "ProfitFactor": summary["ProfitFactor"],
        "MaximumDrawdown": summary["MaxDrawdown"],
        "Sharpe": summary["SharpeRatio"],
        "Sortino": summary["SortinoRatio"],
        "Calmar": summary["CalmarRatio"],
        "RecoveryFactor": summary["RecoveryFactor"],
        "Expectancy": summary["Expectancy"],
        "TradeCount": summary["Trades"],
        "WinRate": summary["WinRate"],
        "AverageWin": summary["AverageWin"],
        "AverageLoss": summary["AverageLoss"],
        "ExposurePercent": summary["ExposurePercent"],
        "MaximumConsecutiveLosses": summary["MaxConsecutiveLosses"],
        "FinalEquity": summary["FinalCapital"],
        "ProfitMissedDueToAI": economics["profit_missed"],
        "LossesAvoidedDueToAI": economics["losses_avoided"],
        "NetEconomicValueAddedByAI": economics["net_economic_value"],
        "ReturnToDrawdown": round(
            float(summary["TotalReturn"]) / drawdown, 4
        ) if drawdown else 0.0,
        "ProfitPerUnitDrawdown": round(
            float(summary["NetProfit"]) / drawdown, 4
        ) if drawdown else 0.0,
    }


def _economic_delta(baseline, current):
    """Measure direct missed upside/avoided downside against Strategy Only."""
    if current is baseline:
        return {"profit_missed": 0.0, "losses_avoided": 0.0, "net_economic_value": 0.0}
    current_by_key = {_key(trade): trade for trade in current}
    profit_missed = losses_avoided = baseline_profit = current_profit = 0.0
    for trade in baseline:
        baseline_value = float(getattr(trade, "portfolio_profit", 0.0))
        current_value = float(getattr(
            current_by_key.get(_key(trade)), "portfolio_profit", 0.0
        ))
        baseline_profit += baseline_value
        if baseline_value > current_value:
            profit_missed += max(baseline_value - max(current_value, 0.0), 0.0)
        if baseline_value < current_value:
            losses_avoided += max(abs(min(baseline_value, 0.0)) - abs(min(current_value, 0.0)), 0.0)
        current_profit += current_value
    return {
        "profit_missed": round(profit_missed, 2),
        "losses_avoided": round(losses_avoided, 2),
        "net_economic_value": round(current_profit - baseline_profit, 2),
    }


def _trade_rows(results):
    rows = []
    for mode in MODE_ORDER:
        result = results[mode]
        raw_by_key = {_key(trade): trade for trade in result["trades"]}
        audit_keys = set()
        for audit in result.get("audits", []):
            key = (audit["symbol"], audit["signal_date"])
            audit_keys.add(key)
            rows.append(_audit_row(mode, audit, raw_by_key.get(key)))
        # A defensive fallback for a trade that has no signal-audit entry.
        for key, trade in raw_by_key.items():
            if key not in audit_keys:
                rows.append(_trade_row(mode, trade))
    return pd.DataFrame(rows)


def _audit_row(mode, audit, trade):
    row = {
        "Mode": mode,
        "Symbol": audit["symbol"],
        "SignalDate": audit["signal_date"],
        "FinalSignal": audit["final_signal"],
        "AIProbability": audit.get("ai_probability"),
        "AIPositionMultiplier": audit.get("ai_multiplier", 1.0),
        "AIRank": audit.get("ai_rank", 0.0),
        "AIRejectionReason": audit.get("ai_rejection_reason", ""),
    }
    if trade is None:
        row.update({
            "TradeGenerated": False, "Executed": False,
            "FinalPositionSize": 0, "PortfolioRejectionReason": "",
            "EntryDate": "", "ExitDate": "", "Result": "",
            "PortfolioProfit": 0.0,
        })
        return row
    row.update(_trade_values(trade))
    return row


def _trade_row(mode, trade):
    return {
        "Mode": mode,
        "Symbol": trade.symbol,
        "SignalDate": trade.signal_date,
        "FinalSignal": "BUY",
        "AIProbability": trade.ai_probability,
        "AIPositionMultiplier": trade.ai_multiplier,
        "AIRank": trade.ai_rank,
        "AIRejectionReason": trade.ai_rejection_reason,
        **_trade_values(trade),
    }


def _trade_values(trade):
    return {
        "TradeGenerated": True,
        "Executed": bool(trade.executed),
        "FinalPositionSize": int(getattr(trade, "final_position_size", 0)),
        "PortfolioRejectionReason": getattr(trade, "portfolio_rejection_reason", ""),
        "EntryDate": trade.entry_date,
        "ExitDate": trade.exit_date,
        "Result": trade.result,
        "PortfolioProfit": float(getattr(trade, "portfolio_profit", 0.0)),
    }


def _key(trade):
    return trade.symbol, str(trade.signal_date or trade.entry_date)
