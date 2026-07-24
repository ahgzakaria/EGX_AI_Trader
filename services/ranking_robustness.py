"""Phase 5 robustness validation for the leakage-safe AI ranking overlay."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import random

import numpy as np
import pandas as pd

from backtesting.statistics import BacktestStatistics
from config.settings_manager import settings
from core.symbols import load_symbols
from portfolio.portfolio_simulator import PortfolioSimulator
from services.backtest_service import _run_overlay_mode, _walk_forward_context
from services.experiment_tracking import ExperimentRun
from strategy.trading_decision import TradingDecisionService


MODES = (
    TradingDecisionService.STRATEGY_ONLY,
    TradingDecisionService.AI_RANKING_ONLY,
)


def run_ai_ranking_robustness():
    """Run deterministic, segmented and stress validation without tuning policy."""
    settings.reload()
    cfg = __import__("backtesting.config", fromlist=["load"]).load()
    if not settings.get("ai").get("enabled", False):
        raise RuntimeError("AI ranking robustness validation requires ai.enabled=true")

    symbols = load_symbols("data/symbols.csv")
    experiment = ExperimentRun("RESEARCH", "AI_RANKING_ROBUSTNESS", symbols)
    try:
        context = _walk_forward_context(symbols, cfg)
        base = {
            mode: _run_overlay_mode(symbols, mode, context, cfg)
            for mode in MODES
        }
        order_checks = _assert_order_invariance(base, cfg)
        yearly = _period_rows(base, cfg)
        segmented = _segmented_rows(base, cfg)
        capacity = _capacity_rows(base, cfg)
        stress, delayed = _stress_rows(base, symbols, context, cfg)
        bootstrap = _bootstrap_rows(base, cfg)

        reports = Path("reports")
        reports.mkdir(exist_ok=True)
        paths = {
            "yearly": reports / "ai_ranking_yearly.csv",
            "regime": reports / "ai_ranking_regime.csv",
            "capacity": reports / "ai_ranking_capacity.csv",
            "stress": reports / "ai_ranking_stress_tests.csv",
            "bootstrap": reports / "ai_ranking_bootstrap.csv",
        }
        yearly.to_csv(paths["yearly"], index=False, encoding="utf-8-sig")
        segmented.to_csv(paths["regime"], index=False, encoding="utf-8-sig")
        capacity.to_csv(paths["capacity"], index=False, encoding="utf-8-sig")
        stress.to_csv(paths["stress"], index=False, encoding="utf-8-sig")
        bootstrap.to_csv(paths["bootstrap"], index=False, encoding="utf-8-sig")

        experiment.complete(
            metrics={mode: result["summary"] for mode, result in base.items()},
            failures=context["label_failures"],
            successful_symbols=max(len(symbols) - len(context["label_failures"]), 0),
            walk_forward_status="COMPLETED",
            extra_metadata={
                "comparison_start": str(context["coverage_start"].date()),
                "comparison_end": str(context["coverage_end"].date()),
                "robustness_seed": 20260711,
            },
        )

        return {
            "base": base,
            "delayed": delayed,
            "context": context,
            "order_checks": order_checks,
            "yearly": yearly,
            "segmented": segmented,
            "capacity": capacity,
            "stress": stress,
            "bootstrap": bootstrap,
            "paths": {name: str(path) for name, path in paths.items()},
            "run_id": experiment.run_id,
        }
    except BaseException as error:
        experiment.fail(error)
        raise


def _simulate(trades, cfg, capacity=None):
    simulation = PortfolioSimulator(
        deepcopy(trades),
        initial_capital=cfg.INITIAL_CAPITAL,
        risk_percent=cfg.RISK_PERCENT,
        allow_overlapping_trades=cfg.ALLOW_OVERLAPPING_TRADES,
        max_open_positions=cfg.MAX_OPEN_POSITIONS if capacity is None else capacity,
        max_portfolio_risk_percent=cfg.MAX_PORTFOLIO_RISK_PERCENT,
    ).run()
    executed = simulation["executed_trades"]
    return {
        "executed": executed,
        "simulation": simulation,
        "summary": BacktestStatistics(
            executed, initial_capital=cfg.INITIAL_CAPITAL
        ).summary(),
    }


def _assert_order_invariance(base, cfg):
    checks = []
    for mode in MODES:
        candidates = base[mode]["trades"]
        expected = _signature(_simulate(candidates, cfg))
        orders = [("reversed", list(reversed(candidates)))]
        for seed in (7, 11, 29, 101, 20260711):
            shuffled = list(candidates)
            random.Random(seed).shuffle(shuffled)
            orders.append((f"shuffle_seed_{seed}", shuffled))
        for name, ordered in orders:
            actual = _signature(_simulate(ordered, cfg))
            if actual != expected:
                raise RuntimeError(
                    f"Order-invariance failure for {mode} under {name}"
                )
            checks.append({"Mode": mode, "Order": name, "Passed": True})
    return pd.DataFrame(checks)


def _signature(result):
    summary = result["summary"]
    trades = tuple(sorted(
        (
            trade.symbol, str(trade.signal_date), trade.entry_date,
            trade.exit_date, trade.final_position_size, trade.portfolio_profit,
        ) for trade in result["executed"]
    ))
    return (
        trades,
        summary["NetProfit"], summary["FinalCapital"], summary["MaxDrawdown"],
        summary["ProfitFactor"],
    )


def _period_rows(base, cfg):
    rows = []
    for mode in MODES:
        frame = _trades_frame(base[mode]["executed"])
        if frame.empty:
            continue
        dates = pd.to_datetime(frame["entry_date"])
        frame["Year"] = dates.dt.year.astype(str)
        frame["Quarter"] = dates.dt.year.astype(str) + "-Q" + dates.dt.quarter.astype(str)
        for period_type, column in (("Year", "Year"), ("Quarter", "Quarter")):
            for period, indices in frame.groupby(column).groups.items():
                rows.append({
                    "PeriodType": period_type,
                    "Period": period,
                    "Mode": mode,
                    **_metrics(_summary_for_indices(base[mode]["executed"], indices, cfg)),
                })
    return pd.DataFrame(rows)


def _segmented_rows(base, cfg):
    rows = []
    thresholds = _bucket_thresholds(base[TradingDecisionService.STRATEGY_ONLY]["trades"])
    for mode in MODES:
        trades = base[mode]["executed"]
        frame = _trades_frame(trades)
        if frame.empty:
            continue
        frame["MarketRegime"] = frame["regime"].fillna("UNKNOWN").replace("", "UNKNOWN")
        frame["LiquidityBucket"] = frame["volume_ratio"].map(
            lambda value: _bucket(value, thresholds["liquidity"])
        )
        frame["VolatilityBucket"] = frame["atr_percent"].map(
            lambda value: _bucket(value, thresholds["volatility"])
        )
        for dimension in ("MarketRegime", "LiquidityBucket", "VolatilityBucket"):
            for bucket, indices in frame.groupby(dimension).groups.items():
                rows.append({
                    "Dimension": dimension,
                    "Bucket": bucket,
                    "Mode": mode,
                    **_metrics(_summary_for_indices(trades, indices, cfg)),
                })
    return pd.DataFrame(rows)


def _bucket_thresholds(trades):
    frame = _trades_frame(trades)
    def quantiles(column):
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        return tuple(values.quantile([1 / 3, 2 / 3]).tolist()) if len(values) else (0.0, 0.0)
    return {"liquidity": quantiles("volume_ratio"), "volatility": quantiles("atr_percent")}


def _bucket(value, thresholds):
    low, high = thresholds
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "Unknown"
    if value <= low:
        return "Low"
    if value <= high:
        return "Medium"
    return "High"


def _capacity_rows(base, cfg):
    rows = []
    for requested in (3, 5, 8):
        for mode in MODES:
            result = _simulate(base[mode]["trades"], cfg, capacity=requested)
            rows.append({
                "RequestedCapacity": requested,
                "EffectiveCapacity": result["simulation"]["effective_max_positions"],
                "Mode": mode,
                **_metrics(result["summary"]),
            })
    return pd.DataFrame(rows)


def _stress_rows(base, symbols, context, cfg):
    rows = []
    scenarios = (
        ("Base", 1.0, 1.0),
        ("Commission +25%", 1.25, 1.0),
        ("Commission +50%", 1.50, 1.0),
        ("Slippage +25%", 1.0, 1.25),
        ("Slippage +50%", 1.0, 1.50),
    )
    for name, commission_factor, slippage_factor in scenarios:
        for mode in MODES:
            stressed = _reprice_execution_path(
                base[mode]["trades"], cfg, commission_factor, slippage_factor
            )
            result = _simulate(stressed, cfg)
            rows.append({"Scenario": name, "Mode": mode, **_metrics(result["summary"])})

    # This path re-runs entry/exit simulation from a next-bar open rather than
    # approximating a delay from already-completed trades.
    delayed = {
        mode: _run_overlay_mode(
            symbols, mode, context, cfg, execution_delay_bars=1
        ) for mode in MODES
    }
    for mode in MODES:
        rows.append({
            "Scenario": "One-bar delayed execution",
            "Mode": mode,
            **_metrics(delayed[mode]["summary"]),
        })

    strategy_executed = base[TradingDecisionService.STRATEGY_ONLY]["executed"]
    top_symbols = {
        symbol for symbol, _profit in _top_symbol_profits(strategy_executed, 5)
    }
    top_trades = {
        (trade.symbol, str(trade.signal_date or trade.entry_date))
        for trade in sorted(
            strategy_executed,
            key=lambda trade: trade.portfolio_profit,
            reverse=True,
        )[:10]
    }
    for name, predicate in (
        ("Remove top 5 Strategy-Only symbols", lambda trade: trade.symbol not in top_symbols),
        ("Remove top 10 Strategy-Only trades", lambda trade: (
            trade.symbol, str(trade.signal_date or trade.entry_date)
        ) not in top_trades),
    ):
        for mode in MODES:
            remaining = [trade for trade in base[mode]["trades"] if predicate(trade)]
            result = _simulate(remaining, cfg)
            rows.append({"Scenario": name, "Mode": mode, **_metrics(result["summary"])})
    return pd.DataFrame(rows), delayed


def _reprice_execution_path(trades, cfg, commission_factor, slippage_factor):
    adjusted = deepcopy(trades)
    base_commission = float(cfg.COMMISSION)
    base_slippage = float(cfg.SLIPPAGE)
    for trade in adjusted:
        raw_entry = float(trade.entry_price) / (1 + base_slippage)
        raw_exit = float(trade.exit_price) / (1 - base_slippage)
        trade.entry_price = round(raw_entry * (1 + base_slippage * slippage_factor), 4)
        trade.exit_price = round(raw_exit * (1 - base_slippage * slippage_factor), 4)
        gross = round(trade.exit_price - trade.entry_price, 2)
        commission = round(
            (trade.entry_price + trade.exit_price)
            * base_commission * commission_factor,
            4,
        )
        trade.profit = round(gross - commission, 2)
        _refresh_trade_metrics(trade)
    return adjusted


def _refresh_trade_metrics(trade):
    trade.risk_per_share = round(abs(trade.entry_price - trade.stop_loss), 2)
    trade.reward_per_share = round(trade.exit_price - trade.entry_price, 2)
    trade.profit_percent = round(
        trade.profit / trade.entry_price * 100, 2
    ) if trade.entry_price else 0.0
    trade.r_multiple = round(
        trade.reward_per_share / trade.risk_per_share, 2
    ) if trade.risk_per_share else 0.0


def _top_symbol_profits(trades, count):
    totals = {}
    for trade in trades:
        totals[trade.symbol] = totals.get(trade.symbol, 0.0) + trade.portfolio_profit
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)[:count]


def _bootstrap_rows(base, cfg, samples=1000, seed=20260711):
    rows = []
    for mode_index, mode in enumerate(MODES):
        trades = base[mode]["executed"]
        values = np.array([trade.portfolio_profit for trade in trades], dtype=float)
        rng = np.random.default_rng(seed + mode_index)
        if len(values) == 0:
            continue
        for iteration in range(samples):
            sample = rng.choice(values, size=len(values), replace=True)
            gross_profit = sample[sample > 0].sum()
            gross_loss = abs(sample[sample < 0].sum())
            rows.append({
                "Mode": mode,
                "Seed": seed + mode_index,
                "Iteration": iteration + 1,
                "ReturnPercent": round(float(sample.sum()) / cfg.INITIAL_CAPITAL * 100, 6),
                "ProfitFactor": round(
                    float(gross_profit / gross_loss) if gross_loss else 0.0, 6
                ),
            })
    return pd.DataFrame(rows)


def _trades_frame(trades):
    return pd.DataFrame([trade.__dict__ for trade in trades])


def _summary_for_indices(trades, indices, cfg):
    selected = [trades[index] for index in indices]
    return BacktestStatistics(selected, initial_capital=cfg.INITIAL_CAPITAL).summary()


def _metrics(summary):
    return {
        "ReturnPercent": summary["TotalReturn"],
        "NetProfit": summary["NetProfit"],
        "ProfitFactor": summary["ProfitFactor"],
        "MaximumDrawdown": summary["MaxDrawdown"],
        "Sharpe": summary["SharpeRatio"],
        "Sortino": summary["SortinoRatio"],
        "Calmar": summary["CalmarRatio"],
        "Expectancy": summary["Expectancy"],
        "TradeCount": summary["Trades"],
        "WinRate": summary["WinRate"],
        "FinalEquity": summary["FinalCapital"],
    }
