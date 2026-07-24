"""Ask-side paper fills and fixed fill-relative TP/SL levels."""

from __future__ import annotations

from scalping.config import ScalpingConfig
from scalping.models import EntryFill


def build_entry_fill(signal_price, ask, quantity, config: ScalpingConfig):
    if not ask or float(ask) <= 0 or int(quantity) <= 0:
        raise ValueError("A positive Ask and quantity are required")
    requested = config.round_price(float(ask), "up")
    unrounded_fill = requested * (1.0 + config.slippage)
    actual_fill = config.round_price(unrounded_fill, "up")
    target = config.round_price(
        actual_fill * (1.0 + config.take_profit_percent / 100.0), "up"
    )
    stop = config.round_price(
        actual_fill * (1.0 - config.stop_loss_percent / 100.0), "down"
    )
    notional = actual_fill * int(quantity)
    return EntryFill(
        signal_price=float(signal_price),
        requested_entry_price=requested,
        actual_entry_fill=actual_fill,
        target_price=target,
        stop_price=stop,
        quantity=int(quantity),
        entry_cost=notional * config.commission,
        slippage_cost=max(0.0, actual_fill - requested) * int(quantity),
    )
