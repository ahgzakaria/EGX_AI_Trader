"""Contract tests for evaluation liveness and stall-aware classification.

Every test here is written against the 2026-08-04 failure shape: a run whose
source kept delivering rows, whose cursor kept advancing, and which kept
reporting LIVE_SHADOW_HEALTHY for more than two hours after normalization had
stopped producing a single event.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from scalping_orb.config import OrbDataConfig
from scalping_orb.liveness import (
    CycleObservation,
    EvaluationHealth,
    EvaluationLivenessMonitor,
    NormalizationHealth,
)
from scalping_orb.shadow_service import (
    SessionClassification,
    ShadowLiveStatus,
    classify_session,
)

DAY = date(2026, 8, 4)
CONFIG = OrbDataConfig()


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Cairo wall clock expressed in UTC (Cairo is UTC+3)."""

    return datetime(DAY.year, DAY.month, DAY.day, hour - 3, minute, second,
                    tzinfo=timezone.utc)


def monitor(**overrides) -> EvaluationLivenessMonitor:
    settings = dict(
        normalization_stall_max_cycles=5,
        normalization_stall_max_seconds=120.0,
        evaluation_stall_max_seconds=180.0,
    )
    settings.update(overrides)
    return EvaluationLivenessMonitor(OrbDataConfig(**settings))


# -- normalization health -------------------------------------------------


def test_rows_that_produce_events_are_healthy():
    verdict = monitor().observe(
        CycleObservation(at(10, 5), source_rows_read=400, normalized_events=120,
                         symbols_evaluated=30)
    )

    assert verdict.normalization is NormalizationHealth.HEALTHY
    assert verdict.evaluation is EvaluationHealth.HEALTHY
    assert verdict.healthy
    assert verdict.consecutive_zero_normalized_cycles == 0


def test_rows_that_produce_nothing_eventually_stall():
    live = monitor(normalization_stall_max_cycles=5)
    live.observe(CycleObservation(at(10, 0), 400, 120, 30))

    verdicts = [
        live.observe(
            CycleObservation(at(10, 1 + index), source_rows_read=5_000,
                             normalized_events=0, symbols_evaluated=0)
        )
        for index in range(5)
    ]

    assert verdicts[0].normalization is NormalizationHealth.HEALTHY
    assert verdicts[-1].normalization is NormalizationHealth.STALLED
    assert verdicts[-1].consecutive_zero_normalized_cycles == 5
    assert "normalized none" in verdicts[-1].normalization_stall_reason
    assert not verdicts[-1].healthy


def test_a_quiet_source_is_reported_as_idle_not_as_a_normalization_stall():
    """No rows is a source condition. Blaming normalization would be a lie."""

    live = monitor()
    verdict = live.observe(
        CycleObservation(at(10, 5), source_rows_read=0, normalized_events=0,
                         symbols_evaluated=0)
    )

    assert verdict.normalization is NormalizationHealth.IDLE_NO_SOURCE_ROWS
    assert verdict.healthy
    assert not live.critical_stall_observed


def test_a_long_quiet_source_never_becomes_a_normalization_stall():
    """Even a closed market must not be reported as a broken normalizer.

    Without this, a quiet afternoon accumulates zero-normalized cycles and
    trips the stall limit, and the one signal that means "the pipeline is
    broken" starts firing when nothing is wrong.
    """

    live = monitor(normalization_stall_max_cycles=5)
    live.observe(CycleObservation(at(10, 0), 400, 120, 30))

    verdicts = [
        live.observe(
            CycleObservation(at(10, 1 + index), source_rows_read=0,
                             normalized_events=0, symbols_evaluated=0)
        )
        for index in range(12)
    ]

    assert all(
        verdict.normalization is NormalizationHealth.IDLE_NO_SOURCE_ROWS
        for verdict in verdicts
    )
    assert verdicts[-1].consecutive_zero_normalized_cycles == 0
    assert not live.critical_stall_observed


def test_elapsed_time_alone_can_declare_a_stall():
    live = monitor(normalization_stall_max_cycles=10_000,
                   normalization_stall_max_seconds=120.0)
    live.observe(CycleObservation(at(10, 0), 400, 120, 30))

    verdict = live.observe(
        CycleObservation(at(10, 3), source_rows_read=900, normalized_events=0,
                         symbols_evaluated=0)
    )

    assert verdict.normalization is NormalizationHealth.STALLED
    assert verdict.seconds_since_normalized_event == pytest.approx(180.0)


def test_capacity_exhaustion_is_its_own_named_failure():
    live = monitor()
    verdict = live.observe(
        CycleObservation(at(10, 5), source_rows_read=900, normalized_events=40,
                         symbols_evaluated=10, dedupe_capacity_exhausted=True)
    )

    assert verdict.normalization is NormalizationHealth.CAPACITY_EXHAUSTED
    assert verdict.critical
    assert not verdict.healthy


def test_a_recorded_critical_stall_survives_recovery():
    """A run cannot clear its own history by resuming later."""

    live = monitor(normalization_stall_max_cycles=2)
    live.observe(CycleObservation(at(10, 0), 400, 120, 30))
    for index in range(2):
        live.observe(CycleObservation(at(10, 1 + index), 5_000, 0, 0))
    assert live.critical_stall_observed

    recovered = live.observe(CycleObservation(at(13, 0), 400, 120, 30))

    assert recovered.normalization is NormalizationHealth.HEALTHY
    assert live.critical_stall_observed, "the recorded gap must not be erased"
    assert live.critical_stall_first_seen == at(10, 2)


# -- evaluation health ----------------------------------------------------


def test_evaluation_stalls_when_no_symbol_is_evaluated_for_too_long():
    live = monitor(evaluation_stall_max_seconds=180.0)
    live.observe(CycleObservation(at(10, 0), 400, 120, 30))

    verdict = live.observe(
        CycleObservation(at(10, 4), source_rows_read=400, normalized_events=120,
                         symbols_evaluated=0)
    )

    assert verdict.normalization is NormalizationHealth.HEALTHY
    assert verdict.evaluation is EvaluationHealth.STALLED
    assert not verdict.healthy


# -- live status ----------------------------------------------------------


class _Watermark:
    live_evidence_fresh = True


class _Snapshot:
    events_by_ticker = {"AALR": ()}
    watermark = _Watermark()


def live_status(verdict):
    from scalping_orb.shadow_service import OrbShadowService

    return OrbShadowService.live_status_for(
        OrbShadowService.__new__(OrbShadowService), _Snapshot(), liveness=verdict
    )


def test_a_live_source_with_dead_normalization_is_not_healthy():
    live = monitor(normalization_stall_max_cycles=1)
    live.observe(CycleObservation(at(10, 0), 400, 120, 30))
    stalled = live.observe(CycleObservation(at(10, 1), 5_000, 0, 0))

    assert live_status(stalled) is ShadowLiveStatus.LIVE_SHADOW_NORMALIZATION_STALLED


def test_a_live_source_with_dead_evaluation_is_not_healthy():
    live = monitor(evaluation_stall_max_seconds=60.0)
    live.observe(CycleObservation(at(10, 0), 400, 120, 30))
    stalled = live.observe(CycleObservation(at(10, 5), 400, 120, 0))

    assert live_status(stalled) is ShadowLiveStatus.LIVE_SHADOW_EVALUATION_STALLED


def test_a_fully_live_pipeline_is_healthy():
    verdict = monitor().observe(CycleObservation(at(10, 5), 400, 120, 30))

    assert live_status(verdict) is ShadowLiveStatus.LIVE_SHADOW_HEALTHY


# -- session classification ----------------------------------------------


def _classify(**overrides):
    base = dict(
        session_date=DAY,
        runner_started_utc=at(9, 55),
        runner_finished_utc=at(14, 20),
        config=CONFIG,
        maximum_polling_gap_seconds=30.0,
        allowed_polling_gap_seconds=300.0,
        heartbeat_count=500,
        minimum_heartbeats=60,
        cursor_advanced=True,
        opening_ranges_ready=50,
        observed_exchange_minutes=250,
        minimum_exchange_minutes=200,
        normalization_progress_through_continuous_end=True,
        evaluation_progress_through_continuous_end=True,
        no_critical_evaluation_stall=True,
    )
    base.update(overrides)
    return classify_session(**base)


def test_a_session_that_stopped_normalizing_cannot_be_full():
    classification, reasons = _classify(
        normalization_progress_through_continuous_end=False
    )

    assert classification is SessionClassification.PARTIAL_SHADOW_SESSION
    assert "NORMALIZATION_STALLED_BEFORE_CONTINUOUS_END" in reasons


def test_a_session_that_stopped_evaluating_cannot_be_full():
    classification, reasons = _classify(
        evaluation_progress_through_continuous_end=False
    )

    assert classification is SessionClassification.PARTIAL_SHADOW_SESSION
    assert "EVALUATION_STALLED_BEFORE_CONTINUOUS_END" in reasons


def test_a_recorded_critical_stall_prevents_full_even_after_recovery():
    classification, reasons = _classify(no_critical_evaluation_stall=False)

    assert classification is SessionClassification.PARTIAL_SHADOW_SESSION
    assert "CRITICAL_EVALUATION_STALL_OBSERVED" in reasons


def test_the_2026_08_04_shape_classifies_as_partial():
    """Perfect source coverage, dead pipeline after 12:11 Cairo."""

    classification, reasons = _classify(
        maximum_polling_gap_seconds=2.0,
        heartbeat_count=100_000,
        observed_exchange_minutes=255,
        normalization_progress_through_continuous_end=False,
        evaluation_progress_through_continuous_end=False,
        no_critical_evaluation_stall=False,
    )

    assert classification is SessionClassification.PARTIAL_SHADOW_SESSION
    assert set(reasons) == {
        "NORMALIZATION_STALLED_BEFORE_CONTINUOUS_END",
        "EVALUATION_STALLED_BEFORE_CONTINUOUS_END",
        "CRITICAL_EVALUATION_STALL_OBSERVED",
    }


def test_a_healthy_pipeline_still_reaches_full():
    classification, reasons = _classify()

    assert classification is SessionClassification.FULL_SHADOW_SESSION
    assert reasons == ()


def test_liveness_arguments_are_required_not_defaulted():
    """A caller that cannot prove liveness must not silently get FULL."""

    with pytest.raises(TypeError):
        classify_session(
            session_date=DAY,
            runner_started_utc=at(9, 55),
            runner_finished_utc=at(14, 20),
            config=CONFIG,
            maximum_polling_gap_seconds=30.0,
            allowed_polling_gap_seconds=300.0,
            heartbeat_count=500,
            minimum_heartbeats=60,
            cursor_advanced=True,
            opening_ranges_ready=50,
            observed_exchange_minutes=250,
            minimum_exchange_minutes=200,
        )


# -- configuration contract ----------------------------------------------


def test_retention_below_hard_capacity_is_rejected():
    with pytest.raises(ValueError):
        OrbDataConfig(
            deduplication_retention_payloads=100,
            deduplication_hard_capacity=10,
        )


def test_the_dedup_contract_change_changes_the_configuration_identity():
    baseline = OrbDataConfig().fingerprint
    widened = OrbDataConfig(deduplication_retention_payloads=200_000).fingerprint

    assert baseline != widened


def test_the_removed_cap_setting_is_gone():
    """A stale caller must fail loudly rather than set an ignored field."""

    assert not hasattr(OrbDataConfig(), "maximum_seen_payloads_per_session")
    with pytest.raises(TypeError):
        OrbDataConfig(maximum_seen_payloads_per_session=250_000)
