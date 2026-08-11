"""Post-signal outcome measurement.

Research only. Nothing here starts Rubix, opens a websocket, authenticates,
reaches the network, triggers a scheduled task, or touches a production
database — every fixture below is built in `tmp_path` or in memory.

The 34 signals these tests protect were produced live on 2026-08-06, 08-10 and
08-11 and are frozen. The measurement layer must therefore be reproducible
against them: the same evidence measured twice has to give the same numbers,
and a hole in the price path has to say so rather than average across itself.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import sqlite3

import pytest

from scalping_orb.config import OrbDataConfig
from scalping_orb.performance.outcome_store import OutcomeStore
from scalping_orb.performance.signal_outcomes import (
    MeasurementQuality,
    MeasurementReason,
    OutcomeMeasurementConfig,
    PriceObservation,
    SignalDiscoveryError,
    SignalRecord,
    TpSlStatus,
    discover_signals,
    measure_signal,
    read_observations,
    session_end_utc,
)


CAIRO = timezone(timedelta(hours=3))
DAY = date(2026, 8, 11)
CONFIG = OrbDataConfig()
MEASUREMENT = OutcomeMeasurementConfig()


def at(hour, minute, second=0):
    """A Cairo wall time, as UTC."""

    return datetime.combine(DAY, time(hour, minute, second), tzinfo=CAIRO).astimezone(
        timezone.utc
    )


def signal(ticker="EGAL", detected=None, episodes=1):
    detected = detected or at(10, 36, 43)
    return SignalRecord(
        session_date=DAY,
        canonical_ticker=ticker,
        lane_a_run_id="lane-a",
        detection_timestamp_utc=detected,
        entry_ready_observation_count=53,
        entry_ready_episode_count=episodes,
        last_entry_ready_observed_at_utc=detected + timedelta(hours=3),
        opening_range_version_identity="or-v1",
        detection_evidence_fingerprint="fp",
    )


def path(points):
    return tuple(
        PriceObservation(market_timestamp_utc=stamp, price=price)
        for stamp, price in points
    )


# -- session boundary ------------------------------------------------------


def test_session_end_is_the_cairo_continuous_close_in_utc():
    boundary = session_end_utc(DAY, CONFIG)
    assert boundary == datetime(2026, 8, 11, 11, 15, tzinfo=timezone.utc)
    assert boundary.astimezone(CAIRO).time() == CONFIG.continuous_end


def test_observations_after_the_close_are_excluded():
    """The auction is a different mechanism and is outside the window."""

    outcome = measure_signal(
        signal(),
        path(
            [
                (at(10, 40), 100.0),
                (at(14, 14), 101.0),
                (at(14, 20), 130.0),  # closing auction
                (at(14, 28), 140.0),  # post-market
            ]
        ),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.observation_count == 2
    assert outcome.maximum_favorable_price == 101.0
    assert outcome.session_end_price == 101.0


# -- entry selection -------------------------------------------------------


def test_entry_is_the_first_quote_at_or_after_detection():
    detected = at(11, 0)
    outcome = measure_signal(
        signal(detected=detected),
        path([(at(10, 59, 30), 9.0), (at(11, 0, 7), 10.0), (at(11, 5), 11.0)]),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.entry_price == 10.0
    assert outcome.entry_quote_market_timestamp_utc == at(11, 0, 7)
    assert outcome.entry_lag_seconds == pytest.approx(7.0)


def test_a_signal_with_no_forward_quote_is_never_given_a_substitute_price():
    outcome = measure_signal(
        signal(detected=at(13, 0)),
        path([(at(12, 59), 10.0)]),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.measurement_quality is MeasurementQuality.NO_FUTURE_DATA
    assert MeasurementReason.NO_QUOTE_AFTER_DETECTION in outcome.measurement_reasons
    assert outcome.entry_price is None
    assert outcome.maximum_favorable_excursion_percent is None
    assert outcome.session_end_percent is None
    assert not outcome.measured


def test_detection_after_the_close_is_unmeasurable_not_zero():
    outcome = measure_signal(
        signal(detected=at(14, 20)),
        path([(at(14, 21), 10.0)]),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.measurement_quality is MeasurementQuality.NO_FUTURE_DATA
    assert (
        MeasurementReason.DETECTION_AT_OR_AFTER_SESSION_END
        in outcome.measurement_reasons
    )


# -- excursions ------------------------------------------------------------


def test_excursion_signs_and_magnitudes():
    outcome = measure_signal(
        signal(),
        path(
            [
                (at(11, 0), 100.0),
                (at(11, 10), 104.0),
                (at(11, 20), 97.0),
                (at(11, 30), 102.0),
            ]
        ),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.entry_price == 100.0
    assert outcome.maximum_favorable_excursion_absolute == pytest.approx(4.0)
    assert outcome.maximum_favorable_excursion_percent == pytest.approx(4.0)
    assert outcome.seconds_to_maximum_favorable == pytest.approx(600.0)
    # signed, never an unsigned "drawdown" that reads like a gain
    assert outcome.maximum_adverse_excursion_absolute == pytest.approx(-3.0)
    assert outcome.maximum_adverse_excursion_percent == pytest.approx(-3.0)
    assert outcome.seconds_to_maximum_adverse == pytest.approx(1200.0)
    assert outcome.session_end_percent == pytest.approx(2.0)
    assert outcome.price_change_count == 3


def test_a_flat_path_measures_zero_on_both_sides():
    outcome = measure_signal(
        signal(),
        path([(at(11, 0), 10.0), (at(11, 1), 10.0), (at(11, 2), 10.0)]),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.maximum_favorable_excursion_percent == 0.0
    assert outcome.maximum_adverse_excursion_percent == 0.0
    assert outcome.price_change_count == 0
    assert outcome.measurement_quality is MeasurementQuality.PRICE_DATA_GAP  # tail


def test_extremes_report_the_earliest_time_they_were_reached():
    outcome = measure_signal(
        signal(),
        path([(at(11, 0), 10.0), (at(11, 5), 11.0), (at(11, 9), 11.0)]),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.maximum_favorable_at_utc == at(11, 5)


# -- price data gaps -------------------------------------------------------


def dense(start, end, price=10.0, step=60):
    points = []
    stamp = start
    while stamp <= end:
        points.append((stamp, price))
        stamp += timedelta(seconds=step)
    return points


def test_a_continuous_path_to_the_close_is_measurement_complete():
    outcome = measure_signal(
        signal(),
        path(dense(at(11, 0), at(14, 15))),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.measurement_quality is MeasurementQuality.MEASUREMENT_COMPLETE
    assert outcome.measurement_reasons == ()


def test_a_hole_in_the_path_is_flagged_rather_than_averaged_across():
    points = dense(at(11, 0), at(12, 0)) + dense(at(12, 20), at(14, 15))
    outcome = measure_signal(
        signal(), path(points), session_end=session_end_utc(DAY, CONFIG), config=MEASUREMENT
    )
    assert outcome.measurement_quality is MeasurementQuality.PRICE_DATA_GAP
    assert (
        MeasurementReason.OBSERVATION_GAP_EXCEEDS_BUDGET in outcome.measurement_reasons
    )
    assert outcome.maximum_observation_gap_seconds == pytest.approx(1200.0)
    # the excursions are still reported; the label travels with them
    assert outcome.maximum_favorable_excursion_percent is not None


def test_a_path_that_stops_early_is_flagged_on_tail_coverage():
    outcome = measure_signal(
        signal(),
        path(dense(at(11, 0), at(14, 0))),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.measurement_quality is MeasurementQuality.PRICE_DATA_GAP
    assert MeasurementReason.TAIL_COVERAGE_INCOMPLETE in outcome.measurement_reasons
    assert outcome.tail_gap_seconds == pytest.approx(900.0)


def test_a_single_observation_is_insufficient():
    outcome = measure_signal(
        signal(),
        path([(at(14, 14, 45), 10.0)]),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.measurement_quality is MeasurementQuality.PRICE_DATA_GAP
    assert MeasurementReason.INSUFFICIENT_OBSERVATIONS in outcome.measurement_reasons
    assert outcome.maximum_observation_gap_seconds == 0.0


def test_the_gap_budget_is_a_declared_contract_with_a_fingerprint():
    assert MEASUREMENT.maximum_observation_gap_seconds == 300.0
    assert MEASUREMENT.maximum_tail_gap_seconds == 300.0
    assert MEASUREMENT.fingerprint == OutcomeMeasurementConfig().fingerprint
    assert (
        OutcomeMeasurementConfig(maximum_observation_gap_seconds=600.0).fingerprint
        != MEASUREMENT.fingerprint
    )
    with pytest.raises(ValueError):
        OutcomeMeasurementConfig(maximum_observation_gap_seconds=0.0)
    with pytest.raises(ValueError):
        OutcomeMeasurementConfig(minimum_observations=1)


# -- no invented stop or target --------------------------------------------


def test_no_outcome_ever_carries_a_reconstructed_stop_or_target():
    outcome = measure_signal(
        signal(),
        path(dense(at(11, 0), at(14, 15))),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    assert outcome.tp_sl_status is TpSlStatus.TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED
    fields = set(vars(outcome))
    assert not {name for name in fields if "r_multiple" in name}
    assert not {name for name in fields if "stop" in name or "target" in name}


# -- price source reading --------------------------------------------------


def quotes_database(rows):
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE quotes (id INTEGER PRIMARY KEY, ticker TEXT, "
        "last_price REAL, market_timestamp TEXT, received_at TEXT)"
    )
    connection.executemany(
        "INSERT INTO quotes (ticker, last_price, market_timestamp, received_at) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    return connection


def test_reader_bounds_the_window_chronologically_not_lexically():
    """A `+03:00` bound sorts above a `+00:00` auction row; parsing prevents that."""

    connection = quotes_database(
        [
            ("EGAL", 100.0, "2026-08-11T08:00:00+00:00", "x"),
            ("EGAL", 101.0, "2026-08-11T11:14:00+00:00", "x"),
            ("EGAL", 900.0, "2026-08-11T11:20:00+00:00", "x"),  # 14:20 Cairo
            ("MIPH", 500.0, "2026-08-11T09:00:00+00:00", "x"),
        ]
    )
    observations = read_observations(
        connection,
        "EGAL",
        start_utc=datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc),
        end_utc=session_end_utc(DAY, CONFIG),
    )
    assert [item.price for item in observations] == [100.0, 101.0]


def test_reader_skips_non_positive_prices_and_orders_by_market_time():
    connection = quotes_database(
        [
            ("EGAL", 0.0, "2026-08-11T08:05:00+00:00", "x"),
            ("EGAL", None, "2026-08-11T08:06:00+00:00", "x"),
            ("EGAL", 12.0, "2026-08-11T08:20:00+00:00", "2026-08-11T08:00:00+00:00"),
            ("EGAL", 11.0, "2026-08-11T08:10:00+00:00", "2026-08-11T09:00:00+00:00"),
        ]
    )
    observations = read_observations(
        connection,
        "EGAL",
        start_utc=datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc),
        end_utc=session_end_utc(DAY, CONFIG),
    )
    # ordered by market_timestamp, never by received_at
    assert [item.price for item in observations] == [11.0, 12.0]


# -- signal discovery ------------------------------------------------------


def shadow_database(rows, *, runs=(("lane-a", "FOLLOW"),)):
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE orb_shadow_runs (run_id TEXT, mode TEXT, started_at_utc TEXT)"
    )
    connection.executemany(
        "INSERT INTO orb_shadow_runs VALUES (?, ?, ?)",
        [(run_id, mode, f"2026-08-11T0{index}:00:00+00:00")
         for index, (run_id, mode) in enumerate(runs)],
    )
    connection.execute(
        "CREATE TABLE orb_shadow_live_states (run_id TEXT, session_date TEXT, "
        "canonical_ticker TEXT, final_state TEXT, observed_at_utc TEXT, "
        "opening_range_version_identity TEXT, evidence_fingerprint TEXT, "
        "candidate_identity TEXT)"
    )
    connection.executemany(
        "INSERT INTO orb_shadow_live_states VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    connection.commit()
    return connection


def state(ticker, state_name, stamp, run_id="lane-a", identity=None):
    return (
        run_id,
        "2026-08-11",
        ticker,
        state_name,
        stamp.isoformat(),
        "or-v1",
        f"fp-{ticker}-{stamp.isoformat()}",
        identity or f"ci-{ticker}-{stamp.isoformat()}",
    )


def test_discovery_anchors_a_signal_at_its_first_entry_ready_observation():
    connection = shadow_database(
        [
            state("EGAL", "WAIT_BREAKOUT", at(10, 20)),
            state("EGAL", "ENTRY_READY_RESEARCH", at(10, 36, 43)),
            state("EGAL", "ENTRY_READY_RESEARCH", at(10, 41, 40)),
            state("MIPH", "ENTRY_READY_RESEARCH", at(11, 0)),
        ]
    )
    signals = discover_signals(connection)
    assert [item.canonical_ticker for item in signals] == ["EGAL", "MIPH"]
    egal = signals[0]
    assert egal.detection_timestamp_utc == at(10, 36, 43)
    assert egal.entry_ready_observation_count == 2
    assert egal.last_entry_ready_observed_at_utc == at(10, 41, 40)
    assert egal.session_date == DAY
    assert egal.lane_a_run_id == "lane-a"


def test_a_symbol_that_flickers_stays_one_signal_with_the_flicker_recorded():
    """MIPH re-entered ENTRY_READY six times on 2026-08-11 via BREAKOUT_REJECTED_STALE."""

    rows = [state("MIPH", "WAIT_BREAKOUT", at(10, 16))]
    stamps = [(10, 36), (12, 26), (13, 6), (13, 15), (13, 33), (14, 10)]
    for hour, minute in stamps:
        rows.append(state("MIPH", "ENTRY_READY_RESEARCH", at(hour, minute)))
        rows.append(state("MIPH", "BREAKOUT_REJECTED_STALE", at(hour, minute + 1)))
    signals = discover_signals(shadow_database(rows))
    assert len(signals) == 1
    assert signals[0].entry_ready_episode_count == 6
    assert signals[0].detection_timestamp_utc == at(10, 36)


def test_candidate_identity_is_per_evaluation_and_never_splits_a_signal():
    connection = shadow_database(
        [
            state("EGAL", "ENTRY_READY_RESEARCH", at(10, 36), identity="a"),
            state("EGAL", "ENTRY_READY_RESEARCH", at(10, 41), identity="b"),
            state("EGAL", "ENTRY_READY_RESEARCH", at(10, 46), identity="c"),
        ]
    )
    assert len(discover_signals(connection)) == 1


def test_reconstruction_lane_signals_are_not_measured():
    connection = shadow_database(
        [
            state("EGAL", "ENTRY_READY_RESEARCH", at(10, 36)),
            state("CANA", "ENTRY_READY_RESEARCH", at(10, 40), run_id="lane-b"),
        ],
        runs=(("lane-a", "FOLLOW"), ("lane-b", "RECONSTRUCT")),
    )
    assert [item.canonical_ticker for item in discover_signals(connection)] == ["EGAL"]


def test_an_ambiguous_live_lane_refuses_to_guess():
    connection = shadow_database(
        [state("EGAL", "ENTRY_READY_RESEARCH", at(10, 36))],
        runs=(("lane-a", "FOLLOW"), ("lane-a2", "FOLLOW")),
    )
    with pytest.raises(SignalDiscoveryError):
        discover_signals(connection)
    assert len(discover_signals(connection, lane_a_run_id="lane-a")) == 1


def test_a_session_with_no_live_lane_refuses_rather_than_returning_nothing():
    connection = shadow_database([], runs=(("lane-b", "RECONSTRUCT"),))
    with pytest.raises(SignalDiscoveryError):
        discover_signals(connection)


# -- persistence -----------------------------------------------------------


def measured():
    return measure_signal(
        signal(),
        path(dense(at(11, 0), at(14, 15)) + [(at(12, 0), 11.0)]),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )


def store_at(tmp_path):
    store = OutcomeStore(tmp_path / "outcomes.db")
    store.initialize()
    return store


def record(store, outcomes):
    return store.record(
        outcomes,
        session_date=DAY,
        lane_a_run_id="lane-a",
        shadow_database="orb_full_shadow_2026-08-11.db",
        price_source_database="rubix_live_market.db",
        session_end_utc=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )


def test_outcomes_round_trip_through_the_store(tmp_path):
    store = store_at(tmp_path)
    outcome = measured()
    record(store, [outcome])
    rows = store.rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["canonical_ticker"] == "EGAL"
    assert row["session_date"] == "2026-08-11"
    assert row["tp_sl_status"] == TpSlStatus.TP_SL_RULE_EXISTS_BUT_NOT_PERSISTED.value
    assert row["entry_price"] == pytest.approx(outcome.entry_price)
    assert row["maximum_adverse_excursion_percent"] <= 0.0
    assert row["maximum_favorable_excursion_percent"] >= 0.0
    assert row["entry_ready_episode_count"] == 1


def test_remeasuring_replaces_rather_than_duplicates(tmp_path):
    store = store_at(tmp_path)
    first = record(store, [measured()])
    second = record(store, [measured()])
    assert first != second
    rows = store.rows()
    assert len(rows) == 1
    assert rows[0]["measurement_run_id"] == second


def test_unmeasurable_signals_are_persisted_as_such(tmp_path):
    store = store_at(tmp_path)
    outcome = measure_signal(
        signal(ticker="CANA", detected=at(13, 0)),
        (),
        session_end=session_end_utc(DAY, CONFIG),
        config=MEASUREMENT,
    )
    record(store, [outcome])
    row = store.rows()[0]
    assert row["measurement_quality"] == MeasurementQuality.NO_FUTURE_DATA.value
    assert row["entry_price"] is None
    assert row["session_end_percent"] is None
    assert store.quality_counts() == {MeasurementQuality.NO_FUTURE_DATA.value: 1}


def test_the_store_refuses_to_open_a_protected_production_database(tmp_path):
    with pytest.raises(ValueError):
        OutcomeStore(tmp_path / "rubix_live_market.db")
    with pytest.raises(ValueError):
        OutcomeStore(tmp_path / "scalping.db")


def test_the_measurement_run_records_its_own_contract(tmp_path):
    store = store_at(tmp_path)
    run_id = record(store, [measured()])
    with store.connect() as connection:
        row = connection.execute(
            "SELECT * FROM orb_signal_outcome_runs WHERE measurement_run_id=?",
            (run_id,),
        ).fetchone()
    assert row["measurement_config_fingerprint"] == MEASUREMENT.fingerprint
    assert row["signals_measured"] == 1
    assert row["session_end_utc"].startswith("2026-08-11T11:15")
    assert "rubix_live_market.db" in row["price_source_database"]
