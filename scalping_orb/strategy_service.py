"""Repository boundary for the Phase 2B Core research engine.

This layer only *moves* data: it loads completed bars and a frozen opening
range from the independent research database, calls the pure engine, and
persists what the engine returned. It makes no strategy decision of its own.

It deliberately cannot:

* start Rubix, subscribe to a symbol, or authenticate;
* reach a provider, the network, or Yahoo;
* write to any production database;
* send an alert, create a paper trade, or place an order;
* modify the dashboard.

The furthest outcome it can persist is ``ENTRY_READY_RESEARCH``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Sequence

from scalping_orb.bars import (
    CompletedBar,
    aggregate_completed_five_minute_bars,
    aggregate_completed_one_minute_bars,
)
from scalping_orb.capabilities import LiveDecisionCapability
from scalping_orb.engine import (
    CompletedBarSequence,
    DailyContext,
    ORBResearchEvaluation,
    ORBStrategyContext,
    OpeningRangeSnapshot,
    OrbResearchEngine,
)
from scalping_orb.events import (
    HistoricalReplayStatus,
    LiveFreshnessStatus,
    MarketTimeStatus,
    UniverseMembershipStatus,
    VolumeCapability,
)
from scalping_orb.opening_range import OpeningRangeStatus
from scalping_orb.repository import OrbResearchRepository
from scalping_orb.session import require_aware
from scalping_orb.states import OrbResearchState
from scalping_orb.strategy_config import (
    EvaluationMode,
    OpeningRangeVersionMode,
    OrbStrategyConfig,
)


@dataclass(frozen=True)
class SessionResearchReport:
    """Counts only. Deliberately carries no performance measure."""

    session_date: date
    evaluated_symbols: int
    persisted_candidates: int
    state_counts: dict[str, int]
    rejection_counts: dict[str, int]
    volume_assessed: int
    price_only: int
    entry_ready_research: int


class OrbResearchService:
    """Evaluate stored completed bars and persist Research Only evidence."""

    def __init__(
        self,
        repository: OrbResearchRepository,
        config: OrbStrategyConfig | None = None,
        *,
        engine: OrbResearchEngine | None = None,
    ):
        self.repository = repository
        self.config = config or OrbStrategyConfig()
        self.engine = engine or OrbResearchEngine(self.config)

    # -- context assembly ---------------------------------------------------

    def _opening_range_snapshot(
        self,
        session_id: str,
        ticker: str,
        *,
        version_mode: OpeningRangeVersionMode,
    ) -> OpeningRangeSnapshot | None:
        """Bind to an exact opening-range version.

        ``DECISION_TIME_ORIGINAL_VERSION`` always resolves to the frozen
        ``revision=0`` row, so a later research correction cannot silently
        alter an already-evaluated sequence.
        """

        if version_mode is OpeningRangeVersionMode.DECISION_TIME_ORIGINAL_VERSION:
            result = self.repository.get_opening_range_version(session_id, ticker, 0)
            revision = 0
        else:
            versions = self.repository.load_opening_range_revisions(session_id, ticker)
            if not versions:
                return None
            revision = max(item[0] for item in versions)
            result = self.repository.get_opening_range_version(
                session_id, ticker, revision
            )
        if result is None:
            return None
        return OpeningRangeSnapshot(
            canonical_ticker=result.canonical_ticker,
            session_date=result.session_date,
            revision=revision,
            status=result.status,
            high=result.opening_range_high,
            low=result.opening_range_low,
            frozen_at_utc=result.frozen_at_utc,
            source_identity=result.source_identity,
        )

    def evaluate_symbol(
        self,
        session_id: str,
        canonical_ticker: str,
        *,
        as_of: datetime,
        evaluation_mode: EvaluationMode = EvaluationMode.HISTORICAL_REPLAY,
        version_mode: OpeningRangeVersionMode = (
            OpeningRangeVersionMode.DECISION_TIME_ORIGINAL_VERSION
        ),
        daily: DailyContext | None = None,
        one_minute: Sequence[CompletedBar] | None = None,
        five_minute: Sequence[CompletedBar] | None = None,
    ) -> ORBResearchEvaluation | None:
        """Evaluate one symbol. Returns ``None`` when no range version exists."""

        evaluated = require_aware(as_of, "as_of").astimezone(timezone.utc)
        ticker = str(canonical_ticker).strip().upper()
        snapshot = self._opening_range_snapshot(
            session_id, ticker, version_mode=version_mode
        )
        if snapshot is None:
            return None

        if one_minute is None or five_minute is None:
            # Ask the database for this symbol only. Loading the whole session
            # and filtering in Python re-reads every row once per symbol, which
            # is the dominant cost of a full-session replay.
            events = list(self.repository.load_events(session_id, ticker))
            one_result = aggregate_completed_one_minute_bars(
                events, as_of=evaluated, config=self.config.data
            )
            five_result = aggregate_completed_five_minute_bars(
                one_result.bars, as_of=evaluated, config=self.config.data
            )
            one_minute = one_result.bars
            five_minute = five_result.bars
            membership = (
                events[0].universe_membership_status
                if events
                else UniverseMembershipStatus.UNKNOWN_UNMAPPED_IDENTIFIER
            )
            eligible = bool(events and events[0].operationally_eligible)
            volume_capability = (
                VolumeCapability.VOLUME_AVAILABLE
                if events and any(e.volume_delta is not None for e in events)
                else VolumeCapability.VOLUME_UNAVAILABLE
            )
            market_time = (
                events[0].market_time_status
                if events
                else MarketTimeStatus.MARKET_TIME_UNRELIABLE
            )
            replay_status = (
                events[0].historical_replay_status
                if events
                else HistoricalReplayStatus.HISTORICAL_REPLAY_ACCEPTED
            )
            freshness = (
                events[0].live_freshness_status
                if events
                else LiveFreshnessStatus.COLLECTOR_FRESHNESS_UNAVAILABLE
            )
        else:
            membership = UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX
            eligible = True
            volume_capability = (
                VolumeCapability.VOLUME_AVAILABLE
                if any(bar.volume is not None for bar in five_minute)
                else VolumeCapability.VOLUME_UNAVAILABLE
            )
            market_time = MarketTimeStatus.MARKET_TIME_VALID
            replay_status = HistoricalReplayStatus.HISTORICAL_REPLAY_ACCEPTED
            freshness = LiveFreshnessStatus.COLLECTOR_FRESHNESS_UNAVAILABLE

        context = ORBStrategyContext(
            canonical_ticker=ticker,
            session_date=snapshot.session_date,
            opening_range=snapshot,
            universe_membership_status=membership,
            operationally_eligible=eligible,
            daily=daily or DailyContext(),
            evaluation_mode=evaluation_mode,
            opening_range_version_mode=version_mode,
            market_time_status=market_time,
            historical_replay_status=replay_status,
            live_freshness_status=freshness,
            live_decision_capability=(
                LiveDecisionCapability.LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE
            ),
            volume_capability=volume_capability,
        )
        sequence = CompletedBarSequence(
            one_minute=tuple(one_minute),
            five_minute=tuple(five_minute),
            as_of_utc=evaluated,
        )
        return self.engine.evaluate(context, sequence)

    def evaluate_and_persist(
        self, session_id: str, canonical_ticker: str, **kwargs
    ) -> ORBResearchEvaluation | None:
        evaluation = self.evaluate_symbol(session_id, canonical_ticker, **kwargs)
        if evaluation is not None:
            self.repository.persist_research_evaluation(session_id, evaluation)
        return evaluation

    def replay_session(
        self,
        session_id: str,
        session_date: date,
        tickers: Iterable[str],
        *,
        as_of: datetime,
        evaluation_mode: EvaluationMode = EvaluationMode.HISTORICAL_REPLAY,
        daily_context: dict | None = None,
    ) -> SessionResearchReport:
        """Deterministically replay one session. Idempotent across runs."""

        states: dict[str, int] = {}
        rejections: dict[str, int] = {}
        evaluated = persisted = volume_assessed = price_only = ready = 0
        for ticker in sorted({str(item).strip().upper() for item in tickers}):
            evaluation = self.evaluate_and_persist(
                session_id,
                ticker,
                as_of=as_of,
                evaluation_mode=evaluation_mode,
                daily=(daily_context or {}).get(ticker),
            )
            if evaluation is None:
                continue
            evaluated += 1
            persisted += 1
            states[evaluation.final_state.value] = (
                states.get(evaluation.final_state.value, 0) + 1
            )
            for reason in evaluation.rejection_reasons:
                rejections[reason.value] = rejections.get(reason.value, 0) + 1
            if evaluation.pullback is not None:
                if evaluation.pullback.volume_assessed:
                    volume_assessed += 1
                if evaluation.pullback.price_only:
                    price_only += 1
            if evaluation.final_state is OrbResearchState.ENTRY_READY_RESEARCH:
                ready += 1
        return SessionResearchReport(
            session_date=session_date,
            evaluated_symbols=evaluated,
            persisted_candidates=persisted,
            state_counts=dict(sorted(states.items())),
            rejection_counts=dict(sorted(rejections.items())),
            volume_assessed=volume_assessed,
            price_only=price_only,
            entry_ready_research=ready,
        )


__all__ = ["OrbResearchService", "SessionResearchReport"]
