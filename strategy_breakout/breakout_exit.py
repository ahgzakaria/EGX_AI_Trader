"""Independent BREAKOUT_SWING exits with conservative same-bar handling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class BreakoutExitResult:
    exit_index: int
    exit_price: float
    exit_reason: str
    gross_return_percent: float
    net_profit_per_share: float
    target1_hit: bool
    breakout_failure: bool
    false_breakout: bool
    holding_bars: int


def simulate_exit(
    df: pd.DataFrame,
    signal_index: int,
    entry_index: int,
    entry_price: float,
    stop: float,
    target1: float,
    target2: float,
    breakout_level: float,
    config: Any,
) -> BreakoutExitResult:
    """Simulate only future bars; no entry/exit value reads beyond each step."""

    partial_fraction = float(config.partial_percent)
    remaining = 1.0
    realized = 0.0
    target1_hit = False
    false_breakout = False
    active_stop = float(stop)
    trailing = str(config.target_model).upper() == "TRAILING_ATR"
    # With daily OHLC, the sequence inside the entry candle is unknowable.
    # BREAKOUT_SWING therefore starts exit evaluation on the next completed
    # candle.  This avoids a same-day look-ahead ambiguity and guarantees that
    # the unchanged portfolio simulator can release the position chronologically.
    first_exit_index = entry_index + 1
    if first_exit_index >= len(df):
        raise ValueError("BREAKOUT_SWING requires a completed candle after entry")
    last_index = min(len(df) - 1, entry_index + int(config.max_holding_days))
    final_price = float(df["Close"].iloc[last_index])
    reason = "MAX_HOLDING"

    for i in range(first_exit_index, last_index + 1):
        bar = df.iloc[i]
        low, high, close = float(bar["Low"]), float(bar["High"]), float(bar["Close"])
        if i <= entry_index + 2 and close < breakout_level:
            false_breakout = True

        # Conservative ambiguity: Stop is assumed first when both levels fit.
        if low <= active_stop:
            final_price = active_stop
            realized += remaining * final_price
            remaining = 0.0
            reason = "STOP_LOSS" if not target1_hit else "TRAILING_STOP"
            last_index = i
            break

        if not target1_hit and high >= target1:
            realized += partial_fraction * target1
            remaining -= partial_fraction
            target1_hit = True

        if high >= target2:
            final_price = target2
            realized += remaining * final_price
            remaining = 0.0
            reason = "TARGET2"
            last_index = i
            break

        if trailing and target1_hit:
            candidate = close - float(config.trailing_atr_multiple) * float(bar["ATR"])
            active_stop = max(active_stop, candidate, entry_price)

        final_price = close

    if remaining > 0:
        realized += remaining * final_price
    weighted_exit = realized
    gross_return = (weighted_exit / entry_price - 1) * 100 if entry_price else 0.0
    exit_after_slippage = weighted_exit * (1 - float(config.slippage))
    total_entry_cost = entry_price * (1 + float(config.commission))
    total_exit_value = exit_after_slippage * (1 - float(config.commission))
    net_profit = total_exit_value - total_entry_cost
    return BreakoutExitResult(
        exit_index=last_index,
        exit_price=round(weighted_exit, int(config.price_precision)),
        exit_reason=reason,
        gross_return_percent=round(gross_return, 4),
        net_profit_per_share=round(net_profit, 6),
        target1_hit=target1_hit,
        breakout_failure=bool(reason == "STOP_LOSS" and not target1_hit),
        false_breakout=false_breakout,
        holding_bars=max(0, last_index - entry_index),
    )
