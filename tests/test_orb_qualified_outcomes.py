"""Outcome measurement v2 — signals measured against their own levels.

Research only. Nothing here starts Rubix, opens a websocket, authenticates,
reaches the network, triggers a scheduled task, or touches a production
database.

The point of v2 is that it can be wrong in ways v1 could not, because it makes
ordering claims: "the target came before the stop" is a statement about a
sequence, and Rubix stores periodic snapshots rather than trade prints. Most of
what follows pins the cases where the evidence does *not* support such a claim.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import sqlite3

import pytest

from scalping_orb.config import OrbDataConfig
from scalping_orb.performance.outcome_store import (
    SCHEMA_VERSION,
    OutcomeStore,
    QualifiedOutcomeStore,
)
from scalping_orb.performance.qualified_outcomes import (
    OUTCOME_V2_SCHEMA_VERSION,
    MilestoneLevel,
    OutcomeEvidenceSource,
    OutcomeQuality,
    OutcomeStatus,
    QualificationSnapshot,
    discover_qualified_signals,
    measure_qualified_signal,
)
from scalping_orb.performance.signal_outcomes import (
    MeasurementReason,
    OutcomeMeasurementConfig,
    PriceObservation,
    SignalDiscoveryError,
    SignalRecord,
    measure_signal,
    session_end_utc,
)


CAIRO = timezone(timedelta(hours=3))
DAY = date(2026, 8, 20)
CONFIG = OrbDataConfig()
MEASUREMENT = OutcomeMeasurementConfig()
CLOSE = session_end_utc(DAY, CONFIG)

TRIGGER = 10.00
STOP = 9.80
TARGET_1 = 10.20
TARGET_2 = 10.40
RISK = 0.20


def at(hour, minute, second=0):
    return datetime.combine(DAY, time(hour, minute, second), tzinfo=CAIRO).astimezone(
        timezone.utc
    )


def signal(ticker="TEST", detected=None, episodes=1):
    detected = detected or at(10, 40)
    return SignalRecord(
        session_date=DAY,
        canonical_ticker=ticker,
        lane_a_run_id="lane-a",
        detection_timestamp_utc=detected,
        entry_ready_observation_count=5,
        entry_ready_episode_count=episodes,
        last_entry_ready_observed_at_utc=detected + timedelta(hours=1),
        opening_range_version_identity="or-v1",
        detection_evidence_fingerprint="fp",
    )


def qualification(**kwargs):
    base = dict(
        live_state_id="lsid",
        cycle_id="c0",
        trigger_price=TRIGGER,
        proposed_stop=STOP,
        target_1=TARGET_1,
        target_2=TARGET_2,
        usable_target=TARGET_2,
        risk_per_share=RISK,
        target_1_r_multiple=1.0,
        target_2_r_multiple=2.0,
        effective_reward_risk=2.0,
        stop_distance_absolute=RISK,
        stop_distance_percent=0.02,
        stop_distance_atr=1.0,
        atr_status="INTRADAY_ATR_AVAILABLE",
        daily_resistance_status="DAILY_RESISTANCE_ABOVE_TRIGGER",
        qualification_status="QUALIFICATION_PERSISTED",
        qualification_schema_version=1,
        strategy_fingerprint="sf",
        engine_version="ev",
    )
    base.update(kwargs)
    return QualificationSnapshot(**base)


def path(points):
    return tuple(
        PriceObservation(market_timestamp_utc=stamp, price=price)
        for stamp, price in points
    )


def measure(points, *, qual=None, sig=None, config=None):
    return measure_qualified_signal(
        sig or signal(),
        qual or qualification(),
        path(points),
        session_end=CLOSE,
        config=config or MEASUREMENT,
    )


def to_close(start, price, step=60):
    """Fill to 14:15 so the window is never flagged partial by accident."""

    points, stamp = [], start
    while stamp <= CLOSE:
        points.append((stamp, price))
        stamp += timedelta(seconds=step)
    return points


def walk(breakpoints, *, step=60, extras=()):
    """A dense piecewise-constant path from the first breakpoint to 14:15.

    Dense on purpose: the gap rule refuses to order milestones seen after a
    hole wider than the budget, so a sparse fixture would test the gap rule
    rather than the ordering rule it was written for.
    """

    stamp = breakpoints[0][0]
    points = []
    while stamp <= CLOSE:
        price = breakpoints[0][1]
        for moment, value in breakpoints:
            if moment <= stamp:
                price = value
        points.append((stamp, price))
        stamp += timedelta(seconds=step)
    return sorted(points + list(extras))


# =========================================================================== #
# 1-3. Ordered milestones
# =========================================================================== #


def test_target_1_before_stop():
    outcome = measure(walk([(at(10, 41), 10.00), (at(10, 50), 10.21)]))
    assert outcome.outcome_status is OutcomeStatus.TARGET_1_FIRST
    assert (
        outcome.outcome_evidence_source
        is OutcomeEvidenceSource.QUOTE_SEQUENCE_SINGLE_MILESTONE
    )
    assert outcome.target_1_reached and not outcome.stop_reached
    assert outcome.first_target_1_touch_at_utc == at(10, 50)
    assert outcome.first_milestone is MilestoneLevel.TARGET_1
    assert outcome.exit_price_proxy == 10.21
    assert outcome.proxy_r_multiple == pytest.approx((10.21 - 10.00) / RISK)


def test_stop_before_target_1():
    outcome = measure(walk([(at(10, 41), 10.00), (at(10, 45), 9.79)]))
    assert outcome.outcome_status is OutcomeStatus.STOP_FIRST
    assert outcome.first_milestone is MilestoneLevel.STOP
    assert outcome.stop_reached and not outcome.target_1_reached
    assert outcome.proxy_r_multiple == pytest.approx((9.79 - 10.00) / RISK)
    assert outcome.proxy_r_multiple < 0


def test_stop_after_target_1_leaves_position_outcome_undefined():
    """T1 then the stop. Without an execution model, both are milestones."""

    outcome = measure(
        walk([(at(10, 41), 10.00), (at(10, 50), 10.25), (at(11, 0), 9.70)])
    )
    assert outcome.outcome_status is OutcomeStatus.STOP_AFTER_T1
    assert outcome.target_1_reached and outcome.stop_reached
    assert not outcome.target_2_reached
    # the first milestone is still established, so a proxy R is computable
    assert outcome.first_milestone is MilestoneLevel.TARGET_1
    assert outcome.exit_at_utc == at(10, 50)


def test_target_2_reached_after_target_1_is_recorded_as_both():
    outcome = measure(
        walk([(at(10, 41), 10.00), (at(10, 50), 10.25), (at(11, 0), 10.45)])
    )
    assert outcome.outcome_status is OutcomeStatus.TARGET_1_FIRST
    assert outcome.target_1_reached and outcome.target_2_reached
    assert outcome.target_1_observed_before_target_2 is True
    assert outcome.first_target_1_touch_at_utc == at(10, 50)
    assert outcome.first_target_2_touch_at_utc == at(11, 0)


def test_a_jump_straight_through_both_targets_never_observed_target_1_alone():
    """Reaching T2 does not prove T1 was independently observed."""

    outcome = measure(walk([(at(10, 41), 10.00), (at(10, 50), 10.50)]))
    assert outcome.outcome_status is OutcomeStatus.TARGET_2_FIRST
    assert outcome.first_target_1_touch_at_utc == outcome.first_target_2_touch_at_utc
    assert outcome.target_1_observed_before_target_2 is False
    assert outcome.first_milestone is MilestoneLevel.TARGET_2


def test_stop_after_target_2():
    outcome = measure(
        walk([(at(10, 41), 10.00), (at(10, 50), 10.45), (at(11, 0), 9.70)])
    )
    assert outcome.outcome_status is OutcomeStatus.STOP_AFTER_T2
    assert outcome.first_milestone is MilestoneLevel.TARGET_2


# =========================================================================== #
# 4-5. Ambiguity
# =========================================================================== #


def test_stop_and_target_at_the_same_instant_is_never_ordered():
    """Two mutually exclusive prices carrying one exchange timestamp."""

    same = at(10, 50)
    outcome = measure(
        walk([(at(10, 41), 10.00)], extras=[(same, 9.75), (same, 10.30)])
    )
    assert (
        outcome.outcome_status
        is OutcomeStatus.BOTH_STOP_AND_TARGET_TOUCHED_SAME_INSTANT
    )
    assert (
        outcome.outcome_evidence_source is OutcomeEvidenceSource.SAME_INSTANT_UNRESOLVED
    )
    assert outcome.stop_reached and outcome.target_1_reached
    # nothing invented: no milestone, no return, no R
    assert outcome.first_milestone is None
    assert outcome.exit_price_proxy is None
    assert outcome.measurement_return_pct is None
    assert outcome.proxy_r_multiple is None


def test_distinct_instants_do_order_them():
    """When exchange time separates the touches, the sequence is used."""

    outcome = measure(
        walk([(at(10, 41), 10.00), (at(10, 50), 10.30), (at(10, 51), 9.75)])
    )
    assert outcome.outcome_status is OutcomeStatus.STOP_AFTER_T1
    assert (
        outcome.outcome_evidence_source
        is OutcomeEvidenceSource.QUOTE_SEQUENCE_DISTINCT_INSTANTS
    )
    reversed_outcome = measure(
        walk([(at(10, 41), 10.00), (at(10, 50), 9.75), (at(10, 51), 10.30)])
    )
    assert reversed_outcome.outcome_status is OutcomeStatus.STOP_FIRST
    assert reversed_outcome.first_milestone is MilestoneLevel.STOP


def test_a_touch_after_a_wide_gap_does_not_establish_ordering():
    """The level was reached; the path through the gap was not observed."""

    outcome = measure(
        [(at(10, 41), 10.00), (at(11, 0), 10.30)] + to_close(at(11, 1), 10.30)
    )
    assert outcome.outcome_status is OutcomeStatus.EXIT_UNDETERMINED_PRICE_DATA_GAP
    assert (
        outcome.outcome_evidence_source
        is OutcomeEvidenceSource.OBSERVATION_GAP_UNRESOLVED
    )
    assert outcome.observation_gap_before_first_touch_seconds == pytest.approx(1140.0)
    assert outcome.target_1_reached, "the milestone itself is still recorded"
    assert outcome.first_milestone is None
    assert outcome.proxy_r_multiple is None
    assert outcome.outcome_measurement_quality is OutcomeQuality.PRICE_DATA_GAP


# =========================================================================== #
# 6-9. Windows and data quality
# =========================================================================== #


def test_no_exit_before_the_continuous_close():
    outcome = measure(to_close(at(10, 41), 10.05))
    assert outcome.outcome_status is OutcomeStatus.NO_EXIT_BY_CONTINUOUS_CLOSE
    assert outcome.outcome_evidence_source is OutcomeEvidenceSource.NOT_APPLICABLE
    assert outcome.outcome_measurement_quality is OutcomeQuality.MEASUREMENT_COMPLETE
    assert not (outcome.stop_reached or outcome.target_1_reached)
    assert outcome.first_milestone is None
    assert outcome.session_end_percent == pytest.approx(0.0)


def test_a_hole_in_the_path_is_flagged():
    points = to_close(at(10, 41), 10.05, step=60)
    thinned = [p for p in points if not (at(11, 0) < p[0] < at(11, 30))]
    outcome = measure(thinned)
    assert outcome.outcome_measurement_quality is OutcomeQuality.PRICE_DATA_GAP
    assert (
        MeasurementReason.OBSERVATION_GAP_EXCEEDS_BUDGET in outcome.measurement_reasons
    )


def test_a_window_that_stops_early_is_partial_not_complete():
    outcome = measure(to_close(at(10, 41), 10.05)[:60])
    assert outcome.outcome_measurement_quality is OutcomeQuality.PARTIAL_WINDOW
    assert MeasurementReason.TAIL_COVERAGE_INCOMPLETE in outcome.measurement_reasons
    assert outcome.outcome_status is OutcomeStatus.NO_EXIT_BY_CONTINUOUS_CLOSE


def test_no_future_data_is_never_given_a_substitute():
    outcome = measure([(at(10, 39), 10.00)])
    assert outcome.outcome_status is OutcomeStatus.NO_FUTURE_DATA
    assert outcome.outcome_measurement_quality is OutcomeQuality.NO_FUTURE_DATA
    assert outcome.entry_price_proxy is None
    assert outcome.session_end_percent is None
    assert MeasurementReason.NO_QUOTE_AFTER_DETECTION in outcome.measurement_reasons


def test_horizons_beyond_the_close_are_never_zero_filled():
    outcome = measure(
        [(at(14, 14), 10.05), (at(14, 20), 10.90), (at(14, 30), 11.50)]
    )
    assert outcome.observation_count == 1, "auction and post-market are excluded"
    assert not outcome.target_1_reached
    assert outcome.session_end_percent == pytest.approx(0.0)


def test_detection_after_the_close_is_unmeasurable():
    outcome = measure([(at(14, 20), 10.30)], sig=signal(detected=at(14, 18)))
    assert outcome.outcome_status is OutcomeStatus.NO_FUTURE_DATA
    assert (
        MeasurementReason.DETECTION_AT_OR_AFTER_SESSION_END
        in outcome.measurement_reasons
    )


# =========================================================================== #
# 10-11. The qualification snapshot
# =========================================================================== #


def test_levels_are_copied_never_recomputed():
    qual = qualification(proposed_stop=9.11, target_1=10.77, target_2=11.99)
    outcome = measure(to_close(at(10, 41), 10.05), qual=qual)
    assert outcome.qualification.proposed_stop == 9.11
    assert outcome.qualification.target_1 == 10.77
    assert outcome.qualification.target_2 == 11.99
    # the odd levels are used as-is: 10.05 is below 10.77, so nothing triggered
    assert not outcome.target_1_reached
    assert outcome.qualification.trigger_price == TRIGGER


def test_the_measured_levels_are_the_persisted_ones(tmp_path):
    store = QualifiedOutcomeStore(tmp_path / "outcomes.db")
    store.initialize()
    qual = qualification(proposed_stop=9.11, target_1=10.77)
    outcome = measure(to_close(at(10, 41), 10.05), qual=qual)
    record(store, [outcome])
    row = store.qualified_rows()[0]
    assert row["proposed_stop"] == 9.11
    assert row["target_1"] == 10.77
    assert row["trigger_price"] == TRIGGER
    assert row["risk_per_share"] == RISK
    assert row["effective_reward_risk"] == 2.0
    assert row["atr_status"] == "INTRADAY_ATR_AVAILABLE"
    assert row["daily_resistance_status"] == "DAILY_RESISTANCE_ABOVE_TRIGGER"
    assert row["strategy_fingerprint"] == "sf"
    assert row["engine_version"] == "ev"
    assert row["qualification_schema_version"] == 1
    assert row["outcome_schema_version"] == OUTCOME_V2_SCHEMA_VERSION


def test_a_changed_qualification_cannot_pass_unnoticed():
    """The snapshot is fingerprinted, so a silent edit is detectable."""

    baseline = qualification()
    assert baseline.fingerprint == qualification().fingerprint
    assert qualification(proposed_stop=9.5).fingerprint != baseline.fingerprint
    assert qualification(target_2=11.0).fingerprint != baseline.fingerprint
    assert qualification(strategy_fingerprint="other").fingerprint != baseline.fingerprint
    # identity fields are not part of the level fingerprint
    assert qualification(live_state_id="other").fingerprint == baseline.fingerprint


def test_an_unusable_qualification_measures_excursions_but_claims_no_outcome():
    outcome = measure(
        to_close(at(10, 41), 10.30), qual=qualification(proposed_stop=None)
    )
    assert outcome.outcome_status is OutcomeStatus.NO_EXIT_BY_CONTINUOUS_CLOSE
    assert outcome.outcome_evidence_source is OutcomeEvidenceSource.NOT_APPLICABLE
    assert outcome.first_milestone is None
    assert outcome.maximum_favorable_excursion_percent is not None
    assert not outcome.target_1_reached


# =========================================================================== #
# Discovery
# =========================================================================== #


def shadow_database(states, qualifications, *, runs=(("lane-a", "FOLLOW"),)):
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE orb_shadow_runs (run_id TEXT, mode TEXT, started_at_utc TEXT)"
    )
    connection.executemany(
        "INSERT INTO orb_shadow_runs VALUES (?,?,?)",
        [(r, m, f"2026-08-20T0{i}:00:00+00:00") for i, (r, m) in enumerate(runs)],
    )
    connection.execute(
        "CREATE TABLE orb_shadow_live_states (live_state_id TEXT, run_id TEXT, "
        "session_date TEXT, canonical_ticker TEXT, final_state TEXT, "
        "observed_at_utc TEXT, opening_range_version_identity TEXT, "
        "evidence_fingerprint TEXT)"
    )
    connection.executemany(
        "INSERT INTO orb_shadow_live_states VALUES (?,?,?,?,?,?,?,?)", states
    )
    connection.execute(
        "CREATE TABLE orb_signal_qualification (live_state_id TEXT PRIMARY KEY, "
        "cycle_id TEXT, trigger_price REAL, proposed_stop REAL, target_1 REAL, "
        "target_2 REAL, usable_target REAL, risk_per_share REAL, "
        "target_1_r_multiple REAL, target_2_r_multiple REAL, "
        "effective_reward_risk REAL, stop_distance_absolute REAL, "
        "stop_distance_percent REAL, stop_distance_atr REAL, atr_status TEXT, "
        "daily_resistance_status TEXT, qualification_status TEXT, "
        "qualification_schema_version INTEGER, strategy_fingerprint TEXT, "
        "engine_version TEXT)"
    )
    connection.executemany(
        "INSERT INTO orb_signal_qualification VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        qualifications,
    )
    connection.commit()
    return connection


def state_row(live_state_id, ticker, state, stamp, run_id="lane-a"):
    return (
        live_state_id, run_id, DAY.isoformat(), ticker, state, stamp.isoformat(),
        "or-v1", f"fp-{live_state_id}",
    )


def qual_row(live_state_id, cycle_id="c0", stop=STOP):
    return (
        live_state_id, cycle_id, TRIGGER, stop, TARGET_1, TARGET_2, TARGET_2,
        RISK, 1.0, 2.0, 2.0, RISK, 0.02, 1.0, "INTRADAY_ATR_AVAILABLE",
        "DAILY_RESISTANCE_ABOVE_TRIGGER", "QUALIFICATION_PERSISTED", 1, "sf", "ev",
    )


def test_discovery_anchors_at_the_first_emission_and_uses_its_levels():
    connection = shadow_database(
        [
            state_row("a", "TEST", "WAIT_BREAKOUT", at(10, 20)),
            state_row("b", "TEST", "ENTRY_READY_RESEARCH", at(10, 40)),
            state_row("c", "TEST", "ENTRY_READY_RESEARCH", at(10, 45)),
        ],
        [qual_row("b", "c1", stop=9.80), qual_row("c", "c2", stop=9.55)],
    )
    discovered = discover_qualified_signals(connection)
    assert len(discovered) == 1
    found, qual = discovered[0]
    assert found.detection_timestamp_utc == at(10, 40)
    assert qual.live_state_id == "b" and qual.cycle_id == "c1"
    assert qual.proposed_stop == 9.80, "the levels as of first emission"


def test_a_signal_without_a_qualification_row_is_not_measured():
    """This is what keeps the historical 34 out of v2."""

    connection = shadow_database(
        [state_row("a", "OLD", "ENTRY_READY_RESEARCH", at(10, 40))], []
    )
    assert discover_qualified_signals(connection) == ()


def test_flicker_stays_one_signal_and_counts_its_episodes():
    connection = shadow_database(
        [
            state_row("a", "TEST", "ENTRY_READY_RESEARCH", at(10, 40)),
            state_row("b", "TEST", "BREAKOUT_REJECTED_STALE", at(10, 45)),
            state_row("c", "TEST", "ENTRY_READY_RESEARCH", at(10, 50)),
        ],
        [qual_row("a"), qual_row("c", "c2")],
    )
    discovered = discover_qualified_signals(connection)
    assert len(discovered) == 1
    assert discovered[0][0].entry_ready_episode_count == 2
    assert discovered[0][1].live_state_id == "a"


def test_an_ambiguous_live_lane_refuses_to_guess():
    connection = shadow_database(
        [state_row("a", "TEST", "ENTRY_READY_RESEARCH", at(10, 40))],
        [qual_row("a")],
        runs=(("lane-a", "FOLLOW"), ("lane-a2", "FOLLOW")),
    )
    with pytest.raises(SignalDiscoveryError):
        discover_qualified_signals(connection)
    assert len(discover_qualified_signals(connection, lane_a_run_id="lane-a")) == 1


# =========================================================================== #
# 12-14. Persistence
# =========================================================================== #


def record(store, outcomes):
    return store.record_qualified(
        outcomes,
        session_date=DAY,
        lane_a_run_id="lane-a",
        shadow_database="orb_full_shadow_2026-08-20.db",
        price_source_database="rubix_live_market.db",
        session_end_utc=CLOSE,
        config=MEASUREMENT,
    )


def test_a_v2_row_round_trips(tmp_path):
    store = QualifiedOutcomeStore(tmp_path / "outcomes.db")
    store.initialize()
    outcome = measure(
        walk([(at(10, 41), 10.00), (at(10, 50), 10.21)])
    )
    record(store, [outcome])
    rows = store.qualified_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome_status"] == OutcomeStatus.TARGET_1_FIRST.value
    assert row["first_milestone"] == MilestoneLevel.TARGET_1.value
    assert row["target_1_reached"] == 1 and row["stop_reached"] == 0
    assert row["proxy_r_multiple"] == pytest.approx(outcome.proxy_r_multiple)
    assert row["qualification_fingerprint"] == outcome.qualification.fingerprint
    assert store.outcome_status_counts() == {OutcomeStatus.TARGET_1_FIRST.value: 1}


def test_rerunning_the_measurement_replaces_rather_than_duplicates(tmp_path):
    store = QualifiedOutcomeStore(tmp_path / "outcomes.db")
    store.initialize()
    outcome = measure(to_close(at(10, 41), 10.05))
    first = record(store, [outcome])
    second = record(store, [outcome])
    assert first != second
    rows = store.qualified_rows()
    assert len(rows) == 1
    assert rows[0]["measurement_run_id"] == second


def test_a_rejected_row_rolls_the_whole_batch_back(tmp_path):
    """The outcome write is one transaction; a bad row leaves none behind."""

    store = QualifiedOutcomeStore(tmp_path / "outcomes.db")
    store.initialize()
    good = measure(
        walk([(at(10, 41), 10.00), (at(10, 50), 10.21)])
    )
    # a return without a milestone behind it violates the schema's own rule
    broken = replace(
        measure(to_close(at(10, 41), 10.05)), measurement_return_pct=1.23
    )
    with pytest.raises(sqlite3.IntegrityError):
        record(store, [good, broken])
    assert store.qualified_rows() == ()


def test_the_store_still_refuses_a_protected_production_database(tmp_path):
    with pytest.raises(ValueError):
        QualifiedOutcomeStore(tmp_path / "rubix_live_market.db")


def test_v1_rows_are_untouched_by_a_v2_measurement(tmp_path):
    """The historical 34 keep their status; v2 writes to its own table."""

    database = tmp_path / "outcomes.db"
    v1 = OutcomeStore(database)
    v1.initialize()
    legacy = measure_signal(
        signal(ticker="LEGACY"),
        path(to_close(at(10, 41), 10.05)),
        session_end=CLOSE,
        config=MEASUREMENT,
    )
    v1.record(
        [legacy],
        session_date=DAY,
        lane_a_run_id="lane-a",
        shadow_database="old.db",
        price_source_database="rubix_live_market.db",
        session_end_utc=CLOSE,
        config=MEASUREMENT,
    )
    before = [dict(row) for row in v1.rows()]
    assert before[0]["tp_sl_status"] == "TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED"

    v2 = QualifiedOutcomeStore(database)
    v2.initialize()
    record(v2, [measure(to_close(at(10, 41), 10.05))])

    assert [dict(row) for row in v1.rows()] == before
    assert len(v2.qualified_rows()) == 1
    with v2.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"orb_signal_outcomes", "orb_signal_outcomes_v2"} <= tables


def test_the_v1_schema_upgrades_additively(tmp_path):
    database = tmp_path / "outcomes.db"
    OutcomeStore(database).initialize()
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"orb_signal_outcomes", "orb_signal_outcomes_v2"} <= tables
    assert SCHEMA_VERSION == 2


# =========================================================================== #
# 15. Vocabulary
# =========================================================================== #


def test_no_execution_terminology_anywhere_in_the_surface():
    """No "realized return", no fill, no order, no position sizing."""

    import inspect

    from scalping_orb.performance import outcome_store, qualified_outcomes

    for module in (qualified_outcomes, outcome_store):
        source = inspect.getsource(module).lower()
        for forbidden in ("realized_return", "realised_return", "realized_r",
                          "fill_price", "order_id", "position_size", "shares_held"):
            assert forbidden not in source, f"{forbidden} in {module.__name__}"

    outcome = measure(
        walk([(at(10, 41), 10.00), (at(10, 50), 10.21)])
    )
    fields = set(vars(outcome))
    assert "measurement_return_pct" in fields and "proxy_r_multiple" in fields
    assert not {name for name in fields if "realized" in name or "realised" in name}
    assert not {name for name in fields if "pnl" in name or "position" in name}


def test_the_trigger_and_the_entry_proxy_are_stored_separately(tmp_path):
    store = QualifiedOutcomeStore(tmp_path / "outcomes.db")
    store.initialize()
    outcome = measure([(at(10, 41), 10.07)] + to_close(at(10, 42), 10.07))
    record(store, [outcome])
    row = store.qualified_rows()[0]
    assert row["trigger_price"] == TRIGGER
    assert row["entry_price_proxy"] == 10.07
    assert row["trigger_price"] != row["entry_price_proxy"]


def test_proxy_r_is_measured_from_the_entry_proxy_not_the_trigger():
    """Measured from the trigger it would be a tautological +1R or -1R."""

    outcome = measure(
        walk([(at(10, 41), 10.05), (at(10, 50), 10.21)])
    )
    from_trigger = (10.21 - TRIGGER) / RISK
    from_entry = (10.21 - 10.05) / RISK
    assert outcome.proxy_r_multiple == pytest.approx(from_entry)
    assert outcome.proxy_r_multiple != pytest.approx(from_trigger)
