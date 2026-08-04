"""Evaluation-liveness health for ORB shadow runs.

The 2026-08-04 shadow session kept reporting ``LIVE_SHADOW_HEALTHY`` for more
than two hours after normalization had stopped emitting events entirely: the
health signal only proved that the *source* was still delivering rows. This
module separates the three things that must all be true for a live run to be
healthy, and names the failure when they are not:

* the source is still delivering rows,
* those rows are still being normalized into events,
* those events are still reaching symbol evaluation.

A run that reads a million rows and normalizes none of them is not healthy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from scalping_orb.config import OrbDataConfig


class NormalizationHealth(str, Enum):
    """Whether raw source rows are still becoming normalized events."""

    HEALTHY = "NORMALIZATION_HEALTHY"
    STALLED = "NORMALIZATION_STALLED"
    CAPACITY_EXHAUSTED = "DEDUPLICATION_CAPACITY_EXHAUSTED"
    IDLE_NO_SOURCE_ROWS = "NORMALIZATION_IDLE_NO_SOURCE_ROWS"


class EvaluationHealth(str, Enum):
    """Whether normalized events are still reaching symbol evaluation."""

    HEALTHY = "EVALUATION_HEALTHY"
    STALLED = "EVALUATION_STALLED"
    IDLE_NO_EVENTS = "EVALUATION_IDLE_NO_EVENTS"


HEALTHY_NORMALIZATION = frozenset(
    {NormalizationHealth.HEALTHY, NormalizationHealth.IDLE_NO_SOURCE_ROWS}
)
HEALTHY_EVALUATION = frozenset(
    {EvaluationHealth.HEALTHY, EvaluationHealth.IDLE_NO_EVENTS}
)

CRITICAL_NORMALIZATION = frozenset(
    {NormalizationHealth.STALLED, NormalizationHealth.CAPACITY_EXHAUSTED}
)


@dataclass(frozen=True)
class CycleObservation:
    """One polling cycle's raw progress counters."""

    observed_at_utc: datetime
    source_rows_read: int
    normalized_events: int
    symbols_evaluated: int
    dedupe_capacity_exhausted: bool = False


@dataclass(frozen=True)
class LivenessVerdict:
    normalization: NormalizationHealth
    evaluation: EvaluationHealth
    consecutive_zero_normalized_cycles: int
    seconds_since_normalized_event: float | None
    seconds_since_symbol_evaluated: float | None
    normalization_stall_reason: str | None

    @property
    def healthy(self) -> bool:
        return (
            self.normalization in HEALTHY_NORMALIZATION
            and self.evaluation in HEALTHY_EVALUATION
        )

    @property
    def critical(self) -> bool:
        return self.normalization in CRITICAL_NORMALIZATION

    def as_dict(self) -> dict[str, object]:
        return {
            "normalization_health": self.normalization.value,
            "evaluation_health": self.evaluation.value,
            "consecutive_zero_normalized_cycles": (
                self.consecutive_zero_normalized_cycles
            ),
            "seconds_since_normalized_event": self.seconds_since_normalized_event,
            "seconds_since_symbol_evaluated": self.seconds_since_symbol_evaluated,
            "normalization_stall_reason": self.normalization_stall_reason,
        }


class EvaluationLivenessMonitor:
    """Track whether normalization and evaluation are still making progress.

    The monitor latches a critical stall for the lifetime of the run. A run
    that recovers still carries the recorded gap, so a stalled session can
    never be promoted to a full observed session by running long enough
    afterwards.
    """

    def __init__(self, config: OrbDataConfig | None = None):
        self.config = config or OrbDataConfig()
        self._zero_normalized_cycles = 0
        self._last_normalized_at: datetime | None = None
        self._last_evaluated_at: datetime | None = None
        self._capacity_exhausted = False
        self._critical_stall_observed = False
        self._critical_stall_reason: str | None = None
        self._critical_stall_first_seen: datetime | None = None

    @property
    def critical_stall_observed(self) -> bool:
        """True once a critical stall happened, regardless of later recovery."""

        return self._critical_stall_observed

    @property
    def critical_stall_reason(self) -> str | None:
        return self._critical_stall_reason

    @property
    def critical_stall_first_seen(self) -> datetime | None:
        return self._critical_stall_first_seen

    def observe(self, cycle: CycleObservation) -> LivenessVerdict:
        observed = _utc(cycle.observed_at_utc)
        rows = max(0, int(cycle.source_rows_read))
        events = max(0, int(cycle.normalized_events))
        evaluated = max(0, int(cycle.symbols_evaluated))

        if cycle.dedupe_capacity_exhausted:
            self._capacity_exhausted = True

        if events > 0:
            self._zero_normalized_cycles = 0
            self._last_normalized_at = observed
        elif rows > 0:
            # Rows arrived and produced nothing: the only shape of a real
            # normalization stall. No rows at all is a source condition and is
            # reported as such rather than blamed on normalization.
            self._zero_normalized_cycles += 1

        if evaluated > 0:
            self._last_evaluated_at = observed

        since_normalized = _elapsed(self._last_normalized_at, observed)
        since_evaluated = _elapsed(self._last_evaluated_at, observed)

        normalization, reason = self._normalization_health(
            rows=rows, events=events, since_normalized=since_normalized
        )
        evaluation = self._evaluation_health(
            events=events, evaluated=evaluated, since_evaluated=since_evaluated
        )

        verdict = LivenessVerdict(
            normalization=normalization,
            evaluation=evaluation,
            consecutive_zero_normalized_cycles=self._zero_normalized_cycles,
            seconds_since_normalized_event=since_normalized,
            seconds_since_symbol_evaluated=since_evaluated,
            normalization_stall_reason=reason,
        )
        if verdict.critical and not self._critical_stall_observed:
            self._critical_stall_observed = True
            self._critical_stall_reason = reason or normalization.value
            self._critical_stall_first_seen = observed
        return verdict

    def _normalization_health(
        self, *, rows: int, events: int, since_normalized: float | None
    ) -> tuple[NormalizationHealth, str | None]:
        if self._capacity_exhausted:
            return (
                NormalizationHealth.CAPACITY_EXHAUSTED,
                "deduplication capacity exhausted; unseen payloads at risk",
            )
        if events > 0:
            return NormalizationHealth.HEALTHY, None
        if rows == 0 and self._zero_normalized_cycles == 0:
            return NormalizationHealth.IDLE_NO_SOURCE_ROWS, None

        max_cycles = int(self.config.normalization_stall_max_cycles)
        if self._zero_normalized_cycles >= max_cycles:
            return (
                NormalizationHealth.STALLED,
                f"{self._zero_normalized_cycles} consecutive cycles read source "
                f"rows and normalized none (limit {max_cycles})",
            )
        max_seconds = float(self.config.normalization_stall_max_seconds)
        if since_normalized is not None and since_normalized >= max_seconds:
            return (
                NormalizationHealth.STALLED,
                f"no normalized event for {since_normalized:.0f}s "
                f"(limit {max_seconds:.0f}s)",
            )
        if rows == 0:
            return NormalizationHealth.IDLE_NO_SOURCE_ROWS, None
        return NormalizationHealth.HEALTHY, None

    def _evaluation_health(
        self, *, events: int, evaluated: int, since_evaluated: float | None
    ) -> EvaluationHealth:
        if evaluated > 0:
            return EvaluationHealth.HEALTHY
        max_seconds = float(self.config.evaluation_stall_max_seconds)
        if since_evaluated is not None and since_evaluated >= max_seconds:
            return EvaluationHealth.STALLED
        if since_evaluated is None and events == 0:
            return EvaluationHealth.IDLE_NO_EVENTS
        return EvaluationHealth.HEALTHY


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("observed_at_utc must be timezone-aware")
    return value.astimezone(timezone.utc)


def _elapsed(since: datetime | None, now: datetime) -> float | None:
    if since is None:
        return None
    return max(0.0, (now - since) / timedelta(seconds=1))
