"""The typical stock, from the measured record — the benchmark a holder faces.

An exit rule that fires before a fall everybody had has earned nothing, and a
breakout that rose while the whole market rose has not either. Both questions
need the same number: what the median symbol did over the same sessions. It is
defined once here rather than per study, so two measurements of this project's
rules can never be scored against two different markets.

EGX30 is not that number and is not used here. It is thirty names weighted by
size, and the terminal's index history ran ten sessions behind its own stock
record on 2026-09-21 — a study benchmarked on it cannot score its most recent
evidence at all. The cross-section is exactly as fresh as the record itself.

The median, not the mean: one symbol doubling is not what the holder of
another was up against.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd

#: Below this many symbols on both ends of a window it is not a cross-section,
#: and a "median stock" built from a handful of names would be noise wearing a
#: benchmark's clothes.
MINIMUM_SYMBOLS = 30


def close_panel(database=None):
    """Every symbol the measured record holds, one column each, by session."""
    from sector_flow import measured_turnover

    path = Path(database or measured_turnover.DEFAULT_DATABASE).as_posix()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        rows = pd.read_sql(
            f"SELECT ticker, session_date, close FROM {measured_turnover.TABLE} "
            f"WHERE close > 0", connection)
    if rows.empty:
        return pd.DataFrame()
    rows["session_date"] = pd.to_datetime(rows["session_date"]).values
    return rows.pivot_table(index="session_date", columns="ticker",
                            values="close", aggfunc="last").sort_index()


def median_return(panel, session, horizon):
    """The median symbol's percent return over ``horizon`` sessions, or ``None``.

    ``None`` means the question cannot be answered yet rather than zero: the
    session is not in the record, the window has not finished, or too few
    symbols traded on both ends of it.
    """
    if panel is None or panel.empty:
        return None
    day = pd.Timestamp(session).normalize()
    position = panel.index.searchsorted(day, side="right") - 1
    if position < 0 or panel.index[position] != day:
        return None
    if position + horizon >= len(panel.index):
        return None
    start = panel.iloc[position]
    later = panel.iloc[position + horizon]
    both = start.notna() & later.notna() & (start > 0)
    if int(both.sum()) < MINIMUM_SYMBOLS:
        return None
    return float(((later[both] / start[both] - 1.0) * 100.0).median())


__all__ = ["MINIMUM_SYMBOLS", "close_panel", "median_return"]
