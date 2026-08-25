"""An unmeasurable backtest span must not become a one-day backtest.

``_total_days`` used to catch every exception and return 1. One is the most
damaging value it could have chosen, because it divides into every annualised
figure the class reports.

With 500 trades and a single malformed date, ``trades_per_year`` became
500 / (1 / 365.25) = 182,625, and the Sharpe ratio -- annualised by the square
root of that -- was inflated roughly sixtyfold. CAGR, Calmar and exposure were
annualised over a single day. Six reported statistics, every one of them
invented, none of them flagged.

The rule this enforces is the project's own, already applied to the trading
costs and the spread: an unmeasured value is not a small one, and must never
be reported as a number.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from backtesting.statistics import BacktestStatistics

#: Every summary key whose value is annualised, and therefore meaningless
#: without a span to annualise over.
ANNUALISED = ("SharpeRatio", "SortinoRatio", "CalmarRatio", "CAGR",
              "ExposurePercent", "TradesPerYear")


@dataclass
class _Trade:
    symbol: str = "COMI"
    entry_date: str = "2026-01-05"
    exit_date: str = "2026-02-02"
    result: str = "WIN"
    portfolio_profit: float = 500.0
    profit_percent: float = 5.0
    r_multiple: float = 1.5
    holding_days: int = 20
    shares: int = 100
    entry_price: float = 50.0
    executed: bool = True


def _trades(count=8, **overrides):
    return [_Trade(**overrides) for _ in range(count)]


def test_a_normal_run_reports_every_annualised_figure():
    """The guard is only worth having if the ordinary path still works."""
    summary = BacktestStatistics(_trades()).summary()

    assert summary["BacktestDays"] == 28
    for key in ANNUALISED:
        assert summary[key] is not None, key


def test_a_malformed_date_makes_the_span_unknown_not_one_day():
    summary = BacktestStatistics(_trades(exit_date="not-a-date")).summary()

    assert summary["BacktestDays"] is None


def test_every_annualised_figure_reports_unknown_rather_than_a_guess():
    """This is the whole point: not one of them may carry a number derived
    from a span nobody could measure."""
    summary = BacktestStatistics(_trades(exit_date="2026/02/02")).summary()

    for key in ANNUALISED:
        assert summary[key] is None, f"{key} was invented from an unknown span"


def test_the_numbers_that_do_not_need_a_span_still_report():
    """Failing to date the backtest is no reason to lose the win rate."""
    summary = BacktestStatistics(_trades(exit_date="")).summary()

    assert summary["Trades"] == 8
    assert summary["WinRate"] == 100.0
    assert summary["NetProfit"] == 4000.0
    assert summary["ProfitFactor"] is not None


def test_the_reason_is_recorded_rather_than_left_to_be_guessed():
    stats = BacktestStatistics(_trades(entry_date="05-01-2026"))
    stats.summary()

    assert stats.total_days_error
    assert "ValueError" in stats.total_days_error


def test_a_clean_run_leaves_no_error_behind():
    stats = BacktestStatistics(_trades())
    stats.summary()

    assert stats.total_days_error == ""


def test_the_old_behaviour_would_have_been_caught_here():
    """A regression to `return 1` would show up as a span of one day and a
    trades-per-year in the tens of thousands, so both are asserted against."""
    summary = BacktestStatistics(_trades(count=500, exit_date="bad")).summary()

    assert summary["BacktestDays"] != 1
    assert summary["TradesPerYear"] is None


@pytest.mark.parametrize("bad", ["", "2026-13-45", "yesterday", "20260202"])
def test_the_shapes_a_bad_date_actually_takes(bad):
    """Empty, out of range, prose, and the right digits in the wrong format.
    Each has to reach the same honest answer."""
    summary = BacktestStatistics(_trades(exit_date=bad)).summary()

    assert summary["BacktestDays"] is None
    assert summary["SharpeRatio"] is None


def test_a_null_date_never_reaches_the_span_at_all():
    """It fails earlier and louder, in EquityCurve, which sorts on exit_date
    and cannot order None against None.

    Recorded rather than fixed: a crash on malformed input is the honest
    direction, and it is a different problem from the silent one this file
    exists for. Should that sort ever be made tolerant, this test fails and
    the None case belongs in the parametrize list above.
    """
    with pytest.raises(TypeError):
        BacktestStatistics(_trades(exit_date=None)).summary()


def test_an_unexpected_failure_is_not_absorbed_into_a_missing_statistic():
    """The handler is narrowed to date parsing on purpose. Anything else is a
    bug, and a bug that hides inside a None is a bug nobody finds."""
    stats = BacktestStatistics(_trades())

    class _Exploding:
        def __getattr__(self, name):
            raise RuntimeError("something else entirely")

    stats.trades = [_Exploding()]

    with pytest.raises(RuntimeError):
        stats._total_days()
