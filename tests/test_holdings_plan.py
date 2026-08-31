"""Exit-plan construction: where the levels come from, and what is refused.

The frames here are built by hand so every expected level is arithmetic the
reader can redo, not a number captured from a previous run.
"""

from __future__ import annotations

import pandas as pd
import pytest

from holdings.book import FeeModel, build_book
from holdings.plan import (
    NO_ATR,
    NO_HISTORY,
    NO_OVERHEAD_STRUCTURE,
    STOP_ABOVE_PRICE,
    ExitPolicy,
    build_plan,
    load_policy,
    plan_differs,
)


FREE = FeeModel(rates_loaded=True)
POLICY = ExitPolicy(partial_fraction=0.5, move_to_breakeven=True,
                    trailing_enabled=False, max_holding_sessions=20)


def frame(closes, *, highs=None, lows=None, atr=0.5, ema20=None):
    """A minimal indicator frame: 25 daily bars ending on 2026-08-31."""

    index = pd.date_range("2026-07-28", periods=len(closes), freq="D")
    data = {
        "Close": closes,
        "High": highs if highs is not None else [value + 0.1 for value in closes],
        "Low": lows if lows is not None else [value - 0.1 for value in closes],
        "Volume": [100_000] * len(closes),
        "ATR": [atr] * len(closes),
    }
    if ema20 is not None:
        data["EMA20"] = [ema20] * len(closes)
    return pd.DataFrame(data, index=index)


def flat_then(last, *, count=25, base=10.0, **kwargs):
    return frame([base] * (count - 1) + [last], **kwargs)


def position(quantity=100, price=10.0):
    return build_book(
        [{"kind": "BUY", "symbol": "ABUK", "date": "2026-08-03",
          "quantity": quantity, "price": price}], FREE,
    ).positions["ABUK"]


# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #

def test_targets_are_resistance_then_the_atr_expansion_above_it():
    """The daily engine's own two targets, applied to a position already held."""

    # 24 bars at 10.00 (highs 10.10), then a close at 10.05 below that high.
    plan = build_plan("ABUK", flat_then(10.05, atr=0.5), policy=POLICY)

    assert plan.available
    assert plan.resistance == pytest.approx(10.10)
    assert plan.target_partial == pytest.approx(10.10)
    # resistance + 2 * ATR
    assert plan.target_final == pytest.approx(11.10)
    assert plan.target_source == "RESISTANCE_THEN_ATR_EXPANSION"


def test_the_stop_is_the_structural_one_the_signal_cards_use():
    """``support - 0.30 * ATR``: a held position is stopped where a new signal
    in the same stock would be."""

    plan = build_plan("ABUK", flat_then(10.05, atr=0.5), policy=POLICY)

    # 20-bar low is 9.90 (lows are close - 0.10); 9.90 - 0.15.
    assert plan.support == pytest.approx(9.90)
    assert plan.stop == pytest.approx(9.75)
    assert plan.stop_source == "STRUCTURE"


def test_a_position_above_every_named_level_gets_no_final_target():
    """Absence is stated. There is nothing overhead to call a final target, and
    a round number invented to fill the gap would read as measured."""

    # A close at 12.00, far above the 10.10 resistance and the 11.10 extension.
    plan = build_plan("ABUK", flat_then(12.0, atr=0.5), policy=POLICY)

    assert plan.target_partial is None
    assert plan.target_final is None
    assert NO_OVERHEAD_STRUCTURE in plan.notes


def test_a_position_between_resistance_and_the_extension_keeps_one_target():
    """Past the 20-bar high but not past the extension: one real level remains,
    and the rest is managed by the stop."""

    plan = build_plan("ABUK", flat_then(10.5, atr=0.5), policy=POLICY)

    assert plan.target_partial == pytest.approx(11.10)
    assert plan.target_final is None
    assert plan.target_source == "ATR_EXPANSION_ONLY"


# --------------------------------------------------------------------------- #
# The stop only ever tightens
# --------------------------------------------------------------------------- #

def test_a_stop_already_in_force_is_never_loosened():
    """The rule that decides whether a small loss stays small."""

    plan = build_plan("ABUK", flat_then(10.05, atr=0.5), policy=POLICY,
                      previous_stop=9.90)

    assert plan.stop == pytest.approx(9.90)
    assert plan.stop_source == "CARRIED"


def test_a_tighter_computed_stop_replaces_a_looser_carried_one():
    plan = build_plan("ABUK", flat_then(10.05, atr=0.5), policy=POLICY,
                      previous_stop=9.00)

    assert plan.stop == pytest.approx(9.75)
    assert plan.stop_source == "STRUCTURE"


def test_after_the_partial_the_stop_moves_to_breakeven_not_to_the_average():
    """Breakeven includes the cost of leaving, so the remainder cannot turn into
    a loss through fees alone."""

    fees = FeeModel(percent_per_side=0.01, order_fee_egp=10.0, rates_loaded=True)
    held = build_book(
        [{"kind": "BUY", "symbol": "ABUK", "date": "2026-08-03",
          "quantity": 100, "price": 10.0}], fees,
    ).positions["ABUK"]

    plan = build_plan("ABUK", flat_then(10.6, atr=0.5), policy=POLICY,
                      position=held, fee_model=fees, partial_taken=True)

    assert plan.stop == pytest.approx(plan.breakeven)
    assert plan.stop > held.average_price
    assert plan.stop_source == "BREAKEVEN"


def test_the_breakeven_stop_is_not_applied_before_the_partial_is_taken():
    fees = FeeModel(percent_per_side=0.01, order_fee_egp=10.0, rates_loaded=True)
    held = position()

    plan = build_plan("ABUK", flat_then(10.05, atr=0.5), policy=POLICY,
                      position=held, fee_model=fees, partial_taken=False)

    assert plan.stop_source == "STRUCTURE"


def test_a_trailing_stop_above_the_price_is_reported_not_tidied_away():
    """Price below the EMA the policy trails on is a broken trend, and the plan
    has to say so rather than quietly show a stop that cannot be placed."""

    trailing = ExitPolicy(trailing_enabled=True, trailing_mode="EMA20")
    plan = build_plan("ABUK", flat_then(10.05, atr=0.5, ema20=10.40),
                      policy=trailing)

    assert plan.trailing_stop == pytest.approx(10.40)
    assert plan.stop == pytest.approx(10.40)
    assert STOP_ABOVE_PRICE in plan.notes
    assert plan.stop_breached


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

def test_a_short_history_produces_no_levels():
    plan = build_plan("ABUK", frame([10.0] * 5), policy=POLICY)

    assert not plan.available
    assert plan.reason == NO_HISTORY


def test_a_missing_atr_refuses_to_fall_back_to_a_percentage():
    """A percentage stop on a stock whose real range is three times wider is a
    stop that noise hits every week."""

    bars = flat_then(10.05)
    bars["ATR"] = float("nan")

    plan = build_plan("ABUK", bars, policy=POLICY)

    assert not plan.available
    assert plan.reason == NO_ATR


def test_reward_risk_is_none_rather_than_negative_when_there_is_no_reward():
    plan = build_plan("ABUK", flat_then(12.0, atr=0.5), policy=POLICY)

    assert plan.reward_risk is None


# --------------------------------------------------------------------------- #
# Versioning
# --------------------------------------------------------------------------- #

def test_an_unchanged_plan_is_not_written_again():
    plan = build_plan("ABUK", flat_then(10.05, atr=0.5), policy=POLICY)
    stored = {"stop": plan.stop, "target_partial": plan.target_partial,
              "target_final": plan.target_final}

    assert not plan_differs(stored, plan)
    assert plan_differs({**stored, "stop": 9.50}, plan)


# --------------------------------------------------------------------------- #
# The policy is the measured one
# --------------------------------------------------------------------------- #

def test_the_policy_is_read_from_the_backtested_settings_not_restated_here():
    """If these two ever diverge, the portfolio is managing exits by rules the
    backtest never tested."""

    policy = load_policy()

    assert policy.partial_fraction == pytest.approx(0.5)
    assert policy.move_to_breakeven is True
    assert policy.max_holding_sessions == 20
    assert policy.source == "config/settings.json:backtest"
