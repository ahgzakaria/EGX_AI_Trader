"""Chronological, no-look-ahead backtest for the adaptive selector."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict

import pandas as pd

from backtesting.statistics import BacktestStatistics
from portfolio.portfolio_simulator import PortfolioSimulator
from strategy_selector.selector import AdaptiveStrategySelector
from strategy_selector.selector_score import WalkForwardPerformanceLedger


def _date(value):
    return pd.Timestamp(value).normalize()


def _trade_output(trade, strategy):
    if trade is None:
        return {"Signal": "AVOID", "Score": 0, "Confidence": 0, "RR": 0}
    output = {
        "Signal": "BUY",
        "Score": getattr(trade, "score", 0),
        "Confidence": getattr(trade, "confidence", 0),
        "RR": getattr(trade, "rr", 0),
    }
    if strategy == "BREAKOUT_SWING":
        output["EdgeScore"] = getattr(trade, "ai_rank", 0)
    return output


class AdaptiveSelectorBacktester:
    def __init__(self, settings, portfolio_config):
        self.settings = settings
        self.portfolio_config = portfolio_config

    def run(self, classic_candidates, breakout_candidates, classifications):
        classic_candidates = list(classic_candidates)
        breakout_candidates = list(breakout_candidates)
        by_date = {
            "CLASSIC": defaultdict(list),
            "BREAKOUT_SWING": defaultdict(list),
        }
        for trade in classic_candidates:
            by_date["CLASSIC"][_date(trade.signal_date)].append(trade)
        for trade in breakout_candidates:
            by_date["BREAKOUT_SWING"][_date(trade.signal_date)].append(trade)

        # The learning stream observes every strategy candidate, not only
        # selector-approved trades.  An outcome is visible only on a strictly
        # later prediction date than its exit.
        outcomes = sorted(
            [
                (_date(trade.exit_date), "CLASSIC", trade)
                for trade in classic_candidates
            ] + [
                (_date(trade.exit_date), "BREAKOUT_SWING", trade)
                for trade in breakout_candidates
            ],
            key=lambda item: (item[0], item[1], item[2].symbol),
        )
        ledger = WalkForwardPerformanceLedger(self.settings)
        selector = AdaptiveStrategySelector(self.settings, ledger)
        pointer = 0
        selected = []
        decisions = []
        last_learning_exit = None
        signal_dates = sorted(set(by_date["CLASSIC"]) | set(by_date["BREAKOUT_SWING"]))

        for signal_date in signal_dates:
            while pointer < len(outcomes) and outcomes[pointer][0] < signal_date:
                exit_date, strategy, trade = outcomes[pointer]
                classification = classifications.get(_date(trade.signal_date))
                regime = classification.regime if classification else (
                    getattr(trade, "regime", "UNKNOWN") or "UNKNOWN"
                )
                ledger.observe(strategy, regime, getattr(trade, "profit_percent", 0))
                last_learning_exit = exit_date
                pointer += 1

            classification = classifications.get(signal_date)
            if classification is None:
                continue
            classic_symbols = {trade.symbol: trade for trade in by_date["CLASSIC"][signal_date]}
            breakout_symbols = {
                trade.symbol: trade for trade in by_date["BREAKOUT_SWING"][signal_date]
            }
            for symbol in sorted(set(classic_symbols) | set(breakout_symbols)):
                classic_trade = classic_symbols.get(symbol)
                breakout_trade = breakout_symbols.get(symbol)
                result = selector.select_symbol(
                    _trade_output(classic_trade, "CLASSIC"),
                    _trade_output(breakout_trade, "BREAKOUT_SWING"),
                    classification,
                )
                result.update({
                    "Date": signal_date.date().isoformat(),
                    "Symbol": symbol,
                    "ClassicDecision": "BUY" if classic_trade else "AVOID",
                    "ClassicRR": getattr(classic_trade, "rr", 0),
                    "BreakoutDecision": "BUY" if breakout_trade else "AVOID",
                    "BreakoutRR": getattr(breakout_trade, "rr", 0),
                    "LatestTrainingExitDate": (
                        last_learning_exit.date().isoformat()
                        if last_learning_exit is not None else None
                    ),
                    "ChronologyValid": (
                        last_learning_exit is None or last_learning_exit < signal_date
                    ),
                })
                decisions.append(result)
                chosen = None
                if result["FinalRecommendation"] == "BUY_CLASSIC":
                    chosen = classic_trade
                elif result["FinalRecommendation"] == "BUY_BREAKOUT":
                    chosen = breakout_trade
                if chosen is not None:
                    chosen = deepcopy(chosen)
                    chosen.ai_mode = "ADAPTIVE_SELECTOR"
                    chosen.ai_rank = result["StrategyEdgeScore"]
                    chosen.selector_regime = classification.regime
                    chosen.selector_preferred_strategy = result["PreferredStrategy"]
                    chosen.selector_confidence = result["SelectorConfidence"]
                    chosen.selector_recommendation = result["FinalRecommendation"]
                    chosen.selector_reason = result["SelectorReason"]
                    selected.append(chosen)

        simulation = PortfolioSimulator(
            selected,
            initial_capital=self.portfolio_config.INITIAL_CAPITAL,
            risk_percent=self.portfolio_config.RISK_PERCENT,
            allow_overlapping_trades=self.portfolio_config.ALLOW_OVERLAPPING_TRADES,
            max_open_positions=self.portfolio_config.MAX_OPEN_POSITIONS,
            max_portfolio_risk_percent=self.portfolio_config.MAX_PORTFOLIO_RISK_PERCENT,
        ).run()
        executed = simulation["executed_trades"]

        # Complete the ledger only after the last historical decision.  This
        # snapshot is valid for a later live scan but never influenced replay.
        while pointer < len(outcomes):
            _, strategy, trade = outcomes[pointer]
            classification = classifications.get(_date(trade.signal_date))
            regime = classification.regime if classification else (
                getattr(trade, "regime", "UNKNOWN") or "UNKNOWN"
            )
            ledger.observe(strategy, regime, getattr(trade, "profit_percent", 0))
            pointer += 1

        return {
            "mode": "ADAPTIVE_SELECTOR",
            "candidates": selected,
            "executed": executed,
            "simulation": simulation,
            "summary": BacktestStatistics(
                executed, initial_capital=self.portfolio_config.INITIAL_CAPITAL
            ).summary(),
            "decisions": decisions,
            "performance": ledger.export(),
        }


def robustness_rows(results_by_mode, classifications):
    rows = []
    for mode, result in results_by_mode.items():
        grouped = defaultdict(list)
        for trade in result["executed"]:
            classification = classifications.get(_date(trade.signal_date))
            tags = classification.robustness_tags if classification else ("UNKNOWN",)
            for tag in tags:
                grouped[tag].append(trade)
        for tag, trades in sorted(grouped.items()):
            profits = [float(getattr(trade, "portfolio_profit", 0)) for trade in trades]
            gross_profit = sum(value for value in profits if value > 0)
            gross_loss = abs(sum(value for value in profits if value < 0))
            rows.append({
                "Mode": mode,
                "Condition": tag,
                "Trades": len(trades),
                "WinRate": round(sum(value > 0 for value in profits) / len(trades) * 100, 2),
                "NetProfit": round(sum(profits), 2),
                "ProfitFactor": round(gross_profit / gross_loss, 2) if gross_loss else 0,
                "Expectancy": round(sum(profits) / len(trades), 2),
            })
    return rows
