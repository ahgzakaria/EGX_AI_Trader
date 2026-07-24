"""SCALPING V3 — EXPECTED_RANGE_SCALPER (isolated, decision-support, paper-only).

A liquidity-first pre-session scalping selector: rank the EGX universe by
consistent Average Volume/Turnover first, historical daily volatility and 2%
opportunity frequency second, estimate an expected daily range from completed
daily history, then use live Rubix quotes only to locate price inside that range
and display every long-entry scenario for the user to decide.

Completely isolated from the Swing/Daily engine, the Adaptive Selector, the AI
ranking, the preserved fixed-2% Scalping strategy, the Intraday Range Scalper,
the Event-Driven Data Gate and the Rubix collector. Long-only, disabled by
default, never places an order.
"""

from scalping_expected_range.config import ExpectedRangeConfig

__all__ = ["ExpectedRangeConfig"]
