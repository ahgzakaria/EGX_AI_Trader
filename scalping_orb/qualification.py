"""The stop and target levels the engine already computed, kept instead of dropped.

An evaluation only reaches `ENTRY_READY_RESEARCH` after `_structural_risk` and
`_targets` both return without rejection reasons — otherwise it becomes
`RECLAIM_FAILED`. So every emitted signal has, at that instant, a structural
stop strictly below its trigger, a 1R and 2R target, a usable target bounded by
D-1 resistance, and an effective reward/risk at or above the configured
minimum.

Those values were computed and then discarded: `ShadowStateRecord` copied the
state, the fingerprints and the timing, and neither `risk` nor `targets`. The
cost of that omission is precise. The 34 signals measured across 2026-08-06,
08-10 and 08-11 have a median adverse excursion of −1.46 %, with 23 of 34
reaching −1 %, and every one of their stops sat somewhere in the 0–3 % band
below trigger that `maximum_stop_distance_percent` permits. A large share of
them plausibly stopped out before their favourable excursion arrived, and the
evidence cannot say which — so the measurement of those 34 stays permanently
labelled `TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED` and is never reconstructed.

This module carries those values from the engine to the database. It computes
nothing. Every field is copied from an object the engine already materialised,
and where the engine had no value the absence is recorded as a named status
rather than as a zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


#: Bumped when the qualification row's field set changes. Independent of the
#: repository's `SCHEMA_VERSION`, which tracks the database as a whole.
QUALIFICATION_SCHEMA_VERSION = 1


class QualificationStatus(str, Enum):
    """Whether the engine's levels reached the row."""

    PERSISTED = "QUALIFICATION_PERSISTED"
    #: Defensive. `ENTRY_READY_RESEARCH` is unreachable without a valid risk
    #: proposal and target projection, so this should never be written — but a
    #: row that says so is worth more than a row of silent nulls.
    ENGINE_VALUES_MISSING = "QUALIFICATION_UNAVAILABLE_ENGINE_VALUES_MISSING"


class DailyResistanceStatus(str, Enum):
    """Why `nearest_daily_resistance` is what it is.

    `TargetProjection.nearest_daily_resistance` is `None` both when D-1 context
    was never supplied and when it was supplied and simply held no level above
    the trigger. Those are different facts — the first says the projection was
    made blind, the second says it was made with knowledge that nothing stood
    in the way — and a single null cannot tell them apart.
    """

    RESISTANCE_ABOVE_TRIGGER = "DAILY_RESISTANCE_ABOVE_TRIGGER"
    NO_RESISTANCE_ABOVE_TRIGGER = "NO_DAILY_RESISTANCE_ABOVE_TRIGGER"
    CONTEXT_UNAVAILABLE = "DAILY_CONTEXT_UNAVAILABLE"


#: `atr_status` holds an `IntradayAtrStatus` value. This extra member covers the
#: defensive case where there is no breakout assessment to read an ATR from.
ATR_BREAKOUT_CONTEXT_UNAVAILABLE = "BREAKOUT_CONTEXT_UNAVAILABLE"

#: The only `atr_status` under which a numeric `atr_value` may be stored. The
#: schema enforces it; a warming-up ATR is null, never 0.0.
ATR_AVAILABLE = "INTRADAY_ATR_AVAILABLE"


@dataclass(frozen=True)
class SignalQualification:
    """One emitted signal's risk and target levels, copied from the engine.

    Attached to the `ShadowStateRecord` it belongs to, so the levels travel
    with the signal through the existing persistence path and land in the same
    transaction. There is no separate identity: the row is keyed on the Lane A
    `live_state_id` the signal already has.

    `trigger_price` is the engine's confirmed reclaim trigger — a price level
    the rules validated, **not an order fill**. Nothing in this package places
    an order, and the outcome measurement's entry price remains a separately
    labelled quote proxy. The two must never be conflated: one is what the
    strategy said, the other is what the tape did afterwards.
    """

    status: QualificationStatus

    # -- risk (StructuralRiskProposal + TargetProjection.risk_per_share) ----
    trigger_price: float | None
    proposed_stop: float | None
    stop_basis: str | None
    raw_pullback_low: float | None
    buffer_applied: float | None
    buffer_basis: str | None
    stop_distance_absolute: float | None
    stop_distance_percent: float | None
    stop_distance_atr: float | None
    risk_per_share: float | None

    # -- targets (TargetProjection) ----------------------------------------
    target_1: float | None
    target_2: float | None
    target_1_r_multiple: float | None
    target_2_r_multiple: float | None
    usable_target: float | None
    effective_reward_risk: float | None
    meets_minimum_reward_risk: bool | None

    # -- resistance context -------------------------------------------------
    resistance_before_target_1: bool | None
    nearest_daily_resistance: float | None
    reward_before_resistance: float | None
    daily_resistance_status: DailyResistanceStatus

    # -- ATR provenance (BreakoutAssessment.intraday_atr) -------------------
    atr_value: float | None
    atr_status: str
    atr_interval_minutes: int | None
    atr_lookback_bars: int | None
    atr_observed_bars: int | None

    # -- provenance ---------------------------------------------------------
    strategy_fingerprint: str
    engine_version: str
    schema_version: int = QUALIFICATION_SCHEMA_VERSION


def qualification_from_evaluation(
    evaluation, *, daily_context=None
) -> SignalQualification | None:
    """Copy an emitted signal's levels off the evaluation. Never recomputes.

    Returns `None` for any evaluation that is not research-ready, so a
    qualification row exists if and only if a signal was emitted.

    `daily_context` is the `DailyContext` the evaluation was run against. It is
    passed separately because `ORBResearchEvaluation` does not carry it, and
    reading `available` off it is the only way to distinguish "no D-1 data" from
    "D-1 data with nothing above the trigger". Nothing else is taken from it.
    """

    if not getattr(evaluation, "research_ready", False):
        return None

    risk = evaluation.risk
    targets = evaluation.targets
    breakout = evaluation.breakout

    if breakout is None or breakout.intraday_atr is None:
        atr_value = None
        atr_status = ATR_BREAKOUT_CONTEXT_UNAVAILABLE
        atr_interval = atr_lookback = atr_observed = None
    else:
        atr = breakout.intraday_atr
        atr_status = str(getattr(atr.status, "value", atr.status))
        # A warming-up or unavailable ATR stores no number. Substituting 0.0
        # would read downstream as "volatility was zero", which is a claim the
        # data never made.
        atr_value = float(atr.value) if atr.available else None
        atr_interval = int(atr.interval_minutes)
        atr_lookback = int(atr.lookback_bars)
        atr_observed = int(atr.observed_bars)

    if risk is None or targets is None:  # pragma: no cover - unreachable by gate
        return SignalQualification(
            status=QualificationStatus.ENGINE_VALUES_MISSING,
            trigger_price=None,
            proposed_stop=None,
            stop_basis=None,
            raw_pullback_low=None,
            buffer_applied=None,
            buffer_basis=None,
            stop_distance_absolute=None,
            stop_distance_percent=None,
            stop_distance_atr=None,
            risk_per_share=None,
            target_1=None,
            target_2=None,
            target_1_r_multiple=None,
            target_2_r_multiple=None,
            usable_target=None,
            effective_reward_risk=None,
            meets_minimum_reward_risk=None,
            resistance_before_target_1=None,
            nearest_daily_resistance=None,
            reward_before_resistance=None,
            daily_resistance_status=DailyResistanceStatus.CONTEXT_UNAVAILABLE,
            atr_value=atr_value,
            atr_status=atr_status,
            atr_interval_minutes=atr_interval,
            atr_lookback_bars=atr_lookback,
            atr_observed_bars=atr_observed,
            strategy_fingerprint=evaluation.strategy_fingerprint,
            engine_version=evaluation.engine_version,
        )

    if daily_context is None or not getattr(daily_context, "available", False):
        resistance_status = DailyResistanceStatus.CONTEXT_UNAVAILABLE
    elif targets.nearest_daily_resistance is None:
        resistance_status = DailyResistanceStatus.NO_RESISTANCE_ABOVE_TRIGGER
    else:
        resistance_status = DailyResistanceStatus.RESISTANCE_ABOVE_TRIGGER

    return SignalQualification(
        status=QualificationStatus.PERSISTED,
        trigger_price=targets.trigger_price,
        proposed_stop=risk.proposed_stop,
        stop_basis=risk.stop_basis,
        raw_pullback_low=risk.raw_pullback_low,
        buffer_applied=risk.buffer_applied,
        buffer_basis=risk.buffer_basis,
        stop_distance_absolute=risk.stop_distance_absolute,
        stop_distance_percent=risk.stop_distance_percent,
        stop_distance_atr=risk.stop_distance_atr,
        risk_per_share=targets.risk_per_share,
        target_1=targets.target_1,
        target_2=targets.target_2,
        target_1_r_multiple=targets.target_1_r_multiple,
        target_2_r_multiple=targets.target_2_r_multiple,
        usable_target=targets.usable_target,
        effective_reward_risk=targets.effective_reward_risk,
        meets_minimum_reward_risk=targets.meets_minimum_reward_risk,
        resistance_before_target_1=targets.resistance_before_target_1,
        nearest_daily_resistance=targets.nearest_daily_resistance,
        reward_before_resistance=targets.reward_before_resistance,
        daily_resistance_status=resistance_status,
        atr_value=atr_value,
        atr_status=atr_status,
        atr_interval_minutes=atr_interval,
        atr_lookback_bars=atr_lookback,
        atr_observed_bars=atr_observed,
        strategy_fingerprint=evaluation.strategy_fingerprint,
        engine_version=evaluation.engine_version,
    )
