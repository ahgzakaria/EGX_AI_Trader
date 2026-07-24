"""Typed scalping domain records with no dependency on the swing engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4


class SetupType(str, Enum):
    MOMENTUM_BREAKOUT = "MOMENTUM_BREAKOUT"
    OPENING_RANGE_BREAKOUT = "OPENING_RANGE_BREAKOUT"
    VWAP_RECLAIM_FIRST_PULLBACK = "VWAP_RECLAIM_FIRST_PULLBACK"


class TradeStatus(str, Enum):
    TARGET_HIT = "TARGET_HIT"
    STOP_HIT = "STOP_HIT"
    SESSION_CLOSE = "SESSION_CLOSE"
    MANUAL_EXIT = "MANUAL_EXIT"
    DATA_STALE = "DATA_STALE"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class MarketSnapshot:
    ticker: str
    timestamp: datetime
    last: float | None
    bid: float | None
    ask: float | None
    volume: float | None
    provider: str
    freshness: str
    quote_age_seconds: float | None
    session_phase: str
    bid_size: float | None = None
    ask_size: float | None = None
    collector_status: str | None = None


@dataclass(frozen=True)
class Opportunity:
    ticker: str
    setup: SetupType
    timestamp: datetime
    signal_price: float
    ask: float
    bid: float
    volume: float
    spread_percent: float
    score: float
    reasons: tuple[str, ...]
    freshness: str
    actionable: bool
    blocked_reason: str | None = None
    ai_probability: float | None = None
    opportunity_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class EntryFill:
    signal_price: float
    requested_entry_price: float
    actual_entry_fill: float
    target_price: float
    stop_price: float
    quantity: int
    entry_cost: float
    slippage_cost: float


@dataclass
class Position:
    position_id: str
    signal_id: str
    ticker: str
    setup: str
    opened_at: datetime
    entry: EntryFill
    status: str = "OPEN"
    current_bid: float | None = None


@dataclass(frozen=True)
class ExitResult:
    status: TradeStatus
    exited_at: datetime
    exit_fill: float
    gross_return_percent: float
    trading_costs: float
    slippage_cost: float
    net_return_percent: float
    realized_pnl: float
    ambiguous_same_bar: bool = False


@dataclass
class DailyRiskState:
    session_date: str
    starting_equity: float
    realized_pnl: float = 0.0
    trades_count: int = 0
    open_positions: int = 0
    consecutive_losses: int = 0
    portfolio_heat: float = 0.0
    symbol_exposure: dict[str, float] = field(default_factory=dict)
    last_exit_at: dict[str, datetime] = field(default_factory=dict)
