"""Deterministic completed 1m/5m bars for the ORB data foundation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
from typing import Iterable
from zoneinfo import ZoneInfo

from scalping_orb.config import MissingVolumePolicy, OrbDataConfig
from scalping_orb.events import (
    DataQualityEvent,
    MarketTimeStatus,
    NormalizedIntradayEvent,
    VolumeDeltaStatus,
)
from scalping_orb.session import (
    CONTINUOUS_PHASES,
    OrbSessionClassifier,
    OrbSessionPhase,
    require_aware,
)


@dataclass(frozen=True)
class CompletedBar:
    canonical_ticker: str
    interval_minutes: int
    session_date: date
    bar_start_utc: datetime
    bar_end_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    update_count: int
    first_sequence: int | None
    last_sequence: int | None
    data_quality_flags: tuple[str, ...]
    completed: bool
    session_phase: OrbSessionPhase
    source_identity: str
    component_bar_count: int = 0


@dataclass(frozen=True)
class BarAggregationResult:
    bars: tuple[CompletedBar, ...]
    quality_events: tuple[DataQualityEvent, ...]


def _bucket_start(
    value: datetime, interval_minutes: int, zone: ZoneInfo
) -> datetime:
    local = require_aware(value).astimezone(zone)
    minute = (local.minute // interval_minutes) * interval_minutes
    return local.replace(minute=minute, second=0, microsecond=0).astimezone(
        timezone.utc
    )


def _identity(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(values):
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def aggregate_completed_one_minute_bars(
    events: Iterable[NormalizedIntradayEvent],
    *,
    as_of: datetime,
    config: OrbDataConfig | None = None,
) -> BarAggregationResult:
    """Build closed 1m price bars; absent source minutes remain absent."""

    cfg = config or OrbDataConfig()
    evaluated = require_aware(as_of, "as_of").astimezone(timezone.utc)
    zone = ZoneInfo(cfg.timezone)
    groups: dict[tuple[str, date, datetime], list[NormalizedIntradayEvent]] = {}
    quality: list[DataQualityEvent] = []
    for event in sorted(
        events,
        key=lambda item: (
            item.market_timestamp_utc,
            item.canonical_ticker,
            item.sequence if item.sequence is not None else -1,
            item.source_identity,
        ),
    ):
        if event.market_time_status == MarketTimeStatus.MARKET_TIME_UNRELIABLE:
            quality.append(
                DataQualityEvent(
                    code="MARKET_TIME_UNRELIABLE_EXCLUDED",
                    observed_at_utc=evaluated,
                    canonical_ticker=event.canonical_ticker,
                    session_date=event.session_date,
                    source_identity=event.source_identity,
                )
            )
            continue
        if event.session_phase not in CONTINUOUS_PHASES:
            if event.session_phase == OrbSessionPhase.CLOSING_AUCTION:
                quality.append(
                    DataQualityEvent(
                        code="AUCTION_EVENT_EXCLUDED",
                        observed_at_utc=evaluated,
                        canonical_ticker=event.canonical_ticker,
                        session_date=event.session_date,
                        source_identity=event.source_identity,
                    )
                )
            continue
        start = _bucket_start(event.market_timestamp_utc, 1, zone)
        groups.setdefault((event.canonical_ticker, event.session_date, start), []).append(
            event
        )

    bars: list[CompletedBar] = []
    classifier = OrbSessionClassifier(cfg, holidays=())
    for (ticker, session_date, start), members in sorted(groups.items()):
        end = start + timedelta(minutes=1)
        if end > evaluated:
            quality.append(
                DataQualityEvent(
                    code="PARTIAL_ONE_MINUTE_BAR_EXCLUDED",
                    observed_at_utc=evaluated,
                    canonical_ticker=ticker,
                    session_date=session_date,
                    detail=f"bar_end={end.isoformat()}",
                )
            )
            continue
        prices = [member.last_price for member in members if member.last_price is not None]
        if not prices:
            quality.append(
                DataQualityEvent(
                    code="BAR_PRICE_UNAVAILABLE",
                    observed_at_utc=evaluated,
                    canonical_ticker=ticker,
                    session_date=session_date,
                )
            )
            continue
        flags = list(
            dict.fromkeys(flag for member in members for flag in member.quality_flags)
        )
        delta_statuses = tuple(member.volume_delta_status for member in members)
        volume_valid = bool(delta_statuses) and all(
            status == VolumeDeltaStatus.AVAILABLE for status in delta_statuses
        )
        volume = (
            sum(float(member.volume_delta or 0) for member in members)
            if volume_valid
            else None
        )
        if volume is None:
            flags.append("VOLUME_UNAVAILABLE")
            flags.extend(
                f"VOLUME_{status.value}" for status in delta_statuses if status != VolumeDeltaStatus.AVAILABLE
            )
            if cfg.missing_volume_policy == MissingVolumePolicy.REJECT_BAR:
                quality.append(
                    DataQualityEvent(
                        code="BAR_REJECTED_VOLUME_UNAVAILABLE",
                        observed_at_utc=evaluated,
                        canonical_ticker=ticker,
                        session_date=session_date,
                    )
                )
                continue
        if len(members) < cfg.minimum_updates_per_completed_bar:
            flags.append("INSUFFICIENT_UPDATES")
        sequences = [member.sequence for member in members if member.sequence is not None]
        bars.append(
            CompletedBar(
                canonical_ticker=ticker,
                interval_minutes=1,
                session_date=session_date,
                bar_start_utc=start,
                bar_end_utc=end,
                open=float(prices[0]),
                high=float(max(prices)),
                low=float(min(prices)),
                close=float(prices[-1]),
                volume=volume,
                update_count=len(members),
                first_sequence=min(sequences) if sequences else None,
                last_sequence=max(sequences) if sequences else None,
                data_quality_flags=tuple(dict.fromkeys(flags)),
                completed=True,
                session_phase=classifier.classify(start),
                source_identity=_identity(member.source_identity for member in members),
                component_bar_count=0,
            )
        )
    return BarAggregationResult(tuple(bars), tuple(quality))


def aggregate_completed_five_minute_bars(
    one_minute_bars: Iterable[CompletedBar],
    *,
    as_of: datetime,
    config: OrbDataConfig | None = None,
) -> BarAggregationResult:
    """Aggregate only five exact completed 1m slots into a completed 5m bar."""

    cfg = config or OrbDataConfig()
    evaluated = require_aware(as_of, "as_of").astimezone(timezone.utc)
    zone = ZoneInfo(cfg.timezone)
    groups: dict[tuple[str, date, datetime], list[CompletedBar]] = {}
    quality: list[DataQualityEvent] = []
    for bar in sorted(
        one_minute_bars,
        key=lambda item: (item.bar_start_utc, item.canonical_ticker),
    ):
        if bar.interval_minutes != 1 or not bar.completed:
            continue
        if bar.session_phase not in CONTINUOUS_PHASES:
            quality.append(
                DataQualityEvent(
                    code="NON_CONTINUOUS_COMPONENT_EXCLUDED",
                    observed_at_utc=evaluated,
                    canonical_ticker=bar.canonical_ticker,
                    session_date=bar.session_date,
                    source_identity=bar.source_identity,
                )
            )
            continue
        start = _bucket_start(bar.bar_start_utc, 5, zone)
        groups.setdefault((bar.canonical_ticker, bar.session_date, start), []).append(bar)

    result: list[CompletedBar] = []
    classifier = OrbSessionClassifier(cfg, holidays=())
    for (ticker, session_date, start), members in sorted(groups.items()):
        end = start + timedelta(minutes=5)
        if end > evaluated:
            quality.append(
                DataQualityEvent(
                    code="PARTIAL_FIVE_MINUTE_BAR_EXCLUDED",
                    observed_at_utc=evaluated,
                    canonical_ticker=ticker,
                    session_date=session_date,
                    detail=f"bar_end={end.isoformat()}",
                )
            )
            continue
        by_slot = {member.bar_start_utc: member for member in members}
        expected = tuple(start + timedelta(minutes=offset) for offset in range(5))
        if any(slot not in by_slot for slot in expected):
            quality.append(
                DataQualityEvent(
                    code="INCOMPLETE_FIVE_MINUTE_COMPONENTS",
                    observed_at_utc=evaluated,
                    canonical_ticker=ticker,
                    session_date=session_date,
                    detail=f"observed={len(by_slot)};expected=5",
                )
            )
            continue
        ordered = [by_slot[slot] for slot in expected]
        flags = list(
            dict.fromkeys(flag for member in ordered for flag in member.data_quality_flags)
        )
        volume = (
            sum(float(member.volume) for member in ordered)
            if all(member.volume is not None for member in ordered)
            else None
        )
        if volume is None:
            flags.append("VOLUME_UNAVAILABLE")
            if cfg.missing_volume_policy == MissingVolumePolicy.REJECT_BAR:
                continue
        sequences = [
            value
            for member in ordered
            for value in (member.first_sequence, member.last_sequence)
            if value is not None
        ]
        result.append(
            CompletedBar(
                canonical_ticker=ticker,
                interval_minutes=5,
                session_date=session_date,
                bar_start_utc=start,
                bar_end_utc=end,
                open=ordered[0].open,
                high=max(member.high for member in ordered),
                low=min(member.low for member in ordered),
                close=ordered[-1].close,
                volume=volume,
                update_count=sum(member.update_count for member in ordered),
                first_sequence=min(sequences) if sequences else None,
                last_sequence=max(sequences) if sequences else None,
                data_quality_flags=tuple(dict.fromkeys(flags)),
                completed=True,
                session_phase=classifier.classify(start),
                source_identity=_identity(member.source_identity for member in ordered),
                component_bar_count=5,
            )
        )
    return BarAggregationResult(tuple(result), tuple(quality))
