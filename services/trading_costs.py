"""What a round trip costs, as one number a human can check.

The ORB engine sets both targets as pure multiples of the risk unit, whose only
floor is the minimum pullback depth. Nothing in the strategy compares either
target against the cost of trading, so it will happily report reward/risk 2.0
on a move that cannot pay its own commission. That is not a bug in the engine
-- 2.0 is what 2R means -- but a reader who is not shown the cost has no way to
tell a 1.6% target from a 0.4% one.

The dashboard already charged the quoted spread. It charged nothing else, while
`config/settings.json` carried a commission and a slippage figure that no part
of the scalping view read. On 2026-08-18, adding them changed the picture
completely: ten of thirteen signals lost money at their own first target.

Components, and why each is charged the way it is:

* **Spread** -- crossing it costs the full quoted width once over the round
  trip, not twice: you buy at the ask and sell at the bid.
* **Commission** -- charged on entry and on exit, matching how
  `services/ranking_robustness.py` prices a trade: ``(entry + exit) * rate``.
* **Slippage** -- charged on both sides for the same reason.
* **Tax on gains** -- structurally different from the rest. It applies to a
  profit, not to turnover, so it reduces a winning trade and does nothing to a
  losing one. Charging it as a flat percentage of price, the way the others
  work, would overstate the cost of every loss.

The tax rate ships unset. A rate is a fact about the reader's own account and
jurisdiction, and inventing a plausible-looking default would put a number on
screen that nobody chose and everybody would trust.

Read-only. Computes nothing the engine uses and filters nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: Where the rates live, so a reader who disagrees knows what to edit.
CONFIG_SECTION = "scalping"
CONFIG_KEYS = ("commission", "slippage", "capital_gains_tax_percent")


@dataclass(frozen=True)
class TradingCosts:
    """Per-side rates as fractions of price, plus a tax rate on realised gain.

    ``capital_gains_tax_percent`` is ``None`` when unset, which the caller must
    surface rather than silently treat as zero.

    ``rates_loaded`` says whether these came from settings or are the empty
    default. The distinction is not cosmetic: the first version of this loader
    called a function that did not exist, caught the ImportError, and returned
    zeros. Every cost on screen read 0.00% and every signal looked profitable.
    A cost model that fails to zero is worse than none, because it produces a
    confident wrong answer instead of an obvious gap.
    """

    commission_per_side: float = 0.0
    slippage_per_side: float = 0.0
    capital_gains_tax_percent: Optional[float] = None
    rates_loaded: bool = False
    load_error: Optional[str] = None
    #: Per-line fee percentages the commission was summed from, so a reader can
    #: check the total against a broker contract note line by line rather than
    #: trusting one aggregate number.
    fee_lines: tuple = ()
    #: Charged per order regardless of size, so its weight depends entirely on
    #: position size and it cannot be folded into a percentage.
    order_fee_egp: float = 0.0

    def order_fee_percent(self, position_egp: Optional[float]) -> Optional[float]:
        """The flat order fee as a percentage of a given position, both sides."""

        if not position_egp or not self.order_fee_egp:
            return None
        return self.order_fee_egp * 2.0 / float(position_egp) * 100.0

    @property
    def round_trip_percent(self) -> float:
        """Turnover cost as a percentage of price, excluding spread and tax."""

        return (self.commission_per_side + self.slippage_per_side) * 2.0 * 100.0

    @property
    def tax_configured(self) -> bool:
        return self.capital_gains_tax_percent is not None

    def total_percent(self, spread_percent: Optional[float]) -> Optional[float]:
        """Everything charged on turnover, or ``None`` if the spread is unknown.

        An unmeasured spread makes the total unknown, not smaller. Substituting
        zero would produce a confident understatement of cost on exactly the
        signals where the book was never observed -- and a reader comparing a
        complete row against a silently partial one has no way to tell.
        """

        if spread_percent is None:
            return None
        return self.round_trip_percent + float(spread_percent)

    def net_target_percent(
        self, move_percent: Optional[float], spread_percent: Optional[float]
    ) -> Optional[float]:
        """What is left of a move to target after costs, and after tax on it.

        Returns ``None`` when the move is unknown. A negative result means the
        target does not pay for reaching it.
        """

        total = self.total_percent(spread_percent)
        if move_percent is None or total is None:
            return None
        net = float(move_percent) - total
        if net > 0 and self.capital_gains_tax_percent:
            net *= 1.0 - (self.capital_gains_tax_percent / 100.0)
        return net

    def net_reward_risk(
        self,
        trigger: Optional[float],
        stop: Optional[float],
        target: Optional[float],
        spread_percent: Optional[float],
    ) -> Optional[float]:
        """Reward/risk after every cost, or ``None`` when inputs are missing.

        Costs widen the loss and shrink the gain by the same turnover amount;
        tax then applies only to what is left of the gain. Returns ``None``
        rather than a negative ratio when the costs have eaten the whole move,
        because a caller that prints "-0.4" invites reading it as a small loss
        when it means the trade cannot win.
        """

        total = self.total_percent(spread_percent)
        if None in (trigger, stop, target) or total is None:
            return None
        risk = trigger - stop
        reward = target - trigger
        if risk <= 0 or reward <= 0:
            return None

        turnover = trigger * (total / 100.0)
        net_gain = reward - turnover
        if net_gain > 0 and self.capital_gains_tax_percent:
            net_gain *= 1.0 - (self.capital_gains_tax_percent / 100.0)
        net_loss = risk + turnover
        if net_loss <= 0 or net_gain <= 0:
            return None
        return net_gain / net_loss


def load_trading_costs(section: Optional[dict] = None) -> TradingCosts:
    """Read the rates from the ``scalping`` settings section.

    Never raises -- a dashboard should not die over a config read -- but never
    pretends either: a failure returns rates that report themselves as not
    loaded, carrying the reason, so the caller shows a gap instead of a zero.
    """

    error = None
    if section is None:
        try:
            from config.settings_manager import settings as settings_manager

            section = settings_manager.get(CONFIG_SECTION)
        except Exception as failure:                             # noqa: BLE001
            error = f"{type(failure).__name__}: {str(failure)[:120]}"
            section = None

    if not isinstance(section, dict):
        return TradingCosts(
            rates_loaded=False,
            load_error=error or f"settings section '{CONFIG_SECTION}' is missing",
        )

    def rate(name: str) -> tuple[float, bool]:
        raw = section.get(name)
        if raw is None:
            return 0.0, False
        try:
            return max(0.0, float(raw)), True
        except (TypeError, ValueError):
            return 0.0, False

    # A fee schedule, when present, is the authority: it is transcribed from a
    # broker contract note and each line can be checked against it. The scalar
    # `commission` is kept as the fallback for callers that never had one.
    schedule = section.get("fee_schedule")
    fee_lines: tuple = ()
    order_fee = 0.0
    commission, has_commission = rate("commission")

    if isinstance(schedule, dict):
        lines = []
        for key, value in schedule.items():
            if key.startswith("_") or not key.endswith("_percent"):
                continue
            try:
                lines.append((key[: -len("_percent")], max(0.0, float(value))))
            except (TypeError, ValueError):
                continue
        if lines:
            fee_lines = tuple(lines)
            commission = sum(percent for _, percent in lines) / 100.0
            has_commission = True
        try:
            order_fee = max(0.0, float(schedule.get("order_fee_egp") or 0.0))
        except (TypeError, ValueError):
            order_fee = 0.0

    slippage, _ = rate("slippage")

    tax = section.get("capital_gains_tax_percent")
    try:
        tax = None if tax is None else max(0.0, float(tax))
    except (TypeError, ValueError):
        tax = None

    # Commission is the term that dominates; without it the total is not worth
    # displaying as a cost at all.
    return TradingCosts(
        commission_per_side=commission,
        slippage_per_side=slippage,
        capital_gains_tax_percent=tax,
        rates_loaded=has_commission,
        load_error=None if has_commission else "no 'commission' rate in settings",
        fee_lines=fee_lines,
        order_fee_egp=order_fee,
    )
