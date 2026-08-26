"""What a backtested round trip costs.

Until 2026-08-26 this charged a 0.30% commission per side and 0.05% of
slippage, 0.703% over a round trip, and **nothing at all for the spread**. Both
halves of that were wrong, in opposite directions:

* The broker's own contract note (`config/settings.json -> scalping.fee_schedule`,
  notional 102,560 EGP) totals **0.1819% per side** -- brokerage and custody,
  stamp duty, EGX, MCDR, FRA and risk insurance. The modelled 0.30% was 65% too
  high.
* The spread was free. Measured from live quotes across the 170 of 172 symbols
  the backtest actually traded that have them, the trade-weighted median is
  **0.500%**.

The two errors nearly cancelled -- 0.703% modelled against 0.864% measured --
which is why nothing looked obviously broken. The residual 0.161% per round trip
was still about a third of the strategy's reported per-trade edge, and larger
than most of the effects being compared against each other.

How each component is charged, following `services/trading_costs.py`:

* **Spread** costs the full quoted width **once** over the round trip, not
  twice: you buy at the ask and sell at the bid. Charged here as half a width on
  each fill, which sums to one width.
* **Commission** is charged on entry and on exit, as ``(entry + exit) * rate``.
* **Slippage** is charged on both sides. It is retained at its previous value
  and is *not* part of the 0.864% measurement, which priced the quoted spread
  only. Fill imperfection beyond the quote is real but unmeasured here, so the
  total lands at 0.964% -- deliberately the conservative side of what was
  measured rather than the flattering one.

The spread is a single universe-level figure rather than per-symbol. Per-symbol
would be better and is measurable, but `TradingCosts` is constructed without a
symbol in `backtesting/engine.py`, which is a sealed release file. Charging the
median to every symbol understates cost on wide names and overstates it on tight
ones; the measured spread distribution is in `SWING_STRATEGY_PROBE.md`.
"""

from backtesting.config import load as load_backtest_config


class TradingCosts:

    def __init__(
        self,
        commission=None,
        slippage=None,
        spread_percent=None
    ):

        """
        Trading Costs Configuration

        commission     = 0.001819 -> 0.1819% per side (broker contract note)
        slippage       = 0.0005   -> 0.05% per side
        spread_percent = 0.50     -> 0.50% full width, charged once per round trip

        لو محددتش القيم صراحة، بيتقروا Live من settings.json وقت إنشاء
        الكلاس (مش وقت استيراد الملف).
        """

        cfg = load_backtest_config()

        self.commission = (
            commission
            if commission is not None
            else cfg.COMMISSION
        )

        self.slippage = (
            slippage
            if slippage is not None
            else cfg.SLIPPAGE
        )

        spread = (
            spread_percent
            if spread_percent is not None
            else getattr(cfg, "SPREAD_PERCENT", 0.0)
        )

        # Half a width on each fill, so the round trip pays one full width.
        self.half_spread = float(spread) / 100.0 / 2.0

    # ==================================
    # Round Trip Cost (for reporting and tests)
    # ==================================

    def round_trip_percent(self):

        """Total cost of a round trip as a percentage of price.

        Commission is charged on both legs, slippage on both, and the spread
        once. Kept as an explicit method so a reader can check the number
        against a contract note without reverse-engineering the fills.
        """

        return round(

            100.0 * (
                2 * self.commission
                + 2 * self.slippage
                + 2 * self.half_spread
            ),

            4

        )

    # ==================================
    # Entry Execution Price
    # ==================================

    def entry_price(self, price):

        return round(

            price *

            (1 + self.slippage + self.half_spread),

            4

        )

    # ==================================
    # Exit Execution Price
    # ==================================

    def exit_price(self, price):

        return round(

            price *

            (1 - self.slippage - self.half_spread),

            4

        )

    # ==================================
    # Commission Cost
    # ==================================

    def commission_cost(

        self,

        entry_price,

        exit_price

    ):

        return round(

            (entry_price + exit_price)

            * self.commission,

            4

        )

    # ==================================
    # Gross Profit
    # ==================================

    def gross_profit(

        self,

        entry_price,

        exit_price

    ):

        return round(

            exit_price - entry_price,

            2

        )

    # ==================================
    # Net Profit
    # ==================================

    def net_profit(

        self,

        entry_price,

        exit_price

    ):

        gross = self.gross_profit(

            entry_price,

            exit_price

        )

        commission = self.commission_cost(

            entry_price,

            exit_price

        )

        return round(

            gross - commission,

            2

        )
