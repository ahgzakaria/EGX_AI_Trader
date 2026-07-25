"""Scalping V2 — long-only range entry model with dynamic stop/targets.

Three independent, separately-explained patterns (never merged into one opaque
BUY): LOWER_RANGE_BOUNCE, MID_RANGE_RECLAIM, RANGE_BREAKOUT_RETEST. Enforces a
no-chasing rule and computes cost-adjusted net RR to three targets.

Research / decision-support / paper only. No broker, no orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from scalping.range_detector import RangeClass, RangeLevels


class EntryPattern(str, Enum):
    LOWER_RANGE_BOUNCE = "LOWER_RANGE_BOUNCE"
    MID_RANGE_RECLAIM = "MID_RANGE_RECLAIM"
    RANGE_BREAKOUT_RETEST = "RANGE_BREAKOUT_RETEST"


class OpportunityStatus(str, Enum):
    RANGE_BUY_READY = "RANGE_BUY_READY"
    RANGE_BUY_WAIT = "RANGE_BUY_WAIT"
    BREAKOUT_RETEST_READY = "BREAKOUT_RETEST_READY"
    WATCH_VOLATILE = "WATCH_VOLATILE"
    RANGE_CONSUMED = "RANGE_CONSUMED"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    LIQUIDITY_TOO_LOW = "LIQUIDITY_TOO_LOW"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class RangeOpportunity:
    ticker: str
    status: OpportunityStatus
    pattern: str | None
    entry_zone_low: float | None
    entry_zone_high: float | None
    stop: float | None
    target_1: float | None
    target_2: float | None
    target_3: float | None
    risk_percent: float | None
    reward_percent_t1: float | None
    net_rr_t1: float | None
    net_rr_t2: float | None
    net_rr_t3: float | None
    remaining_range_percent: float | None
    range_position_percent: float | None
    reason: str = ""
    invalidation: str = ""
    detail: dict = field(default_factory=dict)


def evaluate_entry(
    ticker,
    levels: RangeLevels,
    *,
    bid=None,
    ask=None,
    atr_value=None,
    config,
):
    """Return a :class:`RangeOpportunity` for one symbol from its range levels."""

    if levels.classification == RangeClass.DATA_INSUFFICIENT:
        return _status(ticker, OpportunityStatus.DATA_INSUFFICIENT, levels,
                       reason=levels.reason)

    if levels.classification == RangeClass.ILLIQUID:
        return _status(ticker, OpportunityStatus.LIQUIDITY_TOO_LOW, levels,
                       reason="classified ILLIQUID")

    last = levels.last
    span = (levels.session_high - levels.session_low) if (
        levels.session_high is not None and levels.session_low is not None) else 0.0
    if not last or span <= 0:
        return _status(ticker, OpportunityStatus.DATA_INSUFFICIENT, levels,
                       reason="no usable last/range")

    position = levels.range_position_percent or 0.0

    # No-chasing rule: never issue a bounce/reclaim BUY near the top of range.
    if position >= config.no_chase_range_position_percent and \
            levels.classification != RangeClass.BREAKOUT_IN_PROGRESS:
        return _status(ticker, OpportunityStatus.RANGE_CONSUMED, levels,
                       reason=(f"range position {position:.0f}% >= no-chase "
                               f"{config.no_chase_range_position_percent:.0f}%; "
                               "most of the range already consumed"))

    atr_buffer = (atr_value or 0.0) * config.intraday_atr_buffer_multiple

    # --- Pattern selection (long-only) -----------------------------------
    if levels.classification == RangeClass.BREAKOUT_IN_PROGRESS:
        return _breakout_retest(ticker, levels, atr_buffer, bid, ask, config)

    if position <= config.range_lower_zone_percent:
        return _lower_range_bounce(ticker, levels, atr_buffer, bid, ask, config)

    if levels.midpoint is not None and last < levels.midpoint:
        return _mid_range_reclaim(ticker, levels, atr_buffer, bid, ask, config)

    # Between midpoint and no-chase line, and not a breakout: watch, don't chase.
    return _status(ticker, OpportunityStatus.RANGE_BUY_WAIT, levels,
                   reason=(f"price at {position:.0f}% of range (above lower zone, "
                           "below no-chase); wait for a pullback into the lower zone"))


def _lower_range_bounce(ticker, levels, atr_buffer, bid, ask, config):
    entry_low = levels.session_low
    entry_high = levels.lower_third
    stop = _stop(levels.session_low, levels.intraday_support, atr_buffer)
    return _build(ticker, EntryPattern.LOWER_RANGE_BOUNCE, levels, entry_low,
                  entry_high, stop, bid, ask, config,
                  reason="price in lower range zone; long toward midpoint/upper band",
                  invalidation=f"close below {stop:.3f} (range low / swing / ATR buffer)")


def _mid_range_reclaim(ticker, levels, atr_buffer, bid, ask, config):
    entry_low = levels.midpoint
    entry_high = levels.midpoint * 1.002 if levels.midpoint else None
    stop = _stop(levels.lower_third, levels.intraday_support, atr_buffer)
    return _build(ticker, EntryPattern.MID_RANGE_RECLAIM, levels, entry_low,
                  entry_high, stop, bid, ask, config,
                  reason="reclaim of midpoint; long toward upper third/high",
                  invalidation=f"close back below {stop:.3f}")


def _breakout_retest(ticker, levels, atr_buffer, bid, ask, config):
    # Retest of the broken opening-range/resistance level.
    breakout_level = levels.opening_range_15_high or levels.intraday_resistance
    if breakout_level is None:
        return _status(ticker, OpportunityStatus.WATCH_VOLATILE, levels,
                       reason="breakout in progress but no clean retest level yet")
    entry_low = breakout_level
    entry_high = breakout_level * 1.003
    stop = _stop(breakout_level - atr_buffer, levels.intraday_support, atr_buffer)
    opp = _build(ticker, EntryPattern.RANGE_BREAKOUT_RETEST, levels, entry_low,
                 entry_high, stop, bid, ask, config,
                 reason="confirmed break above opening range; long on successful retest",
                 invalidation=f"close back below {stop:.3f} (failed retest)")
    if opp.status == OpportunityStatus.RANGE_BUY_READY:
        return _replace_status(opp, OpportunityStatus.BREAKOUT_RETEST_READY)
    return opp


def _build(ticker, pattern, levels, entry_low, entry_high, stop, bid, ask, config,
           *, reason, invalidation):
    if entry_low is None or stop is None or stop <= 0:
        return _status(ticker, OpportunityStatus.NO_TRADE, levels,
                       reason="could not compute a valid entry/stop", pattern=pattern.value)

    entry = float(entry_high or entry_low)
    if entry <= stop:
        return _status(ticker, OpportunityStatus.NO_TRADE, levels,
                       reason="stop is not below entry", pattern=pattern.value)

    t1 = levels.midpoint
    t2 = levels.upper_third
    t3 = levels.intraday_resistance or levels.session_high

    # Targets must be above entry and strictly increasing to be usable.
    targets = [t for t in (t1, t2, t3) if t is not None and t > entry]
    if not targets:
        return _status(ticker, OpportunityStatus.RANGE_CONSUMED, levels,
                       reason="no target above entry (reward exhausted)", pattern=pattern.value)

    risk_percent = (entry - stop) / entry * 100.0
    cost_pct = (config.commission_estimate + config.slippage_estimate) * 100.0 * 2  # round-trip

    def net_rr(target):
        if target is None or target <= entry:
            return None
        reward = (target - entry) / entry * 100.0
        net_reward = reward - cost_pct
        return round(net_reward / risk_percent, 3) if risk_percent > 0 else None

    rr1, rr2, rr3 = net_rr(t1), net_rr(t2), net_rr(t3)
    reward_t1 = ((t1 - entry) / entry * 100.0) if (t1 and t1 > entry) else None
    remaining_range = ((levels.session_high - levels.last) /
                       (levels.session_high - levels.session_low) * 100.0) if (
        levels.session_high and levels.session_low and
        levels.session_high > levels.session_low) else None

    # Best available net RR across the three targets decides readiness.
    best_rr = max([r for r in (rr1, rr2, rr3) if r is not None], default=None)
    if best_rr is None or best_rr < config.minimum_net_rr:
        status = OpportunityStatus.RANGE_BUY_WAIT
        reason = (f"{reason}; best net RR {best_rr} < minimum "
                  f"{config.minimum_net_rr} after costs")
    else:
        status = OpportunityStatus.RANGE_BUY_READY

    return RangeOpportunity(
        ticker=ticker, status=status, pattern=pattern.value,
        entry_zone_low=round(float(entry_low), 4),
        entry_zone_high=round(float(entry_high or entry_low), 4),
        stop=round(float(stop), 4),
        target_1=_r(t1), target_2=_r(t2), target_3=_r(t3),
        risk_percent=round(risk_percent, 3),
        reward_percent_t1=round(reward_t1, 3) if reward_t1 is not None else None,
        net_rr_t1=rr1, net_rr_t2=rr2, net_rr_t3=rr3,
        remaining_range_percent=round(remaining_range, 2) if remaining_range is not None else None,
        range_position_percent=levels.range_position_percent,
        reason=reason, invalidation=invalidation,
        detail={"classification": levels.classification.value, "cost_pct_roundtrip": cost_pct},
    )


def _stop(primary, support, atr_buffer):
    """Most conservative (highest protection) stop below entry candidates."""

    candidates = [c for c in (primary, support) if c is not None and c > 0]
    if not candidates:
        return None
    base = min(candidates)  # furthest protective level
    return base - atr_buffer


def _status(ticker, status, levels, *, reason, pattern=None):
    return RangeOpportunity(
        ticker=ticker, status=status, pattern=pattern,
        entry_zone_low=None, entry_zone_high=None, stop=None,
        target_1=None, target_2=None, target_3=None,
        risk_percent=None, reward_percent_t1=None,
        net_rr_t1=None, net_rr_t2=None, net_rr_t3=None,
        remaining_range_percent=None,
        range_position_percent=levels.range_position_percent,
        reason=reason, invalidation="",
        detail={"classification": levels.classification.value},
    )


def _replace_status(opp, status):
    return RangeOpportunity(**{**opp.__dict__, "status": status})


def _r(value):
    return round(float(value), 4) if value is not None else None
