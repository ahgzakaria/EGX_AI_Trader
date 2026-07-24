"""Paper-only position lifecycle backed by the dedicated scalping database."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from core.egx_session import cairo_now
from scalping.config import ScalpingConfig
from scalping.database import ScalpingDatabase
from scalping.entry_engine import build_entry_fill
from scalping.exit_engine import evaluate_bar, exit_required, session_close_exit
from scalping.models import DailyRiskState, Position, SetupType
from scalping.risk_engine import apply_close, can_open, position_size


class ScalpingPaperPortfolio:
    def __init__(self, config=None, database=None):
        self.config = config or ScalpingConfig()
        self.database = database or ScalpingDatabase(self.config.database_path)

    def execute(self, opportunity, signal_id, state: DailyRiskState, now=None):
        now = cairo_now(now or datetime.now(timezone.utc))
        allowed, reason = can_open(state, opportunity.ticker, now, self.config)
        if not opportunity.actionable:
            allowed, reason = False, opportunity.blocked_reason or "NON_ACTIONABLE"
        quantity = position_size(
            state.starting_equity + state.realized_pnl,
            opportunity.ask, self.config, opportunity.volume,
        )
        if quantity <= 0:
            allowed, reason = False, "ZERO_POSITION_SIZE"
        self.database.record_entry_attempt(
            signal_id, now, opportunity.ask, quantity,
            "ACCEPTED" if allowed else "REJECTED", reason,
        )
        if not allowed:
            self.database.record_rejection(opportunity, reason)
            if reason in {"DAILY_LOSS_KILL_SWITCH", "CONSECUTIVE_LOSS_KILL_SWITCH"}:
                self.database.add_alert(
                    reason, f"New scalping entries stopped: {reason}",
                    ticker=opportunity.ticker,
                    key=f"{reason}|{state.session_date}",
                )
            return None, reason
        fill = build_entry_fill(opportunity.signal_price, opportunity.ask, quantity, self.config)
        position_id = self.database.record_fill_and_position(
            signal_id, opportunity.ticker, opportunity.setup.value, now, fill
        )
        position = Position(
            position_id=position_id, signal_id=signal_id, ticker=opportunity.ticker,
            setup=opportunity.setup.value, opened_at=now, entry=fill,
        )
        state.trades_count += 1
        state.open_positions += 1
        risk = fill.actual_entry_fill * fill.quantity * self.config.stop_loss_percent / 100.0
        state.portfolio_heat += risk
        state.symbol_exposure[opportunity.ticker] = fill.actual_entry_fill * fill.quantity
        self.database.save_risk_state(state)
        self.database.add_alert(
            "ENTRY_FILLED", f"Paper entry filled for {opportunity.ticker} at {fill.actual_entry_fill}",
            ticker=opportunity.ticker, key=f"ENTRY|{position_id}",
        )
        return position, None

    def evaluate(self, position, high, low, bid, timestamp, state):
        result = evaluate_bar(position, high, low, bid, timestamp, self.config)
        if result is None and exit_required(position, timestamp, self.config):
            result = session_close_exit(position, bid, timestamp, self.config)
        if result is None:
            self.database.update_position_bid(position.position_id, bid, cairo_now(timestamp))
            return None
        self.database.close_position(position.position_id, result)
        risk = position.entry.actual_entry_fill * position.entry.quantity * self.config.stop_loss_percent / 100.0
        state.portfolio_heat = max(0.0, state.portfolio_heat - risk)
        apply_close(state, position.ticker, result)
        self.database.save_risk_state(state)
        alert_type = {
            "TARGET_HIT": "TARGET_HIT", "STOP_HIT": "STOP_HIT",
            "SESSION_CLOSE": "SESSION_CLOSE_EXIT",
        }.get(result.status.value, "POSITION_CLOSED")
        self.database.add_alert(
            alert_type, f"{position.ticker} closed: {result.status.value}",
            ticker=position.ticker, key=f"EXIT|{position.position_id}",
        )
        return result

    @staticmethod
    def restore_position(row):
        from scalping.models import EntryFill
        fill = EntryFill(
            signal_price=row["entry_fill"], requested_entry_price=row["entry_fill"],
            actual_entry_fill=row["entry_fill"], target_price=row["target_price"],
            stop_price=row["stop_price"], quantity=row["quantity"],
            entry_cost=row["entry_cost"], slippage_cost=0.0,
        )
        return Position(
            position_id=row["position_id"], signal_id=row["signal_id"],
            ticker=row["ticker"], setup=row["setup"],
            opened_at=datetime.fromisoformat(row["opened_at"]), entry=fill,
            current_bid=row.get("current_bid"),
        )
