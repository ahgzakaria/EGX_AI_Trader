"""Typed, deterministic Rubix quote normalization with no invented fields."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Callable, Iterable

from core.universe import lookup
from scalping_orb.config import DuplicateSequencePolicy, OrbDataConfig
from scalping_orb.session import OrbSessionClassifier, OrbSessionPhase, require_aware


class SourceEventType(str, Enum):
    RUBIX_QUOTE = "RUBIX_QUOTE"


class FeedTimestampQuality(str, Enum):
    VERIFIED_FEED_TIMESTAMP = "VERIFIED_FEED_TIMESTAMP"
    RECEIPT_TIME_ONLY = "RECEIPT_TIME_ONLY"


class NormalizationMode(str, Enum):
    LIVE = "LIVE"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"


class MarketTimeStatus(str, Enum):
    MARKET_TIME_VALID = "MARKET_TIME_VALID"
    MARKET_TIME_VALID_RECEIVE_DELAYED = "MARKET_TIME_VALID_RECEIVE_DELAYED"
    MARKET_TIME_UNRELIABLE = "MARKET_TIME_UNRELIABLE"


class HistoricalReplayStatus(str, Enum):
    HISTORICAL_REPLAY_ACCEPTED = "HISTORICAL_REPLAY_ACCEPTED"
    LATE_CORRECTION_ONLY = "LATE_CORRECTION_ONLY"
    HISTORICAL_REPLAY_REJECTED = "HISTORICAL_REPLAY_REJECTED"


class LiveFreshnessStatus(str, Enum):
    LIVE_FRESHNESS_PASSED = "LIVE_FRESHNESS_PASSED"
    LIVE_FRESHNESS_FAILED = "LIVE_FRESHNESS_FAILED"
    COLLECTOR_FRESHNESS_UNAVAILABLE = "COLLECTOR_FRESHNESS_UNAVAILABLE"


class SpreadCapability(str, Enum):
    SPREAD_AVAILABLE = "SPREAD_AVAILABLE"
    SPREAD_UNAVAILABLE = "SPREAD_UNAVAILABLE"


class VolumeCapability(str, Enum):
    VOLUME_AVAILABLE = "VOLUME_AVAILABLE"
    VOLUME_UNAVAILABLE = "VOLUME_UNAVAILABLE"


class UniverseMembershipStatus(str, Enum):
    """Why a canonical ticker may or may not carry ORB operational eligibility.

    Phase 2A still stores every historical observation. The status only records
    that "observed in the Rubix database" is not the same claim as "eligible for
    a new ORB trade"; that decision belongs to the active EODHD universe plus a
    verified Rubix mapping, plus an explicit open-position exit exception.
    """

    ACTIVE_UNIVERSE_VERIFIED_RUBIX = "ACTIVE_UNIVERSE_VERIFIED_RUBIX"
    ACTIVE_UNIVERSE_UNVERIFIED_RUBIX = "ACTIVE_UNIVERSE_UNVERIFIED_RUBIX"
    ARCHIVED_INACTIVE_SYMBOL = "ARCHIVED_INACTIVE_SYMBOL"
    UNKNOWN_UNMAPPED_IDENTIFIER = "UNKNOWN_UNMAPPED_IDENTIFIER"


OPERATIONALLY_ELIGIBLE_MEMBERSHIP = frozenset(
    {UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX}
)


class VolumeDeltaStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    BASELINE_ESTABLISHED = "BASELINE_ESTABLISHED"
    CUMULATIVE_VOLUME_UNAVAILABLE = "CUMULATIVE_VOLUME_UNAVAILABLE"
    CUMULATIVE_VOLUME_DECREASE = "CUMULATIVE_VOLUME_DECREASE"
    SEQUENCE_GAP_UNALLOCATABLE = "SEQUENCE_GAP_UNALLOCATABLE"
    SEQUENCE_REGRESSION_UNALLOCATABLE = "SEQUENCE_REGRESSION_UNALLOCATABLE"
    MISSING_INTERVAL_UNALLOCATABLE = "MISSING_INTERVAL_UNALLOCATABLE"


@dataclass(frozen=True)
class RubixQuoteInput:
    canonical_ticker: str
    verified_rubix_symbol: str
    market_timestamp: datetime
    receive_timestamp: datetime
    sequence: int | None
    last_price: float | None
    cumulative_volume: float | None
    bid: float | None
    ask: float | None
    has_feed_timestamp: bool
    source_event_type: SourceEventType = SourceEventType.RUBIX_QUOTE
    source_row_id: int | str | None = None


@dataclass(frozen=True)
class DataQualityEvent:
    code: str
    observed_at_utc: datetime
    canonical_ticker: str | None = None
    session_date: date | None = None
    detail: str | None = None
    source_identity: str | None = None


@dataclass(frozen=True)
class NormalizedIntradayEvent:
    canonical_ticker: str
    verified_rubix_symbol: str
    session_date: date
    market_timestamp_utc: datetime
    receive_timestamp_utc: datetime
    sequence: int | None
    last_price: float | None
    cumulative_volume: float | None
    bid: float | None
    ask: float | None
    source_event_type: SourceEventType
    feed_timestamp_quality: FeedTimestampQuality
    source_identity: str
    quote_age_seconds: float
    spread_absolute: float | None
    spread_percent: float | None
    spread_capability: SpreadCapability
    volume_capability: VolumeCapability
    sequence_gap: int
    duplicate_status: str
    out_of_order_status: str
    session_phase: OrbSessionPhase
    quality_flags: tuple[str, ...]
    volume_delta: float | None = None
    volume_delta_status: VolumeDeltaStatus = (
        VolumeDeltaStatus.CUMULATIVE_VOLUME_UNAVAILABLE
    )
    universe_membership_status: UniverseMembershipStatus = (
        UniverseMembershipStatus.UNKNOWN_UNMAPPED_IDENTIFIER
    )
    operationally_eligible: bool = False
    market_time_status: MarketTimeStatus = MarketTimeStatus.MARKET_TIME_UNRELIABLE
    historical_replay_status: HistoricalReplayStatus = (
        HistoricalReplayStatus.HISTORICAL_REPLAY_REJECTED
    )
    live_freshness_status: LiveFreshnessStatus = (
        LiveFreshnessStatus.COLLECTOR_FRESHNESS_UNAVAILABLE
    )
    receive_lag_seconds: float | None = None
    collector_age_seconds: float | None = None


@dataclass(frozen=True)
class NormalizationBatch:
    events: tuple[NormalizedIntradayEvent, ...]
    quality_events: tuple[DataQualityEvent, ...]


@dataclass(frozen=True)
class DeduplicationTelemetry:
    """Bounded-dedup state, recorded per cycle as liveness evidence."""

    entries: int
    capacity: int
    hard_capacity: int
    admitted: int
    evictions: int
    exact_redeliveries: int
    capacity_exhausted: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "dedupe_entries": self.entries,
            "dedupe_capacity": self.capacity,
            "dedupe_hard_capacity": self.hard_capacity,
            "dedupe_admitted": self.admitted,
            "dedupe_evictions": self.evictions,
            "exact_redeliveries": self.exact_redeliveries,
            "dedupe_capacity_exhausted": self.capacity_exhausted,
        }


def _number(value, *, nonnegative=False, positive=False) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    if positive and result <= 0:
        return None
    if nonnegative and result < 0:
        return None
    return result


def _source_identity(raw: RubixQuoteInput) -> str:
    """Hash verified market fields only; never serialize arbitrary raw payloads."""

    payload = {
        "canonical_ticker": str(raw.canonical_ticker).strip().upper(),
        "verified_rubix_symbol": str(raw.verified_rubix_symbol).strip().upper(),
        "market_timestamp": require_aware(raw.market_timestamp).astimezone(
            timezone.utc
        ).isoformat(),
        "receive_timestamp": require_aware(raw.receive_timestamp).astimezone(
            timezone.utc
        ).isoformat(),
        "sequence": raw.sequence,
        "last_price": _number(raw.last_price, positive=True),
        "cumulative_volume": _number(raw.cumulative_volume, nonnegative=True),
        "bid": _number(raw.bid, positive=True),
        "ask": _number(raw.ask, positive=True),
        "source_event_type": raw.source_event_type.value,
        "source_row_id": str(raw.source_row_id) if raw.source_row_id is not None else None,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sequence_payload_identity(raw: RubixQuoteInput) -> str:
    """Fingerprint market payload while ignoring receive/replay metadata."""

    payload = {
        "canonical_ticker": str(raw.canonical_ticker).strip().upper(),
        "verified_rubix_symbol": str(raw.verified_rubix_symbol).strip().upper(),
        "market_timestamp": require_aware(raw.market_timestamp).astimezone(
            timezone.utc
        ).isoformat(),
        "sequence": raw.sequence,
        "last_price": _number(raw.last_price, positive=True),
        "cumulative_volume": _number(raw.cumulative_volume, nonnegative=True),
        "bid": _number(raw.bid, positive=True),
        "ask": _number(raw.ask, positive=True),
        "source_event_type": raw.source_event_type.value,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _default_mapping_validator(canonical: str, rubix: str) -> bool:
    record = lookup(canonical)
    return bool(
        record
        and record.has_verified_rubix_mapping
        and record.rubix_symbol.upper() == rubix.upper()
    )


def _default_membership_resolver(canonical: str) -> UniverseMembershipStatus:
    record = lookup(canonical)
    if record is None:
        return UniverseMembershipStatus.UNKNOWN_UNMAPPED_IDENTIFIER
    if not record.is_active:
        return UniverseMembershipStatus.ARCHIVED_INACTIVE_SYMBOL
    if record.has_verified_rubix_mapping:
        return UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX
    return UniverseMembershipStatus.ACTIVE_UNIVERSE_UNVERIFIED_RUBIX


class RubixEventNormalizer:
    """Stateful per-symbol/session normalizer with deterministic deduplication."""

    def __init__(
        self,
        config: OrbDataConfig | None = None,
        *,
        holidays=None,
        mapping_validator: Callable[[str, str], bool] | None = None,
        membership_resolver: Callable[[str], UniverseMembershipStatus] | None = None,
        mode: NormalizationMode = NormalizationMode.LIVE,
    ):
        self.config = config or OrbDataConfig()
        self.classifier = OrbSessionClassifier(self.config, holidays=holidays)
        self.mapping_validator = mapping_validator or _default_mapping_validator
        self.membership_resolver = membership_resolver or _default_membership_resolver
        self.mode = NormalizationMode(mode)
        self._seen_sequences: dict[tuple[str, date], dict[int, str]] = {}
        self._seen_payloads: OrderedDict[tuple[tuple[str, date], str], None] = (
            OrderedDict()
        )
        self._last_market: dict[tuple[str, date], datetime] = {}
        self._last_sequence: dict[tuple[str, date], int] = {}
        self._active_session_date: date | None = None
        self._seen_payload_count = 0
        self._dedupe_evictions = 0
        self._exact_redeliveries = 0
        self._capacity_exhausted = False

    def deduplication_telemetry(self) -> DeduplicationTelemetry:
        """Expose bounded-dedup counters so a run can prove it is still live."""

        return DeduplicationTelemetry(
            entries=len(self._seen_payloads),
            capacity=int(self.config.deduplication_retention_payloads),
            hard_capacity=int(self.config.deduplication_hard_capacity),
            admitted=self._seen_payload_count,
            evictions=self._dedupe_evictions,
            exact_redeliveries=self._exact_redeliveries,
            capacity_exhausted=self._capacity_exhausted,
        )

    def _roll_session(self, session_date: date) -> bool:
        """Keep live dedup/order state bounded to one current market session."""

        if self._active_session_date is None:
            self._active_session_date = session_date
            return True
        if session_date == self._active_session_date:
            return True
        if session_date < self._active_session_date:
            return False
        self._active_session_date = session_date
        self._seen_sequences.clear()
        self._seen_payloads.clear()
        self._last_market.clear()
        self._last_sequence.clear()
        self._seen_payload_count = 0
        self._dedupe_evictions = 0
        self._exact_redeliveries = 0
        self._capacity_exhausted = False
        return True

    def normalize_many(
        self, raw_events: Iterable[RubixQuoteInput], *, evaluated_at: datetime
    ) -> NormalizationBatch:
        evaluated = require_aware(evaluated_at, "evaluated_at").astimezone(timezone.utc)
        accepted: list[NormalizedIntradayEvent] = []
        quality: list[DataQualityEvent] = []
        for raw in raw_events:
            event, issues = self.normalize(raw, evaluated_at=evaluated)
            quality.extend(issues)
            if event is not None:
                accepted.append(event)
        return NormalizationBatch(tuple(accepted), tuple(quality))

    def normalize(
        self, raw: RubixQuoteInput, *, evaluated_at: datetime
    ) -> tuple[NormalizedIntradayEvent | None, tuple[DataQualityEvent, ...]]:
        evaluated = require_aware(evaluated_at, "evaluated_at").astimezone(timezone.utc)
        market = require_aware(raw.market_timestamp, "market_timestamp").astimezone(
            timezone.utc
        )
        received = require_aware(
            raw.receive_timestamp, "receive_timestamp"
        ).astimezone(timezone.utc)
        canonical = str(raw.canonical_ticker).strip().upper()
        rubix = str(raw.verified_rubix_symbol).strip().upper()
        source_identity = _source_identity(raw)
        sequence_identity = _sequence_payload_identity(raw)
        session_date = self.classifier.session_date(market)
        phase = self.classifier.classify(market)
        key = (canonical, session_date)
        issues: list[DataQualityEvent] = []

        def issue(code: str, detail: str | None = None) -> None:
            issues.append(
                DataQualityEvent(
                    code=code,
                    observed_at_utc=evaluated,
                    canonical_ticker=canonical or None,
                    session_date=session_date,
                    detail=detail,
                    source_identity=source_identity,
                )
            )

        if not canonical or not rubix or not self.mapping_validator(canonical, rubix):
            issue("RUBIX_MAPPING_UNAVAILABLE")
            return None, tuple(issues)

        if not self._roll_session(session_date):
            issue("CROSS_SESSION_LATE_EVENT_REJECTED")
            return None, tuple(issues)

        sequence = int(raw.sequence) if raw.sequence is not None else None
        if sequence is not None:
            seen = self._seen_sequences.setdefault(key, {})
            if sequence in seen:
                if (
                    self.config.duplicate_sequence_policy
                    == DuplicateSequencePolicy.REJECT_ALL
                ):
                    issue("DUPLICATE_SEQUENCE_REJECTED")
                elif seen[sequence] == sequence_identity:
                    issue("DUPLICATE_SEQUENCE_IDENTICAL")
                else:
                    issue("DUPLICATE_SEQUENCE_CONFLICT")
                return None, tuple(issues)

        # Provider sequence is absent from every observed Rubix row, so the
        # market payload itself is the only deterministic identity available.
        # Two events that differ in any verified market field keep distinct
        # identities even when they share a timestamp; an exact redelivery
        # (reconnect or replay) collapses regardless of receive time or row id.
        #
        # Retention is a rolling window, not a session-wide set: the source
        # redelivers within a few hundred rows, while a full session carries
        # roughly half a million distinct identities. Evicting the oldest
        # identity keeps memory bounded without ever discarding an unseen
        # payload — the failure that silently truncated 2026-08-04.
        payload_key = (key, sequence_identity)
        if payload_key in self._seen_payloads:
            self._seen_payloads.move_to_end(payload_key)
            self._exact_redeliveries += 1
            issue("DUPLICATE_MARKET_PAYLOAD_IDENTICAL")
            return None, tuple(issues)
        if len(self._seen_payloads) > int(self.config.deduplication_hard_capacity):
            # Unreachable while eviction works; latched so that a regression
            # surfaces as an explicit defect instead of silent data loss.
            self._capacity_exhausted = True
            issue(
                "DEDUPLICATION_CAPACITY_EXHAUSTED",
                f"capacity={self.config.deduplication_hard_capacity}",
            )

        flags: list[str] = []
        last_market = self._last_market.get(key)
        out_of_order_status = "ORDERED"
        if last_market is not None and market < last_market:
            seconds = (last_market - market).total_seconds()
            if seconds > self.config.out_of_order_tolerance_seconds:
                if self.mode == NormalizationMode.LIVE:
                    issue("OUT_OF_ORDER_REJECTED", f"lag_seconds={seconds:.6f}")
                    return None, tuple(issues)
                out_of_order_status = "LATE_CORRECTION_ONLY"
                flags.append("LATE_CORRECTION_ONLY")
                issue("LATE_CORRECTION_ONLY", f"lag_seconds={seconds:.6f}")
            else:
                out_of_order_status = "OUT_OF_ORDER_WITHIN_TOLERANCE"
                flags.append(out_of_order_status)

        sequence_gap = 0
        last_sequence = self._last_sequence.get(key)
        if sequence is not None and last_sequence is not None:
            if sequence > last_sequence + 1:
                sequence_gap = sequence - last_sequence - 1
                flags.append("SEQUENCE_GAP")
            elif sequence < last_sequence:
                flags.append("SEQUENCE_REGRESSION")

        price = _number(raw.last_price, positive=True)
        cumulative = _number(raw.cumulative_volume, nonnegative=True)
        bid = _number(raw.bid, positive=True)
        ask = _number(raw.ask, positive=True)
        if price is None:
            flags.append("PRICE_UNAVAILABLE")
        if cumulative is None:
            flags.append("CUMULATIVE_VOLUME_UNAVAILABLE")

        spread_absolute = spread_percent = None
        spread_capability = SpreadCapability.SPREAD_UNAVAILABLE
        if bid is not None and ask is not None and ask >= bid:
            spread_absolute = ask - bid
            midpoint = (ask + bid) / 2
            spread_percent = (spread_absolute / midpoint * 100) if midpoint else None
            spread_capability = SpreadCapability.SPREAD_AVAILABLE
        else:
            flags.append("SPREAD_UNAVAILABLE")
            if bid is not None and ask is not None and ask < bid:
                flags.append("CROSSED_QUOTE")

        receive_lag = (received - market).total_seconds()
        collector_age = (evaluated - received).total_seconds()
        age = receive_lag
        if receive_lag < -self.config.out_of_order_tolerance_seconds:
            flags.append("FUTURE_MARKET_TIMESTAMP")
        if receive_lag > self.config.maximum_quote_age_seconds:
            flags.append("STALE_QUOTE")
            flags.append("LIVE_FRESHNESS_FAILED")
        if received < market:
            flags.append("RECEIVE_BEFORE_MARKET_TIMESTAMP")

        if not raw.has_feed_timestamp or received < market:
            market_time_status = MarketTimeStatus.MARKET_TIME_UNRELIABLE
            historical_status = HistoricalReplayStatus.HISTORICAL_REPLAY_REJECTED
            live_freshness = LiveFreshnessStatus.LIVE_FRESHNESS_FAILED
            flags.extend(("MARKET_TIME_UNRELIABLE", "LIVE_FRESHNESS_FAILED"))
        else:
            market_time_status = (
                MarketTimeStatus.MARKET_TIME_VALID_RECEIVE_DELAYED
                if receive_lag > self.config.maximum_quote_age_seconds
                else MarketTimeStatus.MARKET_TIME_VALID
            )
            historical_status = (
                HistoricalReplayStatus.LATE_CORRECTION_ONLY
                if out_of_order_status == "LATE_CORRECTION_ONLY"
                else HistoricalReplayStatus.HISTORICAL_REPLAY_ACCEPTED
            )
            live_freshness = (
                LiveFreshnessStatus.LIVE_FRESHNESS_PASSED
                if receive_lag <= self.config.maximum_quote_age_seconds
                and collector_age <= self.config.maximum_quote_age_seconds
                and collector_age >= -self.config.out_of_order_tolerance_seconds
                else LiveFreshnessStatus.LIVE_FRESHNESS_FAILED
            )
            if market_time_status == MarketTimeStatus.MARKET_TIME_VALID_RECEIVE_DELAYED:
                flags.append(market_time_status.value)
            if live_freshness == LiveFreshnessStatus.LIVE_FRESHNESS_FAILED:
                flags.append(live_freshness.value)

        membership = self.membership_resolver(canonical)
        if membership not in OPERATIONALLY_ELIGIBLE_MEMBERSHIP:
            flags.append(f"UNIVERSE_MEMBERSHIP:{membership.value}")
            flags.append("NOT_OPERATIONALLY_ELIGIBLE")

        event = NormalizedIntradayEvent(
            canonical_ticker=canonical,
            verified_rubix_symbol=rubix,
            session_date=session_date,
            market_timestamp_utc=market,
            receive_timestamp_utc=received,
            sequence=sequence,
            last_price=price,
            cumulative_volume=cumulative,
            bid=bid,
            ask=ask,
            source_event_type=raw.source_event_type,
            feed_timestamp_quality=(
                FeedTimestampQuality.VERIFIED_FEED_TIMESTAMP
                if raw.has_feed_timestamp
                else FeedTimestampQuality.RECEIPT_TIME_ONLY
            ),
            source_identity=source_identity,
            quote_age_seconds=age,
            spread_absolute=spread_absolute,
            spread_percent=spread_percent,
            spread_capability=spread_capability,
            volume_capability=(
                VolumeCapability.VOLUME_AVAILABLE
                if cumulative is not None
                else VolumeCapability.VOLUME_UNAVAILABLE
            ),
            sequence_gap=sequence_gap,
            duplicate_status="UNIQUE",
            out_of_order_status=out_of_order_status,
            session_phase=phase,
            quality_flags=tuple(dict.fromkeys(flags)),
            universe_membership_status=membership,
            operationally_eligible=membership in OPERATIONALLY_ELIGIBLE_MEMBERSHIP,
            market_time_status=market_time_status,
            historical_replay_status=historical_status,
            live_freshness_status=live_freshness,
            receive_lag_seconds=receive_lag,
            collector_age_seconds=collector_age,
        )
        while len(self._seen_payloads) >= int(
            self.config.deduplication_retention_payloads
        ):
            self._seen_payloads.popitem(last=False)
            self._dedupe_evictions += 1
        self._seen_payloads[payload_key] = None
        self._seen_payload_count += 1
        if sequence is not None:
            self._seen_sequences.setdefault(key, {})[sequence] = sequence_identity
            self._last_sequence[key] = max(sequence, last_sequence or sequence)
        if last_market is None or market > last_market:
            self._last_market[key] = market
        return event, tuple(issues)


class CumulativeVolumeTracker:
    """Convert cumulative volume to deltas only when allocation is defensible."""

    def __init__(self, config: OrbDataConfig | None = None):
        self.config = config or OrbDataConfig()
        self._last: dict[tuple[str, date], float] = {}
        self._last_timestamp: dict[tuple[str, date], datetime] = {}

    def apply(self, event: NormalizedIntradayEvent) -> NormalizedIntradayEvent:
        key = (event.canonical_ticker, event.session_date)
        current = event.cumulative_volume
        previous_timestamp = self._last_timestamp.get(key)
        self._last_timestamp[key] = event.market_timestamp_utc
        if current is None:
            return replace(
                event,
                volume_delta=None,
                volume_delta_status=VolumeDeltaStatus.CUMULATIVE_VOLUME_UNAVAILABLE,
            )
        previous = self._last.get(key)
        self._last[key] = current
        if previous is None:
            return replace(
                event,
                volume_delta=None,
                volume_delta_status=VolumeDeltaStatus.BASELINE_ESTABLISHED,
            )
        if "SEQUENCE_REGRESSION" in event.quality_flags:
            return replace(
                event,
                volume_delta=None,
                volume_delta_status=VolumeDeltaStatus.SEQUENCE_REGRESSION_UNALLOCATABLE,
            )
        if event.sequence_gap:
            return replace(
                event,
                volume_delta=None,
                volume_delta_status=VolumeDeltaStatus.SEQUENCE_GAP_UNALLOCATABLE,
            )
        if current < previous:
            return replace(
                event,
                volume_delta=None,
                volume_delta_status=VolumeDeltaStatus.CUMULATIVE_VOLUME_DECREASE,
            )
        if (
            previous_timestamp is not None
            and current > previous
            and (event.market_timestamp_utc - previous_timestamp).total_seconds()
            > self.config.maximum_volume_delta_gap_seconds
        ):
            return replace(
                event,
                volume_delta=None,
                volume_delta_status=VolumeDeltaStatus.MISSING_INTERVAL_UNALLOCATABLE,
            )
        return replace(
            event,
            volume_delta=current - previous,
            volume_delta_status=VolumeDeltaStatus.AVAILABLE,
        )

    def apply_many(
        self, events: Iterable[NormalizedIntradayEvent]
    ) -> tuple[NormalizedIntradayEvent, ...]:
        return tuple(self.apply(event) for event in events)
