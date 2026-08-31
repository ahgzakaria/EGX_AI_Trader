"""Bulk entry: broker invoices, and a spreadsheet for what predates them.

Two doors into the book, and both of them stop at a preview.

**Invoices** are the exact door. Quantity, price and the fee total are read from
the document the account was actually charged by, so nothing is modelled and
nothing is averaged by hand. Each invoice carries the broker's own execution
numbers, which makes re-importing the same file a no-op instead of a doubled
position.

**A spreadsheet** is for the history that has no invoice to hand. It takes two
shapes:

* *transactions* -- one row per buy or sell, the same fields an invoice has;
* *opening positions* -- symbol, quantity and the average price the broker
  states, for a holding accumulated over several purchases whose commissions
  can no longer be separated. That average is recorded as a single lot with
  **zero** additional fees, because the broker's stated average already
  contains them. Charging the modelled schedule on top would bill the same
  commission twice and quietly raise every breakeven on the page.

Nothing here writes on its own. Both doors produce a list of rows with a status
each, the page shows them, and only then are they committed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from core.universe import canonical
from holdings.book import BUY, DEPOSIT, SELL, WITHDRAW
from holdings.invoices import EQUITY, ParsedInvoice
from holdings.store import StoreError

# What a row will do.
TRADE = "TRADE"
CASH = "CASH"
SKIP = "SKIP"

# Whether it may do it.
NEW = "NEW"
DUPLICATE = "DUPLICATE"
UNRESOLVED = "UNRESOLVED"
NOT_EGX = "NOT_EGX"
INVALID = "INVALID"
#: The row is well-formed but cannot be true of this account yet -- almost
#: always a sale of shares whose purchase has not been recorded.
CONFLICT = "CONFLICT"

#: Statuses that will be written when the import is confirmed.
IMPORTABLE = (NEW,)

OPENING_NOTE = "opening position — broker-stated average, fees already included"


@dataclass(frozen=True)
class ImportRow:
    """One prospective entry, with why it is or is not going to be written."""

    kind: str = SKIP
    status: str = INVALID
    symbol: str = ""
    trade_date: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    fees_egp: float | None = None
    amount_egp: float = 0.0
    note: str = ""
    reference: str = ""
    source_file: str = ""
    source_label: str = ""
    detail: str = ""

    @property
    def importable(self) -> bool:
        return self.status in IMPORTABLE and self.kind in (TRADE, CASH)

    @property
    def value_egp(self) -> float:
        return self.amount_egp if self.kind == CASH else self.quantity * self.price


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #

def plan_invoice_import(invoices, store=None, *, manual_symbols=None,
                        funds_as_cash=True) -> list:
    """Turn parsed invoices into rows, each with a status and a reason.

    ``manual_symbols`` maps an invoice reference to a ticker the user picked by
    hand, for the rare listing whose ISIN is not yet in the reference file. It
    is the only way a symbol can be decided outside the file, and it is
    recorded per row rather than written back into the reference data.
    """

    manual_symbols = manual_symbols or {}
    known = store.imported_references() if store is not None else set()
    rows = []

    for invoice in invoices or ():
        label = f"{invoice.source_file or 'invoice'} · صفحة {invoice.page}"
        base = ImportRow(
            reference=invoice.reference,
            source_file=invoice.source_file,
            source_label=label,
            trade_date=invoice.trade_date,
            side=invoice.side,
        )

        if not invoice.ok:
            rows.append(replace(base, status=INVALID, kind=SKIP,
                                detail=invoice.reason))
            continue

        if invoice.reference and invoice.reference in known:
            rows.append(replace(
                base, status=DUPLICATE, kind=SKIP,
                symbol=invoice.ticker or invoice.symbol_code,
                quantity=invoice.quantity, price=invoice.price,
                fees_egp=invoice.fees_egp,
                detail="مسجّلة بالفعل — نفس أرقام تنفيذ الوسيط",
            ))
            continue

        if invoice.instrument != EQUITY:
            # A money-market fund is not a position. Selling it returns cash to
            # the trading balance and buying it takes cash out; recorded that
            # way, the cash figure on the page stays true. Recorded as a stock,
            # it would appear as a holding with an exit plan attached to it.
            if not funds_as_cash:
                rows.append(replace(
                    base, status=NOT_EGX, kind=SKIP,
                    symbol=invoice.security_name or invoice.symbol_code,
                    quantity=invoice.quantity, price=invoice.price,
                    detail="ليس سهمًا مقيدًا — لم يُسجَّل",
                ))
                continue
            rows.append(replace(
                base, status=NEW, kind=CASH,
                symbol=invoice.security_name or invoice.symbol_code,
                side=DEPOSIT if invoice.side == SELL else WITHDRAW,
                amount_egp=invoice.grand_total,
                quantity=invoice.quantity, price=invoice.price,
                note=f"{invoice.security_name} ({invoice.symbol_code})",
                detail="صندوق نقدي — يُسجَّل كحركة كاش لا كمركز",
            ))
            continue

        symbol = invoice.ticker or canonical(manual_symbols.get(invoice.reference, ""))
        if not symbol:
            rows.append(replace(
                base, status=UNRESOLVED, kind=SKIP,
                symbol=invoice.symbol_code,
                quantity=invoice.quantity, price=invoice.price,
                fees_egp=invoice.fees_egp,
                detail=f"{invoice.security_name}: الرمز الدولي "
                       f"{invoice.symbol_code} غير معروف — اختر السهم يدويًا",
            ))
            continue

        rows.append(replace(
            base, status=NEW, kind=TRADE, symbol=symbol,
            quantity=invoice.quantity, price=invoice.price,
            fees_egp=invoice.fees_egp,
            note=f"invoice {invoice.source_file} p{invoice.page}",
            detail=f"{invoice.security_name} — الإجمالي {invoice.value:,.2f} "
                   f"ورسوم {invoice.fees_egp:,.2f}",
        ))

    return rows


# --------------------------------------------------------------------------- #
# Spreadsheets
# --------------------------------------------------------------------------- #

#: Accepted spellings for each field, lowercased and stripped of spaces.
COLUMN_ALIASES = {
    "date": ("date", "trade_date", "tradedate", "التاريخ", "تاريخ"),
    "symbol": ("symbol", "ticker", "code", "السهم", "الرمز", "الكود"),
    "side": ("side", "type", "transactiontype", "action", "العملية", "النوع"),
    "quantity": ("quantity", "qty", "shares", "الكمية", "عدد", "عددالاسهم"),
    "price": ("price", "executedprice", "السعر", "سعر", "سعرالتنفيذ"),
    "average_price": ("averageprice", "avgprice", "average", "avg", "cost",
                      "averagecost", "متوسطالشراء", "المتوسط", "متوسط",
                      "متوسطالسعر", "متوسطالتكلفة"),
    "fees": ("fees", "fee", "commission", "totalfees", "العمولة", "الرسوم",
             "عمولة", "اجماليالرسوم"),
    "note": ("note", "notes", "comment", "ملاحظة", "ملاحظات"),
}

BUY_WORDS = {"buy", "b", "شراء", "شرا", "شراءات"}
SELL_WORDS = {"sell", "s", "بيع", "بيوع"}

TRANSACTIONS = "TRANSACTIONS"
OPENING = "OPENING"


def _key(name):
    return "".join(str(name).split()).strip().lower().replace("_", "")


def map_columns(columns) -> dict:
    """``{field: actual column name}`` for whatever spellings were used."""

    found = {}
    for column in columns:
        key = _key(column)
        for field, aliases in COLUMN_ALIASES.items():
            if key in aliases and field not in found:
                found[field] = column
    return found


def detect_shape(columns) -> str:
    """Which of the two spreadsheet shapes this is.

    A ``Side`` column means one row per transaction. Its absence, with an
    average price present, means opening positions. The distinction is made
    from the columns rather than asked for, because a file that has a side per
    row cannot be anything else.
    """

    mapping = map_columns(columns)
    if "side" in mapping and "price" in mapping:
        return TRANSACTIONS
    if "average_price" in mapping:
        return OPENING
    if "side" in mapping:
        return TRANSACTIONS
    return ""


def _side(value):
    text = str(value or "").strip().lower()
    if text in BUY_WORDS:
        return BUY
    if text in SELL_WORDS:
        return SELL
    return ""


def _positive(value):
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 and number == number else None


def _stated_fee(value):
    """``None`` means "charge the schedule"; a number, zero included, is stated.

    An empty cell and a zero are different claims. Blank says the user does not
    know what was charged, so the fee schedule fills in. Zero says nothing was
    charged, and must survive as zero rather than being replaced by a modelled
    figure the account never paid.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "-", "—"}:
        return None
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return None
    return number if number >= 0 and number == number else None


def plan_file_import(frame, *, default_date="") -> list:
    """Turn a spreadsheet into rows, each with a status and a reason.

    Refuses per row, never per file: one unreadable line does not throw away
    the twenty that were fine, and the reader can see exactly which one to fix.
    """

    if frame is None or getattr(frame, "empty", True):
        return []
    mapping = map_columns(frame.columns)
    shape = detect_shape(frame.columns)
    rows = []

    for position, record in enumerate(frame.to_dict("records"), start=1):
        label = f"صف {position}"
        symbol = canonical(record.get(mapping.get("symbol", ""), ""))
        quantity = _positive(record.get(mapping.get("quantity", ""), None))
        note = str(record.get(mapping.get("note", ""), "") or "").strip()
        raw_date = record.get(mapping.get("date", ""), "") if "date" in mapping else ""
        trade_date = str(raw_date or default_date or "").strip()[:10]

        base = ImportRow(source_label=label, symbol=symbol, trade_date=trade_date,
                         note=note)

        if not symbol:
            rows.append(replace(base, status=INVALID, detail="لا يوجد رمز سهم"))
            continue
        if quantity is None:
            rows.append(replace(base, status=INVALID, detail="الكمية غير صالحة"))
            continue

        if shape == OPENING:
            price = _positive(record.get(mapping.get("average_price", ""), None))
            if price is None:
                rows.append(replace(base, status=INVALID,
                                    detail="متوسط السعر غير صالح"))
                continue
            if not trade_date:
                rows.append(replace(base, status=INVALID,
                                    detail="لا يوجد تاريخ للمركز الافتتاحي"))
                continue
            rows.append(replace(
                base, status=NEW, kind=TRADE, side=BUY, quantity=quantity,
                price=price,
                # Zero, not None: the broker's average already contains the
                # commission, and the schedule must not be charged over it.
                fees_egp=0.0,
                note=note or OPENING_NOTE,
                detail=f"مركز افتتاحي — {quantity:,.0f} بمتوسط {price:,.3f} "
                       "(الرسوم داخل المتوسط)",
            ))
            continue

        side = _side(record.get(mapping.get("side", ""), ""))
        price = _positive(record.get(mapping.get("price", ""), None))
        fees = _stated_fee(
            record.get(mapping["fees"]) if "fees" in mapping else None)

        if not side:
            rows.append(replace(base, status=INVALID,
                                detail="العملية لازم تكون شراء/بيع أو Buy/Sell"))
            continue
        if price is None:
            rows.append(replace(base, status=INVALID, detail="السعر غير صالح"))
            continue
        if not trade_date:
            rows.append(replace(base, status=INVALID, detail="لا يوجد تاريخ"))
            continue

        rows.append(replace(
            base, status=NEW, kind=TRADE, side=side, quantity=quantity,
            price=price, fees_egp=fees,
            detail=("العمولة من الملف" if fees is not None
                    else "العمولة ستُحسب من جدول الرسوم"),
        ))

    return rows


# --------------------------------------------------------------------------- #
# Validation against the book that already exists
# --------------------------------------------------------------------------- #

def _ordered(rows):
    """The order the import will write in: oldest first, buys before sells."""

    return sorted(rows, key=lambda row: (row.trade_date, 0 if row.side == BUY else 1))


def validate_rows(rows, store, *, fee_model=None) -> list:
    """Mark rows that the book would refuse, before anything is written.

    Replays each prospective row onto the history that already exists. The
    common failure has one cause and one fix: an invoice sells shares whose
    purchase was never recorded, because the position predates the invoices.
    Saying that plainly in the preview is the difference between a fixable
    message and a database error.
    """

    from holdings.book import BookError, build_book

    if store is None:
        return list(rows or [])

    events = list(store.events())
    next_id = max([int(event.get("id") or 0) for event in events] or [0]) + 1
    verdicts = {}

    for offset, row in enumerate(_ordered(
            [item for item in rows or () if item.importable and item.kind == TRADE])):
        candidate = {
            "id": next_id + offset,
            "kind": row.side,
            "date": row.trade_date,
            "symbol": row.symbol,
            "quantity": row.quantity,
            "price": row.price,
            "fees_egp": row.fees_egp,
        }
        try:
            build_book(events + [candidate], fee_model)
        except BookError:
            held = 0.0
            try:
                position = build_book(events, fee_model).positions.get(row.symbol)
                held = getattr(position, "quantity", 0.0) or 0.0
            except BookError:
                held = 0.0
            verdicts[id(row)] = (
                f"بيع {row.quantity:,.0f} من {row.symbol} والرصيد المسجّل "
                f"{held:,.0f} — سجّل المركز الافتتاحي أولًا من تبويب «أول مرة»، "
                "أو استورد فواتير الشراء الأقدم."
            )
            continue
        events.append(candidate)

    return [
        replace(row, status=CONFLICT, kind=SKIP, detail=verdicts[id(row)])
        if id(row) in verdicts else row
        for row in rows or ()
    ]


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ImportResult:
    """What actually happened, per row, after the user confirmed."""

    written: int = 0
    skipped: int = 0
    failures: tuple = ()

    @property
    def ok(self) -> bool:
        return not self.failures


def apply_import(store, rows, *, fee_model=None) -> ImportResult:
    """Write the importable rows, in date order, reporting every failure.

    Written oldest first so that a sale never reaches the book before the
    purchase it draws its shares from -- the store would refuse it, correctly,
    and the user would see a failure caused only by the order of the file.
    """

    ordered = _ordered([row for row in rows or () if row.importable])
    written = 0
    failures = []
    for row in ordered:
        try:
            if row.kind == CASH:
                store.record_cash(
                    row.trade_date, row.side, row.amount_egp, note=row.note,
                    source_reference=row.reference, source_file=row.source_file,
                )
            else:
                store.record_trade(
                    row.symbol, row.trade_date, row.side, row.quantity, row.price,
                    fees_egp=row.fees_egp, note=row.note, fee_model=fee_model,
                    source_reference=row.reference, source_file=row.source_file,
                )
            written += 1
        except StoreError as error:
            failures.append((row.source_label or row.symbol, str(error)))
    return ImportResult(
        written=written,
        skipped=len([row for row in rows or () if not row.importable]),
        failures=tuple(failures),
    )


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

TRANSACTIONS_TEMPLATE = (
    "Date,Symbol,Side,Quantity,Price,Fees,Note\n"
    "2026-08-03,ABUK,BUY,1000,75.00,140.30,\n"
    "2026-08-17,ABUK,BUY,500,80.00,76.80,\n"
    "2026-08-24,ABUK,SELL,500,84.50,80.10,\n"
)

OPENING_TEMPLATE = (
    "Symbol,Quantity,AveragePrice,Date,Note\n"
    "ABUK,1500,76.70,2026-08-17,\n"
    "COMI,300,95.20,2026-07-20,\n"
)
