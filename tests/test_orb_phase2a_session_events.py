from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from scalping_orb.config import OrbDataConfig
from scalping_orb.events import (
    CumulativeVolumeTracker,
    RubixEventNormalizer,
    RubixQuoteInput,
    SpreadCapability,
    VolumeCapability,
    VolumeDeltaStatus,
)
from scalping_orb.session import OrbSessionClassifier, OrbSessionPhase


CAIRO = ZoneInfo("Africa/Cairo")
TRADING_DAY = date(2026, 8, 2)


def at(hour, minute, second=0, *, day=TRADING_DAY):
    return datetime.combine(day, time(hour, minute, second), tzinfo=CAIRO)


def raw(
    market,
    *,
    ticker="TEST",
    rubix="CASE~TEST",
    sequence=1,
    price=10.0,
    volume=100.0,
    bid=9.9,
    ask=10.1,
    received=None,
    row_id=None,
):
    return RubixQuoteInput(
        canonical_ticker=ticker,
        verified_rubix_symbol=rubix,
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


def normalizer(config=None):
    return RubixEventNormalizer(
        config or OrbDataConfig(),
        holidays=(),
        mapping_validator=lambda canonical, rubix: rubix == f"CASE~{canonical}",
    )


@pytest.mark.parametrize(
    ("instant", "phase"),
    [
        (at(9, 59), OrbSessionPhase.PRE_SESSION),
        (at(10, 0), OrbSessionPhase.OPENING_RANGE_BUILDING),
        (at(10, 14, 59), OrbSessionPhase.OPENING_RANGE_BUILDING),
        (at(10, 15), OrbSessionPhase.CONTINUOUS_AFTER_OPENING_RANGE),
        (at(13, 30), OrbSessionPhase.LATE_CONTINUOUS),
        (at(14, 15), OrbSessionPhase.CLOSING_AUCTION),
        (at(14, 25), OrbSessionPhase.POST_MARKET),
    ],
)
def test_cairo_session_phase_boundaries(instant, phase):
    assert OrbSessionClassifier(holidays=()).classify(instant) == phase


def test_session_classifier_converts_utc_to_cairo():
    instant = datetime(2026, 8, 2, 7, 0, tzinfo=timezone.utc)
    classifier = OrbSessionClassifier(holidays=())
    assert classifier.to_cairo(instant).hour == 10
    assert classifier.classify(instant) == OrbSessionPhase.OPENING_RANGE_BUILDING


def test_session_classifier_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        OrbSessionClassifier(holidays=()).classify(datetime(2026, 8, 2, 10, 0))


def test_non_trading_day_is_explicit():
    friday = datetime(2026, 8, 7, 11, 0, tzinfo=CAIRO)
    assert (
        OrbSessionClassifier(holidays=()).classify(friday)
        == OrbSessionPhase.NON_TRADING_DAY
    )


def test_config_is_immutable_and_validated():
    config = OrbDataConfig()
    with pytest.raises(Exception):
        config.continuous_start = time(9, 0)
    with pytest.raises(ValueError, match="strictly ordered"):
        OrbDataConfig(opening_range_end=time(14, 20))


def test_config_mapping_and_fingerprint_are_deterministic():
    config = OrbDataConfig.from_mapping(
        {"continuous_start": "10:00", "supported_bar_intervals": [1, 5]}
    )
    assert config.opening_range_minutes == 15
    assert config.fingerprint == OrbDataConfig().fingerprint


def test_normalization_retains_only_verified_typed_fields():
    event, issues = normalizer().normalize(raw(at(10, 1)), evaluated_at=at(10, 1, 1))
    assert not issues
    assert event.canonical_ticker == "TEST"
    assert event.verified_rubix_symbol == "CASE~TEST"
    assert event.market_timestamp_utc.tzinfo == timezone.utc
    assert event.spread_capability == SpreadCapability.SPREAD_AVAILABLE
    assert event.volume_capability == VolumeCapability.VOLUME_AVAILABLE
    assert event.spread_percent == pytest.approx(2.0)


def test_unverified_mapping_fails_closed():
    event, issues = normalizer().normalize(
        raw(at(10, 1), rubix="CASE~OTHER"), evaluated_at=at(10, 1, 1)
    )
    assert event is None
    assert [item.code for item in issues] == ["RUBIX_MAPPING_UNAVAILABLE"]


def test_duplicate_sequence_identical_is_dropped_idempotently():
    engine = normalizer()
    item = raw(at(10, 1), row_id=5)
    first, _ = engine.normalize(item, evaluated_at=at(10, 1, 1))
    second, issues = engine.normalize(item, evaluated_at=at(10, 1, 1))
    assert first is not None and second is None
    assert issues[0].code == "DUPLICATE_SEQUENCE_IDENTICAL"


def test_duplicate_sequence_replay_with_new_receive_time_is_still_identical():
    engine = normalizer()
    first = raw(at(10, 1), row_id=5)
    replay = replace(
        first,
        receive_timestamp=first.receive_timestamp + timedelta(seconds=1),
        source_row_id=6,
    )
    engine.normalize(first, evaluated_at=at(10, 1, 1))
    event, issues = engine.normalize(replay, evaluated_at=at(10, 1, 2))
    assert event is None
    assert issues[0].code == "DUPLICATE_SEQUENCE_IDENTICAL"


def test_duplicate_sequence_conflict_is_rejected():
    engine = normalizer()
    engine.normalize(raw(at(10, 1), price=10, row_id=1), evaluated_at=at(10, 1, 1))
    second, issues = engine.normalize(
        raw(at(10, 1), price=11, row_id=2), evaluated_at=at(10, 1, 2)
    )
    assert second is None
    assert issues[0].code == "DUPLICATE_SEQUENCE_CONFLICT"


def test_sequence_gap_is_explicit():
    engine = normalizer()
    engine.normalize(raw(at(10, 1), sequence=10), evaluated_at=at(10, 1, 1))
    event, _ = engine.normalize(raw(at(10, 2), sequence=13), evaluated_at=at(10, 2, 1))
    assert event.sequence_gap == 2
    assert "SEQUENCE_GAP" in event.quality_flags


def test_out_of_order_timestamp_is_rejected():
    engine = normalizer()
    engine.normalize(raw(at(10, 2), sequence=1), evaluated_at=at(10, 2, 1))
    event, issues = engine.normalize(
        raw(at(10, 1), sequence=2), evaluated_at=at(10, 2, 1)
    )
    assert event is None
    assert issues[0].code == "OUT_OF_ORDER_REJECTED"


def test_stale_quote_is_flagged():
    item = raw(at(10, 0))
    item = replace(item, receive_timestamp=at(10, 2))
    event, _ = normalizer().normalize(item, evaluated_at=at(10, 2))
    assert "STALE_QUOTE" in event.quality_flags


def test_missing_bid_or_ask_stays_unavailable():
    event, _ = normalizer().normalize(
        raw(at(10, 1), bid=None), evaluated_at=at(10, 1, 1)
    )
    assert event.bid is None
    assert event.spread_percent is None
    assert event.spread_capability == SpreadCapability.SPREAD_UNAVAILABLE


def test_missing_cumulative_volume_stays_unavailable():
    event, _ = normalizer().normalize(
        raw(at(10, 1), volume=None), evaluated_at=at(10, 1, 1)
    )
    assert event.cumulative_volume is None
    assert event.volume_capability == VolumeCapability.VOLUME_UNAVAILABLE


def test_symbol_state_is_isolated():
    engine = normalizer()
    one, _ = engine.normalize(raw(at(10, 1), ticker="AAA", rubix="CASE~AAA"), evaluated_at=at(10, 1, 1))
    two, _ = engine.normalize(raw(at(10, 1), ticker="BBB", rubix="CASE~BBB"), evaluated_at=at(10, 1, 1))
    assert one is not None and two is not None
    assert one.source_identity != two.source_identity


def normalized_series(items, config=None):
    engine = normalizer(config)
    output = []
    for item in items:
        event, issues = engine.normalize(item, evaluated_at=item.receive_timestamp)
        assert not issues
        output.append(event)
    return output


def test_cumulative_volume_valid_delta_and_duplicate_value():
    events = normalized_series(
        [raw(at(10, 0), sequence=1, volume=100), raw(at(10, 0, 10), sequence=2, volume=100), raw(at(10, 0, 20), sequence=3, volume=125)]
    )
    tracked = CumulativeVolumeTracker().apply_many(events)
    assert tracked[0].volume_delta_status == VolumeDeltaStatus.BASELINE_ESTABLISHED
    assert tracked[1].volume_delta == 0
    assert tracked[2].volume_delta == 25


def test_cumulative_volume_resets_by_session_date():
    events = normalized_series(
        [raw(at(10, 0), volume=100), raw(at(10, 0, day=date(2026, 8, 3)), volume=5)]
    )
    tracked = CumulativeVolumeTracker().apply_many(events)
    assert tracked[0].volume_delta is None
    assert tracked[1].volume_delta_status == VolumeDeltaStatus.BASELINE_ESTABLISHED


def test_reconnect_or_unexplained_decrease_never_creates_negative_volume():
    events = normalized_series(
        [raw(at(10, 0), sequence=1, volume=100), raw(at(10, 0, 10), sequence=2, volume=10)]
    )
    tracked = CumulativeVolumeTracker().apply_many(events)
    assert tracked[1].volume_delta is None
    assert tracked[1].volume_delta_status == VolumeDeltaStatus.CUMULATIVE_VOLUME_DECREASE


def test_sequence_gap_volume_is_unallocatable():
    events = normalized_series(
        [raw(at(10, 0), sequence=1, volume=100), raw(at(10, 0, 10), sequence=3, volume=120)]
    )
    tracked = CumulativeVolumeTracker().apply_many(events)
    assert tracked[1].volume_delta is None
    assert tracked[1].volume_delta_status == VolumeDeltaStatus.SEQUENCE_GAP_UNALLOCATABLE


def test_positive_delta_after_missing_interval_is_unallocatable():
    config = OrbDataConfig(maximum_volume_delta_gap_seconds=60)
    events = normalized_series(
        [raw(at(10, 0), sequence=1, volume=100), raw(at(10, 2), sequence=2, volume=120)],
        config,
    )
    tracked = CumulativeVolumeTracker(config).apply_many(events)
    assert tracked[1].volume_delta is None
    assert tracked[1].volume_delta_status == VolumeDeltaStatus.MISSING_INTERVAL_UNALLOCATABLE
