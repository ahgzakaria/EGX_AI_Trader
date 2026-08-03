"""ORB full-shadow run controls — active-universe filter and cross-run comparison.

Research only. No test starts Rubix, opens a websocket, authenticates, reaches
the network, or reads the production database. Sources are temporary synthetic
SQLite files shaped like the real `quotes` table.

The two gaps under test were both "declared but not real": a CLI flag that
changed nothing, and a reconstruction run that could not actually compare
against the live run it was meant to be measured against.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from scalping_orb.config import OrbDataConfig
from scalping_orb.events import (
    CumulativeVolumeTracker,
    NormalizationMode,
    RubixEventNormalizer,
    UniverseMembershipStatus,
)
from scalping_orb.repository import SCHEMA_VERSION, OrbResearchRepository
from scalping_orb.shadow_service import (
    ComparisonReason,
    OrbShadowService,
    ShadowLiveStatus,
    ShadowRunSelectionError,
    classify_difference,
    live_records_from_rows,
    select_live_run,
)
from scalping_orb.shadow_snapshot import ShadowSnapshotBuilder
from scalping_orb.shadow_source import SOURCE_TABLE, ShadowCursor, ShadowSourceReader
from scalping_orb.strategy_config import OrbStrategyConfig
from scripts.run_orb_shadow_session import ShadowRunner, list_runs, parse_args


CAIRO = ZoneInfo("Africa/Cairo")
DAY = date(2026, 8, 4)
CONFIG = OrbDataConfig()

SOURCE_SCHEMA = """
CREATE TABLE quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL, last_price REAL, bid REAL, ask REAL, volume REAL,
    market_timestamp TEXT NOT NULL, received_at TEXT NOT NULL,
    exchange TEXT, sequence INTEGER, change_percent REAL,
    has_feed_timestamp INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_quotes_ticker_time ON quotes(ticker, market_timestamp);
"""


def at(hour, minute, second=0):
    return datetime.combine(DAY, time(hour, minute, second), tzinfo=CAIRO).astimezone(
        timezone.utc
    )


def synthetic_source(path, tickers=("AAA", "BBB"), minutes=40):
    rows = []
    for index in range(minutes):
        moment = at(10, 0) + timedelta(minutes=index)
        for offset, ticker in enumerate(tickers):
            price = 10.0 + 0.01 * index + 0.1 * offset
            rows.append(
                (
                    ticker, price, price - 0.01, price + 0.01,
                    1000.0 * (index + 1), moment.isoformat(),
                    (moment + timedelta(seconds=1)).isoformat(), "CASE", 0.0,
                )
            )
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SOURCE_SCHEMA)
        connection.executemany(
            """INSERT INTO quotes (ticker,last_price,bid,ask,volume,market_timestamp,
               received_at,exchange,sequence,change_percent,has_feed_timestamp)
               VALUES (?,?,?,?,?,?,?,?,NULL,?,1)""",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return Path(path)


@pytest.fixture
def source(tmp_path):
    return synthetic_source(tmp_path / "rubix_synthetic.db")


def normalize(quotes, membership=None):
    """Normalize with an optional per-ticker membership resolver."""

    normalizer = RubixEventNormalizer(
        CONFIG,
        holidays=(),
        mapping_validator=lambda a, b: True,
        membership_resolver=membership,
        mode=NormalizationMode.HISTORICAL_REPLAY,
    )
    batch = normalizer.normalize_many(quotes, evaluated_at=at(14, 15))
    return CumulativeVolumeTracker(CONFIG).apply_many(batch.events), batch.quality_events


def read_all(source_path):
    return ShadowSourceReader(source_path, CONFIG).read_batch(
        ShadowCursor(), session_date=DAY
    )


def build(events, *, active_universe_only=False, quality=(), rows=0):
    return ShadowSnapshotBuilder(
        CONFIG, active_universe_only=active_universe_only
    ).build(
        DAY, events, as_of=at(14, 15), cursor_low_source_id=0,
        cursor_high_source_id=rows, quality_events=quality, source_rows_observed=rows,
    )


#: Resolver that marks BBB archived and CCC active-but-unmapped.
def mixed_membership(canonical):
    if canonical == "BBB":
        return UniverseMembershipStatus.ARCHIVED_INACTIVE_SYMBOL
    if canonical == "CCC":
        return UniverseMembershipStatus.ACTIVE_UNIVERSE_UNVERIFIED_RUBIX
    return UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX


# =========================================================================== #
# ACTIVE UNIVERSE FLAG
# =========================================================================== #


def test_the_flag_evaluates_active_and_mapped_symbols_only(tmp_path):
    path = synthetic_source(tmp_path / "mix.db", tickers=("AAA", "BBB", "CCC"))
    events, _ = normalize(read_all(path).quotes, membership=mixed_membership)
    filtered = build(events, active_universe_only=True)
    assert filtered.research_eligible_tickers == ("AAA",)
    assert filtered.evaluation_tickers == ("AAA",)
    assert set(filtered.tickers) == {"AAA", "BBB", "CCC"}


def test_archived_symbols_are_not_evaluated_for_new_entry_research(tmp_path):
    path = synthetic_source(tmp_path / "arch.db", tickers=("AAA", "BBB"))
    events, _ = normalize(read_all(path).quotes, membership=mixed_membership)
    snapshot = build(events, active_universe_only=True)
    records = OrbShadowService(
        OrbStrategyConfig(), active_universe_only=True
    ).evaluate_reconstruction(snapshot)
    assert {r.canonical_ticker for r in records} == {"AAA"}


def test_active_but_unmapped_symbols_are_not_evaluated(tmp_path):
    path = synthetic_source(tmp_path / "unmapped.db", tickers=("AAA", "CCC"))
    events, _ = normalize(read_all(path).quotes, membership=mixed_membership)
    snapshot = build(events, active_universe_only=True)
    assert "CCC" not in snapshot.evaluation_tickers
    assert "CCC" in snapshot.tickers, "still observed, just not evaluated"


def test_archived_rows_still_count_in_source_observation_metrics(tmp_path):
    """Filtering evaluation must never look like filtering observation."""

    path = synthetic_source(tmp_path / "counts.db", tickers=("AAA", "BBB", "CCC"))
    batch = read_all(path)
    events, quality = normalize(batch.quotes, membership=mixed_membership)

    broad = build(events, quality=quality, rows=batch.rows_read)
    filtered = build(events, active_universe_only=True, quality=quality, rows=batch.rows_read)

    for snapshot in (broad, filtered):
        assert snapshot.quality_summary.source_rows_observed == batch.rows_read
        assert snapshot.quality_summary.normalized_events == len(events)
        assert snapshot.quality_summary.normalized_symbols_observed == 3
        assert snapshot.quality_summary.completed_one_minute_bars > 0
    # Identical observation, different evaluation scope.
    assert (
        broad.quality_summary.completed_one_minute_bars
        == filtered.quality_summary.completed_one_minute_bars
    )
    assert filtered.quality_summary.operationally_eligible_symbols == 1
    assert filtered.quality_summary.symbols_withheld_by_universe_filter >= 1
    assert broad.quality_summary.symbols_withheld_by_universe_filter == 0


def test_the_four_counts_are_distinguished(tmp_path):
    path = synthetic_source(tmp_path / "four.db", tickers=("AAA", "BBB", "CCC"))
    batch = read_all(path)
    events, _ = normalize(batch.quotes, membership=mixed_membership)
    snapshot = build(events, active_universe_only=True, rows=batch.rows_read)
    summary = snapshot.quality_summary
    assert summary.source_rows_observed == batch.rows_read          # source rows
    assert summary.normalized_symbols_observed == 3                  # symbols seen
    assert summary.operationally_eligible_symbols == 1               # eligible
    assert len(snapshot.evaluation_tickers) == 1                     # evaluated


def test_the_disabled_flag_preserves_broad_research_observation(tmp_path):
    path = synthetic_source(tmp_path / "broad.db", tickers=("AAA", "BBB", "CCC"))
    events, _ = normalize(read_all(path).quotes, membership=mixed_membership)
    snapshot = build(events, active_universe_only=False)
    assert set(snapshot.evaluation_tickers) == {"AAA", "BBB", "CCC"}
    records = OrbShadowService(OrbStrategyConfig()).evaluate_reconstruction(snapshot)
    assert {r.canonical_ticker for r in records} == {"AAA", "BBB", "CCC"}


def test_the_engine_still_rejects_ineligible_candidates_when_the_flag_is_off(tmp_path):
    """Broad mode records *why* a symbol is ineligible instead of skipping it."""

    path = synthetic_source(tmp_path / "reasons.db", tickers=("AAA", "BBB"))
    events, _ = normalize(read_all(path).quotes, membership=mixed_membership)
    snapshot = build(events, active_universe_only=False)
    records = OrbShadowService(OrbStrategyConfig()).evaluate_reconstruction(snapshot)
    archived = next(r for r in records if r.canonical_ticker == "BBB")
    assert "ARCHIVED_SYMBOL_INELIGIBLE" in archived.rejection_reasons
    assert archived.final_state == "FAILED"


def test_cursor_progression_is_identical_with_the_flag_on_or_off(tmp_path):
    """The filter must not touch source progress in any way."""

    path = synthetic_source(tmp_path / "cursor.db", tickers=("AAA", "BBB", "CCC"))
    reader = ShadowSourceReader(path, CONFIG, batch_size=13)
    progressions = []
    for _ in range(2):
        cursor = ShadowCursor()
        seen = []
        while True:
            batch = reader.read_batch(cursor, session_date=DAY)
            if batch.rows_read == 0:
                break
            seen.append(batch.cursor_after.last_source_id)
            cursor = batch.cursor_after
        progressions.append(seen)
    assert progressions[0] == progressions[1]


def test_the_flag_state_is_persisted_and_reported(tmp_path, source):
    args = parse_args([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out"),
        "--session-date", DAY.isoformat(), "--once", "--smoke",
        "--active-universe-only",
    ])
    summary = ShadowRunner(args).run()
    assert summary["active_universe_only"] is True
    repository = OrbResearchRepository(tmp_path / "orb.db")
    row = repository.find_shadow_runs(session_date=DAY)[0]
    assert row["active_universe_only"] == 1
    text = (tmp_path / "out" / "shadow_run_summary.json").read_text(encoding="utf-8")
    assert '"active_universe_only": true' in text


def test_the_flag_state_appears_in_the_banner(tmp_path, source, capsys):
    import scripts.run_orb_shadow_session as runner_module

    runner_module.main([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out"),
        "--session-date", DAY.isoformat(), "--once", "--smoke",
        "--active-universe-only",
    ])
    out = capsys.readouterr().out
    assert "ACTIVE_UNIVERSE_ONLY" in out
    assert "RESEARCH ONLY" in out
    assert "PRODUCTION EXECUTION DISABLED" in out


# =========================================================================== #
# CROSS-RUN COMPARISON
# =========================================================================== #


def runner_identities(source_path):
    """The exact source and config identities the runner itself will compute.

    A live run only qualifies as a comparison source if these match, so a test
    fixture must derive them the same way rather than inventing labels.
    """

    from scripts.run_orb_shadow_session import _path_identity

    return _path_identity(Path(source_path)), CONFIG.fingerprint


def _live_run(repository, run_id, *, session="2026-08-04", mode="FOLLOW",
              source_id="sid", config_id="cid", lane_a=1):
    repository.start_shadow_run(
        run_id, date.fromisoformat(session), mode=mode, started_at_utc=at(10, 0),
        runner_started_before_open=True, source_path_identity=source_id,
        config_identity=config_id, strategy_fingerprint="sf", engine_version="ev",
    )
    if lane_a:
        from scalping_orb.shadow_service import ShadowCycleMetrics, ShadowStateRecord

        cycle = ShadowCycleMetrics(
            cycle_id=f"{run_id}-c0", cycle_index=0, started_at_utc=at(11, 0),
            finished_at_utc=at(11, 1), cursor_low_source_id=0, cursor_high_source_id=1,
            source_rows_read=1, normalized_events=1, session_loads=1,
            symbols_in_snapshot=1, symbols_evaluated=1, snapshot_identity="s",
            live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, duration_seconds=0.1,
        )
        record = ShadowStateRecord(
            session_date=date.fromisoformat(session), canonical_ticker="AAA",
            opening_range_revision=0, opening_range_version_identity="or-v1",
            final_state="BREAKOUT_REJECTED_STALE", terminal=True,
            rejection_reasons=("LIVE_DECISION_DISABLED_FRESHNESS",),
            evidence_fingerprint="e", candidate_identity="c",
            live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,
            observed_at_utc=at(11, 0), exchange_watermark_utc=at(11, 0),
            observed_receive_lag_seconds=1.0,
        )
        repository.persist_shadow_cycle(run_id, cycle, (record,))
    return run_id


def test_zero_matching_prior_runs_fails_clearly(tmp_path, source):
    args = parse_args([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--session-date", DAY.isoformat(),
        "--reconstruct", "--compare-latest-live-run",
    ])
    with pytest.raises(ShadowRunSelectionError, match="no prior live Shadow run"):
        ShadowRunner(args)


def test_a_failed_selection_never_emits_a_fake_all_historical_comparison(tmp_path, source):
    """The whole point: silence beats a plausible-looking wrong report."""

    args = parse_args([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--session-date", DAY.isoformat(),
        "--reconstruct", "--compare-latest-live-run",
    ])
    with pytest.raises(ShadowRunSelectionError):
        ShadowRunner(args)
    repository = OrbResearchRepository(tmp_path / "orb.db")
    assert repository.table_count("orb_shadow_cross_run_comparison") == 0
    assert repository.table_count("orb_shadow_reconstruction_states") == 0


def test_ambiguous_prior_runs_require_an_explicit_run_id(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    _live_run(repository, "runA")
    _live_run(repository, "runB")
    candidates = repository.find_shadow_runs(session_date=DAY)
    with pytest.raises(ShadowRunSelectionError, match="2 eligible live runs"):
        select_live_run(
            candidates, session_date=DAY,
            source_path_identity="sid", config_identity="cid",
        )
    chosen = select_live_run(
        candidates, explicit_run_id="runB", session_date=DAY,
        source_path_identity="sid", config_identity="cid",
    )
    assert chosen["run_id"] == "runB"


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"session_date": date(2026, 8, 5)}, "no prior live Shadow run"),
        ({"source_path_identity": "other-source"}, "no prior live Shadow run"),
        ({"config_identity": "other-config"}, "no prior live Shadow run"),
    ],
)
def test_a_mismatched_run_is_rejected(tmp_path, kwargs, match):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    _live_run(repository, "runA")
    base = dict(
        session_date=DAY, source_path_identity="sid", config_identity="cid"
    )
    base.update(kwargs)
    with pytest.raises(ShadowRunSelectionError, match=match):
        select_live_run(repository.find_shadow_runs(), **base)


def test_a_reconstruction_run_is_never_offered_as_a_live_source(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    _live_run(repository, "reconA", mode="RECONSTRUCT")
    with pytest.raises(ShadowRunSelectionError, match="no prior live Shadow run"):
        select_live_run(
            repository.find_shadow_runs(), session_date=DAY,
            source_path_identity="sid", config_identity="cid",
        )


def test_a_live_run_without_lane_a_rows_is_not_eligible(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    _live_run(repository, "empty", lane_a=0)
    with pytest.raises(ShadowRunSelectionError):
        select_live_run(
            repository.find_shadow_runs(), session_date=DAY,
            source_path_identity="sid", config_identity="cid",
        )


def test_an_explicit_but_ineligible_run_id_is_refused(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    _live_run(repository, "reconA", mode="RECONSTRUCT")
    with pytest.raises(ShadowRunSelectionError, match="not an eligible live run"):
        select_live_run(
            repository.find_shadow_runs(), explicit_run_id="reconA",
            session_date=DAY, source_path_identity="sid", config_identity="cid",
        )


def test_stored_lane_a_rows_rehydrate_into_comparable_records(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    _live_run(repository, "runA")
    records = live_records_from_rows(repository.load_shadow_live_states_full("runA"))
    assert len(records) == 1
    assert records[0].canonical_ticker == "AAA"
    assert records[0].live_status is ShadowLiveStatus.LIVE_SHADOW_HEALTHY
    assert records[0].rejection_reasons == ("LIVE_DECISION_DISABLED_FRESHNESS",)


def _prepare_live_run(tmp_path, source, run_id="liveRun"):
    """A prior live run that genuinely qualifies as a comparison source.

    Built directly rather than by running `--once`: a `--once` run against a
    *past* synthetic session produces no Lane A rows at all, because freshness
    correctly fails, so it could never be a comparison source. Only a real
    in-session `--follow` run yields Lane A.
    """

    repository = OrbResearchRepository(tmp_path / "orb.db")
    source_id, config_id = runner_identities(source)
    _live_run(repository, run_id, source_id=source_id, config_id=config_id)
    repository.finish_shadow_run(
        run_id, finished_at_utc=at(14, 20), stop_reason="CONTINUOUS_END_REACHED",
        session_classification="PARTIAL_SHADOW_SESSION",
    )
    return repository, run_id


def test_a_once_run_against_a_past_session_yields_no_lane_a_rows(tmp_path, source):
    """Documents why a smoke run can never serve as a comparison source."""

    summary = ShadowRunner(parse_args([
        "--rubix-db-path", str(source), "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out"), "--session-date", DAY.isoformat(),
        "--once", "--smoke",
    ])).run()
    assert summary["live_evaluations"] == 0
    repository = OrbResearchRepository(tmp_path / "orb.db")
    assert repository.table_count("orb_shadow_live_states") == 0
    with pytest.raises(ShadowRunSelectionError):
        select_live_run(
            repository.find_shadow_runs(), session_date=DAY,
            source_path_identity=runner_identities(source)[0],
            config_identity=CONFIG.fingerprint,
        )


def test_a_cross_run_comparison_stores_both_run_ids(tmp_path, source):
    repository, live_id = _prepare_live_run(tmp_path, source)
    summary = ShadowRunner(parse_args([
        "--rubix-db-path", str(source), "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out2"), "--session-date", DAY.isoformat(),
        "--reconstruct", "--compare-live-run-id", live_id,
    ])).run()
    assert summary["comparison_scope"] == "CROSS_RUN"
    assert summary["compared_live_run_id"] == live_id

    rows = repository.load_cross_run_comparison(live_id, summary["run_id"])
    assert rows, "cross-run comparison rows must exist"
    with repository.connect() as connection:
        pairs = connection.execute(
            "SELECT DISTINCT live_run_id, reconstruction_run_id "
            "FROM orb_shadow_cross_run_comparison"
        ).fetchall()
    assert [tuple(p) for p in pairs] == [(live_id, summary["run_id"])]
    # The reconstruction run records which live run it was measured against.
    recon_row = next(
        r for r in repository.find_shadow_runs() if r["run_id"] == summary["run_id"]
    )
    assert recon_row["compared_live_run_id"] == live_id


def test_the_cross_run_comparison_finds_the_prior_lane_a_state(tmp_path, source):
    """The prior run's Lane A must actually reach the comparison."""

    repository, live_id = _prepare_live_run(tmp_path, source)
    summary = ShadowRunner(parse_args([
        "--rubix-db-path", str(source), "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out2"), "--session-date", DAY.isoformat(),
        "--reconstruct", "--compare-live-run-id", live_id,
    ])).run()
    rows = repository.load_cross_run_comparison(live_id, summary["run_id"])
    aaa = next(r for r in rows if r["canonical_ticker"] == "AAA")
    assert aaa["live_state"] == "BREAKOUT_REJECTED_STALE"
    # Not the fabricated all-HISTORICAL_ONLY report a same-run compare gives.
    assert aaa["difference_reason"] != ComparisonReason.HISTORICAL_ONLY.value


def test_the_reconstruction_run_writes_no_lane_a_rows(tmp_path, source):
    repository, live_id = _prepare_live_run(tmp_path, source)
    before = repository.table_count("orb_shadow_live_states")
    recon = ShadowRunner(parse_args([
        "--rubix-db-path", str(source), "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "o2"), "--session-date", DAY.isoformat(),
        "--reconstruct", "--compare-live-run-id", live_id,
    ])).run()
    assert recon["live_evaluations"] == 0
    assert repository.table_count("orb_shadow_live_states") == before
    assert repository.load_shadow_live_states(recon["run_id"]) == ()


def test_reconstruction_never_even_calls_the_live_evaluator(tmp_path, source):
    """Tests the guard itself, not a condition that happens to mask it.

    Asserting only "no Lane A rows appeared" passes vacuously here: freshness
    already yields zero live records for a past session, so removing the guard
    would not fail that assertion. This forces `evaluate_live` to return a row
    if it is ever reached, so the guard is what is actually under test.
    """

    repository, live_id = _prepare_live_run(tmp_path, source)
    calls: list[str] = []
    original = OrbShadowService.evaluate_live

    def spy(self, snapshot, *, tickers=None):
        calls.append("evaluate_live")
        return (
            _rec("ENTRY_READY_RESEARCH"),  # would be written if the guard were gone
        )

    OrbShadowService.evaluate_live = spy
    try:
        recon = ShadowRunner(parse_args([
            "--rubix-db-path", str(source),
            "--research-db-path", str(tmp_path / "orb.db"),
            "--output-dir", str(tmp_path / "o3"), "--session-date", DAY.isoformat(),
            "--reconstruct", "--compare-live-run-id", live_id,
        ])).run()
    finally:
        OrbShadowService.evaluate_live = original

    assert calls == [], "reconstruction must not invoke the live evaluator at all"
    assert recon["live_evaluations"] == 0
    assert repository.load_shadow_live_states(recon["run_id"]) == ()


def test_repeated_cross_run_comparison_is_idempotent(tmp_path, source):
    repository, live_id = _prepare_live_run(tmp_path, source)
    argv = [
        "--rubix-db-path", str(source), "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "o2"), "--session-date", DAY.isoformat(),
        "--reconstruct", "--compare-live-run-id", live_id,
    ]
    first = ShadowRunner(parse_args(argv)).run()
    rows = repository.load_cross_run_comparison(live_id, first["run_id"])
    count = len(rows)
    # Re-persisting the identical pair inserts nothing further.
    service = OrbShadowService(OrbStrategyConfig())
    written = repository.persist_cross_run_comparison(live_id, first["run_id"], ())
    assert written == 0
    assert len(repository.load_cross_run_comparison(live_id, first["run_id"])) == count


def test_the_auto_selection_picks_the_single_eligible_run(tmp_path, source):
    repository, live_id = _prepare_live_run(tmp_path, source)
    summary = ShadowRunner(parse_args([
        "--rubix-db-path", str(source), "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "o3"), "--session-date", DAY.isoformat(),
        "--reconstruct", "--compare-latest-live-run",
    ])).run()
    assert summary["compared_live_run_id"] == live_id
    assert summary["comparison_scope"] == "CROSS_RUN"


def test_the_comparison_flags_apply_to_reconstruction_only(tmp_path, source):
    args = parse_args([
        "--rubix-db-path", str(source), "--research-db-path", str(tmp_path / "orb.db"),
        "--session-date", DAY.isoformat(), "--follow", "--compare-latest-live-run",
    ])
    with pytest.raises(ValueError, match="apply to --reconstruct only"):
        ShadowRunner(args)


# =========================================================================== #
# COMPARISON TAXONOMY AND EVIDENCE
# =========================================================================== #


def _rec(state, *, version="v1", status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, reasons=()):
    from scalping_orb.shadow_service import ShadowStateRecord

    return ShadowStateRecord(
        session_date=DAY, canonical_ticker="AAA", opening_range_revision=0,
        opening_range_version_identity=version, final_state=state, terminal=False,
        rejection_reasons=tuple(reasons), evidence_fingerprint="e",
        candidate_identity="c", live_status=status, observed_at_utc=at(11, 0),
        exchange_watermark_utc=at(11, 0), observed_receive_lag_seconds=1.0,
    )


def _recon_rec(state, *, version="v1"):
    from scalping_orb.shadow_service import ShadowReconstructionRecord

    return ShadowReconstructionRecord(
        session_date=DAY, canonical_ticker="AAA", opening_range_revision=0,
        opening_range_version_identity=version, final_state=state, terminal=False,
        rejection_reasons=(), evidence_fingerprint="e", candidate_identity="c",
    )


@pytest.mark.parametrize(
    "live, recon, revised, expected",
    [
        (_rec("WAIT_BREAKOUT"), _recon_rec("WAIT_BREAKOUT"), False, ComparisonReason.IDENTICAL),
        (_rec("DATA_UNAVAILABLE"), _recon_rec("DATA_UNAVAILABLE"), False, ComparisonReason.DATA_UNAVAILABLE),
        (None, _recon_rec("WAIT_BREAKOUT"), False, ComparisonReason.HISTORICAL_ONLY),
        (_rec("WAIT_BREAKOUT"), None, False, ComparisonReason.LIVE_ONLY),
        (
            _rec("BREAKOUT_REJECTED_STALE", reasons=("STALE_LIVE_DATA",)),
            _recon_rec("ENTRY_READY_RESEARCH"), True,
            ComparisonReason.FRESHNESS_REJECTED_LIVE,
        ),
        (
            _rec("WAIT_BREAKOUT", version="v1"),
            _recon_rec("ENTRY_READY_RESEARCH", version="v2"), True,
            ComparisonReason.OPENING_RANGE_REVISED,
        ),
        (
            _rec("WAIT_BREAKOUT"), _recon_rec("ENTRY_READY_RESEARCH"), False,
            ComparisonReason.STATE_DIFFERENCE,
        ),
    ],
)
def test_every_category_is_reachable_with_recorded_evidence(live, recon, revised, expected):
    reason, evidence = classify_difference(live, recon, revised)
    assert reason is expected
    assert evidence, "every category must record why it was selected"
    assert all(isinstance(item, str) for item in evidence)


def test_freshness_evidence_records_that_a_revision_was_secondary():
    reason, evidence = classify_difference(
        _rec("BREAKOUT_REJECTED_STALE", version="v1", reasons=("STALE_LIVE_DATA",)),
        _recon_rec("ENTRY_READY_RESEARCH", version="v2"),
        True,
    )
    assert reason is ComparisonReason.FRESHNESS_REJECTED_LIVE
    assert "opening_range_revised=true_but_not_primary" in evidence
    assert any(item.startswith("live_rejection_reasons=") for item in evidence)


def test_state_difference_states_that_no_cause_was_established():
    _reason, evidence = classify_difference(
        _rec("WAIT_BREAKOUT"), _recon_rec("ENTRY_READY_RESEARCH"), False
    )
    assert "cause_not_established" in evidence


def test_the_evidence_is_persisted_with_the_category(tmp_path, source):
    repository, live_id = _prepare_live_run(tmp_path, source)
    ShadowRunner(parse_args([
        "--rubix-db-path", str(source), "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "o2"), "--session-date", DAY.isoformat(),
        "--reconstruct", "--compare-live-run-id", live_id,
    ])).run()
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT difference_reason, difference_evidence_json "
            "FROM orb_shadow_cross_run_comparison"
        ).fetchall()
    assert rows
    for reason, evidence_json in rows:
        assert reason in {r.value for r in ComparisonReason}
        assert json.loads(evidence_json), f"{reason} stored no evidence"


# =========================================================================== #
# RUN LISTING
# =========================================================================== #


def test_run_listing_is_deterministic(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    for name in ("runB", "runA", "runC"):
        _live_run(repository, name)
    first = [row["run_id"] for row in repository.find_shadow_runs()]
    second = [row["run_id"] for row in repository.find_shadow_runs()]
    assert first == second
    assert first == sorted(first), "ties break on run_id, so ordering is total"


def test_run_listing_reports_lane_counts_and_classification(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    _live_run(repository, "runA")
    repository.finish_shadow_run(
        "runA", finished_at_utc=at(14, 20), stop_reason="DONE",
        session_classification="PARTIAL_SHADOW_SESSION",
    )
    row = repository.find_shadow_runs()[0]
    assert row["lane_a_rows"] == 1
    assert row["lane_b_rows"] == 0
    assert row["session_classification"] == "PARTIAL_SHADOW_SESSION"
    assert row["research_only"] == 1
    assert row["finished_at_utc"] is not None


def test_run_listing_exposes_no_secret_or_payload(tmp_path, capsys):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    _live_run(repository, "runA")
    args = parse_args([
        "--list-runs", "--research-db-path", str(tmp_path / "orb.db"),
    ])
    assert list_runs(args) == 0
    out = capsys.readouterr().out
    assert "runA" in out
    assert "RESEARCH ONLY" in out
    for forbidden in ("password", "api_key", "token", "last_price", "bid", "ask"):
        assert forbidden not in out.lower()


def test_run_listing_requires_no_source_path(tmp_path):
    args = parse_args(["--list-runs", "--research-db-path", str(tmp_path / "orb.db")])
    assert args.rubix_db_path is None
    assert list_runs(args) == 0


# =========================================================================== #
# MIGRATION 6
# =========================================================================== #


V5_TABLES = (
    "orb_sessions", "orb_normalized_events", "orb_bars", "orb_opening_ranges",
    "orb_candidates", "orb_state_transitions", "orb_shadow_runs",
    "orb_shadow_cursors", "orb_shadow_cycles", "orb_shadow_live_states",
    "orb_shadow_reconstruction_states", "orb_shadow_live_replay_comparison",
)


def _seed_v5(path):
    from scalping_orb.events import RubixQuoteInput
    from scalping_orb.shadow import OrbShadowIngestionService

    repository = OrbResearchRepository(path, target_schema_version=5)
    config = OrbDataConfig(research_database_path=str(path))
    service = OrbShadowIngestionService(
        repository, config, holidays=(), mapping_validator=lambda a, b: True
    )
    quotes = []
    seq = 0
    for ticker in ("AAA", "BBB"):
        for minute in range(20):
            seq += 1
            moment = at(10, 0) + timedelta(minutes=minute)
            quotes.append(
                RubixQuoteInput(
                    canonical_ticker=ticker, verified_rubix_symbol=f"CASE~{ticker}",
                    market_timestamp=moment, receive_timestamp=moment + timedelta(seconds=1),
                    sequence=seq, last_price=10.0 + minute * 0.01,
                    cumulative_volume=100.0 * (minute + 1), bid=9.99, ask=10.01,
                    has_feed_timestamp=True, source_row_id=seq,
                )
            )
    service.ingest(quotes, evaluated_at=at(14, 15))
    # A v5-era run row, written with the v5 column set only — the point is to
    # prove migration 6 upgrades rows that predate its columns.
    with repository.transaction() as connection:
        connection.execute(
            """INSERT INTO orb_shadow_runs
               (run_id, session_date, mode, started_at_utc, finished_at_utc,
                runner_started_before_open, stop_reason, session_classification,
                source_path_identity, config_identity, strategy_fingerprint,
                engine_version, research_only, production_disabled, recorded_at_utc)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("legacyRun", DAY.isoformat(), "FOLLOW", at(10, 0).isoformat(), None,
             1, None, None, "sid", "cid", "sf", "ev", 1, 1, at(10, 0).isoformat()),
        )
        connection.execute(
            """INSERT INTO orb_shadow_cycles
               (cycle_id, run_id, cycle_index, started_at_utc, finished_at_utc,
                cursor_low_source_id, cursor_high_source_id, source_rows_read,
                normalized_events, session_loads, symbols_in_snapshot,
                symbols_evaluated, snapshot_identity, live_status,
                duration_seconds, source_failure, recorded_at_utc)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("legacyCycle", "legacyRun", 0, at(11, 0).isoformat(),
             at(11, 1).isoformat(), 0, 1, 1, 1, 1, 1, 1, "s",
             "LIVE_SHADOW_HEALTHY", 0.1, None, at(11, 1).isoformat()),
        )
        connection.execute(
            """INSERT INTO orb_shadow_live_states
               (live_state_id, run_id, cycle_id, session_date, canonical_ticker,
                opening_range_revision, opening_range_version_identity, final_state,
                terminal, rejection_reasons_json, evidence_fingerprint,
                candidate_identity, live_status, observed_at_utc,
                exchange_watermark_utc, observed_receive_lag_seconds,
                research_only, recorded_at_utc)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("ls1", "legacyRun", "legacyCycle", DAY.isoformat(), "AAA", 0, "or-v1",
             "WAIT_BREAKOUT", 0, "[]", "e", "c", "LIVE_SHADOW_HEALTHY",
             at(11, 0).isoformat(), at(11, 0).isoformat(), 1.0, 1,
             at(11, 0).isoformat()),
        )
    return repository


def _snapshot(repository, tables):
    with repository.connect() as connection:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tables
        }


def test_migration_six_upgrades_a_populated_v5_database_without_row_loss(tmp_path):
    path = tmp_path / "orb.db"
    old = _seed_v5(path)
    assert old.database_status()["user_version"] == 5
    before = _snapshot(old, V5_TABLES)
    assert before["orb_normalized_events"] > 0
    assert before["orb_shadow_runs"] == 1
    assert before["orb_shadow_live_states"] == 1

    upgraded = OrbResearchRepository(path)
    assert upgraded.database_status()["user_version"] == 6
    assert _snapshot(upgraded, V5_TABLES) == before
    assert upgraded.table_count("orb_shadow_cross_run_comparison") == 0


def test_older_rows_get_safe_defaults_for_the_new_columns(tmp_path):
    path = tmp_path / "orb.db"
    _seed_v5(path)
    upgraded = OrbResearchRepository(path)
    row = upgraded.find_shadow_runs()[0]
    # NULL is the honest default: the flag did not exist when the row was written.
    assert row["active_universe_only"] is None
    assert row["compared_live_run_id"] is None


def test_migration_six_keeps_wal_and_foreign_keys(tmp_path):
    path = tmp_path / "orb.db"
    _seed_v5(path)
    upgraded = OrbResearchRepository(path)
    status = upgraded.database_status()
    assert status["journal_mode"] == "WAL"
    assert status["foreign_keys"] is True
    with upgraded.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repeating_migration_six_changes_nothing(tmp_path):
    path = tmp_path / "orb.db"
    _seed_v5(path)
    first = OrbResearchRepository(path)
    before = _snapshot(first, V5_TABLES)
    for _ in range(3):
        OrbResearchRepository(path).migrate()
    final = OrbResearchRepository(path)
    assert final.database_status()["user_version"] == SCHEMA_VERSION
    assert _snapshot(final, V5_TABLES) == before


def test_a_failing_migration_six_rolls_back_atomically(tmp_path, monkeypatch):
    import scalping_orb.repository as repository_module

    path = tmp_path / "orb.db"
    old = _seed_v5(path)
    before = _snapshot(old, V5_TABLES)

    broken = dict(repository_module.MIGRATIONS)
    broken[6] = ("phase2c_full_shadow_run_controls", "CREATE TABLE not valid sql (;")
    monkeypatch.setattr(repository_module, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.Error):
        OrbResearchRepository(path)
    monkeypatch.undo()

    recovered = OrbResearchRepository(path, target_schema_version=5)
    assert recovered.database_status()["user_version"] == 5
    assert _snapshot(recovered, V5_TABLES) == before
    with recovered.connect() as connection:
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
    assert "orb_shadow_cross_run_comparison" not in names


def test_a_downgrade_below_six_is_refused(tmp_path):
    path = tmp_path / "orb.db"
    OrbResearchRepository(path)
    with pytest.raises(RuntimeError, match="downgrade"):
        OrbResearchRepository(path, target_schema_version=5)


def test_no_forbidden_trading_table_or_column_is_introduced(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    forbidden = (
        "order", "execution", "position", "trade", "pnl", "profit", "broker",
        "fill", "alert", "notification", "commission", "quantity",
    )
    with repository.connect() as connection:
        names = {
            r[0] for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in ("orb_shadow_cross_run_comparison",):
            for row in connection.execute(f"PRAGMA table_info({table})"):
                assert not any(word in row[1].lower() for word in forbidden), row[1]
    for word in forbidden:
        assert not any(word in name.lower() for name in names), word


# =========================================================================== #
# REGRESSIONS
# =========================================================================== #


def test_the_source_remains_read_only(source):
    reader = ShadowSourceReader(source, CONFIG)
    with reader.connect() as connection:
        assert int(connection.execute("PRAGMA query_only").fetchone()[0]) == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM quotes")


def test_no_dashboard_alert_or_trading_surface_was_added():
    import io
    import tokenize

    def code_only(text):
        kept = []
        previous = tokenize.INDENT
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING and previous in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL
            ):
                continue
            kept.append(token.string)
            if token.type not in (tokenize.NL, tokenize.COMMENT):
                previous = token.type
        return " ".join(kept)

    forbidden = (
        "streamlit", "send_alert", "notify", "place_order", "submit_order",
        "broker", "paper_trade", "position_size", "portfolio_heat", "realized_pnl",
        "websocket", "authenticate", "subprocess", "taskkill",
    )
    for name in (
        "scalping_orb/shadow_source.py", "scalping_orb/shadow_snapshot.py",
        "scalping_orb/shadow_service.py", "scripts/run_orb_shadow_session.py",
    ):
        code = code_only(Path(name).read_text(encoding="utf-8"))
        for word in forbidden:
            assert word not in code, f"{name} contains {word!r}"


def test_no_threshold_was_changed():
    """The run controls touch orchestration, never strategy numbers."""

    config = OrbStrategyConfig()
    assert config.minimum_reward_risk == 1.5
    assert config.target_1_r_multiple == 1.0
    assert config.target_2_r_multiple == 2.0
    assert config.maximum_breakout_extension_percent == 0.020
    assert config.maximum_structural_breach_bars == 0
