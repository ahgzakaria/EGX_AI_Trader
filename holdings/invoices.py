"""Read a broker e-invoice and turn it into trades, exactly.

The invoices this reads are PDFs with real embedded text -- fonts, ToUnicode
maps, text operators -- so nothing here guesses at pixels. No OCR, no vision
model, no network: the numbers are read, not recognised. That distinction is
the whole reason this module can be trusted with a cost basis.

Three properties make a misparse loud instead of silent:

**The arithmetic must reconcile.** Every invoice states a value, a fee total
and a grand total, and they are related by identity: a purchase costs
``value + fees``, a sale returns ``value - fees``. If a parse produces numbers
that fail that identity, the invoice is refused and the reason is reported. A
wrong number that reconciles is essentially impossible; a wrong number that
does not is caught before it reaches the book.

**The price comes from the value, not from the printed average.** The invoice
rounds its own average to two decimals -- 3,600 shares at a printed 24.65
against a stated total of 88,735.25 differ by 4.75 EGP. Dividing the total by
the quantity is exact, and the total is the number the account was actually
debited.

**Nothing is written without being shown first.** This module only parses. The
page renders what was read, beside what it reconciles to, and the user commits.

Instruments that are not EGX equities -- money-market funds such as Thndr
Savings, which carry no ISIN and no fees -- are identified as such rather than
forced into a position. Recording a cash fund as a stock would put a fictional
holding in the portfolio and a fictional exit plan beside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re

from core.universe import canonical

#: Where the ISIN -> ticker mapping lives. The same file the sector map uses.
SECURITY_FILE = "data/sectors.csv"

#: An EGX ISIN: two country letters, then nine alphanumerics and a check digit.
ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

#: ``<name...> <code> <Buy|Sell> <average cost> EGP`` -- the name may be empty
#: when it wrapped onto earlier lines, which is why it is optional here.
SECURITY_LINE = re.compile(
    r"^(?P<name>.*?)\s*(?P<code>[A-Za-z0-9\-]{6,})\s+(?P<side>Buy|Sell)\s+"
    r"(?P<average>[\d,]+\.?\d*)\s*EGP\s*$",
    re.IGNORECASE,
)

#: ``<reference> <quantity> <price> EGP <value> EGP``
EXECUTION_LINE = re.compile(
    r"^(?P<reference>.+?)\s+(?P<quantity>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<price>[\d,]+\.?\d*)\s*EGP\s+(?P<value>[\d,]+\.?\d*)\s*EGP\s*$",
    re.IGNORECASE,
)

#: ``<quantity> <average price> EGP <total> EGP`` under the totals header.
TOTALS_LINE = re.compile(
    r"^(?P<quantity>[\d,]+(?:\.\d+)?)\s+(?P<price>[\d,]+\.?\d*)\s*EGP\s+"
    r"(?P<value>[\d,]+\.?\d*)\s*EGP\s*$",
    re.IGNORECASE,
)

FEE_LINE = re.compile(r"^(?P<label>.*?)\s*(?P<amount>[\d,]+\.?\d*)\s*EGP\s*$",
                      re.IGNORECASE)
DATE_LINE = re.compile(r"^(?P<date>\d{2}/\d{2}/\d{4})\s*$")

TOTALS_HEADER = "total quantity"
EXECUTIONS_HEADER = "transaction no"
FEES_HEADER = "fees amount"
#: Everything before this header is the invoice's letterhead -- the account
#: holder's name among it. Nothing above it is read, so no personal detail is
#: ever carried into the portfolio database.
SECURITY_HEADER = "security name"
TOTAL_FEES = "total fees"
GRAND_TOTAL = "grand total"

EQUITY = "EQUITY"
FUND = "FUND"
UNKNOWN = "UNKNOWN"

#: The identity below must hold to within this many pounds. Invoices state
#: every component to the piastre, so the only slack needed is for the
#: half-piastre each printed figure was rounded by.
RECONCILE_TOLERANCE_EGP = 0.05


@dataclass(frozen=True)
class Execution:
    """One fill inside an invoice, kept for the audit trail and for dedupe."""

    reference: str
    quantity: float
    price: float
    value: float


@dataclass(frozen=True)
class ParsedInvoice:
    """One invoice page, read and reconciled."""

    page: int = 0
    ok: bool = False
    reason: str = ""
    trade_date: str = ""
    security_name: str = ""
    symbol_code: str = ""
    ticker: str = ""
    side: str = ""
    quantity: float = 0.0
    #: ``value / quantity`` -- exact, and what the account was charged.
    price: float = 0.0
    #: The average the invoice printed, kept only so the two can be compared.
    printed_price: float = 0.0
    value: float = 0.0
    fees_egp: float = 0.0
    grand_total: float = 0.0
    instrument: str = UNKNOWN
    fee_lines: tuple = ()
    executions: tuple = ()
    reference: str = ""
    source_file: str = ""

    @property
    def tradeable(self) -> bool:
        """A recognised EGX equity with a resolved ticker and sane numbers."""

        return self.ok and self.instrument == EQUITY and bool(self.ticker)

    @property
    def net_egp(self) -> float:
        """Cash the invoice moved: negative on a purchase, positive on a sale."""

        return -self.grand_total if self.side == "BUY" else self.grand_total


def _number(text):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _iso_date(value):
    try:
        return datetime.strptime(str(value).strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


# --------------------------------------------------------------------------- #
# Security identity
# --------------------------------------------------------------------------- #

_SECURITY_CACHE: dict = {}


def load_security_map(path=SECURITY_FILE) -> dict:
    """``{ISIN: ticker}`` from the reference file, cached per path.

    The invoice identifies a security by ISIN, not by ticker, and that is the
    stronger identifier: a ticker can be reused after a delisting, an ISIN
    cannot. 222 of the 228 rows in the shipped file carry one.
    """

    key = str(path)
    if key in _SECURITY_CACHE:
        return _SECURITY_CACHE[key]
    mapping = {}
    source = Path(path)
    if source.is_file():
        import pandas as pd

        try:
            frame = pd.read_csv(source)
        except Exception:                                        # noqa: BLE001
            frame = None
        if frame is not None and {"ISIN", "Ticker"} <= set(frame.columns):
            for isin, ticker in zip(frame["ISIN"], frame["Ticker"]):
                code = str(isin).strip().upper()
                symbol = canonical(ticker)
                if code and code != "NAN" and symbol:
                    mapping[code] = symbol
    _SECURITY_CACHE[key] = mapping
    return mapping


def clear_security_cache():
    _SECURITY_CACHE.clear()


def classify_code(code) -> str:
    """Is this identifier an EGX security, or something else entirely?"""

    text = str(code or "").strip().upper()
    if ISIN_PATTERN.match(text):
        return EQUITY
    # A code that is not an ISIN is not an EGX listing. Thndr Savings arrives
    # as ``thndrsavings`` with no fees; it is a money-market fund and belongs
    # in the cash account, not in the portfolio.
    return FUND if text else UNKNOWN


def resolve_ticker(code, path=SECURITY_FILE) -> str:
    """The canonical ticker for an ISIN, or ``""`` when it is not on file."""

    return load_security_map(path).get(str(code or "").strip().upper(), "")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def parse_invoice_text(text, *, page=0, source_file="",
                       security_path=SECURITY_FILE) -> ParsedInvoice:
    """Read one invoice page. Never raises; a failure comes back as a reason."""

    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    invoice = ParsedInvoice(page=page, source_file=str(source_file or ""))
    if not lines:
        return _failed(invoice, "the page has no text")

    trade_date = ""
    security = None
    executions = []
    totals = None
    fee_lines = []
    total_fees = None
    grand_total = None
    section = ""
    pending = ""

    for line in lines:
        lowered = line.lower()
        if not trade_date:
            matched = DATE_LINE.match(line)
            if matched:
                trade_date = _iso_date(matched.group("date"))
                continue

        if lowered.startswith(SECURITY_HEADER):
            section, pending = "security", ""
            continue
        if lowered.startswith(EXECUTIONS_HEADER):
            section, pending = "executions", ""
            continue
        if lowered.startswith(TOTALS_HEADER):
            section, pending = "totals", ""
            continue
        if lowered.startswith(FEES_HEADER):
            section, pending = "fees", ""
            continue
        if lowered.startswith(GRAND_TOTAL):
            matched = FEE_LINE.match(line)
            grand_total = _number(matched.group("amount")) if matched else None
            section = ""
            continue
        if lowered.startswith(TOTAL_FEES):
            matched = FEE_LINE.match(line)
            total_fees = _number(matched.group("amount")) if matched else None
            continue
        if lowered.startswith("note:"):
            break

        if section == "security" and security is None:
            matched = SECURITY_LINE.match(line)
            if matched:
                name = " ".join(part for part in (pending, matched.group("name")) if part)
                security = {
                    "name": name.strip(),
                    "code": matched.group("code").strip(),
                    "side": matched.group("side").upper(),
                    "printed_average": _number(matched.group("average")),
                }
                pending = ""
            else:
                # A security name long enough to wrap arrives as its own lines
                # before the one carrying the code.
                pending = f"{pending} {line}".strip() if pending else line
            continue

        if section == "executions":
            # A reference can wrap mid-token (a UUID split across two lines),
            # so an unmatched line is held and prefixed onto the next one.
            candidate = (pending + line) if pending else line
            matched = EXECUTION_LINE.match(candidate)
            if matched:
                executions.append(Execution(
                    reference=matched.group("reference").strip(),
                    quantity=_number(matched.group("quantity")) or 0.0,
                    price=_number(matched.group("price")) or 0.0,
                    value=_number(matched.group("value")) or 0.0,
                ))
                pending = ""
            else:
                # Joined verbatim, trailing hyphen included: the wrap in a UUID
                # falls after a hyphen that belongs to the reference, and a
                # reference that does not match the broker's own is useless for
                # checking an entry against the invoice it came from.
                pending = candidate
            continue

        if section == "totals" and totals is None:
            matched = TOTALS_LINE.match(line)
            if matched:
                totals = {
                    "quantity": _number(matched.group("quantity")),
                    "price": _number(matched.group("price")),
                    "value": _number(matched.group("value")),
                }
            continue

        if section == "fees":
            matched = FEE_LINE.match(line)
            if matched:
                label = " ".join(
                    part for part in (pending, matched.group("label")) if part).strip()
                fee_lines.append((label, _number(matched.group("amount")) or 0.0))
                pending = ""
            else:
                pending = line
            continue

        # Security-name fragments arrive before the line carrying the code.
        if security is None:
            pending = f"{pending} {line}".strip() if pending else line

    if security is None:
        return _failed(invoice, "no security line (name, code, Buy/Sell) was found")
    if not trade_date:
        return _failed(invoice, "no invoice date was found")
    if not totals or not totals.get("quantity") or totals.get("value") is None:
        return _failed(invoice, "no totals line (quantity, average price, total) was found")
    if total_fees is None:
        return _failed(invoice, "no fee total was found")
    if grand_total is None:
        return _failed(invoice, "no grand total was found")

    quantity = float(totals["quantity"])
    value = float(totals["value"])
    if quantity <= 0 or value <= 0:
        return _failed(invoice, f"quantity or value is not positive ({quantity}, {value})")

    side = security["side"]
    instrument = classify_code(security["code"])
    ticker = resolve_ticker(security["code"], security_path) if instrument == EQUITY else ""

    # The identity that makes a misparse impossible to miss.
    expected = value + total_fees if side == "BUY" else value - total_fees
    if abs(expected - grand_total) > RECONCILE_TOLERANCE_EGP:
        return _failed(
            invoice,
            f"the invoice does not reconcile: {value:,.2f} "
            f"{'+' if side == 'BUY' else '-'} {total_fees:,.2f} = {expected:,.2f}, "
            f"but the stated grand total is {grand_total:,.2f}",
        )

    return ParsedInvoice(
        page=page,
        ok=True,
        trade_date=trade_date,
        security_name=security["name"],
        symbol_code=security["code"],
        ticker=ticker,
        side=side,
        quantity=quantity,
        price=value / quantity,
        printed_price=float(totals.get("price") or 0.0),
        value=value,
        fees_egp=float(total_fees),
        grand_total=float(grand_total),
        instrument=instrument,
        fee_lines=tuple(fee_lines),
        executions=tuple(executions),
        reference=invoice_reference(security["code"], trade_date, side, executions,
                                   quantity, value),
        source_file=str(source_file or ""),
        reason=(
            "" if instrument == EQUITY and ticker
            else ("not an EGX listing (no ISIN) — a fund or cash instrument"
                  if instrument != EQUITY
                  else f"ISIN {security['code']} is not in {SECURITY_FILE}")
        ),
    )


def _failed(invoice: ParsedInvoice, reason: str) -> ParsedInvoice:
    from dataclasses import replace

    return replace(invoice, ok=False, reason=reason)


def invoice_reference(code, trade_date, side, executions, quantity, value) -> str:
    """A stable identity for one invoice, so importing it twice is a no-op.

    Built from the broker's own execution numbers when they are present, since
    those are unique per fill. When they are not, the shape of the invoice
    itself -- code, date, side, quantity, value -- identifies it well enough
    that a genuine duplicate matches and two different invoices do not.
    """

    references = [item.reference for item in executions or () if item.reference]
    if references:
        return "|".join(sorted(references))
    return f"{str(code).upper()}@{trade_date}@{side}@{quantity:g}@{value:.2f}"


def parse_invoice_pdf(source, *, security_path=SECURITY_FILE) -> list:
    """Parse every page of a PDF into invoices, in page order.

    ``source`` is a path or an open binary stream, so an uploaded file can be
    read without ever being written to disk.
    """

    try:
        from pypdf import PdfReader
    except ImportError as error:                                 # pragma: no cover
        raise RuntimeError(
            "pypdf is required to read invoice PDFs: pip install pypdf"
        ) from error

    name = getattr(source, "name", None) or str(source)
    reader = PdfReader(source)
    invoices = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as failure:                             # noqa: BLE001
            invoices.append(_failed(
                ParsedInvoice(page=index, source_file=Path(str(name)).name),
                f"the page could not be read: {type(failure).__name__}",
            ))
            continue
        invoices.append(parse_invoice_text(
            text, page=index, source_file=Path(str(name)).name,
            security_path=security_path,
        ))
    return invoices
