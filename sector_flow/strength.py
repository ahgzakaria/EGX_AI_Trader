"""Sector strength measured from liquidity, not from the scan's own scores.

`decision_support.sector_analysis.sector_summary` derives a sector's strength
from the average Edge and Momentum of the scanned symbols inside it, and that
strength then feeds back into each of those symbols' Edge scores. The quantity
is therefore partly a function of itself: a sector looks strong because its
stocks scored well, and its stocks then score better because the sector looks
strong.

This module supplies an independent alternative. A sector's strength is how
much its turnover exceeds *its own* recent norm -- the RVOL already computed by
the daily history against a strictly trailing 20-session median. It is measured
from the market's traded value, so nothing in the scan can influence it.

Scale: RVOL 0.0x -> 0.00, 1.0x (a normal session) -> 0.50, 2.0x or more -> 1.00.
The mapping is linear and deliberately blunt; the ordering carries the meaning,
not the exact value.

This describes where money is moving, not whether prices will rise.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from sector_flow import DEFAULT_DATABASE, HISTORY_TABLE
from sector_flow.history import DEFAULT_MIN_COVERAGE, latest_snapshot


#: RVOL that maps to full strength. A sector trading at twice its own median.
FULL_STRENGTH_RVOL = 2.0


def rvol_to_strength(rvol):
    """Map a sector's RVOL onto the [0, 1] scale the Edge score expects."""

    if rvol is None or pd.isna(rvol):
        return None
    return max(0.0, min(1.0, float(rvol) / FULL_STRENGTH_RVOL))


def liquidity_strength(history, min_coverage=DEFAULT_MIN_COVERAGE):
    """Return ``{sector: strength}`` from the latest complete session's RVOL.

    An empty mapping means the history could not supply a measurement, and the
    caller should keep whatever it was using before rather than substitute a
    default -- a fabricated 0.5 for every sector is not neutral, it is a claim
    that every sector is average.
    """

    if history is None or getattr(history, "empty", True):
        return {}
    snapshot = latest_snapshot(history, min_coverage)
    if snapshot.empty or "RVOL" not in snapshot:
        return {}

    strengths = {}
    for _, row in snapshot.iterrows():
        strength = rvol_to_strength(row.get("RVOL"))
        if strength is not None:
            strengths[str(row["Sector"])] = strength
    return strengths


def strength_frame(history, min_coverage=DEFAULT_MIN_COVERAGE):
    """Return the measurement behind each strength, for disclosure in the UI."""

    if history is None or getattr(history, "empty", True):
        return pd.DataFrame()
    snapshot = latest_snapshot(history, min_coverage)
    if snapshot.empty:
        return pd.DataFrame()

    frame = snapshot[["SessionDate", "Sector", "TurnoverShare", "RVOL", "TurnoverZ"]].copy()
    frame["SectorStrength"] = frame["RVOL"].map(rvol_to_strength)
    return frame.sort_values("SectorStrength", ascending=False).reset_index(drop=True)


def load_latest_strength(database_path=DEFAULT_DATABASE,
                         min_coverage=DEFAULT_MIN_COVERAGE):
    """Return ``{sector: strength}`` reading only the newest complete session.

    The scan path calls this on every run, so it must not load the whole
    history. An unreadable or absent database returns an empty mapping rather
    than raising: sector evidence is advisory, and its absence must never stop
    a scan from producing rows.
    """

    query = (
        f"SELECT Sector, RVOL FROM {HISTORY_TABLE} WHERE SessionDate = "
        f"(SELECT MAX(SessionDate) FROM {HISTORY_TABLE} "
        f" WHERE COALESCE(SessionCoverage, 1) >= ?) "
        f"AND COALESCE(SessionCoverage, 1) >= ?"
    )
    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
            rows = connection.execute(query, (min_coverage, min_coverage)).fetchall()
    except sqlite3.Error:
        return {}

    strengths = {}
    for sector, rvol in rows:
        strength = rvol_to_strength(rvol)
        if strength is not None:
            strengths[str(sector)] = strength
    return strengths
