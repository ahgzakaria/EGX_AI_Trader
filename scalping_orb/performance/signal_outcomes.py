"""What price did after each `ENTRY_READY_RESEARCH` signal — measurement only.

The ORB shadow lanes record that a research candidate reached
`ENTRY_READY_RESEARCH`, and then stop. Nothing in the existing evidence says
what the price subsequently did, so no statement about the strategy — good,
bad, or otherwise — is currently supportable by anything but impression.

This module closes exactly that gap and nothing more. For each signal it
reads the collector's own price history forward from detection to the session
boundary and records the maximum favourable excursion, the maximum adverse
excursion, and the price at the boundary. It computes no R multiple, applies
no target and no stop, and expresses no opinion.

Three deliberate refusals:

* **No reconstructed TP/SL.** A stop and target *rule* exists in
  `strategy_config`, but the per-signal values were never persisted for these
  34 signals. Recomputing them now from today's configuration would produce
  numbers that look like evidence and are not, so every outcome carries
  `TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED` instead.
* **No substituted entry price.** If the collector has no quote at or after
  detection, the outcome is `NO_FUTURE_DATA`. The previous quote is not
  reused; the opening range high is not used as a proxy.
* **No silent interpolation across gaps.** A measurement window with a hole
  in it is marked `PRICE_DATA_GAP` and the excursions carry that label with
  them, rather than being quietly reported as if the window were continuous.

Chronology is `market_timestamp` throughout. `received_at` describes when the
collector saw a row, not when the exchange printed it, and ordering a price
path by it would be ordering by collection luck.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import sqlite3
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from scalping_orb.config import OrbDataConfig


#: The collector table holding the price path.
SOURCE_TABLE = "quotes"

#: Lane A — the live observation lane. Lane B reconstructions are excluded:
#: measuring a signal the market never saw live would answer a different
#: question than the one being asked.
LIVE_LANE_MODE = "FOLLOW"

SIGNAL_STATE = "ENTRY_READY_RESEARCH"


class SignalDiscoveryError(RuntimeError):
    """The shadow evidence does not identify a single live lane unambiguously."""


class MeasurementQuality(str, Enum):
    """How much the measured numbers can carry."""

    MEASUREMENT_COMPLETE = "MEASUREMENT_COMPLETE"
    PRICE_DATA_GAP = "PRICE_DATA_GAP"
    NO_FUTURE_DATA = "NO_FUTURE_DATA"


class MeasurementReason(str, Enum):
    """Why a signal is not `MEASUREMENT_COMPLETE`."""

    NO_QUOTE_AFTER_DETECTION = "NO_QUOTE_AFTER_DETECTION"
    DETECTION_AT_OR_AFTER_SESSION_END = "DETECTION_AT_OR_AFTER_SESSION_END"
    OBSERVATION_GAP_EXCEEDS_BUDGET = "OBSERVATION_GAP_EXCEEDS_BUDGET"
    TAIL_COVERAGE_INCOMPLETE = "TAIL_COVERAGE_INCOMPLETE"
    INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"


class TpSlStatus(str, Enum):
    """Why no per-signal stop or target appears in any outcome."""

    TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED = "TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED"


@dataclass(frozen=True)
class OutcomeMeasurementConfig:
    """The measurement contract, fingerprinted so results stay attributable.

    The gap budget is derived from the collector's observed cadence, not from
    the outcomes it classifies. Across all 217 tickers between 10:15 and 14:15
    on 2026-08-06, 08-10 and 08-11, consecutive same-ticker quote gaps sat at
    p99 34-43 s and p99.9 114-143 s; the widest gap inside any of the 34
    measurement windows was 237 s. A 300 s budget therefore sits above every
    regular cadence gap observed, so it flags genuine discontinuity rather
    than ordinary thin trading — and on that dataset it flags nothing, which
    is a fact about the data and not a threshold chosen to produce it.
    """

    maximum_observation_gap_seconds: float = 300.0
    maximum_tail_gap_seconds: float = 300.0
    minimum_observations: int = 2

    def __post_init__(self) -> None:
        for name in ("maximum_observation_gap_seconds", "maximum_tail_gap_seconds"):
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if int(self.minimum_observations) < 2:
            raise ValueError("minimum_observations must be at least 2")

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SignalRecord:
    """One research candidate, identified by symbol and session.

    A symbol can leave `ENTRY_READY_RESEARCH` and re-enter it later in the
    same session — on 2026-08-11 MIPH did so six times, flipping through
    `BREAKOUT_REJECTED_STALE` each time. Those are the same setup flickering
    on evidence staleness, not new setups, so they collapse into one record
    anchored at the earliest observation. `entry_ready_episode_count` keeps
    the flicker visible instead of discarding it.
    """

    session_date: date
    canonical_ticker: str
    lane_a_run_id: str
    detection_timestamp_utc: datetime
    entry_ready_observation_count: int
    entry_ready_episode_count: int
    last_entry_ready_observed_at_utc: datetime
    opening_range_version_identity: str | None
    detection_evidence_fingerprint: str | None


@dataclass(frozen=True)
class PriceObservation:
    market_timestamp_utc: datetime
    price: float


@dataclass(frozen=True)
class SignalOutcome:
    """Measured price behaviour after one signal. No judgement attached."""

    signal: SignalRecord
    session_end_utc: datetime
    measurement_quality: MeasurementQuality
    measurement_reasons: tuple[MeasurementReason, ...]
    tp_sl_status: TpSlStatus
    entry_quote_market_timestamp_utc: datetime | None
    entry_lag_seconds: float | None
    entry_price: float | None
    observation_count: int
    price_change_count: int
    last_observation_utc: datetime | None
    maximum_observation_gap_seconds: float | None
    tail_gap_seconds: float | None
    maximum_favorable_price: float | None
    maximum_favorable_at_utc: datetime | None
    maximum_favorable_excursion_absolute: float | None
    maximum_favorable_excursion_percent: float | None
    seconds_to_maximum_favorable: float | None
    maximum_adverse_price: float | None
    maximum_adverse_at_utc: datetime | None
    maximum_adverse_excursion_absolute: float | None
    maximum_adverse_excursion_percent: float | None
    seconds_to_maximum_adverse: float | None
    session_end_price: float | None
    session_end_absolute: float | None
    session_end_percent: float | None

    @property
    def measured(self) -> bool:
        return self.entry_price is not None


# -- session boundary ------------------------------------------------------


def session_end_utc(
    session_date: date, config: OrbDataConfig | None = None
) -> datetime:
    """The continuous-close boundary for `session_date`, in UTC.

    The boundary is `continuous_end` (14:15 Cairo). The closing auction runs
    to 14:25 and the collector keeps polling past it — 2026-08-06 alone holds
    24,400 rows in the auction window and 12,016 more after it — but an
    auction print is a different price-formation mechanism than continuous
    trading, and none of these signals could have been managed through it.
    """

    cfg = config or OrbDataConfig()
    local = datetime.combine(session_date, cfg.continuous_end, ZoneInfo(cfg.timezone))
    return local.astimezone(timezone.utc)


def _parse_aware(value) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# -- signal discovery ------------------------------------------------------


def discover_signals(
    connection: sqlite3.Connection,
    *,
    lane_a_run_id: str | None = None,
) -> tuple[SignalRecord, ...]:
    """Every live `ENTRY_READY_RESEARCH` signal in one shadow session database.

    Grouping is `(session_date, canonical_ticker)`. `candidate_identity`
    cannot serve as the key: it is a per-evaluation hash, unique on every row,
    so grouping by it would report one signal per evaluation cycle.
    """

    connection.row_factory = sqlite3.Row
    if lane_a_run_id is None:
        runs = connection.execute(
            "SELECT run_id FROM orb_shadow_runs WHERE mode=? ORDER BY started_at_utc",
            (LIVE_LANE_MODE,),
        ).fetchall()
        if not runs:
            raise SignalDiscoveryError(
                f"no {LIVE_LANE_MODE} run in this shadow database"
            )
        if len(runs) > 1:
            found = ", ".join(row["run_id"] for row in runs)
            raise SignalDiscoveryError(
                f"{len(runs)} {LIVE_LANE_MODE} runs present ({found}); "
                "pass lane_a_run_id to choose one explicitly"
            )
        lane_a_run_id = runs[0]["run_id"]

    rows = connection.execute(
        """
        SELECT session_date, canonical_ticker, final_state, observed_at_utc,
               opening_range_version_identity, evidence_fingerprint
        FROM orb_shadow_live_states
        WHERE run_id = ?
        ORDER BY canonical_ticker, observed_at_utc
        """,
        (lane_a_run_id,),
    ).fetchall()

    signals: list[SignalRecord] = []
    ticker: str | None = None
    state: list[sqlite3.Row] = []

    def flush(group: Sequence[sqlite3.Row]) -> None:
        entries = [row for row in group if row["final_state"] == SIGNAL_STATE]
        if not entries:
            return
        episodes = 0
        previous_was_signal = False
        for row in group:
            is_signal = row["final_state"] == SIGNAL_STATE
            if is_signal and not previous_was_signal:
                episodes += 1
            previous_was_signal = is_signal
        first, last = entries[0], entries[-1]
        signals.append(
            SignalRecord(
                session_date=date.fromisoformat(str(first["session_date"])),
                canonical_ticker=str(first["canonical_ticker"]),
                lane_a_run_id=str(lane_a_run_id),
                detection_timestamp_utc=_parse_aware(first["observed_at_utc"]),
                entry_ready_observation_count=len(entries),
                entry_ready_episode_count=episodes,
                last_entry_ready_observed_at_utc=_parse_aware(last["observed_at_utc"]),
                opening_range_version_identity=(
                    None
                    if first["opening_range_version_identity"] is None
                    else str(first["opening_range_version_identity"])
                ),
                detection_evidence_fingerprint=(
                    None
                    if first["evidence_fingerprint"] is None
                    else str(first["evidence_fingerprint"])
                ),
            )
        )

    for row in rows:
        if row["canonical_ticker"] != ticker:
            flush(state)
            ticker = row["canonical_ticker"]
            state = []
        state.append(row)
    flush(state)

    return tuple(
        sorted(signals, key=lambda item: (item.detection_timestamp_utc, item.canonical_ticker))
    )


# -- price path ------------------------------------------------------------


def read_observations(
    connection: sqlite3.Connection,
    ticker: str,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> tuple[PriceObservation, ...]:
    """Traded prices for `ticker` in `[start_utc, end_utc]`, chronologically.

    The SQL range is widened by a day on each side and the exact bound is then
    applied in Python on parsed timestamps. `market_timestamp` is stored as
    text, so a bound written with a different UTC offset than the stored rows
    compares lexically rather than chronologically: `'…T14:15:00+03:00'` sorts
    above `'…T14:20:00+00:00'`, silently admitting auction rows that are ten
    minutes past the boundary. Parsing removes the trap instead of relying on
    every caller to remember it.
    """

    if end_utc < start_utc:
        raise ValueError("end_utc precedes start_utc")
    low = (start_utc - timedelta(days=1)).astimezone(timezone.utc).isoformat()
    high = (end_utc + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    rows = connection.execute(
        f"""
        SELECT market_timestamp, last_price
        FROM {SOURCE_TABLE}
        WHERE ticker = ?
          AND market_timestamp >= ?
          AND market_timestamp <= ?
          AND last_price IS NOT NULL
          AND last_price > 0
        ORDER BY market_timestamp, id
        """,
        (ticker, low, high),
    ).fetchall()

    observations: list[PriceObservation] = []
    for row in rows:
        stamp = _parse_aware(row[0])
        if stamp is None or stamp < start_utc or stamp > end_utc:
            continue
        try:
            price = float(row[1])
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        observations.append(PriceObservation(market_timestamp_utc=stamp, price=price))
    observations.sort(key=lambda item: item.market_timestamp_utc)
    return tuple(observations)


# -- measurement -----------------------------------------------------------


def _unmeasured(
    signal: SignalRecord,
    boundary: datetime,
    reasons: Sequence[MeasurementReason],
) -> SignalOutcome:
    return SignalOutcome(
        signal=signal,
        session_end_utc=boundary,
        measurement_quality=MeasurementQuality.NO_FUTURE_DATA,
        measurement_reasons=tuple(reasons),
        tp_sl_status=TpSlStatus.TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED,
        entry_quote_market_timestamp_utc=None,
        entry_lag_seconds=None,
        entry_price=None,
        observation_count=0,
        price_change_count=0,
        last_observation_utc=None,
        maximum_observation_gap_seconds=None,
        tail_gap_seconds=None,
        maximum_favorable_price=None,
        maximum_favorable_at_utc=None,
        maximum_favorable_excursion_absolute=None,
        maximum_favorable_excursion_percent=None,
        seconds_to_maximum_favorable=None,
        maximum_adverse_price=None,
        maximum_adverse_at_utc=None,
        maximum_adverse_excursion_absolute=None,
        maximum_adverse_excursion_percent=None,
        seconds_to_maximum_adverse=None,
        session_end_price=None,
        session_end_absolute=None,
        session_end_percent=None,
    )


def measure_signal(
    signal: SignalRecord,
    observations: Iterable[PriceObservation],
    *,
    session_end: datetime,
    config: OutcomeMeasurementConfig | None = None,
) -> SignalOutcome:
    """Measure one signal's forward price path.

    Entry is the first observation at or after detection — the earliest price
    a research candidate could conceivably have been filled at, given that the
    signal did not exist before then. Excursions are measured from that entry
    price over `[entry, session_end]`, so by construction the favourable
    excursion is non-negative and the adverse excursion is non-positive. The
    adverse figure stays signed: an unsigned "drawdown of 2.91" reads as a
    gain often enough to be worth the sign.

    The strategy is long only — the engine has no short path, and
    `SELLING_VOLUME_EXPANSION` is a pullback *rejection* reason rather than a
    short trigger — so "favourable" means up for every signal here.
    """

    cfg = config or OutcomeMeasurementConfig()
    boundary = session_end.astimezone(timezone.utc)
    detection = signal.detection_timestamp_utc.astimezone(timezone.utc)

    if detection >= boundary:
        return _unmeasured(
            signal,
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
        return _unmeasured(signal, boundary, (MeasurementReason.NO_QUOTE_AFTER_DETECTION,))

    entry = path[0]
    prices = [item.price for item in path]
    gaps = [
        (path[index].market_timestamp_utc - path[index - 1].market_timestamp_utc).total_seconds()
        for index in range(1, len(path))
    ]
    maximum_gap = max(gaps) if gaps else 0.0
    tail_gap = (boundary - path[-1].market_timestamp_utc).total_seconds()
    changes = sum(1 for index in range(1, len(prices)) if prices[index] != prices[index - 1])

    favorable = entry
    adverse = entry
    for item in path:
        if item.price > favorable.price:
            favorable = item
        if item.price < adverse.price:
            adverse = item
    final = path[-1]

    reasons: list[MeasurementReason] = []
    if len(path) < int(cfg.minimum_observations):
        reasons.append(MeasurementReason.INSUFFICIENT_OBSERVATIONS)
    if maximum_gap > float(cfg.maximum_observation_gap_seconds):
        reasons.append(MeasurementReason.OBSERVATION_GAP_EXCEEDS_BUDGET)
    if tail_gap > float(cfg.maximum_tail_gap_seconds):
        reasons.append(MeasurementReason.TAIL_COVERAGE_INCOMPLETE)
    quality = (
        MeasurementQuality.PRICE_DATA_GAP if reasons else MeasurementQuality.MEASUREMENT_COMPLETE
    )

    return SignalOutcome(
        signal=signal,
        session_end_utc=boundary,
        measurement_quality=quality,
        measurement_reasons=tuple(reasons),
        tp_sl_status=TpSlStatus.TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED,
        entry_quote_market_timestamp_utc=entry.market_timestamp_utc,
        entry_lag_seconds=(entry.market_timestamp_utc - detection).total_seconds(),
        entry_price=entry.price,
        observation_count=len(path),
        price_change_count=changes,
        last_observation_utc=final.market_timestamp_utc,
        maximum_observation_gap_seconds=maximum_gap,
        tail_gap_seconds=tail_gap,
        maximum_favorable_price=favorable.price,
        maximum_favorable_at_utc=favorable.market_timestamp_utc,
        maximum_favorable_excursion_absolute=favorable.price - entry.price,
        maximum_favorable_excursion_percent=(favorable.price - entry.price) / entry.price * 100.0,
        seconds_to_maximum_favorable=(
            favorable.market_timestamp_utc - entry.market_timestamp_utc
        ).total_seconds(),
        maximum_adverse_price=adverse.price,
        maximum_adverse_at_utc=adverse.market_timestamp_utc,
        maximum_adverse_excursion_absolute=adverse.price - entry.price,
        maximum_adverse_excursion_percent=(adverse.price - entry.price) / entry.price * 100.0,
        seconds_to_maximum_adverse=(
            adverse.market_timestamp_utc - entry.market_timestamp_utc
        ).total_seconds(),
        session_end_price=final.price,
        session_end_absolute=final.price - entry.price,
        session_end_percent=(final.price - entry.price) / entry.price * 100.0,
    )
