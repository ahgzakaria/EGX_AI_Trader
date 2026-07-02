from dataclasses import dataclass

from typing import Optional

from backtesting.trade import Trade


@dataclass
class BacktestContext:

    # ==========================
    # Market
    # ==========================

    symbol: str

    data: object

    signal_index: int

    signal: dict

    # ==========================
    # Entry
    # ==========================

    entry_price: Optional[float] = None

    entry_date: Optional[str] = None

    entry_index: Optional[int] = None

    # ==========================
    # Exit
    # ==========================

    exit_price: Optional[float] = None

    exit_date: Optional[str] = None

    exit_index: Optional[int] = None

    exit_reason: Optional[str] = None

    # ==========================
    # Result
    # ==========================

    result: Optional[str] = None

    profit: float = 0.0

    # ==========================
    # Trade
    # ==========================

    trade: Optional[Trade] = None