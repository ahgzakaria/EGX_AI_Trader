"""Display-layer target-state classification. Calculates no strategy levels.

The problem this solves, observed on SAUD.CA: Stock Details showed
``Current 22.11`` beside ``Target 1 22.10`` as if 22.10 were still a future
long target, and reported a risk/reward of 0.43 measured against it. The price
had already passed that level, so the target was history, not a plan.

This module answers one question — *is this level still ahead of price?* — and
nothing else:

  * it never recomputes a target, stop, entry or the frozen decision trace;
  * it compares **full-precision** values, never rounded display strings, so a
    level that only *looks* reached after rounding stays ACTIVE;
  * it is direction-aware, because a target below price is normal for a short;
  * when the timestamps behind the price and the levels cannot be compared, it
    refuses to judge and reports ``STALE_LEVEL`` instead of guessing.

Any risk/reward or remaining-room figure it returns is explicitly *display
derived* for the next still-active target. The original frozen figures stay
untouched and must continue to be shown as the decision trace recorded them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path

import pandas as pd

from core.egx_session import (
    AUCTION_END,
    CONTINUOUS_CLOSE,
    REGULAR_OPEN,
    cairo_now,
    egx_session_phase,
)
# Target states.
ACTIVE = "ACTIVE"
REACHED = "REACHED"
PREVIOUSLY_REACHED = "PREVIOUSLY_REACHED"
STALE_LEVEL = "STALE_LEVEL"

# Scenario direction.
LONG = "LONG"
SHORT = "SHORT"

# Float-equality guard only. This is NOT a tolerance band: it exists so that
# 22.10 compared against a value that is 22.10 to the last representable bit is
# treated as equal. It is far below any EGX tick size, so it can never turn a
# genuinely unreached level into a reached one.
PRICE_EPSILON = 1e-9

# A caller may opt into an age limit.  There is deliberately no default expiry:
# the frozen strategy has no time-expiry rule, and inventing one here would be a
# strategy change.  Timestamp ordering and missing timestamps are still enforced.
DEFAULT_MAX_SNAPSHOT_AGE_HOURS = None

# Price basis labels.
BASIS_LIVE = "LIVE"
BASIS_COMPLETED_CLOSE = "COMPLETED_SESSION_CLOSE"
BASIS_FROZEN_CLOSE = "FROZEN_CLOSE"

# Frozen Classic engine identity.  These files are read only to fingerprint the
# exact implementation; the fingerprint never participates in a decision.
_ROOT = Path(__file__).resolve().parents[1]
_LEVEL_ENGINE_FILES = (
    _ROOT / "strategy" / "decision_engine.py",
    _ROOT / "strategy" / "entry.py",
    _ROOT / "strategy" / "support.py",
)
_RESULT_FIELDS = (
    "Signal", "Support", "Resistance", "BuyLow", "BuyHigh", "StopLoss",
    "Target1", "Target2", "RR", "Score", "Confidence", "Regime",
    "IndexRegime",
)
_FRAME_FIELDS = (
    "Open", "High", "Low", "Close", "Adj Close", "Volume", "EMA20",
    "EMA50", "EMA200", "ATR", "RSI", "MACD",
)


def _number(value):
    """Return ``value`` as a float, or ``None`` when it is not a usable number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number or number <= 0 else number


def _timestamp(value):
    """Return a Timestamp without inventing timezone provenance."""
    if value in (None, ""):
        return None
    try:
        moment = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if moment is pd.NaT:
        return None
    return moment


def _comparable_timestamps(left, right):
    """Normalize a timestamp pair, rejecting naive/aware mixing explicitly."""

    first, second = _timestamp(left), _timestamp(right)
    if first is None or second is None:
        return first, second, "missing timestamp"
    first_aware = first.tzinfo is not None
    second_aware = second.tzinfo is not None
    if first_aware != second_aware:
        return first, second, "naive and timezone-aware timestamps are incompatible"
    if first_aware:
        first, second = first.tz_convert("UTC"), second.tz_convert("UTC")
    return first, second, ""


def _auction_aware_phase(value, holidays=None):
    """Resolve the comparison phase from the centralized EGX calendar."""

    moment = _timestamp(value)
    if moment is None or moment.tzinfo is None:
        return "UNKNOWN"
    local = cairo_now(moment.to_pydatetime())
    base = egx_session_phase(local, holidays=holidays)
    if base in {"WEEKEND", "HOLIDAY"}:
        return base
    clock = local.timetz().replace(tzinfo=None)
    if clock < REGULAR_OPEN:
        return "PRE_OPEN"
    if clock < CONTINUOUS_CLOSE:
        return "CONTINUOUS"
    if clock < AUCTION_END:
        return "CLOSING_AUCTION"
    return "CLOSED"


@lru_cache(maxsize=1)
def classic_levels_engine_version() -> str:
    """Content-derived version of the unchanged Classic level engine."""

    digest = hashlib.sha256()
    for path in _LEVEL_ENGINE_FILES:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"classic_levels@sha256:{digest.hexdigest()[:16]}"


def classic_snapshot_evidence_hash(symbol: str, frame, result: dict) -> str:
    """Fingerprint frozen evidence/result without modifying either object."""

    columns = [name for name in _FRAME_FIELDS if name in frame.columns]
    evidence = frame.loc[:, columns].tail(250).copy()
    evidence.index = pd.DatetimeIndex(pd.to_datetime(evidence.index))
    frame_hash = hashlib.sha256(
        pd.util.hash_pandas_object(evidence, index=True).values.tobytes()
    ).hexdigest()
    payload = {
        "symbol": str(symbol).upper(),
        "engine": classic_levels_engine_version(),
        "last_completed_session": (
            pd.Timestamp(evidence.index[-1]).isoformat() if len(evidence) else None
        ),
        "frame_hash": frame_hash,
        "result": {
            name: _json_value(result.get(name)) for name in _RESULT_FIELDS
        },
    }
    encoded = json.dumps(
        payload, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _json_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


# --------------------------------------------------------------------------- #
# Staleness
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SnapshotStaleness:
    """Whether a frozen level set may be compared with a given price at all."""
    stale: bool
    reason: str
    signal_timestamp: object = None
    price_timestamp: object = None
    age_hours: float | None = None

    @property
    def comparable(self) -> bool:
        return not self.stale


def assess_staleness(signal_timestamp=None, price_timestamp=None, *,
                     max_age_hours: float | None = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
                     stale_reason: str = "",
                     ) -> SnapshotStaleness:
    """Decide whether frozen levels and a price may be compared.

    Incomparable timestamps (either missing, or unparsable) are treated as stale:
    the honest answer is "we cannot tell", never a silent comparison.
    """
    signal, price, timestamp_error = _comparable_timestamps(
        signal_timestamp, price_timestamp
    )
    if stale_reason:
        return SnapshotStaleness(True, str(stale_reason), signal, price, None)
    if signal is None or price is None:
        missing = []
        if signal is None:
            missing.append("signal timestamp")
        if price is None:
            missing.append("price timestamp")
        return SnapshotStaleness(True, f"incomparable: missing {' and '.join(missing)}",
                                 signal, price, None)
    if timestamp_error:
        return SnapshotStaleness(
            True, f"incomparable: {timestamp_error}", signal, price, None
        )
    age_hours = (price - signal).total_seconds() / 3600.0
    if age_hours < 0:
        return SnapshotStaleness(True, "incomparable: price is older than the signal",
                                 signal, price, age_hours)
    if max_age_hours is not None and age_hours > float(max_age_hours):
        return SnapshotStaleness(
            True, f"levels are {age_hours:.1f}h older than the price",
            signal, price, age_hours)
    return SnapshotStaleness(False, "", signal, price, age_hours)


# --------------------------------------------------------------------------- #
# Comparison-price precedence
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ComparisonPrice:
    """One selected comparison price with explicit source and lifecycle."""

    value: float | None
    provider: str
    timestamp: object
    status: str
    reason: str = ""

    @property
    def available(self):
        return self.value is not None

    @property
    def live(self):
        return self.status == BASIS_LIVE


def select_comparison_price(
    *,
    live_price=None,
    live_provider="rubix",
    live_timestamp=None,
    live_status="",
    completed_close=None,
    completed_provider="",
    completed_timestamp=None,
    frozen_price=None,
    frozen_timestamp=None,
    signal_timestamp=None,
    now=None,
    holidays=None,
) -> ComparisonPrice:
    """Select LIVE → COMPLETED CLOSE → FROZEN without fabricating freshness.

    A Rubix quote is live only when it is positive, timezone-aware, not marked
    stale/unavailable, observed at or after signal creation, and the centralized
    EGX calendar says the current phase is continuous trading or closing auction.
    """

    now_value = now or datetime.now(timezone.utc)
    reasons = []
    live_value = _number(live_price)
    provider = str(live_provider or "").strip().lower()
    status_text = str(live_status or "").upper()
    phase = _auction_aware_phase(now_value, holidays=holidays)
    signal, quote, timestamp_error = _comparable_timestamps(
        signal_timestamp, live_timestamp
    )
    live_valid = (
        live_value is not None
        and provider == "rubix"
        and not any(word in status_text for word in ("STALE", "UNAVAILABLE", "ERROR"))
        and not timestamp_error
        and signal is not None
        and quote is not None
        and quote >= signal
        and phase in {"CONTINUOUS", "CLOSING_AUCTION"}
    )
    if live_valid:
        return ComparisonPrice(
            live_value, "rubix", quote, BASIS_LIVE,
            f"valid Rubix overlay during {phase}",
        )

    if live_value is not None:
        if provider != "rubix":
            reasons.append("live provider is not Rubix")
        if status_text and any(
            word in status_text for word in ("STALE", "UNAVAILABLE", "ERROR")
        ):
            reasons.append(f"Rubix status is {status_text}")
        if timestamp_error:
            reasons.append(timestamp_error)
        elif signal is None or quote is None:
            reasons.append("missing live/signal timestamp")
        elif quote < signal:
            reasons.append("Rubix quote predates frozen calculation")
        if phase not in {"CONTINUOUS", "CLOSING_AUCTION"}:
            reasons.append(f"EGX phase is {phase}")

    completed_value = _number(completed_close)
    completed_time = _timestamp(completed_timestamp)
    if completed_value is not None and completed_time is not None:
        return ComparisonPrice(
            completed_value,
            str(completed_provider or "completed-session provider"),
            completed_time,
            BASIS_COMPLETED_CLOSE,
            "; ".join(reasons) or "latest completed-session close",
        )

    frozen_value = _number(frozen_price)
    frozen_time = _timestamp(frozen_timestamp)
    if frozen_value is not None:
        return ComparisonPrice(
            frozen_value, "frozen snapshot", frozen_time, BASIS_FROZEN_CLOSE,
            "; ".join(reasons) or "no fresher comparable price exists",
        )
    return ComparisonPrice(
        None, "", None, STALE_LEVEL,
        "; ".join(reasons) or "no valid comparison price",
    )


# --------------------------------------------------------------------------- #
# Target classification
# --------------------------------------------------------------------------- #

def classify_target(
    current_price,
    target,
    *,
    direction: str = LONG,
    high_water=None,
    low_water=None,
    high_water_timestamp=None,
    low_water_timestamp=None,
    signal_timestamp=None,
    high_water_verified: bool = False,
    low_water_verified: bool = False,
    stale: bool = False,
) -> str:
    """Return the state of one target against ``current_price``.

    Full-precision comparison only — pass raw values, never rounded display text.
    """
    if stale:
        return STALE_LEVEL
    current = _number(current_price)
    level = _number(target)
    if current is None or level is None:
        return STALE_LEVEL

    if str(direction).upper() == SHORT:
        if current <= level + PRICE_EPSILON:
            return REACHED
        extreme = _number(low_water)
        if (
            extreme is not None
            and extreme <= level + PRICE_EPSILON
            and low_water_verified
            and _verified_post_signal(low_water_timestamp, signal_timestamp)
        ):
            return PREVIOUSLY_REACHED
        return ACTIVE

    if current >= level - PRICE_EPSILON:
        return REACHED
    extreme = _number(high_water)
    if (
        extreme is not None
        and extreme >= level - PRICE_EPSILON
        and high_water_verified
        and _verified_post_signal(high_water_timestamp, signal_timestamp)
    ):
        return PREVIOUSLY_REACHED
    return ACTIVE


def _verified_post_signal(observed_timestamp, signal_timestamp):
    """True only for timestamped market evidence observed after signal creation."""

    signal, observed, error = _comparable_timestamps(
        signal_timestamp, observed_timestamp
    )
    return bool(
        not error and signal is not None and observed is not None
        and observed >= signal
    )


def remaining_room_percent(current_price, target, *, direction: str = LONG):
    """Signed distance to a target as a percent of the current price."""
    current = _number(current_price)
    level = _number(target)
    if current is None or level is None or current == 0:
        return None
    room = (level - current) / abs(current) * 100.0
    return room if str(direction).upper() != SHORT else -room


def risk_reward(current_price, stop, target, *, direction: str = LONG):
    """Remaining reward/risk from current price using the original frozen stop."""

    current, stop_value, level = (
        _number(current_price), _number(stop), _number(target)
    )
    if current is None or stop_value is None or level is None:
        return None
    if str(direction).upper() == SHORT:
        if current >= stop_value - PRICE_EPSILON:
            return None
        reward, risk = current - level, stop_value - current
    else:
        if current <= stop_value + PRICE_EPSILON:
            return None
        reward, risk = level - current, current - stop_value
    if risk is None or risk <= PRICE_EPSILON:
        return None
    return reward / risk


# --------------------------------------------------------------------------- #
# Whole-ladder assessment
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class TargetStatus:
    """One target with its state and, when it is the next active one, its metrics."""
    label: str
    value: float | None
    state: str
    is_next_active: bool = False
    remaining_percent: float | None = None

    @property
    def reached(self) -> bool:
        return self.state in (REACHED, PREVIOUSLY_REACHED)

    @property
    def active(self) -> bool:
        return self.state == ACTIVE


@dataclass(frozen=True)
class LevelAssessment:
    """The display verdict for a frozen level ladder against one price."""
    direction: str
    current_price: float | None
    price_basis: str
    targets: tuple
    next_active: TargetStatus | None = None
    staleness: SnapshotStaleness | None = None
    risk_reward_next: float | None = None
    remaining_room_next: float | None = None

    @property
    def stale(self) -> bool:
        return bool(self.staleness and self.staleness.stale)

    @property
    def has_active_target(self) -> bool:
        return self.next_active is not None

    @property
    def all_reached_or_stale(self) -> bool:
        """True when targets were configured but none is still ahead of price."""
        return bool(self.targets) and self.next_active is None

    @property
    def any_reached(self) -> bool:
        return any(status.reached for status in self.targets)

    @property
    def first_reached_label(self) -> str:
        return next((s.label for s in self.targets if s.reached), "")


def assess_levels(current_price, targets, *, direction: str = LONG, entry=None,
                  stop=None, high_water=None, low_water=None,
                  high_water_timestamp=None, low_water_timestamp=None,
                  high_water_verified: bool = False,
                  low_water_verified: bool = False,
                  signal_created_timestamp=None,
                  signal_timestamp=None, price_timestamp=None,
                  price_basis: str = BASIS_FROZEN_CLOSE,
                  max_age_hours: float | None = DEFAULT_MAX_SNAPSHOT_AGE_HOURS,
                  stale_reason: str = "",
                  ) -> LevelAssessment:
    """Classify a target ladder and pick the next still-active target.

    ``targets`` is an ordered sequence of ``(label, value)`` pairs as the strategy
    recorded them — the order is preserved, and promotion simply picks the first
    entry that is still ahead of price. ``risk_reward_next`` and
    ``remaining_room_next`` are display figures from the selected comparison
    price to that promoted target, using the original frozen stop.
    """
    heading = str(direction).upper()
    heading = SHORT if heading == SHORT else LONG
    freshness = assess_staleness(signal_timestamp, price_timestamp,
                                 max_age_hours=max_age_hours,
                                 stale_reason=stale_reason)
    stale = freshness.stale

    statuses = []
    for label, value in targets or ():
        state = classify_target(current_price, value, direction=heading,
                                high_water=high_water, low_water=low_water,
                                high_water_timestamp=high_water_timestamp,
                                low_water_timestamp=low_water_timestamp,
                                high_water_verified=high_water_verified,
                                low_water_verified=low_water_verified,
                                signal_timestamp=signal_created_timestamp,
                                stale=stale)
        statuses.append(TargetStatus(label=str(label), value=_number(value), state=state))

    next_active = next((status for status in statuses if status.active), None)
    if next_active is not None:
        room = remaining_room_percent(current_price, next_active.value, direction=heading)
        next_active = TargetStatus(label=next_active.label, value=next_active.value,
                                   state=next_active.state, is_next_active=True,
                                   remaining_percent=room)
        statuses = [next_active if status.label == next_active.label else status
                    for status in statuses]
        reward = risk_reward(
            current_price, stop, next_active.value, direction=heading
        )
    else:
        room = reward = None

    return LevelAssessment(
        direction=heading, current_price=_number(current_price),
        price_basis=str(price_basis), targets=tuple(statuses),
        next_active=next_active, staleness=freshness,
        risk_reward_next=reward, remaining_room_next=room)


# --------------------------------------------------------------------------- #
# Presentation helpers (labels only — no calculation)
# --------------------------------------------------------------------------- #

STATE_LABELS = {
    ACTIVE: ("نشط", "Active", "blue"),
    REACHED: ("تم الوصول", "Reached", "green"),
    PREVIOUSLY_REACHED: ("تم الوصول سابقًا", "Previously Reached", "amber"),
    STALE_LEVEL: ("مستوى قديم", "Stale Level", "red"),
}

ALL_TARGETS_DONE_EN = "All configured targets reached or stale"
ALL_TARGETS_DONE_AR = "تم الوصول إلى جميع الأهداف المحددة أو أنها قديمة"


def state_label(state: str):
    """(arabic, english, tone) for a target state."""
    return STATE_LABELS.get(state, STATE_LABELS[STALE_LEVEL])


def reached_headline(status: TargetStatus) -> str:
    """e.g. ``Target 1 Reached`` — used instead of active-entry language."""
    return f"{status.label} Reached"


__all__ = [
    "ACTIVE", "REACHED", "PREVIOUSLY_REACHED", "STALE_LEVEL", "LONG", "SHORT",
    "PRICE_EPSILON", "DEFAULT_MAX_SNAPSHOT_AGE_HOURS", "BASIS_LIVE",
    "BASIS_COMPLETED_CLOSE", "BASIS_FROZEN_CLOSE", "SnapshotStaleness",
    "assess_staleness", "ComparisonPrice", "select_comparison_price",
    "classify_target", "remaining_room_percent", "risk_reward", "TargetStatus",
    "LevelAssessment", "assess_levels", "STATE_LABELS", "state_label",
    "reached_headline", "ALL_TARGETS_DONE_EN", "ALL_TARGETS_DONE_AR",
    "classic_levels_engine_version", "classic_snapshot_evidence_hash",
]
