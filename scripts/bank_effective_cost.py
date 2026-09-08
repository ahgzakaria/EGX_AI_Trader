r"""Bank one row per symbol per session of what a round trip actually costs.

Every page in the swing workspace gates on the same arithmetic: 0.4638% in fees
plus the spread. The spread term comes from `daily_microstructure`, which reads
Rubix bid/ask -- so it is the *quoted* spread, what the book showed. What a
trade pays is the *effective* cost, and the two are not the same number.

Nothing in this project could measure the second one, because measuring it needs
to know which side initiated each trade and the Rubix feed carries no aggressor
flag. The MubasherTrade Time & Sales export does: `TRANSACTIONTYPE` is 1 on an
up/flat tick and 0 on a down/flat tick -- checked across 203,766 prints on one
session with zero contradictions. So the cost is measurable as the volume
weighted price buyers paid against the price sellers received.

**Measured inside five-minute buckets, not across the day.** A whole-session
comparison mixes cost with drift: on a stock that rose all day buyers naturally
paid more on average and none of that is cost. Measured day-wide the median came
out 0.537%; bucketed it is 0.250%, so more than half of the day-wide figure was
trend. The bucketed number is also stable across every session measured
(0.229%-0.280%), which a noise measurement would not be.

Why it matters, in one line: Swing Breakout's median trade returns +0.88%, and
the round trip ranges 0.636% in the cheapest quartile of names to 0.844% in the
dearest. Same signal, six times the net, depending only on which name it fired on.

Also banked, because a per-trade archive is the only place it exists: the true
traded value (the daily contract carries OHLCV only, so turnover elsewhere is a
(H+L+C)/3 x Volume proxy) and the share of a session taken by its single largest
print. On 2026-09-07 one print was 97.5% of EFIC's 5.1 billion EGP.

Read-only against the archive, which belongs to another application: `mode=ro`
and `PRAGMA query_only=ON`. It never writes there, and it never asks the
MubasherTrade terminal to download anything -- the files it reads are the ones a
human already exported.

    python scripts/bank_effective_cost.py                 # every session not yet banked
    python scripts/bank_effective_cost.py --session 2026-09-08
    python scripts/bank_effective_cost.py --rebuild       # re-measure what is there
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import statistics
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.universe import canonical  # noqa: E402

BANK_DB = PROJECT_ROOT / "data" / "effective_cost.db"

#: The MubasherTrade PRO export tree. The account folder is discovered rather
#: than written down: it is the user's account number, it differs per install,
#: and it does not belong in a repository.
ARCHIVE_ROOT = (Path.home() / "AppData" / "Roaming" / "MubasherTrade"
                / "PRO Egypt" / "UserData")
ARCHIVE_LEAF = Path("HistoricalTrade") / "CASE"

#: Five minutes. Long enough to hold trades from both sides of the book, short
#: enough that price has little room to trend inside one -- which is the whole
#: point, since drift inside the window is measured as cost.
BUCKET_SECONDS = 300

#: Fewer buckets than this and the median is taken over too little to mean
#: anything. The row is still banked -- its volume and concentration are
#: perfectly good -- but its cost is left NULL rather than filled with a
#: number nobody should act on.
MINIMUM_BUCKETS = 6

#: The published EGX round-trip commission, carried here so a reader of this
#: table can add the two terms without going to look for the first.
FEES_PERCENT = 0.4638

SCHEMA = """
CREATE TABLE IF NOT EXISTS effective_cost (
    canonical_symbol        TEXT    NOT NULL,
    session_date            TEXT    NOT NULL,
    trades                  INTEGER NOT NULL,
    buckets_measured        INTEGER NOT NULL,
    effective_cost_percent  REAL,
    round_trip_percent      REAL,
    buy_initiated_value     REAL    NOT NULL,
    sell_initiated_value    REAL    NOT NULL,
    traded_value            REAL    NOT NULL,
    largest_print_value     REAL    NOT NULL,
    largest_print_share     REAL    NOT NULL,
    source_file             TEXT    NOT NULL,
    banked_at               TEXT    NOT NULL,
    PRIMARY KEY (canonical_symbol, session_date)
);
CREATE INDEX IF NOT EXISTS idx_effective_cost_session
    ON effective_cost(session_date);
"""


def archive_directory(explicit=None) -> Path:
    """Where the exported sessions live, or a clear failure saying they do not."""

    if explicit:
        directory = Path(explicit)
        if not directory.is_dir():
            raise SystemExit(f"Archive directory not found: {directory}")
        return directory
    if ARCHIVE_ROOT.is_dir():
        for account in sorted(ARCHIVE_ROOT.iterdir()):
            candidate = account / ARCHIVE_LEAF
            if candidate.is_dir():
                return candidate
    raise SystemExit(
        "No MubasherTrade Time & Sales export found under "
        f"{ARCHIVE_ROOT}. Export a session from the terminal, or pass --archive."
    )


def session_of(path: Path) -> str:
    """YYYYMMDD.db -> YYYY-MM-DD, or "" for a file that is not one."""

    stem = path.stem
    if len(stem) != 8 or not stem.isdigit():
        return ""
    return f"{stem[:4]}-{stem[4:6]}-{stem[6:]}"


def read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30
    )
    connection.execute("PRAGMA query_only=ON")
    return connection


def bucket_of(stamp: str) -> int | None:
    """HHMMSS -> which five-minute bucket of the day, or None if malformed."""

    if len(stamp) != 6 or not stamp.isdigit():
        return None
    seconds = int(stamp[:2]) * 3600 + int(stamp[2:4]) * 60 + int(stamp[4:])
    return seconds // BUCKET_SECONDS


def measure_session(path: Path) -> dict[str, dict]:
    """Every symbol in one exported session, keyed by canonical ticker."""

    connection = read_only(path)
    try:
        rows = connection.execute(
            "SELECT SYMBOL, TRADETIME, TRADEPRICE, TRADEQUANTITY, TRANSACTIONTYPE "
            "FROM TRADES"
        )
        buckets: dict[tuple[str, int], list[float]] = defaultdict(
            lambda: [0.0, 0.0, 0.0, 0.0]
        )
        totals: dict[str, dict] = {}
        for symbol, stamp, price_text, size_text, side in rows:
            ticker = canonical(symbol)
            if not ticker:
                continue
            try:
                price = float(price_text)
                size = float(size_text)
            except (TypeError, ValueError):
                continue
            if price <= 0 or size <= 0:
                continue
            slot = bucket_of(str(stamp))
            if slot is None:
                continue

            value = price * size
            running = totals.setdefault(ticker, {
                "trades": 0, "buy_value": 0.0, "sell_value": 0.0,
                "traded_value": 0.0, "largest_print": 0.0,
            })
            running["trades"] += 1
            running["traded_value"] += value
            running["largest_print"] = max(running["largest_print"], value)

            cell = buckets[(ticker, slot)]
            if str(side) == "1":
                cell[0] += value
                cell[1] += size
                running["buy_value"] += value
            else:
                cell[2] += value
                cell[3] += size
                running["sell_value"] += value
    finally:
        connection.close()

    spreads: dict[str, list[float]] = defaultdict(list)
    for (ticker, _), (buy_value, buy_size, sell_value, sell_size) in buckets.items():
        # A bucket with trades on one side only says nothing about the cost of
        # crossing: there is no other side to compare against.
        if buy_size <= 0 or sell_size <= 0:
            continue
        buy_vwap = buy_value / buy_size
        sell_vwap = sell_value / sell_size
        middle = (buy_vwap + sell_vwap) / 2
        if middle > 0:
            spreads[ticker].append((buy_vwap - sell_vwap) / middle * 100)

    measured = {}
    for ticker, running in totals.items():
        usable = spreads.get(ticker, [])
        cost = statistics.median(usable) if len(usable) >= MINIMUM_BUCKETS else None
        measured[ticker] = {
            "trades": running["trades"],
            "buckets_measured": len(usable),
            "effective_cost_percent": cost,
            "round_trip_percent": None if cost is None else FEES_PERCENT + cost,
            "buy_initiated_value": running["buy_value"],
            "sell_initiated_value": running["sell_value"],
            "traded_value": running["traded_value"],
            "largest_print_value": running["largest_print"],
            "largest_print_share": (
                running["largest_print"] / running["traded_value"] * 100
                if running["traded_value"] > 0 else 0.0
            ),
        }
    return measured


def open_bank(path: Path = BANK_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.executescript(SCHEMA)
    return connection


def already_banked(bank: sqlite3.Connection) -> set[str]:
    return {row[0] for row in bank.execute(
        "SELECT DISTINCT session_date FROM effective_cost")}


def bank_session(bank: sqlite3.Connection, session: str, path: Path) -> int:
    measured = measure_session(path)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    bank.executemany(
        "INSERT OR REPLACE INTO effective_cost VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(ticker, session, row["trades"], row["buckets_measured"],
          row["effective_cost_percent"], row["round_trip_percent"],
          row["buy_initiated_value"], row["sell_initiated_value"],
          row["traded_value"], row["largest_print_value"],
          row["largest_print_share"], path.name, stamp)
         for ticker, row in measured.items()],
    )
    bank.commit()
    return len(measured)


def main(argv=None) -> int:
    args = parse_args(argv)
    directory = archive_directory(args.archive)
    bank = open_bank(Path(args.bank))

    files = {session_of(p): p for p in sorted(directory.glob("*.db")) if session_of(p)}
    if not files:
        print(f"No exported sessions in {directory}")
        return 0

    if args.session:
        if args.session not in files:
            print(f"{args.session} is not in the archive. Available: "
                  f"{min(files)} .. {max(files)}")
            return 1
        wanted = [args.session]
    else:
        done = set() if args.rebuild else already_banked(bank)
        wanted = [session for session in sorted(files) if session not in done]

    if not wanted:
        print(f"Nothing to bank; {len(files)} sessions already measured.")
        return 0

    print(f"{'session':<13}{'symbols':>9}{'median cost':>13}{'round trip':>12}")
    for session in wanted:
        count = bank_session(bank, session, files[session])
        costs = [row[0] for row in bank.execute(
            "SELECT effective_cost_percent FROM effective_cost "
            "WHERE session_date=? AND effective_cost_percent IS NOT NULL",
            (session,))]
        if costs:
            middle = statistics.median(costs)
            print(f"{session:<13}{count:>9}{middle:>12.3f}%"
                  f"{FEES_PERCENT + middle:>11.3f}%")
        else:
            print(f"{session:<13}{count:>9}{'-':>13}{'-':>12}")

    total = bank.execute("SELECT COUNT(*) FROM effective_cost").fetchone()[0]
    sessions = bank.execute(
        "SELECT COUNT(DISTINCT session_date) FROM effective_cost").fetchone()[0]
    print(f"\n{total:,} symbol-sessions banked across {sessions} sessions "
          f"-> {args.bank}")
    bank.close()
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default=None,
                        help="the exported Time & Sales directory")
    parser.add_argument("--bank", default=str(BANK_DB))
    parser.add_argument("--session", help="one session, as YYYY-MM-DD")
    parser.add_argument("--rebuild", action="store_true",
                        help="re-measure sessions already banked")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
