"""Hybrid EGX Daily OHLCV Bridge (isolated, read-only, records-only).

Replaces the Yahoo-only freshness dependency: a trusted external provider supplies
older completed history while a local Rubix finalizer builds the latest completed
session from captured chronological events. A reconciliation layer records the
source of every row into a normalized daily cache with per-row provenance.

This package NEVER modifies a trading strategy, expected-range calculation,
scoring weight, TP/SL, or scenario logic; never enables production; never
overwrites historical rows silently; never fabricates or forward-fills bars; and
never treats Yahoo as ground truth.
"""

from core.daily_bridge.schema import NormalizedDailyBar

__all__ = ["NormalizedDailyBar"]
