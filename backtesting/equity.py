"""The portfolio's equity path, and the drawdown read off it.

Until 2026-08-29 this class booked profit **only when a trade closed** and never
valued an open position. The curve therefore stepped once per exit, in exit-date
order, and could not fall while a position was open and losing. Every
`MaxDrawdown` this project has ever published — for the Daily Dashboard
strategy, for CONFIRMED_VOLUME_BREAKOUT, in every audit that quoted one — was a
*closed-trade* drawdown.

That understates the real figure, and it understates it **more the more
positions are held at once**: with fifteen open for twenty sessions, a
market-wide fall reaches the curve only as those positions close, spread over
the following month and netted against whatever opened meanwhile. It was found
while sweeping exactly that variable, where it produced the conclusion that
diversification was nearly free (`CAPACITY_IS_THE_CONSTRAINT.md`).

So the class now builds a **daily** curve when it is given prices: cash, plus
shares times that session's close for everything open. What it does *not* do is
quietly swap one number for another. Both are computed, both are reported, and
`basis()` says which one `max_drawdown()` returned. A drawdown that silently
changed meaning between two runs of the same code would be worse than the bug.

Without prices the behaviour is exactly as before, and `basis()` says
`CLOSED_TRADE` so a reader is never left guessing which number they have.
"""

from __future__ import annotations

import pandas as pd

#: What `max_drawdown()` was read off.
MARKED = "MARKED_TO_MARKET"
CLOSED_TRADE = "CLOSED_TRADE"


class EquityCurve:

    def __init__(
        self,
        trades,
        initial_capital=100000,
        profit_field="portfolio_profit",
        prices=None,
    ):

        # ==================================
        # ترتيب زمني إجباري حسب تاريخ الخروج
        # ==================================
        # لازم نرتب الصفقات بتاريخ الخروج (مش بترتيب الرموز
        # كما كانت مكتوبة فى الملف الأصلي) عشان منحنى رأس
        # المال يمثل تاريخ محفظة حقيقي.

        self.trades = sorted(
            trades,
            key=lambda t: t.exit_date
        )

        self.initial_capital = initial_capital

        self.profit_field = profit_field

        #: Daily closes, rows=session, columns=symbol. `backtesting/prices.py`
        #: builds one; `None` keeps the pre-2026-08-29 closed-trade behaviour.
        self.prices = prices if prices is not None and len(prices) else None

        self._daily = None

    # ==================================
    # Equity Curve (realised only, one step per exit)
    # ==================================

    def curve(self):

        equity = self.initial_capital

        values = [equity]

        for trade in self.trades:

            equity += getattr(trade, self.profit_field)

            values.append(round(equity, 2))

        return values

    # ==================================
    # Equity Curve (daily, open positions marked to market)
    # ==================================

    def daily_curve(self):
        """Cash plus the market value of everything open, every session.

        Returns an empty Series when there are no prices, which is the signal
        the drawdown methods use to fall back rather than to guess.
        """
        if self._daily is not None:
            return self._daily

        executed = [t for t in self.trades if getattr(t, "shares", 0)]
        if self.prices is None or not executed:
            self._daily = pd.Series(dtype="float64")
            return self._daily

        opens, exits = {}, {}
        for trade in executed:
            opens.setdefault(pd.Timestamp(trade.entry_date), []).append(trade)
            exits.setdefault(pd.Timestamp(trade.exit_date), []).append(trade)

        first = min(opens)
        last = max(exits)
        sessions = self.prices.index
        sessions = sessions[(sessions >= first) & (sessions <= last)]
        if len(sessions) == 0:
            self._daily = pd.Series(dtype="float64")
            return self._daily

        cash = float(self.initial_capital)
        # Keyed by identity, not by value: `Trade` is a dataclass and two
        # positions in the same symbol on the same dates compare equal, which
        # would close the wrong one.
        held = {}
        values = []
        for day in sessions:
            # Exits settle before entries on a date, matching
            # `PortfolioSimulator.run`.
            for trade in exits.get(day, ()):
                if held.pop(id(trade), None) is not None:
                    cash += (trade.shares * trade.entry_price
                             + getattr(trade, self.profit_field))
            for trade in opens.get(day, ()):
                cash -= trade.shares * trade.entry_price
                held[id(trade)] = trade

            marked = 0.0
            for trade in held.values():
                price = self._close(trade.symbol, day)
                # A symbol with no price for that session is carried at cost.
                # Marking it at a stale price would invent a move that did not
                # happen, and a suspended name is exactly when that matters.
                marked += trade.shares * (
                    price if price is not None else trade.entry_price)
            values.append(cash + marked)

        self._daily = pd.Series(values, index=sessions, dtype="float64")
        return self._daily

    def _close(self, symbol, day):
        if symbol not in self.prices.columns:
            return None
        value = self.prices.at[day, symbol]
        if value is None or pd.isna(value) or float(value) <= 0:
            return None
        return float(value)

    # ==================================
    # Which curve the drawdown came from
    # ==================================

    def basis(self):
        return MARKED if len(self.daily_curve()) else CLOSED_TRADE

    # ==================================
    # Max Drawdown (%)
    # ==================================

    def max_drawdown(self):
        """The deepest fall from a running peak, marked to market where possible."""
        daily = self.daily_curve()
        if len(daily):
            return round(-((daily / daily.cummax() - 1) * 100).min(), 2)
        return self.closed_trade_max_drawdown()

    def closed_trade_max_drawdown(self):
        """The pre-2026-08-29 figure, kept so published runs stay reproducible."""

        curve = self.curve()

        peak = curve[0]
        max_dd = 0

        for value in curve:

            if value > peak:
                peak = value

            if peak > 0:
                drawdown = ((peak - value) / peak) * 100
            else:
                drawdown = 0

            if drawdown > max_dd:
                max_dd = drawdown

        return round(max_dd, 2)

    # ==================================
    # Max Drawdown (بالجنيه - مطلوب لحساب Recovery Factor)
    # ==================================

    def max_drawdown_amount(self):

        daily = self.daily_curve()
        if len(daily):
            return round((daily.cummax() - daily).max(), 2)
        return self.closed_trade_max_drawdown_amount()

    def closed_trade_max_drawdown_amount(self):

        curve = self.curve()

        peak = curve[0]
        max_dd_amount = 0

        for value in curve:

            if value > peak:
                peak = value

            drawdown_amount = peak - value

            if drawdown_amount > max_dd_amount:
                max_dd_amount = drawdown_amount

        return round(max_dd_amount, 2)

    # ==================================
    # Final Capital
    # ==================================

    def final_capital(self):
        """Realised, deliberately.

        Every position is closed by the end of a backtest, so the marked curve
        agrees here — but only to within the last session's rounding, and this
        number feeds `total_return` and `CAGR`. It stays on the realised sum so
        those cannot drift with a price frame.
        """

        return self.curve()[-1]

    # ==================================
    # Total Return %
    # ==================================

    def total_return(self):

        final = self.final_capital()

        return round(
            (
                (final - self.initial_capital)
                / self.initial_capital
            ) * 100,
            2
        )
