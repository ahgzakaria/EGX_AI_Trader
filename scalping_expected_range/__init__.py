"""SCALPING V3 — EXPECTED_RANGE_SCALPER (isolated research support).

The pre-session selector ranks stable horizontal support/resistance channels
from completed EODHD Daily history. Daily volatility is only a minimum
opportunity condition. Live Rubix readiness remains a separate, read-only
research layer over the immutable frozen candidates.

Completely isolated from the Swing/Daily engine, the Adaptive Selector, the AI
ranking, the preserved fixed-2% Scalping strategy, the Intraday Range Scalper,
the Event-Driven Data Gate and the Rubix collector. Long-only, disabled by
default, never places an order.
"""

from scalping_expected_range.config import (
    DailyRangeBoundConfig,
    DailyHistoricalSelectionConfig,
    ExpectedRangeConfig,
    RangeBoundScoreWeights,
)

__all__ = [
    "DailyHistoricalSelectionConfig",
    "DailyRangeBoundConfig",
    "ExpectedRangeConfig",
    "RangeBoundScoreWeights",
]
