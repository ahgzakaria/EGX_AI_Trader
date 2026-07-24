"""Independent configuration for the optional scalping module."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP


PRODUCTION_TP_PERCENT = 2.0
PRODUCTION_SL_PERCENT = 2.0


@dataclass(frozen=True)
class ScalpingConfig:
    enabled: bool = False
    mode: str = "PAPER_ONLY"
    take_profit_percent: float = PRODUCTION_TP_PERCENT
    stop_loss_percent: float = PRODUCTION_SL_PERCENT
    require_rubix_fresh: bool = True
    allow_yahoo_actionable: bool = False
    close_at_session_end: bool = True
    entry_cutoff: str = "14:10"
    forced_exit_time: str = "14:25"
    quote_max_age_seconds: int = 60
    commission: float = 0.003
    slippage: float = 0.0005
    initial_capital: float = 100_000.0
    risk_per_trade_percent: float = 0.5
    max_open_positions: int = 3
    max_daily_loss_percent: float = 2.0
    max_trades_per_day: int = 8
    max_consecutive_losses: int = 3
    max_exposure_per_symbol_percent: float = 20.0
    max_spread_percent: float = 0.5
    minimum_liquidity: float = 100_000.0
    minimum_relative_volume: float = 1.0
    portfolio_heat_percent: float = 2.0
    revenge_cooldown_minutes: int = 30
    opening_range_minutes: int = 15
    momentum_lookback_bars: int = 5
    breakout_lookback_bars: int = 20
    minimum_momentum_percent: float = 0.3
    database_path: str = "data/scalping.db"
    # EGX instruments can quote at different decimal precision. Bands remain
    # configurable because the exchange may change instrument tick tables.
    tick_size_bands: tuple = field(default_factory=lambda: (
        {"max_price": 2.0, "tick_size": 0.001},
        {"max_price": None, "tick_size": 0.01},
    ))

    @classmethod
    def from_mapping(cls, values=None):
        values = dict(values or {})
        allowed = cls.__dataclass_fields__
        if isinstance(values.get("tick_size_bands"), list):
            values["tick_size_bands"] = tuple(values["tick_size_bands"])
        return cls(**{key: value for key, value in values.items() if key in allowed})

    def as_dict(self):
        payload = asdict(self)
        payload["tick_size_bands"] = list(self.tick_size_bands)
        return payload

    @property
    def entry_cutoff_time(self):
        return time.fromisoformat(self.entry_cutoff)

    @property
    def forced_exit_clock(self):
        return time.fromisoformat(self.forced_exit_time)

    def tick_size(self, price):
        value = float(price)
        for band in self.tick_size_bands:
            maximum = band.get("max_price")
            if maximum is None or value < float(maximum):
                return float(band["tick_size"])
        raise ValueError(f"No tick-size band configured for price {price}")

    def round_price(self, price, direction="nearest"):
        tick = Decimal(str(self.tick_size(price)))
        units = Decimal(str(price)) / tick
        rounding = {
            "up": ROUND_CEILING, "down": ROUND_FLOOR, "nearest": ROUND_HALF_UP,
        }.get(direction)
        if rounding is None:
            raise ValueError(f"Unsupported rounding direction: {direction}")
        return float(units.quantize(Decimal("1"), rounding=rounding) * tick)
