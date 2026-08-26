"""The shipped defaults are the measured configuration, pinned.

`DEFAULT_SETTINGS` and the running `config/settings.json` had drifted into two
different strategies — eleven settings apart, not one parameter nudged. Both
were measured on 2026-08-26 under the corrected cost model. The configuration
now in the defaults returned **+60.08%** at profit factor **1.29** with a
**16.17%** drawdown and seven of ten years positive. The alternative returned
**-36.23%** at **0.88** with a **46.3%** drawdown and three of ten.

The defaults lost, so the defaults changed. These tests stop them drifting back
without a measurement, which is how they got there the first time.

They pin *values*, not agreement with `config/settings.json`. That file is meant
to be edited; a test asserting the two match would fail the moment anyone tuned
anything, and would teach people to ignore it.
"""

import pytest

from config.settings_manager import DEFAULT_SETTINGS

STRATEGY = DEFAULT_SETTINGS["strategy"]
BACKTEST = DEFAULT_SETTINGS["backtest"]


@pytest.mark.parametrize("key,expected", [
    ("min_score", 50),
    ("min_confidence", 65),
    ("min_trend", 12),
    ("min_momentum", 3),
    ("min_volume", 0),
    ("quality_min_adx", 21),
])
def test_thresholds_are_the_measured_ones(key, expected):
    assert STRATEGY[key] == expected


def test_the_thresholds_are_the_lower_pair_not_the_higher_one():
    # The losing configuration raised these. They filter on a score measured at
    # r = -0.032 against outcome -- no signal -- so raising them discards trades
    # without improving the ones that survive.
    assert STRATEGY["min_score"] < 65
    assert STRATEGY["min_confidence"] < 80
    assert STRATEGY["min_trend"] < 25


@pytest.mark.parametrize("key", ["require_market_analyzer", "require_quality_filter"])
def test_both_gates_are_on(key):
    # The losing configuration had both off. More trades, worse ones.
    assert STRATEGY[key] is True


def test_exit_takes_the_second_target_with_a_partial():
    # TARGET1 exits earlier for less. Paired with min_rr 3.0, which selects
    # distant targets, it captures a small part of the move the ratio was
    # computed on: win rate rises, profit factor falls below 1.
    assert BACKTEST["exit_mode"] == "TARGET2"
    assert BACKTEST["partial_exit"] is True


def test_overlapping_trades_are_allowed():
    assert BACKTEST["allow_overlapping_trades"] is True


def test_the_risk_control_and_its_trailing_stop_are_both_present():
    # min_rr 3.0 is a risk control; the trailing stop is not separable from it.
    # At 1.5 the trail was worth 0.095%; at 3.0 it is +40.36% against -32.52%.
    assert STRATEGY["min_rr"] == 3.0
    assert BACKTEST["trailing_enabled"] is True


def test_candle_confirmation_stays_off():
    # Its detector refuses to score a fabricated Open, so requiring it would
    # block every signal. Fail-closed and intended, but only safe while off.
    assert STRATEGY["require_candle_confirmation"] is False


def test_costs_are_the_contract_note_and_a_measured_spread():
    assert BACKTEST["commission"] == pytest.approx(0.001819)
    assert BACKTEST["spread_percent"] > 0
    # Unmeasured symbols pay more than the median, never less.
    assert BACKTEST["unmeasured_spread_percent"] > BACKTEST["spread_percent"]


def test_the_universe_filter_ships_off():
    # Measured and rejected: tightening it degrades results monotonically, and
    # the one threshold that looked like a win rested on three trades.
    assert BACKTEST["max_spread_percent"] is None
