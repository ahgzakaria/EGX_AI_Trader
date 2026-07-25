"""Scalping V2 — event-driven data-quality model (research, isolated).

Rationale (proven on the 2026-07-21 EGX session): minute-occupancy is the wrong
quality measure for an event-driven quote feed. Liquid EGX symbols (e.g. COMI:
3,477 genuine two-sided events spanning 267/270 minutes, median gap 0-3 s) score
only ~24% minute-coverage yet are clearly well-observed. This model measures
genuine event evidence instead — WITHOUT fabricating or forward-filling minutes.

It never lowers or touches the legacy 60% minute gate (kept as a diagnostic),
never changes Entry/Stop/Target/RR, and never enables trading. Pure functions
over already-received events; zero/None prices are never treated as genuine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class EventStatus(str, Enum):
    EVENT_DATA_VALID = "EVENT_DATA_VALID"
    EVENT_DATA_LIMITED = "EVENT_DATA_LIMITED"
    STALE_QUOTE = "STALE_QUOTE"
    SUBSCRIPTION_LOST = "SUBSCRIPTION_LOST"
    RANGE_UNRELIABLE = "RANGE_UNRELIABLE"
    LIQUIDITY_TOO_LOW = "LIQUIDITY_TOO_LOW"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


class RangeStatus(str, Enum):
    RANGE_CONFIRMED = "RANGE_CONFIRMED"
    RANGE_PARTIAL = "RANGE_PARTIAL"
    RANGE_UNRELIABLE = "RANGE_UNRELIABLE"


@dataclass(frozen=True)
class EventQualityConfig:
    session_minutes: int = 270
    min_events: int = 60
    min_two_sided_ratio: float = 0.80
    max_quote_age_seconds: float = 90.0
    min_span_minutes: float = 180.0
    # Range confidence is bounded by the largest silent gap during the session
    # (typically a market-wide connection outage, not symbol inactivity): a big
    # blackout could hide a real move, so the observed High/Low is only partial.
    max_gap_confirmed_seconds: float = 300.0   # <=5 min silent -> range trustworthy
    max_gap_partial_seconds: float = 900.0     # <=15 min -> partial; beyond -> unreliable
    max_spread_percent: float = 0.6
    min_turnover_egp: float = 500_000.0
    min_distinct_prices: int = 5
    # Opening-auction grace: a symbol's first ~15 min (before continuous quoting
    # stabilises) is a SESSION_START_EDGE, not a connection outage, and must NOT
    # invalidate the session range (proven on 2026-07-21: the dominant per-symbol
    # gap was the 10:01->10:15 Cairo opening edge while the connection was healthy).
    opening_grace_seconds: float = 900.0


@dataclass(frozen=True)
class EventQualityResult:
    symbol: str
    # Three distinct coverage types (Phase 1)
    connection_coverage: float | None       # market-wide, supplied by caller
    observed_minute_coverage: float         # LEGACY diagnostic, preserved
    active_event_coverage: float            # events / active-minutes evidence
    # Event evidence
    event_count: int
    positive_event_count: int
    distinct_prices: int
    span_minutes: float
    median_gap_seconds: float
    p90_gap_seconds: float
    max_gap_seconds: float           # RAW max event gap (transparency)
    fatal_gap_seconds: float         # max GENUINE in-session connection-outage gap
    max_gap_classification: str      # why the raw max gap is/ isn't fatal
    two_sided_ratio: float
    latest_quote_age_seconds: float | None
    spread_percent: float | None
    cumulative_volume: float | None
    turnover_egp: float | None
    event_high: float | None
    event_low: float | None
    event_last: float | None
    # Scores (0-100)
    event_activity_score: float
    liquidity_score: float
    executability_score: float
    range_confidence_score: float
    event_data_quality_score: float
    # Verdicts
    status: EventStatus
    range_status: RangeStatus
    reasons: tuple = field(default_factory=tuple)


def compute_event_quality(
    symbol,
    events: pd.DataFrame,
    *,
    connection_coverage=None,
    now,
    config: EventQualityConfig | None = None,
    session_start=None,
    session_close=None,
    connection_outage_intervals=None,
):
    """Return an :class:`EventQualityResult` from a symbol's genuine session events.

    ``events`` columns: market_timestamp (tz-aware), last_price, bid, ask, volume.
    ``now`` is a tz-aware timestamp for freshness. Zero/None last_price rows are
    ignored for price/range purposes (never treated as genuine prices).
    """

    cfg = config or EventQualityConfig()
    reasons = []

    if events is None or events.empty:
        return _insufficient(symbol, cfg, connection_coverage,
                             reason="no events received for symbol")

    ev = events.copy()
    ev["ts"] = pd.to_datetime(ev["market_timestamp"], utc=True, errors="coerce")
    ev = ev.dropna(subset=["ts"]).sort_values("ts")
    event_count = len(ev)

    # Genuine positive prices only (zeros/nulls are transport artifacts).
    px = pd.to_numeric(ev["last_price"], errors="coerce")
    positive = px[px > 0]
    positive_count = int(len(positive))
    distinct_prices = int(positive.nunique())

    # Coverage types.
    minutes = ev["ts"].dt.strftime("%Y-%m-%d %H:%M")
    active_minutes = int(minutes.nunique())
    observed_minute_coverage = round(active_minutes / cfg.session_minutes, 4)
    # active_event_coverage: events per active minute, normalized (dense bursts
    # in active minutes are good evidence even if not every minute is filled).
    active_event_coverage = round(
        min(1.0, (event_count / max(1, active_minutes)) / 10.0), 4
    )  # ~10 events/active-min saturates the score

    # Gaps between consecutive events (seconds).
    gaps = ev["ts"].diff().dropna().dt.total_seconds()
    median_gap = float(gaps.median()) if len(gaps) else float("inf")
    p90_gap = float(gaps.quantile(0.9)) if len(gaps) else float("inf")
    max_gap = float(gaps.max()) if len(gaps) else float("inf")
    span_minutes = float((ev["ts"].iloc[-1] - ev["ts"].iloc[0]).total_seconds() / 60) if event_count > 1 else 0.0

    # FATAL gap = the max GENUINE in-session connection-outage gap. Excludes the
    # opening-auction session-start edge, any after-close interval, and gaps that
    # happened while the market-wide connection was healthy (symbol-inactivity).
    # This is the correct range-confidence limiter; the raw max_gap alone wrongly
    # counted the opening edge as fatal (2026-07-21 audit).
    fatal_gap, max_gap_class = _fatal_gap(
        ev["ts"].tolist(), gaps.tolist(), max_gap, cfg,
        session_start, session_close, connection_outage_intervals,
    )

    two_sided = ((pd.to_numeric(ev["bid"], errors="coerce") > 0) &
                 (pd.to_numeric(ev["ask"], errors="coerce") > 0))
    two_sided_ratio = round(float(two_sided.mean()), 4) if event_count else 0.0

    latest_ts = ev["ts"].iloc[-1]
    latest_age = float((pd.Timestamp(now) - latest_ts).total_seconds())

    # Spread from the last genuine two-sided quote.
    spread_percent = _last_spread(ev)
    cum_volume = float(pd.to_numeric(ev["volume"], errors="coerce").max()) if event_count else None
    event_last = float(positive.iloc[-1]) if positive_count else None
    turnover = (event_last * cum_volume) if (event_last and cum_volume) else None
    event_high = float(positive.max()) if positive_count else None
    event_low = float(positive.min()) if positive_count else None

    # -- component scores (0-100) -----------------------------------------
    event_activity_score = min(100.0, event_count / cfg.min_events * 50.0) if event_count else 0.0
    # penalize sparse median gaps only above a few seconds
    if median_gap <= 5:
        gap_score = 100.0
    elif median_gap <= 30:
        gap_score = 70.0
    elif median_gap <= 120:
        gap_score = 40.0
    else:
        gap_score = 10.0
    event_activity_score = round((event_activity_score * 0.6 + gap_score * 0.4), 1)

    liquidity_score = _liquidity_score(turnover, cfg)
    executability_score = _executability_score(spread_percent, two_sided_ratio, latest_age, cfg)
    range_confidence_score = _range_confidence_score(
        positive_count, distinct_prices, span_minutes, fatal_gap, cfg
    )

    # -- hard failures (no component may hide these) ----------------------
    hard = _hard_failures(
        positive_count, distinct_prices, spread_percent, two_sided_ratio,
        latest_age, turnover, span_minutes, max_gap, cfg, reasons,
    )

    quality = round(
        0.30 * event_activity_score + 0.25 * executability_score
        + 0.25 * liquidity_score + 0.20 * range_confidence_score, 1
    )

    status = _status(hard, event_count, cfg, quality)
    range_status = _range_status(fatal_gap, positive_count, distinct_prices, span_minutes, cfg)

    return EventQualityResult(
        symbol=symbol,
        connection_coverage=connection_coverage,
        observed_minute_coverage=observed_minute_coverage,
        active_event_coverage=active_event_coverage,
        event_count=event_count, positive_event_count=positive_count,
        distinct_prices=distinct_prices, span_minutes=round(span_minutes, 1),
        median_gap_seconds=round(median_gap, 1), p90_gap_seconds=round(p90_gap, 1),
        max_gap_seconds=round(max_gap, 1),
        fatal_gap_seconds=round(fatal_gap, 1), max_gap_classification=max_gap_class,
        two_sided_ratio=two_sided_ratio,
        latest_quote_age_seconds=round(latest_age, 1), spread_percent=spread_percent,
        cumulative_volume=cum_volume, turnover_egp=round(turnover, 2) if turnover else None,
        event_high=event_high, event_low=event_low, event_last=event_last,
        event_activity_score=event_activity_score, liquidity_score=liquidity_score,
        executability_score=executability_score,
        range_confidence_score=range_confidence_score,
        event_data_quality_score=quality,
        status=status, range_status=range_status, reasons=tuple(reasons),
    )


def _fatal_gap(timestamps, gaps, raw_max_gap, cfg, session_start, session_close,
               outage_intervals):
    """Return (fatal_gap_seconds, classification_of_raw_max_gap).

    A symbol gap [a, b] counts toward range confidence only when it is a GENUINE
    in-session connection outage:
      * b <= session_close                     (not after-close),
      * a >= session_start + opening_grace     (not the opening-auction edge),
      * and (if market-wide outage intervals are supplied) [a, b] overlaps one
        of them — i.e. the whole feed was down, not just this symbol quiet.
    Gaps that fail these are SESSION_START_EDGE / AFTER_CLOSE / SYMBOL_INACTIVE
    and never invalidate the range.
    """

    if not gaps:
        return float("inf"), "no_gaps"
    grace_end = None
    if session_start is not None:
        grace_end = pd.Timestamp(session_start) + pd.Timedelta(seconds=cfg.opening_grace_seconds)
    close_ts = pd.Timestamp(session_close) if session_close is not None else None
    symbol_first, symbol_last = timestamps[0], timestamps[-1]

    # Classify the RAW max symbol gap (transparency only).
    raw_idx = max(range(len(gaps)), key=lambda i: gaps[i])
    a_raw, b_raw = timestamps[raw_idx], timestamps[raw_idx + 1]
    if close_ts is not None and b_raw > close_ts:
        raw_class = "AFTER_CLOSE_ARTIFACT" if a_raw > close_ts else "SESSION_CLOSE_EDGE"
    elif grace_end is not None and a_raw < grace_end:
        raw_class = "SESSION_START_EDGE"
    elif outage_intervals is not None and not any(
            a_raw < oe and b_raw > os for os, oe in outage_intervals):
        raw_class = "SYMBOL_INACTIVE_WHILE_CONNECTION_HEALTHY"
    else:
        raw_class = "IN_SESSION_CONNECTION_OUTAGE"

    # FATAL magnitude = the longest MARKET-WIDE connection-outage duration that
    # (a) is a genuine outage (whole feed silent), (b) lies in continuous
    # trading after the opening grace and before close, and (c) overlaps this
    # symbol's active window. This uses the OUTAGE duration, never the symbol's
    # own idle gap (a liquid symbol can be quiet while the connection is healthy).
    if outage_intervals is not None:
        fatal = 0.0
        for os, oe in outage_intervals:
            if grace_end is not None and os < grace_end:
                continue
            if close_ts is not None and oe > close_ts:
                continue
            if oe < symbol_first or os > symbol_last:
                continue
            fatal = max(fatal, (oe - os).total_seconds())
        return fatal, raw_class

    # No market-wide context (standalone use): fall back to symbol gaps,
    # excluding the opening edge and any after-close portion.
    fatal = 0.0
    for i, g in enumerate(gaps):
        a, b = timestamps[i], timestamps[i + 1]
        if close_ts is not None and b > close_ts:
            continue
        if grace_end is not None and a < grace_end:
            continue
        fatal = max(fatal, g)
    return fatal, raw_class


def _last_spread(ev):
    for _, row in ev.iloc[::-1].iterrows():
        bid, ask = pd.to_numeric(row["bid"], errors="coerce"), pd.to_numeric(row["ask"], errors="coerce")
        if pd.notna(bid) and pd.notna(ask) and bid > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            return round((ask - bid) / mid * 100.0, 4)
    return None


def _liquidity_score(turnover, cfg):
    if not turnover or turnover <= 0:
        return 0.0
    return round(min(100.0, turnover / cfg.min_turnover_egp * 50.0), 1)


def _executability_score(spread, two_sided_ratio, latest_age, cfg):
    if spread is None or spread > cfg.max_spread_percent:
        return 0.0
    spread_component = max(0.0, 100.0 * (1 - spread / cfg.max_spread_percent))
    fresh = 100.0 if latest_age is not None and latest_age <= cfg.max_quote_age_seconds else 0.0
    return round(0.5 * spread_component + 0.3 * (two_sided_ratio * 100) + 0.2 * fresh, 1)


def _range_confidence_score(positive_count, distinct_prices, span_minutes, max_gap, cfg):
    if positive_count < cfg.min_events or distinct_prices < cfg.min_distinct_prices:
        return 0.0
    span_component = min(100.0, span_minutes / cfg.session_minutes * 100.0)
    if max_gap <= cfg.max_gap_confirmed_seconds:
        gap_component = 100.0
    elif max_gap <= cfg.max_gap_partial_seconds:
        # linear decay across the partial band
        span = cfg.max_gap_partial_seconds - cfg.max_gap_confirmed_seconds
        gap_component = 60.0 * (1 - (max_gap - cfg.max_gap_confirmed_seconds) / span)
    else:
        gap_component = 0.0
    return round(0.5 * span_component + 0.5 * gap_component, 1)


def _hard_failures(positive_count, distinct_prices, spread, two_sided_ratio,
                   latest_age, turnover, span_minutes, max_gap, cfg, reasons):
    failed = False
    if positive_count == 0:
        reasons.append("no valid Last price"); failed = True
    if positive_count < cfg.min_events:
        reasons.append(f"too few genuine events ({positive_count}<{cfg.min_events})"); failed = True
    if two_sided_ratio < cfg.min_two_sided_ratio:
        reasons.append(f"missing Bid/Ask on {(1-two_sided_ratio)*100:.0f}% of events"); failed = True
    if spread is None:
        reasons.append("no executable two-sided quote"); failed = True
    elif spread > cfg.max_spread_percent:
        reasons.append(f"spread {spread:.2f}% > {cfg.max_spread_percent}%"); failed = True
    if latest_age is not None and latest_age > cfg.max_quote_age_seconds * 1_000_000:
        # NB: freshness is scored but only a *hard* fail when absurdly stale
        # (this model is run off-session for research; live gating uses age).
        reasons.append("quote absurdly stale"); failed = True
    if turnover is not None and turnover < cfg.min_turnover_egp:
        reasons.append(f"turnover {turnover:,.0f} < {cfg.min_turnover_egp:,.0f}"); failed = True
    if distinct_prices < cfg.min_distinct_prices:
        reasons.append(f"only {distinct_prices} distinct prices"); failed = True
    return failed


def _status(hard, event_count, cfg, quality):
    if event_count < cfg.min_events:
        return EventStatus.DATA_INSUFFICIENT
    if hard:
        return EventStatus.EVENT_DATA_LIMITED
    return EventStatus.EVENT_DATA_VALID


def _range_status(max_gap, positive_count, distinct_prices, span_minutes, cfg):
    if positive_count < cfg.min_events or distinct_prices < cfg.min_distinct_prices:
        return RangeStatus.RANGE_UNRELIABLE
    if span_minutes < cfg.min_span_minutes:
        return RangeStatus.RANGE_UNRELIABLE
    if max_gap <= cfg.max_gap_confirmed_seconds:
        return RangeStatus.RANGE_CONFIRMED
    if max_gap <= cfg.max_gap_partial_seconds:
        return RangeStatus.RANGE_PARTIAL
    return RangeStatus.RANGE_UNRELIABLE


def _insufficient(symbol, cfg, connection_coverage, *, reason):
    return EventQualityResult(
        symbol=symbol, connection_coverage=connection_coverage,
        observed_minute_coverage=0.0, active_event_coverage=0.0,
        event_count=0, positive_event_count=0, distinct_prices=0, span_minutes=0.0,
        median_gap_seconds=float("inf"), p90_gap_seconds=float("inf"),
        max_gap_seconds=float("inf"), fatal_gap_seconds=float("inf"),
        max_gap_classification="no_events", two_sided_ratio=0.0,
        latest_quote_age_seconds=None, spread_percent=None, cumulative_volume=None,
        turnover_egp=None, event_high=None, event_low=None, event_last=None,
        event_activity_score=0.0, liquidity_score=0.0, executability_score=0.0,
        range_confidence_score=0.0, event_data_quality_score=0.0,
        status=EventStatus.DATA_INSUFFICIENT, range_status=RangeStatus.RANGE_UNRELIABLE,
        reasons=(reason,),
    )
