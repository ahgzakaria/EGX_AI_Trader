"""Reading a broker e-invoice, and refusing to read one wrongly.

The fixtures below are the exact layout of a real Thndr e-invoice with the
account holder's details removed. Every number in them reconciles, which is
what the parser checks: an invoice states a value, a fee total and a grand
total, and a purchase costs ``value + fees`` while a sale returns
``value - fees``. A parse that gets a digit wrong breaks that identity, and a
broken identity is refused rather than imported.
"""

from __future__ import annotations

import pytest

from holdings.invoices import (
    EQUITY,
    FUND,
    ParsedInvoice,
    classify_code,
    invoice_reference,
    parse_invoice_text,
)


SELL_INVOICE = """Invoice
A Client Name
30/08/2026
Custodian: Thndr Technology Holding
Security Name Symbol Code Transaction Type Average Cost
Credit Agricole
Indosuez (Egypt) EGS60041C018 Sell 24.60 EGP
Transaction No. Quantity Price Value
N000260123610 3,241 24.65 EGP 79,890.65 EGP
N000260123611 7 24.65 EGP 172.55 EGP
N000260123612 100 24.64 EGP 2,464.00 EGP
N000260123613 129 24.64 EGP 3,178.56 EGP
N000260123614 123 24.63 EGP 3,029.49 EGP
Total Quantity Average Price Total Cost
3,600 24.65 EGP 88,735.25 EGP
Fees Amount
EGX Services 8.88 EGP
MCDR Services 8.88 EGP
FRA Services 7.99 EGP
Risk Insurance 4.43 EGP
Trading Damgha 44.37 EGP
Brokerage &
Custody Fees 88.73 EGP
Brokerage Order
Fees 2.00 EGP
Total Fees 165.28 EGP
Grand Total 88,569.97 EGP
Note: The text in this invoice is standardized.
"""

BUY_INVOICE = """Invoice
A Client Name
30/08/2026
Custodian: Thndr Technology Holding
Security Name Symbol Code Transaction Type Average Cost
Bonyan for
Development and
Trade
EGS656M1C010 Buy 4.78 EGP
Transaction No. Quantity Price Value
N000260124805 21,000 4.77 EGP 100,170.00 EGP
Total Quantity Average Price Total Cost
21,000 4.77 EGP 100,170.00 EGP
Fees Amount
EGX Services 10.02 EGP
MCDR Services 10.02 EGP
FRA Services 5.01 EGP
Risk Insurance 5.01 EGP
Trading Damgha 50.09 EGP
Brokerage &
Custody Fees 100.17 EGP
Brokerage Order
Fees 2.00 EGP
Total Fees 182.32 EGP
Grand Total 100,352.32 EGP
"""

FUND_INVOICE = """Invoice
A Client Name
30/08/2026
Security Name Symbol Code Transaction Type Average Cost
Thndr Savings thndrsavings sell 1.32 EGP
Transaction No. Quantity Price Value
22ad4616-e612-4590-8b09-
896f45348edb 76,099 1.317 EGP 100,245.21 EGP
Total Quantity Average Price Total Cost
76,099 1.32 EGP 100,245.21 EGP
Fees Amount
Third Party Fees 0.00 EGP
Service Fees 0.00 EGP
Total Fees 0.00 EGP
Grand Total 100,245.21 EGP
"""


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def test_a_sale_is_read_with_its_quantity_fees_and_date():
    invoice = parse_invoice_text(SELL_INVOICE)

    assert invoice.ok, invoice.reason
    assert invoice.side == "SELL"
    assert invoice.trade_date == "2026-08-30"
    assert invoice.quantity == pytest.approx(3600)
    assert invoice.fees_egp == pytest.approx(165.28)
    assert invoice.value == pytest.approx(88_735.25)
    assert invoice.grand_total == pytest.approx(88_569.97)


def test_the_price_is_the_total_divided_by_the_quantity_not_the_printed_average():
    """The printed average is rounded to two decimals and is wrong by 4.75 EGP
    on this invoice. The total is what the account was actually credited."""

    invoice = parse_invoice_text(SELL_INVOICE)

    assert invoice.printed_price == pytest.approx(24.65)
    assert invoice.price == pytest.approx(88_735.25 / 3600)
    assert invoice.quantity * invoice.price == pytest.approx(88_735.25)
    assert invoice.quantity * invoice.printed_price != pytest.approx(88_735.25)


def test_an_isin_is_resolved_to_its_ticker_from_the_reference_file():
    assert parse_invoice_text(SELL_INVOICE).ticker == "CIEB"
    assert parse_invoice_text(BUY_INVOICE).ticker == "BONY"


def test_a_wrapped_security_name_is_joined_and_carries_no_personal_detail():
    """Everything above the security header is letterhead -- the account
    holder's name among it -- and none of it is read."""

    invoice = parse_invoice_text(BUY_INVOICE)

    assert invoice.security_name == "Bonyan for Development and Trade"
    assert "Client" not in invoice.security_name
    assert "Custodian" not in invoice.security_name


def test_every_execution_line_is_kept_for_the_audit_trail():
    invoice = parse_invoice_text(SELL_INVOICE)

    assert len(invoice.executions) == 5
    assert sum(item.quantity for item in invoice.executions) == pytest.approx(3600)
    assert sum(item.value for item in invoice.executions) == pytest.approx(88_735.25)


def test_the_itemised_fee_lines_survive_so_they_can_be_checked_by_hand():
    invoice = parse_invoice_text(SELL_INVOICE)
    fees = dict(invoice.fee_lines)

    assert fees["Brokerage & Custody Fees"] == pytest.approx(88.73)
    assert fees["Brokerage Order Fees"] == pytest.approx(2.00)
    assert sum(fees.values()) == pytest.approx(invoice.fees_egp)


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #

def test_a_purchase_reconciles_as_value_plus_fees():
    invoice = parse_invoice_text(BUY_INVOICE)

    assert invoice.ok
    assert invoice.value + invoice.fees_egp == pytest.approx(invoice.grand_total)


def test_a_misread_digit_is_refused_rather_than_imported():
    """The whole safety property in one test: change one digit and the invoice
    stops reconciling, so it is rejected instead of quietly wrong."""

    corrupted = BUY_INVOICE.replace("100,170.00 EGP\nFees", "100,180.00 EGP\nFees")

    invoice = parse_invoice_text(corrupted)

    assert not invoice.ok
    assert "does not reconcile" in invoice.reason


def test_a_page_that_is_not_an_invoice_is_refused_with_a_reason():
    invoice = parse_invoice_text("Statement of account\nnothing to see here")

    assert not invoice.ok
    assert invoice.reason


def test_an_empty_page_is_refused():
    assert not parse_invoice_text("").ok


# --------------------------------------------------------------------------- #
# Instruments that are not shares
# --------------------------------------------------------------------------- #

def test_a_money_market_fund_is_identified_as_not_a_listing():
    """Recording Thndr Savings as a stock would put a fictional holding in the
    portfolio with an exit plan attached to it."""

    invoice = parse_invoice_text(FUND_INVOICE)

    assert invoice.ok
    assert invoice.instrument == FUND
    assert not invoice.tradeable
    assert invoice.ticker == ""


def test_an_isin_is_told_apart_from_a_fund_code():
    assert classify_code("EGS60041C018") == EQUITY
    assert classify_code("thndrsavings") == FUND


def test_a_reference_wrapped_across_two_lines_is_rejoined_intact():
    """The broker's own reference is the audit link back to the invoice; a
    mangled one cannot be checked against anything."""

    invoice = parse_invoice_text(FUND_INVOICE)

    assert invoice.reference == "22ad4616-e612-4590-8b09-896f45348edb"


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

def test_the_reference_is_the_brokers_execution_numbers_sorted():
    invoice = parse_invoice_text(SELL_INVOICE)

    assert invoice.reference.startswith("N000260123610|")
    assert invoice.reference.count("|") == 4


def test_two_different_invoices_never_share_a_reference():
    sale = parse_invoice_text(SELL_INVOICE)
    purchase = parse_invoice_text(BUY_INVOICE)

    assert sale.reference and purchase.reference
    assert sale.reference != purchase.reference


def test_an_invoice_without_execution_numbers_still_gets_a_stable_identity():
    reference = invoice_reference("EGS60041C018", "2026-08-30", "SELL", (), 3600, 88735.25)

    assert reference == invoice_reference(
        "EGS60041C018", "2026-08-30", "SELL", (), 3600, 88735.25)
    assert reference != invoice_reference(
        "EGS60041C018", "2026-08-31", "SELL", (), 3600, 88735.25)


def test_an_unparsed_invoice_is_never_tradeable():
    assert not ParsedInvoice().tradeable


# --------------------------------------------------------------------------- #
# The PDF layer
# --------------------------------------------------------------------------- #

def test_a_pdf_with_no_extractable_text_is_refused_page_by_page():
    """A scanned invoice -- an image inside a PDF -- carries no text to read.

    It is reported as unreadable rather than half-read. This module never
    guesses at pixels, and a page it cannot read is a page it declines.
    """

    import io

    from pypdf import PdfWriter

    from holdings.invoices import parse_invoice_pdf

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    buffer.name = "scanned.pdf"

    invoices = parse_invoice_pdf(buffer)

    assert len(invoices) == 1
    assert not invoices[0].ok
    assert invoices[0].source_file == "scanned.pdf"
    assert invoices[0].page == 1
