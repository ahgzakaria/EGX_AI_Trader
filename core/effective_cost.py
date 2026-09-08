"""What a round trip costs in a given name, read from the banked measurements.

`scripts/bank_effective_cost.py` writes one row per symbol per session: the
volume-weighted price buyers paid against the price sellers received, measured
inside five-minute buckets so intraday drift is not charged as cost. This is the
read side, shared by every page that shows a signal next to its cost.

The number is a property of the symbol rather than of the day -- splitting each
symbol's sessions in half, the first half predicts the second at r = 0.836 over
211 names -- which is what makes a per-symbol median worth carrying at all. It
still moves, so the median is taken over a recent window rather than over
everything ever banked.

**Absent is not zero.** A symbol with too few measured sessions returns no
record, and a page must show that as unknown. Filling it with a universe median
would put a confident number on the one name nobody measured, which is exactly
where a wrong cost does the most damage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import statistics

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BANK_DB = PROJECT_ROOT / "data" / "effective_cost.db"

#: The published EGX round-trip commission. The measured crossing cost is added
#: to it; neither term is useful alone.
FEES_PERCENT = 0.4638

#: Sessions a symbol needs before its median is reported. Three is low, and
#: deliberately: the measure is stable enough that a handful of sessions already
#: separates a 0.03% name from a 1.6% one, and withholding it entirely would
#: leave every page on the flat assumption it is meant to replace.
MINIMUM_SESSIONS = 3

#: How far back a median is taken. Long enough to be steady, short enough that a
#: name whose liquidity genuinely changed stops being described by last quarter.
RECENT_SESSIONS = 30


@dataclass(frozen=True)
class SymbolCost:
    """One symbol's measured cost of trading, and how well it is known."""

    symbol: str
    effective_cost_percent: float
    sessions_measured: int
    last_session: str

    @property
    def round_trip_percent(self) -> float:
        """Fees plus crossing: what a full in-and-out actually costs."""

        return FEES_PERCENT + self.effective_cost_percent

    def covers(self, expected_move_percent: float) -> bool:
        """Would a move of this size survive the round trip in this name?"""

        return expected_move_percent > self.round_trip_percent


def load_symbol_costs(
    symbols=None, *, path: Path | None = None,
    minimum_sessions: int = MINIMUM_SESSIONS,
    recent_sessions: int = RECENT_SESSIONS,
) -> dict[str, SymbolCost]:
    """Median measured cost per symbol, keyed by canonical ticker.

    Returns an empty mapping when nothing has been banked yet -- a page that
    has never run the banker should render exactly as it did before, not break.
    """

    database = Path(path) if path else BANK_DB
    if not database.is_file():
        return {}
    try:
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro", uri=True, timeout=15
        )
    except sqlite3.Error:
        return {}
    try:
        rows = connection.execute(
            "SELECT canonical_symbol, session_date, effective_cost_percent "
            "FROM effective_cost WHERE effective_cost_percent IS NOT NULL "
            "ORDER BY canonical_symbol, session_date DESC"
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        connection.close()

    wanted = {str(s).strip().upper() for s in symbols} if symbols else None
    gathered: dict[str, list[tuple[str, float]]] = {}
    for symbol, session, cost in rows:
        if wanted is not None and symbol not in wanted:
            continue
        seen = gathered.setdefault(symbol, [])
        if len(seen) < recent_sessions:
            seen.append((session, cost))

    costs = {}
    for symbol, seen in gathered.items():
        if len(seen) < minimum_sessions:
            continue
        costs[symbol] = SymbolCost(
            symbol=symbol,
            effective_cost_percent=statistics.median(cost for _, cost in seen),
            sessions_measured=len(seen),
            last_session=max(session for session, _ in seen),
        )
    return costs


def round_trip_for(costs: dict[str, SymbolCost], symbol) -> float | None:
    """The round trip for one symbol, or None when it has not been measured."""

    record = costs.get(str(symbol).strip().upper())
    return None if record is None else record.round_trip_percent


__all__ = [
    "BANK_DB",
    "FEES_PERCENT",
    "MINIMUM_SESSIONS",
    "RECENT_SESSIONS",
    "SymbolCost",
    "load_symbol_costs",
    "round_trip_for",
]
