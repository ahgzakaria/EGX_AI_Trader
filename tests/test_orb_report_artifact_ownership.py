"""ORB report finalization, per-run artifact ownership and reconstruction timing.

Research only. Nothing here starts Rubix, opens a websocket, authenticates,
reaches the network, triggers a scheduled task, or reads a production database.

These tests pin three defects found while auditing the 2026-08-05 live session,
which met every observation criterion and still published a permanent
`PARTIAL_SHADOW_SESSION` report:

1. `report_completed` was a FULL criterion, and the report rendered the verdict
   *before* that criterion could be set. It was therefore never true inside any
   report — a FULL session could not publish a FULL document.
2. Lane A and Lane B both wrote `shadow_run_summary.json` into one session
   directory, so the post-close reconstruction silently replaced the live
   session's summary with its own (mode=RECONSTRUCT,
   runner_started_before_open=false, PARTIAL_SHADOW_SESSION).
3. Lane B persisted only its row write time, so no Lane A/Lane B detection-time
   delta could be computed at all.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from scalping_orb.repository import SCHEMA_VERSION, OrbResearchRepository
from scalping_orb.shadow_orchestrator import (
    FullSessionCriteria,
    OrchestratorState,
    OrchestratorVerdict,
    PipelineCompletionStatus,
    ReportPublicationError,
    ReportPublicationStatus,
    publish_atomically,
)
from scalping_orb.shadow_service import (
    ReconstructedTimeStatus,
    ShadowComparisonRow,
    ShadowLiveStatus,
    ShadowReconstructionRecord,
    ShadowStateRecord,
    TimingComparisonStatus,
    reconstructed_times,
    timing_comparison,
)
from scripts.run_orb_shadow_session import (
    LANE_A,
    LANE_B,
    SESSION_SUMMARY_ALIAS,
    ShadowRunner,
    lane_artifact_name,
    lane_for_mode,
    read_run_summary,
)
from scripts.run_orb_shadow_session import parse_args as session_args

from tests.test_orb_shadow_orchestrator import (
    DAY,
    at,
    orchestrator,
    source,  # noqa: F401 - pytest fixture
    synthetic_source,
)


ALL_CRITERIA_MET = FullSessionCriteria(
    **{name: True for name in FullSessionCriteria.__dataclass_fields__}
)


def _seed_live_run(instance, *, lane_a=1, classification="FULL_SHADOW_SESSION"):
    """A committed live run shaped like the 2026-08-05 Lane A run."""

    from scalping_orb.shadow_service import ShadowCycleMetrics

    repository = instance.repository
    run_id = "seededLiveRun"
    repository.start_shadow_run(
        run_id, DAY, mode="FOLLOW", started_at_utc=at(9, 45),
        runner_started_before_open=True,
        source_path_identity=instance.source_identity,
        config_identity=instance.data_config.fingerprint,
        strategy_fingerprint=instance.strategy_config.strategy_fingerprint,
        engine_version="ev", active_universe_only=True,
    )
    if lane_a:
        cycle = ShadowCycleMetrics(
            cycle_id="seed-c0", cycle_index=0, started_at_utc=at(11, 0),
            finished_at_utc=at(11, 1), cursor_low_source_id=0, cursor_high_source_id=1,
            source_rows_read=1, normalized_events=1, session_loads=1,
            symbols_in_snapshot=1, symbols_evaluated=1, snapshot_identity="s",
            live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, duration_seconds=0.1,
        )
        record = ShadowStateRecord(
            session_date=DAY, canonical_ticker="AAA", opening_range_revision=0,
            opening_range_version_identity="or-v1",
            final_state="BREAKOUT_REJECTED_STALE", terminal=True,
            rejection_reasons=("LIVE_DECISION_DISABLED_FRESHNESS",),
            evidence_fingerprint="e", candidate_identity="c",
            live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,
            observed_at_utc=at(11, 0), exchange_watermark_utc=at(11, 0),
            observed_receive_lag_seconds=1.0,
        )
        repository.persist_shadow_cycle(run_id, cycle, (record,))
    repository.finish_shadow_run(
        run_id, finished_at_utc=at(14, 20), stop_reason="CONTINUOUS_END_REACHED",
        session_classification=classification,
    )
    return run_id


def _ready_to_report(tmp_path, source, *, criteria=ALL_CRITERIA_MET):
    """An orchestrator standing exactly where `write_report` is called.

    `report_dir` and `log_dir` are redirected into `tmp_path`: the defaults sit
    under the repository, and a test must never publish into the checkout it is
    testing.
    """

    instance = orchestrator(tmp_path, source)
    instance.report_dir = tmp_path / "report"
    instance.log_dir = tmp_path / "log"
    assert instance.pre_session_checks()
    _seed_live_run(instance)
    instance.state = OrchestratorState.LIVE_SHADOW_PARTIAL
    assert instance.run_post_session()
    instance.criteria = criteria
    return instance


def _report_text(instance):
    return (instance.report_dir / "FULL_SHADOW_SESSION_REPORT.md").read_text(
        encoding="utf-8"
    )


def _status_json(instance):
    return json.loads(
        (instance.report_dir / "orchestrator_status.json").read_text(encoding="utf-8")
    )


# =========================================================================== #
# REPORT FINALIZATION
# =========================================================================== #


def test_report_completion_is_not_a_full_criterion():
    """The self-reference itself, pinned.

    While `report_completed` was a criterion it could only be satisfied after
    the report existed, so `criteria.verdict()` evaluated inside the renderer
    was structurally incapable of returning FULL.
    """

    assert "report_completed" not in FullSessionCriteria.__dataclass_fields__
    for name in FullSessionCriteria.__dataclass_fields__:
        assert "report" not in name, f"{name} makes the verdict self-referential"
        assert "publish" not in name


def test_every_full_criterion_is_settled_before_any_artifact_exists(tmp_path, source):
    instance = _ready_to_report(tmp_path, source)
    before = instance.session_evidence_verdict()
    assert before is OrchestratorVerdict.FULL_SHADOW_SESSION_OBSERVED
    assert instance.write_report() is True
    # Publishing changed the pipeline status and nothing about the evidence.
    assert instance.session_evidence_verdict() is before
    assert instance.criteria == ALL_CRITERIA_MET


def test_a_full_session_publishes_a_full_report(tmp_path, source):
    instance = _ready_to_report(tmp_path, source)
    assert instance.write_report() is True

    text = _report_text(instance)
    assert "FULL_SHADOW_SESSION_OBSERVED" in text
    assert "All FULL criteria met." in text
    assert "PARTIAL_SHADOW_SESSION" not in text
    assert "report_completed" not in text


def test_database_log_report_and_status_json_all_agree(tmp_path, source):
    instance = _ready_to_report(tmp_path, source)
    assert instance.write_report() is True
    instance.move(OrchestratorState.SESSION_COMPLETE, "workflow complete")
    payload = instance._finish()

    expected = OrchestratorVerdict.FULL_SHADOW_SESSION_OBSERVED.value
    stored = instance.repository.find_orchestrator_runs(session_date=DAY)[0]

    assert stored["final_verdict"] == expected                   # database
    assert payload["session_evidence_verdict"] == expected       # returned status
    assert _status_json(instance)["session_evidence_verdict"] == expected
    assert expected in _report_text(instance)                    # report
    log = (instance.log_dir / "orchestrator.log").read_text(encoding="utf-8")
    assert expected in log                                       # log

    assert stored["pipeline_completion_status"] == (
        PipelineCompletionStatus.SESSION_COMPLETE.value
    )


def test_the_status_json_never_contradicts_the_report(tmp_path, source):
    instance = _ready_to_report(tmp_path, source)
    assert instance.write_report() is True
    status = _status_json(instance)
    assert status["session_evidence_verdict"] in _report_text(instance)
    assert status["final_verdict"] == status["session_evidence_verdict"]
    assert status["report_publication_status"] == (
        ReportPublicationStatus.PUBLISHED.value
    )


def test_a_publication_failure_is_explicit_and_never_completes(tmp_path, source, monkeypatch):
    instance = _ready_to_report(tmp_path, source)

    import scripts.run_orb_shadow_orchestrator as module

    def _boom(payloads, **kwargs):
        raise ReportPublicationError("disk full")

    monkeypatch.setattr(module, "publish_atomically", _boom)
    assert instance.write_report() is False

    assert instance.state is OrchestratorState.SESSION_FAILED
    assert instance.state is not OrchestratorState.SESSION_COMPLETE
    assert instance.report_publication is ReportPublicationStatus.FAILED
    assert any(
        failure.failure_code == "REPORT_PUBLICATION_FAILED"
        for failure in instance.failures
    )
    assert instance.pipeline_completion_status() is (
        PipelineCompletionStatus.REPORT_PUBLICATION_FAILED
    )


def test_a_publication_failure_keeps_the_substantive_evidence(tmp_path, source, monkeypatch):
    """A reporting fault must not un-observe the session."""

    instance = _ready_to_report(tmp_path, source)

    import scripts.run_orb_shadow_orchestrator as module

    monkeypatch.setattr(
        module,
        "publish_atomically",
        lambda payloads, **kwargs: (_ for _ in ()).throw(ReportPublicationError("io")),
    )
    assert instance.write_report() is False
    payload = instance._finish()

    assert payload["session_evidence_verdict"] == (
        OrchestratorVerdict.FULL_SHADOW_SESSION_OBSERVED.value
    )
    assert payload["pipeline_completion_status"] == (
        PipelineCompletionStatus.REPORT_PUBLICATION_FAILED.value
    )
    stored = instance.repository.find_orchestrator_runs(session_date=DAY)[0]
    assert stored["final_verdict"] == (
        OrchestratorVerdict.FULL_SHADOW_SESSION_OBSERVED.value
    )


def test_no_contradictory_report_is_published_on_failure(tmp_path, source, monkeypatch):
    instance = _ready_to_report(tmp_path, source)

    import scripts.run_orb_shadow_orchestrator as module

    monkeypatch.setattr(
        module,
        "publish_atomically",
        lambda payloads, **kwargs: (_ for _ in ()).throw(ReportPublicationError("io")),
    )
    assert instance.write_report() is False
    assert not (instance.report_dir / "FULL_SHADOW_SESSION_REPORT.md").exists()
    assert not (instance.report_dir / "orchestrator_status.json").exists()


def test_a_previous_valid_report_survives_a_publication_failure(tmp_path, source):
    instance = _ready_to_report(tmp_path, source)
    assert instance.write_report() is True
    good_report = _report_text(instance)
    good_status = _status_json(instance)

    # A second generation that cannot be written: one target sits under a
    # directory that does not exist, so staging it fails after the report's
    # replacement has already been staged successfully.
    report = instance.report_dir / "FULL_SHADOW_SESSION_REPORT.md"
    status = instance.report_dir / "orchestrator_status.json"
    unwritable = instance.report_dir / "absent" / "orchestrator_status.json"
    with pytest.raises(OSError):
        publish_atomically({report: "REPLACED", unwritable: "{}"})

    assert report.read_text(encoding="utf-8") == good_report
    assert json.loads(status.read_text(encoding="utf-8")) == good_status


def test_partial_publication_is_impossible(tmp_path):
    first = tmp_path / "one.md"
    second = tmp_path / "two.json"
    first.write_text("ORIGINAL ONE", encoding="utf-8")
    second.write_text("ORIGINAL TWO", encoding="utf-8")

    unwritable = tmp_path / "absent_directory" / "three.md"
    with pytest.raises(OSError):
        publish_atomically(
            {first: "NEW ONE", second: "NEW TWO", unwritable: "NEW THREE"}
        )

    # Neither of the two writable targets moved: publication is all-or-nothing.
    assert first.read_text(encoding="utf-8") == "ORIGINAL ONE"
    assert second.read_text(encoding="utf-8") == "ORIGINAL TWO"


def test_publication_leaves_no_temporary_files_behind(tmp_path):
    target = tmp_path / "report.md"
    publish_atomically({target: "published"})
    assert target.read_text(encoding="utf-8") == "published"
    assert [p.name for p in tmp_path.iterdir()] == ["report.md"]


def test_a_failed_publication_leaves_no_temporary_files_behind(tmp_path):
    good = tmp_path / "report.md"
    good.write_text("original", encoding="utf-8")
    blocked = tmp_path / "absent" / "status.json"
    with pytest.raises(OSError):
        publish_atomically({good: "new", blocked: "new"})
    assert [p.name for p in tmp_path.iterdir()] == ["report.md"]
    assert good.read_text(encoding="utf-8") == "original"


def test_the_report_states_publication_separately_from_the_verdict(tmp_path, source):
    instance = _ready_to_report(tmp_path, source)
    assert instance.write_report() is True
    text = _report_text(instance)
    assert "## Session evidence verdict" in text
    assert "## Report publication" in text
    assert ReportPublicationStatus.PUBLISHED.value in text


def test_an_unmet_criterion_still_produces_a_partial_report(tmp_path, source):
    criteria = replace(ALL_CRITERIA_MET, opening_range_observed_live=False)
    instance = _ready_to_report(tmp_path, source, criteria=criteria)
    assert instance.write_report() is True
    text = _report_text(instance)
    assert "PARTIAL_SHADOW_SESSION" in text
    assert "opening_range_observed_live" in text


# =========================================================================== #
# ARTIFACT OWNERSHIP
# =========================================================================== #


def _run_session(tmp_path, source, out, *extra):
    args = session_args([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(out),
        "--session-date", DAY.isoformat(),
        *extra,
    ])
    return ShadowRunner(args).run()


def _seed_qualifying_follow_run(tmp_path, sibling_run_id):
    """A FOLLOW run sharing the identities of an already-executed run.

    `--reconstruct --compare-live-run-id` deliberately refuses ONCE, SMOKE and
    RECONSTRUCT sources, so a cross-run comparison needs a real FOLLOW row.
    """

    from scalping_orb.shadow_service import ShadowCycleMetrics

    repository = OrbResearchRepository(tmp_path / "orb.db")
    sibling = next(
        row
        for row in repository.find_shadow_runs(session_date=DAY)
        if row["run_id"] == sibling_run_id
    )
    run_id = "seededFollowRun"
    repository.start_shadow_run(
        run_id, DAY, mode="FOLLOW", started_at_utc=at(9, 45),
        runner_started_before_open=True,
        source_path_identity=sibling["source_path_identity"],
        config_identity=sibling["config_identity"],
        strategy_fingerprint=sibling["strategy_fingerprint"],
        engine_version=sibling["engine_version"], active_universe_only=True,
    )
    cycle = ShadowCycleMetrics(
        cycle_id="seed-c0", cycle_index=0, started_at_utc=at(11, 0),
        finished_at_utc=at(11, 1), cursor_low_source_id=0, cursor_high_source_id=1,
        source_rows_read=1, normalized_events=1, session_loads=1,
        symbols_in_snapshot=1, symbols_evaluated=1, snapshot_identity="s",
        live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, duration_seconds=0.1,
    )
    record = ShadowStateRecord(
        session_date=DAY, canonical_ticker="AAA", opening_range_revision=0,
        opening_range_version_identity="or-v1", final_state="WAIT_BREAKOUT",
        terminal=False, rejection_reasons=(), evidence_fingerprint="e",
        candidate_identity="c", live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,
        observed_at_utc=at(11, 0), exchange_watermark_utc=at(11, 0),
        observed_receive_lag_seconds=1.0,
    )
    repository.persist_shadow_cycle(run_id, cycle, (record,))
    repository.finish_shadow_run(
        run_id, finished_at_utc=at(14, 20), stop_reason="CONTINUOUS_END_REACHED",
        session_classification="FULL_SHADOW_SESSION",
    )
    return run_id


def test_lane_is_derived_from_the_run_mode():
    assert lane_for_mode("FOLLOW") == LANE_A
    assert lane_for_mode("ONCE") == LANE_A
    assert lane_for_mode("RECONSTRUCT") == LANE_B


def test_follow_and_reconstruct_write_separate_artifacts(tmp_path, source):
    out = tmp_path / "out"
    live = _run_session(tmp_path, source, out, "--once")
    replay = _run_session(tmp_path, source, out, "--reconstruct")

    live_summary = out / lane_artifact_name(
        live["run_id"], LANE_A, "shadow_run_summary", "json"
    )
    replay_summary = out / lane_artifact_name(
        replay["run_id"], LANE_B, "shadow_run_summary", "json"
    )
    assert live_summary.is_file() and replay_summary.is_file()
    assert live_summary != replay_summary
    assert json.loads(live_summary.read_text(encoding="utf-8"))["mode"] == "ONCE"
    assert json.loads(replay_summary.read_text(encoding="utf-8"))["mode"] == "RECONSTRUCT"


def test_lane_b_cannot_overwrite_lane_a(tmp_path, source):
    """The exact 2026-08-05 overwrite, pinned."""

    out = tmp_path / "out"
    live = _run_session(tmp_path, source, out, "--once")
    live_summary = out / lane_artifact_name(
        live["run_id"], LANE_A, "shadow_run_summary", "json"
    )
    live_quality = out / lane_artifact_name(
        live["run_id"], LANE_A, "shadow_session_quality", "csv"
    )
    before = (
        live_summary.read_bytes(),
        live_quality.read_bytes(),
    )

    _run_session(tmp_path, source, out, "--reconstruct")

    assert (live_summary.read_bytes(), live_quality.read_bytes()) == before
    assert json.loads(live_summary.read_text(encoding="utf-8"))["mode"] == "ONCE"


def test_no_run_writes_the_ambiguous_shared_filename(tmp_path, source):
    out = tmp_path / "out"
    live = _run_session(tmp_path, source, out, "--once")
    _run_session(tmp_path, source, out, "--reconstruct")
    for legacy in (
        "shadow_run_summary.json",
        "shadow_session_quality.csv",
        "shadow_live_states.csv",
        "shadow_reconstruction_states.csv",
        "shadow_live_vs_reconstruction.csv",
        "shadow_cycle_metrics.csv",
    ):
        assert not (out / legacy).exists(), legacy


def test_readers_select_the_correct_lane_and_run(tmp_path, source):
    out = tmp_path / "out"
    live = _run_session(tmp_path, source, out, "--once")
    replay = _run_session(tmp_path, source, out, "--reconstruct")

    assert read_run_summary(out, lane=LANE_A, run_id=live["run_id"])["mode"] == "ONCE"
    assert (
        read_run_summary(out, lane=LANE_B, run_id=replay["run_id"])["mode"]
        == "RECONSTRUCT"
    )
    # And by lane alone, via the explicitly-named session aliases.
    assert read_run_summary(out, lane=LANE_A)["mode"] == "ONCE"
    assert read_run_summary(out, lane=LANE_B)["mode"] == "RECONSTRUCT"


def test_the_session_aliases_name_the_lane_they_mirror(tmp_path, source):
    out = tmp_path / "out"
    live = _run_session(tmp_path, source, out, "--once")
    _run_session(tmp_path, source, out, "--reconstruct")
    assert (out / SESSION_SUMMARY_ALIAS[LANE_A]).name == "session_live_summary.json"
    assert (
        out / SESSION_SUMMARY_ALIAS[LANE_B]
    ).name == "session_reconstruction_summary.json"
    assert json.loads(
        (out / SESSION_SUMMARY_ALIAS[LANE_A]).read_text(encoding="utf-8")
    )["mode"] == "ONCE"


def test_an_ambiguous_legacy_artifact_is_read_with_a_warning(tmp_path):
    out = tmp_path / "legacy"
    out.mkdir()
    (out / "shadow_run_summary.json").write_text(
        json.dumps({"mode": "RECONSTRUCT", "session_classification": "PARTIAL"}),
        encoding="utf-8",
    )
    with pytest.warns(RuntimeWarning, match="pre-migration shared artifact"):
        payload = read_run_summary(out, lane=LANE_A)
    assert payload["artifact_ownership"] == "AMBIGUOUS_LEGACY_SHARED_ARTIFACT"


def test_a_missing_summary_raises_rather_than_guessing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        read_run_summary(empty, lane=LANE_A)


def test_run_metadata_records_the_exact_artifact_paths(tmp_path, source):
    out = tmp_path / "out"
    live = _run_session(tmp_path, source, out, "--once")
    repository = OrbResearchRepository(tmp_path / "orb.db")
    row = next(
        r
        for r in repository.find_shadow_runs(session_date=DAY)
        if r["run_id"] == live["run_id"]
    )
    assert row["artifact_lane"] == LANE_A
    paths = json.loads(row["artifact_paths_json"])
    assert paths["shadow_run_summary"] == lane_artifact_name(
        live["run_id"], LANE_A, "shadow_run_summary", "json"
    )
    for name in paths.values():
        assert (out / name).is_file()
    assert live["artifact_paths"] == paths


def test_the_report_links_both_lane_artifacts(tmp_path, source):
    instance = _ready_to_report(tmp_path, source)
    assert instance.write_report() is True
    text = _report_text(instance)
    assert "## Per-run artifacts" in text
    assert lane_artifact_name(
        instance.live_run_id, LANE_A, "shadow_run_summary", "json"
    ) in text
    assert lane_artifact_name(
        instance.reconstruction_run_id, LANE_B, "shadow_run_summary", "json"
    ) in text


# =========================================================================== #
# RECONSTRUCTION TIMESTAMPS
# =========================================================================== #


class _Transition:
    def __init__(self, exchange_timestamp_utc):
        self.exchange_timestamp_utc = exchange_timestamp_utc


class _Assessment:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class _Evaluation:
    def __init__(self, *, final_state, transitions=(), breakout=None, pullback=None, reclaim=None):
        self.final_state = final_state
        self.transitions = tuple(transitions)
        self.breakout = breakout
        self.pullback = pullback
        self.reclaim = reclaim


def test_the_historical_state_time_comes_from_the_last_exchange_transition():
    early, late = at(10, 30), at(11, 45)
    times = reconstructed_times(
        _Evaluation(
            final_state="WAIT_BREAKOUT",
            transitions=(_Transition(early), _Transition(late)),
        )
    )
    assert times["reconstructed_state_time_utc"] == late
    assert times["reconstructed_time_status"] == (
        ReconstructedTimeStatus.HISTORICAL_TIME_RECOVERED.value
    )


def test_every_assessment_timestamp_is_a_bar_boundary():
    breakout_at, pullback_at, reclaim_at = at(10, 35), at(10, 50), at(11, 5)
    times = reconstructed_times(
        _Evaluation(
            final_state="ENTRY_READY_RESEARCH",
            transitions=(_Transition(reclaim_at),),
            breakout=_Assessment(bar_end_utc=breakout_at),
            pullback=_Assessment(low_bar_start_utc=pullback_at),
            reclaim=_Assessment(confirmation_bar_end_utc=reclaim_at),
        )
    )
    assert times["reconstructed_breakout_time_utc"] == breakout_at
    assert times["reconstructed_pullback_time_utc"] == pullback_at
    assert times["reconstructed_reclaim_time_utc"] == reclaim_at
    assert times["reconstructed_entry_ready_time_utc"] == reclaim_at


def test_entry_ready_time_is_only_set_when_the_state_is_entry_ready():
    times = reconstructed_times(
        _Evaluation(
            final_state="PULLBACK_TOO_DEEP",
            transitions=(_Transition(at(11, 0)),),
            reclaim=_Assessment(confirmation_bar_end_utc=at(11, 0)),
        )
    )
    assert times["reconstructed_entry_ready_time_utc"] is None


def test_an_unavailable_historical_time_stays_null_with_a_typed_reason():
    times = reconstructed_times(
        _Evaluation(final_state="DATA_UNAVAILABLE", transitions=(_Transition(None),))
    )
    assert times["reconstructed_state_time_utc"] is None
    assert times["reconstructed_time_status"] == (
        ReconstructedTimeStatus.NO_EXCHANGE_TIMESTAMPED_TRANSITION.value
    )

    none_at_all = reconstructed_times(
        _Evaluation(final_state="DATA_UNAVAILABLE", transitions=())
    )
    assert none_at_all["reconstructed_state_time_utc"] is None
    assert none_at_all["reconstructed_time_status"] == (
        ReconstructedTimeStatus.STATE_HAS_NO_HISTORICAL_INSTANT.value
    )


def test_no_reconstructed_time_is_ever_the_current_clock():
    before = datetime.now(timezone.utc)
    times = reconstructed_times(
        _Evaluation(final_state="DATA_UNAVAILABLE", transitions=())
    )
    after = datetime.now(timezone.utc)
    for key, value in times.items():
        if key.endswith("_utc"):
            assert value is None or not (before <= value <= after), key


def _persist_reconstruction(repository, run_id, record):
    repository.start_shadow_run(
        run_id, DAY, mode="RECONSTRUCT", started_at_utc=at(14, 20),
        runner_started_before_open=False, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
        active_universe_only=True,
    )
    repository.persist_shadow_reconstruction(run_id, (record,))
    return repository.load_shadow_reconstruction_states(run_id)[0]


def test_the_decision_time_is_persisted_and_is_not_the_insert_time(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    historical = at(11, 35)
    record = ShadowReconstructionRecord(
        session_date=DAY, canonical_ticker="AAA", opening_range_revision=0,
        opening_range_version_identity="or-v1",
        final_state="ENTRY_READY_RESEARCH", terminal=True, rejection_reasons=(),
        evidence_fingerprint="e", candidate_identity="c",
        reconstructed_state_time_utc=historical,
        reconstructed_entry_ready_time_utc=historical,
        reconstructed_time_status=(
            ReconstructedTimeStatus.HISTORICAL_TIME_RECOVERED.value
        ),
    )
    row = _persist_reconstruction(repository, "reconRun", record)

    assert row["reconstructed_state_time_utc"] == historical.isoformat()
    assert row["reconstructed_entry_ready_time_utc"] == historical.isoformat()
    assert row["reconstructed_time_status"] == (
        ReconstructedTimeStatus.HISTORICAL_TIME_RECOVERED.value
    )

    with repository.connect() as connection:
        written = connection.execute(
            "SELECT recorded_at_utc FROM orb_shadow_reconstruction_states"
        ).fetchone()[0]
    assert written != row["reconstructed_state_time_utc"], (
        "the write time must never stand in for the historical decision time"
    )


def test_an_unrecoverable_time_is_stored_as_null_not_as_a_clock(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    record = ShadowReconstructionRecord(
        session_date=DAY, canonical_ticker="BBB", opening_range_revision=0,
        opening_range_version_identity="or-v1",
        final_state="DATA_UNAVAILABLE", terminal=True, rejection_reasons=(),
        evidence_fingerprint="e", candidate_identity="c",
        reconstructed_time_status=(
            ReconstructedTimeStatus.STATE_HAS_NO_HISTORICAL_INSTANT.value
        ),
    )
    row = _persist_reconstruction(repository, "reconRun", record)
    assert row["reconstructed_state_time_utc"] is None
    assert row["reconstructed_time_status"] == (
        ReconstructedTimeStatus.STATE_HAS_NO_HISTORICAL_INSTANT.value
    )


def test_a_live_reconstruction_persists_real_bar_times(tmp_path, source):
    out = tmp_path / "out"
    live = _run_session(tmp_path, source, out, "--once")
    replay = _run_session(tmp_path, source, out, "--reconstruct")
    repository = OrbResearchRepository(tmp_path / "orb.db")
    rows = repository.load_shadow_reconstruction_states(replay["run_id"])
    assert rows
    for row in rows:
        assert row["reconstructed_time_status"] in {
            status.value for status in ReconstructedTimeStatus
        }
        if row["reconstructed_state_time_utc"] is not None:
            moment = datetime.fromisoformat(row["reconstructed_state_time_utc"])
            # A market instant on the session date, never the replay's own run time.
            assert moment.astimezone(timezone.utc).date() == DAY


# =========================================================================== #
# MIGRATION
# =========================================================================== #


def test_migration_eight_is_the_head_and_is_additive(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    assert repository.database_status()["user_version"] == SCHEMA_VERSION >= 8


def test_migration_eight_preserves_rows_written_at_version_seven(tmp_path):
    path = tmp_path / "orb.db"
    old = OrbResearchRepository(path, target_schema_version=7)
    assert old.database_status()["user_version"] == 7
    old.start_shadow_run(
        "legacyRun", DAY, mode="RECONSTRUCT", started_at_utc=at(14, 20),
        runner_started_before_open=False, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
        active_universe_only=True,
    )
    with old.transaction() as connection:
        connection.execute(
            """INSERT INTO orb_shadow_reconstruction_states VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("legacyRow", "legacyRun", DAY.isoformat(), "AAA", 0, "or-v1",
             "WAIT_BREAKOUT", 1, "[]", "e", "c", "HISTORICAL_REPLAY", 1,
             at(14, 21).isoformat()),
        )

    upgraded = OrbResearchRepository(path)
    assert upgraded.database_status()["user_version"] == SCHEMA_VERSION
    rows = upgraded.load_shadow_reconstruction_states("legacyRun")
    assert len(rows) == 1
    assert rows[0]["canonical_ticker"] == "AAA"
    assert rows[0]["final_state"] == "WAIT_BREAKOUT"
    # Readable, and honest that it carries no historical timestamp.
    assert rows[0]["reconstructed_state_time_utc"] is None
    assert rows[0]["reconstructed_time_status"] == (
        ReconstructedTimeStatus.NOT_RECORDED_PRE_MIGRATION.value
    )


# =========================================================================== #
# COMPARISON TIMING
# =========================================================================== #


def test_matching_states_with_both_times_produce_a_delta():
    delta, status = timing_comparison(at(11, 5), at(11, 0), states_match=True)
    assert delta == pytest.approx(300.0)
    assert status == TimingComparisonStatus.TIMING_COMPARABLE.value


def test_a_missing_lane_b_time_is_typed_unavailable():
    delta, status = timing_comparison(at(11, 5), None, states_match=True)
    assert delta is None
    assert status == TimingComparisonStatus.LANE_B_TIME_UNAVAILABLE.value


def test_a_missing_lane_a_time_is_typed_unavailable():
    delta, status = timing_comparison(None, at(11, 0), states_match=True)
    assert delta is None
    assert status == TimingComparisonStatus.LANE_A_TIME_UNAVAILABLE.value


def test_two_missing_times_are_typed_unavailable():
    delta, status = timing_comparison(None, None, states_match=True)
    assert delta is None
    assert status == TimingComparisonStatus.BOTH_TIMES_UNAVAILABLE.value


def test_mismatched_states_are_never_timed():
    """Subtracting the instant of one state from another is not a latency."""

    delta, status = timing_comparison(at(11, 5), at(11, 0), states_match=False)
    assert delta is None
    assert status == TimingComparisonStatus.STATE_NOT_COMPARABLE.value


def test_the_comparison_carries_timing_through_to_the_database(tmp_path, source):
    out = tmp_path / "out"
    seedling = _run_session(tmp_path, source, out, "--once")
    live_run_id = _seed_qualifying_follow_run(tmp_path, seedling["run_id"])
    replay = _run_session(
        tmp_path, source, out, "--reconstruct", "--compare-live-run-id", live_run_id
    )
    repository = OrbResearchRepository(tmp_path / "orb.db")
    rows = repository.load_cross_run_comparison(live_run_id, replay["run_id"])
    assert rows
    valid = {status.value for status in TimingComparisonStatus}
    for row in rows:
        assert row["timing_comparison_status"] in valid
        if row["timing_comparison_status"] != (
            TimingComparisonStatus.TIMING_COMPARABLE.value
        ):
            assert row["detection_delta_seconds"] is None
        else:
            assert row["detection_delta_seconds"] is not None


def test_a_delta_is_never_derived_from_the_reconstruction_write_time(tmp_path):
    """The mutation this test exists to catch.

    If `lane_b_detection_time_utc` were sourced from `recorded_at_utc`, every
    comparable symbol would show a delta of roughly minus-the-audit-lag, and it
    would be identical for every symbol in the run.
    """

    from scalping_orb.shadow_service import OrbShadowService

    live_at = at(11, 0)
    historical = at(10, 35)
    live = ShadowStateRecord(
        session_date=DAY, canonical_ticker="AAA", opening_range_revision=0,
        opening_range_version_identity="or-v1", final_state="WAIT_BREAKOUT",
        terminal=False, rejection_reasons=(), evidence_fingerprint="e",
        candidate_identity="c", live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,
        observed_at_utc=live_at, exchange_watermark_utc=live_at,
        observed_receive_lag_seconds=1.0,
    )
    replay = ShadowReconstructionRecord(
        session_date=DAY, canonical_ticker="AAA", opening_range_revision=0,
        opening_range_version_identity="or-v1", final_state="WAIT_BREAKOUT",
        terminal=False, rejection_reasons=(), evidence_fingerprint="e",
        candidate_identity="c",
        reconstructed_state_time_utc=historical,
        reconstructed_time_status=(
            ReconstructedTimeStatus.HISTORICAL_TIME_RECOVERED.value
        ),
    )
    rows = OrbShadowService().compare(DAY, (live,), (replay,))
    assert len(rows) == 1
    row = rows[0]
    assert row.lane_a_detection_time_utc == live_at
    assert row.lane_b_detection_time_utc == historical
    assert row.detection_delta_seconds == pytest.approx(1500.0)
    assert row.timing_comparison_status == (
        TimingComparisonStatus.TIMING_COMPARABLE.value
    )


def test_a_reconstruction_without_a_time_is_reported_not_estimated():
    from scalping_orb.shadow_service import OrbShadowService

    live = ShadowStateRecord(
        session_date=DAY, canonical_ticker="AAA", opening_range_revision=0,
        opening_range_version_identity="or-v1", final_state="DATA_UNAVAILABLE",
        terminal=True, rejection_reasons=(), evidence_fingerprint="e",
        candidate_identity="c", live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,
        observed_at_utc=at(11, 0), exchange_watermark_utc=at(11, 0),
        observed_receive_lag_seconds=1.0,
    )
    replay = ShadowReconstructionRecord(
        session_date=DAY, canonical_ticker="AAA", opening_range_revision=0,
        opening_range_version_identity="or-v1", final_state="DATA_UNAVAILABLE",
        terminal=True, rejection_reasons=(), evidence_fingerprint="e",
        candidate_identity="c",
        reconstructed_time_status=(
            ReconstructedTimeStatus.STATE_HAS_NO_HISTORICAL_INSTANT.value
        ),
    )
    row = OrbShadowService().compare(DAY, (live,), (replay,))[0]
    assert row.detection_delta_seconds is None
    assert row.timing_comparison_status == (
        TimingComparisonStatus.LANE_B_TIME_UNAVAILABLE.value
    )


# =========================================================================== #
# REGRESSIONS
# =========================================================================== #


def test_the_2026_08_05_full_shape_remains_full():
    """Every observation criterion met, as Lane A `bfe9159e` actually was."""

    assert ALL_CRITERIA_MET.verdict() is (
        OrchestratorVerdict.FULL_SHADOW_SESSION_OBSERVED
    )
    assert ALL_CRITERIA_MET.failures() == ()


@pytest.mark.parametrize(
    "stalled",
    [
        "normalization_progressed_to_continuous_end",
        "evaluation_progressed_to_continuous_end",
        "no_critical_evaluation_stall",
    ],
)
def test_the_2026_08_04_stall_shape_remains_partial(stalled):
    """A liveness failure must still be PARTIAL after the reporting change."""

    criteria = replace(ALL_CRITERIA_MET, **{stalled: False})
    assert criteria.verdict() is OrchestratorVerdict.PARTIAL_SHADOW_SESSION
    assert stalled in criteria.failures()


def test_a_failed_live_lane_is_still_failed_not_merely_partial():
    assert ALL_CRITERIA_MET.verdict(live_failed=True) is (
        OrchestratorVerdict.FAILED_SHADOW_SESSION
    )


def test_once_and_smoke_runs_remain_ineligible_as_a_live_source():
    from scalping_orb.shadow_service import live_source_disqualification

    once = {
        "mode": "ONCE", "session_classification": "FULL_SHADOW_SESSION",
        "runner_started_before_open": 1, "lane_a_rows": 5,
    }
    smoke = {
        "mode": "FOLLOW", "session_classification": "PARTIAL_SMOKE_SESSION",
        "runner_started_before_open": 1, "lane_a_rows": 5,
    }
    reconstruct = {
        "mode": "RECONSTRUCT", "session_classification": "FULL_SHADOW_SESSION",
        "runner_started_before_open": 1, "lane_a_rows": 5,
    }
    assert live_source_disqualification(once) is not None
    assert live_source_disqualification(smoke) is not None
    assert live_source_disqualification(reconstruct) is not None


def test_a_reconstruct_run_still_writes_zero_lane_a_rows(tmp_path, source):
    out = tmp_path / "out"
    live = _run_session(tmp_path, source, out, "--once")
    repository = OrbResearchRepository(tmp_path / "orb.db")
    before = repository.table_count("orb_shadow_live_states")
    replay = _run_session(tmp_path, source, out, "--reconstruct")
    assert repository.table_count("orb_shadow_live_states") == before
    assert repository.load_shadow_live_states(replay["run_id"]) == ()


def test_the_report_never_becomes_an_execution_instruction(tmp_path, source):
    instance = _ready_to_report(tmp_path, source)
    assert instance.write_report() is True
    text = _report_text(instance)
    assert "NOT A BUY SIGNAL" in text
    assert "NOT LIVE EXECUTION" in text
    assert "Production execution disabled" in text
    for instruction in ("place order", "target quantity", "## Order", "Recommendation:"):
        assert instruction not in text
    status = _status_json(instance)
    assert status["research_only"] is True
    assert status["production_disabled"] is True
