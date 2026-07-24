"""Tests for the event-driven data-quality model (synthetic events).

Proves: zeros never treated as prices (no fabrication), range confidence is
bounded by the max blackout gap, stale/illiquid/wide-spread hard failures, and
that a well-observed liquid symbol with LOW minute coverage is still admitted as
EVENT_DATA_VALID (the core premise the legacy minute gate gets wrong).
"""

from datetime import timezone

import pandas as pd

from scalping.event_quality import (
    EventQualityConfig,
    EventStatus,
    RangeStatus,
    compute_event_quality,
)

NOW = pd.Timestamp("2026-07-21 11:35", tz="UTC")


def _events(specs):
    """specs: list of (offset_seconds_from_open, last, bid, ask, volume)."""
    open_ts = pd.Timestamp("2026-07-21 07:00", tz="UTC")
    rows = []
    for off, last, bid, ask, vol in specs:
        rows.append({
            "market_timestamp": (open_ts + pd.Timedelta(seconds=off)).isoformat(),
            "last_price": last, "bid": bid, "ask": ask, "volume": vol,
        })
    return pd.DataFrame(rows)


def _bursty_session(burst_gap_seconds=240, n_bursts=66, per_burst=12,
                    gap_black=None, price0=100.0, spread_frac=0.0004):
    """Realistic liquid EGX symbol: dense bursts of two-sided events in a subset
    of minutes, spread across the full session (mimics COMI: many events, low
    minute-occupancy, tight intra-burst gaps, bounded inter-burst gaps)."""
    specs = []
    vol = 1000
    for b in range(n_bursts):
        base = b * burst_gap_seconds
        if gap_black and gap_black[0] <= base < gap_black[1]:
            continue
        for k in range(per_burst):
            off = base + k * 1.5
            px = price0 + ((b + k) % 20) * 0.05
            half = px * spread_frac
            vol += 500
            specs.append((off, px, px - half, px + half, vol))
    return _events(specs)


def test_well_observed_liquid_low_minute_coverage_is_admitted():
    # Bursty dense events, tight intra-burst gaps, full span, two-sided ->
    # EVENT_DATA_VALID even though minute coverage is far below 60%.
    ev = _bursty_session(burst_gap_seconds=240, n_bursts=66, per_burst=12)
    r = compute_event_quality("COMI.CA", ev, connection_coverage=0.73, now=NOW,
                              config=EventQualityConfig(min_turnover_egp=1000))
    assert r.status == EventStatus.EVENT_DATA_VALID
    assert r.observed_minute_coverage < 0.60   # legacy would reject
    assert r.two_sided_ratio == 1.0
    assert r.range_status == RangeStatus.RANGE_CONFIRMED  # bounded ~4-min gaps


def test_zero_prices_are_not_treated_as_prices():
    ev = _events([(i * 30, 0.0, 0.0, 0.0, 0) for i in range(100)])  # all zeros
    r = compute_event_quality("X.CA", ev, now=NOW)
    assert r.positive_event_count == 0
    assert r.event_high is None and r.event_low is None
    assert r.status == EventStatus.DATA_INSUFFICIENT or "no valid Last" in " ".join(r.reasons)


def test_range_confidence_bounded_by_blackout_gap():
    # Same bursty session but with a 14-minute blackout in the middle.
    ev = _bursty_session(burst_gap_seconds=240, n_bursts=66, per_burst=12,
                         gap_black=(7000, 7000 + 14 * 60))
    r = compute_event_quality("COMI.CA", ev, now=NOW,
                              config=EventQualityConfig(min_turnover_egp=1000))
    assert r.max_gap_seconds >= 14 * 60 - 120
    # A 14-min blackout -> not fully confirmed (a move could have been missed).
    assert r.range_status in (RangeStatus.RANGE_PARTIAL, RangeStatus.RANGE_UNRELIABLE)


def test_wide_spread_fails_executability():
    # A genuinely wide-spread symbol (all quotes ~5% wide) must fail.
    ev = _bursty_session(burst_gap_seconds=240, n_bursts=40, per_burst=12,
                         spread_frac=0.05)  # ~10% spread
    r = compute_event_quality("X.CA", ev, now=NOW,
                              config=EventQualityConfig(max_spread_percent=0.6, min_turnover_egp=1000))
    assert r.executability_score == 0.0
    assert any("spread" in x for x in r.reasons)


def test_too_few_events_is_data_insufficient():
    ev = _bursty_session(burst_gap_seconds=240, n_bursts=3, per_burst=2)
    r = compute_event_quality("X.CA", ev, now=NOW, config=EventQualityConfig(min_events=60))
    assert r.status == EventStatus.DATA_INSUFFICIENT


def test_no_events_is_insufficient():
    r = compute_event_quality("X.CA", pd.DataFrame(), now=NOW)
    assert r.status == EventStatus.DATA_INSUFFICIENT
    assert r.event_count == 0


def test_short_span_is_range_unreliable():
    # Many events but all crammed into the first ~20 minutes -> range not trustworthy
    ev = _bursty_session(burst_gap_seconds=60, n_bursts=20, per_burst=15)
    r = compute_event_quality("X.CA", ev, now=NOW,
                              config=EventQualityConfig(min_span_minutes=180, min_turnover_egp=1000))
    assert r.range_status == RangeStatus.RANGE_UNRELIABLE


def test_legacy_minute_coverage_field_is_preserved():
    ev = _bursty_session()
    r = compute_event_quality("X.CA", ev, now=NOW)
    # Legacy diagnostic is still computed and exposed unchanged.
    assert 0.0 <= r.observed_minute_coverage <= 1.0


# -- gap-classification fix (2026-07-21 audit) --------------------------------

SESSION_START = pd.Timestamp("2026-07-21 07:00", tz="UTC")   # 10:00 Cairo
SESSION_CLOSE = pd.Timestamp("2026-07-21 11:30", tz="UTC")   # 14:30 Cairo


def test_opening_auction_edge_gap_is_not_fatal():
    # One opening print, then a 14-min gap, then dense continuous trading.
    # The opening gap is a SESSION_START_EDGE and must NOT invalidate the range.
    specs = [(0, 100.0, 99.98, 100.02, 1000)]  # 10:00:00 open print
    # dense continuous events from 10:15 onward, tight gaps, to ~14:20
    t = 15 * 60
    v = 1000
    while t < 4 * 3600 + 20 * 60:
        v += 500
        px = 100.0 + ((t // 60) % 20) * 0.05
        specs.append((t, px, px - 0.02, px + 0.02, v))
        t += 20
    ev = _events(specs)
    r = compute_event_quality("COMI.CA", ev, now=NOW,
                              config=EventQualityConfig(min_turnover_egp=1000),
                              session_start=SESSION_START, session_close=SESSION_CLOSE,
                              connection_outage_intervals=[])  # no market-wide outage
    assert r.max_gap_seconds >= 14 * 60 - 60      # raw max gap is the opening edge
    assert r.max_gap_classification == "SESSION_START_EDGE"
    assert r.fatal_gap_seconds < 5 * 60           # no genuine outage
    assert r.range_status == RangeStatus.RANGE_CONFIRMED


def test_genuine_in_session_outage_still_fails():
    # Dense trading with a real 6-min market-wide outage in the middle.
    black = (2 * 3600, 2 * 3600 + 6 * 60)
    ev = _bursty_session(burst_gap_seconds=60, n_bursts=240, per_burst=8, gap_black=black)
    outage = [(SESSION_START + pd.Timedelta(seconds=black[0]),
               SESSION_START + pd.Timedelta(seconds=black[1]))]
    r = compute_event_quality("X.CA", ev, now=NOW,
                              config=EventQualityConfig(min_turnover_egp=1000,
                                                        max_gap_confirmed_seconds=300),
                              session_start=SESSION_START, session_close=SESSION_CLOSE,
                              connection_outage_intervals=outage)
    assert r.fatal_gap_seconds >= 6 * 60 - 60
    assert r.max_gap_classification == "IN_SESSION_CONNECTION_OUTAGE"
    assert r.range_status != RangeStatus.RANGE_CONFIRMED


def test_symbol_inactive_while_connection_healthy_is_not_fatal():
    # A 10-min symbol gap while NO market-wide outage covers it -> symbol was
    # simply inactive; the connection was healthy -> non-fatal.
    black = (2 * 3600, 2 * 3600 + 10 * 60)
    ev = _bursty_session(burst_gap_seconds=60, n_bursts=240, per_burst=8, gap_black=black)
    r = compute_event_quality("X.CA", ev, now=NOW,
                              config=EventQualityConfig(min_turnover_egp=1000),
                              session_start=SESSION_START, session_close=SESSION_CLOSE,
                              connection_outage_intervals=[])  # market-wide healthy
    assert r.max_gap_seconds >= 10 * 60 - 60
    assert r.max_gap_classification == "SYMBOL_INACTIVE_WHILE_CONNECTION_HEALTHY"
    assert r.fatal_gap_seconds < 5 * 60
    assert r.range_status == RangeStatus.RANGE_CONFIRMED


def test_report_runtime_invariance():
    # The same immutable session must yield the same range verdict whether the
    # report runs right after close or hours later.
    ev = _bursty_session(burst_gap_seconds=240, n_bursts=66, per_burst=12)
    just_after = pd.Timestamp("2026-07-21 11:35", tz="UTC")
    hours_later = pd.Timestamp("2026-07-21 20:00", tz="UTC")
    cfg = EventQualityConfig(min_turnover_egp=1000)
    a = compute_event_quality("X.CA", ev, now=just_after, config=cfg,
                              session_start=SESSION_START, session_close=SESSION_CLOSE,
                              connection_outage_intervals=[])
    b = compute_event_quality("X.CA", ev, now=hours_later, config=cfg,
                              session_start=SESSION_START, session_close=SESSION_CLOSE,
                              connection_outage_intervals=[])
    assert a.range_status == b.range_status
    assert a.fatal_gap_seconds == b.fatal_gap_seconds
    assert a.max_gap_seconds == b.max_gap_seconds
