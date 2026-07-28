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
class DailyHistoricalScoreWeights:
    """Typed, daily-only Historical Scalping Potential weights."""

    movement_potential: float = 0.40
    range_stability: float = 0.25
    zone_consistency: float = 0.20
    liquidity: float = 0.15

    def __post_init__(self):
        total = (
            self.movement_potential
            + self.range_stability
            + self.zone_consistency
            + self.liquidity
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"daily historical score weights must sum to 1.0, got {total}")
        if min(
            self.movement_potential,
            self.range_stability,
            self.zone_consistency,
            self.liquidity,
        ) < 0:
            raise ValueError("daily historical score weights cannot be negative")


@dataclass(frozen=True)
class DailyHistoricalEligibility:
    """Hard data/opportunity gates, separate from ranking-quality scores."""

    minimum_median_range_percent: float = 1.50
    minimum_two_percent_frequency: float = 0.30
    minimum_median_turnover_egp: float = 1_000_000.0


@dataclass(frozen=True)
class DailyZoneConsistencyConfig:
    """Explainable robust-zone formulation and severe-behaviour safety floor."""

    dispersion_mad_ratio_limit: float = 0.35
    dispersion_iqr_ratio_limit: float = 0.80
    expected_zone_mad_multiplier: float = 2.50
    expected_zone_minimum_range_fraction: float = 0.15
    outlier_rate_scale: float = 0.25
    divergence_ratio_scale: float = 0.75

    dispersion_weight: float = 0.40
    coverage_weight: float = 0.30
    outlier_weight: float = 0.20
    divergence_weight: float = 0.10

    conservative_minimum_weight: float = 0.65
    balanced_geometric_weight: float = 0.35
    # The conservative minimum/geometric combination already penalizes
    # imbalance. The selected default stays zero to keep the score monotonic;
    # non-zero values are sensitivity-test controls only.
    asymmetry_penalty_strength: float = 0.0

    severe_combined_floor: float = 20.0
    severe_side_floor: float = 10.0
    severe_outlier_rate: float = 0.35
    event_gap_rate: float = 0.20
    event_divergence_ratio: float = 1.50

    very_stable_threshold: float = 70.0
    stable_threshold: float = 60.0
    moderate_threshold: float = 45.0
    unstable_threshold: float = 30.0

    recent_deterioration_tolerance: float = 5.0
    recent_penalty_per_zone_point: float = 0.10
    maximum_recent_penalty: float = 3.0
    confirmation_transition_points: float = 8.0

    def __post_init__(self):
        side_weights = (
            self.dispersion_weight
            + self.coverage_weight
            + self.outlier_weight
            + self.divergence_weight
        )
        combination_weights = (
            self.conservative_minimum_weight
            + self.balanced_geometric_weight
        )
        if abs(side_weights - 1.0) > 1e-9:
            raise ValueError("zone side weights must sum to 1.0")
        if abs(combination_weights - 1.0) > 1e-9:
            raise ValueError("zone combination weights must sum to 1.0")
        positive = (
            self.dispersion_mad_ratio_limit,
            self.dispersion_iqr_ratio_limit,
            self.expected_zone_mad_multiplier,
            self.expected_zone_minimum_range_fraction,
            self.outlier_rate_scale,
            self.divergence_ratio_scale,
        )
        if min(positive) <= 0:
            raise ValueError("zone normalization limits must be positive")
        bounded = (
            self.asymmetry_penalty_strength,
            self.severe_outlier_rate,
            self.event_gap_rate,
        )
        if any(value < 0 or value > 1 for value in bounded):
            raise ValueError("zone rates and penalty strength must be within [0, 1]")
        labels = (
            self.very_stable_threshold,
            self.stable_threshold,
            self.moderate_threshold,
            self.unstable_threshold,
        )
        if tuple(sorted(labels, reverse=True)) != labels:
            raise ValueError("zone confidence thresholds must be descending")
        if self.maximum_recent_penalty < 0:
            raise ValueError("maximum recent penalty cannot be negative")


@dataclass(frozen=True)
class DailyHistoricalSelectionConfig:
    """Configuration for the EODHD-daily baseline selector only.

    It intentionally contains no live-price, spread, momentum, RVOL or Rubix
    fields. Intraday enrichment has its own readiness gate.
    """

    source_provider: str = "EODHD_DAILY"
    metric_version: str = "DAILY_HISTORICAL_SELECTION_V2"
    config_version: str = "DAILY_HISTORICAL_SELECTION_CONFIG_V2"
    raw_adjusted_mode: str = "SPLIT_ADJUSTED_OHLC_EVENT_SPECIFIC_VOLUME"
    lookback_sessions: int = 60
    recent_confirmation_sessions: int = 30
    minimum_valid_sessions: int = 30
    preferred_valid_sessions: int = 60
    require_preferred_depth_for_candidates: bool = True
    candidate_display_limit: int = 20
    maximum_stale_calendar_days: int = 10
    intraday_enrichment_minimum_sessions: int = 20
    useful_range_percent: float = 1.50
    outlier_mad_z: float = 3.50
    robust_band_mad_multiplier: float = 2.0
    abnormal_gap_percent: float = 10.0
    weights: DailyHistoricalScoreWeights = field(
        default_factory=DailyHistoricalScoreWeights
    )
    zone: DailyZoneConsistencyConfig = field(
        default_factory=DailyZoneConsistencyConfig
    )
    eligibility: DailyHistoricalEligibility = field(
        default_factory=DailyHistoricalEligibility
    )

    def __post_init__(self):
        if self.source_provider != "EODHD_DAILY":
            raise ValueError("daily historical selection source must be EODHD_DAILY")
        if self.minimum_valid_sessions < 1:
            raise ValueError("minimum_valid_sessions must be positive")
        if self.preferred_valid_sessions < self.minimum_valid_sessions:
            raise ValueError("preferred_valid_sessions cannot be below the minimum")
        if self.lookback_sessions < self.preferred_valid_sessions:
            raise ValueError("lookback_sessions cannot be below the preferred history")
        if self.recent_confirmation_sessions < self.minimum_valid_sessions:
            raise ValueError("recent confirmation cannot be below minimum history")
        if self.recent_confirmation_sessions > self.lookback_sessions:
            raise ValueError("recent confirmation cannot exceed the primary lookback")
        if self.candidate_display_limit < 1:
            raise ValueError("candidate display limit must be positive")
        if self.maximum_stale_calendar_days < 0:
            raise ValueError("maximum stale calendar days cannot be negative")
        if self.intraday_enrichment_minimum_sessions < 1:
            raise ValueError("intraday enrichment minimum must be positive")
        if self.abnormal_gap_percent <= 0:
            raise ValueError("abnormal gap threshold must be positive")


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
