"""Independent research backtest for BREAKOUT_SWING.

Signal, geometry and exits come exclusively from ``strategy_breakout``.  The
validated PortfolioSimulator and statistics classes are reused as passive,
unchanged infrastructure so capital constraints remain comparable.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterable

import pandas as pd

from backtesting.statistics import BacktestStatistics
from backtesting.trade import Trade
from portfolio.portfolio_simulator import PortfolioSimulator
from strategy_breakout.breakout_exit import simulate_exit
from strategy_breakout.breakout_strategy import BreakoutConfig, BreakoutSwingStrategy


@dataclass(frozen=True)
class BreakoutTradeRecord:
    symbol: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    stop_loss: float
    target1: float
    target2: float
    rr: float
    score: int
    confidence: int
    edge_score: float
    regime: str
    setup_types: str
    entry_mode: str
    stop_model: str
    target_model: str
    result: str
    exit_reason: str
    profit_per_share: float
    holding_bars: int
    target1_hit: bool
    breakout_failure: bool
    false_breakout: bool
    breakout_level: float


def _regime(frame: pd.DataFrame, i: int) -> str:
    last = frame.iloc[i]
    if last["EMA20"] > last["EMA50"] > last["EMA200"] and last["Close"] > last["EMA20"]:
        return "BULL"
    if last["Close"] < last["EMA20"] and last["EMA20"] < last["EMA50"]:
        return "BEAR"
    return "SIDEWAYS"


def _portfolio_trade(record: BreakoutTradeRecord, frame: pd.DataFrame, signal_index: int) -> Trade:
    last = frame.iloc[signal_index]
    trade = Trade(
        symbol=record.symbol,
        entry_date=record.entry_date,
        exit_date=record.exit_date,
        entry_price=record.entry_price,
        exit_price=record.exit_price,
        stop_loss=record.stop_loss,
        target1=record.target1,
        target2=record.target2,
        rr=record.rr,
        result=record.result,
        exit_reason=record.exit_reason,
        profit=record.profit_per_share,
        score=record.score,
        confidence=record.confidence,
        trend_score=0,
        volume_score=0,
        momentum_score=0,
        candle_score=0,
        breakout_score=record.score,
        rsi=float(last.get("RSI", 0)),
        adx=float(last.get("ADX", 0)),
        atr=float(last.get("ATR", 0)),
        macd=float(last.get("MACD", 0)),
        ema20_dist=float(last.get("EMA20_DIST", 0)),
        ema50_dist=float(last.get("EMA50_DIST", 0)),
        ema200_dist=float(last.get("EMA200_DIST", 0)),
        volume_ratio=float(last.get("VOLUME_RATIO", 0)),
        atr_percent=float(last.get("ATR_PERCENT", 0)),
        bb_position=float(last.get("BB_POSITION", 0)),
        obv=float(last.get("OBV", 0)),
        rsi7=float(last.get("RSI7", 0)),
        ema20_slope=float(last.get("EMA20_SLOPE", 0)),
        ema50_slope=float(last.get("EMA50_SLOPE", 0)),
        rsi_slope=float(last.get("RSI_SLOPE", 0)),
        adx_rising=float(last.get("ADX_RISING", 0)),
        bb_width=float(last.get("BB_WIDTH", 0)),
        obv_slope=float(last.get("OBV_SLOPE", 0)),
        dist_high20=float(last.get("DIST_HIGH20", 0)),
        dist_low20=float(last.get("DIST_LOW20", 0)),
        macd_cross_age=int(last.get("MACD_CROSS_AGE", 0)),
        reasons=f"BREAKOUT_SWING | {record.setup_types}",
        signal_date=record.signal_date,
        regime=record.regime,
        ai_mode="BREAKOUT_SWING",
    )
    trade.ai_rank = record.edge_score
    return trade


class BreakoutBacktester:
    def __init__(self, config: BreakoutConfig, portfolio_config):
        self.config = config
        self.portfolio_config = portfolio_config
        self.strategy = BreakoutSwingStrategy(config)

    def run_symbol(
        self,
        symbol: str,
        frame: pd.DataFrame,
        start_date=None,
        end_date=None,
    ) -> tuple[list[BreakoutTradeRecord], list[Trade]]:
        records: list[BreakoutTradeRecord] = []
        trades: list[Trade] = []
        minimum = max(200, self.config.breakout_lookback + 2)
        i = minimum
        start = pd.Timestamp(start_date) if start_date is not None else None
        end = pd.Timestamp(end_date) if end_date is not None else None
        # One completed candle after entry is required by the independent
        # daily-bar exit model; the entry candle is never used for an exit.
        while i < len(frame) - self.config.entry_delay_bars - 1:
            date = pd.Timestamp(frame.index[i])
            if start is not None and date < start:
                i += 1
                continue
            if end is not None and date > end:
                break
            decision = self.strategy.evaluate(frame, i)
            if decision["Signal"] != "BUY":
                i += 1
                continue
            entry_index = i + self.config.entry_delay_bars
            raw_entry = float(frame["Open"].iloc[entry_index])
            actual_entry = raw_entry * (1 + self.config.slippage)
            stop = float(decision["StopLoss"])
            target1 = float(decision["Target1"])
            target2 = float(decision["Target2"])
            actual_risk = actual_entry - stop
            actual_rr = (target2 - actual_entry) / actual_risk if actual_risk > 0 else 0.0
            # A gap can invalidate yesterday's geometry; fail loudly in audit
            # fields by skipping rather than silently accepting a lower RR.
            if not (self.config.minimum_rr <= actual_rr <= self.config.maximum_rr):
                i += 1
                continue
            exit_result = simulate_exit(
                frame,
                i,
                entry_index,
                actual_entry,
                stop,
                target1,
                target2,
                float(decision["BreakoutLevel"]),
                self.config,
            )
            result = "WIN" if exit_result.net_profit_per_share > 0 else (
                "LOSS" if exit_result.net_profit_per_share < 0 else "BREAKEVEN"
            )
            record = BreakoutTradeRecord(
                symbol=symbol,
                signal_date=str(date.date()),
                entry_date=str(pd.Timestamp(frame.index[entry_index]).date()),
                exit_date=str(pd.Timestamp(frame.index[exit_result.exit_index]).date()),
                entry_price=round(actual_entry, self.config.price_precision),
                exit_price=exit_result.exit_price,
                stop_loss=stop,
                target1=target1,
                target2=target2,
                rr=round(actual_rr, 3),
                score=int(decision["Score"]),
                confidence=int(decision["Confidence"]),
                edge_score=float(decision["EdgeScore"]),
                regime=_regime(frame, i),
                setup_types=" | ".join(decision["SetupTypes"]),
                entry_mode=self.config.entry_mode,
                stop_model=self.config.stop_model,
                target_model=self.config.target_model,
                result=result,
                exit_reason=exit_result.exit_reason,
                profit_per_share=exit_result.net_profit_per_share,
                holding_bars=exit_result.holding_bars,
                target1_hit=exit_result.target1_hit,
                breakout_failure=exit_result.breakout_failure,
                false_breakout=exit_result.false_breakout,
                breakout_level=float(decision["BreakoutLevel"]),
            )
            records.append(record)
            trades.append(_portfolio_trade(record, frame, i))
            i = max(i + 1, exit_result.exit_index + 1)
        return records, trades

    def run(self, frames: dict[str, pd.DataFrame], start_date=None, end_date=None) -> dict:
        records: list[BreakoutTradeRecord] = []
        candidates: list[Trade] = []
        failures = []
        for symbol in sorted(frames):
            try:
                symbol_records, symbol_trades = self.run_symbol(
                    symbol, frames[symbol], start_date, end_date
                )
                records.extend(symbol_records)
                candidates.extend(symbol_trades)
            except Exception as error:
                failures.append({"Symbol": symbol, "Error": str(error)})
        return portfolio_result(
            candidates,
            records,
            self.portfolio_config,
            mode="BREAKOUT_SWING",
            failures=failures,
        )


def portfolio_result(candidates, records, portfolio_config, mode, failures=None):
    simulation = PortfolioSimulator(
        sorted(candidates, key=lambda trade: trade.entry_date),
        initial_capital=portfolio_config.INITIAL_CAPITAL,
        risk_percent=portfolio_config.RISK_PERCENT,
        allow_overlapping_trades=portfolio_config.ALLOW_OVERLAPPING_TRADES,
        max_open_positions=portfolio_config.MAX_OPEN_POSITIONS,
        max_portfolio_risk_percent=portfolio_config.MAX_PORTFOLIO_RISK_PERCENT,
    ).run()
    executed = simulation["executed_trades"]
    summary = BacktestStatistics(
        executed, initial_capital=portfolio_config.INITIAL_CAPITAL
    ).summary()
    record_by_key = {
        (record.symbol, record.entry_date, record.exit_date): record for record in records
    }
    executed_records = [
        record_by_key.get((trade.symbol, trade.entry_date, trade.exit_date))
        for trade in executed
    ]
    executed_records = [record for record in executed_records if record is not None]
    summary.update({
        "BreakoutFailureRate": round(
            sum(record.breakout_failure for record in executed_records)
            / len(executed_records) * 100, 2
        ) if executed_records else 0,
        "FalseBreakoutRate": round(
            sum(record.false_breakout for record in executed_records)
            / len(executed_records) * 100, 2
        ) if executed_records else 0,
        "CandidateTrades": len(candidates),
        "PortfolioRejected": len(simulation["rejected_trades"]),
    })
    return {
        "mode": mode,
        "records": records,
        "candidates": candidates,
        "executed": executed,
        "simulation": simulation,
        "summary": summary,
        "failures": failures or [],
        "regime_performance": grouped_performance(executed_records, "regime"),
    }


def grouped_performance(records: Iterable[BreakoutTradeRecord], field: str) -> list[dict]:
    grouped = defaultdict(list)
    for record in records:
        grouped[getattr(record, field, "UNKNOWN") or "UNKNOWN"].append(record)
    output = []
    for label, values in sorted(grouped.items()):
        profits = [record.profit_per_share for record in values]
        output.append({
            field: label,
            "Trades": len(values),
            "WinRate": round(sum(value > 0 for value in profits) / len(values) * 100, 2),
            "AverageProfitPerShare": round(sum(profits) / len(values), 4),
            "BreakoutFailureRate": round(
                sum(record.breakout_failure for record in values) / len(values) * 100, 2
            ),
            "FalseBreakoutRate": round(
                sum(record.false_breakout for record in values) / len(values) * 100, 2
            ),
        })
    return output


def combine_candidate_books(classic_candidates, breakout_result, portfolio_config):
    """Combine completed trade candidates; entry/exit logic stays separate."""

    combined = deepcopy(list(classic_candidates)) + deepcopy(
        list(breakout_result["candidates"])
    )
    simulation = PortfolioSimulator(
        combined,
        initial_capital=portfolio_config.INITIAL_CAPITAL,
        risk_percent=portfolio_config.RISK_PERCENT,
        allow_overlapping_trades=portfolio_config.ALLOW_OVERLAPPING_TRADES,
        max_open_positions=portfolio_config.MAX_OPEN_POSITIONS,
        max_portfolio_risk_percent=portfolio_config.MAX_PORTFOLIO_RISK_PERCENT,
    ).run()
    executed = simulation["executed_trades"]
    return {
        "mode": "COMBINED_STRATEGIES",
        "candidates": combined,
        "executed": executed,
        "simulation": simulation,
        "summary": BacktestStatistics(
            executed, initial_capital=portfolio_config.INITIAL_CAPITAL
        ).summary(),
    }


def records_frame(result: dict) -> pd.DataFrame:
    return pd.DataFrame([asdict(record) for record in result.get("records", [])])
