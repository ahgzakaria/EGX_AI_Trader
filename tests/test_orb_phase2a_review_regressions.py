"""Regressions for defects found in the independent Phase 2A review.

Every test here failed against the pre-review implementation. They deliberately
exercise the *production* Rubix condition (``sequence=None``), the boundary
microseconds, price/volume independence, restart and multi-connection paths,
and the universe-eligibility boundary.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import sqlite3
import threading
from zoneinfo import ZoneInfo

import pytest

from scalping_orb.bars import (
    aggregate_completed_five_minute_bars,
    aggregate_completed_one_minute_bars,
)
from scalping_orb.config import OrbDataConfig
from scalping_orb.events import (
    CumulativeVolumeTracker,
    RubixEventNormalizer,
    RubixQuoteInput,
    UniverseMembershipStatus,
)
from scalping_orb.opening_range import OpeningRangeStatus, build_opening_range
from scalping_orb.repository import OrbResearchRepository
from scalping_orb.session import OrbSessionClassifier, OrbSessionPhase
from scalping_orb.shadow import OrbShadowIngestionService


CAIRO = ZoneInfo("Africa/Cairo")
DAY = date(2026, 8, 2)
MICROSECOND = timedelta(microseconds=1)


def at(hour, minute, second=0, microsecond=0, *, day=DAY):
    return datetime.combine(
        day, time(hour, minute, second, microsecond), tzinfo=CAIRO
    )


def quote(
    market,
    *,
    ticker="TEST",
    price=10.0,
    volume=100.0,
    bid=9.9,
    ask=10.1,
    received=None,
    row_id=None,
    sequence=None,
):
    """A quote shaped like real Rubix rows: no provider sequence."""

    return RubixQuoteInput(
        canonical_ticker=ticker,
        verified_rubix_symbol=f"CASE~{ticker}",
        market_timestamp=market,
        receive_timestamp=received or market + timedelta(milliseconds=50),
        sequence=sequence,
        last_price=price,
        cumulative_volume=volume,
        bid=bid,
        ask=ask,
        has_feed_timestamp=True,
        source_row_id=row_id,
    )


def normalizer(config=None, *, membership=None):
    return RubixEventNormalizer(
        config or OrbDataConfig(),
        holidays=(),
        mapping_validator=lambda canonical, rubix: rubix == f"CASE~{canonical}",
        membership_resolver=membership
        or (lambda _: UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX),
    )


def service(repository, config, **kwargs):
    kwargs.setdefault("mapping_validator", lambda *_: True)
    kwargs.setdefault(
        "membership_resolver",
        lambda _: UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX,
    )
    return OrbShadowIngestionService(repository, config, holidays=(), **kwargs)


# --------------------------------------------------------------------------
# Session boundaries, to the microsecond
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("boundary", "before", "on_or_after"),
    [
        (at(10, 0), OrbSessionPhase.PRE_SESSION, OrbSessionPhase.OPENING_RANGE_BUILDING),
        (
            at(10, 15),
            OrbSessionPhase.OPENING_RANGE_BUILDING,
            OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE,
        ),
        (
            at(13, 30),
            OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE,
            OrbSessionPhase.LATE_CONTINUOUS,
        ),
        (at(14, 15), OrbSessionPhase.LATE_CONTINUOUS, OrbSessionPhase.CLOSING_AUCTION),
        (at(14, 25), OrbSessionPhase.CLOSING_AUCTION, OrbSessionPhase.POST_MARKET),
    ],
)
def test_every_boundary_is_half_open_to_the_microsecond(boundary, before, on_or_after):
    classifier = OrbSessionClassifier(holidays=())
    assert classifier.classify(boundary - MICROSECOND) == before
    assert classifier.classify(boundary) == on_or_after
    assert classifier.classify(boundary + MICROSECOND) == on_or_after


def test_late_continuous_boundary_follows_configuration_not_a_literal():
    config = OrbDataConfig(late_continuous_start=time(12, 0))
    classifier = OrbSessionClassifier(config, holidays=())
    assert (
        classifier.classify(at(12, 0) - MICROSECOND)
        == OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE
    )
    assert classifier.classify(at(12, 0)) == OrbSessionPhase.LATE_CONTINUOUS


def test_session_window_is_deterministic_across_the_egypt_dst_change():
    classifier = OrbSessionClassifier(holidays=())
    for session_date in (date(2026, 4, 23), date(2026, 4, 26), date(2026, 11, 1)):
        window = classifier.window(session_date)
        span = window.auction_end_utc - window.continuous_start_utc
        assert span == timedelta(minutes=265)
        assert window.continuous_start_utc.tzinfo == timezone.utc
        assert (
            classifier.classify(window.opening_range_end_utc - MICROSECOND)
            in {OrbSessionPhase.OPENING_RANGE_BUILDING, OrbSessionPhase.NON_TRADING_DAY}
        )


# --------------------------------------------------------------------------
# Event identity without any provider sequence
# --------------------------------------------------------------------------


def test_reconnect_redelivery_without_sequence_is_deduplicated():
    """No Rubix row carries a sequence, so payload identity must be the key."""

    engine = normalizer()
    first = quote(at(10, 1), row_id=11, received=at(10, 1, 0, 100000))
    redelivered = quote(at(10, 1), row_id=97, received=at(10, 1, 0, 900000))
    accepted, _ = engine.normalize(first, evaluated_at=at(10, 1, 1))
    repeat, issues = engine.normalize(redelivered, evaluated_at=at(10, 1, 1))
    assert accepted is not None
    assert repeat is None
    assert [item.code for item in issues] == ["DUPLICATE_MARKET_PAYLOAD_IDENTICAL"]


def test_same_timestamp_distinct_events_are_never_collapsed_without_sequence():
    engine = normalizer()
    first, _ = engine.normalize(
        quote(at(10, 1), price=10.0, volume=100), evaluated_at=at(10, 1, 1)
    )
    second, issues = engine.normalize(
        quote(at(10, 1), price=10.5, volume=140), evaluated_at=at(10, 1, 1)
    )
    assert first is not None and second is not None
    assert first.source_identity != second.source_identity
    assert not issues


def test_update_count_is_not_inflated_by_feed_redelivery():
    engine = normalizer()
    tracker = CumulativeVolumeTracker()
    events = []
    for second, price, volume in ((5, 10.0, 100), (20, 11.0, 110), (40, 10.5, 130)):
        for attempt in range(2):
            item = quote(
                at(10, 0, second),
                price=price,
                volume=volume,
                row_id=f"{second}-{attempt}",
                received=at(10, 0, second, 1000 * (attempt + 1)),
            )
            event, _ = engine.normalize(item, evaluated_at=at(10, 1))
            if event is not None:
                events.append(tracker.apply(event))
    bars = aggregate_completed_one_minute_bars(events, as_of=at(10, 1)).bars
    assert len(events) == 3
    assert bars[0].update_count == 3


def test_replaying_a_whole_batch_changes_nothing(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)
    batch = [
        quote(at(10, minute, 10), price=10 + minute / 10, volume=(minute + 1) * 10,
              row_id=minute)
        for minute in range(15)
    ]
    first = service(repository, config).ingest(batch, evaluated_at=at(10, 15))
    # A fresh service is a process restart: no in-memory dedup state survives.
    second = service(repository, config).ingest(batch, evaluated_at=at(10, 15))
    assert first.inserted_events == 15
    assert second.inserted_events == 0
    assert repository.table_count("orb_normalized_events") == 15
    assert repository.table_count("orb_bars") == 15 + 3
    quality = repository.table_count("orb_data_quality_events")
    third = service(repository, config).ingest(batch, evaluated_at=at(10, 15))
    assert third.inserted_events == 0
    assert repository.table_count("orb_data_quality_events") == quality


# --------------------------------------------------------------------------
# Price quality and volume quality are independent
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "volumes"),
    [
        ("cumulative_reset", (100.0, 100.0, 40.0)),
        ("cumulative_decrease", (100.0, 130.0, 129.0)),
        ("missing_cumulative", (100.0, 130.0, None)),
        ("duplicate_cumulative", (100.0, 130.0, 130.0)),
        ("absent_from_first_event", (None, None, None)),
    ],
)
def test_broken_volume_never_invalidates_valid_price_ohlc(label, volumes):
    engine = normalizer()
    tracker = CumulativeVolumeTracker()
    events = []
    for index, volume in enumerate(volumes):
        event, _ = engine.normalize(
            quote(at(10, 0, index * 10), price=10.0 + index, volume=volume),
            evaluated_at=at(10, 1),
        )
        events.append(tracker.apply(event))
    bars = aggregate_completed_one_minute_bars(events, as_of=at(10, 1)).bars
    assert len(bars) == 1, label
    bar = bars[0]
    assert (bar.open, bar.high, bar.low, bar.close) == (10.0, 12.0, 10.0, 12.0), label
    assert bar.volume is None, label
    assert "VOLUME_UNAVAILABLE" in bar.data_quality_flags, label


def test_volume_gap_over_sixty_seconds_degrades_volume_only():
    engine = normalizer()
    tracker = CumulativeVolumeTracker()
    events = []
    for minute, volume in ((0, 100.0), (3, 500.0)):
        event, _ = engine.normalize(
            quote(at(10, minute, 10), price=10.0 + minute, volume=volume),
            evaluated_at=at(10, 5),
        )
        events.append(tracker.apply(event))
    bars = aggregate_completed_one_minute_bars(events, as_of=at(10, 5)).bars
    assert [bar.open for bar in bars] == [10.0, 13.0]
    assert [bar.volume for bar in bars] == [None, None]
    assert all(bar.volume is None or bar.volume >= 0 for bar in bars)


def test_opening_range_price_ready_while_volume_total_stays_unavailable():
    engine = normalizer()
    tracker = CumulativeVolumeTracker()
    events = []
    for minute in range(15):
        volume = None if minute == 7 else (minute + 1) * 10.0
        event, _ = engine.normalize(
            quote(at(10, minute, 10), price=10 + minute / 10, volume=volume),
            evaluated_at=at(10, 15),
        )
        events.append(tracker.apply(event))
    bars = aggregate_completed_one_minute_bars(events, as_of=at(10, 15)).bars
    result = build_opening_range("TEST", DAY, bars, as_of=at(10, 15))
    assert result.status == OpeningRangeStatus.READY
    assert result.opening_range_high is not None
    assert result.opening_range_low is not None
    assert result.valid_volume_total is None
    assert "VOLUME_UNAVAILABLE" in result.data_quality_flags


# --------------------------------------------------------------------------
# Bar completion boundaries
# --------------------------------------------------------------------------


def test_five_minute_bar_is_unavailable_one_microsecond_before_its_close():
    engine = normalizer()
    tracker = CumulativeVolumeTracker()
    events = []
    for minute in range(15):
        event, _ = engine.normalize(
            quote(at(10, minute, 10), price=10 + minute / 10, volume=(minute + 1) * 10),
            evaluated_at=at(10, 20),
        )
        events.append(tracker.apply(event))
    one_minute = aggregate_completed_one_minute_bars(events, as_of=at(10, 20)).bars

    def starts(as_of):
        bars = aggregate_completed_five_minute_bars(one_minute, as_of=as_of).bars
        return [bar.bar_start_utc.astimezone(CAIRO).strftime("%H:%M") for bar in bars]

    assert starts(at(10, 15) - MICROSECOND) == ["10:00", "10:05"]
    assert starts(at(10, 15)) == ["10:00", "10:05", "10:10"]


def test_five_minute_bars_never_bridge_pre_session_or_the_auction():
    engine = normalizer()
    tracker = CumulativeVolumeTracker()
    events = []
    minutes = [(9, 57), (9, 58), (9, 59)] + [(10, m) for m in range(5)]
    minutes += [(14, m) for m in range(10, 20)]
    for hour, minute in minutes:
        event, _ = engine.normalize(
            quote(at(hour, minute, 10), price=10.0, volume=(hour * 60 + minute) * 10),
            evaluated_at=at(14, 25),
        )
        if event is not None:
            events.append(tracker.apply(event))
    one_minute = aggregate_completed_one_minute_bars(events, as_of=at(14, 25)).bars
    five = aggregate_completed_five_minute_bars(one_minute, as_of=at(14, 25)).bars
    starts = [bar.bar_start_utc.astimezone(CAIRO).strftime("%H:%M") for bar in five]
    assert starts == ["10:00", "14:10"]
    for bar in five:
        assert bar.session_phase != OrbSessionPhase.CLOSING_AUCTION
        assert bar.bar_end_utc <= OrbSessionClassifier(holidays=()).window(
            DAY
        ).continuous_end_utc or bar.bar_start_utc >= OrbSessionClassifier(
            holidays=()
        ).window(DAY).continuous_start_utc


# --------------------------------------------------------------------------
# Capability honesty
# --------------------------------------------------------------------------


def test_auction_quotes_never_become_the_current_continuous_market(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)
    items = [
        quote(at(10, minute, 10), price=10 + minute / 10, volume=(minute + 1) * 10)
        for minute in range(15)
    ]
    items.append(
        quote(at(14, 20), price=99.0, volume=99999.0, bid=98.0, ask=100.0)
    )
    result = service(repository, config).ingest(items, evaluated_at=at(14, 25))
    capabilities = result.symbols[0].capabilities
    assert capabilities.current_bid != 98.0
    assert capabilities.current_ask != 100.0
    assert capabilities.opening_range_high is not None
    with repository.connect() as connection:
        stored = connection.execute(
            "SELECT current_bid,current_ask FROM orb_capabilities"
        ).fetchone()
    assert stored["current_bid"] != 98.0


def test_no_capability_surface_ever_names_a_proxy_vwap():
    from scalping_orb import capabilities as capability_module

    source = (
        capability_module.PriceReferenceStatus,
        capability_module.PriceReferenceCapabilities,
    )
    names = [
        name
        for item in source
        for name in (
            list(item.__members__)
            if hasattr(item, "__members__")
            else list(item.__dataclass_fields__)
        )
    ]
    for name in names:
        if "VWAP" in name.upper():
            assert "TRUE_VWAP" in name.upper(), name
        if "PROXY" in name.upper():
            assert "VWAP" not in name.upper(), name


def test_time_of_day_rvol_threshold_comes_from_configuration():
    from scalping_orb.capabilities import (
        PriceReferenceStatus,
        assess_price_reference_capabilities,
    )

    relaxed = OrbDataConfig(minimum_time_of_day_rvol_sessions=13)
    assessed = assess_price_reference_capabilities(
        opening_range=None,
        latest_event=None,
        completed_bars=(),
        intraday_history_sessions=13,
        config=relaxed,
    )
    assert (
        assessed.time_of_day_rvol_status
        == PriceReferenceStatus.TIME_OF_DAY_RVOL_HISTORY_SUFFICIENT_NOT_IMPLEMENTED
    )
    strict = assess_price_reference_capabilities(
        opening_range=None,
        latest_event=None,
        completed_bars=(),
        intraday_history_sessions=13,
        config=OrbDataConfig(),
    )
    assert (
        strict.time_of_day_rvol_status
        == PriceReferenceStatus.TIME_OF_DAY_RVOL_INSUFFICIENT_HISTORY
    )


# --------------------------------------------------------------------------
# Universe eligibility boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "eligible"),
    [
        (UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX, True),
        (UniverseMembershipStatus.ACTIVE_UNIVERSE_UNVERIFIED_RUBIX, False),
        (UniverseMembershipStatus.ARCHIVED_INACTIVE_SYMBOL, False),
        (UniverseMembershipStatus.UNKNOWN_UNMAPPED_IDENTIFIER, False),
    ],
)
def test_observed_in_rubix_is_never_by_itself_operational_eligibility(status, eligible):
    engine = normalizer(membership=lambda _: status)
    event, _ = engine.normalize(quote(at(10, 1)), evaluated_at=at(10, 1, 1))
    assert event is not None, "historical observations are always preserved"
    assert event.universe_membership_status == status
    assert event.operationally_eligible is eligible
    assert ("NOT_OPERATIONALLY_ELIGIBLE" in event.quality_flags) is not eligible


def test_archived_symbol_eligibility_survives_a_persistence_round_trip(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)
    ingestion = service(
        repository,
        config,
        membership_resolver=lambda _: UniverseMembershipStatus.ARCHIVED_INACTIVE_SYMBOL,
    )
    result = ingestion.ingest((quote(at(10, 0, 10)),), evaluated_at=at(10, 1))
    reloaded = repository.load_events(result.session_ids[0])
    assert len(reloaded) == 1
    assert (
        reloaded[0].universe_membership_status
        == UniverseMembershipStatus.ARCHIVED_INACTIVE_SYMBOL
    )
    assert reloaded[0].operationally_eligible is False
    with repository.connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM orb_normalized_events WHERE operationally_eligible=1"
        ).fetchone()[0] == 0


def test_default_membership_resolver_reads_the_authoritative_universe():
    from core.universe import active_universe, inactive_universe
    from scalping_orb.events import _default_membership_resolver

    active = next(
        record for record in active_universe() if record.has_verified_rubix_mapping
    )
    assert (
        _default_membership_resolver(active.canonical_symbol)
        == UniverseMembershipStatus.ACTIVE_UNIVERSE_VERIFIED_RUBIX
    )
    archived = next(
        record for record in inactive_universe() if record.has_verified_rubix_mapping
    )
    assert (
        _default_membership_resolver(archived.canonical_symbol)
        == UniverseMembershipStatus.ARCHIVED_INACTIVE_SYMBOL
    )
    assert (
        _default_membership_resolver("NOT_A_REAL_TICKER")
        == UniverseMembershipStatus.UNKNOWN_UNMAPPED_IDENTIFIER
    )


# --------------------------------------------------------------------------
# Frozen opening-range versioning
# --------------------------------------------------------------------------


def test_late_correction_is_stored_beside_the_untouched_frozen_range(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)
    repository.ensure_session("session", DAY, "config")

    def range_for(high_bump: float):
        engine = normalizer()
        tracker = CumulativeVolumeTracker()
        events = []
        for minute in range(15):
            price = 10 + minute / 10 + (high_bump if minute == 3 else 0)
            event, _ = engine.normalize(
                quote(at(10, minute, 10), price=price, volume=(minute + 1) * 10),
                evaluated_at=at(10, 15),
            )
            events.append(tracker.apply(event))
        bars = aggregate_completed_one_minute_bars(events, as_of=at(10, 15)).bars
        return build_opening_range("TEST", DAY, bars, as_of=at(10, 15))

    original = range_for(0.0)
    corrected = range_for(5.0)
    assert original.status == corrected.status == OpeningRangeStatus.READY
    assert original.source_identity != corrected.source_identity

    repository.insert_opening_range("session", original)
    revision = repository.record_opening_range_revision("session", corrected)
    assert revision == 1
    # The same correction seen again must not create an endless revision chain.
    assert repository.record_opening_range_revision("session", corrected) is None

    frozen = repository.get_frozen_opening_range("session", "TEST")
    assert frozen.opening_range_high == original.opening_range_high
    assert frozen.source_identity == original.source_identity
    versions = repository.load_opening_range_revisions("session", "TEST")
    assert [(rev, is_frozen) for rev, is_frozen, _ in versions] == [(0, 1), (1, 0)]
    assert versions[1][2] == corrected.source_identity


def test_shadow_ingestion_persists_the_corrected_range_not_only_an_audit_note(tmp_path):
    """A later correction must be recoverable as data, not only as a hash string."""

    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)

    def batch(bump: float, row_offset: int):
        return [
            quote(
                at(10, minute, 10),
                price=10 + minute / 10 + (bump if minute == 3 else 0),
                volume=(minute + 1) * 10,
                row_id=row_offset + minute,
            )
            for minute in range(15)
        ]

    first = service(repository, config).ingest(batch(0.0, 0), evaluated_at=at(10, 15))
    assert first.symbols[0].opening_range.status == OpeningRangeStatus.READY
    frozen_high = first.symbols[0].opening_range.opening_range_high

    # A corrected replay of the same window, seen by a restarted process.
    later = service(repository, config).ingest(batch(5.0, 500), evaluated_at=at(10, 16))
    session_id = later.session_ids[0]

    assert later.symbols[0].opening_range.opening_range_high == frozen_high
    versions = repository.load_opening_range_revisions(session_id, "TEST")
    assert [revision for revision, _, _ in versions] == [0, 1]
    assert [is_frozen for _, is_frozen, _ in versions] == [1, 0]
    with repository.connect() as connection:
        stored = connection.execute(
            """SELECT revision,opening_range_high FROM orb_opening_ranges
               WHERE session_id=? AND canonical_ticker='TEST' ORDER BY revision""",
            (session_id,),
        ).fetchall()
    assert stored[0]["opening_range_high"] == frozen_high
    assert stored[1]["opening_range_high"] > frozen_high


def test_only_one_frozen_range_can_exist_per_symbol_and_session(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.ensure_session("session", DAY, "config")
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO orb_opening_ranges VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("id-0", "session", "TEST", DAY.isoformat(), 0, "READY", 11.0, 9.0, 10.0,
             2.0, 20.0, 15, 15, 1.0, None, None, None, "[]", None, "aa", 1, "now"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO orb_opening_ranges VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("id-1", "session", "TEST", DAY.isoformat(), 1, "READY", 12.0, 9.0,
                 10.5, 3.0, 28.0, 15, 15, 1.0, None, None, None, "[]", None, "bb",
                 1, "now"),
            )


# --------------------------------------------------------------------------
# Database safety
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "rubix_live_market.db",
        "rubix_bridge_provenance.db",
        "scalping.db",
        "scalping_historical_watchlists.db",
        "uptrend_pullback_watchlists.db",
        "expected_range_paper_state.db",
        "forward_testing.db",
        "decision_support.db",
        "normalized_daily_cache.db",
        "market_data_cache.sqlite",
        "tickerchart_quotes.sqlite3",
    ],
)
def test_every_known_production_database_name_is_refused(tmp_path, name):
    with pytest.raises(ValueError, match="protected/production"):
        OrbResearchRepository(tmp_path / name)


def test_an_existing_foreign_database_is_never_migrated(tmp_path):
    foreign = tmp_path / "someones_other_research.db"
    with sqlite3.connect(foreign) as connection:
        connection.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, qty REAL)")
        connection.execute("INSERT INTO positions VALUES (1, 42.0)")
    before = foreign.read_bytes()
    with pytest.raises(ValueError, match="without ORB tables"):
        OrbResearchRepository(foreign)
    assert foreign.read_bytes() == before


def test_a_non_database_file_is_never_overwritten(tmp_path):
    csv_path = tmp_path / "paper_trades_copy.db"
    csv_path.write_text("ticker,qty\nAALR,100\n", encoding="utf-8")
    before = csv_path.read_bytes()
    with pytest.raises(ValueError, match="non-SQLite"):
        OrbResearchRepository(csv_path)
    assert csv_path.read_bytes() == before


def test_traversal_cannot_reach_a_protected_name(tmp_path):
    with pytest.raises(ValueError, match="protected/production"):
        OrbResearchRepository(tmp_path / "research" / ".." / "rubix_live_market.db")


def test_foreign_keys_are_enforced_on_every_connection(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    for _ in range(3):
        with repository.connect() as connection:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO orb_capabilities "
                    "(capability_id,session_id,canonical_ticker,assessed_at_utc,"
                    "opening_range_high,opening_range_low,current_bid,current_ask,"
                    "spread_status,volume_status,true_vwap_status,"
                    "bar_weighted_typical_price_proxy,proxy_status,"
                    "time_of_day_rvol_status,intraday_history_sessions) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("cap", "missing-session", "TEST", "now", None, None, None, None,
                     "SPREAD_UNAVAILABLE", "VOLUME_UNAVAILABLE",
                     "TRUE_VWAP_UNAVAILABLE", None,
                     "BAR_WEIGHTED_PRICE_PROXY_UNAVAILABLE",
                     "TIME_OF_DAY_RVOL_INSUFFICIENT_HISTORY", 0),
                )


def test_concurrent_readers_do_not_block_or_observe_a_partial_write(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.ensure_session("session", DAY, "config")
    observed: list[str] = []
    failures: list[str] = []
    release = threading.Event()

    def writer():
        try:
            with repository.transaction() as connection:
                connection.execute(
                    "UPDATE orb_sessions SET status='DONE' WHERE session_id='session'"
                )
                release.set()
                threading.Event().wait(0.25)
        except Exception as error:  # pragma: no cover - surfaced through failures
            failures.append(repr(error))

    def reader():
        try:
            release.wait(2)
            with repository.connect() as connection:
                observed.append(
                    connection.execute(
                        "SELECT status FROM orb_sessions WHERE session_id='session'"
                    ).fetchone()[0]
                )
        except Exception as error:  # pragma: no cover - surfaced through failures
            failures.append(repr(error))

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not failures
    assert observed == ["COLLECTING"], "WAL readers must not see an uncommitted write"
    with repository.connect() as connection:
        assert connection.execute(
            "SELECT status FROM orb_sessions WHERE session_id='session'"
        ).fetchone()[0] == "DONE"


def test_a_second_writer_waits_rather_than_failing_immediately(tmp_path):
    repository = OrbResearchRepository(tmp_path / "orb.db")
    repository.ensure_session("session", DAY, "config")
    failures: list[str] = []
    started = threading.Event()

    def holder():
        with repository.transaction() as connection:
            connection.execute("UPDATE orb_sessions SET status='A'")
            started.set()
            threading.Event().wait(0.4)

    def contender():
        started.wait(2)
        try:
            with repository.transaction() as connection:
                connection.execute("UPDATE orb_sessions SET status='B'")
        except Exception as error:
            failures.append(repr(error))

    threads = [threading.Thread(target=holder), threading.Thread(target=contender)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert not failures, "busy_timeout must absorb single-writer contention"
    with repository.connect() as connection:
        assert connection.execute("SELECT status FROM orb_sessions").fetchone()[0] == "B"


def test_retention_is_bounded_executable_and_keeps_derived_evidence(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)
    batch = [
        quote(at(10, minute, 10), price=10 + minute / 10, volume=(minute + 1) * 10)
        for minute in range(15)
    ]
    service(repository, config).ingest(batch, evaluated_at=at(10, 15))
    bars_before = repository.table_count("orb_bars")
    cutoff = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert repository.prune_normalized_events_before(cutoff, 5) == 5
    assert repository.table_count("orb_normalized_events") == 10
    assert repository.prune_normalized_events_before(cutoff, 100) == 10
    assert repository.prune_normalized_events_before(cutoff, 100) == 0
    assert repository.table_count("orb_bars") == bars_before


# --------------------------------------------------------------------------
# Shadow ingestion safety
# --------------------------------------------------------------------------


def test_shadow_module_carries_no_collector_auth_or_order_surface():
    import inspect

    from scalping_orb import shadow

    source = inspect.getsource(shadow).lower()
    for forbidden in (
        "websocket",
        "password",
        "api_key",
        "token",
        "login",
        "authenticate",
        "place_order",
        "submit_order",
        "buy(",
        "sell(",
    ):
        assert forbidden not in source, forbidden
    assert not hasattr(shadow, "start_collector")


def test_shadow_never_writes_outside_its_own_research_database(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "research" / "orb.db"))
    sentinels = {}
    for name in ("rubix_live_market_copy.db", "scalping_copy.db", "paper_trades.csv"):
        path = tmp_path / name
        path.write_bytes(b"untouched")
        sentinels[path] = path.read_bytes()
    repository = OrbResearchRepository(config.research_database_path)
    service(repository, config).ingest(
        (quote(at(10, 0, 10)),), evaluated_at=at(10, 1)
    )
    for path, before in sentinels.items():
        assert path.read_bytes() == before
    written = {
        item.name
        for item in tmp_path.rglob("*")
        if item.is_file() and item.stat().st_mtime_ns > 0 and item.parent.name == "research"
    }
    assert written <= {"orb.db", "orb.db-wal", "orb.db-shm"}


def test_quality_events_record_failures_without_quote_payloads(tmp_path):
    config = OrbDataConfig(research_database_path=str(tmp_path / "orb.db"))
    repository = OrbResearchRepository(config.research_database_path)
    ingestion = service(repository, config, mapping_validator=lambda *_: False)
    ingestion.ingest(
        (quote(at(10, 0, 10), price=1234.56, bid=1234.0, ask=1235.0),),
        evaluated_at=at(10, 1),
    )
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT code,detail,source_identity FROM orb_data_quality_events"
        ).fetchall()
    assert rows
    for row in rows:
        blob = f"{row['code']}|{row['detail']}|{row['source_identity']}"
        assert "1234.56" not in blob
        assert "1235.0" not in blob
