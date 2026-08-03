from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import subprocess

import pytest

import scalping_orb.repository as repository_module
from scalping_orb.bars import aggregate_completed_one_minute_bars
from scalping_orb.capabilities import (
    HistoricalBarCapability,
    LiveDecisionCapability,
    assess_price_reference_capabilities,
)
from scalping_orb.config import OrbDataConfig
from scalping_orb.events import (
    HistoricalReplayStatus,
    LiveFreshnessStatus,
    MarketTimeStatus,
    NormalizationMode,
    RubixEventNormalizer,
    RubixQuoteInput,
    UniverseMembershipStatus,
)
from scalping_orb.opening_range import OpeningRangeStatus
from scalping_orb.replay import RubixReadOnlyReplaySource, stable_bar_bytes
from scalping_orb.repository import OrbResearchRepository
from scalping_orb.shadow import OrbShadowIngestionService


UTC = timezone.utc


def moment(day, hour, minute, second=0):
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=UTC)


def quote(market, *, received=None, price=10.0, volume=100.0,
          ticker="AALR", row_id=1, feed_time=True):
    return RubixQuoteInput(
        canonical_ticker=ticker,
        verified_rubix_symbol=f"CASE~{ticker}",
        market_timestamp=market,
        receive_timestamp=received or market + timedelta(milliseconds=10),
        sequence=None,
        last_price=price,
        cumulative_volume=volume,
        bid=price - 0.01,
        ask=price + 0.01,
        has_feed_timestamp=feed_time,
        source_row_id=row_id,
    )


def normalizer(config=None, mode=NormalizationMode.LIVE):
    return RubixEventNormalizer(
        config or OrbDataConfig(),
        mapping_validator=lambda *_: True,
        membership_resolver=lambda *_: (
            UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX
        ),
        mode=mode,
    )


def test_market_time_places_bar_receive_time_controls_live_freshness():
    day = date(2026, 8, 2)
    market = moment(day, 7, 1)
    item = quote(market, received=market + timedelta(seconds=120))
    event, _ = normalizer(mode=NormalizationMode.HISTORICAL_REPLAY).normalize(
        item, evaluated_at=item.receive_timestamp
    )
    assert event.market_time_status == MarketTimeStatus.MARKET_TIME_VALID_RECEIVE_DELAYED
    assert event.historical_replay_status == HistoricalReplayStatus.HISTORICAL_REPLAY_ACCEPTED
    assert event.live_freshness_status == LiveFreshnessStatus.LIVE_FRESHNESS_FAILED
    bar = aggregate_completed_one_minute_bars(
        (event,), as_of=market + timedelta(minutes=3)
    ).bars[0]
    assert bar.bar_start_utc == market.replace(second=0, microsecond=0)
    capabilities = assess_price_reference_capabilities(
        opening_range=None,
        latest_event=event,
        completed_bars=(bar,),
        intraday_history_sessions=13,
    )
    assert capabilities.current_bid is None
    assert capabilities.historical_bar_capability == (
        HistoricalBarCapability.HISTORICAL_BAR_RECONSTRUCTABLE
    )
    assert capabilities.live_decision_capability == (
        LiveDecisionCapability.LIVE_DECISION_DISABLED_STALE_QUOTE
    )


def test_unreliable_market_time_is_stored_but_never_builds_a_bar():
    day = date(2026, 8, 2)
    item = quote(moment(day, 7, 1), feed_time=False)
    event, _ = normalizer(mode=NormalizationMode.HISTORICAL_REPLAY).normalize(
        item, evaluated_at=item.receive_timestamp
    )
    assert event.market_time_status == MarketTimeStatus.MARKET_TIME_UNRELIABLE
    result = aggregate_completed_one_minute_bars(
        (event,), as_of=moment(day, 7, 3)
    )
    assert result.bars == ()
    assert result.quality_events[0].code == "MARKET_TIME_UNRELIABLE_EXCLUDED"


def test_late_correction_is_historical_only_and_live_policy_stays_zero_tolerance():
    day = date(2026, 8, 2)
    late = quote(moment(day, 7, 1), row_id=2)
    for mode, accepted in ((NormalizationMode.LIVE, False),
                           (NormalizationMode.HISTORICAL_REPLAY, True)):
        engine = normalizer(mode=mode)
        engine.normalize(quote(moment(day, 7, 2)), evaluated_at=moment(day, 7, 3))
        event, issues = engine.normalize(late, evaluated_at=moment(day, 7, 3))
        assert (event is not None) is accepted
        assert issues[0].code == (
            "LATE_CORRECTION_ONLY" if accepted else "OUT_OF_ORDER_REJECTED"
        )
        if accepted:
            assert event.historical_replay_status == HistoricalReplayStatus.LATE_CORRECTION_ONLY
            assert event.live_freshness_status == LiveFreshnessStatus.LIVE_FRESHNESS_FAILED


def test_dedup_memory_is_bounded_and_cleared_on_session_rollover():
    config = OrbDataConfig(maximum_seen_payloads_per_session=1)
    engine = normalizer(config)
    first_day = date(2026, 8, 2)
    next_day = date(2026, 8, 3)
    first, _ = engine.normalize(quote(moment(first_day, 7, 1)), evaluated_at=moment(first_day, 7, 1, 1))
    blocked, issues = engine.normalize(
        quote(moment(first_day, 7, 2), row_id=2, price=10.1),
        evaluated_at=moment(first_day, 7, 2, 1),
    )
    rolled, _ = engine.normalize(
        quote(moment(next_day, 7, 1), row_id=3),
        evaluated_at=moment(next_day, 7, 1, 1),
    )
    assert first is not None and rolled is not None
    assert blocked is None and issues[0].code == "DEDUP_MEMORY_LIMIT_REACHED"


def _rubix_fixture(path: Path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE quotes (
          id INTEGER PRIMARY KEY,ticker TEXT NOT NULL,last_price REAL,bid REAL,ask REAL,
          volume REAL,market_timestamp TEXT NOT NULL,received_at TEXT NOT NULL,
          exchange TEXT,sequence INTEGER,change_percent REAL,
          has_feed_timestamp INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE candles_1m (
          ticker TEXT NOT NULL,minute TEXT NOT NULL,open REAL NOT NULL,high REAL NOT NULL,
          low REAL NOT NULL,close REAL NOT NULL,volume REAL NOT NULL DEFAULT 0,
          updates INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(ticker,minute));
        """
    )
    dense = date(2026, 8, 2)
    source_id = 1
    for offset in range(15):
        market = moment(dense, 7, offset)
        price = 10 + offset / 100
        row = (source_id, "AALR", price, price - .01, price + .01,
               100 + offset, market.isoformat(),
               (market + timedelta(seconds=5)).isoformat(), "EGX", None, None, 1)
        connection.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
        source_id += 1
        duplicate = list(row)
        duplicate[0] = source_id
        duplicate[7] = (market + timedelta(seconds=30)).isoformat()
        connection.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", duplicate)
        source_id += 1
        connection.execute(
            "INSERT INTO candles_1m VALUES (?,?,?,?,?,?,?,?)",
            ("AALR", market.isoformat(), price, price, price, price, offset, 2),
        )
    sparse = date(2026, 7, 14)
    for ticker, volumes in (("AALR", (100, 120)), ("ADRI", (100, 40))):
        for offset, volume in enumerate(volumes):
            market = moment(sparse, 7, offset)
            connection.execute(
                "INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (source_id, ticker, 10 + offset, 9.9, 10.1, volume,
                 market.isoformat(), (market + timedelta(seconds=120)).isoformat(),
                 "EGX", None, None, 1),
            )
            source_id += 1
    connection.commit()
    connection.close()


def test_real_adapter_uses_actual_pipeline_is_read_only_and_double_replay_is_idempotent(tmp_path):
    rubix = tmp_path / "rubix.db"
    _rubix_fixture(rubix)
    before = hashlib.sha256(rubix.read_bytes()).hexdigest()
    source = RubixReadOnlyReplaySource(rubix)
    plan = source.build_plan()
    batch = source.load(plan)
    repository = OrbResearchRepository(tmp_path / "research.db")
    bars_first = []
    bars_second = []
    for target in (bars_first, bars_second):
        inserted = 0
        for session_date, raw_events in batch.raw_events_by_session:
            service = OrbShadowIngestionService(
                repository,
                normalization_mode=NormalizationMode.HISTORICAL_REPLAY,
            )
            result = service.ingest(
                raw_events,
                evaluated_at=source.classifier.window(session_date).continuous_end_utc,
            )
            inserted += result.inserted_events
            target.extend(result.one_minute_bars)
            target.extend(result.five_minute_bars)
        if target is bars_second:
            assert inserted == 0
    assert stable_bar_bytes(bars_first) == stable_bar_bytes(bars_second)
    assert batch.exact_payload_repeats >= 15
    assert batch.identity_hash_collisions == 0
    assert hashlib.sha256(rubix.read_bytes()).hexdigest() == before
    with source.connect() as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden(x)")


def _create_v1_fixture(path: Path):
    repository = OrbResearchRepository(path, target_schema_version=1)
    now = "2026-08-02T07:00:00+00:00"
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO orb_sessions(session_id,session_date,config_hash,status,created_at_utc,updated_at_utc) VALUES ('s','2026-08-02','cfg','COLLECTING',?,?)",
            (now, now),
        )
        connection.execute(
            """INSERT INTO orb_normalized_events
               (source_identity,session_id,canonical_ticker,verified_rubix_symbol,
                session_date,market_timestamp_utc,receive_timestamp_utc,source_event_type,
                feed_timestamp_quality,quote_age_seconds,spread_capability,
                volume_capability,volume_delta_status,sequence_gap,duplicate_status,
                out_of_order_status,session_phase,quality_flags_json,persisted_at_utc)
               VALUES ('event-hash','s','AALR','CASE~AALR','2026-08-02',?,?,
                'RUBIX_QUOTE','VERIFIED_FEED_TIMESTAMP',1,'SPREAD_AVAILABLE',
                'VOLUME_AVAILABLE','AVAILABLE',0,'UNIQUE','ORDERED',
                'OPENING_RANGE_BUILDING','[]',?)""",
            (now, now, now),
        )
        connection.execute(
            """INSERT INTO orb_bars
               (session_id,canonical_ticker,interval_minutes,session_date,bar_start_utc,
                bar_end_utc,open,high,low,close,update_count,data_quality_flags_json,
                completed,session_phase,source_identity,component_bar_count,persisted_at_utc)
               VALUES ('s','AALR',1,'2026-08-02',?,?,10,10,10,10,1,'[]',1,
               'OPENING_RANGE_BUILDING','bar-hash',0,?)""",
            (now, "2026-08-02T07:01:00+00:00", now),
        )
        connection.execute(
            """INSERT INTO orb_opening_ranges
               (opening_range_id,session_id,canonical_ticker,session_date,revision,status,
                completed_bar_count,expected_bar_count,coverage_ratio,
                data_quality_flags_json,is_frozen,recorded_at_utc)
               VALUES ('or','s','AALR','2026-08-02',0,'BUILDING',1,15,.066667,'[]',0,?)""",
            (now,),
        )
        connection.execute(
            """INSERT INTO orb_capabilities
               (capability_id,session_id,canonical_ticker,assessed_at_utc,spread_status,
                volume_status,true_vwap_status,proxy_status,time_of_day_rvol_status,
                intraday_history_sessions) VALUES ('cap','s','AALR',?,
                'SPREAD_AVAILABLE','VOLUME_AVAILABLE','TRUE_VWAP_UNAVAILABLE',
                'BAR_WEIGHTED_PRICE_PROXY_UNAVAILABLE',
                'TIME_OF_DAY_RVOL_INSUFFICIENT_HISTORY',1)""",
            (now,),
        )
        connection.execute(
            """INSERT INTO orb_data_quality_events
               (quality_id,session_id,canonical_ticker,session_date,observed_at_utc,
                code,dedupe_key) VALUES ('q','s','AALR','2026-08-02',?,
                'TEST_QUALITY','quality-hash')""",
            (now,),
        )


def test_real_v1_to_v2_migration_is_in_place_lossless_and_idempotent(tmp_path):
    path = tmp_path / "v1.db"
    _create_v1_fixture(path)
    before_size = path.stat().st_size
    upgraded = OrbResearchRepository(path, target_schema_version=2)
    status = upgraded.database_status()
    assert status["user_version"] == 2 and status["journal_mode"] == "WAL"
    with upgraded.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        row = connection.execute(
            "SELECT source_identity,universe_membership_status,operationally_eligible FROM orb_normalized_events"
        ).fetchone()
        assert tuple(row) == ("event-hash", "UNKNOWN_UNMAPPED_IDENTIFIER", 0)
        assert connection.execute("SELECT count(*) FROM orb_bars").fetchone()[0] == 1
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(orb_normalized_events)")}
        assert "idx_orb_events_eligibility" in indexes
    assert path.stat().st_size >= before_size
    assert OrbResearchRepository(path, target_schema_version=2).database_status()["user_version"] == 2


def test_interrupted_v1_to_v2_migration_rolls_back_without_deleting_fixture(tmp_path, monkeypatch):
    path = tmp_path / "v1-interrupted.db"
    _create_v1_fixture(path)
    broken = dict(repository_module.MIGRATIONS)
    broken[2] = ("broken-v2", repository_module.MIGRATION_2 + "\nINVALID SQL;")
    monkeypatch.setattr(repository_module, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        OrbResearchRepository(path, target_schema_version=2)
    assert path.exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT source_identity FROM orb_normalized_events").fetchone()[0] == "event-hash"
        columns = {row[1] for row in connection.execute("PRAGMA table_info(orb_normalized_events)")}
        assert "operationally_eligible" not in columns


def test_read_only_repository_skips_migration_and_rejects_writes(tmp_path):
    path = tmp_path / "orb.db"
    OrbResearchRepository(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    readonly = OrbResearchRepository(path, read_only=True)
    assert readonly.database_status()["integrity"] == "ok"
    with pytest.raises(RuntimeError, match="cannot write"):
        with readonly.transaction():
            pass
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_retention_trigger_prunes_raw_then_derived_in_bounded_steps(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    _create = date(2025, 1, 5)
    repository.ensure_session("old", _create, "cfg")
    first = repository.run_retention(
        evaluated_at=moment(date(2025, 2, 10), 12, 0),
        event_retention_days=30,
        derived_data_retention_days=365,
        batch_size=1,
    )
    assert first.derived_sessions_deleted == 0
    second = repository.run_retention(
        evaluated_at=moment(date(2026, 2, 10), 12, 0),
        event_retention_days=30,
        derived_data_retention_days=365,
        batch_size=1,
    )
    assert second.derived_sessions_deleted == 1


def test_exact_opening_range_versions_remain_queryable_and_duplicate_correction_is_idempotent(tmp_path):
    day = date(2026, 8, 2)
    repository = OrbResearchRepository(tmp_path / "orb.db")
    raw = tuple(
        quote(moment(day, 7, minute), price=10 + minute / 10,
              volume=100 + minute, row_id=minute)
        for minute in range(15)
    )
    service = OrbShadowIngestionService(
        repository,
        mapping_validator=lambda *_: True,
        membership_resolver=lambda *_: UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX,
        normalization_mode=NormalizationMode.HISTORICAL_REPLAY,
    )
    first = service.ingest(raw, evaluated_at=moment(day, 7, 15))
    session_id = first.session_ids[0]
    corrected = list(raw)
    corrected[-1] = replace(corrected[-1], last_price=20.0, source_row_id=99)
    restarted = OrbShadowIngestionService(
        repository,
        mapping_validator=lambda *_: True,
        membership_resolver=lambda *_: UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX,
        normalization_mode=NormalizationMode.HISTORICAL_REPLAY,
    )
    restarted.ingest(corrected, evaluated_at=moment(day, 7, 16))
    restarted.ingest(corrected, evaluated_at=moment(day, 7, 16))
    assert repository.get_opening_range_version(session_id, "AALR", 0).opening_range_high < 20
    assert repository.get_opening_range_version(session_id, "AALR", 1).opening_range_high == 20
    assert repository.get_opening_range_version(session_id, "AALR", 2) is None


def test_latency_and_historical_capabilities_survive_persistence(tmp_path):
    day = date(2026, 8, 2)
    market = moment(day, 7, 1)
    repository = OrbResearchRepository(tmp_path / "orb.db")
    service = OrbShadowIngestionService(
        repository,
        mapping_validator=lambda *_: True,
        membership_resolver=lambda *_: UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX,
        normalization_mode=NormalizationMode.HISTORICAL_REPLAY,
    )
    result = service.ingest(
        (quote(market, received=market + timedelta(seconds=120)),),
        evaluated_at=market + timedelta(minutes=3),
    )
    with repository.connect() as connection:
        event = connection.execute(
            "SELECT market_time_status,historical_replay_status,live_freshness_status FROM orb_normalized_events"
        ).fetchone()
        capability = connection.execute(
            "SELECT historical_bar_capability,live_decision_capability FROM orb_capabilities"
        ).fetchone()
    assert tuple(event) == (
        "MARKET_TIME_VALID_RECEIVE_DELAYED",
        "HISTORICAL_REPLAY_ACCEPTED",
        "LIVE_FRESHNESS_FAILED",
    )
    assert tuple(capability) == (
        "HISTORICAL_BAR_RECONSTRUCTABLE",
        "LIVE_DECISION_DISABLED_STALE_QUOTE",
    )
    assert result.symbols[0].capabilities.current_bid is None


def test_reviewed_universe_counts_keep_archived_observed_but_ineligible():
    import csv

    path = Path(
        "reports/audits/strategies/orb_first_pullback/phase2a_review/"
        "PHASE2A_SYMBOL_UNIVERSE_RECONCILIATION.csv"
    )
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    active = [row for row in rows if row["operationally_eligible_for_new_orb_entry"] == "YES"]
    archived = [
        row for row in rows
        if row["operationally_eligible_for_new_orb_entry"] == "EXIT_MONITORING_EXCEPTION_ONLY"
    ]
    assert len(active) == 225
    assert len(archived) == 40
    assert all(row["observed_in_rubix_quotes"] == "YES" for row in active + archived)
    assert not any(row["operationally_eligible_for_new_orb_entry"] == "YES" for row in archived)


def test_durable_docs_are_tracked_path_and_mutable_artifacts_remain_ignored():
    docs = Path("docs/audits/strategies/orb_first_pullback")
    assert (docs / "ORB_FIRST_PULLBACK_ARCHITECTURE_AUDIT.md").is_file()
    assert (docs / "phase2a_review/PHASE2A_INDEPENDENT_REVIEW.md").is_file()
    for candidate in (
        "data/research/example.db",
        "data/research/example.db-wal",
        "data/research/example.db-shm",
        "reports/audits/strategies/orb_first_pullback/phase2a_gate_closure/out.csv",
        "data/eodhd_cache/copied-local-cache.json",
    ):
        completed = subprocess.run(
            ["git", "check-ignore", "-q", candidate], check=False
        )
        assert completed.returncode == 0, candidate
