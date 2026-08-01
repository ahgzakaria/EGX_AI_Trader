"""Dedicated Research Only thresholds for the daily AI pullback scenario."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PullbackResearchConfig:
    """All pullback constants live here; none changes a production strategy gate."""

    decision_mode: str = "RESEARCH_ONLY"
    minimum_history_bars: int = 80
    pivot_radius: int = 2
    structure_lookback_bars: int = 80
    maximum_impulse_lookback_bars: int = 60
    slope_lookback_bars: int = 10
    minimum_ema20_slope_percent_per_bar: float = 0.02
    minimum_ema50_slope_percent_per_bar: float = 0.005
    materially_declining_ema50_percent_per_bar: float = -0.03
    minimum_impulse_duration_bars: int = 5
    minimum_impulse_strength_atr: float = 2.0
    minimum_pullback_percent: float = 1.0
    minimum_pullback_atr: float = 0.50
    healthy_retracement_maximum_percent: float = 61.8
    failed_retracement_percent: float = 78.6
    healthy_pullback_maximum_atr: float = 3.0
    failed_pullback_atr: float = 4.5
    support_lookback_bars: int = 60
    support_cluster_tolerance_atr: float = 0.50
    support_cluster_tolerance_percent: float = 1.0
    support_zone_padding_atr: float = 0.15
    support_approach_atr: float = 0.50
    stop_buffer_atr: float = 0.50
    volume_window: int = 20
    contracting_volume_ratio: float = 0.85
    expanding_volume_ratio: float = 1.20
    aggressive_selling_volume_ratio: float = 1.50
    bullish_rejection_wick_to_body: float = 1.50
    bullish_rejection_close_location: float = 0.60
    minimum_reward_risk: float = 1.50
    meaningful_target_minimum_distance_atr: float = 0.75
    transaction_cost_bps: float = 30.0
    research_horizons: tuple[int, ...] = (3, 5, 10, 20)

    def __post_init__(self):
        if self.decision_mode != "RESEARCH_ONLY":
            raise ValueError("pullback scenario must remain RESEARCH_ONLY")
        if self.pivot_radius < 1 or self.minimum_history_bars < 20:
            raise ValueError("invalid pullback history/pivot configuration")
        if not (0 < self.healthy_retracement_maximum_percent
                < self.failed_retracement_percent < 100):
            raise ValueError("invalid retracement thresholds")
        if self.minimum_reward_risk <= 0 or self.stop_buffer_atr <= 0:
            raise ValueError("risk thresholds must be positive")


DEFAULT_PULLBACK_RESEARCH_CONFIG = PullbackResearchConfig()


__all__ = ["PullbackResearchConfig", "DEFAULT_PULLBACK_RESEARCH_CONFIG"]
