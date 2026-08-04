"""Immutable configuration for the ORB Phase 2A data foundation only."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time
from enum import Enum
import hashlib
import json
from zoneinfo import ZoneInfo


class DuplicateSequencePolicy(str, Enum):
    """How repeated provider sequence numbers are handled."""

    DROP_IDENTICAL_REJECT_CONFLICT = "DROP_IDENTICAL_REJECT_CONFLICT"
    REJECT_ALL = "REJECT_ALL"


class MissingVolumePolicy(str, Enum):
    """Phase 2A never invents volume."""

    MARK_UNAVAILABLE = "MARK_UNAVAILABLE"
    REJECT_BAR = "REJECT_BAR"


class PartialBarPolicy(str, Enum):
    """A partial bar is never promoted to completed evidence."""

    EXCLUDE = "EXCLUDE"


@dataclass(frozen=True)
class OrbDataConfig:
    """One audited source of every Phase 2A time and quality setting.

    ``late_continuous_start`` is a display/data-phase boundary, not an entry
    cutoff. Trading thresholds deliberately do not belong in Phase 2A.
    """

    timezone: str = "Africa/Cairo"
    continuous_start: time = time(10, 0)
    opening_range_end: time = time(10, 15)
    late_continuous_start: time = time(13, 30)
    continuous_end: time = time(14, 15)
    auction_end: time = time(14, 25)
    supported_bar_intervals: tuple[int, ...] = (1, 5)
    maximum_quote_age_seconds: float = 60.0
    duplicate_sequence_policy: DuplicateSequencePolicy = (
        DuplicateSequencePolicy.DROP_IDENTICAL_REJECT_CONFLICT
    )
    out_of_order_tolerance_seconds: float = 0.0
    maximum_volume_delta_gap_seconds: float = 60.0
    minimum_updates_per_completed_bar: int = 1
    missing_volume_policy: MissingVolumePolicy = MissingVolumePolicy.MARK_UNAVAILABLE
    partial_bar_policy: PartialBarPolicy = PartialBarPolicy.EXCLUDE
    research_database_path: str = "data/research/orb_first_pullback.db"
    event_retention_days: int = 30
    derived_data_retention_days: int = 365
    retention_batch_size: int = 10_000
    # Rubix redelivers the same market payload within a narrow row-id window
    # (2026-08-04 evidence: p50 39 rows, p99 140, p99.9 347). A session-wide
    # identity set is therefore unnecessary and, at ~494k distinct identities
    # per day, unbounded. Retention keeps a rolling window far wider than any
    # observed redelivery; the hard capacity is a memory backstop that fails
    # closed instead of silently discarding unseen payloads.
    deduplication_retention_payloads: int = 100_000
    deduplication_hard_capacity: int = 400_000
    normalization_stall_max_cycles: int = 20
    normalization_stall_max_seconds: float = 300.0
    evaluation_stall_max_seconds: float = 600.0
    opening_range_minimum_bar_coverage: float = 1.0
    complete_bar_coverage: float = 0.95
    dense_session_coverage: float = 0.80
    complete_session_symbol_fraction: float = 0.90
    minimum_time_of_day_rvol_sessions: int = 20

    def __post_init__(self) -> None:
        ZoneInfo(self.timezone)
        times = (
            self.continuous_start,
            self.opening_range_end,
            self.late_continuous_start,
            self.continuous_end,
            self.auction_end,
        )
        if any(value.tzinfo is not None for value in times):
            raise ValueError("Session clock settings must be timezone-free wall times")
        if not (
            self.continuous_start
            < self.opening_range_end
            <= self.late_continuous_start
            < self.continuous_end
            < self.auction_end
        ):
            raise ValueError("ORB session times must be strictly ordered")
        if tuple(sorted(set(self.supported_bar_intervals))) != (1, 5):
            raise ValueError("Phase 2A supports exactly the 1-minute and 5-minute intervals")
        if self.maximum_quote_age_seconds < 0:
            raise ValueError("maximum_quote_age_seconds cannot be negative")
        if self.out_of_order_tolerance_seconds < 0:
            raise ValueError("out_of_order_tolerance_seconds cannot be negative")
        if self.maximum_volume_delta_gap_seconds <= 0:
            raise ValueError("maximum_volume_delta_gap_seconds must be positive")
        if self.minimum_updates_per_completed_bar < 1:
            raise ValueError("minimum_updates_per_completed_bar must be positive")
        if not 0 < self.opening_range_minimum_bar_coverage <= 1:
            raise ValueError("opening_range_minimum_bar_coverage must be in (0, 1]")
        for name in (
            "complete_bar_coverage",
            "dense_session_coverage",
            "complete_session_symbol_fraction",
        ):
            if not 0 < float(getattr(self, name)) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        for value in (
            self.event_retention_days,
            self.derived_data_retention_days,
            self.retention_batch_size,
            self.deduplication_retention_payloads,
            self.deduplication_hard_capacity,
            self.normalization_stall_max_cycles,
            self.minimum_time_of_day_rvol_sessions,
        ):
            if int(value) <= 0:
                raise ValueError("Retention and history settings must be positive")
        for value in (
            self.normalization_stall_max_seconds,
            self.evaluation_stall_max_seconds,
        ):
            if float(value) <= 0:
                raise ValueError("Stall thresholds must be positive")
        if int(self.deduplication_hard_capacity) < int(
            self.deduplication_retention_payloads
        ):
            raise ValueError(
                "deduplication_hard_capacity must be at least "
                "deduplication_retention_payloads"
            )
        if not str(self.research_database_path).strip():
            raise ValueError("research_database_path is required")

    @property
    def opening_range_minutes(self) -> int:
        start = self.continuous_start.hour * 60 + self.continuous_start.minute
        end = self.opening_range_end.hour * 60 + self.opening_range_end.minute
        return end - start

    @classmethod
    def from_mapping(cls, values: dict | None = None) -> "OrbDataConfig":
        payload = dict(values or {})
        for key in (
            "continuous_start",
            "opening_range_end",
            "late_continuous_start",
            "continuous_end",
            "auction_end",
        ):
            if isinstance(payload.get(key), str):
                payload[key] = time.fromisoformat(payload[key])
        if isinstance(payload.get("supported_bar_intervals"), list):
            payload["supported_bar_intervals"] = tuple(payload["supported_bar_intervals"])
        enum_fields = {
            "duplicate_sequence_policy": DuplicateSequencePolicy,
            "missing_volume_policy": MissingVolumePolicy,
            "partial_bar_policy": PartialBarPolicy,
        }
        for key, enum_type in enum_fields.items():
            if key in payload and not isinstance(payload[key], enum_type):
                payload[key] = enum_type(payload[key])
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in payload.items() if key in allowed})

    def as_dict(self) -> dict:
        payload = asdict(self)
        for key in (
            "continuous_start",
            "opening_range_end",
            "late_continuous_start",
            "continuous_end",
            "auction_end",
        ):
            payload[key] = payload[key].isoformat(timespec="minutes")
        payload["supported_bar_intervals"] = list(self.supported_bar_intervals)
        payload["duplicate_sequence_policy"] = self.duplicate_sequence_policy.value
        payload["missing_volume_policy"] = self.missing_volume_policy.value
        payload["partial_bar_policy"] = self.partial_bar_policy.value
        return payload

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
