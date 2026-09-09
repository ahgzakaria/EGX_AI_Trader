r"""Bank the four daily columns only MubasherTrade has, straight from its store.

`data/measured_turnover.db` was built on 2026-09-07 out of 230 CSVs exported by
hand from the MubasherTrade terminal. That export dialog offers OHLCV and
turnover and nothing else, so four columns the application already holds were
left on the floor:

* **NOTR** -- trades in the session. Turnover alone cannot tell 5M EGP in three
  prints from 5M EGP in three hundred, and the swing engine's liquidity floor
  is the gate that decides whether a backtest bought what a person could not.
* **VWAP** -- the exchange's own, rather than one recomputed from a daily bar.
* **CIT / COT** -- the value bought into and sold out of, which is the only
  signed order flow anywhere in this project. Neither EODHD nor the Rubix feed
  carries an aggressor side.

Reading `history.db` directly reproduces the hand-built import exactly: turnover,
volume and close match on **746,251 of 746,251 rows**, so this is a drop-in for
a manual step rather than a second opinion. It also covers 578 symbols instead
of 230.

**The trap, encoded here rather than rediscovered.** The cash-flow columns exist
on every row back to 2003 but carry nothing before mid-2009: every such row puts
the whole turnover on one side, which reads as a day of pure selling and is
really a day of no measurement. Coverage runs 0% through 2008, 59% in 2009 and
78-95% from 2010. `buy_share` is therefore NULL unless both sides are positive,
and a study that treats the absent ones as zero builds a bucket out of missing
data -- which is exactly what happened the first time this was measured.

Mubasher complements EODHD, it does not replace it: it is missing 11 of the 241
names in the universe, and `history.db` refreshes when the terminal decides to
rather than every session. Nothing here should become the only supplier of a
daily bar.

    python scripts/bank_daily_flow.py                  # everything not yet banked
    python scripts/bank_daily_flow.py --since 2020-01-01
    python scripts/bank_daily_flow.py --rebuild
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.universe import canonical  # noqa: E402

BANK_DB = PROJECT_ROOT / "data" / "daily_flow.db"

#: The MubasherTrade PRO store. The account folder is discovered rather than
#: written down: it is the user's account number and differs per install.
ARCHIVE_ROOT = (Path.home() / "AppData" / "Roaming" / "MubasherTrade"
                / "PRO Egypt" / "UserData")
HISTORY_LEAF = Path("History") / "CASE" / "history.db"

#: Relative slack allowed before the in/out split is called inconsistent with
#: the total it should reproduce. Measured over 26,436 rows the identity held
#: exactly, so anything outside floating-point noise is a real disagreement.
IDENTITY_TOLERANCE = 1e-6

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_flow (
    canonical_symbol  TEXT    NOT NULL,
    session_date      TEXT    NOT NULL,
    close             REAL    NOT NULL,
    volume            REAL    NOT NULL,
    turnover          REAL    NOT NULL,
    trades            INTEGER,
    vwap              REAL,
    average_trade     REAL,
    buy_value         REAL,
    sell_value        REAL,
    buy_volume        REAL,
    sell_volume       REAL,
    buy_trades        INTEGER,
    sell_trades       INTEGER,
    buy_share         REAL,
    flow_measured     INTEGER NOT NULL,
    identity_holds    INTEGER,
    banked_at         TEXT    NOT NULL,
    PRIMARY KEY (canonical_symbol, session_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_flow_session ON daily_flow(session_date);
"""


def history_database(explicit=None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"History database not found: {path}")
        return path
    if ARCHIVE_ROOT.is_dir():
        for account in sorted(ARCHIVE_ROOT.iterdir()):
            candidate = account / HISTORY_LEAF
            if candidate.is_file():
                return candidate
    raise SystemExit(
        f"No MubasherTrade history store found under {ARCHIVE_ROOT}. "
        "Open the terminal once, or pass --history."
    )


def read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=60
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def as_session(day) -> str | None:
    """20260907 -> 2026-09-07, or None for anything that is not a date."""

    text = str(day).strip()
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def measure_row(close, volume, turnover, trades, vwap,
                cash_in, cash_out, size_in, size_out, count_in, count_out) -> dict | None:
    """One session of one symbol, with the split reported only when it is real."""

    close = number(close); volume = number(volume); turnover = number(turnover)
    if close is None or close <= 0 or volume is None or turnover is None:
        return None
    if volume < 0 or turnover < 0:
        return None

    trades = number(trades); vwap = number(vwap)
    cash_in = number(cash_in); cash_out = number(cash_out)
    size_in = number(size_in); size_out = number(size_out)
    count_in = number(count_in); count_out = number(count_out)

    # Both sides strictly positive, or there was no split to record. A zero on
    # one side is the pre-2009 placeholder, not a session nobody bought into.
    measured = (cash_in is not None and cash_out is not None
                and cash_in > 0 and cash_out > 0)
    share = cash_in / (cash_in + cash_out) if measured else None

    identity = None
    if measured and turnover > 0:
        identity = int(abs((cash_in + cash_out) - turnover)
                       <= max(1.0, turnover * IDENTITY_TOLERANCE))

    return {
        "close": close,
        "volume": volume,
        "turnover": turnover,
        "trades": int(trades) if trades is not None and trades >= 0 else None,
        "vwap": vwap if vwap is not None and vwap > 0 else None,
        "average_trade": (turnover / trades
                          if trades is not None and trades > 0 else None),
        "buy_value": cash_in if measured else None,
        "sell_value": cash_out if measured else None,
        "buy_volume": size_in if measured and size_in is not None and size_in >= 0 else None,
        "sell_volume": size_out if measured and size_out is not None and size_out >= 0 else None,
        "buy_trades": (int(count_in) if measured and count_in is not None
                       and count_in >= 0 else None),
        "sell_trades": (int(count_out) if measured and count_out is not None
                        and count_out >= 0 else None),
        "buy_share": share,
        "flow_measured": int(measured),
        "identity_holds": identity,
    }


def read_symbol(history: sqlite3.Connection, table: str, since: str | None):
    """Every usable session for one symbol table, newest ordering irrelevant."""

    try:
        rows = history.execute(
            f"SELECT DATE, CLS, VOL, TOVR, NOTR, VWAP, CIT, CIV, CITR, "
            f"COT, COV, COTR FROM [{table}]"
        ).fetchall()
    except sqlite3.Error:
        return
    for (day, close, volume, turnover, trades, vwap,
         cash_in, size_in, count_in, cash_out, size_out, count_out) in rows:
        session = as_session(day)
        if session is None or (since and session < since):
            continue
        measured = measure_row(close, volume, turnover, trades, vwap,
                               cash_in, cash_out, size_in, size_out,
                               count_in, count_out)
        if measured:
            yield session, measured


def open_bank(path: Path = BANK_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60)
    connection.executescript(SCHEMA)
    return connection


def bank(history_path: Path, bank_db: sqlite3.Connection, since=None) -> dict:
    history = read_only(history_path)
    tables = [r[0] for r in history.execute(
        "SELECT name FROM sqlite_master WHERE type='table'") if r[0].startswith("_")]
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    counts = {"symbols": 0, "rows": 0, "with_flow": 0, "identity_failures": 0}
    for table in tables:
        symbol = canonical(table[1:])
        if not symbol:
            continue
        payload = []
        for session, row in read_symbol(history, table, since):
            payload.append((
                symbol, session, row["close"], row["volume"], row["turnover"],
                row["trades"], row["vwap"], row["average_trade"],
                row["buy_value"], row["sell_value"], row["buy_volume"],
                row["sell_volume"], row["buy_trades"], row["sell_trades"],
                row["buy_share"], row["flow_measured"], row["identity_holds"],
                stamp,
            ))
            counts["rows"] += 1
            counts["with_flow"] += row["flow_measured"]
            if row["identity_holds"] == 0:
                counts["identity_failures"] += 1
        if payload:
            counts["symbols"] += 1
            bank_db.executemany(
                "INSERT OR REPLACE INTO daily_flow VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
            bank_db.commit()
    history.close()
    return counts


def main(argv=None) -> int:
    args = parse_args(argv)
    history_path = history_database(args.history)
    bank_db = open_bank(Path(args.bank))

    since = args.since
    if not since and not args.rebuild:
        row = bank_db.execute("SELECT MAX(session_date) FROM daily_flow").fetchone()
        since = row[0] if row and row[0] else None

    print(f"reading {history_path}")
    print(f"  from {since or 'the beginning'}")
    counts = bank(history_path, bank_db, since)

    print(f"\n{counts['rows']:,} symbol-sessions banked across "
          f"{counts['symbols']} symbols")
    print(f"  carrying a real buy/sell split: {counts['with_flow']:,} "
          f"({counts['with_flow'] * 100 / max(1, counts['rows']):.1f}%)")
    if counts["identity_failures"]:
        print(f"  in + out did NOT reproduce the total: "
              f"{counts['identity_failures']:,} rows")

    total = bank_db.execute("SELECT COUNT(*) FROM daily_flow").fetchone()[0]
    span = bank_db.execute(
        "SELECT MIN(session_date), MAX(session_date) FROM daily_flow").fetchone()
    print(f"\n{total:,} rows in {args.bank}  ({span[0]} .. {span[1]})")
    bank_db.close()
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", default=None,
                        help="path to MubasherTrade history.db")
    parser.add_argument("--bank", default=str(BANK_DB))
    parser.add_argument("--since", help="only sessions on or after YYYY-MM-DD")
    parser.add_argument("--rebuild", action="store_true",
                        help="re-read everything rather than continuing")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
