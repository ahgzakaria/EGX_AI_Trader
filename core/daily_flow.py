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


def session_trades(symbols=None, session_date=None, *,
                   path: Path | None = None) -> dict[str, int]:
    """Trades in one named session, per symbol. The day a signal fired on.

    The median over 250 sessions says whether a name is tradeable in general and
    is a poor guide to one day: inside the universe that clears the liquidity
    floor, 4.94% of sessions since 2024 carried fewer than fifty trades, and
    names that pass comfortably have had days of a single print worth a few
    hundred pounds. A signal on such a session assumes a fill that was not there.

    With no session_date the latest banked session is used. A symbol with no row
    for that date is absent, never zero -- a session nobody measured and a
    session nobody traded are different facts and only one of them is a warning.
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
        if session_date is None:
            row = connection.execute(
                "SELECT MAX(session_date) FROM daily_flow").fetchone()
            session_date = row[0] if row else None
            if session_date is None:
                return {}
        rows = connection.execute(
            "SELECT canonical_symbol, trades FROM daily_flow "
            "WHERE session_date = ? AND trades IS NOT NULL",
            (str(session_date),)).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        connection.close()

    wanted = {str(s).strip().upper() for s in symbols} if symbols else None
    return {symbol: int(trades) for symbol, trades in rows
            if wanted is None or symbol in wanted}


def trades_for(counts: dict[str, int], symbol) -> int | None:
    """This symbol's count for the session, or None when it was not measured."""

    return counts.get(str(symbol).strip().upper())


def session_buy_share(symbols=None, session_date=None, *,
                      path: Path | None = None) -> dict[str, float]:
    """Share of the session's value that was buy-initiated, per symbol.

    The exchange's own split -- CIT against COT -- not an indicator inferred
    from where the close sat inside the bar. Measured on the same breakout with
    the exit held constant, a gate on this beats every textbook money-flow
    proxy at matched selectivity: 4.27% mean against CMF's 4.00% and MFI's
    3.78%, and it moves the median trade by half a point where the proxies move
    it by none.

    **The recent sessions will often be blank.** The split lives only in the
    terminal's history store, which refreshes when the application decides to;
    the minute store that fills the recent tail carries no aggressor side, so
    those rows have none. Blank means unmeasured and must be shown as unmeasured
    -- an unknown flow is not a balanced one.
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
        if session_date is None:
            row = connection.execute(
                "SELECT MAX(session_date) FROM daily_flow "
                "WHERE buy_share IS NOT NULL").fetchone()
            session_date = row[0] if row else None
            if session_date is None:
                return {}
        rows = connection.execute(
            "SELECT canonical_symbol, buy_share FROM daily_flow "
            "WHERE session_date = ? AND buy_share IS NOT NULL",
            (str(session_date),)).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        connection.close()

    wanted = {str(s).strip().upper() for s in symbols} if symbols else None
    return {symbol: float(share) for symbol, share in rows
            if wanted is None or symbol in wanted}


def buy_share_for(shares: dict[str, float], symbol) -> float | None:
    """This symbol's buy share, or None when the session was not measured."""

    return shares.get(str(symbol).strip().upper())


__all__ = [
    "BANK_DB",
    "DEFAULT_SESSIONS",
    "MINIMUM_SESSIONS",
    "buy_share_for",
    "median_trades",
    "median_turnover",
    "session_buy_share",
    "session_trades",
    "trades_for",
]
