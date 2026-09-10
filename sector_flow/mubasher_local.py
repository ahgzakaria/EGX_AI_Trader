"""Read MubasherTrade PRO's own databases instead of exporting CSVs by hand.

The measured-turnover store was filled by opening the terminal's Export History
dialog, typing 240 symbols into a one-line search box, and saving 240 CSV files.
That is why it stopped on 2026-09-07: the export is a manual act, so the store
is only ever as fresh as the last time somebody sat down and did it.

The terminal keeps the same data locally, in SQLite, and updates it itself:

``History/CASE/history.db``
    The full daily record, 2003 to the last time the terminal downloaded
    history, one table per instrument. Its closes and turnover are identical to
    the CSV export -- 714,443 of 714,443 sessions agree to the cent -- because
    the export is written from this file. Reading it directly is the same data
    with no typing, and it carries columns the export drops: the trade count,
    the volume-weighted price, and the split of turnover between buyers coming
    in and sellers going out.

``Intraday/CASE/INTRADAY_MASTER.db``
    One table per symbol of minute bars for a rolling fourteen sessions, with
    turnover and trade count per minute, updated live while the terminal runs.
    It is what makes today available at all: ``history.db`` only moves when the
    terminal is told to download history, and it has not moved since the export.

Two things this file knows that are not obvious from either database:

**Neither ``history.db`` nor the CSV export contains an opening price.** The
``OP`` column is the previous session's close -- identical to ``CLS[d-1]`` in
122,719 of 126,299 rows, and the exceptions are roundings and ex-dates -- and
the exporter writes the *high* into the CSV's "Open" column, which is why all
138,148 exported rows have ``Open == High``. The only true EGX open on this
machine is the first minute bar in the intraday store. Sessions sourced from
``history.db`` therefore get no open at all rather than a plausible wrong one.

**The close is only official when the auction printed.** EGX trades
continuously to 14:15, then crosses one auction whose single price is the
official close. In the intraday store that shows up as a gap and then a block
of minutes all at one price: on 2026-09-10 COMI's last continuous trade was
138.33 and its auction was 138.17, and 163 of 191 symbols closed away from
their last continuous print. Where the intraday store holds no bar after 14:15
the last price is not the close, and reconstructing one anyway reproduces the
official close for only 30% of sessions against 99.4% when the auction is
there. Those rows are stored with ``close_confirmed = 0`` so the daily bridge
can say so instead of guessing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
import sqlite3

import pandas as pd

from sector_flow.measured_turnover import (DEFAULT_DATABASE, METADATA_TABLE,
                                           SCHEMA, TABLE)

logger = logging.getLogger(__name__)

try:                                                    # pragma: no cover
    from zoneinfo import ZoneInfo
    CAIRO = ZoneInfo("Africa/Cairo")
except Exception:                                       # pragma: no cover
    CAIRO = timezone(timedelta(hours=2))

#: Where the terminal keeps per-account data. The numeric leaf is the account
#: number, so it is globbed rather than named.
INSTALL_ROOT = Path.home() / "AppData/Roaming/MubasherTrade/PRO Egypt/UserData"

HISTORY_RELATIVE = "History/CASE/history.db"
INTRADAY_RELATIVE = "Intraday/CASE/INTRADAY_MASTER.db"

#: ``TMIN`` counts minutes from the Unix epoch in UTC. 29,817,329 is
#: 2026-09-10 11:29 UTC, which is 14:29 in Cairo -- the last minute of that
#: session's auction.
TMIN_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: Bars at or after this Cairo time belong to the closing auction. Continuous
#: trading ends at 14:15 and the cross prints from 14:25; the ten minutes
#: between are the call, and no bar appears in them.
AUCTION_FROM_MINUTE = 14 * 60 + 15

#: Instrument tables that are not equities. Bonds and treasury series carry a
#: run of four digits; the index tables carry a space or start with EGX.
NOT_EQUITY = re.compile(r"\d{4}|\s|^EGX", re.IGNORECASE)


def find_root(root=None):
    """Return the account's UserData directory, or ``None`` if not installed.

    A missing installation is not an error anywhere in this module. The store
    it fills is an enrichment, and a machine without the terminal must still be
    able to run everything else.
    """

    if root:
        candidate = Path(root)
        return candidate if candidate.is_dir() else None
    if not INSTALL_ROOT.is_dir():
        return None
    accounts = [p for p in sorted(INSTALL_ROOT.iterdir())
                if p.is_dir() and (p / HISTORY_RELATIVE).exists()]
    if not accounts:
        accounts = [p for p in sorted(INSTALL_ROOT.iterdir()) if p.is_dir()]
    return accounts[-1] if accounts else None


def _open_read_only(path, immutable=False):
    """Open a database the terminal may be writing, without writing to it.

    ``immutable=1`` is for ``history.db`` only, which the terminal touches once
    per download and not during a session. The intraday store is written
    continuously, so it is opened plain read-only and left to SQLite's locking.
    """

    uri = f"file:{Path(path).as_posix()}?mode=ro"
    if immutable:
        uri += "&immutable=1"
    return sqlite3.connect(uri, uri=True)


def _instrument_tables(connection, symbols=None):
    """Return ``(table, ticker)`` for the instruments an import should read."""

    wanted = {str(s).split(".")[0].upper() for s in symbols} if symbols else None
    chosen = []
    for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        name = row[0]
        if not name.startswith("_"):
            continue
        ticker = name[1:].upper()
        if wanted is None:
            if NOT_EQUITY.search(ticker):
                continue
        elif ticker not in wanted:
            continue
        chosen.append((name, ticker))
    return chosen


def read_history(root=None, symbols=None):
    """Return every daily bar ``history.db`` holds, as store rows.

    ``open`` is deliberately absent: the column of that name in the source is
    the previous close, and a wrong open is worse than none.
    """

    base = find_root(root)
    if base is None or not (base / HISTORY_RELATIVE).exists():
        return pd.DataFrame()

    frames = []
    with _open_read_only(base / HISTORY_RELATIVE, immutable=True) as connection:
        for table, ticker in _instrument_tables(connection, symbols):
            try:
                rows = pd.read_sql(
                    f'SELECT DATE, HIG, LOW, CLS, VOL, TOVR FROM "{table}"',
                    connection)
            except Exception as error:              # one instrument, not the file
                logger.warning("mubasher history: %s unreadable (%s)", ticker, error)
                continue
            if rows.empty:
                continue
            session = pd.to_datetime(rows["DATE"], format="%Y%m%d", errors="coerce")
            frames.append(pd.DataFrame({
                "ticker": ticker,
                "session_date": session.dt.strftime("%Y-%m-%d"),
                "turnover": pd.to_numeric(rows["TOVR"], errors="coerce"),
                "volume": pd.to_numeric(rows["VOL"], errors="coerce"),
                "open": float("nan"),
                "high": pd.to_numeric(rows["HIG"], errors="coerce"),
                "low": pd.to_numeric(rows["LOW"], errors="coerce"),
                "close": pd.to_numeric(rows["CLS"], errors="coerce"),
                # This file is what the export was written from, and the
                # export's Closed column is the auction price.
                "close_confirmed": 1,
            }))

    if not frames:
        return pd.DataFrame()
    tidy = pd.concat(frames, ignore_index=True)
    tidy = tidy[tidy["session_date"].notna() & (tidy["turnover"] > 0)]
    return tidy.drop_duplicates(subset=["ticker", "session_date"], keep="last")


def session_of(tmin):
    """Return ``(session_date, minute_of_day)`` in Cairo for a ``TMIN`` value."""

    stamp = TMIN_EPOCH + timedelta(minutes=int(tmin))
    local = stamp.astimezone(CAIRO)
    return local.strftime("%Y-%m-%d"), local.hour * 60 + local.minute


def read_intraday(root=None, symbols=None):
    """Return a daily bar per symbol-session rebuilt from the minute store.

    Volume and turnover are the sum of the minutes, which runs slightly light:
    against ``history.db`` the median session is exact and 97.4% land within
    0.5%, and it is never over -- the minute bars leave out something the daily
    record counts, most visibly on instruments that also trade a rights line.
    That is why these rows only fill sessions ``history.db`` does not have.
    """

    base = find_root(root)
    if base is None or not (base / INTRADAY_RELATIVE).exists():
        return pd.DataFrame()

    records = []
    with _open_read_only(base / INTRADAY_RELATIVE) as connection:
        for table, ticker in _instrument_tables(connection, symbols):
            try:
                rows = connection.execute(
                    f'SELECT TMIN, OP, HIG, LOW, CLS, VOL, TOVR FROM "{table}" '
                    f'ORDER BY CAST(TMIN AS INTEGER)').fetchall()
            except Exception as error:
                logger.warning("mubasher intraday: %s unreadable (%s)", ticker, error)
                continue

            sessions = {}
            for tmin, op, hig, low, cls, vol, tovr in rows:
                try:
                    day, minute = session_of(tmin)
                    bar = (float(op), float(hig), float(low), float(cls),
                           float(vol or 0), float(tovr or 0))
                except (TypeError, ValueError):
                    continue
                sessions.setdefault(day, []).append((minute, bar))

            for day, bars in sessions.items():
                opens, highs, lows, closes, vols, tovrs = zip(*(b for _, b in bars))
                auction = max(minute for minute, _ in bars) >= AUCTION_FROM_MINUTE
                records.append({
                    "ticker": ticker,
                    "session_date": day,
                    "turnover": sum(tovrs),
                    "volume": sum(vols),
                    # The first minute bar of the session, which is the only
                    # true open this machine holds.
                    "open": opens[0],
                    "high": max(highs),
                    "low": min(lows),
                    "close": closes[-1],
                    "close_confirmed": int(auction),
                })

    if not records:
        return pd.DataFrame()
    tidy = pd.DataFrame(records)
    return tidy[tidy["turnover"] > 0]


def store_symbols(database=DEFAULT_DATABASE):
    """Return the tickers the store already holds, without their suffix."""

    if not Path(database).exists():
        return set()
    try:
        with _open_read_only(database) as connection:
            rows = connection.execute(f"SELECT DISTINCT ticker FROM {TABLE}").fetchall()
    except sqlite3.Error:
        return set()
    return {str(r[0]).split(".")[0].upper() for r in rows}


def default_scope(database=DEFAULT_DATABASE):
    """Return the symbols an import should cover: the store's own scope.

    The tradeable universe plus whatever the store already holds. Widening it
    to every table in ``history.db`` would pull in indices, treasury series and
    rights lines, and sector share is a ratio of one symbol's turnover to the
    market's -- an index row would be counted as a company.
    """

    scope = store_symbols(database)
    try:
        from core.symbols import load_tradeable_symbols
        scope |= {str(s).split(".")[0].upper() for s in load_tradeable_symbols()}
    except Exception as error:                          # pragma: no cover
        logger.warning("mubasher import: universe unavailable (%s)", error)
    return scope


def import_local(root=None, database=DEFAULT_DATABASE, suffix=".CA", symbols=None):
    """Refresh the measured store from the terminal's own databases.

    ``history.db`` is authoritative wherever it reaches; the minute store fills
    only the sessions after it, which is the part that used to require the
    export. Returns the metadata row it wrote, or raises if neither database
    produced a usable row -- a store silently replaced by nothing is the one
    outcome worth failing on.
    """

    base = find_root(root)
    if base is None:
        raise FileNotFoundError(
            "MubasherTrade PRO data not found; pass --root with the UserData folder")

    scope = ({str(s).split(".")[0].upper() for s in symbols} if symbols
             else default_scope(database))
    history = read_history(base, symbols=scope)
    intraday = read_intraday(base, symbols=scope)

    if history.empty and intraday.empty:
        raise ValueError(f"No usable rows in {base}")

    if history.empty:
        stored = filled = intraday
    elif intraday.empty:
        stored, filled = history, intraday
    else:
        known = set(zip(history["ticker"], history["session_date"]))
        fresh = [pair not in known for pair
                 in zip(intraday["ticker"], intraday["session_date"])]
        filled = intraday[fresh]
        stored = pd.concat([history, filled], ignore_index=True)

    stored = stored.sort_values(["ticker", "session_date"])
    stored = stored.drop_duplicates(subset=["ticker", "session_date"], keep="last")
    stored = stored.assign(ticker=stored["ticker"] + suffix)

    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        # Dropped rather than emptied, for the same reason import_directory
        # drops: a store written before close_confirmed existed would keep its
        # old columns and reject every row with "no column named
        # close_confirmed".
        connection.execute(f"DROP TABLE IF EXISTS {TABLE}")
        connection.executescript(SCHEMA)
        stored.to_sql(TABLE, connection, if_exists="append", index=False)

    unconfirmed = stored[stored["close_confirmed"] == 0]
    metadata = {
        "imported_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source": "mubasher_local",
        "source_root": str(base),
        "symbols": int(stored["ticker"].nunique()),
        "sessions": int(stored["session_date"].nunique()),
        "rows": int(len(stored)),
        "first_session": str(stored["session_date"].min()),
        "last_session": str(stored["session_date"].max()),
        "history_rows": int(len(history)),
        "history_last_session": (None if history.empty
                                 else str(history["session_date"].max())),
        "intraday_rows_appended": int(len(filled)),
        "intraday_sessions_appended": (sorted(set(filled["session_date"]))
                                       if len(filled) else []),
        "unconfirmed_close_rows": int(len(unconfirmed)),
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            f"INSERT OR REPLACE INTO {METADATA_TABLE} VALUES (?, ?)",
            (metadata["imported_at"], json.dumps(metadata, sort_keys=True)))
    return metadata
