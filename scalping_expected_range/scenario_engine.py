"""Phase 4/6/7/8 — expected-range scenario engine for EXPECTED_RANGE_SCALPER.

Long-only, decision-support only. Given the pre-session expected range and a live
Rubix quote snapshot, it locates the current price inside the expected range,
tests fixed +2% target feasibility after costs, and evaluates every long-entry
scenario INDEPENDENTLY. It never collapses situations into one generic BUY and
never emits a signal without a named scenario.

Honesty on live evidence: a single Rubix snapshot gives Last/Bid/Ask/Spread/
cumulative-Volume/quote-age but not the intraday sequence. Scenarios that need a
genuine recovery/reclaim/retest are therefore surfaced as WAIT/WATCH with the
full plan (entry, +2% target, -2% stop, invalidation) pre-computed; the live
monitor promotes them to READY during the session. This module fabricates no
intraday path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# --- scenario names ---
S1_LOWER_BOUNCE = "EXPECTED_LOWER_RANGE_BOUNCE"
S2_DIP_RECLAIM = "DIP_AND_RECLAIM"
S3_CONTINUATION = "TREND_CONTINUATION_INSIDE_RANGE"
S4_BREAKOUT_RETEST = "EXPECTED_RANGE_BREAKOUT_AND_RETEST"
S5_GAPUP_ROOM = "GAP_UP_WITH_REMAINING_ROOM"
S6_GAPDOWN_RECOVERY = "GAP_DOWN_RECOVERY"
S7_NO_CHASE = "NO_CHASE_RANGE_CONSUMED"

# --- final advisory outputs (Phase 8) ---
READY_LOWER_RANGE_BOUNCE = "READY_LOWER_RANGE_BOUNCE"
WAIT_LOWER_RANGE_BOUNCE = "WAIT_LOWER_RANGE_BOUNCE"
READY_DIP_RECLAIM = "READY_DIP_RECLAIM"
READY_CONTINUATION = "READY_CONTINUATION"
READY_BREAKOUT_RETEST = "READY_BREAKOUT_RETEST"
READY_GAP_RECOVERY = "READY_GAP_RECOVERY"
WATCH_HIGH_VOLUME_VOLATILITY = "WATCH_HIGH_VOLUME_VOLATILITY"
RANGE_CONSUMED = "RANGE_CONSUMED"
SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
LIQUIDITY_TOO_LOW = "LIQUIDITY_TOO_LOW"
DATA_STALE = "DATA_STALE"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
NO_TRADE = "NO_TRADE"


@dataclass
class LiveQuote:
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    spread_percent: float | None = None
    quote_age_seconds: float | None = None
    volume: float | None = None
    session_open: float | None = None
    session_high: float | None = None
    session_low: float | None = None
    available: bool = False
    freshness: str | None = None
    received_at: str | None = None            # receipt timestamp (collector)
    exchange_timestamp: str | None = None      # market/exchange timestamp

    @property
    def two_sided(self) -> bool:
        return self.bid is not None and self.ask is not None and self.bid > 0 and self.ask >= self.bid


@dataclass
class ScenarioResult:
    name: str
    status: str
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    entry_trigger: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    invalidation: float | None = None
    range_position_percent: float | None = None
    remaining_upside_percent: float | None = None
    room_for_2pct: bool | None = None
    net_rr: float | None = None
    net_profit_percent: float | None = None
    net_loss_percent: float | None = None
    historical_evidence: str = ""
    reason: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _cost_fraction(cfg, spread_percent):
    spread_frac = (spread_percent or 0.0) / 100.0
    return 2 * cfg.commission_estimate + spread_frac + 2 * cfg.slippage_estimate


def _fixed_targets(cfg, entry, spread_percent):
    """Fixed +2% / -2% plan with net RR after costs."""
    if not entry or entry <= 0:
        return {}
    tp = entry * (1 + cfg.fixed_take_profit_percent / 100.0)
    sl = entry * (1 - cfg.fixed_stop_loss_percent / 100.0)
    cost = _cost_fraction(cfg, spread_percent)
    net_profit = cfg.fixed_take_profit_percent / 100.0 - cost
    net_loss = cfg.fixed_stop_loss_percent / 100.0 + cost
    net_rr = (net_profit / net_loss) if net_loss > 0 else None
    return {
        "take_profit": round(tp, 4), "stop_loss": round(sl, 4),
        "net_profit_percent": round(net_profit * 100.0, 4),
        "net_loss_percent": round(net_loss * 100.0, 4),
        "net_rr": round(net_rr, 3) if net_rr is not None else None,
    }


def range_position_percent(last, low, high):
    if last is None or low is None or high is None or high <= low:
        return None
    return round((last - low) / (high - low) * 100.0, 2)


@dataclass
class ScenarioEvaluation:
    symbol: str
    scenarios: list
    advisory: str
    range_position_base: float | None
    range_position_conservative: float | None
    range_position_highvol: float | None
    remaining_upside_base_percent: float | None
    remaining_upside_highvol_percent: float | None
    range_consumed_percent: float | None
    data_warning: str = ""


def evaluate_scenarios(analysis, live: LiveQuote, cfg) -> ScenarioEvaluation:
    """Evaluate all long-entry scenarios for one analyzed symbol."""

    expected = analysis.expected
    vol = analysis.volatility
    prev_close = analysis.prev_close
    base = expected.base
    cons = expected.conservative
    hv = expected.high_volatility
    live = live or LiveQuote()

    data_warning = ""
    prov_status = analysis.provenance.data_status
    if prov_status == "DATA_STALE":
        data_warning = analysis.provenance.fallback_reason or "DATA_STALE"

    # No expected range -> insufficient data.
    if base is None or prev_close is None:
        return ScenarioEvaluation(
            analysis.symbol, [ScenarioResult(S7_NO_CHASE, DATA_INSUFFICIENT,
                              reason="no expected range (insufficient completed history)")],
            DATA_INSUFFICIENT, None, None, None, None, None, None, data_warning)

    last = live.last
    spread = live.spread_percent
    hv_high = hv.expected_high if hv else base.expected_high
    hv_low = hv.expected_low if hv else base.expected_low

    pos_base = range_position_percent(last, base.expected_low, base.expected_high)
    pos_cons = range_position_percent(last, cons.expected_low, cons.expected_high) if cons else None
    pos_hv = range_position_percent(last, hv_low, hv_high)

    remaining_base = round((base.expected_high - last) / last * 100.0, 4) if last else None
    remaining_hv = round((hv_high - last) / last * 100.0, 4) if last else None
    consumed = None
    if last and base.expected_high > base.expected_low:
        consumed = round((last - base.expected_low) / (base.expected_high - base.expected_low) * 100.0, 2)

    evidence = _evidence(vol)
    scenarios = []

    # --- hard gates that short-circuit into a single explanatory scenario ---
    gate = _live_gate(live, cfg, analysis)
    if gate is not None:
        status, reason = gate
        scenarios.append(ScenarioResult(S7_NO_CHASE, status, reason=reason,
                                        range_position_percent=pos_base,
                                        remaining_upside_percent=remaining_base,
                                        historical_evidence=evidence))
        return ScenarioEvaluation(analysis.symbol, scenarios, status, pos_base, pos_cons,
                                  pos_hv, remaining_base, remaining_hv, consumed, data_warning)

    # No-chase: even the optimistic high-vol ceiling leaves < fixed target.
    no_room = (remaining_hv is not None and remaining_hv < cfg.no_chase_remaining_upside_percent)
    if no_room and live.available:
        scenarios.append(ScenarioResult(
            S7_NO_CHASE, RANGE_CONSUMED,
            range_position_percent=pos_base, remaining_upside_percent=remaining_hv,
            room_for_2pct=False, historical_evidence=evidence,
            reason=(f"TARGET_ROOM_INSUFFICIENT / DO_NOT_CHASE: expected upside remaining "
                    f"{remaining_hv:.2f}% < required {cfg.fixed_take_profit_percent:.1f}%")))
        # still show the other plans, but advisory is RANGE_CONSUMED
    scenarios.extend([
        _lower_bounce(analysis, live, cfg, base, cons, pos_base, remaining_base, evidence),
        _dip_reclaim(analysis, live, cfg, base, cons, pos_base, remaining_base, evidence),
        _continuation(analysis, live, cfg, base, pos_base, remaining_base, evidence),
        _breakout_retest(analysis, live, cfg, base, hv, pos_base, remaining_hv, evidence),
        _gap_up(analysis, live, cfg, base, hv, prev_close, remaining_hv, evidence),
        _gap_down(analysis, live, cfg, base, cons, prev_close, pos_base, remaining_base, evidence),
    ])

    advisory = _advisory(scenarios, live, analysis, no_room)
    return ScenarioEvaluation(analysis.symbol, scenarios, advisory, pos_base, pos_cons,
                              pos_hv, remaining_base, remaining_hv, consumed, data_warning)


# --- gates -------------------------------------------------------------------

def _live_gate(live: LiveQuote, cfg, analysis):
    if analysis.provenance.data_status == "DATA_INSUFFICIENT":
        return (DATA_INSUFFICIENT, "insufficient completed daily history for a reliable range")
    if not live.available:
        # Pre-session / no live quote: not a rejection, a watch (handled in advisory).
        return None
    if live.quote_age_seconds is not None and live.quote_age_seconds > cfg.maximum_quote_age_seconds:
        return (DATA_STALE, f"STALE_QUOTE: {live.quote_age_seconds:.0f}s > {cfg.maximum_quote_age_seconds}s")
    if not live.two_sided:
        return (DATA_STALE, "MISSING_BID_OR_ASK (no two-sided executable quote)")
    if live.spread_percent is not None and live.spread_percent > cfg.maximum_live_spread_percent:
        return (SPREAD_TOO_WIDE, f"SPREAD_TOO_WIDE: {live.spread_percent:.2f}% > {cfg.maximum_live_spread_percent}%")
    return None


def _evidence(vol) -> str:
    if vol is None:
        return ""
    parts = []
    if vol.target_2pct_frequency is not None:
        parts.append(f"2%rangeFreq={vol.target_2pct_frequency:.0%}")
    if vol.upside_2pct_frequency is not None:
        parts.append(f"2%upFreq={vol.upside_2pct_frequency:.0%}")
    if vol.adr_percent_20 is not None:
        parts.append(f"ADR%={vol.adr_percent_20:.2f}")
    if vol.classification:
        parts.append(vol.classification)
    return " ".join(parts)


# --- scenarios ---------------------------------------------------------------

def _lower_bounce(analysis, live, cfg, base, cons, pos_base, remaining_base, evidence):
    low = (cons.expected_low if cons else base.expected_low)
    entry_low = low
    entry_high = base.expected_low * (1 + cfg.range_lower_zone_percent / 100.0 * 0.0 + 0.0)
    entry = live.last if live.available else low
    plan = _fixed_targets(cfg, entry, live.spread_percent)
    invalidation = round(low * (1 - 0.005), 4)
    status = "LOWER_RANGE_NOT_REACHED"
    if not live.available:
        status = "BOUNCE_WAIT"
    elif pos_base is None:
        status = "INVALID"
    elif pos_base < 0:
        status = "FALLING_WITHOUT_CONFIRMATION"
    elif pos_base <= cfg.range_lower_zone_percent:
        # In the lower zone. Genuine recovery needs sequence -> WAIT unless the
        # last has reclaimed the live session low by a buffer.
        if live.session_low and live.last and live.last > live.session_low * 1.003:
            status = "BOUNCE_READY"
        else:
            status = "BOUNCE_WAIT"
    else:
        status = "LOWER_RANGE_NOT_REACHED"
    return ScenarioResult(
        S1_LOWER_BOUNCE, status, entry_zone_low=round(low, 4),
        entry_zone_high=round(base.expected_low, 4), entry_trigger=round(entry, 4) if entry else None,
        take_profit=plan.get("take_profit"), stop_loss=plan.get("stop_loss"),
        invalidation=invalidation, range_position_percent=pos_base,
        remaining_upside_percent=remaining_base, room_for_2pct=(remaining_base or 0) >= 2.0,
        net_rr=plan.get("net_rr"), net_profit_percent=plan.get("net_profit_percent"),
        net_loss_percent=plan.get("net_loss_percent"), historical_evidence=evidence,
        reason="price at/near expected lower zone; long on confirmed recovery")


def _dip_reclaim(analysis, live, cfg, base, cons, pos_base, remaining_base, evidence):
    level = (cons.expected_low if cons else base.expected_low)
    entry = level  # reclaim of the level
    plan = _fixed_targets(cfg, entry, live.spread_percent)
    status = "BOUNCE_WAIT"
    if not live.available:
        status = "BOUNCE_WAIT"
    elif live.last is not None and live.last >= level and live.two_sided:
        # Trading at/above the level with two-sided support (reclaim candidate).
        status = "READY" if (live.session_low and live.last > level) else "BOUNCE_WAIT"
    elif live.last is not None and live.last < level:
        status = "BOUNCE_WAIT"
    return ScenarioResult(
        S2_DIP_RECLAIM, status, entry_trigger=round(level, 4),
        take_profit=plan.get("take_profit"), stop_loss=plan.get("stop_loss"),
        invalidation=round(level * (1 - 0.005), 4), range_position_percent=pos_base,
        remaining_upside_percent=remaining_base, room_for_2pct=(remaining_base or 0) >= 2.0,
        net_rr=plan.get("net_rr"), net_profit_percent=plan.get("net_profit_percent"),
        net_loss_percent=plan.get("net_loss_percent"), historical_evidence=evidence,
        reason=f"long on reclaim of {round(level, 4)} with live two-sided support")


def _continuation(analysis, live, cfg, base, pos_base, remaining_base, evidence):
    entry = live.last if live.available else None
    plan = _fixed_targets(cfg, entry, live.spread_percent) if entry else {}
    status = "BOUNCE_WAIT"
    room = (remaining_base or 0) >= cfg.fixed_take_profit_percent
    if not live.available:
        status = "BOUNCE_WAIT"
    elif pos_base is None:
        status = "INVALID"
    elif pos_base > cfg.range_upper_zone_percent:
        status = "RANGE_CONSUMED"
    elif not room:
        status = "RANGE_CONSUMED"
    elif cfg.range_lower_zone_percent < pos_base <= cfg.range_upper_zone_percent:
        status = "READY" if live.two_sided else "BOUNCE_WAIT"
    else:
        status = "BOUNCE_WAIT"
    return ScenarioResult(
        S3_CONTINUATION, status, entry_trigger=round(entry, 4) if entry else None,
        take_profit=plan.get("take_profit"), stop_loss=plan.get("stop_loss"),
        invalidation=round(base.expected_low, 4), range_position_percent=pos_base,
        remaining_upside_percent=remaining_base, room_for_2pct=room,
        net_rr=plan.get("net_rr"), net_profit_percent=plan.get("net_profit_percent"),
        net_loss_percent=plan.get("net_loss_percent"), historical_evidence=evidence,
        reason="upward drift inside expected range with >=2% remaining to expected High")


def _breakout_retest(analysis, live, cfg, base, hv, pos_base, remaining_hv, evidence):
    breakout_level = base.expected_high
    entry = breakout_level  # retest of the breakout level
    plan = _fixed_targets(cfg, entry, live.spread_percent)
    failed_level = round(breakout_level * (1 - 0.005), 4)
    status = "LOWER_RANGE_NOT_REACHED"
    if not live.available:
        status = "BOUNCE_WAIT"
    elif live.last is None:
        status = "INVALID"
    elif live.last <= breakout_level:
        status = "LOWER_RANGE_NOT_REACHED"   # not broken out yet
    else:
        # Broken above expected High. Never treat the first spike as an entry.
        status = "BOUNCE_WAIT"   # wait for confirmation/retest
    return ScenarioResult(
        S4_BREAKOUT_RETEST, status, entry_trigger=round(breakout_level, 4),
        take_profit=plan.get("take_profit"), stop_loss=plan.get("stop_loss"),
        invalidation=failed_level, range_position_percent=pos_base,
        remaining_upside_percent=remaining_hv, room_for_2pct=(remaining_hv or 0) >= 2.0,
        net_rr=plan.get("net_rr"), net_profit_percent=plan.get("net_profit_percent"),
        net_loss_percent=plan.get("net_loss_percent"), historical_evidence=evidence,
        reason="long only on confirmed retest of the expected-High breakout; not the first spike")


def _gap_up(analysis, live, cfg, base, hv, prev_close, remaining_hv, evidence):
    entry = live.last if live.available else None
    plan = _fixed_targets(cfg, entry, live.spread_percent) if entry else {}
    is_gap_up = bool(live.available and live.session_open and live.session_open > prev_close)
    room = (remaining_hv or 0) >= cfg.fixed_take_profit_percent
    if not live.available or not live.session_open:
        status = "BOUNCE_WAIT"
    elif not is_gap_up:
        status = "LOWER_RANGE_NOT_REACHED"   # no gap up
    elif not room:
        status = "RANGE_CONSUMED"
    else:
        status = "READY" if live.two_sided else "BOUNCE_WAIT"
    return ScenarioResult(
        S5_GAPUP_ROOM, status, entry_trigger=round(entry, 4) if entry else None,
        take_profit=plan.get("take_profit"), stop_loss=plan.get("stop_loss"),
        invalidation=round(live.session_open, 4) if live.session_open else None,
        remaining_upside_percent=remaining_hv, room_for_2pct=room,
        net_rr=plan.get("net_rr"), net_profit_percent=plan.get("net_profit_percent"),
        net_loss_percent=plan.get("net_loss_percent"), historical_evidence=evidence,
        reason="gap above prev close with statistically supported room left for +2%")


def _gap_down(analysis, live, cfg, base, cons, prev_close, pos_base, remaining_base, evidence):
    level = prev_close  # recover the previous close
    entry = level
    plan = _fixed_targets(cfg, entry, live.spread_percent)
    is_gap_down = bool(live.available and (
        (live.session_open and live.session_open < prev_close)
        or (live.last is not None and base.expected_low is not None and live.last < base.expected_low)))
    if not live.available:
        status = "BOUNCE_WAIT"
    elif not is_gap_down:
        status = "LOWER_RANGE_NOT_REACHED"
    elif live.last is not None and live.last >= level:
        status = "READY" if live.two_sided else "BOUNCE_WAIT"
    else:
        status = "BOUNCE_WAIT"
    return ScenarioResult(
        S6_GAPDOWN_RECOVERY, status, entry_trigger=round(level, 4),
        take_profit=plan.get("take_profit"), stop_loss=plan.get("stop_loss"),
        invalidation=round((cons.expected_low if cons else base.expected_low) * (1 - 0.005), 4),
        range_position_percent=pos_base, remaining_upside_percent=remaining_base,
        room_for_2pct=(remaining_base or 0) >= 2.0, net_rr=plan.get("net_rr"),
        net_profit_percent=plan.get("net_profit_percent"),
        net_loss_percent=plan.get("net_loss_percent"), historical_evidence=evidence,
        reason=f"recover reference {round(level, 4)} after trading below the expected range")


# --- advisory ---------------------------------------------------------------

_READY_MAP = {
    S1_LOWER_BOUNCE: READY_LOWER_RANGE_BOUNCE,
    S2_DIP_RECLAIM: READY_DIP_RECLAIM,
    S3_CONTINUATION: READY_CONTINUATION,
    S4_BREAKOUT_RETEST: READY_BREAKOUT_RETEST,
    S5_GAPUP_ROOM: READY_GAP_RECOVERY,
    S6_GAPDOWN_RECOVERY: READY_GAP_RECOVERY,
}


def _advisory(scenarios, live, analysis, no_room):
    if no_room:
        return RANGE_CONSUMED
    if not live.available:
        # Pre-session watch for tradable candidates; gates handled earlier.
        return WATCH_HIGH_VOLUME_VOLATILITY
    # A scenario is READY if its status is BOUNCE_READY or READY.
    for scn in scenarios:
        if scn.status in ("BOUNCE_READY", "READY") and (scn.room_for_2pct is not False):
            return _READY_MAP.get(scn.name, WATCH_HIGH_VOLUME_VOLATILITY)
    # Otherwise, is anything waiting?
    if any(scn.status in ("BOUNCE_WAIT",) for scn in scenarios):
        # lower-bounce waiting gets the dedicated WAIT advisory
        for scn in scenarios:
            if scn.name == S1_LOWER_BOUNCE and scn.status == "BOUNCE_WAIT":
                return WAIT_LOWER_RANGE_BOUNCE
        return WATCH_HIGH_VOLUME_VOLATILITY
    return NO_TRADE
