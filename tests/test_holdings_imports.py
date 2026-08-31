"""Bulk entry: what gets written, what gets refused, and what never doubles.

Two properties carry most of the weight here. An invoice imported twice must
change nothing, because the user will upload the day's file more than once. And
a sale of shares whose purchase was never recorded must be caught in the
preview with an instruction, not at write time with a database error.
"""

from __future__ import annotations

import pandas as pd
import pytest

from holdings.book import FeeModel
from holdings.imports import (
    CASH,
    CONFLICT,
    DUPLICATE,
    INVALID,
    NEW,
    NOT_EGX,
    OPENING,
    TRADE,
    TRANSACTIONS,
    UNRESOLVED,
    apply_import,
    detect_shape,
    plan_file_import,
    plan_invoice_import,
    validate_rows,
)
from holdings.invoices import parse_invoice_text
from holdings.store import HoldingsStore
from tests.test_holdings_invoices import BUY_INVOICE, FUND_INVOICE, SELL_INVOICE


FREE = FeeModel(rates_loaded=True)


@pytest.fixture
def store(tmp_path):
    return HoldingsStore(tmp_path / "portfolio.db")


def invoices(*texts):
    return [parse_invoice_text(text, page=index, source_file="day.pdf")
            for index, text in enumerate(texts, start=1)]


# --------------------------------------------------------------------------- #
# Invoices into the book
# --------------------------------------------------------------------------- #

def test_a_purchase_invoice_becomes_a_position_with_the_invoiced_fees(store):
    rows = plan_invoice_import(invoices(BUY_INVOICE), store)
    result = apply_import(store, rows, fee_model=FREE)
    position = store.book(FREE).positions["BONY"]

    assert result.written == 1 and result.ok
    assert position.quantity == pytest.approx(21_000)
    # 100,170.00 paid plus the 182.32 the invoice actually charged.
    assert position.cost_basis_egp == pytest.approx(100_352.32)
    assert position.average_price == pytest.approx(100_352.32 / 21_000)


def test_the_modelled_fee_schedule_is_not_charged_over_an_invoiced_one(store):
    """The invoice states what was paid. A schedule applied on top of it would
    bill the same commission twice and raise every breakeven on the page."""

    charged = FeeModel(percent_per_side=0.01, order_fee_egp=10.0, rates_loaded=True)
    apply_import(store, plan_invoice_import(invoices(BUY_INVOICE), store),
                 fee_model=charged)

    assert store.book(charged).positions["BONY"].cost_basis_egp == \
        pytest.approx(100_352.32)


def test_importing_the_same_invoice_twice_changes_nothing(store):
    apply_import(store, plan_invoice_import(invoices(BUY_INVOICE), store),
                 fee_model=FREE)

    second = plan_invoice_import(invoices(BUY_INVOICE), store)
    result = apply_import(store, second, fee_model=FREE)

    assert [row.status for row in second] == [DUPLICATE]
    assert result.written == 0
    assert store.book(FREE).positions["BONY"].quantity == pytest.approx(21_000)


def test_the_database_refuses_a_duplicate_even_if_the_preview_is_bypassed(store):
    """The dedupe is an index, not a check that a later caller might skip."""

    row = plan_invoice_import(invoices(BUY_INVOICE), store)[0]
    apply_import(store, [row], fee_model=FREE)
    result = apply_import(store, [row], fee_model=FREE)

    assert result.written == 0
    assert result.failures


def test_a_money_market_fund_becomes_cash_not_a_position(store):
    rows = plan_invoice_import(invoices(FUND_INVOICE), store)
    apply_import(store, rows, fee_model=FREE)
    book = store.book(FREE)

    assert rows[0].kind == CASH
    assert rows[0].side == "DEPOSIT"
    assert book.cash_egp == pytest.approx(100_245.21)
    assert not book.positions


def test_a_fund_can_be_left_out_entirely(store):
    rows = plan_invoice_import(invoices(FUND_INVOICE), store, funds_as_cash=False)

    assert rows[0].status == NOT_EGX
    assert not rows[0].importable


def test_an_unknown_isin_waits_for_a_symbol_instead_of_guessing(store):
    unknown = BUY_INVOICE.replace("EGS656M1C010", "EGS999X9C999")

    rows = plan_invoice_import(invoices(unknown), store)

    assert rows[0].status == UNRESOLVED
    assert "EGS999X9C999" in rows[0].detail


def test_a_manually_chosen_symbol_resolves_that_invoice(store):
    unknown = BUY_INVOICE.replace("EGS656M1C010", "EGS999X9C999")
    parsed = invoices(unknown)

    rows = plan_invoice_import(
        parsed, store, manual_symbols={parsed[0].reference: "BONY.CA"})

    assert rows[0].status == NEW
    assert rows[0].symbol == "BONY"


def test_an_unreadable_page_is_reported_and_skipped(store):
    rows = plan_invoice_import(invoices("not an invoice at all"), store)

    assert rows[0].status == INVALID
    assert not rows[0].importable


# --------------------------------------------------------------------------- #
# Selling something that was never bought
# --------------------------------------------------------------------------- #

def test_a_sale_with_no_recorded_position_is_caught_in_the_preview(store):
    """The first real-world failure: today's sale invoice refers to a holding
    bought long before the invoices being imported."""

    rows = validate_rows(plan_invoice_import(invoices(SELL_INVOICE), store),
                         store, fee_model=FREE)

    assert rows[0].status == CONFLICT
    assert "المركز الافتتاحي" in rows[0].detail
    assert not rows[0].importable


def test_the_same_sale_imports_once_the_opening_position_exists(store):
    store.record_trade("CIEB", "2026-01-05", "BUY", 4000, 22.0, fees_egp=0,
                       fee_model=FREE)

    rows = validate_rows(plan_invoice_import(invoices(SELL_INVOICE), store),
                         store, fee_model=FREE)
    result = apply_import(store, rows, fee_model=FREE)

    assert rows[0].status == NEW
    assert result.written == 1
    assert store.book(FREE).positions["CIEB"].quantity == pytest.approx(400)


def test_a_buy_and_a_sell_in_one_file_are_written_oldest_first(store):
    """Order in the file must not decide whether the import succeeds."""

    buy = BUY_INVOICE.replace("30/08/2026", "01/08/2026")
    sell = (BUY_INVOICE.replace("Buy", "Sell")
            .replace("N000260124805", "N000260124999")
            .replace("Total Fees 182.32 EGP\nGrand Total 100,352.32 EGP",
                     "Total Fees 182.32 EGP\nGrand Total 99,987.68 EGP"))

    rows = validate_rows(plan_invoice_import(invoices(sell, buy), store),
                         store, fee_model=FREE)
    result = apply_import(store, rows, fee_model=FREE)

    assert result.written == 2 and result.ok
    assert "BONY" not in store.book(FREE).positions or \
        store.book(FREE).positions["BONY"].quantity == pytest.approx(0)


# --------------------------------------------------------------------------- #
# The spreadsheet
# --------------------------------------------------------------------------- #

def test_the_two_file_shapes_are_told_apart_by_their_columns():
    assert detect_shape(["Date", "Symbol", "Side", "Quantity", "Price"]) == TRANSACTIONS
    assert detect_shape(["Symbol", "Quantity", "AveragePrice", "Date"]) == OPENING
    assert detect_shape(["Something", "Else"]) == ""


def test_an_opening_position_is_recorded_at_the_stated_average_with_no_extra_fee(store):
    """The answer to "I bought this over several trades and cannot separate the
    commission": the broker's average already contains it."""

    charged = FeeModel(percent_per_side=0.01, order_fee_egp=10.0, rates_loaded=True)
    frame = pd.DataFrame([
        {"Symbol": "ABUK", "Quantity": 1500, "AveragePrice": 76.70,
         "Date": "2026-08-17"},
    ])

    rows = plan_file_import(frame)
    apply_import(store, rows, fee_model=charged)
    position = store.book(charged).positions["ABUK"]

    assert rows[0].fees_egp == 0.0
    assert position.average_price == pytest.approx(76.70)
    assert position.cost_basis_egp == pytest.approx(1500 * 76.70)


def test_transaction_rows_use_the_stated_fee_and_model_a_blank_one(store):
    charged = FeeModel(percent_per_side=0.01, order_fee_egp=10.0, rates_loaded=True)
    frame = pd.DataFrame([
        {"Date": "2026-08-03", "Symbol": "ABUK", "Side": "BUY", "Quantity": 100,
         "Price": 10.0, "Fees": 7.5},
        {"Date": "2026-08-04", "Symbol": "HRHO", "Side": "شراء", "Quantity": 100,
         "Price": 10.0, "Fees": None},
    ])

    rows = plan_file_import(frame)
    apply_import(store, rows, fee_model=charged)
    book = store.book(charged)

    assert book.positions["ABUK"].cost_basis_egp == pytest.approx(1007.5)
    # 1000 + 1% + 10 flat, because the file stated no fee for this row.
    assert book.positions["HRHO"].cost_basis_egp == pytest.approx(1020.0)


def test_a_stated_zero_fee_is_not_replaced_by_the_schedule(store):
    charged = FeeModel(percent_per_side=0.01, order_fee_egp=10.0, rates_loaded=True)
    frame = pd.DataFrame([
        {"Date": "2026-08-03", "Symbol": "ABUK", "Side": "BUY", "Quantity": 100,
         "Price": 10.0, "Fees": 0},
    ])

    apply_import(store, plan_file_import(frame), fee_model=charged)

    assert store.book(charged).positions["ABUK"].cost_basis_egp == pytest.approx(1000.0)


def test_arabic_column_headings_are_understood():
    frame = pd.DataFrame([
        {"السهم": "ABUK", "الكمية": 1500, "متوسط الشراء": 76.70,
         "التاريخ": "2026-08-17"},
    ])

    rows = plan_file_import(frame)

    assert rows[0].status == NEW
    assert rows[0].symbol == "ABUK"
    assert rows[0].price == pytest.approx(76.70)


def test_one_bad_row_does_not_throw_away_the_good_ones(store):
    frame = pd.DataFrame([
        {"Symbol": "ABUK", "Quantity": 1500, "AveragePrice": 76.70,
         "Date": "2026-08-17"},
        {"Symbol": "", "Quantity": 100, "AveragePrice": 5.0, "Date": "2026-08-17"},
        {"Symbol": "HRHO", "Quantity": 300, "AveragePrice": 0, "Date": "2026-08-17"},
    ])

    rows = plan_file_import(frame)
    statuses = [row.status for row in rows]

    assert statuses == [NEW, INVALID, INVALID]
    assert apply_import(store, rows, fee_model=FREE).written == 1


def test_an_empty_file_produces_nothing():
    assert plan_file_import(pd.DataFrame()) == []
