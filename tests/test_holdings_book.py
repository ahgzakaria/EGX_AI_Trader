"""Average-cost accounting, checked by hand against arithmetic anyone can redo.

These are the numbers the user will act on. Every expectation here is written
as the calculation, not as a captured output, so a wrong change breaks the test
with a reason rather than with a diff.
"""

from __future__ import annotations

import pytest

from holdings.book import (
    BookError,
    FeeModel,
    breakeven_price,
    build_book,
    load_fee_model,
    value_position,
)


#: A fee schedule with round numbers, so the arithmetic below stays legible.
#: 1% per side and a 10 EGP order fee are not the user's real rates; the real
#: ones are asserted separately against the settings file.
SIMPLE = FeeModel(percent_per_side=0.01, order_fee_egp=10.0, rates_loaded=True)
FREE = FeeModel(rates_loaded=True)


def buy(symbol, date, quantity, price, **extra):
    return {"kind": "BUY", "symbol": symbol, "date": date,
            "quantity": quantity, "price": price, **extra}


def sell(symbol, date, quantity, price, **extra):
    return {"kind": "SELL", "symbol": symbol, "date": date,
            "quantity": quantity, "price": price, **extra}


# --------------------------------------------------------------------------- #
# Averaging
# --------------------------------------------------------------------------- #

def test_a_second_buy_averages_against_the_first():
    """The headline behaviour: two lots become one average, weighted by size."""

    book = build_book([
        buy("ABUK", "2026-08-03", 100, 10.0),
        buy("ABUK", "2026-08-17", 300, 12.0),
    ], FREE)
    position = book.positions["ABUK"]

    assert position.quantity == 400
    # (100*10 + 300*12) / 400 -- weighted, not the midpoint 11.0.
    assert position.average_price == pytest.approx(11.5)


def test_entry_fees_are_inside_the_average():
    """What was paid includes what the broker charged to pay it."""

    book = build_book([buy("ABUK", "2026-08-03", 100, 10.0)], SIMPLE)
    position = book.positions["ABUK"]

    # 1000 notional + 1% commission + 10 flat = 1020 for 100 shares.
    assert position.cost_basis_egp == pytest.approx(1020.0)
    assert position.average_price == pytest.approx(10.20)
    assert position.fees_paid_egp == pytest.approx(20.0)


def test_a_contract_note_total_overrides_the_modelled_fee():
    """A real charge the user copied in beats any schedule this project holds."""

    book = build_book([buy("ABUK", "2026-08-03", 100, 10.0, fees_egp=7.5)], SIMPLE)

    assert book.positions["ABUK"].cost_basis_egp == pytest.approx(1007.5)


def test_zero_recorded_fees_are_not_treated_as_missing():
    """``0`` is a stated fact; only ``None`` means "use the schedule"."""

    book = build_book([buy("ABUK", "2026-08-03", 100, 10.0, fees_egp=0)], SIMPLE)

    assert book.positions["ABUK"].cost_basis_egp == pytest.approx(1000.0)


# --------------------------------------------------------------------------- #
# Partial sales
# --------------------------------------------------------------------------- #

def test_a_partial_sale_realizes_against_the_average_and_leaves_it_unchanged():
    """Average cost: selling half does not re-price the half still held."""

    book = build_book([
        buy("ABUK", "2026-08-03", 100, 10.0),
        buy("ABUK", "2026-08-17", 100, 12.0),
        sell("ABUK", "2026-08-24", 100, 13.0),
    ], FREE)
    position = book.positions["ABUK"]

    assert position.quantity == 100
    assert position.average_price == pytest.approx(11.0)
    # 100 shares sold at 13.00 against an 11.00 average.
    assert position.realized_pnl_egp == pytest.approx(200.0)
    assert book.realized_pnl_egp == pytest.approx(200.0)


def test_closing_a_position_leaves_no_basis_behind():
    """A position with no shares must not carry a residual cost basis."""

    book = build_book([
        buy("ABUK", "2026-08-03", 100, 10.0),
        sell("ABUK", "2026-08-24", 100, 11.0),
    ], SIMPLE)
    position = book.positions["ABUK"]

    assert not position.open
    assert position.quantity == 0.0
    assert position.cost_basis_egp == 0.0
    assert position.average_price is None
    # 1100 gross - 21 exit fees - 1020 basis.
    assert position.realized_pnl_egp == pytest.approx(59.0)


def test_selling_more_than_is_held_is_refused_with_both_numbers():
    """Refused, not clamped: a clamped sale reports a confident wrong average."""

    with pytest.raises(BookError) as error:
        build_book([
            buy("ABUK", "2026-08-03", 100, 10.0),
            sell("ABUK", "2026-08-24", 150, 11.0),
        ], FREE)

    assert "150" in str(error.value) and "100" in str(error.value)


def test_a_same_day_buy_is_averaged_before_a_same_day_sale():
    """Same date, no time: insertion order decides, and it must be stable."""

    book = build_book([
        buy("ABUK", "2026-08-03", 100, 10.0, id=1),
        buy("ABUK", "2026-08-24", 100, 12.0, id=2),
        sell("ABUK", "2026-08-24", 100, 13.0, id=3),
    ], FREE)

    # Realized against the 11.00 average, not against the 10.00 first lot.
    assert book.positions["ABUK"].realized_pnl_egp == pytest.approx(200.0)


# --------------------------------------------------------------------------- #
# Corporate actions
# --------------------------------------------------------------------------- #

def test_a_bonus_issue_spreads_the_same_money_over_more_shares():
    """The trap this exists to close: the average must fall, the basis must not.

    Without this, a 10% bonus leaves the user comparing 9.09 against a stored
    10.00 and believing they are 9% down when nothing was lost.
    """

    book = build_book([
        buy("ABUK", "2026-08-03", 100, 10.0),
        {"kind": "STOCK_DIVIDEND", "symbol": "ABUK",
         "date": "2026-08-20", "factor": 1.1},
    ], FREE)
    position = book.positions["ABUK"]

    assert position.quantity == pytest.approx(110.0)
    assert position.cost_basis_egp == pytest.approx(1000.0)
    assert position.average_price == pytest.approx(9.0909, abs=1e-4)


def test_a_split_halves_the_average():
    book = build_book([
        buy("ABUK", "2026-08-03", 100, 10.0),
        {"kind": "SPLIT", "symbol": "ABUK", "date": "2026-08-20", "factor": 2.0},
    ], FREE)

    assert book.positions["ABUK"].average_price == pytest.approx(5.0)


def test_a_cash_dividend_is_income_and_does_not_touch_the_average():
    """Cash received is cash, not a reduction of what the shares cost."""

    book = build_book([
        buy("ABUK", "2026-08-03", 100, 10.0),
        {"kind": "CASH_DIVIDEND", "symbol": "ABUK",
         "date": "2026-08-20", "amount_per_share": 0.5},
    ], FREE)
    position = book.positions["ABUK"]

    assert position.average_price == pytest.approx(10.0)
    assert position.dividend_income_egp == pytest.approx(50.0)
    assert book.dividend_income_egp == pytest.approx(50.0)


def test_a_corporate_action_on_nothing_is_refused():
    with pytest.raises(BookError):
        build_book([
            {"kind": "SPLIT", "symbol": "ABUK", "date": "2026-08-20", "factor": 2.0},
        ], FREE)


# --------------------------------------------------------------------------- #
# Cash
# --------------------------------------------------------------------------- #

def test_cash_follows_every_movement_and_every_trade():
    book = build_book([
        {"kind": "DEPOSIT", "date": "2026-08-01", "amount_egp": 100_000.0},
        buy("ABUK", "2026-08-03", 1000, 10.0),
        sell("ABUK", "2026-08-24", 500, 12.0),
        {"kind": "WITHDRAW", "date": "2026-08-25", "amount_egp": 5_000.0},
    ], FREE)

    # 100000 - 10000 + 6000 - 5000
    assert book.cash_egp == pytest.approx(91_000.0)
    assert book.deposits_egp == pytest.approx(100_000.0)
    assert book.withdrawals_egp == pytest.approx(5_000.0)


# --------------------------------------------------------------------------- #
# Breakeven and valuation
# --------------------------------------------------------------------------- #

def test_breakeven_sits_above_the_average_by_the_cost_of_leaving():
    """The number the user asked for without asking for it.

    "I am up 0.5%" at a price under this is a loss, and nothing on a broker
    screen says so.
    """

    book = build_book([buy("ABUK", "2026-08-03", 100, 10.0)], SIMPLE)
    position = book.positions["ABUK"]

    # basis 1020; selling q shares at P nets q*P*0.99 - 10, so
    # P = (1020 + 10) / (100 * 0.99) = 10.4040...
    assert breakeven_price(position, SIMPLE) == pytest.approx(10.4040, abs=1e-4)
    assert breakeven_price(position, SIMPLE) > position.average_price


def test_breakeven_with_no_fees_is_the_average():
    book = build_book([buy("ABUK", "2026-08-03", 100, 10.0)], FREE)

    assert breakeven_price(book.positions["ABUK"], FREE) == pytest.approx(10.0)


def test_a_configured_gains_tax_raises_the_breakeven():
    """Egypt's rate is set to zero in settings; the arithmetic still has to work
    if that ever changes, and it must change in one place only."""

    taxed = FeeModel(percent_per_side=0.01, order_fee_egp=10.0,
                     tax_percent=10.0, rates_loaded=True)
    book = build_book([buy("ABUK", "2026-08-03", 100, 10.0)], taxed)
    position = book.positions["ABUK"]

    plain = breakeven_price(position, SIMPLE)
    assert breakeven_price(position, taxed) > plain


def test_valuation_charges_the_exit_before_reporting_a_profit():
    book = build_book([buy("ABUK", "2026-08-03", 100, 10.0)], SIMPLE)
    value = value_position(book.positions["ABUK"], 11.0, SIMPLE)

    assert value.market_value_egp == pytest.approx(1100.0)
    # 1100 - 11 commission - 10 order fee.
    assert value.net_proceeds_egp == pytest.approx(1079.0)
    assert value.unrealized_gross_egp == pytest.approx(80.0)
    assert value.unrealized_net_egp == pytest.approx(59.0)
    assert value.above_breakeven


def test_a_price_just_over_the_average_is_reported_as_a_net_loss():
    """The whole reason breakeven is on screen at all."""

    book = build_book([buy("ABUK", "2026-08-03", 100, 10.0)], SIMPLE)
    value = value_position(book.positions["ABUK"], 10.25, SIMPLE)

    assert value.price > value.average_price
    assert value.unrealized_net_egp < 0
    assert not value.above_breakeven


def test_a_missing_price_is_not_valued_as_flat():
    """No price means unknown. Valuing at cost would render as "no change"."""

    book = build_book([buy("ABUK", "2026-08-03", 100, 10.0)], SIMPLE)

    assert value_position(book.positions["ABUK"], None, SIMPLE) is None
    assert value_position(book.positions["ABUK"], 0, SIMPLE) is None


# --------------------------------------------------------------------------- #
# The real fee schedule
# --------------------------------------------------------------------------- #

def test_the_live_fee_model_comes_from_the_broker_contract_note():
    """One source of truth: the same rates the rest of the project charges.

    A portfolio that priced its own exits would eventually disagree with a
    signal card about what the same trade costs.
    """

    model = load_fee_model()

    assert model.rates_loaded, model.load_error
    # 0.1819% per side, summed from the contract-note lines in settings.
    assert model.percent_per_side == pytest.approx(0.001819, abs=1e-6)
    assert model.order_fee_egp == pytest.approx(4.0)
    assert model.fee_lines, "the per-line schedule must stay checkable by hand"


def test_a_zero_tax_rate_is_a_decision_and_reads_as_zero():
    """Egypt charges no capital-gains tax on these trades, and the settings say
    so explicitly rather than leaving it unset, which would mean "unknown"."""

    model = load_fee_model()

    assert model.tax_percent == 0
    assert model.tax_rate == 0.0
