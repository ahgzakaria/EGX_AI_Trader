"""Orchestrator state machine, single-instance lease and health record.

Research only. Nothing here decides a trade, and no state in this module can
represent one: the vocabulary tops out at "the session was observed and
reported". No LLM participates in any transition — every move is a pure
function of committed state and the clock.

The lease is a **transactional database lease**, not a `.lock` file. A lock file
left behind by a hard-kill stays stale forever and eventually gets deleted by
hand, which is how two collectors end up running. A lease carries an expiry and
a heartbeat, so a dead instance's claim ages out on its own while a live one
cannot be stolen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import os
import socket
import uuid


class OrchestratorState(str, Enum):
    """Every state the unattended workflow may occupy."""

    SCHEDULED = "SCHEDULED"
    PRE_SESSION_CHECK = "PRE_SESSION_CHECK"
    WAITING_FOR_START = "WAITING_FOR_START"
    LIVE_SHADOW_STARTING = "LIVE_SHADOW_STARTING"
    LIVE_SHADOW_RUNNING = "LIVE_SHADOW_RUNNING"
    LIVE_SHADOW_STOPPING = "LIVE_SHADOW_STOPPING"
    LIVE_SHADOW_COMPLETE = "LIVE_SHADOW_COMPLETE"
    LIVE_SHADOW_PARTIAL = "LIVE_SHADOW_PARTIAL"
    LIVE_SHADOW_FAILED = "LIVE_SHADOW_FAILED"
    RECONSTRUCTION_STARTING = "RECONSTRUCTION_STARTING"
    RECONSTRUCTION_RUNNING = "RECONSTRUCTION_RUNNING"
    RECONSTRUCTION_COMPLETE = "RECONSTRUCTION_COMPLETE"
    COMPARISON_COMPLETE = "COMPARISON_COMPLETE"
    REPORT_COMPLETE = "REPORT_COMPLETE"
    SESSION_COMPLETE = "SESSION_COMPLETE"
    SESSION_FAILED = "SESSION_FAILED"
    SKIPPED_NON_TRADING_DAY = "SKIPPED_NON_TRADING_DAY"
    SKIPPED_SOURCE_UNAVAILABLE = "SKIPPED_SOURCE_UNAVAILABLE"
    SKIPPED_DUPLICATE_INSTANCE = "SKIPPED_DUPLICATE_INSTANCE"


TERMINAL_STATES = frozenset(
    {
        OrchestratorState.SESSION_COMPLETE,
        OrchestratorState.SESSION_FAILED,
        OrchestratorState.SKIPPED_NON_TRADING_DAY,
        OrchestratorState.SKIPPED_SOURCE_UNAVAILABLE,
        OrchestratorState.SKIPPED_DUPLICATE_INSTANCE,
    }
)

#: States from which post-session work may be retried without touching Lane A.
POST_SESSION_RESUMABLE = frozenset(
    {
        OrchestratorState.LIVE_SHADOW_COMPLETE,
        OrchestratorState.LIVE_SHADOW_PARTIAL,
        OrchestratorState.RECONSTRUCTION_STARTING,
        OrchestratorState.RECONSTRUCTION_RUNNING,
        OrchestratorState.RECONSTRUCTION_COMPLETE,
        OrchestratorState.COMPARISON_COMPLETE,
        OrchestratorState.REPORT_COMPLETE,
    }
)

_FAILURE_SINKS = frozenset(
    {OrchestratorState.SESSION_FAILED, OrchestratorState.LIVE_SHADOW_FAILED}
)

LEGAL_TRANSITIONS: dict[OrchestratorState, frozenset[OrchestratorState]] = {
    OrchestratorState.SCHEDULED: frozenset(
        {
            OrchestratorState.PRE_SESSION_CHECK,
            OrchestratorState.SKIPPED_NON_TRADING_DAY,
            OrchestratorState.SKIPPED_DUPLICATE_INSTANCE,
            OrchestratorState.SESSION_FAILED,
        }
    ),
    OrchestratorState.PRE_SESSION_CHECK: frozenset(
        {
            OrchestratorState.WAITING_FOR_START,
            OrchestratorState.LIVE_SHADOW_STARTING,
            OrchestratorState.SKIPPED_NON_TRADING_DAY,
            OrchestratorState.SKIPPED_SOURCE_UNAVAILABLE,
            OrchestratorState.SKIPPED_DUPLICATE_INSTANCE,
            OrchestratorState.SESSION_FAILED,
        }
    ),
    OrchestratorState.WAITING_FOR_START: frozenset(
        {
            OrchestratorState.LIVE_SHADOW_STARTING,
            OrchestratorState.SKIPPED_SOURCE_UNAVAILABLE,
            OrchestratorState.SESSION_FAILED,
        }
    ),
    OrchestratorState.LIVE_SHADOW_STARTING: frozenset(
        {
            OrchestratorState.LIVE_SHADOW_RUNNING,
            OrchestratorState.LIVE_SHADOW_FAILED,
            OrchestratorState.SESSION_FAILED,
        }
    ),
    OrchestratorState.LIVE_SHADOW_RUNNING: frozenset(
        {
            OrchestratorState.LIVE_SHADOW_STOPPING,
            OrchestratorState.LIVE_SHADOW_FAILED,
        }
    ),
    OrchestratorState.LIVE_SHADOW_STOPPING: frozenset(
        {
            OrchestratorState.LIVE_SHADOW_COMPLETE,
            OrchestratorState.LIVE_SHADOW_PARTIAL,
            OrchestratorState.LIVE_SHADOW_FAILED,
        }
    ),
    OrchestratorState.LIVE_SHADOW_COMPLETE: frozenset(
        {OrchestratorState.RECONSTRUCTION_STARTING, OrchestratorState.SESSION_FAILED}
    ),
    OrchestratorState.LIVE_SHADOW_PARTIAL: frozenset(
        {OrchestratorState.RECONSTRUCTION_STARTING, OrchestratorState.SESSION_FAILED}
    ),
    OrchestratorState.LIVE_SHADOW_FAILED: frozenset(
        {
            # A failed live lane may still have committed Lane A rows worth
            # reconstructing; it may never be reported as a full session.
            OrchestratorState.RECONSTRUCTION_STARTING,
            OrchestratorState.SESSION_FAILED,
        }
    ),
    OrchestratorState.RECONSTRUCTION_STARTING: frozenset(
        {OrchestratorState.RECONSTRUCTION_RUNNING, OrchestratorState.SESSION_FAILED}
    ),
    OrchestratorState.RECONSTRUCTION_RUNNING: frozenset(
        {OrchestratorState.RECONSTRUCTION_COMPLETE, OrchestratorState.SESSION_FAILED}
    ),
    OrchestratorState.RECONSTRUCTION_COMPLETE: frozenset(
        {OrchestratorState.COMPARISON_COMPLETE, OrchestratorState.SESSION_FAILED}
    ),
    OrchestratorState.COMPARISON_COMPLETE: frozenset(
        {OrchestratorState.REPORT_COMPLETE, OrchestratorState.SESSION_FAILED}
    ),
    OrchestratorState.REPORT_COMPLETE: frozenset(
        {OrchestratorState.SESSION_COMPLETE, OrchestratorState.SESSION_FAILED}
    ),
}


def assert_legal_transition(
    prior: OrchestratorState, new: OrchestratorState
) -> None:
    """Fail loudly rather than persisting an impossible workflow history."""

    if prior in TERMINAL_STATES:
        raise ValueError(f"{prior.value} is terminal; cannot move to {new.value}")
    allowed = LEGAL_TRANSITIONS.get(prior)
    if allowed is None or new not in allowed:
        raise ValueError(f"illegal orchestrator transition {prior.value} -> {new.value}")


class OrchestratorVerdict(str, Enum):
    FULL_SHADOW_SESSION_OBSERVED = "FULL_SHADOW_SESSION_OBSERVED"
    PARTIAL_SHADOW_SESSION = "PARTIAL_SHADOW_SESSION"
    FAILED_SHADOW_SESSION = "FAILED_SHADOW_SESSION"


@dataclass(frozen=True)
class FullSessionCriteria:
    """The gate for `FULL_SHADOW_SESSION_OBSERVED`. Every field must be true.

    Deliberately explicit rather than a single boolean: when a session falls
    short, the report must say which criterion failed.
    """

    started_before_session_start: bool = False
    covered_through_continuous_end: bool = False
    opening_range_observed_live: bool = False
    sufficient_heartbeat_coverage: bool = False
    cursor_progressed: bool = False
    no_excessive_polling_outage: bool = False
    sufficient_exchange_minute_coverage: bool = False
    # Source coverage says rows arrived. These say the pipeline behind the
    # source was still alive to turn them into evaluated symbols.
    normalization_progressed_to_continuous_end: bool = False
    evaluation_progressed_to_continuous_end: bool = False
    no_critical_evaluation_stall: bool = False
    graceful_shutdown: bool = False
    lane_a_persisted: bool = False
    reconstruction_completed: bool = False
    cross_run_comparison_completed: bool = False
    report_completed: bool = False
    no_production_execution: bool = True

    def failures(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in self.__dataclass_fields__  # type: ignore[attr-defined]
            if not getattr(self, name)
        )

    @property
    def all_met(self) -> bool:
        return not self.failures()

    def verdict(self, *, live_failed: bool = False) -> OrchestratorVerdict:
        """Never upgrades. Post-session data availability alone is not enough."""

        if live_failed:
            return OrchestratorVerdict.FAILED_SHADOW_SESSION
        if self.all_met:
            return OrchestratorVerdict.FULL_SHADOW_SESSION_OBSERVED
        return OrchestratorVerdict.PARTIAL_SHADOW_SESSION


# --------------------------------------------------------------------------- #
# Single-instance lease
# --------------------------------------------------------------------------- #


class LeaseUnavailable(RuntimeError):
    """Another live instance holds the lease."""


def machine_identity() -> str:
    """Stable, non-identifying host marker. No credential, no user name."""

    return hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class LeaseHolder:
    lease_scope: str
    instance_id: str
    orchestrator_run_id: str
    session_date: date
    process_id: int
    machine_identity: str
    acquired_at_utc: datetime
    last_heartbeat_utc: datetime
    lease_expires_utc: datetime
    released_at_utc: datetime | None = None

    def is_expired(self, now: datetime) -> bool:
        return now >= self.lease_expires_utc

    def is_active(self, now: datetime) -> bool:
        return self.released_at_utc is None and not self.is_expired(now)


def lease_scope_for(session_date: date, source_identity: str) -> str:
    """One lease per (session, source). Two sources are not the same workflow."""

    return f"ORB_SHADOW|{session_date.isoformat()}|{source_identity}"


def new_instance_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class OrchestratorTransition:
    sequence_index: int
    session_date: date
    prior_state: OrchestratorState
    new_state: OrchestratorState
    occurred_at_utc: datetime
    reason: str
    config_identity: str
    live_run_id: str | None = None
    reconstruction_run_id: str | None = None
    source_cursor_id: int | None = None
    heartbeat_at_utc: datetime | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class OrchestratorHealthSample:
    observed_at_utc: datetime
    state: OrchestratorState
    source_cursor_id: int | None = None
    last_source_row_utc: datetime | None = None
    cycle_index: int | None = None
    cycle_duration_seconds: float | None = None
    poll_failures: int | None = None
    maximum_polling_gap_seconds: float | None = None
    source_rows_this_cycle: int | None = None
    normalized_events_total: int | None = None
    symbols_observed: int | None = None
    symbols_eligible: int | None = None
    symbols_evaluated: int | None = None
    completed_one_minute_bars: int | None = None
    completed_five_minute_bars: int | None = None
    opening_ranges_ready: int | None = None
    lane_a_states: int | None = None
    freshness_median_seconds: float | None = None
    freshness_p90_seconds: float | None = None
    freshness_p95_seconds: float | None = None
    percent_above_freshness_budget: float | None = None
    process_rss_bytes: int | None = None


@dataclass(frozen=True)
class OrchestratorFailure:
    occurred_at_utc: datetime
    state: OrchestratorState
    failure_code: str
    detail: str
    recoverable: bool


def process_rss_bytes() -> int | None:
    """Resident memory, when the platform offers it cheaply. Never required."""

    try:  # pragma: no cover - platform dependent
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except Exception:
        pass
    try:  # pragma: no cover - platform dependent
        import ctypes
        import ctypes.wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            return int(counters.WorkingSetSize)
    except Exception:
        return None
    return None


__all__ = [
    "LEGAL_TRANSITIONS",
    "POST_SESSION_RESUMABLE",
    "TERMINAL_STATES",
    "FullSessionCriteria",
    "LeaseHolder",
    "LeaseUnavailable",
    "OrchestratorFailure",
    "OrchestratorHealthSample",
    "OrchestratorState",
    "OrchestratorTransition",
    "OrchestratorVerdict",
    "assert_legal_transition",
    "lease_scope_for",
    "machine_identity",
    "new_instance_id",
    "process_rss_bytes",
]
