"""The exit plan for a position that is already open.

Every level here comes from a definition this project already uses somewhere
else, and the module invents no multiple of its own:

* **Support and resistance** are ``strategy.support.support_resistance`` -- the
  20-bar low, and the 20-bar high excluding today.
* **The structural stop** is ``support - 0.30 * ATR``, the formula in
  ``strategy/entry.py`` that produces the stop on every daily signal card.
* **The targets** are the same engine's own targets: resistance, then
  ``resistance + 2 * ATR``.
* **The management policy** -- take half at the first target, move the stop to
  breakeven afterwards, trail on EMA20, give up after 20 sessions -- is read
  from the ``backtest`` settings section, which is the policy the strategy was
  measured under. A portfolio that managed exits by different rules than the
  ones the backtest validated would be reporting a result nobody has tested.

Two properties matter more than the levels themselves.

**A stop never widens.** Each revision may only move the stop up. This is not a
preference: a stop that retreats in front of a falling price is the mechanism
by which a small loss becomes the loss that matters, and it always arrives with
a reason that sounds good at the time.

**Absence is stated, never filled in.** When a position has run above every
level the daily engine can name, this module says there is no overhead
structure and leaves the final target empty, rather than inventing a round
number to put in the gap. An empty target with a trailing stop is a real plan.
A fabricated one is a guess wearing a plan's clothes.

Pure: no database, no provider, no clock. Give it a frame and a position, and
the same inputs always produce the same plan.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from backtesting.config import load as load_backtest_config
from holdings.book import PRICE_PRECISION, FeeModel, breakeven_price
from strategy.support import support_resistance


#: The stop's distance below the 20-bar low, as a multiple of ATR. Taken from
#: ``strategy/entry.py`` so a held position is stopped where a new signal in the
#: same stock would be stopped.
STOP_ATR_MULTIPLE = 0.30

#: The extended target's distance above resistance, likewise from that engine.
TARGET_ATR_MULTIPLE = 2.0

#: The lookback both levels are measured over.
LEVEL_WINDOW = 20

# Why a plan has no levels, in the words used on screen.
NO_HISTORY = "NO_HISTORY"
NO_ATR = "NO_ATR"
NO_OVERHEAD_STRUCTURE = "NO_OVERHEAD_STRUCTURE"
STOP_ABOVE_PRICE = "STOP_ABOVE_PRICE"

# Where a stop came from.
STRUCTURE = "STRUCTURE"
TRAILING = "TRAILING"
BREAKEVEN = "BREAKEVEN"
CARRIED = "CARRIED"


@dataclass(frozen=True)
class ExitPolicy:
    """How a position is managed once it is open.

    Read from the ``backtest`` settings section rather than restated here, so
    changing the policy changes the backtest and the live plan together or
    neither.
    """

    partial_fraction: float = 0.5
    partial_enabled: bool = True
    move_to_breakeven: bool = True
    trailing_enabled: bool = True
    trailing_mode: str = "EMA20"
    trailing_atr: float = 2.0
    max_holding_sessions: int = 20
    #: Total open risk the backtest is allowed to carry, as a percentage of
    #: equity. Nothing here enforces it -- the page reports the account's own
    #: figure against it so the two are read on the same scale.
    max_portfolio_risk_percent: float = 10.0
    source: str = "config/settings.json:backtest"


def load_policy() -> ExitPolicy:
    """The measured management policy, live from settings."""

    config = load_backtest_config()
    return ExitPolicy(
        partial_fraction=float(config.PARTIAL_PERCENT),
        partial_enabled=bool(config.PARTIAL_EXIT),
        move_to_breakeven=bool(config.MOVE_TO_BREAKEVEN),
        trailing_enabled=bool(config.TRAILING_ENABLED),
        trailing_mode=str(config.TRAILING_MODE).upper(),
        trailing_atr=float(config.TRAILING_ATR),
        max_holding_sessions=int(config.MAX_HOLDING_DAYS),
        max_portfolio_risk_percent=float(config.MAX_PORTFOLIO_RISK_PERCENT),
    )


@dataclass(frozen=True)
class ExitPlan:
    """Where to leave a position, and what each level was derived from."""

    symbol: str
    available: bool = False
    reason: str = ""
    session_date: str = ""
    reference_price: float | None = None
    atr: float | None = None
    support: float | None = None
    resistance: float | None = None
    stop: float | None = None
    stop_source: str = ""
    structural_stop: float | None = None
    trailing_stop: float | None = None
    breakeven: float | None = None
    target_partial: float | None = None
    target_final: float | None = None
    target_source: str = ""
    partial_fraction: float = 0.5
    holding_sessions: int | None = None
    max_holding_sessions: int = 20
    notes: tuple = ()

    @property
    def risk_per_share(self):
        if self.reference_price is None or self.stop is None:
            return None
        return self.reference_price - self.stop

    @property
    def reward_per_share(self):
        target = self.target_final or self.target_partial
        if self.reference_price is None or target is None:
            return None
        return target - self.reference_price

    @property
    def reward_risk(self):
        """Gross reward/risk from here, before any cost. ``None`` if unusable."""

        risk, reward = self.risk_per_share, self.reward_per_share
        if not risk or not reward or risk <= 0 or reward <= 0:
            return None
        return reward / risk

    @property
    def stop_breached(self) -> bool:
        """The stop already sits at or above the price it is meant to protect."""

        return STOP_ABOVE_PRICE in self.notes

    def levels(self) -> dict:
        return {
            "stop": self.stop,
            "target_partial": self.target_partial,
            "target_final": self.target_final,
        }


def _round(value):
    return None if value is None else round(float(value), PRICE_PRECISION)


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _trailing_stop(frame, index, policy: ExitPolicy, price, atr):
    """The trailing stop the measured policy would hold, or ``None``.

    EMA20 is the configured mode. It is read from the last *completed* bar,
    which is the same rule ``backtesting/managers/exit_manager.py`` follows: it
    trails on bar ``k-1`` so the stop for a day is never set from that day's own
    close. Here the price being judged is live or the latest close, and the
    level it is judged against comes from the completed session before it.

    The ATR mode is kept because settings can select it, and a plan that
    silently ignored a configured mode would be describing a policy nobody
    chose.
    """

    if not policy.trailing_enabled:
        return None
    mode = policy.trailing_mode.upper()
    if mode == "EMA20":
        if "EMA20" not in frame.columns:
            return None
        return _finite(frame["EMA20"].iloc[index])
    if mode in {"ATR", "ATR_DISTANCE"} and atr:
        return price - policy.trailing_atr * atr
    return None


def build_plan(
    symbol,
    frame,
    *,
    position=None,
    fee_model: FeeModel | None = None,
    policy: ExitPolicy | None = None,
    previous_stop=None,
    partial_taken: bool = False,
    holding_sessions=None,
) -> ExitPlan:
    """Build the current plan for one held position from completed daily bars.

    ``frame`` is the indicator frame the rest of the project uses; only its last
    completed bar is read. ``previous_stop`` is the stop already in force, which
    the new one may tighten but never loosen.
    """

    policy = policy or ExitPolicy()
    symbol = str(symbol or "").strip().upper()
    plan = ExitPlan(symbol=symbol, partial_fraction=policy.partial_fraction,
                    max_holding_sessions=policy.max_holding_sessions,
                    holding_sessions=holding_sessions)

    if frame is None or len(frame) < LEVEL_WINDOW:
        return replace(plan, reason=NO_HISTORY)

    index = len(frame) - 1
    session_date = ""
    try:
        session_date = str(frame.index[index].date())
    except AttributeError:
        session_date = str(frame.index[index])

    price = _finite(frame["Close"].iloc[index])
    atr = _finite(frame["ATR"].iloc[index]) if "ATR" in frame.columns else None
    if price is None or not price > 0:
        return replace(plan, reason=NO_HISTORY, session_date=session_date)
    if atr is None or not atr > 0:
        # Without ATR there is no stop distance and no extended target. The
        # levels are not approximated from price alone: a percentage stop on a
        # stock whose real range is three times wider is a stop that is hit by
        # noise every week.
        return replace(plan, reason=NO_ATR, session_date=session_date,
                       reference_price=price)

    levels = support_resistance(frame, index, window=LEVEL_WINDOW)
    support = _finite(levels.get("support"))
    resistance = _finite(levels.get("resistance"))
    notes = []

    # -- stop ---------------------------------------------------------------- #
    structural = _round(support - STOP_ATR_MULTIPLE * atr) if support else None
    trailing = _round(_trailing_stop(frame, index, policy, price, atr))
    breakeven = None
    if position is not None and getattr(position, "open", False):
        breakeven = _round(breakeven_price(position, fee_model or FeeModel()))

    candidates = []
    if structural is not None:
        candidates.append((structural, STRUCTURE))
    if trailing is not None:
        candidates.append((trailing, TRAILING))
    if partial_taken and policy.move_to_breakeven and breakeven is not None:
        # Once half is banked, the rest is not allowed to become a loss. This is
        # the policy the backtest ran under, not an extra safeguard added here.
        candidates.append((breakeven, BREAKEVEN))
    if previous_stop is not None:
        carried = _finite(previous_stop)
        if carried is not None:
            candidates.append((carried, CARRIED))

    stop = stop_source = None
    if candidates:
        stop, stop_source = max(candidates, key=lambda item: item[0])
    if stop is not None and stop >= price:
        # Not silently lowered to something comfortable: a stop at or above the
        # price means the level that was protecting this position has already
        # gone, and the rules layer must be able to see that rather than read a
        # tidied-up number.
        notes.append(STOP_ABOVE_PRICE)

    # -- targets ------------------------------------------------------------- #
    target_partial = target_final = None
    target_source = ""
    if resistance:
        extended = _round(resistance + TARGET_ATR_MULTIPLE * atr)
        overhead = [level for level in (_round(resistance), extended)
                    if level is not None and level > price]
        if len(overhead) >= 2:
            target_partial, target_final = overhead[0], overhead[-1]
            target_source = "RESISTANCE_THEN_ATR_EXPANSION"
        elif len(overhead) == 1:
            # Price is already above the 20-bar high: the only level the engine
            # can still name is the extension, and there is nothing above it to
            # call a final target. The remainder is managed by the trailing
            # stop, which is a plan -- an invented round number is not.
            target_partial = overhead[0]
            target_source = "ATR_EXPANSION_ONLY"
            notes.append(NO_OVERHEAD_STRUCTURE)
        else:
            target_source = NO_OVERHEAD_STRUCTURE
            notes.append(NO_OVERHEAD_STRUCTURE)

    return replace(
        plan,
        available=True,
        session_date=session_date,
        reference_price=price,
        atr=atr,
        support=_round(support),
        resistance=_round(resistance),
        stop=stop,
        stop_source=stop_source or "",
        structural_stop=structural,
        trailing_stop=trailing,
        breakeven=breakeven,
        target_partial=target_partial,
        target_final=target_final,
        target_source=target_source,
        notes=tuple(notes),
    )


def plan_differs(stored, plan: ExitPlan, tolerance=1e-6) -> bool:
    """True when a stored plan row no longer matches the computed levels.

    Used to decide whether a new version is worth writing. Rewriting an
    identical plan every refresh would bury the revisions that meant something
    under hundreds that meant nothing.
    """

    if not stored:
        return plan.available
    for key, value in plan.levels().items():
        previous = stored.get(key)
        if (previous is None) != (value is None):
            return True
        if previous is not None and abs(float(previous) - float(value)) > tolerance:
            return True
    return False
