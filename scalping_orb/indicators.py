"""Pure intraday indicators over completed ORB bars.

Deliberately small, dependency-free and side-effect-free. These operate on
``CompletedBar`` tuples only, never on a DataFrame, a provider or a database,
so the research engine stays pure.

Two rules the whole module obeys:

* **Completed bars only.** A caller must not pass a partial bar; there is no
  path that consults a bar ending after the evaluation instant.
* **Never fabricate.** Below the configured warm-up the result is
  ``ATR_UNAVAILABLE`` with a ``None`` value. Daily ATR is separate D-1 context
  and is never substituted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from scalping_orb.bars import CompletedBar


class IntradayAtrStatus(str, Enum):
    AVAILABLE = "INTRADAY_ATR_AVAILABLE"
    WARMING_UP = "INTRADAY_ATR_WARMING_UP"
    UNAVAILABLE = "ATR_UNAVAILABLE"


@dataclass(frozen=True)
class IntradayAtr:
    """An ATR reading that always states how trustworthy it is."""

    status: IntradayAtrStatus
    value: float | None
    interval_minutes: int
    lookback_bars: int
    observed_bars: int

    @property
    def available(self) -> bool:
        return self.status is IntradayAtrStatus.AVAILABLE and self.value is not None


def _valid_ohlc(bar: CompletedBar) -> bool:
    return (
        bar.completed
        and bar.open > 0
        and bar.high > 0
        and bar.low > 0
        and bar.close > 0
        and bar.high >= max(bar.open, bar.low, bar.close)
        and bar.low <= min(bar.open, bar.high, bar.close)
    )


def true_ranges(bars: Sequence[CompletedBar]) -> tuple[float, ...]:
    """Wilder true range. The first bar uses its own high-low only."""

    values: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        span = bar.high - bar.low
        if previous_close is not None:
            span = max(
                span,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        values.append(span)
        previous_close = bar.close
    return tuple(values)


def intraday_atr(
    bars: Iterable[CompletedBar],
    *,
    interval_minutes: int,
    lookback_bars: int,
    minimum_bars: int,
) -> IntradayAtr:
    """Simple-average true range over the most recent completed bars.

    A simple mean rather than Wilder smoothing: with a warm-up as short as six
    bars, a seeded recursive average would carry more of its seed than of the
    session, which would be a fabricated reading in disguise.
    """

    ordered = [
        bar
        for bar in sorted(bars, key=lambda item: item.bar_start_utc)
        if bar.interval_minutes == interval_minutes and _valid_ohlc(bar)
    ]
    observed = len(ordered)
    if observed < max(1, minimum_bars):
        return IntradayAtr(
            status=IntradayAtrStatus.WARMING_UP if observed else IntradayAtrStatus.UNAVAILABLE,
            value=None,
            interval_minutes=interval_minutes,
            lookback_bars=lookback_bars,
            observed_bars=observed,
        )
    window = ordered[-lookback_bars:] if lookback_bars > 0 else ordered
    ranges = true_ranges(window)
    if not ranges:
        return IntradayAtr(
            status=IntradayAtrStatus.UNAVAILABLE,
            value=None,
            interval_minutes=interval_minutes,
            lookback_bars=lookback_bars,
            observed_bars=observed,
        )
    average = sum(ranges) / len(ranges)
    if average <= 0:
        return IntradayAtr(
            status=IntradayAtrStatus.UNAVAILABLE,
            value=None,
            interval_minutes=interval_minutes,
            lookback_bars=lookback_bars,
            observed_bars=observed,
        )
    return IntradayAtr(
        status=IntradayAtrStatus.AVAILABLE,
        value=average,
        interval_minutes=interval_minutes,
        lookback_bars=lookback_bars,
        observed_bars=observed,
    )


def exponential_moving_average(
    bars: Iterable[CompletedBar], *, period: int, interval_minutes: int
) -> float | None:
    """Closing EMA over completed bars, or ``None`` before the period fills.

    Seeded with the simple mean of the first ``period`` closes, which is the
    conventional deterministic seed and avoids over-weighting one bar.
    """

    if period < 1:
        raise ValueError("period must be positive")
    closes = [
        bar.close
        for bar in sorted(bars, key=lambda item: item.bar_start_utc)
        if bar.interval_minutes == interval_minutes and _valid_ohlc(bar)
    ]
    if len(closes) < period:
        return None
    multiplier = 2.0 / (period + 1)
    average = sum(closes[:period]) / period
    for close in closes[period:]:
        average = (close - average) * multiplier + average
    return average


__all__ = [
    "IntradayAtr",
    "IntradayAtrStatus",
    "exponential_moving_average",
    "intraday_atr",
    "true_ranges",
]
