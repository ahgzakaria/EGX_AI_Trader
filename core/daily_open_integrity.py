"""Decide, from evidence alone, whether a daily `Open` column was observed.

Measured 2026-08-25 across every daily source on disk: in
`data/market_data_cache.sqlite` 97.4% of Yahoo opens and 96.3% of Rubix opens
are exactly the previous close, and 26.2% of bars carry an open outside their
own `[low, high]` — 31.87% among bars that actually traded. The raw provider
CSVs in `data/yahoo_cache` are already 98.4% carried forward before any local
ingestion, so the field arrives fabricated rather than being corrupted here.

The control is the same quantity rebuilt from traded minutes, where the open is
the first traded minute by construction: **12.9%** carry-forward and **0.0%**
out-of-range. That is what an honest EGX daily open looks like, and it is what
separates a thin market from a fabricated column.

This module never fetches, never repairs, and never substitutes a value. It
reports what the column is, so a caller can refuse to use it rather than
silently consume a constant. A carried-forward open is not a small open.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: Above this share of opens equal to the prior close, the column is carried
#: forward rather than thin. The honest rate measured from traded minutes is
#: 12.9%, and every fabricated source on disk sits above 72%, so the gap either
#: side of this threshold is wide.
CARRY_FORWARD_LIMIT = 0.20

#: An open outside its own bar cannot have traded. Any occurrence is proof, so
#: the tolerance is for float noise only.
OUT_OF_RANGE_LIMIT = 0.0

#: Below this many transitions the rates are not worth reading.
MINIMUM_OBSERVATIONS = 20


class OpenIntegrity(str, Enum):
    """What the evidence says about a daily `Open` column."""

    #: Consistent with an observed opening auction.
    OBSERVED = "OBSERVED"
    #: Opens equal the prior close far more often than a real market produces.
    CARRIED_FORWARD = "CARRIED_FORWARD"
    #: At least one open lies outside its own bar. Impossible; proof of fabrication.
    OUT_OF_RANGE = "OUT_OF_RANGE"
    #: Too few usable rows to judge. Not a pass.
    UNKNOWN = "UNKNOWN"


#: Verdicts under which the `Open` column must not be used for analysis.
UNUSABLE = frozenset({
    OpenIntegrity.CARRIED_FORWARD,
    OpenIntegrity.OUT_OF_RANGE,
    OpenIntegrity.UNKNOWN,
})


@dataclass(frozen=True)
class OpenIntegrityReport:
    verdict: OpenIntegrity
    observations: int
    carry_forward_rate: float | None
    out_of_range_rate: float | None
    reason: str

    @property
    def usable(self) -> bool:
        return self.verdict not in UNUSABLE


def _column(frame, *names):
    for name in names:
        if name in frame:
            return frame[name]
    return None


def classify_open(frame) -> OpenIntegrityReport:
    """Classify the `Open` column of a daily OHLC frame.

    `frame` is anything with Open/High/Low/Close columns under either
    capitalisation — a pandas DataFrame, or a mapping of sequences.
    """
    opens = _column(frame, "Open", "open")
    highs = _column(frame, "High", "high")
    lows = _column(frame, "Low", "low")
    closes = _column(frame, "Close", "close")
    if opens is None or highs is None or lows is None or closes is None:
        return OpenIntegrityReport(
            OpenIntegrity.UNKNOWN, 0, None, None,
            "frame has no Open/High/Low/Close columns",
        )

    rows = []
    for o, h, l, c in zip(list(opens), list(highs), list(lows), list(closes)):
        try:
            o, h, l, c = float(o), float(h), float(l), float(c)
        except (TypeError, ValueError):
            rows.append(None)
            continue
        if min(o, h, l, c) <= 0 or any(v != v for v in (o, h, l, c)):
            rows.append(None)
            continue
        rows.append((o, h, l, c))

    carried = out_of_range = observations = 0
    for previous, current in zip(rows, rows[1:]):
        if previous is None or current is None:
            continue
        observations += 1
        open_, high, low, _close = current
        if abs(open_ - previous[3]) < 1e-9:
            carried += 1
        if open_ > high + 1e-9 or open_ < low - 1e-9:
            out_of_range += 1

    if observations < MINIMUM_OBSERVATIONS:
        return OpenIntegrityReport(
            OpenIntegrity.UNKNOWN, observations, None, None,
            f"only {observations} usable transitions; "
            f"{MINIMUM_OBSERVATIONS} needed to judge",
        )

    carry_rate = carried / observations
    range_rate = out_of_range / observations

    # Checked first: an out-of-range open is proof, not an estimate.
    if range_rate > OUT_OF_RANGE_LIMIT:
        return OpenIntegrityReport(
            OpenIntegrity.OUT_OF_RANGE, observations, carry_rate, range_rate,
            f"{out_of_range} of {observations} opens lie outside their own "
            f"[low, high] and cannot have traded",
        )
    if carry_rate > CARRY_FORWARD_LIMIT:
        return OpenIntegrityReport(
            OpenIntegrity.CARRIED_FORWARD, observations, carry_rate, range_rate,
            f"{carry_rate:.1%} of opens equal the prior close, against "
            f"{CARRY_FORWARD_LIMIT:.0%} tolerated and 12.9% measured from "
            f"traded minutes",
        )
    return OpenIntegrityReport(
        OpenIntegrity.OBSERVED, observations, carry_rate, range_rate,
        f"{carry_rate:.1%} carry-forward, no out-of-range opens",
    )
