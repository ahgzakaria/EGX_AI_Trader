"""Separate minute/tick-aware conservative scalping backtest."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pandas as pd

from core.egx_session import cairo_now, egx_session_phase
from scalping.config import ScalpingConfig
from scalping.entry_engine import build_entry_fill
from scalping.exit_engine import evaluate_bar, session_close_exit
from scalping.models import DailyRiskState, MarketSnapshot, Position
from scalping.reporting import grouped_results, scalping_metrics
from scalping.risk_engine import apply_close, can_open, position_size
from scalping.setup_detector import detect_setups


class ScalpingBacktest:
    def __init__(self, config=None):
        self.config = config or ScalpingConfig()

    def run(self, frames):
        prepared, limitations = self._prepare(frames)
        if not prepared:
            return self._result([], limitations or ["No usable intraday data"], missed_fills=0)
        timeline = sorted(set().union(*(set(frame.index) for frame in prepared.values())))
        positions = {}
        trades = []
        state = None
        current_date = None
        previous_timestamp = None
        entered_today = set()
        missed_fills = 0
        for timestamp in timeline:
            local = cairo_now(pd.Timestamp(timestamp).to_pydatetime())
            if current_date != local.date():
                if positions:
                    # Data gaps cannot roll risk overnight: use the latest known
                    # bid conservatively and tag SESSION_CLOSE.
                    for ticker, position in list(positions.items()):
                        result = session_close_exit(
                            position, position.current_bid or position.entry.stop_price,
                            previous_timestamp or timestamp, self.config,
                        )
                        trades.append(self._trade_row(position, result, 0.0))
                        positions.pop(ticker)
                current_date = local.date()
                state = DailyRiskState(current_date.isoformat(), self.config.initial_capital)
                entered_today = set()

            for ticker, position in list(positions.items()):
                frame = prepared[ticker]
                if timestamp not in frame.index:
                    continue
                bar = frame.loc[timestamp]
                bid = float(bar.get("Bid", bar["Close"] * (1 - self.config.max_spread_percent / 200)))
                position.current_bid = bid
                result = evaluate_bar(position, bar["High"], bar["Low"], bid, timestamp, self.config)
                if result is None and local.time() >= self.config.forced_exit_clock:
                    result = session_close_exit(position, bid, timestamp, self.config)
                if result is not None:
                    spread_cost = max(0.0, position.entry.requested_entry_price - bid) * position.entry.quantity
                    trades.append(self._trade_row(position, result, spread_cost))
                    apply_close(state, ticker, result)
                    positions.pop(ticker)

            previous_timestamp = timestamp
            if local.time() >= self.config.entry_cutoff_time or egx_session_phase(timestamp) != "OPEN":
                continue
            for ticker in sorted(prepared):
                if ticker in positions or ticker in entered_today:
                    continue
                frame = prepared[ticker]
                if timestamp not in frame.index:
                    continue
                history = frame.loc[:timestamp]
                if len(history) < self.config.breakout_lookback_bars + 1:
                    continue
                bar = history.iloc[-1]
                ask = float(bar.get("Ask", bar["Close"] * (1 + self.config.max_spread_percent / 200)))
                bid = float(bar.get("Bid", bar["Close"] * (1 - self.config.max_spread_percent / 200)))
                snapshot = MarketSnapshot(
                    ticker=ticker, timestamp=local, last=float(bar["Close"]), bid=bid,
                    ask=ask, volume=float(bar["Volume"]), provider="rubix",
                    freshness="FRESH", quote_age_seconds=0.0, session_phase="OPEN",
                    bid_size=bar.get("BidSize"), ask_size=bar.get("AskSize"),
                )
                qualified = [item for item in detect_setups(history, snapshot, self.config) if item.actionable]
                if not qualified:
                    continue
                allowed, _ = can_open(state, ticker, local, self.config)
                if not allowed:
                    continue
                quantity = position_size(
                    self.config.initial_capital + state.realized_pnl, ask, self.config,
                    bar.get("AskSize"),
                )
                if quantity <= 0:
                    missed_fills += 1
                    continue
                opportunity = sorted(qualified, key=lambda item: (-item.score, item.setup.value))[0]
                fill = build_entry_fill(opportunity.signal_price, ask, quantity, self.config)
                position = Position(
                    position_id=str(uuid4()), signal_id=str(uuid4()), ticker=ticker,
                    setup=opportunity.setup.value, opened_at=local, entry=fill,
                    current_bid=bid,
                )
                positions[ticker] = position
                entered_today.add(ticker)
                state.trades_count += 1
                state.open_positions += 1
                risk = fill.actual_entry_fill * fill.quantity * self.config.stop_loss_percent / 100.0
                state.portfolio_heat += risk
                state.symbol_exposure[ticker] = fill.actual_entry_fill * fill.quantity
        if timeline and positions:
            timestamp = timeline[-1]
            for ticker, position in list(positions.items()):
                result = session_close_exit(
                    position, position.current_bid or position.entry.stop_price,
                    timestamp, self.config,
                )
                trades.append(self._trade_row(position, result, 0.0))
        return self._result(trades, limitations, missed_fills=missed_fills)

    def _prepare(self, frames):
        prepared, limitations = {}, []
        for ticker, source in dict(frames or {}).items():
            frame = source.copy().sort_index()
            if not isinstance(frame.index, pd.DatetimeIndex) or len(frame) < 2:
                limitations.append(f"{ticker}: insufficient intraday coverage")
                continue
            differences = frame.index.to_series().diff().dropna().dt.total_seconds()
            if differences.empty or differences.median() > 15 * 60:
                limitations.append(f"{ticker}: daily/non-intraday data rejected")
                continue
            missing = {"Open", "High", "Low", "Close", "Volume"} - set(frame.columns)
            if missing:
                limitations.append(f"{ticker}: missing {', '.join(sorted(missing))}")
                continue
            if frame.index.tz is None:
                frame.index = frame.index.tz_localize("Africa/Cairo")
            prepared[str(ticker)] = frame
        return prepared, limitations

    def _trade_row(self, position, result, spread_cost):
        duration = (result.exited_at - cairo_now(position.opened_at)).total_seconds() / 60.0
        gross_pnl = (
            result.exit_fill - position.entry.actual_entry_fill
        ) * position.entry.quantity
        return {
            "ticker": position.ticker, "setup": position.setup,
            "entry_time": cairo_now(position.opened_at).isoformat(),
            "exit_time": result.exited_at.isoformat(),
            "signal_price": position.entry.signal_price,
            "requested_entry_price": position.entry.requested_entry_price,
            "actual_entry_fill": position.entry.actual_entry_fill,
            "target_price": position.entry.target_price,
            "stop_price": position.entry.stop_price,
            "exit_fill": result.exit_fill, "exit_status": result.status.value,
            "gross_return_percent": result.gross_return_percent,
            "net_return_percent": result.net_return_percent,
            "gross_pnl": gross_pnl, "realized_pnl": result.realized_pnl,
            "trading_costs": result.trading_costs,
            "slippage_cost": result.slippage_cost,
            "spread_cost": spread_cost, "duration_minutes": max(0.0, duration),
            "ambiguous_same_bar": result.ambiguous_same_bar,
            "time_of_day": cairo_now(position.opened_at).strftime("%H:%M"),
        }

    def _result(self, trades, limitations, missed_fills=0):
        metrics = scalping_metrics(trades)
        metrics["missed_fills"] = int(missed_fills)
        return {
            "trades": trades, "metrics": metrics,
            "by_setup": grouped_results(trades, "setup"),
            "by_time_of_day": grouped_results(trades, "time_of_day"),
            "by_symbol": grouped_results(trades, "ticker"),
            "limitations": list(limitations),
            "data_requirement": "Rubix ticks or 1-minute intraday bars; daily OHLCV is rejected",
        }
