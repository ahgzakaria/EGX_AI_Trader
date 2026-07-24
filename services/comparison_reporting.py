"""Artifacts and diagnostics for Strategy Only vs Walk-Forward AI runs."""

from pathlib import Path

import pandas as pd

from ai.dataset import DatasetBuilder


SUMMARY_METRICS = (
    ("Total signals", "signals"),
    ("Executed trades", "Trades"),
    ("AI-rejected trades", "ai_rejected"),
    ("Winning trades", "Wins"),
    ("Losing trades", "Losses"),
    ("Win rate", "WinRate"),
    ("Net profit", "NetProfit"),
    ("Return %", "TotalReturn"),
    ("Profit Factor", "ProfitFactor"),
    ("Maximum Drawdown", "MaxDrawdown"),
    ("Expectancy per trade", "Expectancy"),
    ("Average win", "AverageWin"),
    ("Average loss", "AverageLoss"),
    ("Payoff ratio", "payoff_ratio"),
    ("Sharpe Ratio", "SharpeRatio"),
    ("Sortino Ratio", "SortinoRatio"),
    ("Calmar Ratio", "CalmarRatio"),
    ("Recovery Factor", "RecoveryFactor"),
    ("Maximum consecutive wins", "MaxConsecutiveWins"),
    ("Maximum consecutive losses", "MaxConsecutiveLosses"),
    ("Average holding period", "AverageHoldingDays"),
    ("Exposure %", "ExposurePercent"),
    ("Final equity", "FinalCapital"),
)


def create_comparison_reports(
    strategy,
    ai_filtered,
    historical_filter,
    ai_threshold,
    coverage_start,
    coverage_end,
):
    """Create strict OOS diagnostics and persisted comparative reports."""
    rows = _trade_rows(strategy["trades"], ai_filtered["trades"], historical_filter, ai_threshold)
    trade_frame = pd.DataFrame(rows)
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    trade_frame.to_csv(
        reports / "strategy_vs_ai_trade_comparison.csv",
        index=False,
        encoding="utf-8-sig",
    )

    yearly = _group_comparison(strategy["executed"], ai_filtered["executed"], "year")
    regimes = _group_comparison(strategy["executed"], ai_filtered["executed"], "regime")
    symbols = _group_comparison(strategy["executed"], ai_filtered["executed"], "symbol")
    yearly.to_csv(reports / "strategy_vs_ai_yearly_comparison.csv", index=False, encoding="utf-8-sig")
    regimes.to_csv(reports / "strategy_vs_ai_regime_comparison.csv", index=False, encoding="utf-8-sig")
    symbols.to_csv(reports / "strategy_vs_ai_symbol_comparison.csv", index=False, encoding="utf-8-sig")

    diagnostics = _diagnostics(trade_frame, strategy["summary"], ai_filtered["summary"])
    buckets = _bucket_results(trade_frame)
    buckets.to_csv(reports / "strategy_vs_ai_probability_buckets.csv", index=False, encoding="utf-8-sig")
    metrics = _summary_metrics(strategy, ai_filtered, diagnostics)
    metrics.to_csv(reports / "strategy_vs_ai_summary.csv", index=False, encoding="utf-8-sig")

    return {
        "metrics": metrics,
        "diagnostics": diagnostics,
        "yearly": yearly,
        "regimes": regimes,
        "symbols": symbols,
        "buckets": buckets,
        "coverage_start": str(pd.Timestamp(coverage_start).date()),
        "coverage_end": str(pd.Timestamp(coverage_end).date()),
    }


def _trade_rows(strategy_trades, ai_trades, historical_filter, threshold):
    ai_by_key = {_key(trade): trade for trade in ai_trades}
    rows = []
    for trade in strategy_trades:
        signal_date = pd.Timestamp(trade.signal_date or trade.entry_date)
        features = {feature: getattr(trade, feature, None) for feature in DatasetBuilder.FEATURES}
        prediction = historical_filter.predict(features, signal_date)
        if prediction is None:
            raise RuntimeError(
                "Missing out-of-sample AI prediction for "
                f"{trade.symbol} on {signal_date.date()}"
            )

        approved = prediction["Probability"] >= threshold
        ai_trade = ai_by_key.get(_key(trade))
        rows.append({
            "symbol": trade.symbol,
            "signal_date": signal_date.date().isoformat(),
            "entry_date": trade.entry_date,
            "exit_date": trade.exit_date,
            "year": signal_date.year,
            "regime": trade.regime,
            "strategy_result": trade.result,
            "strategy_profit_per_share": trade.profit,
            "strategy_executed": trade.executed,
            "strategy_portfolio_profit": trade.portfolio_profit,
            "ai_probability": prediction["Probability"],
            "ai_bucket": _bucket(prediction["Probability"]),
            "ai_accepted": approved,
            "ai_trade_found": ai_trade is not None,
            "ai_executed": getattr(ai_trade, "executed", False),
            "ai_result": getattr(ai_trade, "result", ""),
            "ai_portfolio_profit": getattr(ai_trade, "portfolio_profit", 0.0),
        })
    return rows


def _diagnostics(frame, strategy_summary, ai_summary):
    baseline_executed = frame[frame["strategy_executed"]]
    rejected = baseline_executed[~baseline_executed["ai_accepted"]]
    accepted = frame[frame["ai_accepted"]]
    avoided = -rejected.loc[rejected["strategy_portfolio_profit"] < 0, "strategy_portfolio_profit"].sum()
    missed = rejected.loc[rejected["strategy_portfolio_profit"] > 0, "strategy_portfolio_profit"].sum()
    return {
        "profitable_trades_rejected": int((rejected["strategy_portfolio_profit"] > 0).sum()),
        "losing_trades_rejected": int((rejected["strategy_portfolio_profit"] < 0).sum()),
        "profit_avoided_by_rejecting_losers": round(float(avoided), 2),
        "profit_missed_by_rejecting_winners": round(float(missed), 2),
        "direct_rejection_value": round(float(avoided - missed), 2),
        "net_economic_value_added": round(
            ai_summary["NetProfit"] - strategy_summary["NetProfit"], 2
        ),
        "acceptance_rate": round(float(frame["ai_accepted"].mean() * 100), 2) if len(frame) else 0,
        "rejection_rate": round(float((~frame["ai_accepted"]).mean() * 100), 2) if len(frame) else 0,
        "precision_among_accepted": round(
            float((accepted["strategy_result"] == "WIN").mean() * 100), 2
        ) if len(accepted) else 0,
    }


def _summary_metrics(strategy, ai_filtered, diagnostics):
    strategy_summary, ai_summary = strategy["summary"], ai_filtered["summary"]
    values = []
    for label, key in SUMMARY_METRICS:
        if key == "signals":
            strategy_value, ai_value = len(strategy["trades"]), len(ai_filtered["trades"])
        elif key == "ai_rejected":
            strategy_value, ai_value = 0, diagnostics["profitable_trades_rejected"] + diagnostics["losing_trades_rejected"]
        elif key == "payoff_ratio":
            strategy_value = _payoff(strategy_summary)
            ai_value = _payoff(ai_summary)
        else:
            strategy_value, ai_value = strategy_summary.get(key, 0), ai_summary.get(key, 0)
        values.append({"Metric": label, "Strategy Only": strategy_value, "Walk-Forward AI": ai_value})
    return pd.DataFrame(values)


def _group_comparison(strategy_trades, ai_trades, group):
    rows = []
    for mode, trades in (("Strategy Only", strategy_trades), ("Walk-Forward AI", ai_trades)):
        data = pd.DataFrame([trade.__dict__ for trade in trades])
        if data.empty:
            continue
        if group == "year":
            data["year"] = pd.to_datetime(data["signal_date"], errors="coerce").dt.year
        if group not in data.columns:
            data[group] = "Unknown"
        for value, subset in data.groupby(group, dropna=False):
            profit = subset["portfolio_profit"]
            gross_profit = profit[profit > 0].sum()
            gross_loss = abs(profit[profit < 0].sum())
            rows.append({
                group.title(): value, "Mode": mode, "Trades": len(subset),
                "Wins": int((subset["result"] == "WIN").sum()),
                "Losses": int((subset["result"] == "LOSS").sum()),
                "WinRate": round(float((subset["result"] == "WIN").mean() * 100), 2),
                "NetProfit": round(float(profit.sum()), 2),
                "ProfitFactor": round(float(gross_profit / gross_loss), 2) if gross_loss else 0,
            })
    return pd.DataFrame(rows)


def _bucket_results(frame):
    rows = []
    for bucket, subset in frame.groupby("ai_bucket", sort=False):
        rows.append({
            "AIProbabilityBucket": bucket,
            "Signals": len(subset),
            "Accepted": int(subset["ai_accepted"].sum()),
            "Rejected": int((~subset["ai_accepted"]).sum()),
            "WinRate": round(float((subset["strategy_result"] == "WIN").mean() * 100), 2),
            "StrategyPortfolioProfit": round(float(subset["strategy_portfolio_profit"].sum()), 2),
        })
    return pd.DataFrame(rows)


def _key(trade):
    return trade.symbol, trade.signal_date or trade.entry_date


def _bucket(probability):
    lower = int(float(probability) // 10 * 10)
    return f"{lower}-{min(lower + 9, 100)}"


def _payoff(summary):
    return round(summary["AverageWin"] / summary["AverageLoss"], 4) if summary["AverageLoss"] else 0
