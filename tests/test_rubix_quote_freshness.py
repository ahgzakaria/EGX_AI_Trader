"""Rubix quote freshness: session membership and phase decide, not elapsed age.

The defect this pins (RUN_20260804_005327): a quote stamped 2026-08-03 14:30
Cairo was labelled ``FRESH`` at 00:53 Cairo the next morning, 4.45 hours old,
above a 2026-07-30 daily candle. The old rule returned FRESH whenever the
quote's session equalled the latest completed session and the market was not
open - the age check only ever ran inside the OPEN branch.

Every test injects its evaluation instant, so none of them changes meaning at a
date rollover. Nothing here opens a socket, authenticates, manages a collector
process, reads the production database or touches a threshold.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from core.rubix_quote_freshness import (
    DEFAULT_FRESHNESS_SECONDS,
    FRESHNESS_SETTING_KEY,
    STATUS_LABEL,
    ExchangePhase,
    RubixQuoteStatus,
    classify_rubix_quote,
    evaluate_overlay_permission,
    exchange_phase,
    freshness_budget_seconds,
)


CAIRO = ZoneInfo("Africa/Cairo")
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 2026-08-03 is a Monday, 2026-08-02 a Sunday (EGX trades Sun-Thu),
#: 2026-08-01 a Saturday, 2026-07-30 a Thursday.
SESSION = "2026-08-03"
PRIOR_SESSION = "2026-07-30"


def cairo(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=CAIRO).astimezone(
        timezone.utc)


def quote(**overrides):
    """A verified, in-session, freshly received quote unless overridden."""
    base = dict(
        symbol="SPIN.CA",
        evaluated_at=cairo(2026, 8, 3, 12, 0),
        mapping_verified=True,
        quote_price=15.81,
        market_timestamp=cairo(2026, 8, 3, 11, 59),
        receive_timestamp=cairo(2026, 8, 3, 11, 59),
        permitted_session=SESSION,
    )
    base.update(overrides)
    symbol = base.pop("symbol")
    return classify_rubix_quote(symbol, **base)


# =========================================================================== #
# CLASSIFICATION
# =========================================================================== #


def test_a_verified_in_session_quote_is_live():
    assessment = quote()
    assert assessment.status is RubixQuoteStatus.RUBIX_LIVE_CURRENT
    assert assessment.is_live is True
    assert assessment.quote_session == SESSION
    assert assessment.phase == ExchangePhase.CONTINUOUS.value


def test_the_same_quote_after_close_is_the_session_last_not_live():
    """The exact shape of the incident, one phase later."""

    assessment = quote(evaluated_at=cairo(2026, 8, 3, 14, 30))
    assert assessment.status is RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST
    assert assessment.is_live is False
    assert "NOT LIVE" in assessment.label


def test_the_observed_incident_quote_is_never_live():
    """2026-08-03 14:30 Cairo, evaluated the next morning at 00:53."""

    market = cairo(2026, 8, 3, 14, 30)
    receive = cairo(2026, 8, 3, 20, 26)
    for moment, permitted, expected in [
        (cairo(2026, 8, 3, 14, 30), SESSION,
         RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST),
        (cairo(2026, 8, 4, 0, 53), SESSION,
         RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST),
        (cairo(2026, 8, 4, 11, 0), "2026-08-04",
         RubixQuoteStatus.RUBIX_PREVIOUS_SESSION),
    ]:
        assessment = quote(evaluated_at=moment, market_timestamp=market,
                           receive_timestamp=receive, permitted_session=permitted)
        assert assessment.status is expected, moment
        assert assessment.is_live is False


def test_a_previous_session_quote_is_named_as_such():
    assessment = quote(
        evaluated_at=cairo(2026, 8, 3, 12, 0),
        market_timestamp=cairo(2026, 7, 30, 14, 0),
        receive_timestamp=cairo(2026, 7, 30, 14, 0),
        permitted_session=SESSION)
    assert assessment.status is RubixQuoteStatus.RUBIX_PREVIOUS_SESSION
    message = assessment.message()
    assert "RUBIX PREVIOUS SESSION" in message
    assert f"Last quote session: {PRIOR_SESSION}" in message
    assert "Daily EODHD close retained." in message


def test_an_in_session_quote_beyond_the_receive_budget_is_stale():
    assessment = quote(
        evaluated_at=cairo(2026, 8, 3, 12, 0),
        market_timestamp=cairo(2026, 8, 3, 11, 0),
        receive_timestamp=cairo(2026, 8, 3, 11, 0),
        budget_seconds=300.0)
    assert assessment.status is RubixQuoteStatus.RUBIX_STALE
    message = assessment.message()
    assert "RUBIX QUOTE STALE" in message
    assert "Last market time:" in message and "Last receive time:" in message
    assert "Daily EODHD close retained." in message


def test_a_non_progressing_source_is_stale_even_inside_the_budget():
    assessment = quote(source_progressing=False)
    assert assessment.status is RubixQuoteStatus.RUBIX_STALE


@pytest.mark.parametrize("price", [None, 0.0, -1.0, "not-a-price"])
def test_a_missing_or_invalid_price_is_unavailable(price):
    assert quote(quote_price=price).status is RubixQuoteStatus.RUBIX_UNAVAILABLE


def test_an_unverified_mapping_is_unmapped_not_unavailable():
    """An overlay on an unverified symbol is a wrong-symbol price."""

    assessment = quote(mapping_verified=False)
    assert assessment.status is RubixQuoteStatus.RUBIX_UNMAPPED


@pytest.mark.parametrize("field", ["market_timestamp", "receive_timestamp"])
@pytest.mark.parametrize("value", [None, "", "not-a-timestamp"])
def test_missing_or_malformed_timestamps_are_invalid_not_unavailable(field, value):
    """'No quote' and 'a quote we cannot trust' need different responses."""

    assessment = quote(**{field: value})
    assert assessment.status is RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID


def test_a_future_market_timestamp_is_invalid():
    assessment = quote(
        evaluated_at=cairo(2026, 8, 3, 12, 0),
        market_timestamp=cairo(2026, 8, 3, 13, 0),
        receive_timestamp=cairo(2026, 8, 3, 13, 0))
    assert assessment.status is RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID
    assert "future" in assessment.reason


def test_a_receive_timestamp_far_before_its_market_timestamp_is_invalid():
    assessment = quote(
        market_timestamp=cairo(2026, 8, 3, 11, 59),
        receive_timestamp=cairo(2026, 8, 3, 11, 50))
    assert assessment.status is RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID
    assert "precedes" in assessment.reason


def test_small_clock_skew_is_tolerated():
    """Seconds of feed/host skew is normal; minutes of it is a fault."""

    assessment = quote(
        market_timestamp=cairo(2026, 8, 3, 11, 59),
        receive_timestamp=cairo(2026, 8, 3, 11, 59) - timedelta(seconds=30))
    assert assessment.status is RubixQuoteStatus.RUBIX_LIVE_CURRENT


def test_a_quote_dated_after_the_permitted_session_is_invalid():
    assessment = quote(
        evaluated_at=cairo(2026, 8, 4, 12, 0),
        market_timestamp=cairo(2026, 8, 4, 11, 59),
        receive_timestamp=cairo(2026, 8, 4, 11, 59),
        permitted_session=SESSION)
    assert assessment.status is RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID


def test_no_status_is_labelled_generically_fresh():
    for status, label in STATUS_LABEL.items():
        assert label.upper() == label
        if status is not RubixQuoteStatus.RUBIX_LIVE_CURRENT:
            assert "LIVE CURRENT" not in label


# =========================================================================== #
# SESSION PHASES
# =========================================================================== #


@pytest.mark.parametrize("hour,minute,expected", [
    (9, 59, ExchangePhase.PRE_OPEN),
    (10, 0, ExchangePhase.CONTINUOUS),          # boundary: open is inclusive
    (12, 0, ExchangePhase.CONTINUOUS),
    (14, 14, ExchangePhase.CONTINUOUS),
    (14, 15, ExchangePhase.CLOSING_AUCTION),    # boundary: auction starts
    (14, 20, ExchangePhase.CLOSING_AUCTION),
    (14, 25, ExchangePhase.POST_MARKET),        # boundary: auction ends
    (16, 0, ExchangePhase.POST_MARKET),
])
def test_phase_boundaries_on_a_trading_day(hour, minute, expected):
    assert exchange_phase(cairo(2026, 8, 3, hour, minute)) is expected


def test_sunday_is_a_trading_day():
    assert exchange_phase(cairo(2026, 8, 2, 12, 0)) is ExchangePhase.CONTINUOUS


@pytest.mark.parametrize("day", [1, 7])          # Saturday, Friday
def test_weekends_are_not_trading_days(day):
    assert exchange_phase(cairo(2026, 8, day, 12, 0)) is ExchangePhase.WEEKEND


def test_a_configured_holiday_is_not_a_trading_day():
    from datetime import date

    assert exchange_phase(cairo(2026, 8, 3, 12, 0),
                          holidays=[date(2026, 8, 3)]) is ExchangePhase.HOLIDAY


def test_an_auction_quote_may_be_live_but_a_post_market_one_may_not():
    """14:20 is auction; 14:30 is not, and must not use continuous rules."""

    auction = quote(evaluated_at=cairo(2026, 8, 3, 14, 20),
                    market_timestamp=cairo(2026, 8, 3, 14, 19),
                    receive_timestamp=cairo(2026, 8, 3, 14, 19))
    assert auction.status is RubixQuoteStatus.RUBIX_LIVE_CURRENT

    post = quote(evaluated_at=cairo(2026, 8, 3, 14, 30),
                 market_timestamp=cairo(2026, 8, 3, 14, 29),
                 receive_timestamp=cairo(2026, 8, 3, 14, 29))
    assert post.status is RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST


def test_a_thursday_quote_does_not_become_current_on_the_next_sunday():
    assessment = quote(
        evaluated_at=cairo(2026, 8, 2, 12, 0),
        market_timestamp=cairo(2026, 7, 30, 14, 0),
        receive_timestamp=cairo(2026, 7, 30, 14, 0),
        permitted_session="2026-08-02")
    assert assessment.status is RubixQuoteStatus.RUBIX_PREVIOUS_SESSION


def test_a_quote_on_a_weekend_evaluation_is_not_live():
    assessment = quote(
        evaluated_at=cairo(2026, 8, 1, 12, 0),
        market_timestamp=cairo(2026, 7, 30, 14, 0),
        receive_timestamp=cairo(2026, 7, 30, 14, 0),
        permitted_session=PRIOR_SESSION)
    assert assessment.status is RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST
    assert assessment.phase == ExchangePhase.WEEKEND.value


# =========================================================================== #
# OVERLAY PERMISSION
# =========================================================================== #


def test_a_live_quote_on_a_current_daily_symbol_may_overlay():
    permission = evaluate_overlay_permission(quote(), daily_symbol_current=True)
    assert permission.may_label_live is True
    assert permission.may_overlay_display_price is True
    assert permission.may_enter_decision_inputs is True
    assert permission.retained_daily_close is False
    assert permission.decision_price_source == "rubix_live"


def test_a_live_quote_cannot_rescue_a_daily_stale_symbol():
    """The strategy reads a daily candle. A tick is not one."""

    permission = evaluate_overlay_permission(quote(), daily_symbol_current=False)
    assert permission.may_enter_decision_inputs is False
    assert permission.may_overlay_display_price is False
    assert permission.retained_daily_close is True
    assert permission.decision_price_source == "eodhd_daily_close"
    assert "not CURRENT" in permission.denial_reason


@pytest.mark.parametrize("factory,expected", [
    (lambda: quote(evaluated_at=cairo(2026, 8, 3, 14, 30)),
     RubixQuoteStatus.RUBIX_CURRENT_SESSION_LAST),
    (lambda: quote(market_timestamp=cairo(2026, 7, 30, 14, 0),
                   receive_timestamp=cairo(2026, 7, 30, 14, 0)),
     RubixQuoteStatus.RUBIX_PREVIOUS_SESSION),
    (lambda: quote(market_timestamp=cairo(2026, 8, 3, 11, 0),
                   receive_timestamp=cairo(2026, 8, 3, 11, 0)),
     RubixQuoteStatus.RUBIX_STALE),
    (lambda: quote(market_timestamp=None), RubixQuoteStatus.RUBIX_TIMESTAMP_INVALID),
    (lambda: quote(mapping_verified=False), RubixQuoteStatus.RUBIX_UNMAPPED),
    (lambda: quote(quote_price=None), RubixQuoteStatus.RUBIX_UNAVAILABLE),
])
def test_every_non_live_status_is_denied_the_decision_inputs(factory, expected):
    assessment = factory()
    assert assessment.status is expected
    permission = evaluate_overlay_permission(assessment, daily_symbol_current=True)
    assert permission.may_enter_decision_inputs is False
    assert permission.may_label_live is False
    assert permission.retained_daily_close is True
    assert permission.decision_price_source == "eodhd_daily_close"
    assert permission.denial_reason


def test_a_denied_overlay_states_the_retained_price_plainly():
    permission = evaluate_overlay_permission(
        quote(evaluated_at=cairo(2026, 8, 3, 14, 30)), daily_symbol_current=True)
    message = permission.denial_message()
    assert "Decision price: EODHD daily close" in message
    assert "Rubix overlay: not applied" in message


def test_display_and_decision_price_sources_stay_distinct():
    denied = evaluate_overlay_permission(
        quote(evaluated_at=cairo(2026, 8, 3, 14, 30)), daily_symbol_current=True)
    assert denied.display_price_source == "eodhd_daily_close"
    assert denied.decision_price_source == "eodhd_daily_close"
    row = denied.as_row()
    assert row["RubixOverlayApplied"] is False
    assert row["RubixDecisionInputAllowed"] is False
    assert row["DecisionPriceSource"] == "eodhd_daily_close"


def test_a_previous_session_quote_may_still_be_displayed():
    """Withholding the number entirely would hide evidence the operator needs."""

    assessment = quote(market_timestamp=cairo(2026, 7, 30, 14, 0),
                       receive_timestamp=cairo(2026, 7, 30, 14, 0))
    permission = evaluate_overlay_permission(assessment, daily_symbol_current=True)
    assert permission.may_display_quote is True
    assert permission.may_label_live is False


# =========================================================================== #
# CONFIGURATION
# =========================================================================== #


def test_the_budget_is_a_registered_data_quality_setting():
    from config.settings_manager import DEFAULT_SETTINGS

    assert FRESHNESS_SETTING_KEY in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS[FRESHNESS_SETTING_KEY] == DEFAULT_FRESHNESS_SECONDS

    import inspect

    import config.settings_manager as module

    source = inspect.getsource(module)
    preamble = source[max(0, source.index(FRESHNESS_SETTING_KEY) - 600):
                      source.index(FRESHNESS_SETTING_KEY)]
    assert "DATA-QUALITY" in preamble
    assert "not a strategy threshold" in preamble


def test_a_broken_or_negative_budget_falls_back_to_the_default():
    assert freshness_budget_seconds({FRESHNESS_SETTING_KEY: -1}) == \
        DEFAULT_FRESHNESS_SECONDS
    assert freshness_budget_seconds({FRESHNESS_SETTING_KEY: "x"}) == \
        DEFAULT_FRESHNESS_SECONDS
    assert freshness_budget_seconds({}) == DEFAULT_FRESHNESS_SECONDS


# =========================================================================== #
# PURITY AND BOUNDARIES
# =========================================================================== #


def test_the_classifier_reads_no_clock_database_or_settings():
    import ast

    source = (REPO_ROOT / "core" / "rubix_quote_freshness.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    target = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "classify_rubix_quote")
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            for forbidden in ("now", "today", "utcnow", "connect", "execute",
                              "session_state", "getmtime"):
                assert not name.endswith(forbidden), name


def test_the_module_never_manages_a_collector_or_reaches_a_network():
    source = (REPO_ROOT / "core" / "rubix_quote_freshness.py").read_text(
        encoding="utf-8")
    for forbidden in ("sqlite3", "subprocess", "requests", "urllib", "websocket",
                      "Popen", "yahoo", "api_token", "password"):
        assert forbidden not in source.lower()


def test_strategy_thresholds_are_unchanged():
    from scalping_orb.strategy_config import OrbStrategyConfig

    config = OrbStrategyConfig()
    assert config.minimum_reward_risk == 1.5
    assert config.target_2_r_multiple == 2.0


def test_no_test_here_reads_the_wall_clock():
    import ast

    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    guard = "test_no_test_here_reads_the_wall_clock"
    skipped = {id(node) for parent in ast.walk(tree)
               if isinstance(parent, ast.FunctionDef) and parent.name == guard
               for node in ast.walk(parent)}
    for node in ast.walk(tree):
        if id(node) in skipped or not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        assert not name.endswith("datetime.now"), name
        assert not name.endswith("date.today"), name
        assert not name.endswith("utcnow"), name
