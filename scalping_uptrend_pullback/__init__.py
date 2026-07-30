"""UPTREND_PULLBACK_SCALPING — short-term uptrend pullback research selector.

A completely independent second scalping strategy. It shares no state, config,
database, watchlist identity, or ranking with Stable Range-Bound Scalping
(``scalping_expected_range``) or the Swing/Daily engine.

The trend definition is EMA5/EMA10 only; EMA20/EMA50 are deliberately not used.
Historical membership and ranking are frozen from completed EODHD Daily history
at a D-1 (or earlier) cutoff, so live Rubix movement cannot reorder the list.
Rubix stays read-only and is not required by this core selector.

Long-only research zones, disabled by default, never places an order.
"""

from scalping_uptrend_pullback.config import (
    LiquidityConfig,
    PullbackProximityConfig,
    ShortTermTrendConfig,
    SupportZoneConfig,
    UpsideRiskConfig,
    UptrendPullbackScoreWeights,
    UptrendPullbackSelectionConfig,
)
from scalping_uptrend_pullback.selection import (
    FrozenUptrendWatchlist,
    LiquidityProfile,
    PullbackProfile,
    ShortTermTrendProfile,
    SupportZone,
    UpsideRiskProfile,
    UptrendPullbackResult,
    UptrendReadiness,
    analyze_uptrend_pullback,
    assess_readiness,
    build_frozen_uptrend_watchlist,
    load_eodhd_daily_history,
)
from scalping_uptrend_pullback.live_readiness import (
    UptrendLiveReadinessBatch,
    UptrendLiveReadinessEngine,
    UptrendLiveReadinessResult,
)
from scalping_uptrend_pullback.states import (
    CANDIDATE_STATES,
    INSUFFICIENT_UPSIDE,
    STRATEGY_IDENTITY,
    STRATEGY_NAME,
)

__all__ = [
    "CANDIDATE_STATES",
    "INSUFFICIENT_UPSIDE",
    "FrozenUptrendWatchlist",
    "LiquidityConfig",
    "LiquidityProfile",
    "PullbackProfile",
    "PullbackProximityConfig",
    "STRATEGY_IDENTITY",
    "STRATEGY_NAME",
    "ShortTermTrendConfig",
    "ShortTermTrendProfile",
    "SupportZone",
    "SupportZoneConfig",
    "UpsideRiskConfig",
    "UpsideRiskProfile",
    "UptrendPullbackResult",
    "UptrendPullbackScoreWeights",
    "UptrendPullbackSelectionConfig",
    "UptrendReadiness",
    "UptrendLiveReadinessBatch",
    "UptrendLiveReadinessEngine",
    "UptrendLiveReadinessResult",
    "analyze_uptrend_pullback",
    "assess_readiness",
    "build_frozen_uptrend_watchlist",
    "load_eodhd_daily_history",
]
