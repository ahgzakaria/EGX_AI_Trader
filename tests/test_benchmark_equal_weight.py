"""The equal-weight benchmark line, and why it sits beside the median one.

`BacktestStatistics` already reported the median traded name bought and held.
That answers "what would holding these names have paid", and a strategy in the
market a quarter of the time can beat it while losing badly to owning the
market -- the equal-weighted basket over the same names returns several times
the median over this history.

So the summary now carries both. Neither is tradable as stated: the median is
one name chosen with hindsight, and the basket rebalances every session. They
are reported because the alternative was reporting neither, which is what let
"profit factor 1.29" stand on its own for years.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from backtesting.statistics import BacktestStatistics


@dataclass
class FakeTrade:
    """The same shape tests/test_backtest_span_is_not_guessed.py uses."""

    entry_date: str = "2020-01-01"
    exit_date: str = "2020-01-10"
    symbol: str = "AAA"
    result: str = "WIN"
    portfolio_profit: float = 500.0
    profit_percent: float = 5.0
    r_multiple: float = 1.5
    holding_days: int = 9
    shares: int = 100
    entry_price: float = 10.0
    executed: bool = True


def prices(**series) -> pd.DataFrame:
    index = pd.date_range("2020-01-01", periods=len(next(iter(series.values()))))
    return pd.DataFrame(series, index=index)


def summary_for(frame, trades=None):
    trades = trades or [FakeTrade(entry_date="2020-01-01", exit_date="2020-01-10")]
    return BacktestStatistics(trades, initial_capital=100_000,
                              prices=frame).summary()


# --- the field exists and is reported ----------------------------------------

def test_the_summary_carries_an_equal_weight_line():
    frame = prices(AAA=[10.0] * 5 + [20.0] * 5, BBB=[10.0] * 10)
    out = summary_for(frame)
    assert "BenchmarkEqualWeightReturn" in out
    assert "BenchmarkEqualWeightCAGR" in out
    assert "ExcessReturnVsEqualWeight" in out


def test_the_median_line_is_untouched():
    """The existing benchmark keeps its meaning; this is additive."""

    frame = prices(AAA=[10.0] * 5 + [20.0] * 5, BBB=[10.0] * 10)
    out = summary_for(frame)
    assert out["BenchmarkReturn"] is not None
    assert out["BenchmarkSymbols"] == 2


def test_equal_weight_averages_the_names_rather_than_picking_one():
    """The reason both lines are reported, in one fixture.

    One name doubles and two are flat. The median name returned nothing; the
    basket returned a third. A strategy that beat the median here has not
    beaten the market, and only the second line says so. Two names would not
    show it -- with two, the median and the mean are the same number by
    construction, which is how a weaker version of this test could pass while
    measuring nothing.
    """

    frame = prices(AAA=[10.0] * 5 + [20.0] * 5,
                   BBB=[10.0] * 10, CCC=[10.0] * 10)
    out = summary_for(frame)
    assert out["BenchmarkReturn"] == pytest.approx(0.0, abs=0.01)
    assert out["BenchmarkEqualWeightReturn"] == pytest.approx(33.3, abs=1.0)
    assert out["BenchmarkEqualWeightReturn"] > out["BenchmarkReturn"]


def test_a_flat_market_shows_as_flat_on_both_lines():
    frame = prices(AAA=[10.0] * 10, BBB=[10.0] * 10)
    out = summary_for(frame)
    assert out["BenchmarkReturn"] == pytest.approx(0.0, abs=0.01)
    assert out["BenchmarkEqualWeightReturn"] == pytest.approx(0.0, abs=0.01)


def test_the_excess_is_the_strategy_minus_the_basket():
    frame = prices(AAA=[10.0] * 5 + [20.0] * 5, BBB=[10.0] * 10)
    out = summary_for(frame)
    assert out["ExcessReturnVsEqualWeight"] == pytest.approx(
        out["TotalReturn"] - out["BenchmarkEqualWeightReturn"], abs=0.02)


# --- absent is unknown, never zero -------------------------------------------

def test_without_prices_the_equal_weight_line_is_unknown_not_flat():
    """The rule the annualised statistics already follow: an unmeasured
    benchmark is not a flat market."""

    out = BacktestStatistics([FakeTrade(entry_date="2020-01-01", exit_date="2020-01-10")],
                             initial_capital=100_000, prices=None).summary()
    assert out["BenchmarkEqualWeightReturn"] is None
    assert out["ExcessReturnVsEqualWeight"] is None


def test_a_run_with_no_trades_reports_the_field_as_unknown():
    out = BacktestStatistics([], initial_capital=100_000, prices=None).summary()
    assert "BenchmarkEqualWeightReturn" in out
    assert out["BenchmarkEqualWeightReturn"] is None
    assert out["ExcessReturnVsEqualWeight"] is None


def test_a_single_session_window_reports_unknown_rather_than_zero():
    frame = prices(AAA=[10.0], BBB=[10.0])
    out = summary_for(frame, [FakeTrade(entry_date="2020-01-01", exit_date="2020-01-01")])
    assert out["BenchmarkEqualWeightReturn"] is None


def test_a_name_that_lists_midway_does_not_poison_the_basket():
    """A NaN before listing must not be read as a fall to zero."""

    frame = prices(AAA=[10.0] * 10,
                   BBB=[float("nan")] * 5 + [10.0, 10.0, 10.0, 10.0, 10.0])
    out = summary_for(frame)
    assert out["BenchmarkEqualWeightReturn"] == pytest.approx(0.0, abs=0.01)


def test_the_basket_is_reported_for_every_run_that_has_prices():
    """Pinning the requirement that every future summary carries it."""

    frame = prices(AAA=[10.0, 11.0, 12.0, 13.0, 14.0],
                   BBB=[5.0, 5.1, 5.2, 5.3, 5.4])
    out = summary_for(frame, [FakeTrade(entry_date="2020-01-01", exit_date="2020-01-05")])
    assert out["BenchmarkEqualWeightReturn"] is not None
    assert out["BenchmarkEqualWeightCAGR"] is not None
