"""Exit rules: the order they fire in, and what the unmeasured ones may not do.

The constraint under test throughout is that a liquidity reading -- which
nothing on this project has yet validated as an exit signal -- can bank a
profit that already exists but can never pay a 0.46% round trip to realize a
loss.
"""

from __future__ import annotations

import pytest

from holdings.book import FeeModel, build_book
from holdings.plan import ExitPlan
from holdings.rules import (
    EXIT,
    HOLD,
    LIQUIDITY_LEAVING,
    NO_PRICE,
    ON_PLAN,
    RAISE_STOP,
    STOP_BREACHED,
    TARGET_FINAL_REACHED,
    TARGET_PARTIAL_REACHED,
    TIME_STOP,
    TREND_BREAK,
    TRIM,
    WATCH,
    WITHHELD,
    Evidence,
    evaluate,
)


FREE = FeeModel(rates_loaded=True)
REAL = FeeModel(percent_per_side=0.001819, order_fee_egp=4.0, rates_loaded=True)


def held(quantity=1000, price=10.0, fees=FREE):
    return build_book(
        [{"kind": "BUY", "symbol": "ABUK", "date": "2026-08-03",
          "quantity": quantity, "price": price}], fees,
    ).positions["ABUK"]


def plan(**overrides):
    base = {
        "symbol": "ABUK", "available": True, "reference_price": 11.0,
        "stop": 9.75, "target_partial": 12.0, "target_final": 13.0,
        "partial_fraction": 0.5, "max_holding_sessions": 20,
    }
    return ExitPlan(**{**base, **overrides})


DRY = Evidence(relative_volume=0.4, sector="Chemicals", sector_strength=0.3)
BUSY = Evidence(relative_volume=2.5, sector="Chemicals", sector_strength=0.8,
                new_twenty_day_high=True)


# --------------------------------------------------------------------------- #
# The user's own example, end to end
# --------------------------------------------------------------------------- #

def test_the_worked_example_bought_at_ten_partial_at_twelve_final_at_thirteen():
    """Bought at 10, partial at 12, final at 13 -- exactly as described."""

    position = held(price=10.0)

    at_eleven = evaluate(position, plan(), price=11.0, fee_model=FREE)
    at_twelve = evaluate(position, plan(), price=12.0, fee_model=FREE)
    at_thirteen = evaluate(position, plan(), price=13.0, fee_model=FREE)

    assert (at_eleven.action, at_eleven.rule) == (HOLD, ON_PLAN)
    assert (at_twelve.action, at_twelve.rule) == (TRIM, TARGET_PARTIAL_REACHED)
    assert (at_thirteen.action, at_thirteen.rule) == (EXIT, TARGET_FINAL_REACHED)


def test_liquidity_leaving_at_twelve_turns_the_partial_into_a_full_exit():
    """The second half of the user's example: money leaves while the position is
    ahead, so the whole thing is closed instead of half."""

    decision = evaluate(held(price=10.0), plan(), price=12.0,
                        evidence=DRY, fee_model=FREE, partial_taken=True)

    assert decision.action == EXIT
    assert decision.rule == LIQUIDITY_LEAVING
    assert not decision.measured
    assert "السيولة" in decision.reason_ar


# --------------------------------------------------------------------------- #
# Order of precedence
# --------------------------------------------------------------------------- #

def test_a_breached_stop_outranks_every_liquidity_reading():
    decision = evaluate(held(), plan(), price=9.70, evidence=BUSY, fee_model=FREE)

    assert decision.rule == STOP_BREACHED
    assert decision.action == EXIT
    assert decision.urgency == "NOW"


def test_a_broken_trend_exits_even_with_no_level_touched():
    broken = plan(stop=11.40, trailing_stop=11.40, stop_source="TRAILING",
                  notes=("STOP_ABOVE_PRICE",))

    decision = evaluate(held(), broken, price=11.0, fee_model=FREE)

    assert decision.rule == TREND_BREAK
    assert decision.action == EXIT


def test_the_partial_does_not_fire_twice():
    decision = evaluate(held(), plan(), price=12.5, fee_model=FREE,
                        partial_taken=True)

    assert decision.rule != TARGET_PARTIAL_REACHED


def test_the_time_stop_closes_a_position_that_never_reached_its_first_target():
    decision = evaluate(held(), plan(), price=10.5, fee_model=FREE,
                        holding_sessions=21)

    assert decision.rule == TIME_STOP
    assert decision.action == EXIT


def test_the_time_stop_does_not_apply_once_the_partial_is_banked():
    decision = evaluate(held(), plan(), price=10.5, fee_model=FREE,
                        holding_sessions=25, partial_taken=True)

    assert decision.rule != TIME_STOP


# --------------------------------------------------------------------------- #
# The constraint on unmeasured rules
# --------------------------------------------------------------------------- #

def test_liquidity_leaving_never_sells_a_losing_position():
    """The rule that keeps an unmeasured signal from paying a real toll.

    Below breakeven the answer is to tighten and let the stop decide, not to
    realize the loss plus 0.46% on evidence this project has not validated.
    """

    decision = evaluate(held(price=10.0, fees=REAL), plan(), price=9.90,
                        evidence=DRY, fee_model=REAL)

    assert decision.action == RAISE_STOP
    assert decision.rule == LIQUIDITY_LEAVING
    assert decision.action != EXIT


def test_liquidity_leaving_needs_both_the_stock_and_its_sector():
    """A quiet stock inside a busy sector is not money leaving the sector."""

    stock_only = Evidence(relative_volume=0.4, sector_strength=0.9)
    sector_only = Evidence(relative_volume=1.8, sector_strength=0.2)

    for evidence in (stock_only, sector_only):
        decision = evaluate(held(), plan(), price=11.0, evidence=evidence,
                            fee_model=FREE)
        assert decision.rule != LIQUIDITY_LEAVING


def test_the_intraday_forecast_outranks_yesterdays_sector_strength():
    """During an open session, the question is where money goes in the hours
    that are left -- not where it went in a session that has ended."""

    strong_yesterday_leaving_today = Evidence(
        relative_volume=0.4, sector="Chemicals", sector_strength=0.9,
        sector_intraday_change=-0.03,
    )

    decision = evaluate(held(price=10.0), plan(), price=11.0,
                        evidence=strong_yesterday_leaving_today, fee_model=FREE)

    assert decision.rule == LIQUIDITY_LEAVING
    assert decision.action == EXIT


def test_an_inflowing_sector_today_overrides_a_weak_reading_from_yesterday():
    weak_yesterday_arriving_today = Evidence(
        relative_volume=0.4, sector="Chemicals", sector_strength=0.2,
        sector_intraday_change=0.02,
    )

    decision = evaluate(held(), plan(), price=11.0,
                        evidence=weak_yesterday_arriving_today, fee_model=FREE)

    assert decision.rule == ON_PLAN


def test_missing_evidence_is_not_read_as_weak_evidence():
    """An absent sector reading must not fire an exit. Absence is not weakness."""

    decision = evaluate(held(), plan(), price=11.0,
                        evidence=Evidence(relative_volume=0.4), fee_model=FREE)

    assert decision.rule == ON_PLAN


def test_expansion_only_tightens_and_never_recommends_selling():
    decision = evaluate(held(), plan(target_partial=None, target_final=None),
                        price=11.5, evidence=BUSY, fee_model=FREE)

    assert decision.action == RAISE_STOP
    assert not decision.measured


# --------------------------------------------------------------------------- #
# Withholding
# --------------------------------------------------------------------------- #

def test_no_price_is_withheld_rather_than_guessed_from_the_last_close():
    decision = evaluate(held(), plan(), price=None, fee_model=FREE)

    assert decision.action == WITHHELD
    assert decision.rule == NO_PRICE


def test_a_position_with_no_buildable_plan_is_watched_not_advised():
    decision = evaluate(held(), ExitPlan(symbol="ABUK", reason="NO_ATR"),
                        price=11.0, fee_model=FREE)

    assert decision.action == WATCH


# --------------------------------------------------------------------------- #
# Costs reach the recommendation itself
# --------------------------------------------------------------------------- #

def test_every_recommendation_carries_the_net_result_after_costs():
    decision = evaluate(held(price=10.0, fees=REAL), plan(), price=11.0,
                        fee_model=REAL)

    assert decision.net_percent is not None
    assert decision.net_egp is not None
    # 1000 shares: 10% gross, less roughly 0.36% of turnover in fees.
    assert 9.0 < decision.net_percent < 10.0


def test_a_price_above_the_average_but_below_breakeven_is_named_as_a_loss():
    decision = evaluate(held(price=10.0, fees=REAL), plan(), price=10.02,
                        fee_model=REAL)

    assert decision.action == HOLD
    assert decision.net_egp < 0
    assert "التعادل" in decision.reason_ar
