"""Configuration for the Scalping V2 Range Scanner (isolated, research-only).

Reads scalping/range_scalper_settings.json. Never touches the preserved
fixed-2% ScalpingConfig (scalping/config.py) or config/settings.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path


DEFAULT_SETTINGS_PATH = "scalping/range_scalper_settings.json"


@dataclass(frozen=True)
class RangeScalperConfig:
    enabled: bool = False
    mode: str = "PAPER_ONLY"
    activation_delay_minutes: int = 30
    activation_variants_minutes: tuple = (15, 30, 45)
    minimum_data_coverage: float = 0.60
    maximum_quote_age_seconds: int = 90
    minimum_quote_updates: int = 30
    maximum_spread_percent: float = 0.6
    minimum_turnover_egp: float = 500_000.0
    minimum_adr_percentile: float = 50
    minimum_session_range_percentile: float = 50
    minimum_turnover_percentile: float = 40
    range_lower_zone_percent: float = 33.0
    range_upper_zone_percent: float = 66.0
    no_chase_range_position_percent: float = 70.0
    intraday_atr_buffer_multiple: float = 0.5
    intraday_atr_period: int = 14
    minimum_net_rr: float = 1.5
    slippage_estimate: float = 0.0007
    commission_estimate: float = 0.003
    target_model: str = "RANGE_MIDPOINT_UPPER_THIRD_HIGH"
    stop_model: str = "MAX_OF_RANGE_LOW_SWING_ATR"
    opening_range_minutes_primary: int = 15
    opening_range_minutes_secondary: int = 30
    adr_lookback_sessions: int = 20
    atr_lookback_sessions: int = 14
    database_path: str = "data/scalping_range.db"

    @classmethod
    def load(cls, path=DEFAULT_SETTINGS_PATH):
        file = Path(path)
        if not file.is_file():
            return cls()
        data = json.loads(file.read_text(encoding="utf-8"))
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, values=None):
        values = {k: v for k, v in dict(values or {}).items() if not k.startswith("_")}
        allowed = cls.__dataclass_fields__
        if isinstance(values.get("activation_variants_minutes"), list):
            values["activation_variants_minutes"] = tuple(values["activation_variants_minutes"])
        return cls(**{k: v for k, v in values.items() if k in allowed})
