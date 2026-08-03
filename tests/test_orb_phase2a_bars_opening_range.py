from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from scalping_orb.bars import (
    CompletedBar,
    aggregate_completed_five_minute_bars,
    aggregate_completed_one_minute_bars,
)
from scalping_orb.config import OrbDataConfig
from scalping_orb.events import CumulativeVolumeTracker, RubixEventNormalizer
from scalping_orb.opening_range import (
    OpeningRangeRegistry,
    OpeningRangeStatus,
    build_opening_range,
)
from scalping_orb.session import OrbSessionPhase
from tests.test_orb_phase2a_session_events import CAIRO, TRADING_DAY, at, raw


def enriched(raw_events, config=None):
    cfg = config or OrbDataConfig()
    engine = RubixEventNormalizer(
        cfg,
        holidays=(),
        mapping_validator=lambda canonical, rubix: rubix == f"CASE~{canonical}",
    )
    batch = engine.normalize_many(
        raw_events, evaluated_at=max(item.receive_timestamp for item in raw_events)
    )
    return CumulativeVolumeTracker(cfg).apply_many(batch.events)


def test_one_minute_ohlc_update_count_and_volume():
    items = [
        raw(at(9, 59, 50), sequence=1, price=9, volume=0),
        raw(at(10, 0, 5), sequence=2, price=10, volume=5),
        raw(at(10, 0, 10), sequence=3, price=12, volume=8),
        raw(at(10, 0, 20), sequence=4, price=9, volume=10),
        raw(at(10, 0, 40), sequence=5, price=11, volume=15),
    ]
    result = aggregate_completed_one_minute_bars(
        enriched(items), as_of=at(10, 1)
    )
    assert len(result.bars) == 1
    bar = result.bars[0]
    assert (bar.open, bar.high, bar.low, bar.close) == (10, 12, 9, 11)
    assert bar.volume == 15
    assert bar.update_count == 4
    assert bar.completed is True


def test_partial_one_minute_bar_is_excluded():
    items = [raw(at(9, 59, 50), sequence=1, volume=0), raw(at(10, 0, 10), sequence=2, volume=5)]
    result = aggregate_completed_one_minute_bars(
        enriched(items), as_of=at(10, 0, 30)
    )
    assert result.bars == ()
    assert "PARTIAL_ONE_MINUTE_BAR_EXCLUDED" in {
        item.code for item in result.quality_events
    }


def test_no_forward_fill_for_missing_minute():
    items = [
        raw(at(9, 59, 50), sequence=1, volume=0),
        raw(at(10, 0, 10), sequence=2, volume=5),
        raw(at(10, 2, 10), sequence=3, volume=5),
    ]
    result = aggregate_completed_one_minute_bars(
        enriched(items), as_of=at(10, 3)
    )
    assert [bar.bar_start_utc.astimezone(CAIRO).minute for bar in result.bars] == [0, 2]


def test_auction_event_never_enters_continuous_bar():
    items = [raw(at(14, 15, 1), sequence=1, volume=100)]
    result = aggregate_completed_one_minute_bars(
        enriched(items), as_of=at(14, 16)
    )
    assert result.bars == ()
    assert result.quality_events[0].code == "AUCTION_EVENT_EXCLUDED"


def five_source_events():
    items = [raw(at(9, 59, 50), sequence=1, volume=0)]
    for minute in range(5):
        items.append(
            raw(
                at(10, minute, 10),
                sequence=minute + 2,
                price=10 + minute,
                volume=(minute + 1) * 10,
            )
        )
    return items


def test_five_minute_bar_uses_five_completed_one_minute_components():
    one = aggregate_completed_one_minute_bars(
        enriched(five_source_events()), as_of=at(10, 5)
    ).bars
    result = aggregate_completed_five_minute_bars(one, as_of=at(10, 5))
    assert len(result.bars) == 1
    bar = result.bars[0]
    assert bar.component_bar_count == 5
    assert (bar.open, bar.high, bar.low, bar.close) == (10, 14, 10, 14)
    assert bar.volume == 50


def test_five_minute_bar_rejects_missing_component():
    one = list(
        aggregate_completed_one_minute_bars(
            enriched(five_source_events()), as_of=at(10, 5)
        ).bars
    )
    del one[2]
    result = aggregate_completed_five_minute_bars(one, as_of=at(10, 5))
    assert result.bars == ()
    assert result.quality_events[0].code == "INCOMPLETE_FIVE_MINUTE_COMPONENTS"


def test_partial_five_minute_bar_is_excluded_without_future_leakage():
    one = aggregate_completed_one_minute_bars(
        enriched(five_source_events()), as_of=at(10, 5)
    ).bars
    result = aggregate_completed_five_minute_bars(one, as_of=at(10, 4, 59))
    assert result.bars == ()
    assert result.quality_events[0].code == "PARTIAL_FIVE_MINUTE_BAR_EXCLUDED"


def opening_bars(*, volume=True):
    start = at(10, 0).astimezone(timezone.utc)
    bars = []
    for minute in range(15):
        price = 10 + minute / 10
        bars.append(
            CompletedBar(
                canonical_ticker="TEST",
                interval_minutes=1,
                session_date=TRADING_DAY,
                bar_start_utc=start + timedelta(minutes=minute),
                bar_end_utc=start + timedelta(minutes=minute + 1),
                open=price,
                high=price + 0.2,
                low=price - 0.1,
                close=price + 0.1,
                volume=float(minute + 1) if volume else None,
                update_count=2,
                first_sequence=minute * 2,
                last_sequence=minute * 2 + 1,
                data_quality_flags=() if volume else ("VOLUME_UNAVAILABLE",),
                completed=True,
                session_phase=OrbSessionPhase.OPENING_RANGE_BUILDING,
                source_identity=f"{minute:064x}",
            )
        )
    return bars


def test_opening_range_not_ready_before_final_bar_completion():
    result = build_opening_range(
        "TEST", TRADING_DAY, opening_bars(), as_of=at(10, 14, 59)
    )
    assert result.status == OpeningRangeStatus.BUILDING
    assert result.completed_one_minute_bar_count == 14


def test_opening_range_ready_at_1015_with_exact_high_low():
    result = build_opening_range(
        "TEST", TRADING_DAY, opening_bars(), as_of=at(10, 15)
    )
    assert result.status == OpeningRangeStatus.READY
    assert result.opening_range_high == pytest.approx(11.6)
    assert result.opening_range_low == pytest.approx(9.9)
    assert result.completed_one_minute_bar_count == 15
    assert result.coverage_ratio == 1


def test_opening_range_insufficient_coverage():
    result = build_opening_range(
        "TEST", TRADING_DAY, opening_bars()[:-1], as_of=at(10, 15)
    )
    assert result.status == OpeningRangeStatus.INSUFFICIENT_COVERAGE
    assert result.opening_range_high is None


def test_invalid_volume_does_not_invent_total_or_invalidate_price_range():
    result = build_opening_range(
        "TEST", TRADING_DAY, opening_bars(volume=False), as_of=at(10, 15)
    )
    assert result.status == OpeningRangeStatus.READY
    assert result.valid_volume_total is None
    assert "VOLUME_UNAVAILABLE" in result.data_quality_flags


def test_opening_range_auction_contamination_is_rejected():
    bars = opening_bars()
    bars[3] = replace(bars[3], session_phase=OrbSessionPhase.CLOSING_AUCTION)
    result = build_opening_range("TEST", TRADING_DAY, bars, as_of=at(10, 15))
    assert result.status == OpeningRangeStatus.AUCTION_CONTAMINATION_REJECTED


def test_frozen_opening_range_is_immutable_and_late_revision_is_explicit():
    first = build_opening_range(
        "TEST", TRADING_DAY, opening_bars(), as_of=at(10, 15)
    )
    registry = OpeningRangeRegistry()
    frozen, revision = registry.observe(first, detected_at=at(10, 15))
    assert revision is None
    with pytest.raises(FrozenInstanceError):
        frozen.opening_range_high = 99
    revised_bars = opening_bars()
    revised_bars[0] = replace(
        revised_bars[0], high=20, source_identity="f" * 64
    )
    candidate = build_opening_range(
        "TEST", TRADING_DAY, revised_bars, as_of=at(10, 16)
    )
    still_frozen, revision = registry.observe(candidate, detected_at=at(10, 16))
    assert still_frozen.opening_range_high == first.opening_range_high
    assert revision.code == "FROZEN_OPENING_RANGE_REVISION_DETECTED"
