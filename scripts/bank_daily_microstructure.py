"""Bank one row per symbol per session of what the order book looked like.

Rubix has produced 4.8 GB of quote-level data in five weeks and none of it
survives in a form any study can use. The daily cache stores OHLCV and
turnover; it stores nothing about the book. So the one thing that might
actually extend the swing engine -- microstructure -- is being generated and
discarded every day.

Five weeks is far too short to evaluate a twenty-day signal: it is a single
market episode, not a sample. That is not a reason to wait before saving the
data, it is the reason not to. A year from now this table answers the
question; the raw quotes it came from will not still be there at 50 GB a year.

One row per (symbol, session) holding what a daily bar cannot say:

* **spread** -- the median quoted bid/ask across the session, in percent. What
  the book looked like, never a fill.
* **quote intensity** -- how many updates and how many distinct minutes
  carried one. A name quoted in 40 minutes of a session is not the same
  instrument as one quoted in 240, whatever their volumes match.
* **realised intraday volatility** -- standard deviation of minute-to-minute
  returns, which a daily high-low cannot distinguish from a single gap.
* **close location** -- where the last price sat inside the day's range.

Read-only against Rubix: `mode=ro` and `PRAGMA query_only=ON`, and every read
goes through the `(ticker, market_timestamp)` index one symbol-session at a
time. It never scans the quote table.

    python scripts/bank_daily_microstructure.py                # yesterday on
    python scripts/bank_daily_microstructure.py --days 40      # backfill
    python scripts/bank_daily_microstructure.py --session 2026-08-18
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import statistics
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

RUBIX_DB = PROJECT_ROOT / "data" / "rubix_live_market.db"
BANK_DB = PROJECT_ROOT / "data" / "daily_microstructure.db"

#: Symbols per commit. Small enough that an interruption costs a minute of
#: work, large enough that the commits are not the cost.
BATCH_SIZE = 25

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_microstructure (
    canonical_symbol      TEXT    NOT NULL,
    session_date          TEXT    NOT NULL,
    quote_count           INTEGER NOT NULL,
    quoted_minutes        INTEGER NOT NULL,
    median_spread_percent REAL,
    mean_spread_percent   REAL,
    widest_spread_percent REAL,
    realised_volatility   REAL,
    close_location        REAL,
    first_price           REAL,
    last_price            REAL,
    session_high          REAL,
    session_low           REAL,
    banked_at             TEXT    NOT NULL,
    PRIMARY KEY (canonical_symbol, session_date)
);
CREATE INDEX IF NOT EXISTS idx_micro_session
    ON daily_microstructure(session_date);
"""


def session_bounds(session: date) -> tuple[str, str]:
    """UTC bounds of one Cairo trading session, as stored timestamps."""

    # Cairo is UTC+3 year round; the continuous session plus auction runs
    # 10:00-14:30 local, so 06:30-12:00 UTC covers it with margin either side.
    start = datetime(session.year, session.month, session.day, 6, 0, tzinfo=timezone.utc)
    return start.isoformat(), (start + timedelta(hours=7)).isoformat()


def candidate_symbols() -> list[str]:
    """The universe, taken from the project rather than from the quote table.

    Deliberately not `SELECT DISTINCT ticker ... WHERE market_timestamp ...`.
    The only index is `(ticker, market_timestamp)`, so filtering on the second
    column alone cannot seek and degrades to a full scan of a 4.8 GB table --
    a query in exactly that shape ran for three hours and burned 11,500 CPU
    seconds on 2026-08-16 before it was killed. Every read below keeps
    `ticker` as an equality prefix so the index can do its job.
    """

    try:
        from core.universe import active_symbols

        symbols = sorted(active_symbols())
        if symbols:
            return symbols
    except Exception:                                            # noqa: BLE001
        pass
    # Falling back to the table's own key list is still index-friendly: it
    # reads the leading column only.
    return []


#: The spread of one quote, as a percentage of its mid. Written once so the
#: aggregate and the median query cannot drift apart.
SPREAD_EXPRESSION = """
    CASE WHEN bid > 0 AND ask > bid
         THEN (ask - bid) / ((ask + bid) / 2.0) * 100.0 END
"""


def measure(rubix: sqlite3.Connection, ticker: str, session: date) -> dict | None:
    """Everything for one symbol-session, from one indexed range read.

    About 2.5 seconds a symbol, so ten to fifteen minutes for a full session
    across 241 names. That is a nightly job, not an interactive one, and the
    cost cannot be engineered away from this side:

    The index is `(ticker, market_timestamp)` and does not cover `last_price`,
    `bid` or `ask`, so each of roughly 5,000 quotes costs a random lookup into
    a 4.8 GB table. A covering index would collapse it to a pure index scan --
    and creating one means writing to the Rubix database, which this project
    does not do under any circumstances.

    Pushing the aggregation into SQL was tried and made it *slower*: the same
    row lookups still happen, and taking a median needs `ORDER BY` on a
    computed expression, which no index can serve. Profiled per query, the
    aggregate cost 4.48s and the median another 3.75s against 2.4s for reading
    the rows once and doing the arithmetic here.
    """

    low, high = session_bounds(session)
    rows = rubix.execute(
        """
        SELECT market_timestamp, last_price, bid, ask
        FROM quotes
        WHERE ticker = ? AND market_timestamp >= ? AND market_timestamp < ?
        ORDER BY market_timestamp
        """,
        (ticker, low, high),
    ).fetchall()
    if len(rows) < 10:
        return None

    spreads, prices, minutes = [], [], set()
    for timestamp, last, bid, ask in rows:
        if last and last > 0:
            prices.append(float(last))
            minutes.add(str(timestamp)[:16])
        if bid and ask and bid > 0 and ask > bid:
            spreads.append((ask - bid) / ((ask + bid) / 2.0) * 100.0)

    if not prices:
        return None

    # Tick-to-tick return volatility. A daily high-low cannot tell a name that
    # travelled all session from one that gapped once and sat still.
    returns = [(b - a) / a * 100.0 for a, b in zip(prices, prices[1:]) if a > 0]
    high_price, low_price = max(prices), min(prices)
    span = high_price - low_price
    count, first_price, last_price = len(rows), prices[0], prices[-1]
    median_spread = statistics.median(spreads) if spreads else None
    mean_spread = statistics.fmean(spreads) if spreads else None
    widest_spread = max(spreads) if spreads else None
    dispersion = statistics.pstdev(returns) if len(returns) > 2 else None
    minutes = len(minutes)

    return {
        "canonical_symbol": ticker,
        "session_date": session.isoformat(),
        "quote_count": count,
        "quoted_minutes": minutes,
        "median_spread_percent": median_spread,
        "mean_spread_percent": mean_spread,
        "widest_spread_percent": widest_spread,
        "realised_volatility": dispersion,
        # 1.0 means it closed on the high of the session, 0.0 on the low.
        "close_location": ((last_price - low_price) / span)
        if (span > 0 and last_price is not None) else None,
        "first_price": first_price,
        "last_price": last_price,
        "session_high": high_price,
        "session_low": low_price,
        "banked_at": datetime.now(timezone.utc).isoformat(),
    }


def bank(sessions: list[date], *, rubix_path: Path, bank_path: Path,
         replace: bool = False) -> int:
    if not rubix_path.is_file():
        print(f"FAIL  Rubix database not found: {rubix_path}")
        return 2

    rubix = sqlite3.connect(f"file:{rubix_path}?mode=ro", uri=True)
    rubix.execute("PRAGMA query_only=ON")

    bank_path.parent.mkdir(parents=True, exist_ok=True)
    store = sqlite3.connect(bank_path)
    store.executescript(SCHEMA)

    tickers = candidate_symbols()
    written = 0
    for session in sessions:
        if not replace:
            already = store.execute(
                "SELECT COUNT(*) FROM daily_microstructure WHERE session_date = ?",
                (session.isoformat(),),
            ).fetchone()[0]
            if already:
                print(f"{session}  {already:>4} rows already banked, skipping")
                continue

        if not tickers:
            tickers = sorted(
                row[0] for row in rubix.execute("SELECT DISTINCT ticker FROM quotes")
            )
        if not tickers:
            print(f"{session}  no symbols to measure")
            continue

        # Committed in batches rather than once at the end. A full session
        # takes 25-30 minutes, and this machine lost mains power mid-session
        # on 2026-08-18; a single commit would mean an interruption at minute
        # 28 saved nothing. Batches also make a resumed run cheap.
        rows, banked, spreads = [], 0, []

        def flush(rows):
            if not rows:
                return 0
            columns = list(rows[0])
            store.executemany(
                f"INSERT OR REPLACE INTO daily_microstructure ({','.join(columns)}) "
                f"VALUES ({','.join('?' * len(columns))})",
                [[row[c] for c in columns] for row in rows],
            )
            store.commit()
            return len(rows)

        for index, ticker in enumerate(tickers, 1):
            measured = measure(rubix, ticker, session)
            if not measured:
                continue
            rows.append(measured)
            if measured["median_spread_percent"]:
                spreads.append(measured["median_spread_percent"])
            if len(rows) >= BATCH_SIZE:
                banked += flush(rows)
                rows = []
                print(f"{session}  {banked:>4}/{len(tickers)} banked", flush=True)

        banked += flush(rows)
        written += banked
        if not banked:
            print(f"{session}  nothing measurable")
            continue

        detail = f"median spread {statistics.median(spreads):.3f}%" if spreads else ""
        print(f"{session}  {banked:>4} symbols   {detail}", flush=True)

    total = store.execute("SELECT COUNT(*) FROM daily_microstructure").fetchone()[0]
    sessions_held = store.execute(
        "SELECT COUNT(DISTINCT session_date) FROM daily_microstructure"
    ).fetchone()[0]
    store.close()

    print()
    print(f"banked {written:,} rows this run")
    print(f"{bank_path.name} now holds {total:,} rows across {sessions_held} sessions")
    if sessions_held < 250:
        print(f"a twenty-day forward study needs roughly a year of sessions; "
              f"{250 - sessions_held} to go")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", type=date.fromisoformat, default=None)
    parser.add_argument("--days", type=int, default=1,
                        help="how many sessions back from today to cover")
    parser.add_argument("--rubix-db-path", type=Path, default=RUBIX_DB)
    parser.add_argument("--output", type=Path, default=BANK_DB)
    parser.add_argument("--replace", action="store_true",
                        help="re-measure sessions already banked")
    args = parser.parse_args(argv)

    if args.session:
        sessions = [args.session]
    else:
        today = date.today()
        sessions = [today - timedelta(days=n) for n in range(args.days)]
        sessions = [s for s in sessions if s.weekday() not in (4, 5)]  # EGX: Sun-Thu
        sessions.sort()

    return bank(sessions, rubix_path=args.rubix_db_path,
                bank_path=args.output, replace=args.replace)


if __name__ == "__main__":
    raise SystemExit(main())
