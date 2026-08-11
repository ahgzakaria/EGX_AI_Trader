"""Persisting the stop and target levels the engine already computed.

Research only. Nothing here starts Rubix, opens a websocket, authenticates,
reaches the network, triggers a scheduled task, or touches a production
database — every database below is built under `tmp_path`.

The 34 signals measured across 2026-08-06, 08-10 and 08-11 have a median
adverse excursion of −1.46 %, and every one of their stops sat somewhere in the
0–3 % band below trigger that `maximum_stop_distance_percent` permits. Whether
they stopped out before their favourable excursion arrived is unanswerable,
because the levels were computed and then dropped. These tests exist so that
never happens to a future session.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import sqlite3

import pytest

from scalping_orb.bars import CompletedBar
from scalping_orb.engine import (
    CompletedBarSequence,
    DailyContext,
    ORBStrategyContext,
    OpeningRangeSnapshot,
    OrbResearchEngine,
)
from scalping_orb.events import UniverseMembershipStatus, VolumeCapability
from scalping_orb.indicators import IntradayAtr, IntradayAtrStatus
from scalping_orb.opening_range import OpeningRangeStatus
from scalping_orb.qualification import (
    ATR_BREAKOUT_CONTEXT_UNAVAILABLE,
    QUALIFICATION_SCHEMA_VERSION,
    DailyResistanceStatus,
    QualificationStatus,
    SignalQualification,
    qualification_from_evaluation,
)
from scalping_orb.repository import SCHEMA_VERSION, OrbResearchRepository
from scalping_orb.session import OrbSessionPhase
from scalping_orb.shadow_service import (
    ShadowCycleMetrics,
    ShadowLiveStatus,
    ShadowStateRecord,
)
from scalping_orb.states import OrbResearchState
from scalping_orb.strategy_config import ENGINE_VERSION, OrbStrategyConfig


CAIRO = timezone(timedelta(hours=3))
DAY = date(2026, 8, 2)
OR_HIGH = 10.0
OR_LOW = 9.5


def at(hour, minute, second=0):
    return datetime.combine(DAY, time(hour, minute, second), tzinfo=CAIRO).astimezone(
        timezone.utc
    )


# --------------------------------------------------------------------------- #
# A healthy setup that reaches ENTRY_READY_RESEARCH, mirroring Phase 2B core
# --------------------------------------------------------------------------- #


def bar(hour, minute, open_, high, low, close, *, volume=1000.0, interval=5):
    start = at(hour, 0) + timedelta(minutes=minute)
    return CompletedBar(
        canonical_ticker="TEST",
        interval_minutes=interval,
        session_date=DAY,
        bar_start_utc=start,
        bar_end_utc=start + timedelta(minutes=interval),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        update_count=5,
        first_sequence=None,
        last_sequence=None,
        data_quality_flags=(),
        completed=True,
        session_phase=OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE,
        source_identity=f"{hour:02d}{minute:03d}-{interval}",
        component_bar_count=interval,
    )


def healthy_bars():
    warmup = [bar(10, minute, 9.80, 9.95, 9.75, 9.90) for minute in range(15, 50, 5)]
    return warmup + [
        bar(10, 50, 9.90, 10.12, 9.88, 10.10, volume=3000),
        bar(10, 55, 10.10, 10.11, 9.99, 10.00, volume=1200),
        bar(11, 0, 10.00, 10.04, 9.995, 9.999, volume=900),
        bar(11, 5, 10.02, 10.20, 10.01, 10.15, volume=1500),
    ]


def context(**kwargs):
    base = dict(
        canonical_ticker="TEST",
        session_date=DAY,
        opening_range=OpeningRangeSnapshot(
            canonical_ticker="TEST",
            session_date=DAY,
            revision=0,
            status=OpeningRangeStatus.READY,
            high=OR_HIGH,
            low=OR_LOW,
            frozen_at_utc=at(10, 15),
            source_identity="or-source",
        ),
        universe_membership_status=(
            UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX
        ),
        operationally_eligible=True,
        daily=DailyContext(available=True, resistance_levels=(11.5,)),
        volume_capability=VolumeCapability.VOLUME_AVAILABLE,
    )
    base.update(kwargs)
    return ORBStrategyContext(**base)


def evaluate(bars=None, **context_kwargs):
    engine = OrbResearchEngine(OrbStrategyConfig())
    sequence = CompletedBarSequence(
        one_minute=(),
        five_minute=tuple(healthy_bars() if bars is None else bars),
        as_of_utc=at(12, 0),
    )
    return engine.evaluate(context(**context_kwargs), sequence)


@pytest.fixture
def evaluation():
    result = evaluate()
    assert result.final_state is OrbResearchState.ENTRY_READY_RESEARCH
    assert result.risk is not None and result.targets is not None
    return result


# --------------------------------------------------------------------------- #
# Storage fixtures
# --------------------------------------------------------------------------- #


def cycle(index=0, cycle_id="c0"):
    return ShadowCycleMetrics(
        cycle_id=cycle_id,
        cycle_index=index,
        started_at_utc=at(11, 0),
        finished_at_utc=at(11, 1),
        cursor_low_source_id=0,
        cursor_high_source_id=1,
        source_rows_read=1,
        normalized_events=1,
        session_loads=1,
        symbols_in_snapshot=1,
        symbols_evaluated=1,
        snapshot_identity="s",
        live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,
        duration_seconds=0.1,
    )


def live_record(evaluation=None, *, state=None, version="or-v1", observed=None):
    qualification = (
        None
        if evaluation is None
        else qualification_from_evaluation(
            evaluation, daily_context=DailyContext(available=True, resistance_levels=(11.5,))
        )
    )
    return ShadowStateRecord(
        session_date=DAY,
        canonical_ticker="TEST",
        opening_range_revision=0,
        opening_range_version_identity=version,
        final_state=state
        or (evaluation.final_state.value if evaluation else "WAIT_BREAKOUT"),
        terminal=bool(evaluation),
        rejection_reasons=(),
        evidence_fingerprint="e",
        candidate_identity="c",
        live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,
        observed_at_utc=observed or at(11, 10),
        exchange_watermark_utc=at(11, 10),
        observed_receive_lag_seconds=1.0,
        qualification=qualification,
    )


def repository_with_run(tmp_path, name="orb.db"):
    repository = OrbResearchRepository(tmp_path / name)
    repository.start_shadow_run(
        "run",
        DAY,
        mode="FOLLOW",
        started_at_utc=at(10, 0),
        runner_started_before_open=True,
        source_path_identity="sid",
        config_identity="cid",
        strategy_fingerprint="sf",
        engine_version="ev",
    )
    return repository


def qualification_rows(repository):
    with repository.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM orb_signal_qualification ORDER BY detection_at_utc"
            )
        ]


# =========================================================================== #
# A. Exact-value propagation
# =========================================================================== #


def test_every_persisted_level_equals_the_engine_object(tmp_path, evaluation):
    repository = repository_with_run(tmp_path)
    repository.persist_shadow_cycle("run", cycle(), (live_record(evaluation),))

    rows = qualification_rows(repository)
    assert len(rows) == 1
    row = rows[0]
    risk, targets = evaluation.risk, evaluation.targets

    assert row["trigger_price"] == targets.trigger_price
    assert row["proposed_stop"] == risk.proposed_stop
    assert row["stop_basis"] == risk.stop_basis
    assert row["raw_pullback_low"] == risk.raw_pullback_low
    assert row["buffer_applied"] == risk.buffer_applied
    assert row["buffer_basis"] == risk.buffer_basis
    assert row["stop_distance_absolute"] == risk.stop_distance_absolute
    assert row["stop_distance_percent"] == risk.stop_distance_percent
    assert row["stop_distance_atr"] == risk.stop_distance_atr
    assert row["risk_per_share"] == targets.risk_per_share
    assert row["target_1"] == targets.target_1
    assert row["target_2"] == targets.target_2
    assert row["target_1_r_multiple"] == targets.target_1_r_multiple
    assert row["target_2_r_multiple"] == targets.target_2_r_multiple
    assert row["usable_target"] == targets.usable_target
    assert row["effective_reward_risk"] == targets.effective_reward_risk
    assert bool(row["meets_minimum_reward_risk"]) is targets.meets_minimum_reward_risk
    assert bool(row["resistance_before_target_1"]) is targets.resistance_before_target_1
    assert row["nearest_daily_resistance"] == targets.nearest_daily_resistance
    assert row["reward_before_resistance"] == targets.reward_before_resistance
    assert row["qualification_status"] == QualificationStatus.PERSISTED.value
    assert row["qualification_schema_version"] == QUALIFICATION_SCHEMA_VERSION
    assert row["research_only"] == 1


def test_nothing_is_recomputed_only_copied(evaluation):
    """The dataclass must not re-derive a single value it was handed."""

    qualification = qualification_from_evaluation(
        evaluation, daily_context=DailyContext(available=True, resistance_levels=(11.5,))
    )
    # risk_per_share is the stop distance, not a separate calculation
    assert qualification.risk_per_share == evaluation.risk.stop_distance_absolute
    # a mutated projection changes the copy, proving nothing is recalculated
    tampered = replace(evaluation, targets=replace(evaluation.targets, target_1=999.0))
    assert qualification_from_evaluation(tampered).target_1 == 999.0


def test_a_row_exists_if_and_only_if_a_signal_was_emitted(tmp_path):
    repository = repository_with_run(tmp_path)
    quiet = [bar(10, minute, 9.80, 9.95, 9.75, 9.90) for minute in range(15, 50, 5)]
    not_ready = evaluate(quiet)
    assert not_ready.final_state is not OrbResearchState.ENTRY_READY_RESEARCH
    assert qualification_from_evaluation(not_ready) is None

    repository.persist_shadow_cycle(
        "run", cycle(), (live_record(state="WAIT_BREAKOUT"),)
    )
    assert repository.table_count("orb_shadow_live_states") == 1
    assert qualification_rows(repository) == []


def test_the_trigger_is_a_confirmed_level_not_a_fill(evaluation):
    """`trigger_price` must never be presented as an executed entry."""

    qualification = qualification_from_evaluation(evaluation)
    assert qualification.trigger_price == evaluation.reclaim.trigger_price
    fields = set(vars(qualification))
    assert not {name for name in fields if "fill" in name or "executed" in name}
    assert not {name for name in fields if "slippage" in name or "quantity" in name}


class _Watermark:
    live_evidence_fresh = True
    latest_market_timestamp_utc = None
    observed_receive_lag_seconds = 0.5


class _Snapshot:
    """The minimum `evaluate_live` reads. Bars are stubbed; the engine is not."""

    def __init__(self, daily):
        self.session_date = DAY
        self.evaluated_at_utc = at(12, 0)
        self.affected_tickers = ("TEST",)
        self.opening_ranges = {"TEST": context().opening_range}
        self.one_minute_by_ticker = {"TEST": ()}
        self.events_by_ticker = {"TEST": ()}
        self.watermark = _Watermark()
        self.daily_context = {"TEST": daily} if daily is not None else {}
        self.eligibility = {}
        self.operationally_eligible = {"TEST": True}
        self.volume_capability = {"TEST": VolumeCapability.VOLUME_AVAILABLE}

    def final_five_minute_bars(self, ticker):
        return tuple(healthy_bars())


@pytest.mark.parametrize(
    "daily, expected",
    [
        (
            DailyContext(available=True, resistance_levels=(11.5,)),
            DailyResistanceStatus.RESISTANCE_ABOVE_TRIGGER,
        ),
        (
            DailyContext(available=True, resistance_levels=()),
            DailyResistanceStatus.NO_RESISTANCE_ABOVE_TRIGGER,
        ),
        (DailyContext(available=False), DailyResistanceStatus.CONTEXT_UNAVAILABLE),
        (None, DailyResistanceStatus.CONTEXT_UNAVAILABLE),
    ],
)
def test_the_live_lane_attaches_the_engines_own_values(monkeypatch, daily, expected):
    """The wiring, end to end: what `evaluate_live` emits is what the engine said."""

    from scalping_orb.shadow_service import OrbShadowService

    service = OrbShadowService(OrbStrategyConfig())
    direct = evaluate(daily=daily or DailyContext(available=False))
    assert direct.final_state is OrbResearchState.ENTRY_READY_RESEARCH
    monkeypatch.setattr(
        OrbShadowService, "_evaluate", lambda self, *a, **k: direct
    )

    records = service.evaluate_live(_Snapshot(daily))
    assert len(records) == 1
    qualification = records[0].qualification
    assert qualification is not None
    assert qualification.daily_resistance_status is expected
    assert qualification == qualification_from_evaluation(direct, daily_context=daily)
    assert qualification.trigger_price == direct.targets.trigger_price
    assert qualification.proposed_stop == direct.risk.proposed_stop


def test_the_live_lane_attaches_nothing_when_no_signal_was_emitted(monkeypatch):
    from scalping_orb.shadow_service import OrbShadowService

    service = OrbShadowService(OrbStrategyConfig())
    quiet = [bar(10, minute, 9.80, 9.95, 9.75, 9.90) for minute in range(15, 50, 5)]
    direct = evaluate(quiet)
    assert direct.final_state is not OrbResearchState.ENTRY_READY_RESEARCH
    monkeypatch.setattr(OrbShadowService, "_evaluate", lambda self, *a, **k: direct)

    records = service.evaluate_live(_Snapshot(DailyContext(available=True)))
    assert len(records) == 1
    assert records[0].qualification is None


# =========================================================================== #
# B. Transaction atomicity
# =========================================================================== #


def test_a_rejected_qualification_rolls_the_signal_back_with_it(tmp_path, evaluation):
    """Neither row may exist. A signal without its levels is the original bug."""

    repository = repository_with_run(tmp_path)
    broken = replace(
        qualification_from_evaluation(evaluation),
        proposed_stop=evaluation.targets.trigger_price + 1.0,  # stop above trigger
    )
    record = replace(live_record(evaluation), qualification=broken)

    with pytest.raises(sqlite3.IntegrityError):
        repository.persist_shadow_cycle("run", cycle(), (record,))

    assert repository.table_count("orb_shadow_live_states") == 0
    assert repository.table_count("orb_shadow_cycles") == 0
    assert qualification_rows(repository) == []


def test_a_failure_on_the_second_signal_rolls_back_the_first(tmp_path, evaluation):
    repository = repository_with_run(tmp_path)
    good = live_record(evaluation)
    broken = replace(
        live_record(evaluation, version="or-v2"),
        qualification=replace(
            qualification_from_evaluation(evaluation), atr_value=0.0, atr_status="ATR_UNAVAILABLE"
        ),
    )
    with pytest.raises(sqlite3.IntegrityError):
        repository.persist_shadow_cycle("run", cycle(), (good, broken))

    assert repository.table_count("orb_shadow_live_states") == 0
    assert qualification_rows(repository) == []


def test_a_qualification_cannot_reference_a_signal_that_does_not_exist(tmp_path):
    """The foreign key is the second lock on the same invariant."""

    repository = repository_with_run(tmp_path)
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO orb_shadow_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("c0", "run", 0, at(11, 0).isoformat(), at(11, 1).isoformat(),
             0, 1, 1, 1, 1, 1, 1, "s", "LIVE_SHADOW_HEALTHY", 0.1, None,
             at(11, 1).isoformat()),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO orb_signal_qualification (live_state_id, run_id, "
                "cycle_id, session_date, canonical_ticker, detection_at_utc, "
                "opening_range_version_identity, evidence_fingerprint, "
                "candidate_identity, strategy_fingerprint, engine_version, "
                "daily_resistance_status, atr_status, qualification_status, "
                "qualification_schema_version, research_only, generated_at_utc) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("orphan", "run", "c0", DAY.isoformat(), "TEST",
                 at(11, 0).isoformat(), "or-v1", "e", "c", "sf", "ev",
                 "DAILY_CONTEXT_UNAVAILABLE", "ATR_UNAVAILABLE",
                 "QUALIFICATION_PERSISTED", 1, 1, at(11, 1).isoformat()),
            )


# =========================================================================== #
# C & D. Uniqueness, duplicates and flicker
# =========================================================================== #


def test_recording_the_same_cycle_twice_stores_one_qualification(tmp_path, evaluation):
    repository = repository_with_run(tmp_path)
    record = live_record(evaluation)
    repository.persist_shadow_cycle("run", cycle(), (record,))
    repository.persist_shadow_cycle("run", cycle(), (record,))

    assert repository.table_count("orb_shadow_live_states") == 1
    assert len(qualification_rows(repository)) == 1


def test_flicker_follows_the_existing_lane_a_convention(tmp_path, evaluation):
    """One row per cycle observation, exactly as Lane A already appends.

    A symbol that leaves `ENTRY_READY_RESEARCH` and returns — 17 of the 34
    historical signals did, MIPH six times on 2026-08-11 — produces one Lane A
    row per cycle. Qualification follows that key rather than inventing a
    second notion of "the signal", so collapsing re-entries stays an analysis
    decision made downstream, where it already is.
    """

    repository = repository_with_run(tmp_path)
    repository.persist_shadow_cycle(
        "run", cycle(0, "c0"), (live_record(evaluation, observed=at(11, 10)),)
    )
    repository.persist_shadow_cycle(
        "run", cycle(1, "c1"), (live_record(state="BREAKOUT_REJECTED_STALE"),)
    )
    repository.persist_shadow_cycle(
        "run", cycle(2, "c2"), (live_record(evaluation, observed=at(11, 30)),)
    )

    assert repository.table_count("orb_shadow_live_states") == 3
    rows = qualification_rows(repository)
    assert len(rows) == 2, "one per emitted signal observation, none for the flicker"
    assert {row["cycle_id"] for row in rows} == {"c0", "c2"}
    assert len({row["live_state_id"] for row in rows}) == 2


def test_the_qualification_key_is_the_lane_a_identity(tmp_path, evaluation):
    """No parallel identity system: the row is keyed on `live_state_id`."""

    repository = repository_with_run(tmp_path)
    repository.persist_shadow_cycle("run", cycle(), (live_record(evaluation),))
    with repository.connect() as connection:
        joined = connection.execute(
            """SELECT q.live_state_id, l.canonical_ticker, l.final_state
               FROM orb_signal_qualification q
               JOIN orb_shadow_live_states l USING (live_state_id)"""
        ).fetchall()
    assert len(joined) == 1
    assert joined[0]["final_state"] == "ENTRY_READY_RESEARCH"


def test_a_corrected_opening_range_qualifies_separately(tmp_path, evaluation):
    repository = repository_with_run(tmp_path)
    repository.persist_shadow_cycle(
        "run", cycle(0, "c0"), (live_record(evaluation, version="or-v1"),)
    )
    repository.persist_shadow_cycle(
        "run", cycle(1, "c1"), (live_record(evaluation, version="or-v2"),)
    )
    rows = qualification_rows(repository)
    assert {row["opening_range_version_identity"] for row in rows} == {"or-v1", "or-v2"}


# =========================================================================== #
# E. ATR availability
# =========================================================================== #


def test_an_available_atr_stores_its_value_and_provenance(tmp_path, evaluation):
    repository = repository_with_run(tmp_path)
    repository.persist_shadow_cycle("run", cycle(), (live_record(evaluation),))
    row = qualification_rows(repository)[0]
    atr = evaluation.breakout.intraday_atr

    assert atr.status is IntradayAtrStatus.AVAILABLE
    assert row["atr_status"] == IntradayAtrStatus.AVAILABLE.value
    assert row["atr_value"] == atr.value
    assert row["atr_interval_minutes"] == atr.interval_minutes
    assert row["atr_lookback_bars"] == atr.lookback_bars
    assert row["atr_observed_bars"] == atr.observed_bars


@pytest.mark.parametrize(
    "status", [IntradayAtrStatus.WARMING_UP, IntradayAtrStatus.UNAVAILABLE]
)
def test_an_unavailable_atr_is_a_status_never_a_zero(tmp_path, evaluation, status):
    repository = repository_with_run(tmp_path)
    warming = IntradayAtr(
        status=status, value=None, interval_minutes=5, lookback_bars=14, observed_bars=3
    )
    tampered = replace(
        evaluation, breakout=replace(evaluation.breakout, intraday_atr=warming)
    )
    repository.persist_shadow_cycle("run", cycle(), (live_record(tampered),))

    row = qualification_rows(repository)[0]
    assert row["atr_value"] is None, "absence must not be stored as 0.0"
    assert row["atr_status"] == status.value
    assert row["atr_observed_bars"] == 3


def test_the_schema_refuses_a_number_under_an_unavailable_atr(tmp_path, evaluation):
    repository = repository_with_run(tmp_path)
    record = replace(
        live_record(evaluation),
        qualification=replace(
            qualification_from_evaluation(evaluation),
            atr_value=0.0,
            atr_status=IntradayAtrStatus.WARMING_UP.value,
        ),
    )
    with pytest.raises(sqlite3.IntegrityError):
        repository.persist_shadow_cycle("run", cycle(), (record,))


def test_a_missing_breakout_names_itself(evaluation):
    tampered = replace(evaluation, breakout=None)
    qualification = qualification_from_evaluation(tampered)
    assert qualification.atr_status == ATR_BREAKOUT_CONTEXT_UNAVAILABLE
    assert qualification.atr_value is None
    assert qualification.atr_observed_bars is None


# =========================================================================== #
# F. Daily resistance context
# =========================================================================== #


def test_resistance_above_the_trigger_is_recorded_as_such(evaluation):
    qualification = qualification_from_evaluation(
        evaluation, daily_context=DailyContext(available=True, resistance_levels=(11.5,))
    )
    assert (
        qualification.daily_resistance_status
        is DailyResistanceStatus.RESISTANCE_ABOVE_TRIGGER
    )
    assert qualification.nearest_daily_resistance == 11.5


def test_no_resistance_above_differs_from_no_context_at_all(tmp_path):
    """Both leave `nearest_daily_resistance` null. They are not the same fact."""

    with_context = evaluate(daily=DailyContext(available=True, resistance_levels=()))
    without_context = evaluate(daily=DailyContext(available=False))
    assert with_context.final_state is OrbResearchState.ENTRY_READY_RESEARCH
    assert without_context.final_state is OrbResearchState.ENTRY_READY_RESEARCH

    known = qualification_from_evaluation(
        with_context, daily_context=DailyContext(available=True, resistance_levels=())
    )
    blind = qualification_from_evaluation(
        without_context, daily_context=DailyContext(available=False)
    )
    assert known.nearest_daily_resistance is None
    assert blind.nearest_daily_resistance is None
    assert (
        known.daily_resistance_status
        is DailyResistanceStatus.NO_RESISTANCE_ABOVE_TRIGGER
    )
    assert blind.daily_resistance_status is DailyResistanceStatus.CONTEXT_UNAVAILABLE
    assert known.daily_resistance_status is not blind.daily_resistance_status


def test_an_omitted_daily_context_fails_closed_to_unavailable(evaluation):
    assert (
        qualification_from_evaluation(evaluation).daily_resistance_status
        is DailyResistanceStatus.CONTEXT_UNAVAILABLE
    )


def test_the_resistance_status_survives_the_round_trip(tmp_path):
    repository = repository_with_run(tmp_path)
    blind = evaluate(daily=DailyContext(available=False))
    record = replace(
        live_record(state="ENTRY_READY_RESEARCH"),
        qualification=qualification_from_evaluation(
            blind, daily_context=DailyContext(available=False)
        ),
    )
    repository.persist_shadow_cycle("run", cycle(), (record,))
    row = qualification_rows(repository)[0]
    assert row["daily_resistance_status"] == "DAILY_CONTEXT_UNAVAILABLE"
    assert row["nearest_daily_resistance"] is None


# =========================================================================== #
# G. Provenance
# =========================================================================== #


def test_provenance_identifies_the_rules_that_produced_the_levels(tmp_path, evaluation):
    repository = repository_with_run(tmp_path)
    repository.persist_shadow_cycle("run", cycle(), (live_record(evaluation),))
    row = qualification_rows(repository)[0]

    assert row["strategy_fingerprint"] == evaluation.strategy_fingerprint
    assert row["engine_version"] == evaluation.engine_version == ENGINE_VERSION
    assert row["opening_range_version_identity"] == "or-v1"
    assert row["evidence_fingerprint"] == "e"
    assert row["candidate_identity"] == "c"
    assert row["session_date"] == DAY.isoformat()
    assert row["canonical_ticker"] == "TEST"
    assert row["detection_at_utc"] == at(11, 10).isoformat()


def test_a_config_change_changes_the_stored_fingerprint():
    engine = OrbResearchEngine(OrbStrategyConfig(target_2_r_multiple=3.0))
    other = engine.evaluate(
        context(),
        CompletedBarSequence(
            one_minute=(), five_minute=tuple(healthy_bars()), as_of_utc=at(12, 0)
        ),
    )
    assert other.final_state is OrbResearchState.ENTRY_READY_RESEARCH
    baseline = qualification_from_evaluation(evaluate())
    changed = qualification_from_evaluation(other)
    assert changed.strategy_fingerprint != baseline.strategy_fingerprint
    assert changed.target_2_r_multiple == 3.0


# =========================================================================== #
# H. Historical compatibility
# =========================================================================== #


def test_a_pre_migration_database_opens_and_keeps_its_rows(tmp_path):
    """A version 8 session database must survive the upgrade unchanged."""

    old = OrbResearchRepository(tmp_path / "old.db", target_schema_version=8)
    old.start_shadow_run(
        "run", DAY, mode="FOLLOW", started_at_utc=at(10, 0),
        runner_started_before_open=True, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
    )
    old.persist_shadow_cycle("run", cycle(), (live_record(state="WAIT_BREAKOUT"),))
    before = [dict(row) for row in old.load_shadow_live_states("run")]
    with old.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "orb_signal_qualification" not in tables

    upgraded = OrbResearchRepository(tmp_path / "old.db")
    with upgraded.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert [dict(row) for row in upgraded.load_shadow_live_states("run")] == before
    assert upgraded.table_count("orb_signal_qualification") == 0


def test_the_migration_is_idempotent(tmp_path, evaluation):
    repository = repository_with_run(tmp_path)
    repository.persist_shadow_cycle("run", cycle(), (live_record(evaluation),))
    for _ in range(3):
        reopened = OrbResearchRepository(tmp_path / "orb.db")
        reopened.migrate()
    assert len(qualification_rows(OrbResearchRepository(tmp_path / "orb.db"))) == 1


def test_a_reader_that_predates_the_column_still_works(tmp_path, evaluation):
    """`ShadowStateRecord.qualification` defaults, so old constructions hold."""

    record = ShadowStateRecord(
        session_date=DAY,
        canonical_ticker="TEST",
        opening_range_revision=0,
        opening_range_version_identity="or-v1",
        final_state="WAIT_BREAKOUT",
        terminal=False,
        rejection_reasons=(),
        evidence_fingerprint="e",
        candidate_identity="c",
        live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,
        observed_at_utc=at(11, 0),
        exchange_watermark_utc=at(11, 0),
        observed_receive_lag_seconds=1.0,
    )
    assert record.qualification is None
    repository = repository_with_run(tmp_path)
    repository.persist_shadow_cycle("run", cycle(), (record,))
    assert repository.table_count("orb_shadow_live_states") == 1
    assert qualification_rows(repository) == []


# =========================================================================== #
# J. The historical 34 are not touched
# =========================================================================== #


def test_nothing_here_backfills_or_reconstructs_a_historical_signal():
    """Qualification is produced only from a live evaluation, never from a row."""

    import inspect

    from scalping_orb import qualification as module

    source = inspect.getsource(module)
    for forbidden in ("orb_signal_outcomes", "backfill", "reconstruct"):
        assert forbidden not in source.lower().replace("never reconstructed", "")
    parameters = inspect.signature(module.qualification_from_evaluation).parameters
    assert list(parameters) == ["evaluation", "daily_context"]
