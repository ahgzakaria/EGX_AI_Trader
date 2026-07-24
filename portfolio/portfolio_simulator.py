"""Deterministic portfolio-capacity simulation for all backtest modes."""

import math
from collections import defaultdict

from portfolio.sizing import PositionSizer


class PortfolioSimulator:
    """Simulate one shared portfolio with deterministic daily opportunity choice.

    All exits are processed before entries on a date.  Every candidate entering
    on that date is collected, ranked together, then considered in the exact
    documented order.  This removes any dependency on symbol-file or engine
    processing order while preserving the existing capital, heat and overlap
    constraints.
    """

    def __init__(
        self,
        trades,
        initial_capital=100000.0,
        risk_percent=1.0,
        allow_overlapping_trades=False,
        max_open_positions=10,
        max_portfolio_risk_percent=10.0,
    ):
        self.trades = trades
        self.initial_capital = float(initial_capital)
        self.risk_percent = float(risk_percent)
        self.allow_overlapping_trades = allow_overlapping_trades
        self.max_portfolio_risk_amount = round(
            self.initial_capital * max_portfolio_risk_percent / 100, 2
        )
        max_positions_by_heat = (
            math.floor(max_portfolio_risk_percent / self.risk_percent)
            if self.risk_percent > 0
            else max_open_positions
        )
        self.max_positions_by_heat = max_positions_by_heat
        self.effective_max_positions = min(
            int(max_open_positions), max_positions_by_heat
        )

    @staticmethod
    def selection_key(trade):
        """Documented deterministic priority for all same-day candidates.

        The rank is meaningful only for Ranking/Hybrid; the remaining fields
        still make Strategy Only capacity selection reproducible.
        """
        return (
            -_number(getattr(trade, "ai_rank", 0.0)),
            -_number(getattr(trade, "ai_probability", None), default=-1.0),
            -_number(getattr(trade, "score", 0.0)),
            -_number(getattr(trade, "confidence", 0.0)),
            -_number(getattr(trade, "rr", 0.0)),
            str(getattr(trade, "symbol", "")),
        )

    def _daily_events(self):
        entries, exits = defaultdict(list), defaultdict(list)
        for trade in self.trades:
            entries[str(trade.entry_date)].append(trade)
            exits[str(trade.exit_date)].append(trade)
        return entries, exits

    def _reset_trade_state(self):
        for trade in self.trades:
            trade.shares = 0
            trade.portfolio_profit = 0.0
            trade.executed = False
            trade.risk_amount = 0.0
            trade.final_position_size = 0
            trade.portfolio_rejection_reason = ""

    @staticmethod
    def _reject(trade, reason, rejected, reasons):
        trade.executed = False
        trade.final_position_size = 0
        trade.portfolio_rejection_reason = reason
        rejected.append(trade)
        reasons[reason] += 1

    def _enter(
        self, trade, cash, open_symbols, open_count, open_risk_amount,
        executed, rejected, reasons,
    ):
        if not self.allow_overlapping_trades and trade.symbol in open_symbols:
            self._reject(trade, "Overlap", rejected, reasons)
            return cash, open_count, open_risk_amount
        if open_count >= self.effective_max_positions:
            self._reject(trade, "MaxPositions", rejected, reasons)
            return cash, open_count, open_risk_amount

        summary = PositionSizer(
            self.initial_capital, self.risk_percent
        ).calculate(trade.entry_price, trade.stop_loss)
        multiplier = max(0.0, _number(getattr(trade, "ai_multiplier", 1.0)))
        shares = math.floor(summary["Shares"] * multiplier)
        trade_risk = round(shares * abs(trade.entry_price - trade.stop_loss), 2)
        if open_risk_amount + trade_risk > self.max_portfolio_risk_amount:
            self._reject(trade, "Heat", rejected, reasons)
            return cash, open_count, open_risk_amount

        cost = round(shares * trade.entry_price, 2)
        if shares <= 0 or cost > cash:
            self._reject(trade, "Capital", rejected, reasons)
            return cash, open_count, open_risk_amount

        trade.shares = shares
        trade.final_position_size = shares
        trade.risk_amount = trade_risk
        trade.executed = True
        open_symbols.add(trade.symbol)
        executed.append(trade)
        return (
            cash - cost,
            open_count + 1,
            round(open_risk_amount + trade_risk, 2),
        )

    @staticmethod
    def _exit(trade, cash, open_symbols, open_count, open_risk_amount):
        if not trade.executed:
            return cash, open_count, open_risk_amount
        trade.portfolio_profit = round(trade.shares * trade.profit, 2)
        cash += round(trade.shares * trade.entry_price + trade.portfolio_profit, 2)
        trade_risk = round(
            trade.shares * abs(trade.entry_price - trade.stop_loss), 2
        )
        open_symbols.discard(trade.symbol)
        return (
            cash,
            max(open_count - 1, 0),
            round(max(open_risk_amount - trade_risk, 0.0), 2),
        )

    def run(self):
        self._reset_trade_state()
        cash = self.initial_capital
        open_symbols, open_count, open_risk_amount = set(), 0, 0.0
        executed, rejected = [], []
        rejection_reasons = {"Overlap": 0, "MaxPositions": 0, "Heat": 0, "Capital": 0}
        entries, exits = self._daily_events()

        for date in sorted(set(entries) | set(exits)):
            # Releases happen before the daily opportunity batch is selected.
            for trade in sorted(exits[date], key=lambda value: value.symbol):
                cash, open_count, open_risk_amount = self._exit(
                    trade, cash, open_symbols, open_count, open_risk_amount
                )

            # This is the only portfolio entry ordering. It intentionally does
            # not inherit the symbol scanning or input list order.
            for trade in sorted(entries[date], key=self.selection_key):
                cash, open_count, open_risk_amount = self._enter(
                    trade, cash, open_symbols, open_count, open_risk_amount,
                    executed, rejected, rejection_reasons,
                )

        return {
            "executed_trades": executed,
            "rejected_trades": rejected,
            "rejection_reasons": rejection_reasons,
            "effective_max_positions": self.effective_max_positions,
            "final_cash": round(cash, 2),
        }


def _number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
