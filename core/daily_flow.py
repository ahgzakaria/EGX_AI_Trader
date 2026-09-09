"""Turnover as the exchange reported it, for the gates that were estimating it.

`scripts/bank_daily_flow.py` banks the MubasherTrade terminal's own daily store
into `data/daily_flow.db`. This is the read side.

The reason it exists: a turnover proxy of `close x volume` is right to within
0.44% on the median EGX name, and wrong by a factor of forty-eight on a
dollar-quoted one, because the close is in dollars while the turnover is in
pounds. Measured over the last 250 sessions, MOIL's proxy is 154,398 against a
real 6,555,994, and VLMR's is 122,474 against 5,540,511. Both clear the swing
engine's five-million floor comfortably and both were being excluded by it.

That class of error is already known here -- commit 596ecab fixed it for the
Sector Liquidity pipeline, and named the dollar-quoted symbols it exposed. The
swing liquidity gate was never revisited.

**Absent is not zero, and absent is not a licence to guess.** A symbol the bank
has never measured returns no entry, and the caller falls back to whatever it
did before rather than being handed a confident number. Eleven of the 241
universe names are not in the terminal's store at all, and a machine that has
never run the banker has none of them.

`data/measured_turnover.db` holds the same turnover for 230 symbols, imported
by hand from CSV exports and read by `sector_flow.measured_turnover`. The two
agree exactly -- turnover, volume and close identical on all 746,251 overlapping
rows -- and nothing here disturbs that path.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import statistics

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BANK_DB = PROJECT_ROOT / "data" / "daily_flow.db"

#: The window the swing gate measures liquidity over. A year of sessions, so a
#: single block day cannot promote a name and a single holiday cannot demote one.
DEFAULT_SESSIONS = 250

#: Below this a median says more about the sample than the symbol.
MINIMUM_SESSIONS = 60


def median_turnover(symbols=None, *, path: Path | None = None,
                    sessions: int = DEFAULT_SESSIONS,
                    minimum_sessions: int = MINIMUM_SESSIONS) -> dict[str, float]:
    """Median reported turnover per symbol over its last `sessions` sessions.

    Returns an empty mapping when the bank does not exist or cannot be opened,
    so a caller that has never run the banker behaves exactly as it did before.
    """

    database = Path(path) if path else BANK_DB
    if not database.is_file():
        return {}
    try:
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro", uri=True, timeout=30
        )
    except sqlite3.Error:
        return {}
    try:
        rows = connection.execute(
            "SELECT canonical_symbol, turnover FROM daily_flow "
            "WHERE turnover > 0 ORDER BY canonical_symbol, session_date DESC"
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        connection.close()

    wanted = {str(s).strip().upper() for s in symbols} if symbols else None
    gathered: dict[str, list[float]] = {}
    for symbol, turnover in rows:
        if wanted is not None and symbol not in wanted:
            continue
        seen = gathered.setdefault(symbol, [])
        if len(seen) < sessions:
            seen.append(float(turnover))

    return {symbol: statistics.median(values)
            for symbol, values in gathered.items()
            if len(values) >= minimum_sessions}


def median_trades(symbols=None, *, path: Path | None = None,
                  sessions: int = DEFAULT_SESSIONS,
                  minimum_sessions: int = MINIMUM_SESSIONS) -> dict[str, float]:
    """Median trades per session, for callers that need participation not value.

    Not used as a gate: measured across the 150 names that clear the turnover
    floor, the thinnest runs 164 trades a session and a hundred-trade minimum
    excludes none of them. It is here because the per-session figure still tells
    a reader whether one particular day could have been traded.
    """

    database = Path(path) if path else BANK_DB
    if not database.is_file():
        return {}
    try:
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro", uri=True, timeout=30
        )
    except sqlite3.Error:
        return {}
    try:
        rows = connection.execute(
            "SELECT canonical_symbol, trades FROM daily_flow "
            "WHERE trades IS NOT NULL ORDER BY canonical_symbol, session_date DESC"
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        connection.close()

    wanted = {str(s).strip().upper() for s in symbols} if symbols else None
    gathered: dict[str, list[float]] = {}
    for symbol, trades in rows:
        if wanted is not None and symbol not in wanted:
            continue
        seen = gathered.setdefault(symbol, [])
        if len(seen) < sessions:
            seen.append(float(trades))

    return {symbol: statistics.median(values)
            for symbol, values in gathered.items()
            if len(values) >= minimum_sessions}


__all__ = [
    "BANK_DB",
    "DEFAULT_SESSIONS",
    "MINIMUM_SESSIONS",
    "median_trades",
    "median_turnover",
]
