"""A drawdown has to be able to fall while a position is open and losing.

`EquityCurve` booked profit only at exit and never valued an open position, so
its curve stepped once per trade in exit-date order. A portfolio that is down 30%
on paper across fifteen open positions produced a perfectly flat line.

That is not a small inaccuracy in a corner. It understates by more the more
positions are held at once, so it was worst exactly where it was being used to
decide how many to hold, and it was found while sweeping that variable
(`docs/audits/strategies/CAPACITY_IS_THE_CONSTRAINT.md`).

These tests pin three things: that an open loss now shows, that the old number
is still available and unchanged, and that nothing silently picks one over the
other.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtesting.equity import CLOSED_TRADE, MARKED, EquityCurve
from backtesting.prices import daily_closes
from backtesting.statistics import BacktestStatistics


class FakeTrade:
    """The fields `EquityCurve` and `BacktestStatistics` actually read."""

    def __init__(self, symbol, entry_date, exit_date, entry_price, exit_price,
                 shares, stop_loss=0.0):
        self.symbol = symbol
        self.entry_date = entry_date
        self.exit_date = exit_date
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.shares = shares
        self.stop_loss = stop_loss
        self.executed = True
        self.profit = exit_price - entry_price
        self.portfolio_profit = round(shares * (exit_price - entry_price), 2)
        self.result = "WIN" if exit_price > entry_price else "LOSS"
        self.profit_percent = (exit_price / entry_price - 1) * 100
        self.r_multiple = 0.0
        self.holding_days = 1
        self.exit_reason = "Test"


def price_frame(rows):
    """rows: {date: {symbol: close}}."""
    frame = pd.DataFrame(rows).T
    frame.index = pd.to_datetime(frame.index)
    return frame.sort_index()


def test_an_open_loss_shows_in_the_drawdown():
    """One trade that halves and then fully recovers before it closes.

    Closed-trade: it exits flat, so the curve never moves and the drawdown is
    zero. Marked: the account was down 50% of the position in between.
    """
    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-05",
                      entry_price=10.0, exit_price=10.0, shares=1_000)
    prices = price_frame({
        "2024-01-01": {"AAA.CA": 10.0},
        "2024-01-02": {"AAA.CA": 7.0},
        "2024-01-03": {"AAA.CA": 5.0},
        "2024-01-04": {"AAA.CA": 8.0},
        "2024-01-05": {"AAA.CA": 10.0},
    })

    blind = EquityCurve([trade], initial_capital=100_000)
    marked = EquityCurve([trade], initial_capital=100_000, prices=prices)

    assert blind.max_drawdown() == 0.0
    # 1,000 shares from 10.00 down to 5.00 is 5,000 of a 100,000 account.
    assert marked.max_drawdown() == pytest.approx(5.0, abs=0.01)
    assert marked.max_drawdown_amount() == pytest.approx(5_000, abs=1)


def test_the_old_number_is_still_available_and_unchanged():
    """Every figure this project has published is the closed-trade one.

    It stays computable from the same object, so an old run remains
    reproducible rather than becoming unexplainable.
    """
    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-05",
                      entry_price=10.0, exit_price=10.0, shares=1_000)
    prices = price_frame({
        "2024-01-01": {"AAA.CA": 10.0},
        "2024-01-03": {"AAA.CA": 5.0},
        "2024-01-05": {"AAA.CA": 10.0},
    })
    marked = EquityCurve([trade], initial_capital=100_000, prices=prices)

    assert marked.closed_trade_max_drawdown() == 0.0
    assert marked.max_drawdown() > 0.0


def test_without_prices_the_behaviour_is_exactly_what_it_was():
    losing = FakeTrade("AAA.CA", "2024-01-01", "2024-01-05",
                       entry_price=10.0, exit_price=8.0, shares=1_000)
    winning = FakeTrade("BBB.CA", "2024-01-06", "2024-01-10",
                        entry_price=10.0, exit_price=13.0, shares=1_000)
    curve = EquityCurve([losing, winning], initial_capital=100_000)

    assert curve.curve() == [100_000, 98_000.0, 101_000.0]
    assert curve.max_drawdown() == pytest.approx(2.0)
    assert curve.basis() == CLOSED_TRADE


def test_the_summary_always_says_which_curve_the_number_came_from():
    """A metric that silently changes meaning between runs is worse than the bug.

    `require_market_analyzer` is the cautionary case: a gate switched on, unable
    to fire, reporting PASS for years.
    """
    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-03",
                      entry_price=10.0, exit_price=10.0, shares=1_000)
    prices = price_frame({
        "2024-01-01": {"AAA.CA": 10.0},
        "2024-01-02": {"AAA.CA": 6.0},
        "2024-01-03": {"AAA.CA": 10.0},
    })

    blind = BacktestStatistics([trade], initial_capital=100_000).summary()
    marked = BacktestStatistics(
        [trade], initial_capital=100_000, prices=prices).summary()

    assert blind["DrawdownBasis"] == CLOSED_TRADE
    assert marked["DrawdownBasis"] == MARKED
    assert marked["MaxDrawdown"] > marked["MaxDrawdownClosedTrades"]
    assert blind["MaxDrawdown"] == blind["MaxDrawdownClosedTrades"]


def test_calmar_and_recovery_use_the_same_drawdown_that_is_reported():
    """Otherwise the ratio and its denominator describe different things."""
    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-03",
                      entry_price=10.0, exit_price=12.0, shares=1_000)
    prices = price_frame({
        "2024-01-01": {"AAA.CA": 10.0},
        "2024-01-02": {"AAA.CA": 6.0},
        "2024-01-03": {"AAA.CA": 12.0},
    })
    summary = BacktestStatistics(
        [trade], initial_capital=100_000, prices=prices).summary()

    assert summary["RecoveryFactor"] == pytest.approx(
        round(summary["NetProfit"] / summary["MaxDrawdownAmount"], 2))
    if summary["CalmarRatio"]:
        assert summary["CalmarRatio"] == pytest.approx(
            round(summary["CAGR"] / summary["MaxDrawdown"], 2))


def test_a_symbol_with_no_price_is_carried_at_cost_not_at_a_stale_one():
    """A suspended name is exactly when marking at the last print misleads."""
    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-03",
                      entry_price=10.0, exit_price=10.0, shares=1_000)
    # AAA.CA has no column at all; BBB.CA is there so the frame is non-empty.
    prices = price_frame({
        "2024-01-01": {"BBB.CA": 1.0},
        "2024-01-02": {"BBB.CA": 1.0},
        "2024-01-03": {"BBB.CA": 1.0},
    })
    curve = EquityCurve([trade], initial_capital=100_000, prices=prices)

    assert curve.basis() == MARKED
    # Carried at cost throughout, so the account never moves.
    assert curve.max_drawdown() == 0.0


def test_a_gap_in_one_symbol_does_not_move_the_account():
    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-03",
                      entry_price=10.0, exit_price=10.0, shares=1_000)
    prices = price_frame({
        "2024-01-01": {"AAA.CA": 10.0},
        "2024-01-02": {"AAA.CA": float("nan")},
        "2024-01-03": {"AAA.CA": 10.0},
    })
    curve = EquityCurve([trade], initial_capital=100_000, prices=prices)

    assert curve.max_drawdown() == 0.0


def test_unexecuted_trades_are_not_marked():
    """A trade the portfolio refused holds nothing, so it values nothing."""
    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-03",
                      entry_price=10.0, exit_price=10.0, shares=0)
    trade.portfolio_profit = 0.0
    prices = price_frame({
        "2024-01-01": {"AAA.CA": 10.0},
        "2024-01-02": {"AAA.CA": 1.0},
        "2024-01-03": {"AAA.CA": 10.0},
    })
    curve = EquityCurve([trade], initial_capital=100_000, prices=prices)

    assert curve.basis() == CLOSED_TRADE
    assert curve.max_drawdown() == 0.0


def test_two_positions_falling_together_deepen_the_drawdown():
    """The whole reason this matters: concurrency is what the old curve hid."""
    one = FakeTrade("AAA.CA", "2024-01-01", "2024-01-03", 10.0, 10.0, 1_000)
    two = FakeTrade("BBB.CA", "2024-01-01", "2024-01-03", 10.0, 10.0, 1_000)
    prices = price_frame({
        "2024-01-01": {"AAA.CA": 10.0, "BBB.CA": 10.0},
        "2024-01-02": {"AAA.CA": 8.0, "BBB.CA": 8.0},
        "2024-01-03": {"AAA.CA": 10.0, "BBB.CA": 10.0},
    })

    alone = EquityCurve([one], initial_capital=100_000, prices=prices)
    together = EquityCurve([one, two], initial_capital=100_000, prices=prices)

    assert alone.max_drawdown() == pytest.approx(2.0, abs=0.01)
    assert together.max_drawdown() == pytest.approx(4.0, abs=0.01)


def test_final_capital_stays_realised_so_returns_cannot_drift():
    """`TotalReturn` and `CAGR` hang off this; a price frame must not move it."""
    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-03", 10.0, 12.0, 1_000)
    prices = price_frame({
        "2024-01-01": {"AAA.CA": 10.0},
        "2024-01-02": {"AAA.CA": 99.0},
        "2024-01-03": {"AAA.CA": 12.0},
    })

    blind = EquityCurve([trade], initial_capital=100_000)
    marked = EquityCurve([trade], initial_capital=100_000, prices=prices)

    assert marked.final_capital() == blind.final_capital() == 102_000.0
    assert marked.total_return() == blind.total_return() == 2.0


def test_the_price_helper_skips_what_it_cannot_load_rather_than_failing():
    """One unreadable symbol must not cost the whole run its drawdown."""
    def loader(symbol):
        if symbol == "BAD.CA":
            raise RuntimeError("no data")
        return pd.DataFrame(
            {"Close": [1.0, 2.0]},
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]))

    frame = daily_closes(["GOOD.CA", "BAD.CA"], loader=loader)

    assert list(frame.columns) == ["GOOD.CA"]
    assert len(frame) == 2


def test_the_saved_equity_curve_gains_a_date_and_keeps_its_column_name():
    """`dashboard/settings.py` charts `Equity`; that contract does not change."""
    from backtesting.report import BacktestReport

    trade = FakeTrade("AAA.CA", "2024-01-01", "2024-01-03", 10.0, 12.0, 1_000)
    prices = price_frame({
        "2024-01-01": {"AAA.CA": 10.0},
        "2024-01-02": {"AAA.CA": 11.0},
        "2024-01-03": {"AAA.CA": 12.0},
    })

    curve = BacktestReport([trade], prices=prices).equity_curve()

    assert "Equity" in curve
    assert "Date" in curve
    assert len(curve) == 3

    without = BacktestReport([trade]).equity_curve()
    assert "Equity" in without
    assert "Date" not in without
