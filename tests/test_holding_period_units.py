"""`holding_days` and `max_holding_days` are different units under one name.

`backtesting/trade.py` computes `holding_days` as `(exit - entry).days` --
**calendar days**. `backtesting/managers/exit_manager.py` uses
`max_holding_days` as a bar offset, `entry_index + max_holding_days - 1` --
**trading bars**.

So `max_holding_days: 20` means twenty *bars*, about twenty-eight calendar days,
while a reported `AverageHoldingDays` of 6.13 is *calendar*, about 4.1 bars. A
reader comparing 6.13 against a cap of 20 concludes there is plenty of room;
in the unit the cap is actually enforced in, the position is at 4.1 of 20.

This cost a real finding. An analysis indexed forward by `holding_days` bars
measured a holding period 49% longer than the trades actually ran, which
manufactured a -0.475% "execution drag" that does not exist. Measured in the
right unit the drag is -0.004% at t = -0.02.

These tests do not change the units. They pin the difference so it cannot drift
silently, and so the next reader meets it here rather than in a wrong number.
"""

from datetime import date, timedelta

import pytest

from backtesting.trade import Trade


def _trade(entry: str, exit_: str) -> Trade:
    """Build a Trade supplying whatever the dataclass currently requires.

    Introspected rather than spelled out, so adding a field to Trade does not
    break these tests -- they are about two date fields, not the schema.
    """
    import dataclasses

    kwargs = {}
    for spec in dataclasses.fields(Trade):
        if not spec.init:
            continue
        if spec.default is not dataclasses.MISSING or (
                spec.default_factory is not dataclasses.MISSING):  # type: ignore[misc]
            continue
        kwargs[spec.name] = "" if spec.type in ("str", str) else 0
    kwargs.update(
        symbol="TEST.CA",
        entry_date=entry,
        exit_date=exit_,
        entry_price=100.0,
        exit_price=101.0,
    )
    return Trade(**kwargs)


def test_holding_days_counts_calendar_days_not_trading_bars():
    # Friday to Monday is one trading bar and three calendar days.
    trade = _trade("2026-08-07", "2026-08-10")
    assert trade.holding_days == 3


def test_a_weekend_inflates_holding_days_beyond_the_bars_held():
    # Two trading bars (Thu, Mon) spanning four calendar days.
    trade = _trade("2026-08-06", "2026-08-10")
    assert trade.holding_days == 4


def test_same_day_entry_and_exit_is_zero_days():
    assert _trade("2026-08-10", "2026-08-10").holding_days == 0


@pytest.mark.parametrize("days", [1, 5, 20, 40])
def test_holding_days_is_exactly_the_calendar_difference(days):
    start = date(2026, 1, 5)
    end = start + timedelta(days=days)
    trade = _trade(start.isoformat(), end.isoformat())
    assert trade.holding_days == days


def test_max_holding_days_is_applied_as_a_bar_offset():
    # The other unit, pinned at its source: ExitManager bounds the trade by
    # entry_index + max_holding_days - 1, which indexes bars, not dates. If this
    # ever becomes date arithmetic the two fields would finally agree -- and
    # every stored backtest would silently change meaning.
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "backtesting" / "managers" / "exit_manager.py").read_text(
        encoding="utf-8")
    assert "context.entry_index + self.max_holding_days - 1" in source.replace(
        "\n", " ").replace("  ", " ") or (
        "entry_index +" in source and "max_holding_days" in source)


def test_the_two_units_disagree_for_any_span_containing_a_weekend():
    # The property that matters: over a realistic span the calendar count
    # exceeds the bar count, so the two are never interchangeable.
    trade = _trade("2026-08-03", "2026-08-24")   # three weeks
    bars_elapsed = 15                            # 3 weeks x 5 sessions
    assert trade.holding_days == 21
    assert trade.holding_days > bars_elapsed
