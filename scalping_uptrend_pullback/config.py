"""Typed configuration for UPTREND_PULLBACK_SCALPING.

Reads ``scalping_uptrend_pullback/settings.json``. It never touches the Stable
Range-Bound selector config (scalping_expected_range/config.py), the preserved
fixed-2% ``ScalpingConfig``, the Range Scalper config, or config/settings.json.

Every threshold the selector uses lives here. No magic number is allowed to
appear inline in the selection code, so each gate can be moved and tested from
one place. Research defaults only — never claimed optimal, never activates
trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from scalping_uptrend_pullback.states import (
    EODHD_DAILY,
    STRATEGY_IDENTITY,
    STRATEGY_NAME,
    UPTREND_NEAR_SUPPORT,
)

DEFAULT_SETTINGS_PATH = "scalping_uptrend_pullback/settings.json"


@dataclass(frozen=True)
class UptrendPullbackScoreWeights:
    """Uptrend Pullback Scalping Score composition (must sum to 1.0)."""

    short_term_trend_quality: float = 0.30
    support_confluence: float = 0.30
    pullback_quality: float = 0.20
    liquidity: float = 0.10
    upside_versus_risk: float = 0.10

    def __post_init__(self):
        values = (
            self.short_term_trend_quality,
            self.support_confluence,
            self.pullback_quality,
            self.liquidity,
            self.upside_versus_risk,
        )
        if abs(sum(values) - 1.0) > 1e-9:
            raise ValueError("uptrend pullback score weights must sum to 1.0")
        if min(values) < 0:
            raise ValueError("uptrend pullback score weights cannot be negative")


@dataclass(frozen=True)
class ShortTermTrendConfig:
    """EMA5/EMA10 trend definition. EMA20/EMA50 are deliberately absent."""

    fast_ema_period: int = 5
    slow_ema_period: int = 10

    # Slopes are percent-of-price per session over the slope lookback.
    slope_lookback_sessions: int = 3
    minimum_fast_slope_percent_per_session: float = 0.05
    minimum_slow_slope_percent_per_session: float = 0.02
    healthy_fast_slope_percent_per_session: float = 0.45
    healthy_slow_slope_percent_per_session: float = 0.30

    # Trend age is measured over a wide window: a pullback deep enough to dip
    # EMA5 under EMA10 must not erase the fact that the trend exists.
    trend_age_window: int = 30
    minimum_trend_sessions: int = 10

    # "Not structurally below EMA10": the last close must sit inside the
    # tolerance band, and only a limited number of recent closes may breach it.
    trend_confirmation_window: int = 10
    below_slow_ema_tolerance_percent: float = 1.00
    maximum_closes_below_slow_ema: int = 3

    # EMA separation: some spread confirms the trend, too much is stretched.
    ideal_ema_spread_minimum_percent: float = 0.20
    ideal_ema_spread_maximum_percent: float = 3.00
    maximum_ema_spread_percent: float = 8.00

    # Swing structure from confirmed daily pivots.
    structure_pivot_radius: int = 2
    structure_lookback_sessions: int = 30
    minimum_structure_pivots: int = 2
    descending_structure_tolerance_percent: float = 1.00

    def __post_init__(self):
        if not 1 <= self.fast_ema_period < self.slow_ema_period:
            raise ValueError("fast EMA period must be positive and below the slow period")
        if self.slow_ema_period > 15:
            raise ValueError(
                "UPTREND_PULLBACK_SCALPING is a short-term selector; the slow "
                "EMA may not be an EMA20/EMA50-style trend definition"
            )
        if self.slope_lookback_sessions < 1:
            raise ValueError("slope lookback must be at least one session")
        if self.trend_age_window < self.minimum_trend_sessions:
            raise ValueError("trend age window cannot be below the minimum trend sessions")
        if self.minimum_trend_sessions < 1:
            raise ValueError("minimum trend sessions must be positive")
        if self.trend_confirmation_window < 1:
            raise ValueError("trend confirmation window must be positive")
        if self.below_slow_ema_tolerance_percent < 0:
            raise ValueError("below-EMA tolerance cannot be negative")
        if self.maximum_closes_below_slow_ema < 0:
            raise ValueError("maximum closes below the slow EMA cannot be negative")
        if not (
            0
            <= self.ideal_ema_spread_minimum_percent
            < self.ideal_ema_spread_maximum_percent
            < self.maximum_ema_spread_percent
        ):
            raise ValueError("EMA spread thresholds must be ordered and positive")
        if self.structure_pivot_radius < 1:
            raise ValueError("structure pivot radius must be positive")
        if self.minimum_structure_pivots < 2:
            raise ValueError("at least two pivots are needed to judge direction")
        if self.descending_structure_tolerance_percent < 0:
            raise ValueError("descending-structure tolerance cannot be negative")
        if min(
            self.minimum_fast_slope_percent_per_session,
            self.minimum_slow_slope_percent_per_session,
        ) < 0:
            raise ValueError("minimum EMA slopes cannot be negative")
        if (
            self.healthy_fast_slope_percent_per_session
            <= self.minimum_fast_slope_percent_per_session
            or self.healthy_slow_slope_percent_per_session
            <= self.minimum_slow_slope_percent_per_session
        ):
            raise ValueError("healthy EMA slopes must exceed the minimum slopes")


@dataclass(frozen=True)
class SupportZoneConfig:
    """Confluence-based support zone built only from completed daily bars."""

    swing_low_pivot_radius: int = 2
    swing_low_lookback_sessions: int = 40
    maximum_swing_lows: int = 8

    # Maximum gap between two neighbouring levels of the same zone. Chaining is
    # additionally capped by ``maximum_zone_width_percent``.
    cluster_tolerance_percent: float = 2.00
    ema_zone_half_width_percent: float = 0.60
    minimum_zone_half_width_percent: float = 0.25
    maximum_zone_width_percent: float = 4.00

    # Prior consolidation / breakout shelf detection.
    consolidation_lookback_sessions: int = 40
    consolidation_window_sessions: int = 5
    consolidation_maximum_range_percent: float = 3.50
    breakout_confirmation_percent: float = 2.00

    # Daily-bar proxies. Never claimed as intraday reactions.
    touch_tolerance_percent: float = 1.00
    reaction_lookahead_sessions: int = 3
    reaction_advance_percent: float = 1.50

    minimum_support_sources: int = 2
    minimum_touch_count: int = 2
    minimum_support_strength: float = 40.0
    strong_touch_count: int = 5

    invalidation_buffer_percent: float = 1.50

    # Strength composition (must sum to 1.0).
    source_diversity_weight: float = 0.35
    touch_weight: float = 0.30
    reaction_proxy_weight: float = 0.20
    tightness_weight: float = 0.15

    def __post_init__(self):
        if self.swing_low_pivot_radius < 1:
            raise ValueError("swing-low pivot radius must be positive")
        if self.maximum_swing_lows < 1:
            raise ValueError("maximum swing lows must be positive")
        if self.swing_low_lookback_sessions <= 2 * self.swing_low_pivot_radius:
            raise ValueError("swing-low lookback is too short for the pivot radius")
        if self.consolidation_window_sessions < 2:
            raise ValueError("consolidation window must span at least two sessions")
        if self.consolidation_lookback_sessions < self.consolidation_window_sessions:
            raise ValueError("consolidation lookback is shorter than its window")
        if not (
            0
            < self.minimum_zone_half_width_percent
            <= self.ema_zone_half_width_percent
        ):
            raise ValueError("zone half-width thresholds must be ordered and positive")
        if self.maximum_zone_width_percent <= 2 * self.minimum_zone_half_width_percent:
            raise ValueError("maximum zone width is below the minimum zone width")
        if self.cluster_tolerance_percent <= 0:
            raise ValueError("cluster tolerance must be positive")
        if self.touch_tolerance_percent <= 0:
            raise ValueError("touch tolerance must be positive")
        if self.reaction_lookahead_sessions < 1:
            raise ValueError("reaction lookahead must be at least one session")
        if self.invalidation_buffer_percent < 0:
            raise ValueError("invalidation buffer cannot be negative")
        if self.minimum_support_sources < 1:
            raise ValueError("at least one support source is required")
        if self.minimum_touch_count < 0:
            raise ValueError("minimum touch count cannot be negative")
        if not 0 <= self.minimum_support_strength <= 100:
            raise ValueError("minimum support strength must be within [0, 100]")
        if self.strong_touch_count < 1:
            raise ValueError("strong touch count must be positive")
        weights = (
            self.source_diversity_weight,
            self.touch_weight,
            self.reaction_proxy_weight,
            self.tightness_weight,
        )
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("support strength weights must sum to 1.0")
        if min(weights) < 0:
            raise ValueError("support strength weights cannot be negative")


@dataclass(frozen=True)
class PullbackProximityConfig:
    """Proximity bands and pullback-quality thresholds.

    Distance is measured from the last completed close to the nearest edge of
    the support zone, as a percentage of that edge. A close inside the zone is
    distance zero.
    """

    near_support_maximum_percent: float = 3.00
    wait_for_pullback_maximum_percent: float = 6.00

    pullback_reference_lookback_sessions: int = 20
    maximum_pullback_depth_percent: float = 12.00
    ideal_pullback_depth_minimum_percent: float = 2.00
    ideal_pullback_depth_maximum_percent: float = 8.00

    # A controlled pullback drifts back on contracting volume without a large
    # single-session break.
    maximum_single_session_drop_percent: float = 5.00
    ideal_pullback_volume_ratio: float = 0.80
    maximum_pullback_volume_ratio: float = 1.60

    depth_weight: float = 0.50
    orderliness_weight: float = 0.30
    volume_contraction_weight: float = 0.20

    def __post_init__(self):
        if not 0 < self.near_support_maximum_percent < self.wait_for_pullback_maximum_percent:
            raise ValueError("proximity bands must be positive and ordered")
        if self.pullback_reference_lookback_sessions < 2:
            raise ValueError("pullback reference lookback must span at least two sessions")
        if not (
            0
            < self.ideal_pullback_depth_minimum_percent
            < self.ideal_pullback_depth_maximum_percent
            <= self.maximum_pullback_depth_percent
        ):
            raise ValueError("pullback depth thresholds must be ordered")
        if self.maximum_single_session_drop_percent <= 0:
            raise ValueError("maximum single-session drop must be positive")
        if not 0 < self.ideal_pullback_volume_ratio < self.maximum_pullback_volume_ratio:
            raise ValueError("pullback volume ratios must be positive and ordered")
        weights = (
            self.depth_weight,
            self.orderliness_weight,
            self.volume_contraction_weight,
        )
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("pullback quality weights must sum to 1.0")
        if min(weights) < 0:
            raise ValueError("pullback quality weights cannot be negative")


@dataclass(frozen=True)
class UpsideRiskConfig:
    """First research target and invalidation-risk geometry."""

    resistance_pivot_radius: int = 2
    resistance_lookback_sessions: int = 60
    minimum_upside_percent: float = 1.50
    ideal_reward_risk_ratio: float = 2.50
    minimum_scored_reward_risk_ratio: float = 0.50

    def __post_init__(self):
        if self.resistance_pivot_radius < 1:
            raise ValueError("resistance pivot radius must be positive")
        if self.resistance_lookback_sessions <= 2 * self.resistance_pivot_radius:
            raise ValueError("resistance lookback is too short for the pivot radius")
        if self.minimum_upside_percent < 0:
            raise ValueError("minimum upside cannot be negative")
        if (
            self.ideal_reward_risk_ratio
            <= self.minimum_scored_reward_risk_ratio
            <= 0
        ):
            raise ValueError("reward/risk scaling bounds must be positive and ordered")


@dataclass(frozen=True)
class LiquidityConfig:
    """Hard executability gate and the 0-100 liquidity component."""

    minimum_median_volume: float = 30_000.0
    minimum_median_turnover_egp: float = 1_000_000.0
    strong_median_turnover_egp: float = 50_000_000.0
    strong_median_volume: float = 3_000_000.0
    consistency_reference_fraction: float = 0.50

    turnover_weight: float = 0.55
    volume_weight: float = 0.20
    consistency_weight: float = 0.20
    positive_volume_weight: float = 0.05

    def __post_init__(self):
        if self.minimum_median_volume <= 0 or self.minimum_median_turnover_egp <= 0:
            raise ValueError("liquidity minimums must be positive")
        if self.strong_median_turnover_egp <= self.minimum_median_turnover_egp:
            raise ValueError("strong turnover must exceed the minimum turnover")
        if self.strong_median_volume <= self.minimum_median_volume:
            raise ValueError("strong volume must exceed the minimum volume")
        if not 0 < self.consistency_reference_fraction <= 1:
            raise ValueError("consistency reference fraction must be within (0, 1]")
        weights = (
            self.turnover_weight,
            self.volume_weight,
            self.consistency_weight,
            self.positive_volume_weight,
        )
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("liquidity weights must sum to 1.0")
        if min(weights) < 0:
            raise ValueError("liquidity weights cannot be negative")


@dataclass(frozen=True)
class UptrendPullbackSelectionConfig:
    """Complete configuration for the historical uptrend-pullback selector.

    It intentionally contains no live-price, spread, RVOL or Rubix field.
    Historical membership and ranking are frozen before the session.
    """

    strategy_name: str = STRATEGY_NAME
    strategy_identity: str = STRATEGY_IDENTITY
    source_provider: str = EODHD_DAILY
    metric_version: str = "UPTREND_PULLBACK_SCALPING_SELECTION_V1"
    config_version: str = "UPTREND_PULLBACK_SCALPING_CONFIG_V1"
    raw_adjusted_mode: str = "SPLIT_ADJUSTED_OHLC_EVENT_SPECIFIC_VOLUME"

    lookback_sessions: int = 60
    minimum_valid_sessions: int = 40
    preferred_valid_sessions: int = 60
    require_preferred_depth_for_candidates: bool = False
    maximum_stale_calendar_days: int = 10

    # Top 20 is a maximum only; fewer candidates is a valid, honest outcome.
    candidate_display_limit: int = 20

    eligible_states: tuple = (UPTREND_NEAR_SUPPORT,)

    database_path: str = "data/uptrend_pullback_watchlists.db"
    production_enabled: bool = False
    paper_enabled: bool = False
    decision_support_only: bool = True
    automatic_execution: bool = False
    broker_orders_enabled: bool = False

    weights: UptrendPullbackScoreWeights = field(
        default_factory=UptrendPullbackScoreWeights
    )
    trend: ShortTermTrendConfig = field(default_factory=ShortTermTrendConfig)
    support: SupportZoneConfig = field(default_factory=SupportZoneConfig)
    pullback: PullbackProximityConfig = field(
        default_factory=PullbackProximityConfig
    )
    upside: UpsideRiskConfig = field(default_factory=UpsideRiskConfig)
    liquidity: LiquidityConfig = field(default_factory=LiquidityConfig)

    def __post_init__(self):
        if self.source_provider != EODHD_DAILY:
            raise ValueError("uptrend pullback selection source must be EODHD_DAILY")
        if self.strategy_identity != STRATEGY_IDENTITY:
            raise ValueError(
                f"strategy identity must remain {STRATEGY_IDENTITY}"
            )
        if self.minimum_valid_sessions < 1:
            raise ValueError("minimum_valid_sessions must be positive")
        if self.preferred_valid_sessions < self.minimum_valid_sessions:
            raise ValueError("preferred_valid_sessions cannot be below the minimum")
        if self.lookback_sessions < self.preferred_valid_sessions:
            raise ValueError("lookback_sessions cannot be below the preferred history")
        if self.candidate_display_limit < 1:
            raise ValueError("candidate display limit must be positive")
        if self.maximum_stale_calendar_days < 0:
            raise ValueError("maximum stale calendar days cannot be negative")
        if self.automatic_execution or self.broker_orders_enabled:
            raise ValueError(
                "UPTREND_PULLBACK_SCALPING is research-only and may not enable "
                "automatic execution or broker orders"
            )
        if not self.eligible_states:
            raise ValueError("at least one candidate state must be eligible")
        from scalping_uptrend_pullback.states import CANDIDATE_STATES

        unknown = set(self.eligible_states) - set(CANDIDATE_STATES)
        if unknown:
            raise ValueError(f"unknown eligible states: {sorted(unknown)}")
        required_history = max(
            self.trend.structure_lookback_sessions,
            self.support.swing_low_lookback_sessions,
            self.support.consolidation_lookback_sessions,
            self.pullback.pullback_reference_lookback_sessions,
            self.trend.slow_ema_period + self.trend.trend_age_window,
        )
        if self.minimum_valid_sessions < required_history:
            raise ValueError(
                "minimum_valid_sessions is below the history the configured "
                f"windows require ({required_history})"
            )

    @classmethod
    def load(cls, path=DEFAULT_SETTINGS_PATH) -> "UptrendPullbackSelectionConfig":
        file = Path(path)
        if not file.is_file():
            return cls()
        return cls.from_mapping(json.loads(file.read_text(encoding="utf-8")))

    @classmethod
    def from_mapping(cls, values=None) -> "UptrendPullbackSelectionConfig":
        values = {
            key: value
            for key, value in dict(values or {}).items()
            if not key.startswith("_")
        }
        nested = {
            "weights": UptrendPullbackScoreWeights,
            "trend": ShortTermTrendConfig,
            "support": SupportZoneConfig,
            "pullback": PullbackProximityConfig,
            "upside": UpsideRiskConfig,
            "liquidity": LiquidityConfig,
        }
        for key, factory in nested.items():
            section = values.get(key)
            if isinstance(section, dict):
                allowed = factory.__dataclass_fields__
                values[key] = factory(
                    **{k: v for k, v in section.items() if k in allowed}
                )
        if isinstance(values.get("eligible_states"), list):
            values["eligible_states"] = tuple(values["eligible_states"])
        allowed = cls.__dataclass_fields__
        return cls(**{k: v for k, v in values.items() if k in allowed})
