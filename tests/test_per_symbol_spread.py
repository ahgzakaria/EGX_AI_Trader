"""Per-symbol spread in the cost model, pinned.

The model charged every symbol 0.500%, the trade-weighted median of the traded
universe. Measured spreads run from 0.036% on COMI to 4.416% on EOSB, so the
flat rate was simultaneously too harsh on liquid names and too generous on
illiquid ones — and no universe filter can be evaluated while every symbol costs
the same to trade.

These tests pin the resolution order and, more importantly, the fallbacks. A
cost model that silently invents a number is worse than one that is merely
imprecise, so an unknown symbol must fall back visibly and an explicit request
must always win.
"""

import pytest

from backtesting.costs import (
    SPREAD_TABLE_PATH,
    TradingCosts,
    reset_spread_table,
    spread_table,
)


@pytest.fixture(autouse=True)
def _fresh_table():
    reset_spread_table()
    yield
    reset_spread_table()


def test_the_table_exists_and_is_not_empty():
    assert SPREAD_TABLE_PATH.exists()
    assert len(spread_table()) > 100


def test_a_measured_symbol_is_charged_its_own_spread():
    costs = TradingCosts(symbol="COMI.CA")
    assert costs.spread_source == "measured"
    assert costs.spread_percent < 0.2       # COMI is among the tightest
    assert costs.round_trip_percent() < 0.6


def test_a_wide_symbol_is_charged_more_than_the_flat_rate():
    wide = TradingCosts(symbol="EOSB.CA")
    flat = TradingCosts(symbol=None)
    assert wide.spread_source == "measured"
    assert wide.round_trip_percent() > flat.round_trip_percent()


def test_an_unknown_symbol_falls_back_visibly_rather_than_guessing():
    costs = TradingCosts(symbol="NOTAREALTICKER.CA")
    assert costs.spread_source == "flat_default"
    assert costs.spread_percent == pytest.approx(0.5)


def test_no_symbol_at_all_falls_back_the_same_way():
    assert TradingCosts(symbol=None).spread_source == "flat_default"


def test_an_explicit_spread_always_wins_over_a_measured_one():
    # Otherwise a test asking for zero costs would silently get COMI's spread.
    costs = TradingCosts(commission=0, slippage=0, spread_percent=0,
                         symbol="COMI.CA")
    assert costs.spread_source == "explicit"
    assert costs.round_trip_percent() == 0


def test_the_suffix_is_optional_when_matching():
    # Results carry "COMI.CA"; the quote feed carries "COMI". Both must resolve.
    assert TradingCosts(symbol="COMI.CA").spread_percent == pytest.approx(
        TradingCosts(symbol="COMI").spread_percent)


def test_lookup_is_case_insensitive():
    assert TradingCosts(symbol="comi.ca").spread_source == "measured"


def test_a_missing_table_degrades_to_the_flat_rate(monkeypatch, tmp_path):
    # A run without the table should be less precise, not wrong, and must not
    # raise -- the table is research output, not a deployment dependency.
    import backtesting.costs as module

    monkeypatch.setattr(module, "SPREAD_TABLE_PATH", tmp_path / "absent.csv")
    reset_spread_table()
    costs = TradingCosts(symbol="COMI.CA")
    assert costs.spread_source == "flat_default"
    assert costs.round_trip_percent() > 0


def test_comment_lines_in_the_table_are_not_parsed_as_data():
    # The file leads with provenance comments; a parser that read them as rows
    # would produce a symbol named "#" with an unparseable spread.
    table = spread_table()
    assert not any(ticker.startswith("#") for ticker in table)
    assert all(value >= 0 for value in table.values())
