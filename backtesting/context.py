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
    # Dynamic Stop Management
    # ==========================

    current_stop: Optional[float] = None

    highest_price: float = 0.0

    break_even_active: bool = False

    trailing_active: bool = False

    # ==========================
    # Result
    # ==========================

    result: Optional[str] = None

    profit: float = 0.0

    # ==========================
    # Position Sizing
    # ==========================

    shares: int = 0

    position_value: float = 0.0

    risk_amount: float = 0.0

    capital_before: float = 0.0

    capital_after: float = 0.0

    net_profit: float = 0.0

    # ==========================
    # Trade
    # ==========================

    trade: Optional[Trade] = None