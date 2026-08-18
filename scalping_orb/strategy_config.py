"""Typed Phase 2B Core configuration — INITIAL_RESEARCH_DEFAULTS.

Every threshold the research engine consults lives here, with a type, a
validated range, a documented meaning and a serialisation round-trip. No
magic number appears in the engine.

**The defaults are not optimal and are not claimed to be.** They are
``INITIAL_RESEARCH_DEFAULTS``: plausible starting values chosen so the engine
can be exercised deterministically. Calibration needs the independent
high-density sessions that Phase 2A's gate lists as still missing.

This is a *separate* configuration object rather than new fields on
``OrbDataConfig``, because ``OrbDataConfig.fingerprint`` seeds the Phase 2A
``session_id``. Adding fields there would change every existing session
identity and break the replay idempotency Phase 2A was gated on.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import time
from enum import Enum
import hashlib
import json

from scalping_orb.config import OrbDataConfig


#: Bumped whenever engine semantics change in a way that alters a decision.
#: Persisted with every candidate so a stored evaluation stays interpretable.
ENGINE_VERSION = "orb-phase2b-core/1.0.0"

#: Honest provenance marker stored beside every threshold set.
DEFAULTS_PROVENANCE = "INITIAL_RESEARCH_DEFAULTS"


class EvaluationMode(str, Enum):
    """How an evaluation obtained its evidence."""

    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    SHADOW_LIVE = "SHADOW_LIVE"
    LIVE_DISABLED = "LIVE_DISABLED"


class OpeningRangeVersionMode(str, Enum):
    """Which frozen opening-range version an evaluation binds to."""

    DECISION_TIME_ORIGINAL_VERSION = "DECISION_TIME_ORIGINAL_VERSION"
    LATEST_RESEARCH_REVISION = "LATEST_RESEARCH_REVISION"


class ReclaimRule(str, Enum):
    """Deterministic reclaim-confirmation rules. Exactly one is active.

    Only ``CLOSE_RECLAIMS_OR_HIGH`` is implemented in Phase 2B Core. The other
    two are the documented vocabulary of the durable state-machine contract and
    are *declared but not implemented*: selecting one raises rather than
    silently degrading to the default rule, which is what a partial
    implementation would otherwise do.
    """

    #: A completed bar closes back above OR High after trading into/below the zone.
    CLOSE_RECLAIMS_OR_HIGH = "CLOSE_RECLAIMS_OR_HIGH"
    #: A completed close above OR High, then a break of that bar's high.
    CLOSE_ABOVE_THEN_PREVIOUS_BAR_HIGH = "CLOSE_ABOVE_THEN_PREVIOUS_BAR_HIGH"
    #: A bullish rejection bar, then a break of the rejection bar's high.
    REJECTION_BAR_HIGH_BREAK = "REJECTION_BAR_HIGH_BREAK"


#: The reclaim rules Phase 2B Core actually evaluates.
IMPLEMENTED_RECLAIM_RULES = frozenset({ReclaimRule.CLOSE_RECLAIMS_OR_HIGH})


class VolumeRequirementMode(str, Enum):
    """What happens to pullback assessment when bar volume is unavailable."""

    #: Assess on price alone and label the result PULLBACK_PRICE_ONLY.
    ALLOW_PRICE_ONLY_RESEARCH = "ALLOW_PRICE_ONLY_RESEARCH"
    #: Refuse to assess without valid volume.
    REQUIRE_VALID_VOLUME = "REQUIRE_VALID_VOLUME"


@dataclass(frozen=True)
class OrbStrategyConfig:
    """Phase 2B Core thresholds. Immutable, validated, fingerprinted."""

    # -- composed Phase 2A data configuration ------------------------------
    data: OrbDataConfig = field(default_factory=OrbDataConfig)

    # -- BREAKOUT ----------------------------------------------------------
    #: Confirmation interval. Only completed bars of this interval confirm.
    breakout_confirmation_interval_minutes: int = 5
    #: Close must exceed OR High by at least this fraction of OR High.
    minimum_close_above_or_high_percent: float = 0.0005
    #: Above this extension over OR High the breakout is a chase, not an entry.
    maximum_breakout_extension_percent: float = 0.020
    #: Same gate expressed in intraday ATR; applied only when ATR is available.
    maximum_breakout_extension_atr: float = 2.0
    #: Breakout bar range relative to intraday ATR; ATR-conditional.
    maximum_breakout_bar_range_atr: float = 2.5
    #: A confirmation bar built from fewer updates is not trustworthy.
    minimum_updates_for_breakout_bar: int = 2
    #: A D-1 resistance nearer than this leaves no room to work with.
    maximum_distance_to_daily_resistance_percent: float = 0.005
    #: Reward/risk floor evaluated at breakout time.
    minimum_initial_reward_risk: float = 1.2

    # -- PULLBACK ----------------------------------------------------------
    maximum_bars_until_first_pullback: int = 6
    minimum_pullback_depth_percent: float = 0.001
    maximum_pullback_depth_percent: float = 0.020
    maximum_pullback_depth_atr: float = 2.0
    maximum_pullback_bars: int = 6
    #: Phase 2B Core only ever constructs pullback ordinal 1, so this must stay
    #: ``True``; it is validated rather than left as an inert switch.
    require_first_pullback_only: bool = True
    #: How far a completed close may sit below OR High before it is a breach.
    maximum_close_below_or_high_percent: float = 0.003
    #: How many breaching closes are tolerated *while waiting for reclaim*,
    #: which is the only phase in which more than one can occur: the pullback
    #: itself resolves on the first bar that reaches back to OR High, so it
    #: never observes a second breach. ``0`` means the first decisive close
    #: under the threshold fails the structure.
    maximum_structural_breach_bars: int = 0
    #: How far the pullback *low* may wick below the frozen zone floor before
    #: the structure has failed. Separate from the close-based breach limit:
    #: one measures closes, the other measures the extreme of the move.
    maximum_low_below_zone_lower_percent: float = 0.003
    require_reduced_selling_volume_when_volume_valid: bool = True
    volume_requirement_mode: VolumeRequirementMode = (
        VolumeRequirementMode.ALLOW_PRICE_ONLY_RESEARCH
    )

    # -- RECLAIM -----------------------------------------------------------
    reclaim_confirmation_interval_minutes: int = 5
    require_completed_bar: bool = True
    confirmation_expiry_bars: int = 6
    reclaim_rule: ReclaimRule = ReclaimRule.CLOSE_RECLAIMS_OR_HIGH

    # -- RISK --------------------------------------------------------------
    stop_atr_buffer: float = 0.25
    #: Percentage buffer used only when intraday ATR is unavailable.
    stop_percent_buffer: float = 0.002
    maximum_stop_distance_percent: float = 0.030
    minimum_reward_risk: float = 1.5
    target_1_r_multiple: float = 1.0
    target_2_r_multiple: float = 2.0
    maximum_distance_to_target_resistance_percent: float = 0.002

    # -- TARGETS MUST OUTGROW THE COST OF REACHING THEM --------------------
    #
    # Targets were purely R multiples of the risk unit, and the risk unit's
    # only floor is `minimum_pullback_depth_percent` at 0.1%. A shallow
    # pullback therefore produced a target of the same order, and on
    # 2026-08-18 the thirteen signals averaged a 0.86% first target against a
    # measured round trip of 0.46% in fees plus a 0.35% median spread. Eight of
    # the thirteen lost money at their own target with entry, exit and target
    # all going exactly as intended. The engine still reported reward/risk 2.0
    # for every one of them, correctly: 2R is 2R however small R is.
    #
    # Two independent floors are applied, and the target is the largest of the
    # three candidates. Both are set from market structure rather than from
    # any measured outcome -- there are two sessions of signals on record,
    # which is far too few to fit anything to.
    #
    #: Floor the target at a multiple of intraday ATR, so it reflects what the
    #: stock actually moves rather than how deep one pullback happened to be.
    #: EGX liquid names have a median daily range near 2.9%, measured over
    #: 112,411 stock-days; a target far below the day's own volatility is
    #: leaving the move on the table, not being conservative.
    target_1_atr_multiple: float = 1.0
    target_2_atr_multiple: float = 2.0

    #: Round-trip cost as a fraction of price: broker fee schedule (0.1819%
    #: per side) plus slippage (0.05% per side). The spread is measured per
    #: signal and is charged on top of this, so this is a floor, not the total.
    round_trip_cost_percent: float = 0.004638

    #: A target must clear the round trip by at least this multiple. At 1.0 a
    #: perfect trade breaks even, which is not a trade worth taking.
    minimum_target_cost_multiple: float = 2.0

    # -- TIME (Cairo wall clock; auction/session end come from `data`) -----
    earliest_breakout_time: time = time(10, 15)
    latest_research_entry_time: time = time(13, 30)
    expiry_time: time = time(14, 0)

    # -- INTRADAY ATR ------------------------------------------------------
    intraday_atr_interval_minutes: int = 5
    intraday_atr_lookback_bars: int = 14
    intraday_atr_minimum_bars: int = 6

    # -- provenance --------------------------------------------------------
    defaults_provenance: str = DEFAULTS_PROVENANCE

    def __post_init__(self) -> None:
        if self.breakout_confirmation_interval_minutes not in self.data.supported_bar_intervals:
            raise ValueError(
                "breakout_confirmation_interval_minutes must be a supported bar interval"
            )
        if self.reclaim_confirmation_interval_minutes not in self.data.supported_bar_intervals:
            raise ValueError(
                "reclaim_confirmation_interval_minutes must be a supported bar interval"
            )
        if self.reclaim_rule not in IMPLEMENTED_RECLAIM_RULES:
            raise ValueError(
                f"{self.reclaim_rule.value} is declared but not implemented in "
                "Phase 2B Core; only "
                f"{sorted(rule.value for rule in IMPLEMENTED_RECLAIM_RULES)} may be selected"
            )
        if not self.require_completed_bar:
            raise ValueError("require_completed_bar cannot be disabled: a touch is never confirmation")
        if not self.require_first_pullback_only:
            raise ValueError(
                "require_first_pullback_only cannot be disabled: Phase 2B Core "
                "evaluates the first pullback only"
            )
        fractions = {
            "minimum_close_above_or_high_percent": (0.0, 1.0),
            "maximum_breakout_extension_percent": (0.0, 1.0),
            "maximum_distance_to_daily_resistance_percent": (0.0, 1.0),
            "minimum_pullback_depth_percent": (0.0, 1.0),
            "maximum_pullback_depth_percent": (0.0, 1.0),
            "maximum_close_below_or_high_percent": (0.0, 1.0),
            "maximum_low_below_zone_lower_percent": (0.0, 1.0),
            "stop_percent_buffer": (0.0, 1.0),
            "maximum_stop_distance_percent": (0.0, 1.0),
            "maximum_distance_to_target_resistance_percent": (0.0, 1.0),
        }
        for name, (low, high) in fractions.items():
            value = float(getattr(self, name))
            if not low <= value <= high:
                raise ValueError(f"{name} must be a fraction in [{low}, {high}]")
        if self.minimum_pullback_depth_percent >= self.maximum_pullback_depth_percent:
            raise ValueError(
                "minimum_pullback_depth_percent must be below maximum_pullback_depth_percent"
            )
        positives = (
            "maximum_breakout_extension_atr",
            "maximum_breakout_bar_range_atr",
            "minimum_initial_reward_risk",
            "maximum_pullback_depth_atr",
            "minimum_reward_risk",
            "target_1_r_multiple",
            "target_2_r_multiple",
        )
        for name in positives:
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.stop_atr_buffer < 0:
            raise ValueError("stop_atr_buffer cannot be negative")
        if self.target_1_r_multiple >= self.target_2_r_multiple:
            raise ValueError("target_1_r_multiple must be below target_2_r_multiple")
        counters = (
            "maximum_bars_until_first_pullback",
            "maximum_pullback_bars",
            "maximum_structural_breach_bars",
            "confirmation_expiry_bars",
            "minimum_updates_for_breakout_bar",
            "intraday_atr_lookback_bars",
            "intraday_atr_minimum_bars",
        )
        for name in counters:
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.intraday_atr_minimum_bars > self.intraday_atr_lookback_bars:
            raise ValueError(
                "intraday_atr_minimum_bars cannot exceed intraday_atr_lookback_bars"
            )
        times = (
            self.earliest_breakout_time,
            self.latest_research_entry_time,
            self.expiry_time,
        )
        if any(value.tzinfo is not None for value in times):
            raise ValueError("Strategy clock settings must be timezone-free wall times")
        if not (
            self.data.opening_range_end
            <= self.earliest_breakout_time
            < self.latest_research_entry_time
            <= self.expiry_time
            <= self.data.continuous_end
        ):
            raise ValueError(
                "Strategy times must satisfy "
                "opening_range_end <= earliest_breakout < latest_research_entry "
                "<= expiry <= continuous_end"
            )

    # -- derived clock references (never duplicated) -----------------------

    @property
    def auction_start(self) -> time:
        """The auction begins exactly where continuous trading ends."""

        return self.data.continuous_end

    @property
    def continuous_session_end(self) -> time:
        return self.data.continuous_end

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["data"] = self.data.as_dict()
        for key in (
            "earliest_breakout_time",
            "latest_research_entry_time",
            "expiry_time",
        ):
            payload[key] = getattr(self, key).isoformat(timespec="minutes")
        payload["reclaim_rule"] = self.reclaim_rule.value
        payload["volume_requirement_mode"] = self.volume_requirement_mode.value
        return payload

    @classmethod
    def from_mapping(cls, values: dict | None = None) -> "OrbStrategyConfig":
        payload = dict(values or {})
        if isinstance(payload.get("data"), dict):
            payload["data"] = OrbDataConfig.from_mapping(payload["data"])
        for key in (
            "earliest_breakout_time",
            "latest_research_entry_time",
            "expiry_time",
        ):
            if isinstance(payload.get(key), str):
                payload[key] = time.fromisoformat(payload[key])
        if "reclaim_rule" in payload and not isinstance(
            payload["reclaim_rule"], ReclaimRule
        ):
            payload["reclaim_rule"] = ReclaimRule(payload["reclaim_rule"])
        if "volume_requirement_mode" in payload and not isinstance(
            payload["volume_requirement_mode"], VolumeRequirementMode
        ):
            payload["volume_requirement_mode"] = VolumeRequirementMode(
                payload["volume_requirement_mode"]
            )
        allowed = cls.__dataclass_fields__
        return cls(**{k: v for k, v in payload.items() if k in allowed})

    @property
    def strategy_fingerprint(self) -> str:
        """Stable identity of this threshold set plus the engine version."""

        encoded = json.dumps(
            {"engine": ENGINE_VERSION, "config": self.as_dict()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DEFAULTS_PROVENANCE",
    "ENGINE_VERSION",
    "IMPLEMENTED_RECLAIM_RULES",
    "EvaluationMode",
    "OpeningRangeVersionMode",
    "OrbStrategyConfig",
    "ReclaimRule",
    "VolumeRequirementMode",
]
