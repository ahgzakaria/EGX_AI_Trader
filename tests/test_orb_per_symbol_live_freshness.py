"""Per-symbol live decision authority.

Research only. Nothing here starts Rubix, opens a websocket, authenticates,
reaches the network, triggers a scheduled task, or reads a production database.

2026-08-05 observed 6,847 valid live breakouts across 86 symbols and rejected
every one for "freshness" without reading a single quote age: the rejection was
appended unconditionally once the mode was live. `STALE_LIVE_DATA` appeared zero
times in 18,818 Lane A rows, median receive lag was 0.818 s against a 60 s
budget, and `stale_live_bars` was 0.

The gate now asks a per-symbol question. These tests pin the contract, and in
particular the clause a session-wide watermark cannot express: one symbol
ticking must not make another symbol's four-hour-old print look current.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from scalping_orb.capabilities import (
    LIVE_DECISION_ENABLED,
    LiveDecisionCapability,
    assess_live_decision_capability,
)
from scalping_orb.config import OrbDataConfig
from scalping_orb.events import (
    FeedTimestampQuality,
    LiveFreshnessStatus,
    MarketTimeStatus,
    NormalizedIntradayEvent,
    SpreadCapability,
    UniverseMembershipStatus,
    VolumeCapability,
)


CAIRO = timezone(timedelta(hours=3))
DAY = date(2026, 8, 5)
CONFIG = OrbDataConfig()
BUDGET = CONFIG.maximum_quote_age_seconds


def at(hour, minute, second=0):
    return datetime.combine(DAY, time(hour, minute, second), tzinfo=CAIRO).astimezone(
        timezone.utc
    )


def event(
    *,
    ticker="AAA",
    market_at=None,
    received_at=None,
    market_time_status=MarketTimeStatus.MARKET_TIME_VALID,
    live_freshness_status=LiveFreshnessStatus.LIVE_FRESHNESS_PASSED,
    receive_lag_seconds=0.8,
):
    market = market_at or at(11, 0)
    received = received_at or (market + timedelta(seconds=receive_lag_seconds))
    return NormalizedIntradayEvent(
        canonical_ticker=ticker,
        verified_rubix_symbol=ticker,
        session_date=DAY,
        market_timestamp_utc=market,
        receive_timestamp_utc=received,
        sequence=1,
        last_price=10.0,
        cumulative_volume=1000.0,
        bid=9.99,
        ask=10.01,
        source_event_type="QUOTE",
        feed_timestamp_quality=FeedTimestampQuality.VERIFIED_FEED_TIMESTAMP,
        source_identity=f"{ticker}-1",
        quote_age_seconds=receive_lag_seconds,
        spread_absolute=0.02,
        spread_percent=0.2,
        spread_capability=SpreadCapability.SPREAD_AVAILABLE,
        volume_capability=VolumeCapability.VOLUME_AVAILABLE,
        sequence_gap=0,
        duplicate_status="UNIQUE",
        out_of_order_status="IN_ORDER",
        session_phase="CONTINUOUS",
        quality_flags=(),
        universe_membership_status=(
            UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX
        ),
        operationally_eligible=True,
        market_time_status=market_time_status,
        historical_replay_status=None,
        live_freshness_status=live_freshness_status,
        receive_lag_seconds=receive_lag_seconds,
        collector_age_seconds=0.1,
    )


def assess(latest, *, now=None, source_healthy=True):
    return assess_live_decision_capability(
        latest,
        evaluated_at_utc=now or at(11, 0, 30),
        config=CONFIG,
        source_healthy=source_healthy,
    )


# =========================================================================== #
# THE ENABLING PATH
# =========================================================================== #


def test_a_fresh_symbol_is_enabled_for_research_only():
    capability, reason = assess(event(received_at=at(11, 0, 20)), now=at(11, 0, 30))
    assert capability is LIVE_DECISION_ENABLED
    assert capability is LiveDecisionCapability.LIVE_DECISION_ENABLED_RESEARCH_ONLY
    assert "10.0s old" in reason


def test_the_enabled_capability_authorises_research_only():
    assert LIVE_DECISION_ENABLED.value == "LIVE_DECISION_ENABLED_RESEARCH_ONLY"
    for forbidden in ("ORDER", "EXECUTE", "TRADE", "BUY", "SELL", "POSITION"):
        assert forbidden not in LIVE_DECISION_ENABLED.value.upper()


# =========================================================================== #
# FAIL-CLOSED CLAUSES
# =========================================================================== #


def test_an_unhealthy_source_disables_every_symbol():
    capability, reason = assess(event(), source_healthy=False)
    assert capability is (
        LiveDecisionCapability.LIVE_DECISION_DISABLED_SOURCE_UNHEALTHY
    )
    assert "source" in reason


def test_a_symbol_with_no_evidence_of_its_own_is_disabled():
    capability, _ = assess(None)
    assert capability is (
        LiveDecisionCapability.LIVE_DECISION_DISABLED_NO_SYMBOL_EVIDENCE
    )


def test_an_unreliable_market_timestamp_disables_the_symbol():
    capability, _ = assess(
        event(market_time_status=MarketTimeStatus.MARKET_TIME_UNRELIABLE)
    )
    assert capability is (
        LiveDecisionCapability.LIVE_DECISION_DISABLED_MARKET_TIME_UNRELIABLE
    )


def test_a_failed_arrival_freshness_disables_the_symbol():
    capability, _ = assess(
        event(live_freshness_status=LiveFreshnessStatus.LIVE_FRESHNESS_FAILED)
    )
    assert capability is LiveDecisionCapability.LIVE_DECISION_DISABLED_STALE_QUOTE


def test_a_receive_lag_over_budget_disables_the_symbol():
    capability, _ = assess(event(receive_lag_seconds=BUDGET + 1))
    assert capability is LiveDecisionCapability.LIVE_DECISION_DISABLED_STALE_QUOTE


def test_a_missing_receive_timestamp_fails_closed():
    stale = event()
    object.__setattr__(stale, "receive_timestamp_utc", None)
    capability, _ = assess(stale)
    assert capability is not LIVE_DECISION_ENABLED


# =========================================================================== #
# THE CLAUSE A WATERMARK CANNOT EXPRESS
# =========================================================================== #


def test_a_symbol_that_stopped_printing_goes_stale_even_though_it_arrived_fresh():
    """The 2026-08-05 failure mode, inverted.

    This event was perfectly fresh when it arrived at 10:05 — verified feed
    timestamp, 0.8 s receive lag, LIVE_FRESHNESS_PASSED — and it keeps that
    status forever, because the status is settled at normalization time. By
    14:00 the price is four hours old and must not authorise anything.
    """

    printed_once = event(market_at=at(10, 5))
    assert printed_once.live_freshness_status is (
        LiveFreshnessStatus.LIVE_FRESHNESS_PASSED
    )

    fresh_capability, _ = assess(printed_once, now=at(10, 5, 30))
    assert fresh_capability is LIVE_DECISION_ENABLED

    stale_capability, reason = assess(printed_once, now=at(14, 0))
    assert stale_capability is (
        LiveDecisionCapability.LIVE_DECISION_DISABLED_STALE_QUOTE
    )
    assert "over the" in reason


@pytest.mark.parametrize("age", [0.0, BUDGET - 0.1, BUDGET])
def test_ages_inside_the_budget_are_enabled(age):
    moment = at(11, 0)
    capability, _ = assess(
        event(received_at=moment), now=moment + timedelta(seconds=age)
    )
    assert capability is LIVE_DECISION_ENABLED


@pytest.mark.parametrize("age", [BUDGET + 0.1, BUDGET * 2, 3600.0])
def test_ages_outside_the_budget_are_disabled(age):
    moment = at(11, 0)
    capability, _ = assess(
        event(received_at=moment), now=moment + timedelta(seconds=age)
    )
    assert capability is LiveDecisionCapability.LIVE_DECISION_DISABLED_STALE_QUOTE


def test_a_quote_from_the_future_fails_closed():
    moment = at(11, 0)
    capability, reason = assess(
        event(received_at=moment), now=moment - timedelta(seconds=600)
    )
    assert capability is not LIVE_DECISION_ENABLED
    assert "future" in reason


def test_the_budget_is_unchanged_at_sixty_seconds():
    """Pinned deliberately: this change re-wires the gate, not the threshold."""

    assert OrbDataConfig().maximum_quote_age_seconds == 60.0


# =========================================================================== #
# NEVER ANOTHER SYMBOL'S EVIDENCE
# =========================================================================== #


def test_one_symbol_ticking_does_not_refresh_another(monkeypatch):
    """The whole reason the watermark cannot be the authority.

    AAA last printed at 10:05. BBB printed a second ago. A session-wide
    watermark is fresh because of BBB, and AAA must still be refused.
    """

    from scalping_orb.shadow_service import OrbShadowService

    now = at(14, 0)
    stale_symbol = event(ticker="AAA", market_at=at(10, 5))
    live_symbol = event(ticker="BBB", market_at=at(13, 59, 58))

    class _Snapshot:
        events_by_ticker = {"AAA": (stale_symbol,), "BBB": (live_symbol,)}
        evaluated_at_utc = now

    latest_a = OrbShadowService._latest_event(_Snapshot(), "AAA")
    latest_b = OrbShadowService._latest_event(_Snapshot(), "BBB")
    assert latest_a is stale_symbol
    assert latest_b is live_symbol

    # Source healthy for both — that is what the watermark would say.
    capability_a, _ = assess(latest_a, now=now, source_healthy=True)
    capability_b, _ = assess(latest_b, now=now, source_healthy=True)
    assert capability_a is (
        LiveDecisionCapability.LIVE_DECISION_DISABLED_STALE_QUOTE
    )
    assert capability_b is LIVE_DECISION_ENABLED


def test_the_latest_event_is_selected_by_receive_time():
    from scalping_orb.shadow_service import OrbShadowService

    early = event(market_at=at(10, 0))
    late = event(market_at=at(13, 0))

    class _Snapshot:
        events_by_ticker = {"AAA": (late, early)}  # deliberately unordered
        evaluated_at_utc = at(13, 0, 30)

    assert OrbShadowService._latest_event(_Snapshot(), "AAA") is late


def test_an_unknown_symbol_resolves_to_no_evidence():
    from scalping_orb.shadow_service import OrbShadowService

    class _Snapshot:
        events_by_ticker = {"AAA": (event(),)}
        evaluated_at_utc = at(11, 0)

    assert OrbShadowService._latest_event(_Snapshot(), "ZZZ") is None
    capability, _ = assess(None)
    assert capability is not LIVE_DECISION_ENABLED


# =========================================================================== #
# PRODUCTION REMAINS IMPOSSIBLE
# =========================================================================== #


def test_no_execution_vocabulary_was_introduced():
    """Scan real code, not the safety prose that documents the prohibition.

    These modules say "never a broker instruction" in their own docstrings, so a
    naive text search matches the promise instead of a breach.
    """

    import inspect

    from scalping_orb import capabilities, engine, shadow_service
    from tests.test_orb_phase2b_core import _code_only

    for module in (capabilities, engine, shadow_service):
        code = _code_only(inspect.getsource(module)).upper()
        for forbidden in (
            "PLACE_ORDER",
            "SUBMIT_ORDER",
            "SEND_ORDER",
            "EXECUTE_TRADE",
            "BROKER",
        ):
            assert forbidden not in code, f"{module.__name__} mentions {forbidden}"
