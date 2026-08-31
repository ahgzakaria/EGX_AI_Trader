"""What to do about a held position right now, and why.

One recommendation per position, from the first rule that fires. The order is
the point: a breached stop is not a matter of opinion, and a liquidity reading
must never speak over it.

The rules divide into two kinds, and the difference is not cosmetic.

**Measured rules** -- stop, targets, trend break, time stop -- come from the
policy the swing strategy was backtested under. They fire on their own terms.

**Unmeasured rules** -- the liquidity ones -- read evidence nobody on this
project has yet validated as an exit signal. They are constrained accordingly:
they may bank a profit that already exists, and they may tighten a stop. They
may never sell into a loss. The reason is arithmetic, not caution: a round trip
on EGX costs 0.46% in fees plus the spread, against an average intraday move of
0.078%. This project already retired an entire workspace over that ratio. A
rule that pays that toll on a hunch will lose money slowly and look busy doing
it.

Every recommendation this module produces is logged with its evidence, so after
enough sessions the unmeasured rules can be judged on what they did rather than
on how convincing they sound. Until then they are labelled as what they are.

Nothing here places an order. The output is a sentence and a number, and the
decision stays with the person reading it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from holdings.book import FeeModel, value_position
from holdings.plan import TRAILING as TRAILING_SOURCE, ExitPlan


# Actions, in the vocabulary the page renders.
WITHHELD = "WITHHELD"
HOLD = "HOLD"
WATCH = "WATCH"
RAISE_STOP = "RAISE_STOP"
TRIM = "TRIM"
EXIT = "EXIT"

# How soon the action matters.
NOW = "NOW"
TODAY = "TODAY"
SOON = "SOON"
NONE = "NONE"

# Rule names. Stored, so they stay stable and countable.
NO_PRICE = "NO_PRICE"
NO_PLAN = "NO_PLAN"
STOP_BREACHED = "STOP_BREACHED"
TREND_BREAK = "TREND_BREAK"
TARGET_FINAL_REACHED = "TARGET_FINAL_REACHED"
TARGET_PARTIAL_REACHED = "TARGET_PARTIAL_REACHED"
TIME_STOP = "TIME_STOP"
LIQUIDITY_LEAVING = "LIQUIDITY_LEAVING"
EXPANSION = "EXPANSION"
ON_PLAN = "ON_PLAN"

#: Rules whose exit evidence has not been measured on this market. They can
#: bank an existing profit or tighten a stop; they can never realize a loss.
UNMEASURED_RULES = (LIQUIDITY_LEAVING, EXPANSION)

#: Below its own trailing 20-session average volume. The floor the scalping
#: settings already use to call a name tradeable at all.
NORMAL_RELATIVE_VOLUME = 1.0

#: Sector strength of a sector trading at its own median turnover.
#: ``sector_flow.strength`` maps RVOL 1.0 to exactly this.
NORMAL_SECTOR_STRENGTH = 0.5

#: Twice a stock's own norm -- ``sector_flow.strength.FULL_STRENGTH_RVOL``.
EXPANSION_RELATIVE_VOLUME = 2.0


@dataclass(frozen=True)
class Evidence:
    """Everything outside the plan that a rule is allowed to read.

    Every field may be ``None``. A rule that needs a missing field does not
    fire; it never substitutes a default. An absent sector reading is not a
    weak sector.
    """

    relative_volume: float | None = None
    sector: str = ""
    sector_strength: float | None = None
    #: Forecast change in this sector's share of turnover for the rest of
    #: today, from ``sector_flow.intraday``. Negative means money is expected to
    #: leave the sector before the close.
    sector_intraday_change: float | None = None
    spread_percent: float | None = None
    quote_freshness: str = ""
    session_phase: str = ""
    new_twenty_day_high: bool | None = None

    @property
    def sector_weak(self):
        """Is the sector losing money right now? ``None`` when unmeasured.

        The intraday forecast wins when it exists: during an open session it
        answers the same question about the hours that are still to come,
        while the daily strength answers it about a session that has ended.
        """

        if self.sector_intraday_change is not None:
            return self.sector_intraday_change < 0
        if self.sector_strength is not None:
            return self.sector_strength < NORMAL_SECTOR_STRENGTH
        return None

    def as_dict(self) -> dict:
        return {
            "relative_volume": self.relative_volume,
            "sector": self.sector,
            "sector_strength": self.sector_strength,
            "sector_intraday_change": self.sector_intraday_change,
            "spread_percent": self.spread_percent,
            "quote_freshness": self.quote_freshness,
            "session_phase": self.session_phase,
            "new_twenty_day_high": self.new_twenty_day_high,
        }


@dataclass(frozen=True)
class Recommendation:
    """One reading of one position, with the evidence that produced it."""

    symbol: str
    action: str
    rule: str
    urgency: str = NONE
    reason_ar: str = ""
    reason_en: str = ""
    price: float | None = None
    price_basis: str = ""
    quantity: float | None = None
    net_percent: float | None = None
    net_egp: float | None = None
    measured: bool = True
    evidence: dict = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.action in (TRIM, EXIT, RAISE_STOP)


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _percent(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


def evaluate(
    position,
    plan: ExitPlan | None,
    *,
    price=None,
    price_basis="",
    evidence: Evidence | None = None,
    fee_model: FeeModel | None = None,
    partial_taken: bool = False,
    holding_sessions=None,
) -> Recommendation:
    """Return the single recommendation for one position.

    ``price`` is the caller's selected comparison price and its ``price_basis``
    says where it came from. When there is none, the answer is WITHHELD -- not a
    guess from the last close, which during an open session is a price that no
    longer exists.
    """

    symbol = getattr(position, "symbol", "") or getattr(plan, "symbol", "")
    evidence = evidence or Evidence()
    fees = fee_model or FeeModel()
    current = _finite(price)

    def build(action, rule, urgency, reason_ar, reason_en, **extra):
        return Recommendation(
            symbol=symbol,
            action=action,
            rule=rule,
            urgency=urgency,
            reason_ar=reason_ar,
            reason_en=reason_en,
            price=current,
            price_basis=str(price_basis or ""),
            quantity=getattr(position, "quantity", None),
            measured=rule not in UNMEASURED_RULES,
            evidence={**evidence.as_dict(), **extra},
        )

    if current is None or current <= 0:
        return build(
            WITHHELD, NO_PRICE, NONE,
            "لا يوجد سعر حالي موثوق — لا توصية.",
            "No trustworthy current price; no recommendation is issued.",
        )

    value = value_position(position, current, fees)
    net_percent = value.unrealized_net_percent if value else None
    net_egp = value.unrealized_net_egp if value else None
    above_breakeven = bool(value and value.above_breakeven)

    def decided(action, rule, urgency, reason_ar, reason_en, **extra):
        return replace(
            build(action, rule, urgency, reason_ar, reason_en, **extra),
            net_percent=net_percent, net_egp=net_egp,
        )

    if plan is None or not plan.available:
        return decided(
            WATCH, NO_PLAN, NONE,
            "لا توجد خطة خروج محسوبة لهذا السهم (بيانات يومية غير كافية).",
            f"No exit plan could be built ({getattr(plan, 'reason', 'unknown')}).",
        )

    # -- measured rules, in the only order they can safely run in ------------ #

    if plan.stop is not None and current <= plan.stop:
        # One exit, two names. Both mean leave now, but the cause is what gets
        # counted later: a price that fell to a structural stop and a trend that
        # rose past its own moving average are different failures, and lumping
        # them under one rule name would make the outcome log unreadable.
        if plan.stop_source == TRAILING_SOURCE:
            return decided(
                EXIT, TREND_BREAK, NOW,
                f"السعر {current:.3f} تحت المتوسط الذي تتحرك عليه الخطة "
                f"({plan.stop:.3f}) — الاتجاه انكسر، خروج كامل.",
                f"Price {current:.3f} is below the moving average the plan "
                f"trails on ({plan.stop:.3f}); the trend has broken.",
                stop=plan.stop, trailing_stop=plan.trailing_stop,
            )
        return decided(
            EXIT, STOP_BREACHED, NOW,
            f"السعر {current:.3f} عند وقف الخسارة {plan.stop:.3f} أو تحته — خروج كامل.",
            f"Price {current:.3f} is at or below the stop {plan.stop:.3f}.",
            stop=plan.stop,
        )

    if plan.target_final is not None and current >= plan.target_final:
        return decided(
            EXIT, TARGET_FINAL_REACHED, TODAY,
            f"السعر وصل الهدف النهائي {plan.target_final:.3f} — إغلاق ما تبقى.",
            f"Price reached the final target {plan.target_final:.3f}.",
            target=plan.target_final,
        )

    if (
        plan.target_partial is not None
        and current >= plan.target_partial
        and not partial_taken
    ):
        fraction = plan.partial_fraction
        return decided(
            TRIM, TARGET_PARTIAL_REACHED, TODAY,
            f"السعر وصل هدف الجني الجزئي {plan.target_partial:.3f} — "
            f"بيع {fraction * 100:.0f}% ونقل الوقف لسعر التعادل.",
            f"Price reached the partial target {plan.target_partial:.3f}; "
            f"take {fraction * 100:.0f}% and move the stop to breakeven.",
            target=plan.target_partial, partial_fraction=fraction,
        )

    sessions = holding_sessions if holding_sessions is not None else plan.holding_sessions
    if (
        sessions is not None
        and sessions >= plan.max_holding_sessions
        and not partial_taken
    ):
        return decided(
            EXIT, TIME_STOP, TODAY,
            f"مر {int(sessions)} جلسة دون بلوغ الهدف الأول — "
            f"السياسة المختبرة تغلق المركز بعد {plan.max_holding_sessions}.",
            f"Held {int(sessions)} sessions without reaching the first target; "
            f"the measured policy closes after {plan.max_holding_sessions}.",
            holding_sessions=sessions,
        )

    # -- unmeasured rules, allowed only to bank a profit or tighten ---------- #

    volume = _finite(evidence.relative_volume)
    strength = _finite(evidence.sector_strength)
    sector_weak = evidence.sector_weak
    # Both the stock and its sector, never one alone. A quiet stock inside a
    # busy sector is a stock nobody traded today; money leaving the sector
    # around a stock that is still being traded is not money leaving the stock.
    # Requiring both is what keeps this rule from firing on ordinary noise.
    liquidity_leaving = (
        volume is not None and sector_weak is True
        and volume < NORMAL_RELATIVE_VOLUME
    )

    if liquidity_leaving and above_breakeven:
        return decided(
            EXIT, LIQUIDITY_LEAVING, TODAY,
            f"السيولة تخرج من السهم ({volume:.2f}× من معدله) ومن قطاعه "
            f"({evidence.sector or 'غير معروف'}) — والمركز رابح "
            f"{_percent(net_percent)}% بعد التكاليف، فالخروج يحفظ ربحًا قائمًا.",
            f"Liquidity is leaving the stock ({volume:.2f}x its own average) and "
            f"its sector; the position is {_percent(net_percent)}% net ahead, so "
            f"exiting banks a profit that already exists.",
            relative_volume=volume, sector_strength=strength,
        )

    if liquidity_leaving:
        decider = (
            f"الوقف عند {plan.stop:.3f} هو ما يقرر."
            if plan.stop is not None else "لا يوجد وقف محسوب لهذا السهم."
        )
        return decided(
            RAISE_STOP, LIQUIDITY_LEAVING, SOON,
            f"السيولة تخرج من السهم ({volume:.2f}× من معدله) ومن قطاعه، "
            f"لكن المركز ما زال دون سعر التعادل — لا بيع بالخسارة على إشارة "
            f"سيولة غير مُقاسة. {decider}",
            "Liquidity is leaving the stock and its sector, but the position is "
            "below breakeven; an unmeasured liquidity reading does not realize a "
            "loss. The stop decides.",
            relative_volume=volume, sector_strength=strength,
        )

    expanding = (
        volume is not None
        and volume >= EXPANSION_RELATIVE_VOLUME
        and bool(evidence.new_twenty_day_high)
        and sector_weak is not True
    )
    if expanding:
        return decided(
            RAISE_STOP, EXPANSION, SOON,
            f"قمة جديدة بحجم {volume:.2f}× من معدل السهم — "
            "الهدف يتحرك لأعلى مع المقاومة الجديدة، والوقف يضيق ولا يتسع.",
            f"A new 20-day high on {volume:.2f}x average volume; the target moves "
            "up with the new resistance and the stop tightens, never widens.",
            relative_volume=volume, sector_strength=strength,
        )

    # -- nothing to do ------------------------------------------------------- #

    if not above_breakeven:
        return decided(
            HOLD, ON_PLAN, NONE,
            f"المركز ضمن الخطة لكنه تحت سعر التعادل "
            f"{value.breakeven_price:.3f} — البيع الآن خسارة بعد التكاليف.",
            "On plan, but below breakeven: selling now is a loss after costs.",
            breakeven=value.breakeven_price if value else None,
        )

    stop_text = f"الوقف {plan.stop:.3f}" if plan.stop is not None else "لا يوجد وقف محسوب"
    target_text = (
        f" والهدف {plan.target_partial:.3f}." if plan.target_partial is not None
        else "، ولا يوجد مستوى مقاومة أعلى من السعر ليكون هدفًا."
    )
    return decided(
        HOLD, ON_PLAN, NONE,
        f"المركز ضمن الخطة — {stop_text}{target_text}",
        "On plan; no level has been reached.",
    )
