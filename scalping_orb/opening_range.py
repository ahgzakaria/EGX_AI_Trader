"""Frozen 10:00–10:15 opening-range construction from completed 1m bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
from typing import Iterable

from scalping_orb.bars import CompletedBar
from scalping_orb.config import OrbDataConfig
from scalping_orb.session import OrbSessionClassifier, OrbSessionPhase, require_aware


class OpeningRangeStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    BUILDING = "BUILDING"
    READY = "READY"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    INVALID_DATA = "INVALID_DATA"
    AUCTION_CONTAMINATION_REJECTED = "AUCTION_CONTAMINATION_REJECTED"


@dataclass(frozen=True)
class OpeningRangeResult:
    canonical_ticker: str
    session_date: date
    status: OpeningRangeStatus
    opening_range_high: float | None
    opening_range_low: float | None
    opening_range_mid: float | None
    width_absolute: float | None
    width_percent: float | None
    completed_one_minute_bar_count: int
    expected_one_minute_bar_count: int
    coverage_ratio: float
    valid_volume_total: float | None
    first_valid_timestamp_utc: datetime | None
    last_valid_timestamp_utc: datetime | None
    data_quality_flags: tuple[str, ...]
    frozen_at_utc: datetime | None
    source_identity: str | None


@dataclass(frozen=True)
class OpeningRangeRevision:
    canonical_ticker: str
    session_date: date
    detected_at_utc: datetime
    original_source_identity: str
    revised_source_identity: str
    code: str = "FROZEN_OPENING_RANGE_REVISION_DETECTED"


def _bar_identity(bars: Iterable[CompletedBar]) -> str | None:
    identities = sorted(bar.source_identity for bar in bars)
    if not identities:
        return None
    return hashlib.sha256("\n".join(identities).encode("ascii")).hexdigest()


def build_opening_range(
    canonical_ticker: str,
    session_date: date,
    one_minute_bars: Iterable[CompletedBar],
    *,
    as_of: datetime,
    config: OrbDataConfig | None = None,
) -> OpeningRangeResult:
    cfg = config or OrbDataConfig()
    classifier = OrbSessionClassifier(cfg, holidays=())
    evaluated = require_aware(as_of, "as_of").astimezone(timezone.utc)
    window = classifier.window(session_date)
    ticker = str(canonical_ticker).strip().upper()
    expected_count = cfg.opening_range_minutes
    if evaluated < window.continuous_start_utc:
        return OpeningRangeResult(
            ticker, session_date, OpeningRangeStatus.NOT_STARTED,
            None, None, None, None, None, 0, expected_count, 0.0, None,
            None, None, (), None, None,
        )

    candidates = [
        bar
        for bar in one_minute_bars
        if bar.canonical_ticker == ticker
        and bar.session_date == session_date
        and bar.interval_minutes == 1
        and window.continuous_start_utc
        <= bar.bar_start_utc
        < window.opening_range_end_utc
    ]
    flags: list[str] = []
    if any(
        bar.session_phase == OrbSessionPhase.CLOSING_AUCTION for bar in candidates
    ):
        return OpeningRangeResult(
            ticker, session_date,
            OpeningRangeStatus.AUCTION_CONTAMINATION_REJECTED,
            None, None, None, None, None, len(candidates), expected_count,
            len(candidates) / expected_count, None, None, None,
            ("AUCTION_CONTAMINATION",), None, _bar_identity(candidates),
        )

    by_slot: dict[datetime, CompletedBar] = {}
    duplicate_slot = False
    for bar in sorted(candidates, key=lambda item: item.bar_start_utc):
        if bar.bar_start_utc in by_slot:
            duplicate_slot = True
        by_slot[bar.bar_start_utc] = bar
    expected_slots = tuple(
        window.continuous_start_utc + timedelta(minutes=index)
        for index in range(expected_count)
    )
    valid = [
        by_slot[slot]
        for slot in expected_slots
        if slot in by_slot
        and by_slot[slot].completed
        and by_slot[slot].bar_end_utc <= evaluated
    ]
    coverage = len(valid) / expected_count if expected_count else 0.0
    if duplicate_slot:
        flags.append("DUPLICATE_OPENING_RANGE_SLOT")
    missing = expected_count - len(valid)
    if missing:
        flags.append(f"MISSING_OPENING_RANGE_BARS:{missing}")
    if evaluated < window.opening_range_end_utc:
        return OpeningRangeResult(
            ticker, session_date, OpeningRangeStatus.BUILDING,
            None, None, None, None, None, len(valid), expected_count, coverage,
            None,
            valid[0].bar_start_utc if valid else None,
            valid[-1].bar_end_utc if valid else None,
            tuple(flags), None, _bar_identity(valid),
        )

    if duplicate_slot:
        status = OpeningRangeStatus.INVALID_DATA
    elif coverage < cfg.opening_range_minimum_bar_coverage:
        status = OpeningRangeStatus.INSUFFICIENT_COVERAGE
    elif any(
        bar.open <= 0
        or bar.high <= 0
        or bar.low <= 0
        or bar.close <= 0
        or bar.high < max(bar.open, bar.low, bar.close)
        or bar.low > min(bar.open, bar.high, bar.close)
        for bar in valid
    ):
        status = OpeningRangeStatus.INVALID_DATA
        flags.append("INVALID_OPENING_RANGE_OHLC")
    else:
        status = OpeningRangeStatus.READY

    if status != OpeningRangeStatus.READY:
        return OpeningRangeResult(
            ticker, session_date, status,
            None, None, None, None, None, len(valid), expected_count, coverage,
            None,
            valid[0].bar_start_utc if valid else None,
            valid[-1].bar_end_utc if valid else None,
            tuple(dict.fromkeys(flags)), None, _bar_identity(valid),
        )

    high = max(bar.high for bar in valid)
    low = min(bar.low for bar in valid)
    mid = (high + low) / 2
    width = high - low
    width_percent = width / mid * 100 if mid > 0 else None
    if all(bar.volume is not None for bar in valid):
        volume = sum(float(bar.volume) for bar in valid)
    else:
        volume = None
        flags.append("VOLUME_UNAVAILABLE")
    flags.extend(flag for bar in valid for flag in bar.data_quality_flags)
    return OpeningRangeResult(
        canonical_ticker=ticker,
        session_date=session_date,
        status=status,
        opening_range_high=high,
        opening_range_low=low,
        opening_range_mid=mid,
        width_absolute=width,
        width_percent=width_percent,
        completed_one_minute_bar_count=len(valid),
        expected_one_minute_bar_count=expected_count,
        coverage_ratio=coverage,
        valid_volume_total=volume,
        first_valid_timestamp_utc=valid[0].bar_start_utc,
        last_valid_timestamp_utc=valid[-1].bar_end_utc,
        data_quality_flags=tuple(dict.fromkeys(flags)),
        frozen_at_utc=evaluated,
        source_identity=_bar_identity(valid),
    )


class OpeningRangeRegistry:
    """Keep the first READY result immutable and surface later revisions."""

    def __init__(self):
        self._frozen: dict[tuple[str, date], OpeningRangeResult] = {}

    def observe(
        self, result: OpeningRangeResult, *, detected_at: datetime
    ) -> tuple[OpeningRangeResult, OpeningRangeRevision | None]:
        key = (result.canonical_ticker, result.session_date)
        original = self._frozen.get(key)
        if original is None:
            if result.status == OpeningRangeStatus.READY:
                self._frozen[key] = result
            return result, None
        if result.status != OpeningRangeStatus.READY:
            return original, None
        if (
            result.source_identity == original.source_identity
            and result.opening_range_high == original.opening_range_high
            and result.opening_range_low == original.opening_range_low
        ):
            return original, None
        return original, OpeningRangeRevision(
            canonical_ticker=result.canonical_ticker,
            session_date=result.session_date,
            detected_at_utc=require_aware(detected_at).astimezone(timezone.utc),
            original_source_identity=str(original.source_identity),
            revised_source_identity=str(result.source_identity),
        )

    def seed(self, result: OpeningRangeResult) -> None:
        """Prime restart-safe state from the repository without replacing a freeze."""

        if result.status != OpeningRangeStatus.READY:
            return
        key = (result.canonical_ticker, result.session_date)
        self._frozen.setdefault(key, result)

    def get(self, canonical_ticker: str, session_date: date) -> OpeningRangeResult | None:
        return self._frozen.get((str(canonical_ticker).strip().upper(), session_date))
