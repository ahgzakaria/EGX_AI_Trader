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

The spread is charged **per symbol** where one has been measured. Real EGX
spreads run from 0.036% on COMI to 4.4% on the thinnest names, so a flat rate is
simultaneously too harsh on the liquid ones and too generous on the illiquid
ones -- and no universe filter can be evaluated while every symbol costs the
same. The table is `data/universe/egx_spread_table.csv`, built by
`scripts/research/build_spread_table.py`; symbols absent from it fall back to the
configured flat `spread_percent` rather than being guessed at.

Symbols with too few quotes to measure are charged `unmeasured_spread_percent`,
the 90th percentile of the measured distribution, rather than the median. They
quote thinner than the thinnest measured name, and thin quoting predicts a wide
spread -- across the 217 measured symbols the correlation between quote count
and spread is -0.445, the least-quoted quartile sitting at a 0.652% median
against 0.188% for the most-quoted. Charging them the median would flatter the
only names nobody has observed.
"""

import csv
from pathlib import Path

from backtesting.config import load as load_backtest_config

#: Measured medians, loaded once. `None` until first use; `{}` if the table is
#: missing, which is a fallback rather than an error -- a run without the table
#: charges the flat rate and is merely less precise, not wrong.
_SPREAD_TABLE = None
SPREAD_TABLE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "universe" / "egx_spread_table.csv"
)


def spread_table():
    global _SPREAD_TABLE
    if _SPREAD_TABLE is None:
        table = {}
        try:
            with SPREAD_TABLE_PATH.open(encoding="utf-8") as handle:
                rows = [line for line in handle if not line.startswith("#")]
            for row in csv.DictReader(rows):
                try:
                    table[row["ticker"].strip().upper()] = float(
                        row["median_spread_percent"])
                except (KeyError, TypeError, ValueError):
                    continue
        except OSError:
            table = {}
        _SPREAD_TABLE = table
    return _SPREAD_TABLE


def reset_spread_table():
    """Drop the cache so a rebuilt table is picked up without a restart."""
    global _SPREAD_TABLE
    _SPREAD_TABLE = None


class TradingCosts:

    def __init__(
        self,
        commission=None,
        slippage=None,
        spread_percent=None,
        symbol=None
    ):

        """
        Trading Costs Configuration

        commission     = 0.001819 -> 0.1819% per side (broker contract note)
        slippage       = 0.0005   -> 0.05% per side
        spread_percent = 0.50     -> flat fallback, full width once per round trip
        symbol         = "COMI.CA" -> charge this symbol's measured spread instead

        Resolution order: an explicit `spread_percent` always wins, so a caller
        asking for zero costs gets zero costs; then the measured table; then the
        conservative rate for symbols too thinly quoted to measure; then the
        flat default if no conservative rate is configured.

        لو محددتش القيم صراحة، بيتقروا Live من settings.json وقت إنشاء
        الكلاس (مش وقت استيراد الملف).
        """

        cfg = load_backtest_config()
        self.symbol = symbol

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

        if spread_percent is not None:
            spread = spread_percent
            self.spread_source = "explicit"
        else:
            measured = None
            if symbol:
                key = str(symbol).strip().upper()
                measured = spread_table().get(key)
                if measured is None and key.endswith(".CA"):
                    measured = spread_table().get(key[:-3])
            if measured is not None:
                spread, self.spread_source = measured, "measured"
            else:
                # An unmeasured symbol is not an average symbol. It has fewer
                # than 50 quotes across the dense era, which makes it thinner
                # than the thinnest symbol that *is* measured (241 quotes), and
                # thin quoting predicts a wide spread: across the 217 measured
                # names the correlation between quote count and spread is
                # -0.445, with the least-quoted quartile at a 0.652% median
                # against 0.188% for the most-quoted.
                #
                # So charging it the universe median flatters it. The fallback
                # is the 90th percentile of the measured distribution instead.
                # When the alternative is guessing about something unobserved,
                # the conservative side is the correct one.
                spread = getattr(cfg, "UNMEASURED_SPREAD_PERCENT", None)
                if spread is None:
                    spread = getattr(cfg, "SPREAD_PERCENT", 0.0)
                    self.spread_source = "flat_default"
                else:
                    self.spread_source = "unmeasured_conservative"

        self.spread_percent = float(spread)
        # Half a width on each fill, so the round trip pays one full width.
        self.half_spread = self.spread_percent / 100.0 / 2.0

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
