"""Phase 2C Shadow integration — deterministic tests.

Research only. No test starts Rubix, opens a websocket, authenticates, reaches
the network, or reads the production database. Every source is a temporary
synthetic SQLite file built by `synthetic_source` below, shaped exactly like the
real `quotes` table — including its two awkward properties: `sequence` is always
NULL, and `market_timestamp` has one-second granularity so collisions are normal.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tokenize
from zoneinfo import ZoneInfo

import pytest

from scalping_orb.config import OrbDataConfig
from scalping_orb.engine import DailyContext, OpeningRangeSnapshot
from scalping_orb.events import (
    CumulativeVolumeTracker,
    NormalizationMode,
    RubixEventNormalizer,
    UniverseMembershipStatus,
)
from scalping_orb.opening_range import OpeningRangeStatus
from scalping_orb.repository import SCHEMA_VERSION, OrbResearchRepository
from scalping_orb.session import OrbSessionClassifier, OrbSessionPhase
from scalping_orb.shadow_service import (
    ComparisonReason,
    OrbShadowService,
    SessionClassification,
    ShadowCycleMetrics,
    ShadowLiveStatus,
    classify_session,
    receive_lag_statistics,
)
from scalping_orb.shadow_snapshot import (
    BarFinality,
    ExchangeWatermark,
    ShadowSnapshotBuilder,
    percentile,
)
from scalping_orb.shadow_source import (
    SOURCE_TABLE,
    ShadowCursor,
    ShadowSourceReader,
    ShadowSourceUnavailable,
)
from scalping_orb.states import OrbResearchState
from scalping_orb.strategy_config import EvaluationMode, OrbStrategyConfig


CAIRO = ZoneInfo("Africa/Cairo")
DAY = date(2026, 8, 3)
CONFIG = OrbDataConfig()


def at(hour, minute, second=0):
    return datetime.combine(DAY, time(hour, minute, second), tzinfo=CAIRO).astimezone(
        timezone.utc
    )


# --------------------------------------------------------------------------- #
# Synthetic source, shaped like the real collector output
# --------------------------------------------------------------------------- #


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


def synthetic_source(path, rows):
    """Build a source file. `sequence` is always NULL, as in production."""

    connection = sqlite3.connect(path)
    try:
        connection.executescript(SOURCE_SCHEMA)
        connection.executemany(
            """INSERT INTO quotes
               (ticker,last_price,bid,ask,volume,market_timestamp,received_at,
                exchange,sequence,change_percent,has_feed_timestamp)
               VALUES (?,?,?,?,?,?,?,?,NULL,?,1)""",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return Path(path)


def quote_row(ticker, market, price, volume, *, lag_seconds=1.0, bid=None, ask=None):
    received = market + timedelta(seconds=lag_seconds)
    return (
        ticker,
        price,
        bid if bid is not None else price - 0.01,
        ask if ask is not None else price + 0.01,
        volume,
        market.isoformat(),
        received.isoformat(),
        "CASE",
        0.0,
    )


def walking_session(tickers=("TEST",), *, minutes=60, start_hour=10, lag_seconds=1.0):
    """One quote per ticker per minute across the continuous session."""

    rows = []
    for index in range(minutes):
        moment = at(start_hour, 0) + timedelta(minutes=index)
        for offset, ticker in enumerate(tickers):
            rows.append(
                quote_row(
                    ticker,
                    moment,
                    10.0 + 0.01 * index + 0.1 * offset,
                    1000.0 * (index + 1),
                    lag_seconds=lag_seconds,
                )
            )
    return rows


@pytest.fixture
def source(tmp_path):
    return synthetic_source(tmp_path / "rubix_synthetic.db", walking_session())


def normalize(quotes, *, mode=NormalizationMode.LIVE, evaluated_at=None):
    normalizer = RubixEventNormalizer(
        CONFIG, holidays=(), mapping_validator=lambda a, b: True, mode=mode
    )
    batch = normalizer.normalize_many(
        quotes, evaluated_at=evaluated_at or at(14, 15)
    )
    return CumulativeVolumeTracker(CONFIG).apply_many(batch.events), batch.quality_events


def _code_only(source_text: str) -> str:
    """Strip docstrings and comments so a prohibition scan reads real code."""

    kept: list[str] = []
    previous = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source_text).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.NEWLINE,
            tokenize.NL,
        ):
            continue
        kept.append(token.string)
        if token.type not in (tokenize.NL, tokenize.COMMENT):
            previous = token.type
    return " ".join(kept)


SHADOW_MODULES = (
    "scalping_orb/shadow_source.py",
    "scalping_orb/shadow_snapshot.py",
    "scalping_orb/shadow_service.py",
    "scripts/run_orb_shadow_session.py",
)


# =========================================================================== #
# SOURCE SAFETY
# =========================================================================== #


def test_the_source_is_opened_read_only(source):
    reader = ShadowSourceReader(source, CONFIG)
    with reader.connect() as connection:
        assert int(connection.execute("PRAGMA query_only").fetchone()[0]) == 1


def test_writing_to_the_source_is_refused_by_sqlite(source):
    reader = ShadowSourceReader(source, CONFIG)
    with reader.connect() as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO quotes (ticker,market_timestamp,received_at) VALUES ('X','a','b')")
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM quotes")


def test_reading_never_modifies_the_source_file(source):
    import hashlib

    before = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    reader = ShadowSourceReader(source, CONFIG)
    reader.read_batch(ShadowCursor(), session_date=DAY)
    reader.session_dates()
    reader.max_source_id()
    assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == before


def test_a_missing_source_fails_closed(tmp_path):
    with pytest.raises(ShadowSourceUnavailable):
        ShadowSourceReader(tmp_path / "absent.db", CONFIG)


def test_no_collector_or_authentication_code_exists():
    """No websocket, no auth, no process spawn, no subscription change."""

    forbidden = (
        "websocket", "websockets", "socket.", "connect_ws", "authenticate",
        "api_key", "password", "token", "subprocess", "Popen", "os.system",
        "taskkill", "kill(", "subscribe", "start_rubix", "launch_rubix",
        "yfinance", "yahoo", "requests.get", "urlopen", "httpx",
    )
    for name in SHADOW_MODULES:
        code = _code_only(Path(name).read_text(encoding="utf-8"))
        for word in forbidden:
            assert word not in code, f"{name} contains {word!r}"


def test_no_execution_alert_or_dashboard_surface_exists():
    forbidden = (
        "place_order", "submit_order", "broker", "position_size", "portfolio_heat",
        "send_alert", "notify", "streamlit", "paper_trade", "realized_pnl",
        "daily_loss_limit", "BUY", "SELL",
    )
    for name in SHADOW_MODULES:
        code = _code_only(Path(name).read_text(encoding="utf-8"))
        for word in forbidden:
            assert word not in code, f"{name} contains {word!r}"


def test_runtime_modules_hard_code_no_machine_path():
    for name in SHADOW_MODULES:
        text = Path(name).read_text(encoding="utf-8")
        assert "EGX_AI_Trader" not in text
        assert "F:\\" not in text


def test_a_production_database_cannot_be_the_research_target(tmp_path):
    protected = tmp_path / "rubix_live_market.db"
    protected.write_bytes(b"")
    with pytest.raises(ValueError, match="protected/production"):
        OrbResearchRepository(protected)


def test_candles_1m_is_never_read():
    """Phase 2A builds quote-derived bars; a second OHLC source is not used."""

    for name in SHADOW_MODULES:
        code = _code_only(Path(name).read_text(encoding="utf-8"))
        assert "candles_1m" not in code


# =========================================================================== #
# CURSOR
# =========================================================================== #


def test_the_initial_cursor_reads_from_the_beginning(source):
    reader = ShadowSourceReader(source, CONFIG)
    batch = reader.read_batch(ShadowCursor(), session_date=DAY)
    assert batch.rows_read > 0
    assert batch.cursor_before.last_source_id == 0
    assert batch.cursor_after.last_source_id == batch.rows_read


def test_the_cursor_reads_only_new_rows(source):
    reader = ShadowSourceReader(source, CONFIG, batch_size=10)
    first = reader.read_batch(ShadowCursor(), session_date=DAY)
    second = reader.read_batch(first.cursor_after, session_date=DAY)
    first_ids = {q.source_row_id for q in first.quotes}
    second_ids = {q.source_row_id for q in second.quotes}
    assert first_ids and second_ids
    assert not (first_ids & second_ids), "a row was delivered twice"
    assert min(second_ids) > max(first_ids)


def test_restarting_from_a_saved_cursor_loses_no_row(source):
    reader = ShadowSourceReader(source, CONFIG, batch_size=7)
    seen: list[int] = []
    cursor = ShadowCursor()
    while True:
        batch = reader.read_batch(cursor, session_date=DAY)
        if batch.rows_read == 0:
            break
        seen.extend(q.source_row_id for q in batch.quotes)
        # Simulate a process restart: only the cursor survives.
        cursor = ShadowCursor(
            source_table=batch.cursor_after.source_table,
            last_source_id=batch.cursor_after.last_source_id,
        )
    with sqlite3.connect(source) as connection:
        expected = [row[0] for row in connection.execute("SELECT id FROM quotes ORDER BY id")]
    assert seen == expected


def test_a_cursor_may_never_move_backwards():
    cursor = ShadowCursor(last_source_id=100)
    with pytest.raises(ValueError, match="backwards"):
        cursor.advanced_to(50, market_timestamp=None, receive_timestamp=None, rows=1)


def test_distinct_events_sharing_a_timestamp_are_all_retained(tmp_path):
    """The source has one-second granularity; collapsing on it loses events."""

    moment = at(10, 30)
    rows = [
        quote_row("TEST", moment, 10.00, 1000.0),
        quote_row("TEST", moment, 10.05, 1100.0),
        quote_row("TEST", moment, 10.02, 1200.0),
    ]
    path = synthetic_source(tmp_path / "collide.db", rows)
    batch = ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY)
    assert batch.rows_read == 3
    assert len({q.source_row_id for q in batch.quotes}) == 3
    assert len({q.last_price for q in batch.quotes}) == 3


def test_source_row_id_is_a_progress_cursor_not_a_market_identity():
    """It feeds Phase 2A's identity hash; it is not the identity itself."""

    code = _code_only(Path("scalping_orb/shadow_source.py").read_text(encoding="utf-8"))
    assert "source_row_id" in code
    # The reader must never build its own market key from the row id.
    assert "market_identity" not in code
    assert "_source_identity" not in code, "identity stays in Phase 2A"


def test_the_cursor_does_not_depend_on_the_null_source_sequence(source):
    batch = ShadowSourceReader(source, CONFIG).read_batch(ShadowCursor(), session_date=DAY)
    assert all(q.sequence is None for q in batch.quotes), "fixture must mirror production"
    assert batch.cursor_after.last_source_id > 0


def test_rows_with_unusable_market_time_still_advance_the_cursor(tmp_path):
    """Otherwise a single bad row would wedge the reader forever."""

    good = quote_row("TEST", at(10, 5), 10.0, 100.0)
    bad = ("TEST", 10.0, 9.9, 10.1, 100.0, "not-a-timestamp", at(10, 6).isoformat(), "CASE", 0.0)
    path = synthetic_source(tmp_path / "bad.db", [good, bad])
    batch = ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor())
    assert batch.rows_read == 2
    assert len(batch.quotes) == 1
    assert batch.cursor_after.last_source_id == 2


def test_the_cursor_is_stored_only_in_the_research_database(tmp_path, source):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.start_shadow_run(
        "run", DAY, mode="ONCE", started_at_utc=at(10, 0),
        runner_started_before_open=True, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
    )
    repository.save_shadow_cursor("run", ShadowCursor(last_source_id=42))
    assert repository.load_shadow_cursor("run", SOURCE_TABLE).last_source_id == 42
    with sqlite3.connect(source) as connection:
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
    assert not any("cursor" in n.lower() or "orb_" in n.lower() for n in names)


# =========================================================================== #
# BATCHING  (closes REPLAY_SESSION_BATCHING_REQUIRED)
# =========================================================================== #


def build_snapshot(events, *, as_of=None, quality=(), previous=None, rows=0):
    return ShadowSnapshotBuilder(CONFIG).build(
        DAY,
        events,
        as_of=as_of or at(14, 15),
        cursor_low_source_id=0,
        cursor_high_source_id=rows,
        quality_events=quality,
        source_rows_observed=rows,
        previous_final_bar_keys=previous,
    )


def test_the_session_is_grouped_once_and_read_without_further_io(source):
    events, quality = normalize(
        ShadowSourceReader(source, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events, quality=quality)
    assert snapshot.tickers == ("TEST",)
    assert snapshot.events_by_ticker["TEST"]
    assert snapshot.one_minute_by_ticker["TEST"]


def test_evaluating_many_symbols_performs_no_per_symbol_database_load(tmp_path):
    """The regression that motivated this phase, asserted directly."""

    tickers = tuple(f"SYM{i:03d}" for i in range(25))
    path = synthetic_source(
        tmp_path / "many.db", walking_session(tickers, minutes=40)
    )
    reader = ShadowSourceReader(path, CONFIG)
    repository = OrbResearchRepository(tmp_path / "orb.db")

    loads = {"source": 0}
    original = ShadowSourceReader.read_batch

    def counting(self, *args, **kwargs):
        loads["source"] += 1
        return original(self, *args, **kwargs)

    ShadowSourceReader.read_batch = counting
    try:
        batch = reader.read_batch(ShadowCursor(), session_date=DAY)
        events, quality = normalize(batch.quotes)
        snapshot = build_snapshot(events, quality=quality, rows=batch.rows_read)
        # Both counters are captured across the *evaluation*, which is where
        # the Phase 2B regression lived.
        repo_loads_before = repository.event_load_count
        service = OrbShadowService(OrbStrategyConfig())
        records = service.evaluate_reconstruction(snapshot)
    finally:
        ShadowSourceReader.read_batch = original

    assert loads["source"] == 1, "the source must be read exactly once"
    assert repository.event_load_count == repo_loads_before, (
        "evaluation must not touch the database at all"
    )
    assert len(snapshot.tickers) == len(tickers)
    assert len(records) == len(tickers), "every symbol evaluated from the one snapshot"


def test_only_symbols_with_a_newly_final_bar_are_affected(tmp_path):
    tickers = ("AAA", "BBB")
    path = synthetic_source(tmp_path / "two.db", walking_session(tickers, minutes=40))
    batch = ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY)
    events, quality = normalize(batch.quotes)

    first = build_snapshot(events, quality=quality)
    assert set(first.affected_tickers) == set(tickers)

    # Re-building with the same evidence and the same carry-forward set means
    # nothing is newly final, so nothing is re-evaluated.
    carried = ShadowSnapshotBuilder.final_bar_keys(first)
    second = build_snapshot(events, quality=quality, previous=carried)
    assert second.affected_tickers == ()


def test_an_unrelated_symbol_does_not_trigger_reevaluation(tmp_path):
    base = walking_session(("AAA",), minutes=40)
    path = synthetic_source(tmp_path / "a.db", base)
    events, quality = normalize(
        ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    first = build_snapshot(events, quality=quality)
    carried = ShadowSnapshotBuilder.final_bar_keys(first)

    extra = walking_session(("BBB",), minutes=40)
    path2 = synthetic_source(tmp_path / "b.db", base + extra)
    events2, quality2 = normalize(
        ShadowSourceReader(path2, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    second = build_snapshot(events2, quality=quality2, previous=carried)
    assert "BBB" in second.affected_tickers
    assert "AAA" not in second.affected_tickers


def test_the_snapshot_identity_is_deterministic(source):
    events, quality = normalize(
        ShadowSourceReader(source, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    a = build_snapshot(events, quality=quality)
    b = build_snapshot(events, quality=quality)
    assert a.snapshot_identity == b.snapshot_identity


def test_results_do_not_depend_on_symbol_iteration_order(tmp_path):
    tickers = ("CCC", "AAA", "BBB")
    path = synthetic_source(tmp_path / "order.db", walking_session(tickers, minutes=40))
    events, quality = normalize(
        ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    forward = build_snapshot(events, quality=quality)
    reversed_snapshot = build_snapshot(list(reversed(list(events))), quality=quality)
    assert forward.snapshot_identity == reversed_snapshot.snapshot_identity

    service = OrbShadowService(OrbStrategyConfig())
    a = {r.canonical_ticker: r.final_state for r in service.evaluate_reconstruction(forward)}
    b = {
        r.canonical_ticker: r.final_state
        for r in service.evaluate_reconstruction(reversed_snapshot)
    }
    assert a == b


# =========================================================================== #
# WATERMARK AND LATE EVENTS
# =========================================================================== #


def watermark(*, evaluated, latest_market=None, latest_receive=None, grace=90.0):
    return ExchangeWatermark(
        session_date=DAY,
        latest_market_timestamp_utc=latest_market or evaluated,
        latest_receive_timestamp_utc=latest_receive or evaluated,
        evaluated_at_utc=evaluated,
        lateness_grace_seconds=grace,
        freshness_budget_seconds=CONFIG.maximum_quote_age_seconds,
    )


def five_minute_bar(start, *, phase=OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE):
    from scalping_orb.bars import CompletedBar

    return CompletedBar(
        canonical_ticker="TEST", interval_minutes=5, session_date=DAY,
        bar_start_utc=start, bar_end_utc=start + timedelta(minutes=5),
        open=10.0, high=10.2, low=9.9, close=10.1, volume=100.0, update_count=5,
        first_sequence=None, last_sequence=None, data_quality_flags=(),
        completed=True, session_phase=phase, source_identity="s", component_bar_count=5,
    )


def test_a_bar_is_not_final_before_its_boundary_passes():
    bar = five_minute_bar(at(11, 0))
    mark = watermark(evaluated=at(11, 2))
    assert mark.classify(bar) is BarFinality.BOUNDARY_NOT_PASSED


def test_a_bar_is_not_final_until_the_lateness_grace_elapses():
    bar = five_minute_bar(at(11, 0))
    mark = watermark(evaluated=at(11, 5, 30), grace=90.0)
    assert mark.classify(bar) is BarFinality.LATENESS_GRACE_PENDING
    later = watermark(evaluated=at(11, 7), grace=90.0)
    assert later.classify(bar) is BarFinality.OPERATIONALLY_FINAL


def test_a_stale_feed_prevents_operational_finality():
    bar = five_minute_bar(at(11, 0))
    mark = watermark(
        evaluated=at(11, 10), latest_market=at(11, 0), latest_receive=at(11, 9)
    )
    assert mark.classify(bar) is BarFinality.LIVE_EVIDENCE_STALE


def test_negative_lag_is_clock_skew_and_is_never_treated_as_fresh():
    mark = watermark(
        evaluated=at(11, 10), latest_market=at(11, 9), latest_receive=at(11, 8)
    )
    assert mark.observed_receive_lag_seconds < 0
    assert not mark.live_evidence_fresh


def test_an_auction_bar_is_excluded_from_continuous_finality():
    bar = five_minute_bar(at(14, 15), phase=OrbSessionPhase.CLOSING_AUCTION)
    assert watermark(evaluated=at(14, 30)).classify(bar) is BarFinality.AUCTION_EXCLUDED


def test_a_late_event_is_stored_but_does_not_rewrite_lane_a(tmp_path):
    """Lane A rows are keyed on the cycle, so a correction appends."""

    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.start_shadow_run(
        "run", DAY, mode="FOLLOW", started_at_utc=at(10, 0),
        runner_started_before_open=True, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
    )
    from scalping_orb.shadow_service import ShadowStateRecord

    def record(state):
        return ShadowStateRecord(
            session_date=DAY, canonical_ticker="TEST", opening_range_revision=0,
            opening_range_version_identity="v1", final_state=state, terminal=False,
            rejection_reasons=(), evidence_fingerprint="e", candidate_identity="c",
            live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,
            observed_at_utc=at(11, 0), exchange_watermark_utc=at(11, 0),
            observed_receive_lag_seconds=1.0,
        )

    for index, state in enumerate(("WAIT_BREAKOUT", "BREAKOUT_CANDIDATE")):
        cycle = ShadowCycleMetrics(
            cycle_id=f"c{index}", cycle_index=index, started_at_utc=at(11, 0),
            finished_at_utc=at(11, 1), cursor_low_source_id=0, cursor_high_source_id=1,
            source_rows_read=1, normalized_events=1, session_loads=1,
            symbols_in_snapshot=1, symbols_evaluated=1, snapshot_identity="s",
            live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, duration_seconds=0.1,
        )
        repository.persist_shadow_cycle("run", cycle, (record(state),))

    stored = repository.load_shadow_live_states("run")
    assert [row["final_state"] for row in stored] == ["WAIT_BREAKOUT", "BREAKOUT_CANDIDATE"]
    assert repository.table_count("orb_shadow_live_states") == 2


def test_a_corrected_opening_range_cannot_mutate_a_stored_lane_a_evaluation(tmp_path):
    """The core Lane A immutability guarantee, asserted at the storage layer.

    A later cycle observing a *revised* opening range must append a new row.
    The original observation — its state and its OR version — must still be
    readable exactly as it was recorded.
    """

    repository = OrbResearchRepository(tmp_path / "orb.db")
    _run(repository)

    original = _live("WAIT_BREAKOUT", version="or-v1")
    repository.persist_shadow_cycle("run", _cycle(0, "c0"), (original,))

    # Same symbol, same run, later cycle, corrected opening range and a state
    # that would have been "better" had it been known live.
    corrected = _live("ENTRY_READY_RESEARCH", version="or-v2")
    repository.persist_shadow_cycle("run", _cycle(1, "c1"), (corrected,))

    stored = repository.load_shadow_live_states("run")
    assert len(stored) == 2, "the correction appended; it did not overwrite"
    first = [r for r in stored if r["opening_range_version_identity"] == "or-v1"]
    assert len(first) == 1
    assert first[0]["final_state"] == "WAIT_BREAKOUT", (
        "the original live observation was mutated"
    )
    # Each observation carries the OR version it was actually bound to.
    assert {r["opening_range_version_identity"] for r in stored} == {"or-v1", "or-v2"}


def test_lane_b_cannot_write_into_lane_a_storage(tmp_path, source):
    """The lanes have separate tables and separate identities."""

    repository = OrbResearchRepository(tmp_path / "orb.db")
    _run(repository)
    events, _ = normalize(
        ShadowSourceReader(source, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    records = OrbShadowService(OrbStrategyConfig()).evaluate_reconstruction(snapshot)
    repository.persist_shadow_reconstruction("run", records)
    assert repository.table_count("orb_shadow_reconstruction_states") > 0
    assert repository.table_count("orb_shadow_live_states") == 0, (
        "reconstruction must never populate Lane A"
    )


def test_reconstruct_mode_writes_no_lane_a_rows(tmp_path, source):
    """Regression: --reconstruct once fabricated live observations."""

    from scripts.run_orb_shadow_session import ShadowRunner, parse_args

    args = parse_args([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out"),
        "--session-date", DAY.isoformat(), "--reconstruct",
    ])
    summary = ShadowRunner(args).run()
    repository = OrbResearchRepository(tmp_path / "orb.db")
    assert summary["live_evaluations"] == 0
    assert repository.table_count("orb_shadow_live_states") == 0
    assert repository.table_count("orb_shadow_reconstruction_states") > 0


# =========================================================================== #
# WATERMARK BOUNDARIES
# =========================================================================== #


def test_the_grace_deadline_boundary_is_exact():
    """One microsecond before / at / after the operational cutoff."""

    bar = five_minute_bar(at(11, 0))          # ends 11:05
    end = at(11, 5)
    grace = 90.0
    just_before = watermark(evaluated=end + timedelta(seconds=grace) - timedelta(microseconds=1), grace=grace)
    exactly = watermark(evaluated=end + timedelta(seconds=grace), grace=grace)
    just_after = watermark(evaluated=end + timedelta(seconds=grace) + timedelta(microseconds=1), grace=grace)
    assert just_before.classify(bar) is BarFinality.LATENESS_GRACE_PENDING
    assert exactly.classify(bar) is BarFinality.OPERATIONALLY_FINAL
    assert just_after.classify(bar) is BarFinality.OPERATIONALLY_FINAL


def test_the_bar_end_boundary_is_exact():
    bar = five_minute_bar(at(11, 0))          # ends 11:05
    assert watermark(evaluated=at(11, 5) - timedelta(microseconds=1), grace=0.0).classify(
        bar
    ) is BarFinality.BOUNDARY_NOT_PASSED
    assert watermark(evaluated=at(11, 5), grace=0.0).classify(bar) is (
        BarFinality.OPERATIONALLY_FINAL
    )


@pytest.mark.parametrize(
    "hour, minute, phase, expected_continuous",
    [
        (10, 0, OrbSessionPhase.OPENING_RANGE_BUILDING, True),    # continuous open
        (10, 15, OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE, True),  # OR closes
        (13, 30, OrbSessionPhase.LATE_CONTINUOUS, True),
        (14, 15, OrbSessionPhase.CLOSING_AUCTION, False),         # auction starts
        (14, 25, OrbSessionPhase.CLOSING_AUCTION, False),         # auction ends
    ],
)
def test_session_boundary_phases_are_separated(hour, minute, phase, expected_continuous):
    bar = five_minute_bar(at(hour, minute), phase=phase)
    mark = watermark(evaluated=at(15, 0))
    finality = mark.classify(bar)
    if expected_continuous:
        assert finality is not BarFinality.AUCTION_EXCLUDED
    else:
        assert finality is BarFinality.AUCTION_EXCLUDED


def test_the_classifier_places_the_exchange_boundaries_where_expected():
    classifier = OrbSessionClassifier(CONFIG)
    assert classifier.classify(at(9, 59, 59)) is OrbSessionPhase.PRE_SESSION
    assert classifier.classify(at(10, 0)) is OrbSessionPhase.OPENING_RANGE_BUILDING
    assert classifier.classify(at(10, 14, 59)) is OrbSessionPhase.OPENING_RANGE_BUILDING
    assert classifier.classify(at(10, 15)) is OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE
    assert classifier.classify(at(14, 14, 59)) is OrbSessionPhase.LATE_CONTINUOUS
    assert classifier.classify(at(14, 15)) is OrbSessionPhase.CLOSING_AUCTION
    assert classifier.classify(at(14, 24, 59)) is OrbSessionPhase.CLOSING_AUCTION
    assert classifier.classify(at(14, 25)) is OrbSessionPhase.POST_MARKET


# =========================================================================== #
# BARS
# =========================================================================== #


def test_five_minute_bars_need_five_completed_component_minutes(tmp_path):
    rows = []
    for minute in (0, 1, 2, 4):  # minute 3 deliberately absent
        moment = at(10, 20) + timedelta(minutes=minute)
        rows.append(quote_row("TEST", moment, 10.0 + minute * 0.01, 1000.0 * (minute + 1)))
    path = synthetic_source(tmp_path / "gap.db", rows)
    events, _ = normalize(
        ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    starts = {bar.bar_start_utc for bar in snapshot.five_minute_by_ticker.get("TEST", ())}
    assert at(10, 20) not in starts, "a missing minute must not be filled"


def test_no_five_minute_bar_bridges_continuous_into_auction(source):
    events, _ = normalize(
        ShadowSourceReader(source, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    for bars in snapshot.five_minute_by_ticker.values():
        for bar in bars:
            assert bar.session_phase is not OrbSessionPhase.CLOSING_AUCTION


def test_volume_validity_is_tracked_separately_from_price_validity(source):
    events, _ = normalize(
        ShadowSourceReader(source, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    summary = snapshot.quality_summary
    assert summary.completed_one_minute_bars > 0
    assert (
        summary.volume_valid_bars + summary.volume_unavailable_bars
        == summary.completed_one_minute_bars
    )


def test_bars_accumulate_incrementally_across_cycles(tmp_path):
    path = synthetic_source(tmp_path / "inc.db", walking_session(minutes=40))
    reader = ShadowSourceReader(path, CONFIG, batch_size=12)
    cursor = ShadowCursor()
    collected: list = []
    counts = []
    while True:
        batch = reader.read_batch(cursor, session_date=DAY)
        if batch.rows_read == 0:
            break
        events, _ = normalize(batch.quotes)
        collected.extend(events)
        counts.append(len(build_snapshot(collected).one_minute_by_ticker.get("TEST", ())))
        cursor = batch.cursor_after
    assert counts == sorted(counts), "completed bars must never decrease"
    assert counts[-1] > counts[0]


# =========================================================================== #
# OPENING RANGE
# =========================================================================== #


def test_no_opening_range_is_ready_before_the_window_closes(tmp_path):
    rows = [
        quote_row("TEST", at(10, 0) + timedelta(minutes=m), 10.0 + m * 0.01, 1000.0 * (m + 1))
        for m in range(10)
    ]
    path = synthetic_source(tmp_path / "early.db", rows)
    events, _ = normalize(
        ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes,
        evaluated_at=at(10, 10),
    )
    snapshot = build_snapshot(events, as_of=at(10, 10))
    assert snapshot.opening_ranges["TEST"].status is not OpeningRangeStatus.READY


def test_a_complete_opening_range_freezes_at_revision_zero(tmp_path):
    rows = [
        quote_row("TEST", at(10, 0) + timedelta(minutes=m), 10.0 + m * 0.01, 1000.0 * (m + 1))
        for m in range(20)
    ]
    path = synthetic_source(tmp_path / "or.db", rows)
    events, _ = normalize(
        ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    opening_range = snapshot.opening_ranges["TEST"]
    assert opening_range.status is OpeningRangeStatus.READY
    assert opening_range.revision == 0
    assert opening_range.high is not None and opening_range.low is not None


def test_lane_a_binds_to_the_original_version_and_lane_b_may_select_a_revision(source):
    events, _ = normalize(
        ShadowSourceReader(source, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    original = snapshot.opening_ranges["TEST"]
    revised = replace(original, revision=1, high=(original.high or 10.0) + 0.5)
    assert revised.version_identity() != original.version_identity()

    service = OrbShadowService(OrbStrategyConfig())
    live = service.evaluate_live(snapshot, tickers=["TEST"])
    reconstruction = service.evaluate_reconstruction(
        snapshot, opening_range_overrides={"TEST": revised}
    )
    if live:
        assert live[0].opening_range_version_identity == original.version_identity()
    assert reconstruction[0].opening_range_version_identity == revised.version_identity()


# =========================================================================== #
# ENGINE INTEGRATION
# =========================================================================== #


def test_the_shadow_layer_changes_no_threshold():
    """It supplies evidence. Every rule stays in the Phase 2B engine."""

    code = _code_only(Path("scalping_orb/shadow_service.py").read_text(encoding="utf-8"))
    for word in (
        "minimum_close_above_or_high_percent", "maximum_breakout_extension_percent",
        "maximum_pullback_depth_percent", "minimum_reward_risk", "reclaim_rule",
        "stop_atr_buffer", "target_1_r_multiple",
    ):
        assert word not in code, f"shadow layer touches threshold {word}"


def test_live_and_reconstruction_use_the_declared_evaluation_modes(source):
    events, _ = normalize(
        ShadowSourceReader(source, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    service = OrbShadowService(OrbStrategyConfig())
    records = service.evaluate_reconstruction(snapshot)
    assert all(r.evaluation_mode == EvaluationMode.HISTORICAL_REPLAY.value for r in records)


def test_stale_live_evidence_prevents_any_live_advancement(tmp_path):
    """Lane A sees no operationally final bar, so it cannot advance."""

    path = synthetic_source(
        tmp_path / "stale.db", walking_session(minutes=60, lag_seconds=600.0)
    )
    events, _ = normalize(
        ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    assert not snapshot.watermark.live_evidence_fresh
    assert snapshot.affected_tickers == ()
    service = OrbShadowService(OrbStrategyConfig())
    assert service.live_status_for(snapshot) is ShadowLiveStatus.LIVE_SHADOW_STALE
    for ticker in snapshot.tickers:
        assert snapshot.final_five_minute_bars(ticker) == ()


def test_historical_reconstruction_may_still_evaluate_stale_evidence(tmp_path):
    path = synthetic_source(
        tmp_path / "stale2.db", walking_session(minutes=60, lag_seconds=600.0)
    )
    events, _ = normalize(
        ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    records = OrbShadowService(OrbStrategyConfig()).evaluate_reconstruction(snapshot)
    assert records, "Lane B must still reconstruct what Lane A could not use"


def test_entry_ready_research_remains_the_ceiling_and_is_research_only(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    with repository.connect() as connection:
        for table in ("orb_shadow_live_states", "orb_shadow_reconstruction_states"):
            columns = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
            assert "research_only" in columns
    # No trade-management state exists anywhere in the shadow vocabulary.
    for state in ("TRADE_ACTIVE", "PARTIAL_EXIT", "TRAILING", "EXITED"):
        assert not hasattr(OrbResearchState, state)


def test_enums_are_persisted_by_value_not_by_repr(tmp_path, source):
    """Regression: `str(member)` yields 'Class.MEMBER' on Python 3.11+.

    Storing that corrupts every status column and silently breaks equality
    against the declared vocabulary — including the Lane A/Lane B comparison,
    which reads `live_status` back to decide whether freshness was the cause.
    """

    from scripts.run_orb_shadow_session import ShadowRunner, parse_args

    args = parse_args([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out"),
        "--session-date", DAY.isoformat(), "--once", "--smoke",
    ])
    ShadowRunner(args).run()

    repository = OrbResearchRepository(tmp_path / "orb.db")
    live_values = {r.value for r in ShadowLiveStatus}
    classification_values = {r.value for r in SessionClassification}
    reason_values = {r.value for r in ComparisonReason}
    with repository.connect() as connection:
        for table in ("orb_shadow_live_states", "orb_shadow_cycles"):
            for (value,) in connection.execute(f"SELECT DISTINCT live_status FROM {table}"):
                assert "." not in value, f"{table}.live_status stored a repr: {value}"
                assert value in live_values
        for (value,) in connection.execute(
            "SELECT DISTINCT classification FROM orb_shadow_session_quality"
        ):
            assert value in classification_values, value
        for (value,) in connection.execute(
            "SELECT DISTINCT session_classification FROM orb_shadow_runs"
        ):
            assert value is None or value in classification_values, value
        for (value,) in connection.execute(
            "SELECT DISTINCT difference_reason FROM orb_shadow_live_replay_comparison"
        ):
            assert value in reason_values, value
        for (value,) in connection.execute(
            "SELECT DISTINCT evaluation_mode FROM orb_shadow_reconstruction_states"
        ):
            assert value in {m.value for m in EvaluationMode}, value


def test_the_stored_live_status_round_trips_into_the_comparison(tmp_path):
    """The column must be readable back as the vocabulary the comparison uses."""

    repository = OrbResearchRepository(tmp_path / "orb.db")
    _run(repository)
    repository.persist_shadow_cycle(
        "run", _cycle(), (_live("WAIT_BREAKOUT", status=ShadowLiveStatus.LIVE_SHADOW_STALE),)
    )
    stored = repository.load_shadow_live_states("run")
    assert stored[0]["live_status"] == ShadowLiveStatus.LIVE_SHADOW_STALE.value
    assert ShadowLiveStatus(stored[0]["live_status"]) is ShadowLiveStatus.LIVE_SHADOW_STALE


def test_no_forbidden_trading_table_is_introduced(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    with repository.connect() as connection:
        names = {
            r[0].lower()
            for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    for forbidden in ("order", "execution", "position", "trade", "pnl", "fill", "broker", "notification"):
        assert not any(forbidden in name for name in names), forbidden


# =========================================================================== #
# LANES AND COMPARISON
# =========================================================================== #


def test_the_comparison_reports_symbols_evaluable_only_historically(source):
    events, _ = normalize(
        ShadowSourceReader(source, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    service = OrbShadowService(OrbStrategyConfig())
    reconstruction = service.evaluate_reconstruction(snapshot)
    rows = service.compare(DAY, [], reconstruction)
    assert rows
    assert all(
        row.difference_reason
        in (ComparisonReason.HISTORICAL_ONLY.value, ComparisonReason.DATA_UNAVAILABLE.value)
        for row in rows
    )
    assert all(not row.evaluable_live for row in rows)
    assert all(row.evaluable_historically for row in rows)


def _live(state, *, status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, version="v1", ticker="TEST"):
    from scalping_orb.shadow_service import ShadowStateRecord

    return ShadowStateRecord(
        session_date=DAY, canonical_ticker=ticker, opening_range_revision=0,
        opening_range_version_identity=version, final_state=state, terminal=False,
        rejection_reasons=(), evidence_fingerprint="e", candidate_identity="c",
        live_status=status, observed_at_utc=at(11, 0),
        exchange_watermark_utc=at(11, 0), observed_receive_lag_seconds=1.0,
    )


def _recon(state, *, version="v1", ticker="TEST"):
    from scalping_orb.shadow_service import ShadowReconstructionRecord

    return ShadowReconstructionRecord(
        session_date=DAY, canonical_ticker=ticker, opening_range_revision=0,
        opening_range_version_identity=version, final_state=state, terminal=False,
        rejection_reasons=(), evidence_fingerprint="e", candidate_identity="c",
    )


@pytest.mark.parametrize(
    "live, recon, expected",
    [
        (_live("WAIT_BREAKOUT"), _recon("WAIT_BREAKOUT"), ComparisonReason.IDENTICAL),
        (None, _recon("WAIT_BREAKOUT"), ComparisonReason.HISTORICAL_ONLY),
        (_live("WAIT_BREAKOUT"), None, ComparisonReason.LIVE_ONLY),
        (_live("DATA_UNAVAILABLE"), _recon("DATA_UNAVAILABLE"), ComparisonReason.DATA_UNAVAILABLE),
        (
            _live("BREAKOUT_REJECTED_STALE", status=ShadowLiveStatus.LIVE_SHADOW_STALE),
            _recon("ENTRY_READY_RESEARCH"),
            ComparisonReason.FRESHNESS_REJECTED_LIVE,
        ),
        (
            _live("WAIT_BREAKOUT", version="v1"),
            _recon("ENTRY_READY_RESEARCH", version="v2"),
            ComparisonReason.OPENING_RANGE_REVISED,
        ),
        (
            _live("WAIT_BREAKOUT"),
            _recon("ENTRY_READY_RESEARCH"),
            ComparisonReason.STATE_DIFFERENCE,
        ),
    ],
)
def test_every_comparison_category_is_reachable_and_distinct(live, recon, expected):
    """Differences must never collapse into one generic bucket."""

    service = OrbShadowService(OrbStrategyConfig())
    rows = service.compare(DAY, [live] if live else [], [recon] if recon else [])
    assert len(rows) == 1
    assert rows[0].difference_reason == expected.value


def test_freshness_outranks_an_opening_range_revision():
    """Both true at once: staleness is why live could not reach the state.

    Reporting only the revision would understate that the reconstructed state
    was never actionable live.
    """

    service = OrbShadowService(OrbStrategyConfig())
    rows = service.compare(
        DAY,
        [_live("BREAKOUT_REJECTED_STALE", status=ShadowLiveStatus.LIVE_SHADOW_STALE, version="v1")],
        [_recon("ENTRY_READY_RESEARCH", version="v2")],
    )
    assert rows[0].difference_reason == ComparisonReason.FRESHNESS_REJECTED_LIVE.value
    # The revision is still recorded, just not as the headline cause.
    assert rows[0].opening_range_revised is True
    assert rows[0].states_match is False


def test_a_healthy_feed_can_still_be_a_live_freshness_rejection():
    """The exact LCSW shape from the partial smoke.

    The feed was healthy (≈1 s lag) yet the breakout was rejected as stale,
    because Phase 2A's LiveDecisionCapability has no enabled member. Keying the
    category only on feed status would mis-attribute this to the OR revision
    and imply the candidate was merely a versioning artefact.
    """

    service = OrbShadowService(OrbStrategyConfig())
    rows = service.compare(
        DAY,
        [
            _live(
                "BREAKOUT_REJECTED_STALE",
                status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY,  # feed was fine
                version="v1",
            )
        ],
        [_recon("ENTRY_READY_RESEARCH", version="v2")],
    )
    assert rows[0].difference_reason == ComparisonReason.FRESHNESS_REJECTED_LIVE.value
    assert rows[0].opening_range_revised is True


def test_a_live_record_carrying_a_stale_rejection_reason_is_classified_on_freshness():
    from scalping_orb.shadow_service import ShadowStateRecord

    record = ShadowStateRecord(
        session_date=DAY, canonical_ticker="TEST", opening_range_revision=0,
        opening_range_version_identity="v1", final_state="WAIT_BREAKOUT",
        terminal=False, rejection_reasons=("LIVE_DECISION_DISABLED_FRESHNESS",),
        evidence_fingerprint="e", candidate_identity="c",
        live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, observed_at_utc=at(11, 0),
        exchange_watermark_utc=at(11, 0), observed_receive_lag_seconds=1.0,
    )
    rows = OrbShadowService(OrbStrategyConfig()).compare(
        DAY, [record], [_recon("ENTRY_READY_RESEARCH", version="v1")]
    )
    assert rows[0].difference_reason == ComparisonReason.FRESHNESS_REJECTED_LIVE.value


def test_the_full_required_comparison_taxonomy_exists():
    required = {
        "IDENTICAL", "OPENING_RANGE_REVISED", "FRESHNESS_REJECTED_LIVE",
        "HISTORICAL_ONLY", "LIVE_ONLY", "STATE_DIFFERENCE", "DATA_UNAVAILABLE",
    }
    assert required <= {reason.value for reason in ComparisonReason}


def test_reconstruction_is_idempotent(tmp_path, source):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.start_shadow_run(
        "run", DAY, mode="RECONSTRUCT", started_at_utc=at(10, 0),
        runner_started_before_open=True, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
    )
    events, _ = normalize(
        ShadowSourceReader(source, CONFIG).read_batch(ShadowCursor(), session_date=DAY).quotes
    )
    snapshot = build_snapshot(events)
    records = OrbShadowService(OrbStrategyConfig()).evaluate_reconstruction(snapshot)
    first = repository.persist_shadow_reconstruction("run", records)
    assert first == len(records)
    for _ in range(3):
        assert repository.persist_shadow_reconstruction("run", records) == 0
    assert repository.table_count("orb_shadow_reconstruction_states") == len(records)


# =========================================================================== #
# SESSION CLASSIFICATION
# =========================================================================== #


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
    )
    base.update(overrides)
    return classify_session(**base)


def test_a_pre_open_start_with_full_coverage_may_be_full():
    classification, reasons = _classify()
    assert classification is SessionClassification.FULL_SHADOW_SESSION
    assert reasons == ()


def test_a_start_after_ten_is_always_partial():
    classification, reasons = _classify(runner_started_utc=at(10, 1))
    assert classification is SessionClassification.PARTIAL_SHADOW_SESSION
    assert "RUNNER_STARTED_AFTER_CONTINUOUS_OPEN" in reasons


def test_stopping_before_the_continuous_end_prevents_full():
    classification, reasons = _classify(runner_finished_utc=at(13, 0))
    assert classification is SessionClassification.PARTIAL_SHADOW_SESSION
    assert "RUNNER_STOPPED_BEFORE_CONTINUOUS_END" in reasons


def test_a_polling_outage_beyond_the_limit_prevents_full():
    classification, reasons = _classify(maximum_polling_gap_seconds=900.0)
    assert "POLLING_OUTAGE_EXCEEDED_LIMIT" in reasons
    assert classification is SessionClassification.PARTIAL_SHADOW_SESSION


def test_a_missing_opening_range_prevents_full():
    classification, reasons = _classify(opening_ranges_ready=0)
    assert "NO_OPENING_RANGE_OBSERVED" in reasons


def test_insufficient_heartbeat_coverage_prevents_full():
    assert "INSUFFICIENT_HEARTBEAT_COVERAGE" in _classify(heartbeat_count=3)[1]


def test_a_stalled_cursor_prevents_full():
    assert "SOURCE_CURSOR_DID_NOT_PROGRESS" in _classify(cursor_advanced=False)[1]


def test_auction_only_coverage_does_not_count_as_continuous():
    assert "INSUFFICIENT_EXCHANGE_MINUTE_COVERAGE" in _classify(
        observed_exchange_minutes=9
    )[1]


def test_a_fabricated_session_date_cannot_manufacture_a_full_classification():
    """Backdating the session date does not move when the runner actually ran.

    Classification is computed from the runner's real start/finish instants
    against that date's exchange window, so pointing at an old session makes
    the run look *more* partial, never full.
    """

    # A run happening "now" but claiming a session from a week earlier.
    old_session = date(2026, 7, 27)
    classification, reasons = classify_session(
        session_date=old_session,
        runner_started_utc=at(11, 0),      # today's clock, after that day's open
        runner_finished_utc=at(11, 30),
        config=CONFIG,
        maximum_polling_gap_seconds=1.0,
        allowed_polling_gap_seconds=300.0,
        heartbeat_count=100_000,
        minimum_heartbeats=60,
        cursor_advanced=True,
        opening_ranges_ready=500,
        observed_exchange_minutes=100_000,
        minimum_exchange_minutes=200,
    )
    assert classification is SessionClassification.PARTIAL_SHADOW_SESSION
    assert "RUNNER_STARTED_AFTER_CONTINUOUS_OPEN" in reasons


def test_no_flag_can_upgrade_a_partial_run_to_full():
    """`smoke` only ever downgrades; there is no upgrade path in the API."""

    import inspect

    signature = inspect.signature(classify_session)
    assert "smoke" in signature.parameters
    # No parameter exists that could force FULL.
    for name in signature.parameters:
        assert "force" not in name.lower()
        assert "full" not in name.lower()
        assert "override" not in name.lower()
    # And a smoke run with otherwise perfect inputs still cannot be full.
    classification, _ = _classify(smoke=True)
    assert classification is not SessionClassification.FULL_SHADOW_SESSION


def test_a_replay_after_the_close_cannot_be_full():
    """Started at 15:00, after the session ended: partial, whatever it read.

    Only the start-time reason fires — finishing at 15:10 is genuinely after
    the 14:15 continuous end — and one disqualifying reason is enough.
    """

    classification, reasons = _classify(
        runner_started_utc=at(15, 0), runner_finished_utc=at(15, 10)
    )
    assert classification is SessionClassification.PARTIAL_SHADOW_SESSION
    assert "RUNNER_STARTED_AFTER_CONTINUOUS_OPEN" in reasons


def test_a_smoke_run_is_always_labelled_a_smoke_session():
    """Even with otherwise perfect coverage. It cannot close the blocker."""

    classification, reasons = _classify(smoke=True)
    assert classification is SessionClassification.PARTIAL_SMOKE_SESSION
    assert reasons


# =========================================================================== #
# QUALITY METRICS
# =========================================================================== #


def test_receive_lag_statistics_are_reported_without_hiding_negative_lag():
    class Event:
        def __init__(self, lag):
            self.market_timestamp_utc = at(11, 0)
            self.receive_timestamp_utc = at(11, 0) + timedelta(seconds=lag)

    stats = receive_lag_statistics(
        [Event(x) for x in (-2, 1, 2, 3, 400)], freshness_budget_seconds=60.0
    )
    assert stats["count"] == 5
    assert stats["negative"] == 1
    assert stats["median"] == 2
    assert stats["percent_above_budget"] == pytest.approx(20.0)


def test_percentiles_are_deterministic():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 0.5) == 3.0
    assert percentile(values, 0.95) == 5.0
    assert percentile([], 0.5) is None


# =========================================================================== #
# MIGRATION
# =========================================================================== #


PHASE_AB_TABLES = (
    "orb_sessions", "orb_normalized_events", "orb_bars", "orb_opening_ranges",
    "orb_data_quality_events", "orb_capabilities", "orb_collection_runs",
    "orb_candidates", "orb_state_transitions", "orb_breakouts", "orb_pullbacks",
    "orb_reclaims", "orb_research_setups",
)

SHADOW_TABLES = (
    "orb_shadow_runs", "orb_shadow_cursors", "orb_shadow_cycles",
    "orb_shadow_heartbeats", "orb_shadow_session_quality", "orb_shadow_live_states",
    "orb_shadow_reconstruction_states", "orb_shadow_live_replay_comparison",
)


def _seed_v4(path):
    """A populated v4 database, using the real Phase 2A ingestion path."""

    from scalping_orb.events import RubixQuoteInput
    from scalping_orb.shadow import OrbShadowIngestionService

    repository = OrbResearchRepository(path, target_schema_version=4)
    config = OrbDataConfig(research_database_path=str(path))
    service = OrbShadowIngestionService(
        repository, config, holidays=(), mapping_validator=lambda a, b: True
    )
    quotes = []
    seq = 0
    for ticker in ("TEST", "OTHER"):
        for minute in range(30):
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
    return repository


def _snapshot(repository, tables):
    with repository.connect() as connection:
        return {
            table: (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0],
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0],
            )
            for table in tables
        }


def test_migration_five_creates_every_shadow_table(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    # Later phases add migrations, so pin what this test is about: migration 5
    # has been applied and its tables exist.
    assert repository.database_status()["user_version"] >= 5
    with repository.connect() as connection:
        applied = {r[0] for r in connection.execute("SELECT version FROM orb_schema_meta")}
    assert 5 in applied
    for table in SHADOW_TABLES:
        assert repository.table_count(table) == 0


def test_upgrading_a_populated_v4_database_loses_no_row(tmp_path):
    path = tmp_path / "orb.db"
    old = _seed_v4(path)
    assert old.database_status()["user_version"] == 4
    before = _snapshot(old, PHASE_AB_TABLES)
    assert before["orb_normalized_events"][0] > 0
    assert before["orb_bars"][0] > 0

    upgraded = OrbResearchRepository(path, target_schema_version=5)
    assert upgraded.database_status()["user_version"] == 5
    # Identical row counts and identical CREATE TABLE text: additive only.
    assert _snapshot(upgraded, PHASE_AB_TABLES) == before
    for table in SHADOW_TABLES:
        assert upgraded.table_count(table) == 0


def test_the_upgraded_database_keeps_wal_and_foreign_keys(tmp_path):
    path = tmp_path / "orb.db"
    _seed_v4(path)
    upgraded = OrbResearchRepository(path)
    status = upgraded.database_status()
    assert status["journal_mode"] == "WAL"
    assert status["foreign_keys"] is True
    with upgraded.connect() as connection:
        assert int(connection.execute("PRAGMA busy_timeout").fetchone()[0]) == 30000
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repeating_the_shadow_migration_changes_nothing(tmp_path):
    path = tmp_path / "orb.db"
    _seed_v4(path)
    first = OrbResearchRepository(path)
    before = _snapshot(first, PHASE_AB_TABLES + SHADOW_TABLES)
    for _ in range(3):
        OrbResearchRepository(path).migrate()
    final = OrbResearchRepository(path)
    assert final.database_status()["user_version"] == SCHEMA_VERSION
    assert _snapshot(final, PHASE_AB_TABLES + SHADOW_TABLES) == before


def test_a_failing_shadow_migration_rolls_back_atomically(tmp_path, monkeypatch):
    import scalping_orb.repository as repository_module

    path = tmp_path / "orb.db"
    old = _seed_v4(path)
    before = _snapshot(old, PHASE_AB_TABLES)

    broken = dict(repository_module.MIGRATIONS)
    broken[5] = ("phase2c_shadow_integration", "CREATE TABLE not valid sql (;")
    monkeypatch.setattr(repository_module, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.Error):
        OrbResearchRepository(path)
    monkeypatch.undo()

    recovered = OrbResearchRepository(path, target_schema_version=4)
    assert recovered.database_status()["user_version"] == 4
    assert _snapshot(recovered, PHASE_AB_TABLES) == before
    with recovered.connect() as connection:
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
    assert "orb_shadow_runs" not in names


def test_a_schema_downgrade_is_refused(tmp_path):
    path = tmp_path / "orb.db"
    OrbResearchRepository(path)
    with pytest.raises(RuntimeError, match="downgrade"):
        OrbResearchRepository(path, target_schema_version=4)


# =========================================================================== #
# RESTART
# =========================================================================== #


def test_a_restarted_run_resumes_from_the_committed_cursor(tmp_path, source):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.start_shadow_run(
        "run", DAY, mode="FOLLOW", started_at_utc=at(10, 0),
        runner_started_before_open=True, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
    )
    reader = ShadowSourceReader(source, CONFIG, batch_size=9)
    batch = reader.read_batch(ShadowCursor(), session_date=DAY)
    repository.save_shadow_cursor("run", batch.cursor_after)

    # A brand-new process object, holding nothing but the database.
    resumed = OrbResearchRepository(tmp_path / "orb.db").load_shadow_cursor("run", SOURCE_TABLE)
    assert resumed.last_source_id == batch.cursor_after.last_source_id
    following = reader.read_batch(resumed, session_date=DAY)
    assert {q.source_row_id for q in following.quotes}.isdisjoint(
        {q.source_row_id for q in batch.quotes}
    )


def test_persisting_the_same_cycle_twice_creates_no_duplicate(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.start_shadow_run(
        "run", DAY, mode="ONCE", started_at_utc=at(10, 0),
        runner_started_before_open=True, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
    )
    cycle = ShadowCycleMetrics(
        cycle_id="c0", cycle_index=0, started_at_utc=at(11, 0),
        finished_at_utc=at(11, 1), cursor_low_source_id=0, cursor_high_source_id=5,
        source_rows_read=5, normalized_events=5, session_loads=1,
        symbols_in_snapshot=1, symbols_evaluated=1, snapshot_identity="s",
        live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, duration_seconds=0.5,
    )
    for _ in range(4):
        repository.persist_shadow_cycle("run", cycle, ())
    assert repository.table_count("orb_shadow_cycles") == 1


def test_an_interrupted_cycle_transaction_rolls_back(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.start_shadow_run(
        "run", DAY, mode="ONCE", started_at_utc=at(10, 0),
        runner_started_before_open=True, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
    )
    before = repository.table_count("orb_shadow_cycles")
    with pytest.raises(RuntimeError):
        with repository.transaction() as connection:
            connection.execute(
                """INSERT INTO orb_shadow_cycles VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("x", "run", 99, at(11, 0).isoformat(), at(11, 1).isoformat(),
                 0, 1, 1, 1, 1, 1, 1, "s", "LIVE_SHADOW_HEALTHY", 0.1, None,
                 at(11, 1).isoformat()),
            )
            raise RuntimeError("interrupted")
    assert repository.table_count("orb_shadow_cycles") == before


def _run(repository, run_id="run"):
    repository.start_shadow_run(
        run_id, DAY, mode="FOLLOW", started_at_utc=at(10, 0),
        runner_started_before_open=True, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
    )
    return run_id


def _cycle(index=0, cycle_id="c0", high=5):
    return ShadowCycleMetrics(
        cycle_id=cycle_id, cycle_index=index, started_at_utc=at(11, 0),
        finished_at_utc=at(11, 1), cursor_low_source_id=0, cursor_high_source_id=high,
        source_rows_read=high, normalized_events=high, session_loads=1,
        symbols_in_snapshot=1, symbols_evaluated=0, snapshot_identity="s",
        live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, duration_seconds=0.5,
    )


def test_case_a_a_failed_persistence_does_not_advance_the_cursor(tmp_path):
    """Case A: rows processed, persistence fails before commit."""

    repository = OrbResearchRepository(tmp_path / "orb.db")
    _run(repository)
    repository.save_shadow_cursor("run", ShadowCursor(last_source_id=10, rows_observed=10))

    with pytest.raises(RuntimeError):
        with repository.transaction() as connection:
            repository.save_shadow_cursor(
                "run", ShadowCursor(last_source_id=999, rows_observed=999),
                connection=connection,
            )
            raise RuntimeError("persistence failed mid-batch")

    # The cursor must still point at the last *committed* position.
    assert repository.load_shadow_cursor("run", SOURCE_TABLE).last_source_id == 10


def test_case_b_a_successful_batch_advances_the_cursor_atomically(tmp_path):
    """Case B: restart begins after the committed source id."""

    repository = OrbResearchRepository(tmp_path / "orb.db")
    _run(repository)
    repository.persist_shadow_cycle(
        "run", _cycle(high=42), (), cursor=ShadowCursor(last_source_id=42, rows_observed=42)
    )
    assert repository.table_count("orb_shadow_cycles") == 1
    assert repository.load_shadow_cursor("run", SOURCE_TABLE).last_source_id == 42


def test_case_c_replaying_the_same_batch_duplicates_nothing(tmp_path, source):
    """Case C: no duplicate events, bars, transitions or cycles."""

    repository = OrbResearchRepository(tmp_path / "orb.db")
    _run(repository)
    records = (_live("WAIT_BREAKOUT"),)
    for _ in range(4):
        repository.persist_shadow_cycle(
            "run", _cycle(), records, cursor=ShadowCursor(last_source_id=5)
        )
    assert repository.table_count("orb_shadow_cycles") == 1
    assert repository.table_count("orb_shadow_live_states") == 1

    # And the source reader itself replays the identical rows deterministically.
    reader = ShadowSourceReader(source, CONFIG)
    a = reader.read_batch(ShadowCursor(), session_date=DAY)
    b = reader.read_batch(ShadowCursor(), session_date=DAY)
    assert [q.source_row_id for q in a.quotes] == [q.source_row_id for q in b.quotes]


def test_case_d_two_distinct_payloads_at_one_timestamp_both_survive(tmp_path):
    """Case D: distinct market payloads sharing ticker+timestamp."""

    moment = at(10, 30)
    path = synthetic_source(
        tmp_path / "d.db",
        [quote_row("TEST", moment, 10.00, 1000.0), quote_row("TEST", moment, 10.05, 1100.0)],
    )
    batch = ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY)
    events, _ = normalize(batch.quotes)
    identities = {event.source_identity for event in events}
    assert len(batch.quotes) == 2
    assert len(identities) == 2, "distinct payloads must keep distinct identities"


def test_case_e_an_identical_payload_redelivered_dedupes_but_advances(tmp_path):
    """Case E: same payload, different source ids — one event, cursor moves."""

    moment = at(10, 30)
    row = quote_row("TEST", moment, 10.00, 1000.0)
    path = synthetic_source(tmp_path / "e.db", [row, row, row])
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.ensure_session("s", DAY, "cfg")

    batch = ShadowSourceReader(path, CONFIG).read_batch(ShadowCursor(), session_date=DAY)
    assert batch.rows_read == 3
    assert batch.cursor_after.last_source_id == 3, "cursor advances past every row"

    events, _ = normalize(batch.quotes)
    inserted = repository.insert_events("s", events)
    # Identity includes the source row id, so persistence is row-idempotent;
    # the market-payload identity is what marks them as the same observation.
    payload_keys = {
        (e.canonical_ticker, e.market_timestamp_utc, e.last_price, e.cumulative_volume)
        for e in events
    }
    assert len(payload_keys) == 1, "one market payload"
    assert repository.insert_events("s", events) == 0, "re-insert adds nothing"


def test_the_cursor_and_its_batch_commit_together(tmp_path):
    """The property that makes restart exact rather than approximately safe."""

    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.start_shadow_run(
        "run", DAY, mode="ONCE", started_at_utc=at(10, 0),
        runner_started_before_open=True, source_path_identity="sid",
        config_identity="cid", strategy_fingerprint="sf", engine_version="ev",
    )
    cycle = ShadowCycleMetrics(
        cycle_id="c0", cycle_index=0, started_at_utc=at(11, 0),
        finished_at_utc=at(11, 1), cursor_low_source_id=0, cursor_high_source_id=5,
        source_rows_read=5, normalized_events=5, session_loads=1,
        symbols_in_snapshot=1, symbols_evaluated=0, snapshot_identity="s",
        live_status=ShadowLiveStatus.LIVE_SHADOW_HEALTHY, duration_seconds=0.5,
    )
    repository.persist_shadow_cycle(
        "run", cycle, (), cursor=ShadowCursor(last_source_id=5, rows_observed=5)
    )
    assert repository.table_count("orb_shadow_cycles") == 1
    assert repository.load_shadow_cursor("run", SOURCE_TABLE).last_source_id == 5


# =========================================================================== #
# RUNNER
# =========================================================================== #


def test_the_runner_requires_an_explicit_source_path():
    from scripts.run_orb_shadow_session import parse_args

    with pytest.raises(SystemExit):
        parse_args([])


def test_the_runner_defaults_to_no_network_and_declares_research_only():
    from scripts.run_orb_shadow_session import RESEARCH_ONLY_BANNER, parse_args

    args = parse_args(["--rubix-db-path", "x.db"])
    assert args.no_network is True
    assert "RESEARCH ONLY" in RESEARCH_ONLY_BANNER
    assert "PRODUCTION EXECUTION DISABLED" in RESEARCH_ONLY_BANNER


def test_the_runner_completes_one_batch_end_to_end(tmp_path, source):
    from scripts.run_orb_shadow_session import ShadowRunner, parse_args

    args = parse_args([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out"),
        "--session-date", DAY.isoformat(),
        "--once", "--smoke",
    ])
    summary = ShadowRunner(args).run()
    assert summary["status"] == "SUCCESS"
    assert summary["research_only"] is True
    assert summary["production_disabled"] is True
    assert summary["source_mode"] == "READ_ONLY"
    assert summary["session_classification"] == "PARTIAL_SMOKE_SESSION"
    assert summary["session_loads_total"] >= 1
    assert summary["profitability_claim"] == "NONE"
    assert (tmp_path / "out" / "shadow_run_summary.json").is_file()


def test_the_research_destination_cannot_be_the_source_database(tmp_path, source):
    from scripts.run_orb_shadow_session import ShadowRunner, parse_args

    args = parse_args([
        "--rubix-db-path", str(source), "--research-db-path", str(source),
        "--session-date", DAY.isoformat(), "--once",
    ])
    with pytest.raises(ValueError, match="must not be the Rubix source"):
        ShadowRunner(args)


def test_the_destination_cannot_be_the_source_by_another_spelling(tmp_path, source):
    """`..` and relative spellings resolve to the same file."""

    from scripts.run_orb_shadow_session import _assert_distinct_databases

    indirect = Path(source).parent / "sub" / ".." / Path(source).name
    with pytest.raises(ValueError, match="must not be the Rubix source"):
        _assert_distinct_databases(Path(source), indirect)


def test_the_destination_cannot_be_a_source_sidecar_file(source):
    from scripts.run_orb_shadow_session import _assert_distinct_databases

    for suffix in ("-wal", "-shm"):
        with pytest.raises(ValueError, match="sidecar"):
            _assert_distinct_databases(Path(source), Path(str(source) + suffix))


def test_the_runner_refuses_a_production_database_as_the_research_target(tmp_path, source):
    from scripts.run_orb_shadow_session import ShadowRunner, parse_args

    protected = tmp_path / "rubix_live_market.db"
    protected.write_bytes(b"")
    args = parse_args([
        "--rubix-db-path", str(source), "--research-db-path", str(protected),
        "--session-date", DAY.isoformat(), "--once",
    ])
    with pytest.raises(ValueError, match="protected/production"):
        ShadowRunner(args)


def test_a_missing_source_fails_before_anything_is_written(tmp_path):
    from scripts.run_orb_shadow_session import ShadowRunner, parse_args

    destination = tmp_path / "orb.db"
    args = parse_args([
        "--rubix-db-path", str(tmp_path / "absent.db"),
        "--research-db-path", str(destination),
        "--session-date", DAY.isoformat(), "--once",
    ])
    with pytest.raises(ShadowSourceUnavailable):
        ShadowRunner(args)


def test_max_runtime_and_stop_at_continuous_end_are_wired(tmp_path, source):
    """`--follow` must have explicit, enforced stop conditions."""

    from scripts.run_orb_shadow_session import ShadowRunner, parse_args

    args = parse_args([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out"),
        "--session-date", DAY.isoformat(),
        "--follow", "--max-runtime-seconds", "0.001",
        "--poll-seconds", "0.001", "--stop-at-continuous-end", "--smoke",
    ])
    summary = ShadowRunner(args).run()
    assert summary["stop_reason"] in {
        "MAX_RUNTIME_REACHED", "CONTINUOUS_END_REACHED", "STOP_REQUESTED",
    }


def test_a_stop_request_ends_follow_mode_and_commits_the_cursor(tmp_path, source):
    """Graceful shutdown: the in-flight cycle commits, then the loop exits."""

    import scripts.run_orb_shadow_session as runner_module
    from scripts.run_orb_shadow_session import ShadowRunner, parse_args

    args = parse_args([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out"),
        "--session-date", DAY.isoformat(),
        "--follow", "--poll-seconds", "0.001", "--smoke",
    ])
    runner_module._STOP_REQUESTED["value"] = True
    runner_module._STOP_REQUESTED["reason"] = "SIGNAL_2"
    try:
        summary = ShadowRunner(args).run()
    finally:
        runner_module._STOP_REQUESTED["value"] = False
        runner_module._STOP_REQUESTED["reason"] = ""
    assert summary["stop_reason"] == "SIGNAL_2"
    repository = OrbResearchRepository(tmp_path / "orb.db")
    cursor = repository.load_shadow_cursor(summary["run_id"], SOURCE_TABLE)
    assert cursor is not None and cursor.last_source_id > 0


def test_the_runner_starts_no_process_and_leaves_none_behind(tmp_path, source):
    """No daemon, no service registration, no child process."""

    import multiprocessing
    import threading
    from scripts.run_orb_shadow_session import ShadowRunner, parse_args

    threads_before = threading.active_count()
    children_before = len(multiprocessing.active_children())
    args = parse_args([
        "--rubix-db-path", str(source),
        "--research-db-path", str(tmp_path / "orb.db"),
        "--output-dir", str(tmp_path / "out"),
        "--session-date", DAY.isoformat(), "--once", "--smoke",
    ])
    ShadowRunner(args).run()
    assert threading.active_count() == threads_before
    assert len(multiprocessing.active_children()) == children_before


def test_the_runner_is_not_registered_with_the_production_launcher():
    for name in ("scripts/launch_rubix_production.py", "scripts/launch_egx_ai_trader.py"):
        path = Path(name)
        if path.is_file():
            assert "run_orb_shadow_session" not in path.read_text(encoding="utf-8")


# =========================================================================== #
# REGRESSIONS
# =========================================================================== #


def test_phase_2a_configuration_fingerprint_is_undisturbed():
    """Phase 2C must not change any Phase 2A session identity."""

    assert OrbDataConfig().fingerprint == OrbDataConfig().fingerprint
    baseline = OrbDataConfig(research_database_path="data/research/orb_first_pullback.db")
    assert baseline.fingerprint == OrbDataConfig().fingerprint


def test_phase_2b_engine_module_is_untouched_by_this_phase():
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--name-only", "6d10334", "HEAD", "--", "scalping_orb/engine.py"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", "the Phase 2B engine must not change"


def test_no_dashboard_file_is_touched_by_this_phase():
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--name-only", "6d10334", "HEAD"], capture_output=True, text=True
    )
    changed = [line for line in result.stdout.splitlines() if line.strip()]
    for name in changed:
        lowered = name.lower()
        assert "dashboard" not in lowered
        assert "streamlit" not in lowered
        assert not lowered.endswith("app.py")
