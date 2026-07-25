"""Conservative bid-side exits, including mandatory session close."""

from __future__ import annotations

from datetime import datetime

from core.egx_session import cairo_now
from scalping.config import ScalpingConfig
from scalping.models import ExitResult, Position, TradeStatus


def _exit_result(position, reference_price, status, exited_at, config, ambiguous=False):
    raw_fill = float(reference_price) * (1.0 - config.slippage)
    fill = config.round_price(raw_fill, "down")
    entry = position.entry
    gross_pnl = (fill - entry.actual_entry_fill) * entry.quantity
    exit_cost = fill * entry.quantity * config.commission
    costs = entry.entry_cost + exit_cost
    realized = gross_pnl - costs
    notional = entry.actual_entry_fill * entry.quantity
    return ExitResult(
        status=status,
        exited_at=cairo_now(exited_at),
        exit_fill=fill,
        gross_return_percent=(fill / entry.actual_entry_fill - 1.0) * 100.0,
        trading_costs=costs,
        slippage_cost=entry.slippage_cost + max(0.0, float(reference_price) - fill) * entry.quantity,
        net_return_percent=(realized / notional * 100.0) if notional else 0.0,
        realized_pnl=realized,
        ambiguous_same_bar=ambiguous,
    )


def evaluate_bar(position: Position, high, low, bid, timestamp, config: ScalpingConfig):
    """Resolve stop/target conservatively when intrabar order is unknown."""

    target_hit = float(high) >= position.entry.target_price
    stop_hit = float(low) <= position.entry.stop_price
    if target_hit and stop_hit:
        # Loss-first is the production default until tick replay proves order.
        reference = min(float(bid), position.entry.stop_price) if bid else position.entry.stop_price
        return _exit_result(
            position, reference, TradeStatus.STOP_HIT, timestamp, config, ambiguous=True
        )
    if stop_hit:
        reference = min(float(bid), position.entry.stop_price) if bid else position.entry.stop_price
        return _exit_result(position, reference, TradeStatus.STOP_HIT, timestamp, config)
    if target_hit:
        reference = float(bid) if bid and float(bid) >= position.entry.target_price else position.entry.target_price
        return _exit_result(position, reference, TradeStatus.TARGET_HIT, timestamp, config)
    return None


def session_close_exit(position: Position, bid, timestamp, config: ScalpingConfig):
    return _exit_result(position, bid, TradeStatus.SESSION_CLOSE, timestamp, config)


def exit_required(position: Position, timestamp, config: ScalpingConfig):
    current = cairo_now(timestamp)
    if current.date() > cairo_now(position.opened_at).date():
        return True
    return config.close_at_session_end and current.time() >= config.forced_exit_clock
