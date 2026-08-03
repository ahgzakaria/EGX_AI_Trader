"""Phase 2C Shadow integration service — two lanes, one snapshot.

Research only. The furthest state this service can persist is
``ENTRY_READY_RESEARCH``, always flagged Research Only. It cannot place an
order, size a position, send an alert or touch the Dashboard, because no such
code path exists here.

Two lanes, deliberately not merged:

**Lane A — live shadow observation.** What was *safely knowable* at the moment
of observation. ``received_at`` governs freshness; stale or unreliable evidence
cannot advance state; incomplete bars cannot confirm; auction events cannot
advance continuous-session state. Lane A is append-only — a later correction is
a new row in a later cycle, never an edit of an earlier one.

**Lane B — post-session reconstruction.** What the accumulated evidence
supports afterwards, including delayed events and corrected opening ranges.
Idempotent and recomputable.

The comparison of the two is the real deliverable: it measures what freshness
cost. Reconstructed readiness is never presented as having been actionable live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import hashlib
import json
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from scalping_orb.capabilities import LiveDecisionCapability
from scalping_orb.config import OrbDataConfig
from scalping_orb.engine import (
    CompletedBarSequence,
    DailyContext,
    ORBResearchEvaluation,
    ORBStrategyContext,
    OrbResearchEngine,
)
from scalping_orb.events import (
    HistoricalReplayStatus,
    LiveFreshnessStatus,
    MarketTimeStatus,
    UniverseMembershipStatus,
    VolumeCapability,
)
from scalping_orb.session import OrbSessionClassifier, require_aware
from scalping_orb.shadow_snapshot import (
    BarFinality,
    ShadowSessionSnapshot,
    ShadowSnapshotBuilder,
    percentile,
)
from scalping_orb.states import OrbResearchState
from scalping_orb.strategy_config import (
    ENGINE_VERSION,
    EvaluationMode,
    OpeningRangeVersionMode,
    OrbStrategyConfig,
)


class ShadowLiveStatus(str, Enum):
    """Lane A health. There is deliberately no production-live enabled member."""

    LIVE_SHADOW_HEALTHY = "LIVE_SHADOW_HEALTHY"
    LIVE_SHADOW_STALE = "LIVE_SHADOW_STALE"
    LIVE_SHADOW_SOURCE_UNAVAILABLE = "LIVE_SHADOW_SOURCE_UNAVAILABLE"
    LIVE_SHADOW_PARTIAL_SESSION = "LIVE_SHADOW_PARTIAL_SESSION"
    LIVE_SHADOW_FULL_SESSION = "LIVE_SHADOW_FULL_SESSION"
    LIVE_SHADOW_DISABLED_FRESHNESS = "LIVE_SHADOW_DISABLED_FRESHNESS"


class SessionClassification(str, Enum):
    FULL_SHADOW_SESSION = "FULL_SHADOW_SESSION"
    PARTIAL_SHADOW_SESSION = "PARTIAL_SHADOW_SESSION"
    PARTIAL_SMOKE_SESSION = "PARTIAL_SMOKE_SESSION"


class ComparisonReason(str, Enum):
    """Why Lane A and Lane B differ. Never collapsed into one bucket.

    Evaluated in the order below, most decisive first. Freshness deliberately
    outranks an opening-range revision: if live evidence was rejected as stale,
    that is the reason the state was unreachable live, and a concurrent OR
    revision is secondary detail. Reporting the revision alone would understate
    why the reconstructed state was never actionable.

    ``STATE_DIFFERENCE`` exists so a genuine divergence with healthy live
    evidence and no revision is not mislabelled as a known cause.
    """

    #: Neither lane had usable evidence.
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    #: Reconstruction produced a result; live observation never evaluated it.
    HISTORICAL_ONLY = "HISTORICAL_ONLY"
    #: Live observed it; reconstruction did not produce a comparable result.
    LIVE_ONLY = "LIVE_ONLY"
    #: Both lanes agree.
    IDENTICAL = "IDENTICAL"
    #: Live evidence was not fresh/healthy, so live could not reach the state.
    FRESHNESS_REJECTED_LIVE = "FRESHNESS_REJECTED_LIVE"
    #: The lanes bound to different opening-range versions.
    OPENING_RANGE_REVISED = "OPENING_RANGE_REVISED"
    #: A real divergence with healthy live evidence and no revision. Cause
    #: unattributed on purpose.
    STATE_DIFFERENCE = "STATE_DIFFERENCE"


#: Lane A outcomes that mean "live was refused on freshness / live-decision
#: capability", as opposed to a genuine strategy difference.
_FRESHNESS_REJECTED_STATES = frozenset(
    {
        OrbResearchState.BREAKOUT_REJECTED_STALE.value,
        OrbResearchState.DATA_UNAVAILABLE.value,
    }
)
_FRESHNESS_REJECTED_REASONS = frozenset(
    {"STALE_LIVE_DATA", "LIVE_DECISION_DISABLED_FRESHNESS", "UNRELIABLE_MARKET_TIMESTAMP"}
)


def classify_difference(live, reconstruction, revised: bool):
    """Select a comparison category and record the evidence that selected it.

    One deterministic precedence, applied in this order. Each branch returns the
    facts it used, so a stored category can be re-derived instead of trusted.
    """

    unavailable = OrbResearchState.DATA_UNAVAILABLE.value
    live_state = live.final_state if live else None
    recon_state = reconstruction.final_state if reconstruction else None

    if live is None and reconstruction is None:  # pragma: no cover - defensive
        return ComparisonReason.DATA_UNAVAILABLE, ("no_row_in_either_lane",)
    if live_state == unavailable and recon_state == unavailable:
        return ComparisonReason.DATA_UNAVAILABLE, (
            "live_state=DATA_UNAVAILABLE",
            "reconstruction_state=DATA_UNAVAILABLE",
        )
    if live is None:
        return ComparisonReason.HISTORICAL_ONLY, (
            "no_live_row",
            f"reconstruction_state={recon_state}",
        )
    if reconstruction is None:
        return ComparisonReason.LIVE_ONLY, (
            "no_reconstruction_row",
            f"live_state={live_state}",
        )
    if live_state == recon_state:
        return ComparisonReason.IDENTICAL, (f"both_states={live_state}",)
    if _live_rejected_on_freshness(live):
        # Before the revision: if live was refused on freshness or live-decision
        # capability, that is why the state was unreachable live at all, and a
        # concurrent opening-range revision is secondary detail.
        evidence = [f"live_state={live_state}", f"live_status={live.live_status.value}"]
        matched = sorted(_FRESHNESS_REJECTED_REASONS & set(live.rejection_reasons))
        if matched:
            evidence.append("live_rejection_reasons=" + ";".join(matched))
        if revised:
            evidence.append("opening_range_revised=true_but_not_primary")
        return ComparisonReason.FRESHNESS_REJECTED_LIVE, tuple(evidence)
    if revised:
        return ComparisonReason.OPENING_RANGE_REVISED, (
            f"live_or_version={live.opening_range_version_identity[:16]}",
            f"reconstruction_or_version={reconstruction.opening_range_version_identity[:16]}",
            f"live_state={live_state}",
            f"reconstruction_state={recon_state}",
        )
    return ComparisonReason.STATE_DIFFERENCE, (
        f"live_state={live_state}",
        f"reconstruction_state={recon_state}",
        "opening_range_identical",
        "live_evidence_healthy",
        "cause_not_established",
    )


def select_live_run(
    candidates: Sequence[dict],
    *,
    explicit_run_id: str | None = None,
    session_date: date,
    source_path_identity: str,
    config_identity: str,
) -> dict:
    """Pick exactly one prior live run, or refuse.

    Never guesses. An ambiguous match is an error, not a heuristic: silently
    comparing against the wrong session would produce a plausible-looking
    report about the wrong day.
    """

    def _describe(row: dict) -> str:
        return (
            f"{row['run_id'][:16]} mode={row['mode']} "
            f"started={row['started_at_utc']} laneA={row['lane_a_rows']}"
        )

    eligible = [
        row
        for row in candidates
        if row["session_date"] == session_date.isoformat()
        and row["source_path_identity"] == source_path_identity
        and row["config_identity"] == config_identity
        and row["mode"] != "RECONSTRUCT"
        and int(row["research_only"]) == 1
        and int(row["lane_a_rows"]) > 0
    ]

    if explicit_run_id:
        chosen = [row for row in eligible if row["run_id"] == explicit_run_id]
        if not chosen:
            raise ShadowRunSelectionError(
                f"run {explicit_run_id!r} is not an eligible live run for "
                f"session {session_date.isoformat()} on this source and config. "
                f"Eligible: {[_describe(r) for r in eligible] or 'none'}"
            )
        return chosen[0]

    if not eligible:
        raise ShadowRunSelectionError(
            f"no prior live Shadow run with Lane A rows found for session "
            f"{session_date.isoformat()} on this source and config. "
            "Run --list-runs to inspect, or pass --compare-live-run-id. "
            "Refusing to emit an all-HISTORICAL_ONLY comparison."
        )
    if len(eligible) > 1:
        raise ShadowRunSelectionError(
            f"{len(eligible)} eligible live runs for session "
            f"{session_date.isoformat()}; pass --compare-live-run-id to choose. "
            f"Candidates: {[_describe(r) for r in eligible]}"
        )
    return eligible[0]


def _live_rejected_on_freshness(record) -> bool:
    """Two distinct signals, both meaning 'not knowable live'.

    * the *feed* was stale — visible in ``live_status``;
    * the *live decision capability* was disabled — visible only in the Lane A
      record's own state and rejection reasons, because the feed itself can be
      perfectly fresh while Phase 2A still refuses to let live decide.

    The second is the LCSW case from the partial smoke: a healthy 1-second feed
    whose breakout was still rejected as stale, because
    ``LiveDecisionCapability`` has no enabled member. Keying only on
    ``live_status`` would have mis-attributed it to an opening-range revision.
    """

    if record.live_status is not ShadowLiveStatus.LIVE_SHADOW_HEALTHY:
        return True
    if record.final_state in _FRESHNESS_REJECTED_STATES:
        return True
    return bool(_FRESHNESS_REJECTED_REASONS & set(record.rejection_reasons))


@dataclass(frozen=True)
class ShadowCycleMetrics:
    cycle_id: str
    cycle_index: int
    started_at_utc: datetime
    finished_at_utc: datetime
    cursor_low_source_id: int
    cursor_high_source_id: int
    source_rows_read: int
    normalized_events: int
    #: Must stay at 1. More than one load per cycle is the regression that
    #: REPLAY_SESSION_BATCHING_REQUIRED exists to prevent.
    session_loads: int
    symbols_in_snapshot: int
    symbols_evaluated: int
    snapshot_identity: str
    live_status: ShadowLiveStatus
    duration_seconds: float
    source_failure: str | None = None


@dataclass(frozen=True)
class ShadowStateRecord:
    """One Lane A observation. Immutable once written."""

    session_date: date
    canonical_ticker: str
    opening_range_revision: int
    opening_range_version_identity: str
    final_state: str
    terminal: bool
    rejection_reasons: tuple[str, ...]
    evidence_fingerprint: str
    candidate_identity: str
    live_status: ShadowLiveStatus
    observed_at_utc: datetime
    exchange_watermark_utc: datetime | None
    observed_receive_lag_seconds: float | None


@dataclass(frozen=True)
class ShadowReconstructionRecord:
    """One Lane B result."""

    session_date: date
    canonical_ticker: str
    opening_range_revision: int
    opening_range_version_identity: str
    final_state: str
    terminal: bool
    rejection_reasons: tuple[str, ...]
    evidence_fingerprint: str
    candidate_identity: str
    evaluation_mode: str = EvaluationMode.HISTORICAL_REPLAY.value


@dataclass(frozen=True)
class ShadowComparisonRow:
    session_date: date
    canonical_ticker: str
    live_state: str | None
    reconstruction_state: str | None
    live_opening_range_version_identity: str | None
    reconstruction_opening_range_version_identity: str | None
    opening_range_revised: bool
    states_match: bool
    difference_reason: str
    evaluable_live: bool
    evaluable_historically: bool
    #: The Lane A health that was actually observed, carried so the stored
    #: category can be re-derived rather than trusted.
    live_status: "ShadowLiveStatus | None" = None
    live_rejection_reasons: tuple[str, ...] = ()
    #: Exactly which facts selected `difference_reason`. Without this the
    #: category is an assertion; with it, it is reproducible.
    difference_evidence: tuple[str, ...] = ()


class ShadowRunSelectionError(RuntimeError):
    """No prior live run matched, or several did and none was named."""


@dataclass(frozen=True)
class ShadowSessionQuality:
    session_date: date
    classification: SessionClassification
    classification_reasons: tuple[str, ...]
    source_rows_observed: int = 0
    normalized_events: int = 0
    exact_redeliveries_removed: int = 0
    same_timestamp_distinct_retained: int = 0
    source_polling_failures: int = 0
    maximum_polling_gap_seconds: float | None = None
    median_receive_lag_seconds: float | None = None
    p90_receive_lag_seconds: float | None = None
    p95_receive_lag_seconds: float | None = None
    percent_above_freshness_budget: float | None = None
    negative_lag_events: int = 0
    out_of_order_events: int = 0
    late_events_after_cutoff: int = 0
    active_mapped_symbols: int = 0
    completed_one_minute_bars: int = 0
    completed_five_minute_bars: int = 0
    opening_ranges_ready: int = 0
    live_evaluations: int = 0
    reconstruction_evaluations: int = 0
    heartbeat_count: int = 0
    process_runtime_seconds: float = 0.0
    detail: dict = field(default_factory=dict)


def classify_session(
    *,
    session_date: date,
    runner_started_utc: datetime,
    runner_finished_utc: datetime,
    config: OrbDataConfig,
    maximum_polling_gap_seconds: float | None,
    allowed_polling_gap_seconds: float,
    heartbeat_count: int,
    minimum_heartbeats: int,
    cursor_advanced: bool,
    opening_ranges_ready: int,
    observed_exchange_minutes: int,
    minimum_exchange_minutes: int,
    smoke: bool = False,
) -> tuple[SessionClassification, tuple[str, ...]]:
    """Strict FULL classification. Existence of data is never sufficient.

    A run started after 10:00 Cairo is always partial — it cannot have observed
    the opening range forming, so its Lane A history has a hole no later
    evidence can fill.
    """

    classifier = OrbSessionClassifier(config)
    window = classifier.window(session_date)
    reasons: list[str] = []

    if runner_started_utc > window.continuous_start_utc:
        reasons.append("RUNNER_STARTED_AFTER_CONTINUOUS_OPEN")
    if runner_finished_utc < window.continuous_end_utc:
        reasons.append("RUNNER_STOPPED_BEFORE_CONTINUOUS_END")
    if (
        maximum_polling_gap_seconds is not None
        and maximum_polling_gap_seconds > allowed_polling_gap_seconds
    ):
        reasons.append("POLLING_OUTAGE_EXCEEDED_LIMIT")
    if heartbeat_count < minimum_heartbeats:
        reasons.append("INSUFFICIENT_HEARTBEAT_COVERAGE")
    if not cursor_advanced:
        reasons.append("SOURCE_CURSOR_DID_NOT_PROGRESS")
    if opening_ranges_ready < 1:
        reasons.append("NO_OPENING_RANGE_OBSERVED")
    if observed_exchange_minutes < minimum_exchange_minutes:
        reasons.append("INSUFFICIENT_EXCHANGE_MINUTE_COVERAGE")

    if smoke:
        return SessionClassification.PARTIAL_SMOKE_SESSION, tuple(
            reasons or ("EXPLICIT_SMOKE_RUN",)
        )
    if reasons:
        return SessionClassification.PARTIAL_SHADOW_SESSION, tuple(reasons)
    return SessionClassification.FULL_SHADOW_SESSION, ()


class OrbShadowService:
    """Evaluate a shared snapshot through the unchanged Phase 2B engine.

    This layer supplies bars, opening-range version, eligibility, capability,
    evaluation mode and configuration identity. It supplies **no thresholds**
    and reimplements no rule.
    """

    def __init__(
        self,
        config: OrbStrategyConfig | None = None,
        *,
        engine: OrbResearchEngine | None = None,
        lateness_grace_seconds: float = 90.0,
        active_universe_only: bool = False,
    ):
        self.config = config or OrbStrategyConfig()
        self.engine = engine or OrbResearchEngine(self.config)
        self.active_universe_only = bool(active_universe_only)
        self.builder = ShadowSnapshotBuilder(
            self.config.data,
            lateness_grace_seconds=lateness_grace_seconds,
            active_universe_only=self.active_universe_only,
        )
        self.classifier = OrbSessionClassifier(self.config.data)

    # -- Lane A -----------------------------------------------------------

    def live_status_for(self, snapshot: ShadowSessionSnapshot) -> ShadowLiveStatus:
        if not snapshot.events_by_ticker:
            return ShadowLiveStatus.LIVE_SHADOW_SOURCE_UNAVAILABLE
        if not snapshot.watermark.live_evidence_fresh:
            return ShadowLiveStatus.LIVE_SHADOW_STALE
        return ShadowLiveStatus.LIVE_SHADOW_HEALTHY

    def evaluate_live(
        self, snapshot: ShadowSessionSnapshot, *, tickers: Sequence[str] | None = None
    ) -> tuple[ShadowStateRecord, ...]:
        """Lane A. Only operationally final bars; freshness fails closed.

        The engine is handed *only* the bars Lane A is entitled to act on, so
        an unfinalized or stale bar cannot advance state even accidentally.
        """

        status = self.live_status_for(snapshot)
        selected = list(tickers if tickers is not None else snapshot.affected_tickers)
        records: list[ShadowStateRecord] = []
        for ticker in sorted(set(selected)):
            opening_range = snapshot.opening_ranges.get(ticker)
            if opening_range is None:
                continue
            final_bars = snapshot.final_five_minute_bars(ticker)
            evaluation = self._evaluate(
                snapshot,
                ticker,
                opening_range=opening_range,
                five_minute=final_bars,
                one_minute=snapshot.one_minute_by_ticker.get(ticker, ()),
                evaluation_mode=EvaluationMode.SHADOW_LIVE,
                version_mode=OpeningRangeVersionMode.DECISION_TIME_ORIGINAL_VERSION,
                live_fresh=snapshot.watermark.live_evidence_fresh,
            )
            records.append(
                ShadowStateRecord(
                    session_date=snapshot.session_date,
                    canonical_ticker=ticker,
                    opening_range_revision=evaluation.opening_range_revision,
                    opening_range_version_identity=(
                        evaluation.opening_range_version_identity
                    ),
                    final_state=evaluation.final_state.value,
                    terminal=evaluation.terminal,
                    rejection_reasons=tuple(
                        reason.value for reason in evaluation.rejection_reasons
                    ),
                    evidence_fingerprint=evaluation.evidence_fingerprint,
                    candidate_identity=evaluation.candidate_identity,
                    live_status=status,
                    observed_at_utc=snapshot.evaluated_at_utc,
                    exchange_watermark_utc=(
                        snapshot.watermark.latest_market_timestamp_utc
                    ),
                    observed_receive_lag_seconds=(
                        snapshot.watermark.observed_receive_lag_seconds
                    ),
                )
            )
        return tuple(records)

    # -- Lane B -----------------------------------------------------------

    def evaluate_reconstruction(
        self,
        snapshot: ShadowSessionSnapshot,
        *,
        version_mode: OpeningRangeVersionMode = (
            OpeningRangeVersionMode.DECISION_TIME_ORIGINAL_VERSION
        ),
        opening_range_overrides: Mapping[str, object] | None = None,
    ) -> tuple[ShadowReconstructionRecord, ...]:
        """Lane B. All accepted evidence, deterministic, idempotent."""

        overrides = dict(opening_range_overrides or {})
        records: list[ShadowReconstructionRecord] = []
        # Same universe filter as Lane A, so the two lanes are compared over
        # the same candidate set rather than one being systematically wider.
        for ticker in snapshot.evaluation_tickers:
            opening_range = overrides.get(ticker) or snapshot.opening_ranges.get(ticker)
            if opening_range is None:
                continue
            evaluation = self._evaluate(
                snapshot,
                ticker,
                opening_range=opening_range,
                five_minute=snapshot.five_minute_by_ticker.get(ticker, ()),
                one_minute=snapshot.one_minute_by_ticker.get(ticker, ()),
                evaluation_mode=EvaluationMode.HISTORICAL_REPLAY,
                version_mode=version_mode,
                live_fresh=False,
            )
            records.append(
                ShadowReconstructionRecord(
                    session_date=snapshot.session_date,
                    canonical_ticker=ticker,
                    opening_range_revision=evaluation.opening_range_revision,
                    opening_range_version_identity=(
                        evaluation.opening_range_version_identity
                    ),
                    final_state=evaluation.final_state.value,
                    terminal=evaluation.terminal,
                    rejection_reasons=tuple(
                        reason.value for reason in evaluation.rejection_reasons
                    ),
                    evidence_fingerprint=evaluation.evidence_fingerprint,
                    candidate_identity=evaluation.candidate_identity,
                )
            )
        return tuple(records)

    # -- shared evaluation -------------------------------------------------

    def _evaluate(
        self,
        snapshot: ShadowSessionSnapshot,
        ticker: str,
        *,
        opening_range,
        five_minute,
        one_minute,
        evaluation_mode: EvaluationMode,
        version_mode: OpeningRangeVersionMode,
        live_fresh: bool,
    ) -> ORBResearchEvaluation:
        context = ORBStrategyContext(
            canonical_ticker=ticker,
            session_date=snapshot.session_date,
            opening_range=opening_range,
            universe_membership_status=snapshot.eligibility.get(
                ticker, UniverseMembershipStatus.UNKNOWN_UNMAPPED_IDENTIFIER
            ),
            operationally_eligible=snapshot.operationally_eligible.get(ticker, False),
            daily=snapshot.daily_context.get(ticker, DailyContext()),
            evaluation_mode=evaluation_mode,
            opening_range_version_mode=version_mode,
            market_time_status=MarketTimeStatus.MARKET_TIME_VALID,
            historical_replay_status=HistoricalReplayStatus.HISTORICAL_REPLAY_ACCEPTED,
            live_freshness_status=(
                LiveFreshnessStatus.LIVE_FRESHNESS_PASSED
                if live_fresh
                else LiveFreshnessStatus.LIVE_FRESHNESS_FAILED
            ),
            # Phase 2A gave this enum no enabled member, so live cannot reach
            # readiness by construction. Phase 2C does not add one.
            live_decision_capability=(
                LiveDecisionCapability.LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE
            ),
            volume_capability=snapshot.volume_capability.get(
                ticker, VolumeCapability.VOLUME_UNAVAILABLE
            ),
        )
        sequence = CompletedBarSequence(
            one_minute=tuple(one_minute),
            five_minute=tuple(five_minute),
            as_of_utc=snapshot.evaluated_at_utc,
        )
        return self.engine.evaluate(context, sequence)

    # -- comparison --------------------------------------------------------

    def compare(
        self,
        session_date: date,
        live: Sequence[ShadowStateRecord],
        reconstruction: Sequence[ShadowReconstructionRecord],
    ) -> tuple[ShadowComparisonRow, ...]:
        """What did freshness cost, and what did late corrections change?

        For Lane A the *last* observation of a symbol is its live outcome —
        earlier rows stay in the table as history, but the final observed state
        is what could have been known by the end.
        """

        latest_live: dict[str, ShadowStateRecord] = {}
        for record in sorted(live, key=lambda item: item.observed_at_utc):
            latest_live[record.canonical_ticker] = record
        by_reconstruction = {
            record.canonical_ticker: record for record in reconstruction
        }
        rows: list[ShadowComparisonRow] = []
        for ticker in sorted(set(latest_live) | set(by_reconstruction)):
            a = latest_live.get(ticker)
            b = by_reconstruction.get(ticker)
            live_state = a.final_state if a else None
            recon_state = b.final_state if b else None
            revised = bool(
                a
                and b
                and a.opening_range_version_identity
                != b.opening_range_version_identity
            )
            reason, evidence = classify_difference(a, b, revised)
            rows.append(
                ShadowComparisonRow(
                    session_date=session_date,
                    canonical_ticker=ticker,
                    live_state=live_state,
                    reconstruction_state=recon_state,
                    live_opening_range_version_identity=(
                        a.opening_range_version_identity if a else None
                    ),
                    reconstruction_opening_range_version_identity=(
                        b.opening_range_version_identity if b else None
                    ),
                    opening_range_revised=revised,
                    states_match=bool(a and b and live_state == recon_state),
                    difference_reason=reason.value,
                    evaluable_live=a is not None,
                    evaluable_historically=b is not None,
                    live_status=a.live_status if a else None,
                    live_rejection_reasons=tuple(a.rejection_reasons) if a else (),
                    difference_evidence=evidence,
                )
            )
        return tuple(rows)


def live_records_from_rows(rows: Sequence[dict]) -> tuple[ShadowStateRecord, ...]:
    """Rehydrate stored Lane A rows into records the comparison can use.

    Read-only: the prior run's rows are never modified, only read. Reading them
    back through the same type the comparison already consumes means the
    cross-run path shares one code path with the single-run path.
    """

    records: list[ShadowStateRecord] = []
    for row in rows:
        records.append(
            ShadowStateRecord(
                session_date=date.fromisoformat(row["session_date"]),
                canonical_ticker=row["canonical_ticker"],
                opening_range_revision=int(row["opening_range_revision"]),
                opening_range_version_identity=row["opening_range_version_identity"],
                final_state=row["final_state"],
                terminal=bool(row["terminal"]),
                rejection_reasons=tuple(json.loads(row["rejection_reasons_json"])),
                evidence_fingerprint=row["evidence_fingerprint"],
                candidate_identity=row["candidate_identity"],
                live_status=ShadowLiveStatus(row["live_status"]),
                observed_at_utc=datetime.fromisoformat(row["observed_at_utc"]),
                exchange_watermark_utc=(
                    datetime.fromisoformat(row["exchange_watermark_utc"])
                    if row["exchange_watermark_utc"]
                    else None
                ),
                observed_receive_lag_seconds=row["observed_receive_lag_seconds"],
            )
        )
    return tuple(records)


def receive_lag_statistics(
    events: Iterable, *, freshness_budget_seconds: float
) -> dict:
    """Median/p90/p95 receive lag and share over budget.

    Negative lag is counted separately rather than clamped: it is clock skew,
    and hiding it would make the feed look better than it is.
    """

    lags = [
        (event.receive_timestamp_utc - event.market_timestamp_utc).total_seconds()
        for event in events
    ]
    if not lags:
        return {
            "median": None,
            "p90": None,
            "p95": None,
            "percent_above_budget": None,
            "negative": 0,
            "count": 0,
        }
    over = sum(1 for value in lags if value > freshness_budget_seconds)
    return {
        "median": percentile(lags, 0.5),
        "p90": percentile(lags, 0.9),
        "p95": percentile(lags, 0.95),
        "percent_above_budget": 100.0 * over / len(lags),
        "negative": sum(1 for value in lags if value < 0),
        "count": len(lags),
    }


__all__ = [
    "ComparisonReason",
    "ShadowRunSelectionError",
    "classify_difference",
    "live_records_from_rows",
    "select_live_run",
    "OrbShadowService",
    "SessionClassification",
    "ShadowComparisonRow",
    "ShadowCycleMetrics",
    "ShadowLiveStatus",
    "ShadowReconstructionRecord",
    "ShadowSessionQuality",
    "ShadowStateRecord",
    "classify_session",
    "receive_lag_statistics",
]
