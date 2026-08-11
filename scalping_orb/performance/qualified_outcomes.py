"""What the price path did against a signal's **own** persisted levels.

Outcome measurement v1 could only say how far price travelled after a signal,
because the per-signal stop and targets had been dropped before they reached
the database. v2 measures against the levels the engine actually qualified the
signal on — `trigger_price`, `proposed_stop`, `target_1`, `target_2` — read
from `orb_signal_qualification` and never recomputed.

That upgrade only applies to signals emitted after qualification persistence
landed. The 34 historical signals have no qualification row, are not given one,
and keep `TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED` in the v1 table.

Three distinctions this module refuses to blur:

**`trigger_price` is not an entry.** It is the price level the reclaim rule
confirmed. `entry_price_proxy` is the first observable Rubix quote at or after
detection. No order was placed, so neither is a fill, and the two are stored
side by side rather than reconciled.

**A touched level is not a position exit.** T1 and T2 imply a staged exit under
some execution model, and no execution model exists here. So the module records
*milestones* — which levels the path touched, when, in what order — and leaves
position-level profit and loss undefined. `exit_price_proxy` is the first
milestone the path reached, not a closed position.

**Order that was not observed is not order that is known.** Rubix stores
periodic snapshots, not trade prints. Two levels touched at the same
`market_timestamp`, or a first touch arriving after an observation gap wider
than the budget, leave the sequence genuinely undetermined — and the outcome
says so instead of picking one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from enum import Enum
import hashlib
import json
import sqlite3
from typing import Iterable, Sequence

from scalping_orb.performance.signal_outcomes import (
    MeasurementReason,
    OutcomeMeasurementConfig,
    PriceObservation,
    SignalDiscoveryError,
    SignalRecord,
    _parse_aware,
)


LIVE_LANE_MODE = "FOLLOW"
SIGNAL_STATE = "ENTRY_READY_RESEARCH"

#: Bumped when the v2 row's field set changes.
OUTCOME_V2_SCHEMA_VERSION = 1


class OutcomeStatus(str, Enum):
    """What the observed path did against this signal's own levels."""

    TARGET_1_FIRST = "TARGET_1_FIRST"
    #: The first observation to cross a target was already at or above T2, so
    #: T1 was never independently observed on its own.
    TARGET_2_FIRST = "TARGET_2_FIRST"
    STOP_FIRST = "STOP_FIRST"
    STOP_AFTER_T1 = "STOP_AFTER_T1"
    STOP_AFTER_T2 = "STOP_AFTER_T2"
    NO_EXIT_BY_CONTINUOUS_CLOSE = "NO_EXIT_BY_CONTINUOUS_CLOSE"
    #: Stop and target first touched at the same `market_timestamp`. Rubix's
    #: `sequence` column is 100 % NULL and `id` is collector insertion order,
    #: not exchange order, so nothing in the evidence orders them.
    BOTH_STOP_AND_TARGET_TOUCHED_SAME_INSTANT = (
        "BOTH_STOP_AND_TARGET_TOUCHED_SAME_INSTANT"
    )
    #: A first touch that arrived after an observation gap wider than the
    #: budget. The level was reached, but the path through the gap is
    #: unobserved, so "which level came first" rests on missing evidence.
    EXIT_UNDETERMINED_PRICE_DATA_GAP = "EXIT_UNDETERMINED_PRICE_DATA_GAP"
    NO_FUTURE_DATA = "NO_FUTURE_DATA"


#: The statuses under which a first milestone is established by evidence, and
#: a proxy return and proxy R may therefore be computed.
DETERMINED_STATUSES = frozenset(
    {
        OutcomeStatus.TARGET_1_FIRST,
        OutcomeStatus.TARGET_2_FIRST,
        OutcomeStatus.STOP_FIRST,
        OutcomeStatus.STOP_AFTER_T1,
        OutcomeStatus.STOP_AFTER_T2,
    }
)


class OutcomeEvidenceSource(str, Enum):
    """What ordered the milestones — recorded so a reader can weigh it."""

    #: Milestones separated by strictly different `market_timestamp` values.
    QUOTE_SEQUENCE_DISTINCT_INSTANTS = "RUBIX_QUOTE_SEQUENCE_DISTINCT_INSTANTS"
    #: Only one side was ever touched; there was nothing to order.
    QUOTE_SEQUENCE_SINGLE_MILESTONE = "RUBIX_QUOTE_SEQUENCE_SINGLE_MILESTONE"
    #: Both touched at one instant. Deliberately unresolved.
    SAME_INSTANT_UNRESOLVED = "RUBIX_QUOTE_SAME_INSTANT_UNRESOLVED"
    #: The deciding touch followed a gap wider than the budget.
    OBSERVATION_GAP_UNRESOLVED = "RUBIX_QUOTE_OBSERVATION_GAP_UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OutcomeQuality(str, Enum):
    """Reuses v1's vocabulary, plus the truncated-window case."""

    MEASUREMENT_COMPLETE = "MEASUREMENT_COMPLETE"
    PARTIAL_WINDOW = "PARTIAL_WINDOW"
    PRICE_DATA_GAP = "PRICE_DATA_GAP"
    NO_FUTURE_DATA = "NO_FUTURE_DATA"


class MilestoneLevel(str, Enum):
    STOP = "PROPOSED_STOP"
    TARGET_1 = "TARGET_1"
    TARGET_2 = "TARGET_2"


@dataclass(frozen=True)
class QualificationSnapshot:
    """The engine's levels for one signal, copied out of the database as-is.

    Fingerprinted so that a later edit to the qualification row is detectable
    rather than silent: the outcome carries the exact numbers it was measured
    against, and re-measuring against changed levels produces a different
    fingerprint.
    """

    live_state_id: str
    cycle_id: str
    trigger_price: float | None
    proposed_stop: float | None
    target_1: float | None
    target_2: float | None
    usable_target: float | None
    risk_per_share: float | None
    target_1_r_multiple: float | None
    target_2_r_multiple: float | None
    effective_reward_risk: float | None
    stop_distance_absolute: float | None
    stop_distance_percent: float | None
    stop_distance_atr: float | None
    atr_status: str
    daily_resistance_status: str
    qualification_status: str
    qualification_schema_version: int
    strategy_fingerprint: str
    engine_version: str

    @property
    def measurable(self) -> bool:
        """Both a stop and at least the first target must be present."""

        return (
            self.trigger_price is not None
            and self.proposed_stop is not None
            and self.target_1 is not None
            and self.risk_per_share not in (None, 0)
        )

    @property
    def fingerprint(self) -> str:
        payload = {
            key: value
            for key, value in asdict(self).items()
            if key not in ("live_state_id", "cycle_id")
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class QualifiedOutcome:
    """One signal measured against its own persisted levels."""

    signal: SignalRecord
    qualification: QualificationSnapshot
    session_end_utc: datetime
    outcome_status: OutcomeStatus
    outcome_evidence_source: OutcomeEvidenceSource
    outcome_measurement_quality: OutcomeQuality
    measurement_reasons: tuple[MeasurementReason, ...]

    entry_price_proxy: float | None
    entry_quote_market_timestamp_utc: datetime | None
    entry_lag_seconds: float | None
    observation_count: int
    maximum_observation_gap_seconds: float | None
    tail_gap_seconds: float | None
    observation_gap_before_first_touch_seconds: float | None

    first_stop_touch_at_utc: datetime | None
    first_target_1_touch_at_utc: datetime | None
    first_target_2_touch_at_utc: datetime | None
    target_1_reached: bool
    target_2_reached: bool
    stop_reached: bool
    target_1_observed_before_target_2: bool | None

    first_milestone: MilestoneLevel | None
    exit_price_proxy: float | None
    exit_at_utc: datetime | None
    measurement_return_pct: float | None
    proxy_r_multiple: float | None

    maximum_favorable_excursion_percent: float | None
    maximum_adverse_excursion_percent: float | None
    session_end_percent: float | None

    schema_version: int = OUTCOME_V2_SCHEMA_VERSION


# -- discovery -------------------------------------------------------------


def discover_qualified_signals(
    connection: sqlite3.Connection, *, lane_a_run_id: str | None = None
) -> tuple[tuple[SignalRecord, QualificationSnapshot], ...]:
    """Signals that carry their own qualification row.

    A signal is `(session_date, canonical_ticker)` anchored at its earliest
    `ENTRY_READY_RESEARCH` observation, matching v1 and the Lane A convention.
    The qualification taken is the one belonging to **that** observation — the
    levels as of first emission, not a later re-evaluation of the same setup.

    Signals with no qualification row are not returned and are never given
    reconstructed levels. That is what keeps the historical 34 out of v2.
    """

    connection.row_factory = sqlite3.Row
    if lane_a_run_id is None:
        runs = connection.execute(
            "SELECT run_id FROM orb_shadow_runs WHERE mode=? ORDER BY started_at_utc",
            (LIVE_LANE_MODE,),
        ).fetchall()
        if not runs:
            raise SignalDiscoveryError(f"no {LIVE_LANE_MODE} run in this database")
        if len(runs) > 1:
            found = ", ".join(row["run_id"] for row in runs)
            raise SignalDiscoveryError(
                f"{len(runs)} {LIVE_LANE_MODE} runs present ({found}); "
                "pass lane_a_run_id to choose one explicitly"
            )
        lane_a_run_id = runs[0]["run_id"]

    names = {
        row[1] for row in connection.execute("PRAGMA table_info(orb_signal_qualification)")
    }
    if not names:
        return ()

    rows = connection.execute(
        """
        SELECT l.live_state_id, l.session_date, l.canonical_ticker,
               l.observed_at_utc, l.opening_range_version_identity,
               l.evidence_fingerprint, q.*
        FROM orb_shadow_live_states l
        JOIN orb_signal_qualification q USING (live_state_id)
        WHERE l.run_id = ? AND l.final_state = ?
        ORDER BY l.canonical_ticker, l.observed_at_utc
        """,
        (lane_a_run_id, SIGNAL_STATE),
    ).fetchall()

    counts = dict(
        connection.execute(
            """SELECT canonical_ticker, COUNT(*) FROM orb_shadow_live_states
               WHERE run_id=? AND final_state=? GROUP BY canonical_ticker""",
            (lane_a_run_id, SIGNAL_STATE),
        )
    )
    episodes = _episode_counts(connection, lane_a_run_id)
    last_seen = dict(
        connection.execute(
            """SELECT canonical_ticker, MAX(observed_at_utc) FROM orb_shadow_live_states
               WHERE run_id=? AND final_state=? GROUP BY canonical_ticker""",
            (lane_a_run_id, SIGNAL_STATE),
        )
    )

    discovered: list[tuple[SignalRecord, QualificationSnapshot]] = []
    seen: set[str] = set()
    for row in rows:
        ticker = str(row["canonical_ticker"])
        if ticker in seen:
            continue
        seen.add(ticker)
        signal = SignalRecord(
            session_date=date.fromisoformat(str(row["session_date"])),
            canonical_ticker=ticker,
            lane_a_run_id=str(lane_a_run_id),
            detection_timestamp_utc=_parse_aware(row["observed_at_utc"]),
            entry_ready_observation_count=int(counts.get(ticker, 1)),
            entry_ready_episode_count=int(episodes.get(ticker, 1)),
            last_entry_ready_observed_at_utc=_parse_aware(last_seen.get(ticker)),
            opening_range_version_identity=row["opening_range_version_identity"],
            detection_evidence_fingerprint=row["evidence_fingerprint"],
        )
        discovered.append((signal, _snapshot_from_row(row)))
    return tuple(
        sorted(discovered, key=lambda item: (item[0].detection_timestamp_utc, item[0].canonical_ticker))
    )


def _episode_counts(connection, lane_a_run_id) -> dict[str, int]:
    counts: dict[str, int] = {}
    previous_ticker = None
    previous_was_signal = False
    for row in connection.execute(
        """SELECT canonical_ticker, final_state FROM orb_shadow_live_states
           WHERE run_id=? ORDER BY canonical_ticker, observed_at_utc""",
        (lane_a_run_id,),
    ):
        ticker, state = str(row[0]), str(row[1])
        if ticker != previous_ticker:
            previous_ticker, previous_was_signal = ticker, False
        is_signal = state == SIGNAL_STATE
        if is_signal and not previous_was_signal:
            counts[ticker] = counts.get(ticker, 0) + 1
        previous_was_signal = is_signal
    return counts


def _snapshot_from_row(row) -> QualificationSnapshot:
    def number(name):
        value = row[name]
        return None if value is None else float(value)

    return QualificationSnapshot(
        live_state_id=str(row["live_state_id"]),
        cycle_id=str(row["cycle_id"]),
        trigger_price=number("trigger_price"),
        proposed_stop=number("proposed_stop"),
        target_1=number("target_1"),
        target_2=number("target_2"),
        usable_target=number("usable_target"),
        risk_per_share=number("risk_per_share"),
        target_1_r_multiple=number("target_1_r_multiple"),
        target_2_r_multiple=number("target_2_r_multiple"),
        effective_reward_risk=number("effective_reward_risk"),
        stop_distance_absolute=number("stop_distance_absolute"),
        stop_distance_percent=number("stop_distance_percent"),
        stop_distance_atr=number("stop_distance_atr"),
        atr_status=str(row["atr_status"]),
        daily_resistance_status=str(row["daily_resistance_status"]),
        qualification_status=str(row["qualification_status"]),
        qualification_schema_version=int(row["qualification_schema_version"]),
        strategy_fingerprint=str(row["strategy_fingerprint"]),
        engine_version=str(row["engine_version"]),
    )


# -- measurement -----------------------------------------------------------


def _unmeasured(signal, qualification, boundary, reasons) -> QualifiedOutcome:
    return QualifiedOutcome(
        signal=signal,
        qualification=qualification,
        session_end_utc=boundary,
        outcome_status=OutcomeStatus.NO_FUTURE_DATA,
        outcome_evidence_source=OutcomeEvidenceSource.NOT_APPLICABLE,
        outcome_measurement_quality=OutcomeQuality.NO_FUTURE_DATA,
        measurement_reasons=tuple(reasons),
        entry_price_proxy=None,
        entry_quote_market_timestamp_utc=None,
        entry_lag_seconds=None,
        observation_count=0,
        maximum_observation_gap_seconds=None,
        tail_gap_seconds=None,
        observation_gap_before_first_touch_seconds=None,
        first_stop_touch_at_utc=None,
        first_target_1_touch_at_utc=None,
        first_target_2_touch_at_utc=None,
        target_1_reached=False,
        target_2_reached=False,
        stop_reached=False,
        target_1_observed_before_target_2=None,
        first_milestone=None,
        exit_price_proxy=None,
        exit_at_utc=None,
        measurement_return_pct=None,
        proxy_r_multiple=None,
        maximum_favorable_excursion_percent=None,
        maximum_adverse_excursion_percent=None,
        session_end_percent=None,
    )


def measure_qualified_signal(
    signal: SignalRecord,
    qualification: QualificationSnapshot,
    observations: Iterable[PriceObservation],
    *,
    session_end: datetime,
    config: OutcomeMeasurementConfig | None = None,
) -> QualifiedOutcome:
    """Measure one signal's path against its own persisted levels.

    Long only — the engine has no short path — so the stop sits below the
    trigger and both targets above it. A single observation can therefore touch
    the stop or a target but never both; the ambiguity lives *between*
    observations, which is exactly where this refuses to guess.
    """

    cfg = config or OutcomeMeasurementConfig()
    boundary = session_end.astimezone(timezone.utc)
    detection = signal.detection_timestamp_utc.astimezone(timezone.utc)

    if detection >= boundary:
        return _unmeasured(
            signal,
            qualification,
            boundary,
            (
                MeasurementReason.DETECTION_AT_OR_AFTER_SESSION_END,
                MeasurementReason.NO_QUOTE_AFTER_DETECTION,
            ),
        )

    path = tuple(
        item
        for item in sorted(observations, key=lambda o: o.market_timestamp_utc)
        if detection <= item.market_timestamp_utc <= boundary and item.price > 0
    )
    if not path:
        return _unmeasured(
            signal, qualification, boundary, (MeasurementReason.NO_QUOTE_AFTER_DETECTION,)
        )

    entry = path[0]
    prices = [item.price for item in path]
    gaps = [
        (path[i].market_timestamp_utc - path[i - 1].market_timestamp_utc).total_seconds()
        for i in range(1, len(path))
    ]
    maximum_gap = max(gaps) if gaps else 0.0
    tail_gap = (boundary - path[-1].market_timestamp_utc).total_seconds()

    # -- quality ----------------------------------------------------------
    reasons: list[MeasurementReason] = []
    if len(path) < int(cfg.minimum_observations):
        reasons.append(MeasurementReason.INSUFFICIENT_OBSERVATIONS)
    if maximum_gap > float(cfg.maximum_observation_gap_seconds):
        reasons.append(MeasurementReason.OBSERVATION_GAP_EXCEEDS_BUDGET)
    if tail_gap > float(cfg.maximum_tail_gap_seconds):
        reasons.append(MeasurementReason.TAIL_COVERAGE_INCOMPLETE)

    if MeasurementReason.TAIL_COVERAGE_INCOMPLETE in reasons:
        quality = OutcomeQuality.PARTIAL_WINDOW
    elif reasons:
        quality = OutcomeQuality.PRICE_DATA_GAP
    else:
        quality = OutcomeQuality.MEASUREMENT_COMPLETE

    # -- excursions relative to the entry proxy ---------------------------
    favorable = max(prices)
    adverse = min(prices)
    mfe = (favorable - entry.price) / entry.price * 100.0
    mae = (adverse - entry.price) / entry.price * 100.0
    close = (prices[-1] - entry.price) / entry.price * 100.0

    if not qualification.measurable:
        return QualifiedOutcome(
            signal=signal,
            qualification=qualification,
            session_end_utc=boundary,
            outcome_status=OutcomeStatus.NO_EXIT_BY_CONTINUOUS_CLOSE,
            outcome_evidence_source=OutcomeEvidenceSource.NOT_APPLICABLE,
            outcome_measurement_quality=quality,
            measurement_reasons=tuple(reasons),
            entry_price_proxy=entry.price,
            entry_quote_market_timestamp_utc=entry.market_timestamp_utc,
            entry_lag_seconds=(entry.market_timestamp_utc - detection).total_seconds(),
            observation_count=len(path),
            maximum_observation_gap_seconds=maximum_gap,
            tail_gap_seconds=tail_gap,
            observation_gap_before_first_touch_seconds=None,
            first_stop_touch_at_utc=None,
            first_target_1_touch_at_utc=None,
            first_target_2_touch_at_utc=None,
            target_1_reached=False,
            target_2_reached=False,
            stop_reached=False,
            target_1_observed_before_target_2=None,
            first_milestone=None,
            exit_price_proxy=None,
            exit_at_utc=None,
            measurement_return_pct=None,
            proxy_r_multiple=None,
            maximum_favorable_excursion_percent=mfe,
            maximum_adverse_excursion_percent=mae,
            session_end_percent=close,
        )

    # -- first touch of each level ----------------------------------------
    stop_level = float(qualification.proposed_stop)
    target_1 = float(qualification.target_1)
    target_2 = None if qualification.target_2 is None else float(qualification.target_2)

    stop_index = _first_index(path, lambda price: price <= stop_level)
    t1_index = _first_index(path, lambda price: price >= target_1)
    t2_index = (
        None if target_2 is None else _first_index(path, lambda price: price >= target_2)
    )

    stop_at = path[stop_index].market_timestamp_utc if stop_index is not None else None
    t1_at = path[t1_index].market_timestamp_utc if t1_index is not None else None
    t2_at = path[t2_index].market_timestamp_utc if t2_index is not None else None

    # A first target touch that already sits at or above T2 never observed T1
    # on its own. Reporting "T1 then T2" there would invent an intermediate
    # observation the tape does not contain.
    target_1_before_target_2 = None
    if t1_at is not None and t2_at is not None:
        target_1_before_target_2 = t1_at < t2_at
    elif t1_at is not None:
        target_1_before_target_2 = True

    status, evidence, milestone_index, milestone_level = _resolve(
        stop_index, t1_index, t2_index, stop_at, t1_at, t2_at
    )

    gap_before_touch = None
    if milestone_index is not None and milestone_index > 0:
        gap_before_touch = (
            path[milestone_index].market_timestamp_utc
            - path[milestone_index - 1].market_timestamp_utc
        ).total_seconds()
        if gap_before_touch > float(cfg.maximum_observation_gap_seconds):
            # The level was reached, but the path through the gap is unobserved
            # — so which level was reached *first* is not established evidence.
            status = OutcomeStatus.EXIT_UNDETERMINED_PRICE_DATA_GAP
            evidence = OutcomeEvidenceSource.OBSERVATION_GAP_UNRESOLVED
            if quality is OutcomeQuality.MEASUREMENT_COMPLETE:
                quality = OutcomeQuality.PRICE_DATA_GAP
            if MeasurementReason.OBSERVATION_GAP_EXCEEDS_BUDGET not in reasons:
                reasons.append(MeasurementReason.OBSERVATION_GAP_EXCEEDS_BUDGET)

    first_milestone = None
    exit_price = exit_at = measurement_return = proxy_r = None
    if status in DETERMINED_STATUSES and milestone_index is not None:
        observation = path[milestone_index]
        first_milestone = milestone_level
        exit_price = observation.price
        exit_at = observation.market_timestamp_utc
        measurement_return = (exit_price - entry.price) / entry.price * 100.0
        risk = float(qualification.risk_per_share)
        # Measured from the observable entry proxy, not from the trigger:
        # against the trigger the answer would be a tautological -1R or +1R,
        # which describes the rule rather than the session.
        proxy_r = (exit_price - entry.price) / risk

    return QualifiedOutcome(
        signal=signal,
        qualification=qualification,
        session_end_utc=boundary,
        outcome_status=status,
        outcome_evidence_source=evidence,
        outcome_measurement_quality=quality,
        measurement_reasons=tuple(reasons),
        entry_price_proxy=entry.price,
        entry_quote_market_timestamp_utc=entry.market_timestamp_utc,
        entry_lag_seconds=(entry.market_timestamp_utc - detection).total_seconds(),
        observation_count=len(path),
        maximum_observation_gap_seconds=maximum_gap,
        tail_gap_seconds=tail_gap,
        observation_gap_before_first_touch_seconds=gap_before_touch,
        first_stop_touch_at_utc=stop_at,
        first_target_1_touch_at_utc=t1_at,
        first_target_2_touch_at_utc=t2_at,
        target_1_reached=t1_at is not None,
        target_2_reached=t2_at is not None,
        stop_reached=stop_at is not None,
        target_1_observed_before_target_2=target_1_before_target_2,
        first_milestone=first_milestone,
        exit_price_proxy=exit_price,
        exit_at_utc=exit_at,
        measurement_return_pct=measurement_return,
        proxy_r_multiple=proxy_r,
        maximum_favorable_excursion_percent=mfe,
        maximum_adverse_excursion_percent=mae,
        session_end_percent=close,
    )


def _first_index(path: Sequence[PriceObservation], predicate) -> int | None:
    for index, item in enumerate(path):
        if predicate(item.price):
            return index
    return None


def _resolve(stop_index, t1_index, t2_index, stop_at, t1_at, t2_at):
    """Order the milestones, or refuse to.

    Returns `(status, evidence, first-milestone index, first-milestone level)`.
    """

    if stop_index is None and t1_index is None:
        return (
            OutcomeStatus.NO_EXIT_BY_CONTINUOUS_CLOSE,
            OutcomeEvidenceSource.NOT_APPLICABLE,
            None,
            None,
        )

    if t1_index is None:
        return (
            OutcomeStatus.STOP_FIRST,
            OutcomeEvidenceSource.QUOTE_SEQUENCE_SINGLE_MILESTONE,
            stop_index,
            MilestoneLevel.STOP,
        )

    # A first target touch that already sits at or above T2 never observed T1
    # on its own, so the milestone is T2 and the status says so.
    simultaneous_targets = t2_at is not None and t2_at == t1_at
    target_level = (
        MilestoneLevel.TARGET_2 if simultaneous_targets else MilestoneLevel.TARGET_1
    )

    if stop_index is None:
        status = (
            OutcomeStatus.TARGET_2_FIRST
            if simultaneous_targets
            else OutcomeStatus.TARGET_1_FIRST
        )
        return (
            status,
            OutcomeEvidenceSource.QUOTE_SEQUENCE_SINGLE_MILESTONE,
            t1_index,
            target_level,
        )

    if t1_at == stop_at:
        # Two mutually exclusive prices carrying the same exchange timestamp.
        # `sequence` is NULL throughout Rubix and `id` is collector insertion
        # order, so nothing available orders them.
        return (
            OutcomeStatus.BOTH_STOP_AND_TARGET_TOUCHED_SAME_INSTANT,
            OutcomeEvidenceSource.SAME_INSTANT_UNRESOLVED,
            None,
            None,
        )

    if stop_at < t1_at:
        return (
            OutcomeStatus.STOP_FIRST,
            OutcomeEvidenceSource.QUOTE_SEQUENCE_DISTINCT_INSTANTS,
            stop_index,
            MilestoneLevel.STOP,
        )

    status = (
        OutcomeStatus.STOP_AFTER_T2
        if t2_at is not None and t2_at < stop_at
        else OutcomeStatus.STOP_AFTER_T1
    )
    return (
        status,
        OutcomeEvidenceSource.QUOTE_SEQUENCE_DISTINCT_INSTANTS,
        t1_index,
        target_level,
    )
