"""Research-only ingestion boundary; no collector, auth, entry, or order logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
from typing import Iterable

from scalping_orb.bars import (
    CompletedBar,
    aggregate_completed_five_minute_bars,
    aggregate_completed_one_minute_bars,
)
from scalping_orb.capabilities import (
    PriceReferenceCapabilities,
    assess_price_reference_capabilities,
)
from scalping_orb.config import OrbDataConfig
from scalping_orb.events import (
    CumulativeVolumeTracker,
    DataQualityEvent,
    NormalizationMode,
    RubixEventNormalizer,
    RubixQuoteInput,
)
from scalping_orb.opening_range import (
    OpeningRangeResult,
    OpeningRangeRegistry,
    build_opening_range,
)
from scalping_orb.repository import OrbResearchRepository
from scalping_orb.session import CONTINUOUS_PHASES, require_aware


@dataclass(frozen=True)
class ShadowSymbolResult:
    canonical_ticker: str
    opening_range: OpeningRangeResult
    capabilities: PriceReferenceCapabilities


@dataclass(frozen=True)
class ShadowIngestionResult:
    session_ids: tuple[str, ...]
    accepted_events: int
    inserted_events: int
    one_minute_bars: tuple[CompletedBar, ...]
    five_minute_bars: tuple[CompletedBar, ...]
    symbols: tuple[ShadowSymbolResult, ...]
    quality_event_count: int
    retained_events_deleted: int
    retained_sessions_deleted: int


class OrbShadowIngestionService:
    """Persist normalized evidence derived from the existing single Rubix feed."""

    def __init__(
        self,
        repository: OrbResearchRepository,
        config: OrbDataConfig | None = None,
        *,
        holidays=None,
        mapping_validator=None,
        membership_resolver=None,
        normalization_mode: NormalizationMode = NormalizationMode.LIVE,
    ):
        self.repository = repository
        self.config = config or OrbDataConfig(
            research_database_path=str(repository.path)
        )
        self.normalizer = RubixEventNormalizer(
            self.config,
            holidays=holidays,
            mapping_validator=mapping_validator,
            membership_resolver=membership_resolver,
            mode=normalization_mode,
        )
        self.volume_tracker = CumulativeVolumeTracker(self.config)
        self.opening_range_registry = OpeningRangeRegistry()

    def _session_id(self, session_date: date) -> str:
        payload = f"ORB_PHASE2A|{session_date.isoformat()}|{self.config.fingerprint}"
        return hashlib.sha256(payload.encode("ascii")).hexdigest()

    def ingest(
        self, raw_events: Iterable[RubixQuoteInput], *, evaluated_at: datetime
    ) -> ShadowIngestionResult:
        evaluated = require_aware(evaluated_at, "evaluated_at").astimezone(timezone.utc)
        normalized = self.normalizer.normalize_many(raw_events, evaluated_at=evaluated)
        enriched = self.volume_tracker.apply_many(normalized.events)
        by_session: dict[date, list] = {}
        for event in enriched:
            by_session.setdefault(event.session_date, []).append(event)

        all_one: list[CompletedBar] = []
        all_five: list[CompletedBar] = []
        symbol_results: list[ShadowSymbolResult] = []
        inserted_events = 0
        quality_count = 0
        session_ids: list[str] = []
        normal_quality_by_date: dict[date | None, list[DataQualityEvent]] = {}
        for item in normalized.quality_events:
            normal_quality_by_date.setdefault(item.session_date, []).append(item)

        dated_sessions = set(by_session) | {
            value for value in normal_quality_by_date if value is not None
        }
        for session_date in sorted(dated_sessions):
            events = by_session.get(session_date, [])
            session_id = self._session_id(session_date)
            session_ids.append(session_id)
            self.repository.ensure_session(
                session_id, session_date, self.config.fingerprint
            )
            inserted_events += self.repository.insert_events(session_id, events)
            initial_quality = normal_quality_by_date.get(session_date, [])
            quality_count += self.repository.insert_quality_events(
                session_id, initial_quality
            )

            persisted = self.repository.load_events(session_id)
            one_result = aggregate_completed_one_minute_bars(
                persisted, as_of=evaluated, config=self.config
            )
            five_result = aggregate_completed_five_minute_bars(
                one_result.bars, as_of=evaluated, config=self.config
            )
            self.repository.insert_bars(session_id, one_result.bars)
            self.repository.insert_bars(session_id, five_result.bars)
            derived_quality = one_result.quality_events + five_result.quality_events
            quality_count += self.repository.insert_quality_events(
                session_id, derived_quality
            )
            all_one.extend(one_result.bars)
            all_five.extend(five_result.bars)

            # Loop-invariant: one aggregate scan per session, not one per symbol.
            history_sessions = self.repository.count_intraday_sessions()
            for ticker in sorted({event.canonical_ticker for event in persisted}):
                ticker_bars = tuple(
                    bar for bar in one_result.bars if bar.canonical_ticker == ticker
                )
                candidate = build_opening_range(
                    ticker,
                    session_date,
                    ticker_bars,
                    as_of=evaluated,
                    config=self.config,
                )
                frozen = self.repository.get_frozen_opening_range(session_id, ticker)
                if frozen is not None:
                    self.opening_range_registry.seed(frozen)
                selected, revision = self.opening_range_registry.observe(
                    candidate, detected_at=evaluated
                )
                if frozen is None:
                    self.repository.insert_opening_range(session_id, candidate)
                elif revision is not None:
                    self.repository.record_opening_range_revision(
                        session_id, candidate
                    )
                    revision_event = DataQualityEvent(
                        code="FROZEN_OPENING_RANGE_REVISION_DETECTED",
                        observed_at_utc=evaluated,
                        canonical_ticker=ticker,
                        session_date=session_date,
                        detail=(
                            f"original={frozen.source_identity};"
                            f"revised={candidate.source_identity}"
                        ),
                        source_identity=candidate.source_identity,
                    )
                    quality_count += self.repository.insert_quality_events(
                        session_id, (revision_event,)
                    )

                # Auction and post-market quotes must never be presented as the
                # current continuous-trading bid/ask, spread or volume state.
                continuous = [
                    event
                    for event in persisted
                    if event.canonical_ticker == ticker
                    and event.session_phase in CONTINUOUS_PHASES
                ]
                latest = (
                    max(continuous, key=lambda event: event.market_timestamp_utc)
                    if continuous
                    else None
                )
                capabilities = assess_price_reference_capabilities(
                    opening_range=selected,
                    latest_event=latest,
                    completed_bars=ticker_bars,
                    intraday_history_sessions=history_sessions,
                    config=self.config,
                )
                self.repository.insert_capabilities(
                    session_id, ticker, evaluated, capabilities
                )
                symbol_results.append(
                    ShadowSymbolResult(ticker, selected, capabilities)
                )

        orphan_quality = normal_quality_by_date.get(None, [])
        if orphan_quality:
            quality_count += self.repository.insert_quality_events(None, orphan_quality)
        retention = self.repository.run_retention(
            evaluated_at=evaluated,
            event_retention_days=self.config.event_retention_days,
            derived_data_retention_days=self.config.derived_data_retention_days,
            batch_size=self.config.retention_batch_size,
        )
        return ShadowIngestionResult(
            session_ids=tuple(session_ids),
            accepted_events=len(enriched),
            inserted_events=inserted_events,
            one_minute_bars=tuple(all_one),
            five_minute_bars=tuple(all_five),
            symbols=tuple(symbol_results),
            quality_event_count=quality_count,
            retained_events_deleted=retention.normalized_events_deleted,
            retained_sessions_deleted=retention.derived_sessions_deleted,
        )
