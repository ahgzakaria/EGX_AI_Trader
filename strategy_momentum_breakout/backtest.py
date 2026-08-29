"""Backtest for CONFIRMED_VOLUME_BREAKOUT, on the project's own machinery.

This does not reuse `backtesting/engine.py`, and the reason is a single line in
`backtesting/managers/entry_manager.py`: it fills at `buy_high` on any later bar
whose range touches the zone, or -- with `execution_delay_bars` -- at the open.
Neither is this strategy's entry. This one buys at the **next session's close**,
which is the first price known after a signal that is itself a close, and the
open in this project's data is carried forward from the previous close on 96-98%
of bars.

Everything downstream of the fill *is* reused, deliberately: `TradingCosts`
charges the same per-symbol spread, `Trade` is the same record, and
`PortfolioSimulator` and `BacktestStatistics` produce the same summary. So a
difference between the two strategies' headline numbers is a difference in the
strategy or in its portfolio limits, never in how it was scored -- and the
limits differ on purpose, each measured for its own rule.

Two conservative conventions, both matching the existing exit manager:

* When a bar touches both the stop and nothing else, the stop is taken. Daily
  OHLC does not record the order of the high and the low within a session.
* A bar whose high is already below the stop gapped through it overnight, and
  fills at that bar's low rather than at the stop price.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtesting.costs import TradingCosts
from backtesting.trade import Trade
from core.data_provider import load_history, provider_purpose
from indicators.technical import calculate_indicators
from strategy_momentum_breakout.config import BreakoutConfig, load as load_config
from strategy_momentum_breakout.signal import (
    GATES, REJECTION_REASONS, evaluate, measure, warmup_bars,
)


def _first_failure(gate_columns, warm, risk_valid, i) -> str:
    """The most fundamental thing wrong with this bar, not the last thing checked."""
    if not warm[i]:
        return "InsufficientHistory"
    for gate, column in zip(GATES, gate_columns):
        if not column[i]:
            return gate
    # Every gate passed and the bar is warm, so the only thing left that can
    # have failed is the stop sitting at or above the close.
    return "InvalidRisk"


@dataclass
class SymbolResult:
    symbol: str
    trades: list
    rejections: dict
    error: str | None = None


class MomentumBreakoutBacktest:
    """Generate this strategy's trades for one symbol."""

    def __init__(self, symbol: str, cfg: BreakoutConfig | None = None,
                 costs: TradingCosts | None = None):
        self.symbol = symbol
        self.cfg = cfg or load_config()
        self.costs = costs or TradingCosts(symbol=symbol)
        self.rejections = {reason: 0 for reason in REJECTION_REASONS}

    # -- data -----------------------------------------------------------

    def load(self) -> pd.DataFrame:
        with provider_purpose("backtest"):
            frame = load_history(self.symbol, purpose="backtest")
        if frame is None or frame.empty:
            raise ValueError(f"{self.symbol}: no history")
        return calculate_indicators(frame)

    # -- the run --------------------------------------------------------

    def run(self, df: pd.DataFrame | None = None,
            start_date=None, end_date=None) -> list[Trade]:
        df = self.load() if df is None else df
        cfg = self.cfg
        close = df["Close"].to_numpy(float)
        high = df["High"].to_numpy(float)
        low = df["Low"].to_numpy(float)
        index = df.index

        # The whole rule, once, vectorised. Calling `evaluate` per bar would
        # rebuild every rolling window on every bar -- four minutes a symbol
        # rather than a tenth of a second -- and would be a second reading of
        # the same table rather than the same reading.
        table = measure(df, cfg)
        passed = table["Passed"].to_numpy(bool)
        gate_columns = [table[gate if gate != "ClosePosition"
                              else "ClosePositionPassed"].to_numpy(bool)
                        for gate in GATES]
        warm = table["Warm"].to_numpy(bool)
        risk_valid = table["RiskValid"].to_numpy(bool)
        stops = table["StopLoss"].to_numpy(float)
        corporate_action = table["CorporateAction"].to_numpy(bool)

        trades: list[Trade] = []
        i = warmup_bars(cfg)
        if start_date is not None:
            i = max(i, int(index.searchsorted(start_date, side="left")))

        # The last bar that can still be entered *and* held to its cap. A trade
        # the data cannot see the end of is not counted, in either direction.
        last_signal = len(df) - cfg.holding_bars - 2

        while i <= last_signal:
            if end_date is not None and index[i] > end_date:
                break

            if not passed[i]:
                self.rejections[_first_failure(gate_columns, warm, risk_valid, i)] += 1
                i += 1
                continue

            trade = self.resolve_signal(df, i, measured=table)
            if trade is None:
                i += 1
                continue
            trades.append(trade)
            # One position per symbol at a time, so the next search resumes
            # after this trade closed rather than overlapping it.
            i = int(trade.exit_index) + 1

        return trades

    def resolve_signal(self, df, signal_index, measured=None):
        """Walk one signal to its exit and return the `Trade`, or `None`.

        The backtest loop calls this, and so does forward testing
        (`strategy_momentum_breakout/forward.py`) when a recorded signal's
        window has completed. That is the point of it being one method: a
        forward outcome and a backtested one are produced by the same code, so
        the live record cannot quietly diverge from the study it is testing.

        `None` means there is no trade — the next close is already at or below
        the stop, or the frame ends before the position could be entered.
        """
        cfg = self.cfg
        table = measure(df, cfg) if measured is None else measured
        decision = evaluate(df, signal_index, cfg, measured=table)
        decision["StopLoss"] = float(table["StopLoss"].to_numpy(float)[signal_index])
        return self._walk(
            df,
            df["Close"].to_numpy(float),
            df["High"].to_numpy(float),
            df["Low"].to_numpy(float),
            df.index,
            signal_index,
            decision,
            table["CorporateAction"].to_numpy(bool),
        )

    def _walk(self, df, close, high, low, index, signal_index, decision,
              corporate_action):
        cfg = self.cfg
        entry_index = signal_index + 1
        raw_entry = float(close[entry_index])
        if not np.isfinite(raw_entry) or raw_entry <= 0:
            return None

        stop = float(decision["StopLoss"])
        if stop >= raw_entry:
            # The next close opened below the stop the signal implied. There is
            # no trade here: it would be entered already stopped out.
            self.rejections["InvalidRisk"] += 1
            return None

        entry_price = self.costs.entry_price(raw_entry)
        last = min(entry_index + cfg.holding_bars, len(close) - 1)

        exit_index, exit_price, reason, result = None, None, None, None
        for k in range(entry_index + 1, last + 1):
            # A session outside EGX's daily price limit is a split, a bonus
            # issue or a resumption arriving in unadjusted prices, not a market
            # move. A real holder's position is adjusted by the broker; it does
            # not halve, and it does not quintuple either. So the position is
            # closed at the last clean price rather than booked at a price that
            # never existed. Untreated, this produced the record's five largest
            # losses (to -67.8%) and its largest gain (EHDR.CA, across a +394.9%
            # session), which is why the guard is symmetric.
            if corporate_action[k]:
                exit_index = k - 1
                exit_price = self.costs.exit_price(float(close[k - 1]))
                reason = "CorporateAction"
                result = "WIN" if exit_price > entry_price else "LOSS"
                break
            if low[k] <= stop:
                filled = low[k] if high[k] < stop else stop
                exit_index, exit_price = k, self.costs.exit_price(filled)
                reason, result = "StopLoss", "LOSS"
                break
        if exit_index is None:
            exit_index = last
            exit_price = self.costs.exit_price(float(close[last]))
            reason = "HoldingCap"
            result = "WIN" if exit_price > entry_price else "LOSS"

        profit = self.costs.net_profit(entry_price, exit_price)
        return self._record(df, index, signal_index, entry_index, exit_index,
                            entry_price, exit_price, stop, profit, reason,
                            result, decision)

    def _record(self, df, index, signal_index, entry_index, exit_index,
                entry_price, exit_price, stop, profit, reason, result,
                decision):
        candle = df.iloc[signal_index]
        measurements = decision["Measurements"]
        trade = Trade(
            symbol=self.symbol,
            entry_date=str(index[entry_index].date()),
            exit_date=str(index[exit_index].date()),
            entry_price=entry_price,
            exit_price=exit_price,
            stop_loss=stop,
            # This strategy has no target. Reporting a number here would be
            # inventing one, which is the defect it was built to avoid, so the
            # fields carry the entry itself and `rr` stays zero.
            target1=0.0,
            target2=0.0,
            rr=0.0,
            result=result,
            exit_reason=reason,
            profit=profit,
            # There is no score and no confidence. The portfolio simulator ranks
            # same-day candidates by these, so the volume ratio stands in: on a
            # day with more candidates than capacity, the more strongly
            # confirmed breakout is taken first. That is a capacity tie-break,
            # not a claim that volume ratio predicts the outcome.
            score=int(round(measurements["VolumeRatio"] * 10)),
            confidence=0,
            trend_score=0,
            volume_score=int(round(measurements["VolumeRatio"] * 10)),
            momentum_score=0,
            candle_score=0,
            breakout_score=0,
            rsi=round(float(candle["RSI"]), 2),
            adx=round(float(candle["ADX"]), 2),
            atr=round(float(candle["ATR"]), 4),
            macd=round(float(candle["MACD"]), 4),
            ema20_dist=round(float(candle["EMA20_DIST"]), 4),
            ema50_dist=round(float(candle["EMA50_DIST"]), 4),
            ema200_dist=round(float(candle["EMA200_DIST"]), 4),
            volume_ratio=round(float(candle["VOLUME_RATIO"]), 4),
            atr_percent=round(float(candle["ATR_PERCENT"]), 4),
            bb_position=round(float(candle["BB_POSITION"]), 4),
            obv=float(candle["OBV"]),
            rsi7=round(float(candle["RSI7"]), 2),
            ema20_slope=round(float(candle["EMA20_SLOPE"]), 4),
            ema50_slope=round(float(candle["EMA50_SLOPE"]), 4),
            rsi_slope=round(float(candle["RSI_SLOPE"]), 4),
            adx_rising=round(float(candle["ADX_RISING"]), 4),
            bb_width=round(float(candle["BB_WIDTH"]), 4),
            obv_slope=round(float(candle["OBV_SLOPE"]), 6),
            dist_high20=round(float(candle["DIST_HIGH20"]), 4),
            dist_low20=round(float(candle["DIST_LOW20"]), 4),
            macd_cross_age=int(candle["MACD_CROSS_AGE"]),
            reasons=" | ".join(decision["Reasons"]),
            signal_date=str(index[signal_index].date()),
            regime="",
        )
        # Carried for the loop that generated it, not part of the Trade record.
        trade.exit_index = exit_index
        return trade


def run_symbol(symbol: str, cfg: BreakoutConfig | None = None,
               start_date=None, end_date=None) -> SymbolResult:
    engine = MomentumBreakoutBacktest(symbol, cfg)
    try:
        trades = engine.run(start_date=start_date, end_date=end_date)
    except Exception as error:                        # noqa: BLE001 - recorded
        return SymbolResult(symbol, [], engine.rejections,
                            f"{type(error).__name__}: {error}")
    return SymbolResult(symbol, trades, engine.rejections)
