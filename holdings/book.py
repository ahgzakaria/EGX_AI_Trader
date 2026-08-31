"""Average-cost accounting over a real trade history.

Pure functions over an event stream. Nothing here reads a database, a provider
or a clock, so every number it produces can be checked by hand against a broker
contract note.

Three decisions are worth stating, because each one could reasonably have gone
the other way and the difference shows up on screen:

**Average cost, not FIFO.** A partial sale realizes profit against the weighted
average of everything held, which is what an EGX broker statement shows and
what the user asked for. FIFO would report a different realized figure for the
same account on the same day, which is a fine convention and the wrong one to
adopt silently.

**Entry fees enter the cost basis; slippage does not.** The user records the
price actually executed, so the slippage already happened -- adding a modelled
half-percent on top would charge it twice. Commission and the flat order fee
are real charges that never appear in the fill price, so they belong in the
basis. Slippage and spread are forward-looking execution risk and are charged
by the exit estimate, not by the cost of what is already owned.

**A stock dividend or split changes the share count, never the basis.** The
money paid did not change; only how many certificates it is spread across. This
is the failure that silently corrupts a hand-kept average: 100 shares at 10.00
after a 10% bonus is 110 shares at 9.091, and a reader still comparing against
10.00 believes they are losing when they are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from services.trading_costs import TradingCosts, load_trading_costs


BUY = "BUY"
SELL = "SELL"
SPLIT = "SPLIT"
STOCK_DIVIDEND = "STOCK_DIVIDEND"
CASH_DIVIDEND = "CASH_DIVIDEND"
DEPOSIT = "DEPOSIT"
WITHDRAW = "WITHDRAW"

#: Events that change a share count or a cost basis.
POSITION_EVENTS = (BUY, SELL, SPLIT, STOCK_DIVIDEND, CASH_DIVIDEND)
#: Events that change cash without touching any position.
CASH_EVENTS = (DEPOSIT, WITHDRAW)
EVENT_KINDS = POSITION_EVENTS + CASH_EVENTS

#: Prices are quoted to three decimals on this exchange -- see the note in
#: ``strategy/entry.py``: 71% of closes under 1 EGP sit on three decimals and
#: only 24% on two, so rounding a low-priced average to two moves it by up to
#: half a piastre.
PRICE_PRECISION = 3

#: A share count below this is rounding dust from a split factor, not a holding.
CLOSED_BELOW = 1e-6


class BookError(ValueError):
    """An event sequence that cannot be true of a real account."""


# --------------------------------------------------------------------------- #
# Fees
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FeeModel:
    """What a side of a trade costs, separated by what kind of cost it is.

    ``percent_per_side`` and ``order_fee_egp`` are *charges*: they appear on a
    contract note and are known before the order is sent. ``slippage_per_side``
    and the quoted spread are *execution risk*: they are not billed, they are
    the difference between the price named and the price received. Only the
    first pair may enter a cost basis or a breakeven price, because only the
    first pair is a number the user can be held to.
    """

    percent_per_side: float = 0.0
    order_fee_egp: float = 0.0
    slippage_per_side: float = 0.0
    tax_percent: float | None = None
    rates_loaded: bool = False
    load_error: str | None = None
    fee_lines: tuple = ()

    @property
    def tax_rate(self) -> float:
        """The gains-tax rate as a fraction; zero when unset OR set to zero."""

        return float(self.tax_percent or 0.0) / 100.0

    def charge(self, notional_egp) -> float:
        """Billed cost of one side: percentage of notional plus the flat fee."""

        notional = abs(float(notional_egp or 0.0))
        if notional <= 0:
            return 0.0
        return notional * self.percent_per_side + self.order_fee_egp


def fee_model_from_costs(costs: TradingCosts) -> FeeModel:
    """Adapt the shared cost model without redefining any of its rates."""

    return FeeModel(
        percent_per_side=float(costs.commission_per_side or 0.0),
        order_fee_egp=float(costs.order_fee_egp or 0.0),
        slippage_per_side=float(costs.slippage_per_side or 0.0),
        tax_percent=costs.capital_gains_tax_percent,
        rates_loaded=bool(costs.rates_loaded),
        load_error=costs.load_error,
        fee_lines=tuple(costs.fee_lines or ()),
    )


def load_fee_model() -> FeeModel:
    """The live fee schedule, transcribed from the user's own contract note.

    Read from the same settings section the rest of the project prices trades
    with, so a portfolio number and a signal's cost can never disagree. A read
    failure produces a model that reports itself unloaded rather than one that
    silently charges zero.
    """

    return fee_model_from_costs(load_trading_costs())


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

def _number(value, *, positive=False, non_negative=False, name="value"):
    if value is None or isinstance(value, bool):
        raise BookError(f"{name} is required")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise BookError(f"{name} is not a number: {value!r}") from error
    if number != number or number in (float("inf"), float("-inf")):
        raise BookError(f"{name} is not finite: {value!r}")
    if positive and number <= 0:
        raise BookError(f"{name} must be greater than zero, got {number}")
    if non_negative and number < 0:
        raise BookError(f"{name} cannot be negative, got {number}")
    return number


def _sort_key(event):
    """Chronological, then by insertion order for events on the same day.

    Two trades on one date have no time in this store -- the user records a day,
    not a timestamp -- so insertion order decides. It only matters for a same-day
    buy and sell of the same symbol, where the average the sale is measured
    against depends on whether the buy came first.
    """

    return (str(event.get("date") or ""), int(event.get("id") or 0))


# --------------------------------------------------------------------------- #
# Positions
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Position:
    """One holding, as the account would show it."""

    symbol: str
    quantity: float = 0.0
    #: Everything paid for the shares still held, including their entry fees.
    cost_basis_egp: float = 0.0
    realized_pnl_egp: float = 0.0
    dividend_income_egp: float = 0.0
    fees_paid_egp: float = 0.0
    first_trade_date: str = ""
    last_trade_date: str = ""
    #: When the CURRENT holding was opened from nothing. Cleared on a full exit,
    #: so a stock bought, sold and bought again is not aged from the first time.
    opened_on: str = ""
    buy_count: int = 0
    sell_count: int = 0
    #: Sales made since this holding was opened. The exit plan needs to know
    #: whether the partial has already been taken, and "has ever been sold" is
    #: the wrong question for a stock that was traded months ago and re-entered.
    partial_sales: int = 0
    #: Corporate actions applied, as ``(date, kind, factor_or_amount)``.
    adjustments: tuple = ()

    @property
    def open(self) -> bool:
        return self.quantity > CLOSED_BELOW

    @property
    def average_price(self):
        """Weighted average paid per share, fees included."""

        if not self.open:
            return None
        return self.cost_basis_egp / self.quantity


@dataclass(frozen=True)
class Book:
    """The whole account: open positions, closed ones, and cash."""

    positions: dict = field(default_factory=dict)
    cash_egp: float = 0.0
    deposits_egp: float = 0.0
    withdrawals_egp: float = 0.0
    realized_pnl_egp: float = 0.0
    dividend_income_egp: float = 0.0
    fees_paid_egp: float = 0.0

    @property
    def open_positions(self) -> list:
        return [item for item in self.positions.values() if item.open]

    @property
    def closed_positions(self) -> list:
        return [item for item in self.positions.values() if not item.open]

    @property
    def invested_egp(self) -> float:
        return sum(item.cost_basis_egp for item in self.open_positions)


def build_book(events, fee_model: FeeModel | None = None) -> Book:
    """Replay an event history into positions and a cash balance.

    Raises ``BookError`` on a sequence that cannot be true -- selling shares
    that were never bought, a corporate action on nothing. It refuses rather
    than clamping to zero, because a book that quietly absorbs an impossible
    row reports a wrong average with total confidence.
    """

    fees = fee_model or FeeModel()
    positions: dict = {}
    cash = deposits = withdrawals = 0.0
    realized_total = dividends_total = fees_total = 0.0

    for event in sorted(list(events or ()), key=_sort_key):
        kind = str(event.get("kind") or "").upper()
        if kind not in EVENT_KINDS:
            raise BookError(f"unknown event kind: {kind!r}")
        date = str(event.get("date") or "")

        if kind in CASH_EVENTS:
            amount = _number(event.get("amount_egp"), positive=True, name="amount")
            if kind == DEPOSIT:
                cash += amount
                deposits += amount
            else:
                cash -= amount
                withdrawals += amount
            continue

        symbol = str(event.get("symbol") or "").strip().upper()
        if not symbol:
            raise BookError(f"{kind} event has no symbol")
        current = positions.get(symbol) or Position(symbol=symbol)

        if kind == BUY:
            quantity = _number(event.get("quantity"), positive=True, name="quantity")
            price = _number(event.get("price"), positive=True, name="price")
            notional = quantity * price
            charged = event.get("fees_egp")
            charged = (
                fees.charge(notional) if charged is None
                else _number(charged, non_negative=True, name="fees")
            )
            cash -= notional + charged
            fees_total += charged
            reopened = not current.open
            current = replace(
                current,
                quantity=current.quantity + quantity,
                cost_basis_egp=current.cost_basis_egp + notional + charged,
                fees_paid_egp=current.fees_paid_egp + charged,
                first_trade_date=current.first_trade_date or date,
                last_trade_date=date,
                opened_on=date if reopened else current.opened_on,
                buy_count=current.buy_count + 1,
                partial_sales=0 if reopened else current.partial_sales,
            )

        elif kind == SELL:
            quantity = _number(event.get("quantity"), positive=True, name="quantity")
            price = _number(event.get("price"), positive=True, name="price")
            if quantity > current.quantity + 1e-9:
                raise BookError(
                    f"{symbol}: selling {quantity:g} shares on {date} but only "
                    f"{current.quantity:g} were held"
                )
            average = current.cost_basis_egp / current.quantity
            notional = quantity * price
            charged = event.get("fees_egp")
            charged = (
                fees.charge(notional) if charged is None
                else _number(charged, non_negative=True, name="fees")
            )
            realized = notional - charged - quantity * average
            cash += notional - charged
            fees_total += charged
            realized_total += realized
            remaining = current.quantity - quantity
            closed = remaining <= CLOSED_BELOW
            current = replace(
                current,
                # Exactly zero when the position is fully closed: subtracting a
                # rounded average would leave a basis behind with no shares.
                quantity=0.0 if closed else remaining,
                cost_basis_egp=(
                    0.0 if closed else current.cost_basis_egp - quantity * average
                ),
                realized_pnl_egp=current.realized_pnl_egp + realized,
                fees_paid_egp=current.fees_paid_egp + charged,
                last_trade_date=date,
                opened_on="" if closed else current.opened_on,
                sell_count=current.sell_count + 1,
                partial_sales=0 if closed else current.partial_sales + 1,
            )

        elif kind in (SPLIT, STOCK_DIVIDEND):
            factor = _number(event.get("factor"), positive=True, name="factor")
            if not current.open:
                raise BookError(
                    f"{symbol}: a {kind} on {date} applies to no held shares")
            current = replace(
                current,
                quantity=current.quantity * factor,
                adjustments=current.adjustments + ((date, kind, factor),),
            )

        elif kind == CASH_DIVIDEND:
            amount_per_share = _number(
                event.get("amount_per_share"), positive=True, name="dividend")
            if not current.open:
                raise BookError(
                    f"{symbol}: a cash dividend on {date} applies to no held shares")
            received = current.quantity * amount_per_share
            cash += received
            dividends_total += received
            current = replace(
                current,
                dividend_income_egp=current.dividend_income_egp + received,
                adjustments=current.adjustments
                + ((date, CASH_DIVIDEND, amount_per_share),),
            )

        positions[symbol] = current

    return Book(
        positions=positions,
        cash_egp=cash,
        deposits_egp=deposits,
        withdrawals_egp=withdrawals,
        realized_pnl_egp=realized_total,
        dividend_income_egp=dividends_total,
        fees_paid_egp=fees_total,
    )


# --------------------------------------------------------------------------- #
# Valuation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PositionValue:
    """What one position is worth now, gross and after the cost of leaving it."""

    symbol: str
    quantity: float
    average_price: float
    price: float
    market_value_egp: float
    #: Cash that would actually arrive from selling every share at ``price``.
    net_proceeds_egp: float
    unrealized_gross_egp: float
    unrealized_net_egp: float
    unrealized_net_percent: float
    breakeven_price: float
    exit_fees_egp: float
    tax_egp: float = 0.0

    @property
    def above_breakeven(self) -> bool:
        return self.price >= self.breakeven_price


def breakeven_price(position: Position, fee_model: FeeModel | None = None):
    """The price at which selling every share returns exactly what it cost.

    Not the average price. On a 100-share lot bought at 10.00 the average is
    10.04 once entry fees are in the basis, and the price that gets that money
    back after exit fees is 10.12 -- so "I am up 0.5%" at 10.09 is a loss.

    Only *billed* costs are solved for: commission, the flat order fee, and the
    gains tax when one is configured. Slippage and spread are excluded on
    purpose -- a breakeven has to be a price the user can put on a limit order,
    and neither of those is knowable at the moment the order is placed. The
    exit estimate charges them separately.
    """

    if not position.open:
        return None
    fees = fee_model or FeeModel()
    quantity = position.quantity
    basis = position.cost_basis_egp
    rate = fees.percent_per_side
    tax = fees.tax_rate
    # Solve  q*P - q*P*rate - order_fee - tax*(q*P - basis) = basis  for P.
    # The taxed branch assumes the breakeven exit is a gain against the basis,
    # which it always is while any fee is charged at all.
    denominator = quantity * (1.0 - rate - tax)
    if denominator <= 0:
        return None
    numerator = basis * (1.0 - tax) + fees.order_fee_egp
    return numerator / denominator


def value_position(position: Position, price, fee_model: FeeModel | None = None):
    """Value one open position at ``price``, after the cost of selling it.

    ``price`` is whatever the caller selected and is responsible for -- a live
    quote, a completed close, nothing at all. This function neither fetches nor
    judges it; it returns ``None`` when there is no price rather than valuing a
    position at its own cost, which would read on screen as "flat" when the
    truth is "unknown".
    """

    if not position.open or price is None:
        return None
    fees = fee_model or FeeModel()
    try:
        current = float(price)
    except (TypeError, ValueError):
        return None
    if current <= 0 or current != current:
        return None

    quantity = position.quantity
    basis = position.cost_basis_egp
    market_value = quantity * current
    exit_fees = fees.charge(market_value)
    gross = market_value - basis
    tax = fees.tax_rate * gross if gross > 0 else 0.0
    net_proceeds = market_value - exit_fees - tax
    net = net_proceeds - basis
    return PositionValue(
        symbol=position.symbol,
        quantity=quantity,
        average_price=basis / quantity,
        price=current,
        market_value_egp=market_value,
        net_proceeds_egp=net_proceeds,
        unrealized_gross_egp=gross,
        unrealized_net_egp=net,
        unrealized_net_percent=(net / basis * 100.0) if basis > 0 else 0.0,
        breakeven_price=breakeven_price(position, fees),
        exit_fees_egp=exit_fees,
        tax_egp=tax,
    )
