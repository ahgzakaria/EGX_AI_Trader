"""The backtest's round trip, pinned to the broker's contract note.

Until 2026-08-26 the classic backtest charged 0.30% commission per side and
nothing for the spread. The contract note in `config/settings.json ->
scalping.fee_schedule` totals 0.1819% per side, and the trade-weighted median
spread across the 170 of 172 traded symbols with live quotes is 0.500%. The two
errors nearly cancelled -- 0.703% modelled against 0.864% measured -- so nothing
looked broken, while the residual was about a third of the strategy's reported
per-trade edge.

The breakout package had already made this correction; see
`test_breakout_weights_are_measured.py`. These tests stop the classic path
drifting back.
"""

import json
from pathlib import Path

import pytest

from backtesting.costs import TradingCosts

ROOT = Path(__file__).resolve().parents[1]


def contract_note_per_side():
    schedule = json.loads(
        (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
    )["scalping"]["fee_schedule"]
    return sum(
        value for key, value in schedule.items()
        if key.endswith("_percent") and isinstance(value, (int, float))
    ) / 100.0


def test_commission_matches_the_contract_note_not_a_placeholder():
    # 0.003 per side was a placeholder, 65% above what the broker charges.
    assert TradingCosts().commission == pytest.approx(contract_note_per_side())
    assert TradingCosts().commission == pytest.approx(0.001819)


def test_the_spread_is_charged_at_all():
    # The defect: it used to be free.
    assert TradingCosts().half_spread > 0


def test_the_spread_costs_one_full_width_per_round_trip_not_two():
    # You buy at the ask and sell at the bid, so the round trip crosses once.
    costs = TradingCosts(commission=0, slippage=0, spread_percent=0.5)
    entry = costs.entry_price(100.0)
    exit_price = costs.exit_price(100.0)
    assert entry - exit_price == pytest.approx(0.5, abs=1e-6)
    assert costs.round_trip_percent() == pytest.approx(0.5)


def test_round_trip_is_the_sum_of_its_named_parts():
    costs = TradingCosts(commission=0.001819, slippage=0.0005, spread_percent=0.5)
    expected = 100.0 * (2 * 0.001819 + 2 * 0.0005) + 0.5
    assert costs.round_trip_percent() == pytest.approx(expected)


def test_the_configured_round_trip_is_not_below_what_was_measured():
    # The quoted cost measured from live data is 0.864%: 0.3638% broker plus a
    # 0.500% spread. Slippage beyond the quote is unmeasured, so the configured
    # total should land at or above the measurement, never under it.
    assert TradingCosts().round_trip_percent() >= 0.864


def test_zero_costs_must_be_asked_for_explicitly():
    # Passing commission=0 and slippage=0 used to mean "no costs". It no longer
    # does, and a test that assumes it will get a spread it did not expect.
    assert TradingCosts(commission=0, slippage=0).half_spread > 0
    assert TradingCosts(
        commission=0, slippage=0, spread_percent=0
    ).round_trip_percent() == 0


def test_entry_pays_up_and_exit_pays_down():
    costs = TradingCosts()
    assert costs.entry_price(100.0) > 100.0
    assert costs.exit_price(100.0) < 100.0
