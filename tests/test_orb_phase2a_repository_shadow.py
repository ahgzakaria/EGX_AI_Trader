from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest
import scalping_orb.repository as repository_module

from scalping_orb.bars import CompletedBar
from scalping_orb.capabilities import (
    PriceReferenceStatus,
    assess_price_reference_capabilities,
    bar_weighted_typical_price_proxy,
)
from scalping_orb.config import OrbDataConfig
from scalping_orb.events import (
    CumulativeVolumeTracker,
    RubixEventNormalizer,
    RubixQuoteInput,
    SpreadCapability,
)
from scalping_orb.opening_range import OpeningRangeStatus, build_opening_range
from scalping_orb.repository import OrbResearchRepository
from scalping_orb.session import OrbSessionPhase
from scalping_orb.shadow import OrbShadowIngestionService


CAIRO = ZoneInfo("Africa/Cairo")
DAY = date(2026, 8, 2)


def at(hour, minute, second=0):
    return datetime.combine(DAY, time(hour, minute, second), tzinfo=CAIRO)


def raw(market, sequence, price=10.0, volume=100.0, bid=9.9, ask=10.1):
    return RubixQuoteInput(
        canonical_ticker="TEST",
        verified_rubix_symbol="CASE~TEST",
        market_timestamp=market,
        receive_timestamp=market + timedelta(milliseconds=10),
        sequence=sequence,
        last_price=price,
        cumulative_volume=volume,
        bid=bid,
        ask=ask,
        has_feed_timestamp=True,
        source_row_id=sequence,
    )


def one_event():
    config = OrbDataConfig()
    normalizer = RubixEventNormalizer(
        config,
        holidays=(),
        mapping_validator=lambda canonical, rubix: True,
    )
    event, _ = normalizer.normalize(raw(at(10, 0), 1), evaluated_at=at(10, 0, 1))
    return CumulativeVolumeTracker(config).apply(event)


def one_bar(volume=10.0):
    start = at(10, 0).astimezone(timezone.utc)
    return CompletedBar(
        canonical_ticker="TEST",
        interval_minutes=1,
        session_date=DAY,
        bar_start_utc=start,
        bar_end_utc=start + timedelta(minutes=1),
        open=10,
        high=11,
        low=9,
        close=10.5,
        volume=volume,
        update_count=2,
        first_sequence=1,
        last_sequence=2,
        data_quality_flags=(),
        completed=True,
        session_phase=OrbSessionPhase.OPENING_RANGE_BUILDING,
        source_identity="a" * 64,
    )


def test_fresh_and_repeated_migration_are_idempotent(tmp_path):
    path = tmp_path / "orb.db"
    first = OrbResearchRepository(path)
    second = OrbResearchRepository(path)
    assert first.database_status()["user_version"] == repository_module.SCHEMA_VERSION
    assert second.database_status()["integrity"] == "ok"
    with second.connect() as connection:
        applied = connection.execute(
            "SELECT version FROM orb_schema_meta ORDER BY version"
        ).fetchall()
    assert [row[0] for row in applied] == sorted(repository_module.MIGRATIONS)


def test_wal_and_foreign_keys_are_enabled(tmp_path):
    status = OrbResearchRepository(tmp_path / "orb.db").database_status()
    assert status["journal_mode"] == "WAL"
    assert status["foreign_keys"] is True


def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch):
    path = tmp_path / "orb.db"
    OrbResearchRepository(path)
    broken = dict(repository_module.MIGRATIONS)
    next_version = max(broken) + 1
    broken[next_version] = (
        "broken",
        "CREATE TABLE should_roll_back (id INTEGER);\nTHIS IS INVALID SQL;",
    )
    monkeypatch.setattr(repository_module, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        OrbResearchRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='should_roll_back'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM orb_schema_meta WHERE version=?", (next_version,)
        ).fetchone()[0] == 0


def test_repository_rejects_known_production_database_name(tmp_path):
    with pytest.raises(ValueError, match="protected/production"):
        OrbResearchRepository(tmp_path / "rubix_live_market.db")


def test_duplicate_event_and_bar_prevention(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.ensure_session("session", DAY, "config")
    event = one_event()
    assert repository.insert_events("session", (event, event)) == 1
    bar = one_bar()
    assert repository.insert_bars("session", (bar, bar)) == 1
    assert repository.table_count("orb_normalized_events") == 1
    assert repository.table_count("orb_bars") == 1


def test_late_completed_bar_revision_is_audited_not_mutated(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.ensure_session("session", DAY, "config")
    original = one_bar()
    revised = replace(original, high=12, source_identity="b" * 64)
    assert repository.insert_bars("session", (original,)) == 1
    assert repository.insert_bars("session", (revised,)) == 0
    assert repository.table_count("orb_bars") == 1
    assert repository.table_count("orb_data_quality_events") == 1


def test_transaction_rolls_back_atomically(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    with pytest.raises(RuntimeError):
        with repository.transaction() as connection:
            connection.execute(
                """INSERT INTO orb_sessions
                   (session_id,session_date,config_hash,status,created_at_utc,updated_at_utc)
                   VALUES ('rollback','2026-08-02','x','COLLECTING','x','x')"""
            )
            raise RuntimeError("inject rollback")
    assert repository.table_count("orb_sessions") == 0


def test_independent_database_does_not_touch_sentinel(tmp_path):
    sentinel = tmp_path / "scalping-original-copy.db"
    sentinel.write_bytes(b"unchanged")
    repository = OrbResearchRepository(tmp_path / "research" / "orb.db")
    repository.ensure_session("session", DAY, "config")
    assert sentinel.read_bytes() == b"unchanged"
    assert repository.path != sentinel


def test_capabilities_never_expose_true_vwap_and_proxy_is_accurately_named():
    bar = one_bar(volume=10)
    assert bar_weighted_typical_price_proxy((bar,)) == pytest.approx((11 + 9 + 10.5) / 3)
    capabilities = assess_price_reference_capabilities(
        opening_range=None,
        latest_event=None,
        completed_bars=(bar,),
        intraday_history_sessions=13,
    )
    assert capabilities.true_vwap_status == PriceReferenceStatus.TRUE_VWAP_UNAVAILABLE
    assert (
        capabilities.bar_weighted_typical_price_proxy_status
        == PriceReferenceStatus.BAR_WEIGHTED_PRICE_PROXY_AVAILABLE
    )
    assert (
        capabilities.time_of_day_rvol_status
        == PriceReferenceStatus.TIME_OF_DAY_RVOL_INSUFFICIENT_HISTORY
    )
    assert capabilities.intraday_history_sessions == 13
    assert not hasattr(capabilities, "vwap")


def test_capability_spread_unavailable_is_not_inferred():
    event = one_event()
    event = event.__class__(
        **{
            **event.__dict__,
            "bid": None,
            "spread_absolute": None,
            "spread_percent": None,
            "spread_capability": SpreadCapability.SPREAD_UNAVAILABLE,
        }
    )
    capabilities = assess_price_reference_capabilities(
        opening_range=None,
        latest_event=event,
        completed_bars=(),
        intraday_history_sessions=0,
    )
    assert capabilities.current_bid is None
    assert capabilities.spread_status == SpreadCapability.SPREAD_UNAVAILABLE


def test_shadow_service_persists_events_bars_range_and_capabilities(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)
    service = OrbShadowIngestionService(
        repository,
        config,
        holidays=(),
        mapping_validator=lambda canonical, rubix: True,
    )
    events = [raw(at(9, 59, 50), 1, volume=0)]
    events.extend(
        raw(at(10, minute, 10), minute + 2, price=10 + minute / 10, volume=(minute + 1) * 10)
        for minute in range(15)
    )
    result = service.ingest(events, evaluated_at=at(10, 15))
    assert result.accepted_events == 16
    assert len(result.one_minute_bars) == 15
    assert len(result.five_minute_bars) == 3
    assert result.symbols[0].opening_range.status == OpeningRangeStatus.READY
    assert repository.table_count("orb_normalized_events") == 16
    assert repository.table_count("orb_opening_ranges") == 1
    assert repository.table_count("orb_capabilities") == 1
    assert repository.count_intraday_sessions() == 1


def test_shadow_opening_range_building_row_can_freeze_but_not_mutate_after_ready(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)
    service = OrbShadowIngestionService(
        repository, config, holidays=(), mapping_validator=lambda *_: True
    )
    baseline = raw(at(9, 59, 50), 1, volume=0)
    first = [baseline]
    first.extend(
        raw(at(10, minute, 10), minute + 2, volume=(minute + 1) * 10)
        for minute in range(5)
    )
    building = service.ingest(first, evaluated_at=at(10, 5))
    assert building.symbols[0].opening_range.status == OpeningRangeStatus.BUILDING
    rest = [
        raw(at(10, minute, 10), minute + 2, volume=(minute + 1) * 10)
        for minute in range(5, 15)
    ]
    ready = service.ingest(rest, evaluated_at=at(10, 15))
    assert ready.symbols[0].opening_range.status == OpeningRangeStatus.READY
    frozen = repository.get_frozen_opening_range(ready.session_ids[0], "TEST")
    assert frozen.status == OpeningRangeStatus.READY


def test_shadow_service_is_event_idempotent(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)
    first_service = OrbShadowIngestionService(
        repository, config, holidays=(), mapping_validator=lambda *_: True
    )
    event = raw(at(10, 0, 10), 1)
    first = first_service.ingest((event,), evaluated_at=at(10, 1))
    second_service = OrbShadowIngestionService(
        repository, config, holidays=(), mapping_validator=lambda *_: True
    )
    second = second_service.ingest((event,), evaluated_at=at(10, 1))
    assert first.inserted_events == 1
    assert second.inserted_events == 0
    assert repository.table_count("orb_normalized_events") == 1


def test_shadow_service_persists_mapping_failure_without_cross_symbol_data(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)
    service = OrbShadowIngestionService(
        repository, config, holidays=(), mapping_validator=lambda *_: False
    )
    result = service.ingest((raw(at(10, 0), 1),), evaluated_at=at(10, 1))
    assert result.accepted_events == 0
    assert repository.table_count("orb_normalized_events") == 0
    assert repository.table_count("orb_data_quality_events") == 1


def _tiny_rubix(path: Path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE quotes (
              id INTEGER PRIMARY KEY,ticker TEXT,last_price REAL,bid REAL,ask REAL,
              volume REAL,market_timestamp TEXT,received_at TEXT,exchange TEXT,
              sequence INTEGER,change_percent REAL,has_feed_timestamp INTEGER);
            CREATE TABLE candles_1m (
              ticker TEXT,minute TEXT,open REAL,high REAL,low REAL,close REAL,
              volume REAL,updates INTEGER,PRIMARY KEY(ticker,minute));
            CREATE TABLE feed_metrics (
              id INTEGER PRIMARY KEY,observed_at TEXT,event TEXT,ticker TEXT,
              value REAL,detail TEXT);
            """
        )
        start = at(10, 0).astimezone(timezone.utc)
        for minute in range(15):
            stamp = start + timedelta(minutes=minute)
            connection.execute(
                "INSERT INTO candles_1m VALUES (?,?,?,?,?,?,?,?)",
                ("TEST", stamp.isoformat(), 10, 11, 9, 10.5, 5, 1),
            )
            connection.execute(
                "INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    minute + 1,
                    "TEST",
                    10.5,
                    10.4,
                    10.6,
                    minute * 5,
                    stamp.isoformat(),
                    (stamp + timedelta(milliseconds=10)).isoformat(),
                    "CASE",
                    minute + 1,
                    0,
                    1,
                ),
            )
