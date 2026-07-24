"""Isolated configuration for SCALPING V3 — EXPECTED_RANGE_SCALPER.

Reads ``scalping_expected_range/settings.json``. Never touches the preserved
fixed-2% ``ScalpingConfig`` (scalping/config.py), the Range Scalper config
(scalping/range_config.py), or config/settings.json. Research defaults only —
never claimed optimal, never activates trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

DEFAULT_SETTINGS_PATH = "scalping_expected_range/settings.json"


@dataclass(frozen=True)
class ExpectedRangeConfig:
    strategy_name: str = "EXPECTED_RANGE_SCALPER"

    historical_window: int = 20
    secondary_windows: tuple = (10, 30)
    minimum_history_sessions: int = 20

    # Score weights (liquidity-first — combined liquidity weight is 60).
    average_volume_weight: float = 30
    average_turnover_weight: float = 25
    volatility_weight: float = 20
    target_frequency_weight: float = 15
    liquidity_consistency_weight: float = 5
    spread_weight: float = 5

    fixed_take_profit_percent: float = 2.0
    fixed_stop_loss_percent: float = 2.0

    # Liquidity hard gate.
    minimum_average_volume: float = 50_000
    minimum_median_volume: float = 30_000
    minimum_average_turnover_egp: float = 500_000.0
    minimum_volume_consistency: float = 0.60
    minimum_two_percent_frequency: float = 0.30
    low_volume_session_ratio: float = 0.50
    maximum_low_volume_session_fraction: float = 0.40
    maximum_zero_volume_sessions: int = 2

    # Live executability (never alters the historical ranking).
    maximum_live_spread_percent: float = 0.6
    maximum_quote_age_seconds: int = 90

    # Expected-range percentiles.
    expected_range_conservative_percentile: float = 25
    expected_range_base_percentile: float = 50
    expected_range_high_percentile: float = 75

    no_chase_remaining_upside_percent: float = 2.0
    range_lower_zone_percent: float = 25.0
    range_upper_zone_percent: float = 75.0

    commission_estimate: float = 0.003
    slippage_estimate: float = 0.0007

    atr_period: int = 14

    database_path: str = "data/expected_range_scalping.db"

    production_enabled: bool = False
    paper_enabled: bool = False
    decision_support_only: bool = True
    automatic_execution: bool = False
    broker_orders_enabled: bool = False
    strategy_version: str = "ERS-1.1-paper"

    use_rubix_final_daily: bool = True
    normalized_daily_cache_path: str = "data/normalized_daily_cache.db"
    paper_output_root: str = "reports/expected_range_paper"
    outcome_horizons_minutes: tuple = (1, 3, 5, 10, 20)
    minimum_paper_sessions_before_judgment: int = 20
    minimum_executable_signals_before_judgment: int = 30
    minimum_signals_per_scenario_before_judgment: int = 20

    @classmethod
    def load(cls, path=DEFAULT_SETTINGS_PATH):
        file = Path(path)
        if not file.is_file():
            return cls()
        return cls.from_mapping(json.loads(file.read_text(encoding="utf-8")))

    @classmethod
    def from_mapping(cls, values=None):
        values = {k: v for k, v in dict(values or {}).items() if not k.startswith("_")}
        allowed = cls.__dataclass_fields__
        if isinstance(values.get("secondary_windows"), list):
            values["secondary_windows"] = tuple(values["secondary_windows"])
        if isinstance(values.get("outcome_horizons_minutes"), list):
            values["outcome_horizons_minutes"] = tuple(values["outcome_horizons_minutes"])
        return cls(**{k: v for k, v in values.items() if k in allowed})

    @property
    def total_weight(self) -> float:
        return float(
            self.average_volume_weight + self.average_turnover_weight
            + self.volatility_weight + self.target_frequency_weight
            + self.liquidity_consistency_weight + self.spread_weight
        )
