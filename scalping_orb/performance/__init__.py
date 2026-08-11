"""Post-signal outcome measurement for ORB research candidates.

This package measures **what price did** after a signal that the strategy
already produced. It never produces a signal, never scores one, and never
concludes anything about whether the strategy is worth trading. Those are
separate questions and they are deliberately not answered here.
"""

from __future__ import annotations

from scalping_orb.performance.qualified_outcomes import (
    MilestoneLevel,
    OutcomeEvidenceSource,
    OutcomeQuality,
    OutcomeStatus,
    QualificationSnapshot,
    QualifiedOutcome,
    discover_qualified_signals,
    measure_qualified_signal,
)
from scalping_orb.performance.signal_outcomes import (
    MeasurementQuality,
    MeasurementReason,
    OutcomeMeasurementConfig,
    PriceObservation,
    SignalDiscoveryError,
    SignalOutcome,
    SignalRecord,
    TpSlStatus,
    discover_signals,
    measure_signal,
    read_observations,
    session_end_utc,
)

__all__ = [
    "MilestoneLevel",
    "OutcomeEvidenceSource",
    "OutcomeQuality",
    "OutcomeStatus",
    "QualificationSnapshot",
    "QualifiedOutcome",
    "discover_qualified_signals",
    "measure_qualified_signal",
    "MeasurementQuality",
    "MeasurementReason",
    "OutcomeMeasurementConfig",
    "PriceObservation",
    "SignalDiscoveryError",
    "SignalOutcome",
    "SignalRecord",
    "TpSlStatus",
    "discover_signals",
    "measure_signal",
    "read_observations",
    "session_end_utc",
]
