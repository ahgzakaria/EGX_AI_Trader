"""The shared immutable session snapshot, and the exchange-time watermark.

This module closes ``REPLAY_SESSION_BATCHING_REQUIRED``.

Phase 2B loaded the whole session once per symbol. The SQL-filter fix removed
the worst factor but left the shape intact. Here a batch is read once,
normalized once and grouped once; the evaluator then reads symbols *out of the
snapshot* and never returns to the database per symbol.

Two clocks are kept strictly apart:

* the **exchange watermark** — derived from ``market_timestamp`` — decides which
  bars are closed;
* the **source cursor watermark** — ``quotes.id`` — decides what has been read.

Conflating them is how a live pipeline convinces itself a bar is final because
it happens to have stopped receiving rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from scalping_orb.bars import (
    CompletedBar,
    aggregate_completed_five_minute_bars,
    aggregate_completed_one_minute_bars,
)
from scalping_orb.config import OrbDataConfig
from scalping_orb.engine import DailyContext, OpeningRangeSnapshot
from scalping_orb.events import (
    DataQualityEvent,
    NormalizedIntradayEvent,
    UniverseMembershipStatus,
    VolumeCapability,
)
from scalping_orb.opening_range import OpeningRangeStatus, build_opening_range
from scalping_orb.session import CONTINUOUS_PHASES, OrbSessionClassifier, OrbSessionPhase, require_aware


class BarFinality(str, Enum):
    """Why a completed bar is, or is not, operationally final for Lane A."""

    OPERATIONALLY_FINAL = "OPERATIONALLY_FINAL"
    BOUNDARY_NOT_PASSED = "BOUNDARY_NOT_PASSED"
    LATENESS_GRACE_PENDING = "LATENESS_GRACE_PENDING"
    AUCTION_EXCLUDED = "AUCTION_EXCLUDED"
    LIVE_EVIDENCE_STALE = "LIVE_EVIDENCE_STALE"


@dataclass(frozen=True)
class ExchangeWatermark:
    """What exchange time we are entitled to consider settled.

    ``operational_cutoff`` is the watermark minus the configured lateness
    grace: a bar may only be promoted for Lane A once its end has passed that,
    giving genuinely late events a bounded window to arrive first.
    """

    session_date: date
    latest_market_timestamp_utc: datetime | None
    latest_receive_timestamp_utc: datetime | None
    evaluated_at_utc: datetime
    lateness_grace_seconds: float
    freshness_budget_seconds: float

    @property
    def operational_cutoff_utc(self) -> datetime:
        """Bars ending at or before this instant may become final."""

        return self.evaluated_at_utc - timedelta(seconds=self.lateness_grace_seconds)

    @property
    def observed_receive_lag_seconds(self) -> float | None:
        if (
            self.latest_market_timestamp_utc is None
            or self.latest_receive_timestamp_utc is None
        ):
            return None
        return (
            self.latest_receive_timestamp_utc - self.latest_market_timestamp_utc
        ).total_seconds()

    @property
    def live_evidence_fresh(self) -> bool:
        """Negative lag is clock skew, not freshness. It is not treated as fresh."""

        lag = self.observed_receive_lag_seconds
        if lag is None:
            return False
        return 0.0 <= lag <= self.freshness_budget_seconds

    def classify(self, bar: CompletedBar) -> BarFinality:
        if bar.session_phase not in CONTINUOUS_PHASES:
            return BarFinality.AUCTION_EXCLUDED
        if bar.bar_end_utc > self.evaluated_at_utc:
            return BarFinality.BOUNDARY_NOT_PASSED
        if bar.bar_end_utc > self.operational_cutoff_utc:
            return BarFinality.LATENESS_GRACE_PENDING
        if not self.live_evidence_fresh:
            return BarFinality.LIVE_EVIDENCE_STALE
        return BarFinality.OPERATIONALLY_FINAL


@dataclass(frozen=True)
class SessionQualitySummary:
    """Counts only. Deliberately carries no performance measure."""

    source_rows_observed: int = 0
    normalized_events: int = 0
    exact_redeliveries_removed: int = 0
    same_timestamp_distinct_retained: int = 0
    quality_event_count: int = 0
    observed_one_minute_slots: int = 0
    completed_one_minute_bars: int = 0
    completed_five_minute_bars: int = 0
    missing_one_minute_slots: int = 0
    volume_valid_bars: int = 0
    volume_unavailable_bars: int = 0
    stale_live_bars: int = 0
    reconstructable_historical_bars: int = 0
    opening_ranges_ready: int = 0
    opening_ranges_insufficient: int = 0
    archived_symbols_excluded: int = 0
    unmapped_symbols_excluded: int = 0
    negative_lag_events: int = 0
    out_of_order_events: int = 0
    late_events_after_cutoff: int = 0

    def as_dict(self) -> dict:
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__  # type: ignore[attr-defined]
        }


@dataclass(frozen=True)
class ShadowSessionSnapshot:
    """One immutable, content-identified view of a session batch.

    Everything the evaluator needs is grouped by ticker *once*. Nothing here
    reads a database; the snapshot is built from an already-loaded batch.
    """

    session_date: date
    cursor_low_source_id: int
    cursor_high_source_id: int
    evaluated_at_utc: datetime
    watermark: ExchangeWatermark
    events_by_ticker: Mapping[str, tuple[NormalizedIntradayEvent, ...]]
    one_minute_by_ticker: Mapping[str, tuple[CompletedBar, ...]]
    five_minute_by_ticker: Mapping[str, tuple[CompletedBar, ...]]
    opening_ranges: Mapping[str, OpeningRangeSnapshot]
    eligibility: Mapping[str, UniverseMembershipStatus]
    operationally_eligible: Mapping[str, bool]
    volume_capability: Mapping[str, VolumeCapability]
    daily_context: Mapping[str, DailyContext]
    quality_summary: SessionQualitySummary
    quality_events: tuple[DataQualityEvent, ...]
    config_identity: str
    #: Tickers with a newly operationally-final 5m bar in this batch. The
    #: evaluator re-evaluates only these; an untouched symbol is not rebuilt.
    affected_tickers: tuple[str, ...] = ()

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(sorted(self.events_by_ticker))

    @property
    def snapshot_identity(self) -> str:
        """Content-derived, so an identical batch yields an identical id."""

        payload = {
            "session_date": self.session_date.isoformat(),
            "cursor": [self.cursor_low_source_id, self.cursor_high_source_id],
            "config": self.config_identity,
            "tickers": {
                ticker: {
                    "events": len(self.events_by_ticker.get(ticker, ())),
                    "one_minute": [
                        bar.source_identity
                        for bar in self.one_minute_by_ticker.get(ticker, ())
                    ],
                    "five_minute": [
                        bar.source_identity
                        for bar in self.five_minute_by_ticker.get(ticker, ())
                    ],
                    "opening_range": (
                        self.opening_ranges[ticker].version_identity()
                        if ticker in self.opening_ranges
                        else None
                    ),
                }
                for ticker in self.tickers
            },
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def final_five_minute_bars(self, ticker: str) -> tuple[CompletedBar, ...]:
        """Bars Lane A is entitled to act on, in exchange order."""

        return tuple(
            bar
            for bar in self.five_minute_by_ticker.get(ticker, ())
            if self.watermark.classify(bar) is BarFinality.OPERATIONALLY_FINAL
        )


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile. Deterministic and dependency-free."""

    if not values:
        return None
    ordered = sorted(values)
    index = int(round(fraction * (len(ordered) - 1)))
    return ordered[max(0, min(index, len(ordered) - 1))]


class ShadowSnapshotBuilder:
    """Build one snapshot from one already-loaded batch of normalized events.

    The whole point is that this runs **once** per batch. It groups by ticker,
    aggregates bars per ticker from that grouping, freezes opening ranges, and
    hands the evaluator a structure it can read without further I/O.
    """

    def __init__(
        self,
        config: OrbDataConfig | None = None,
        *,
        lateness_grace_seconds: float = 90.0,
    ):
        self.config = config or OrbDataConfig()
        self.zone = ZoneInfo(self.config.timezone)
        self.classifier = OrbSessionClassifier(self.config)
        if float(lateness_grace_seconds) < 0:
            raise ValueError("lateness_grace_seconds cannot be negative")
        self.lateness_grace_seconds = float(lateness_grace_seconds)

    def build(
        self,
        session_date: date,
        events: Iterable[NormalizedIntradayEvent],
        *,
        as_of: datetime,
        cursor_low_source_id: int,
        cursor_high_source_id: int,
        quality_events: Sequence[DataQualityEvent] = (),
        daily_context: Mapping[str, DailyContext] | None = None,
        source_rows_observed: int = 0,
        previous_final_bar_keys: frozenset[tuple[str, datetime]] | None = None,
    ) -> ShadowSessionSnapshot:
        evaluated = require_aware(as_of, "as_of").astimezone(timezone.utc)
        collected = [
            event for event in events if event.session_date == session_date
        ]

        # --- group once -------------------------------------------------
        events_by_ticker: dict[str, list[NormalizedIntradayEvent]] = {}
        for event in collected:
            events_by_ticker.setdefault(event.canonical_ticker, []).append(event)
        for ticker, values in events_by_ticker.items():
            values.sort(key=lambda item: (item.market_timestamp_utc, item.source_identity))

        latest_market: datetime | None = None
        latest_receive: datetime | None = None
        negative_lag = 0
        for event in collected:
            latest_market = (
                event.market_timestamp_utc
                if latest_market is None
                else max(latest_market, event.market_timestamp_utc)
            )
            latest_receive = (
                event.receive_timestamp_utc
                if latest_receive is None
                else max(latest_receive, event.receive_timestamp_utc)
            )
            if event.receive_timestamp_utc < event.market_timestamp_utc:
                negative_lag += 1

        watermark = ExchangeWatermark(
            session_date=session_date,
            latest_market_timestamp_utc=latest_market,
            latest_receive_timestamp_utc=latest_receive,
            evaluated_at_utc=evaluated,
            lateness_grace_seconds=self.lateness_grace_seconds,
            freshness_budget_seconds=self.config.maximum_quote_age_seconds,
        )

        # --- aggregate bars per ticker, from the single grouping ---------
        one_by_ticker: dict[str, tuple[CompletedBar, ...]] = {}
        five_by_ticker: dict[str, tuple[CompletedBar, ...]] = {}
        derived_quality: list[DataQualityEvent] = list(quality_events)
        for ticker, values in events_by_ticker.items():
            one_result = aggregate_completed_one_minute_bars(
                values, as_of=evaluated, config=self.config
            )
            five_result = aggregate_completed_five_minute_bars(
                one_result.bars, as_of=evaluated, config=self.config
            )
            one_by_ticker[ticker] = one_result.bars
            five_by_ticker[ticker] = five_result.bars
            derived_quality.extend(one_result.quality_events)
            derived_quality.extend(five_result.quality_events)

        # --- freeze opening ranges --------------------------------------
        opening_ranges: dict[str, OpeningRangeSnapshot] = {}
        ranges_ready = ranges_insufficient = 0
        for ticker, bars in one_by_ticker.items():
            result = build_opening_range(
                ticker, session_date, bars, as_of=evaluated, config=self.config
            )
            opening_ranges[ticker] = OpeningRangeSnapshot(
                canonical_ticker=ticker,
                session_date=session_date,
                revision=0,
                status=result.status,
                high=result.opening_range_high,
                low=result.opening_range_low,
                frozen_at_utc=result.frozen_at_utc,
                source_identity=result.source_identity,
            )
            if result.status is OpeningRangeStatus.READY:
                ranges_ready += 1
            else:
                ranges_insufficient += 1

        # --- eligibility and capability, from the events we already have --
        eligibility: dict[str, UniverseMembershipStatus] = {}
        eligible: dict[str, bool] = {}
        volume_capability: dict[str, VolumeCapability] = {}
        archived = unmapped = 0
        for ticker, values in events_by_ticker.items():
            first = values[0]
            eligibility[ticker] = first.universe_membership_status
            eligible[ticker] = bool(first.operationally_eligible)
            volume_capability[ticker] = (
                VolumeCapability.VOLUME_AVAILABLE
                if any(item.volume_delta is not None for item in values)
                else VolumeCapability.VOLUME_UNAVAILABLE
            )
            if first.universe_membership_status is (
                UniverseMembershipStatus.ARCHIVED_INACTIVE_SYMBOL
            ):
                archived += 1
            elif not first.operationally_eligible:
                unmapped += 1

        # --- which symbols actually need re-evaluation -------------------
        previous = previous_final_bar_keys or frozenset()
        affected: list[str] = []
        current_final: set[tuple[str, datetime]] = set()
        stale_bars = late_after_cutoff = 0
        for ticker, bars in five_by_ticker.items():
            newly_final = False
            for bar in bars:
                finality = watermark.classify(bar)
                if finality is BarFinality.OPERATIONALLY_FINAL:
                    key = (ticker, bar.bar_start_utc)
                    current_final.add(key)
                    if key not in previous:
                        newly_final = True
                elif finality is BarFinality.LIVE_EVIDENCE_STALE:
                    stale_bars += 1
                elif finality is BarFinality.LATENESS_GRACE_PENDING:
                    late_after_cutoff += 1
            if newly_final:
                affected.append(ticker)

        # --- quality counts ----------------------------------------------
        all_one = [bar for bars in one_by_ticker.values() for bar in bars]
        all_five = [bar for bars in five_by_ticker.values() for bar in bars]
        observed_slots = len(
            {
                (event.canonical_ticker, event.market_timestamp_utc.replace(second=0, microsecond=0))
                for event in collected
            }
        )
        summary = SessionQualitySummary(
            source_rows_observed=int(source_rows_observed),
            normalized_events=len(collected),
            quality_event_count=len(derived_quality),
            observed_one_minute_slots=observed_slots,
            completed_one_minute_bars=len(all_one),
            completed_five_minute_bars=len(all_five),
            missing_one_minute_slots=max(0, observed_slots - len(all_one)),
            volume_valid_bars=sum(1 for bar in all_one if bar.volume is not None),
            volume_unavailable_bars=sum(1 for bar in all_one if bar.volume is None),
            stale_live_bars=stale_bars,
            reconstructable_historical_bars=len(all_five),
            opening_ranges_ready=ranges_ready,
            opening_ranges_insufficient=ranges_insufficient,
            archived_symbols_excluded=archived,
            unmapped_symbols_excluded=unmapped,
            negative_lag_events=negative_lag,
            out_of_order_events=sum(
                1 for event in collected if "OUT_OF_ORDER" in event.quality_flags
            ),
            late_events_after_cutoff=late_after_cutoff,
        )

        return ShadowSessionSnapshot(
            session_date=session_date,
            cursor_low_source_id=int(cursor_low_source_id),
            cursor_high_source_id=int(cursor_high_source_id),
            evaluated_at_utc=evaluated,
            watermark=watermark,
            events_by_ticker={k: tuple(v) for k, v in events_by_ticker.items()},
            one_minute_by_ticker=one_by_ticker,
            five_minute_by_ticker=five_by_ticker,
            opening_ranges=opening_ranges,
            eligibility=eligibility,
            operationally_eligible=eligible,
            volume_capability=volume_capability,
            daily_context=dict(daily_context or {}),
            quality_summary=summary,
            quality_events=tuple(derived_quality),
            config_identity=self.config.fingerprint,
            affected_tickers=tuple(sorted(affected)),
        )

    @staticmethod
    def final_bar_keys(snapshot: ShadowSessionSnapshot) -> frozenset[tuple[str, datetime]]:
        """Carry-forward set so the next cycle knows what was already final."""

        return frozenset(
            (ticker, bar.bar_start_utc)
            for ticker in snapshot.five_minute_by_ticker
            for bar in snapshot.final_five_minute_bars(ticker)
        )


__all__ = [
    "BarFinality",
    "ExchangeWatermark",
    "SessionQualitySummary",
    "ShadowSessionSnapshot",
    "ShadowSnapshotBuilder",
    "percentile",
]
