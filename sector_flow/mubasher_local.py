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


# --------------------------------------------------------------------------- #
# One session at a time, for the research that works in minutes rather than days
# --------------------------------------------------------------------------- #

TAPE_RELATIVE = "HistoricalTrade/CASE"

#: Below this many trades a session's tape says nothing about a symbol's
#: spread, and Roll's covariance is noise. 30 is where the estimate stopped
#: swinging by more than its own size between adjacent sessions.
_ROLL_MIN_TRADES = 30

#: Below this the covariance was negative only by float error. See
#: ``roll_spread_estimates``.
_ROLL_MIN_PERCENT = 1e-4


def available_sessions(root=None):
    """Cairo dates the minute store currently holds, newest last.

    It keeps a rolling fourteen sessions. Anything older has to come from the
    daily record, which has no minutes at all.
    """

    base = find_root(root)
    if base is None or not (base / INTRADAY_RELATIVE).exists():
        return []
    try:
        with _open_read_only(base / INTRADAY_RELATIVE) as connection:
            rows = connection.execute("SELECT INTRADAYDATE FROM INTRADAY_MASTER").fetchall()
    except sqlite3.Error:
        return []
    dates = []
    for row in rows:
        raw = str(row[0])
        if len(raw) == 8:
            dates.append(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}")
    return sorted(dates)


def session_minutes(session, root=None, symbols=None):
    """Per-symbol shape of one session, from the minute store.

    Returns ``{ticker: dict}`` with the session's open, close, high, low,
    reported turnover, bar count, the last minute reached, and whether the
    close is the auction's.

    The open here is the real one. It is the only true EGX opening price on
    this machine: the daily record's ``OP`` column is the previous close, and
    the terminal's CSV export puts the high in its "Open" column.
    """

    base = find_root(root)
    if base is None or not (base / INTRADAY_RELATIVE).exists():
        return {}

    day = str(session)[:10]
    shapes = {}
    with _open_read_only(base / INTRADAY_RELATIVE) as connection:
        for table, ticker in _instrument_tables(connection, symbols):
            try:
                rows = connection.execute(
                    f'SELECT TMIN, OP, HIG, LOW, CLS, VOL, TOVR FROM "{table}" '
                    f'ORDER BY CAST(TMIN AS INTEGER)').fetchall()
            except sqlite3.Error:
                continue

            bars = []
            for tmin, op, hig, low, cls, vol, tovr in rows:
                try:
                    stamp, minute = session_of(tmin)
                    if stamp != day:
                        continue
                    bars.append((minute, float(op), float(hig), float(low),
                                 float(cls), float(vol or 0), float(tovr or 0)))
                except (TypeError, ValueError):
                    continue
            if not bars:
                continue

            last_minute = bars[-1][0]
            shapes[ticker] = {
                "open": bars[0][1],
                "close": bars[-1][4],
                "high": max(b[2] for b in bars),
                "low": min(b[3] for b in bars),
                "turnover": sum(b[6] for b in bars),
                "volume": sum(b[5] for b in bars),
                "bars": len(bars),
                "last_minute": f"{last_minute // 60:02d}:{last_minute % 60:02d}",
                "close_confirmed": last_minute >= AUCTION_FROM_MINUTE,
            }
    return shapes


def session_opens(session, root=None, symbols=None):
    """``{ticker: opening price}`` for one session, or an empty dict."""
    return {ticker: shape["open"]
            for ticker, shape in session_minutes(session, root, symbols).items()
            if shape["open"] > 0}


def roll_spread_estimates(session, root=None):
    """Roll's effective spread per symbol, from the session's trade tape.

    ``2 * sqrt(-cov(dP_t, dP_t-1))`` over the session's own trades, as a
    percentage of the mean traded price, and ``None`` where the covariance is
    not negative -- which is Roll's model saying it has no estimate rather than
    an estimate of zero.

    **It is not the quoted spread and must not be used as one.** Measured
    against the quoted median spread on 2026-09-08, -09 and -10, across ~205
    symbols a session: correlation 0.74 to 0.84, rank correlation 0.78 to 0.85,
    but biased low -- median 0.26-0.28% against a quoted 0.39-0.46% -- so at a
    0.3% cutoff it admits about twice as many symbols as tight and disagrees
    with the quoted verdict on 22-36% of them.

    So it is recorded beside a prediction and never read by the rule that makes
    one. What it is good for is ranking; what it cannot do is stand in for a
    cost.
    """

    base = find_root(root)
    if base is None:
        return {}
    tape = base / TAPE_RELATIVE / f"{str(session)[:10].replace('-', '')}.db"
    if not tape.exists():
        return {}

    import math
    import statistics

    prices = {}
    try:
        with _open_read_only(tape) as connection:
            for symbol, price in connection.execute(
                    "SELECT SYMBOL, TRADEPRICE FROM TRADES "
                    "WHERE ISODDLOTTRADE='0' ORDER BY CAST(SEQUENCE AS INTEGER)"):
                try:
                    prices.setdefault(str(symbol).upper(), []).append(float(price))
                except (TypeError, ValueError):
                    continue
    except sqlite3.Error as error:
        logger.warning("mubasher tape: %s unreadable (%s)", tape.name, error)
        return {}

    estimates = {}
    for symbol, series in prices.items():
        if len(series) < _ROLL_MIN_TRADES:
            continue
        changes = [b - a for a, b in zip(series, series[1:])]
        if len(changes) < 20:
            continue
        first, second = changes[:-1], changes[1:]
        mean_first = statistics.mean(first)
        mean_second = statistics.mean(second)
        covariance = sum((a - mean_first) * (b - mean_second)
                         for a, b in zip(first, second)) / (len(first) - 1)
        mid = statistics.mean(series)
        if covariance >= 0 or mid <= 0:
            estimates[symbol] = None
            continue
        estimate = 100.0 * 2.0 * math.sqrt(-covariance) / mid
        # A covariance that is negative only by float error is not a bounce.
        # A price that walked one way all session gives cov of exactly zero in
        # exact arithmetic and about -1e-30 in this one, which came out as a
        # spread of 0.000000000001% -- a number that would read as "free to
        # trade". Every real EGX spread is orders above this floor; it rejects
        # arithmetic noise, and claims nothing about the market.
        estimates[symbol] = estimate if estimate >= _ROLL_MIN_PERCENT else None
    return estimates


def isin_ticker_map(root=None):
    """``{ISIN: Mubasher ticker}`` from the terminal's own CASE symbol master.

    Mubasher files some companies under a different ticker from the universe's:
    EODHD's AIND, ALRA and MATD are the terminal's AIHC, AIFI and MMAT. The ISIN
    is what identifies them as the same company, never the name. Returns an
    empty mapping when the master cannot be read.
    """

    base = find_root(root)
    if base is None:
        return {}
    path = base.parent.parent / "Cache" / "PrimarySystemMeta.db"
    if not path.is_file():
        return {}
    try:
        with _open_read_only(path) as connection:
            row = connection.execute(
                "SELECT JSON FROM SYMBOL_MASTER WHERE EXCHANGE='CASE' AND LANGUAGE='EN'"
            ).fetchone()
        if not row:
            return {}
        payload = json.loads(row[0])
        fields = payload["HED"]["TD"].split("|")
        mapping = {}
        for line in payload["DAT"]["TD"]:
            entry = dict(zip(fields, str(line).split("|")))
            isin = str(entry.get("ISIN_CODE", "")).strip()
            ticker = str(entry.get("SYMBOL", "")).strip().upper()
            if isin and ticker:
                mapping.setdefault(isin, ticker)
        return mapping
    except (sqlite3.Error, KeyError, ValueError, TypeError) as error:
        logger.warning("mubasher symbol master unreadable (%s)", error)
        return {}


def _isin_aliases(base, scope, isin_of=None, by_isin=None):
    """``{Mubasher ticker: scope ticker}`` for scope tickers with no table of their own.

    Only a ticker the terminal genuinely lacks is resolved, and only to a table
    no other scope ticker already claims, so a company can never be imported
    twice under two names.
    """

    try:
        if isin_of is None:
            from core.universe import load_universe
            isin_of = {record.canonical_symbol: str(getattr(record, "isin", "") or "").strip()
                       for record in load_universe()}
        if by_isin is None:
            by_isin = isin_ticker_map(base)
        if not by_isin:
            return {}
        with _open_read_only(base / HISTORY_RELATIVE, immutable=True) as connection:
            held = {ticker for _, ticker in _instrument_tables(connection)}
    except Exception as error:                              # an enrichment, not a gate
        logger.warning("mubasher import: ISIN resolution skipped (%s)", error)
        return {}

    aliases = {}
    for ticker in sorted(scope):
        if ticker in held:
            continue
        twin = by_isin.get(isin_of.get(ticker, ""))
        if twin and twin in held and twin not in scope and twin not in aliases:
            aliases[twin] = ticker
    return aliases


def _predecessors(scope):
    """``{live ticker: [retired tickers]}`` from the alias registry, for tickers in scope."""

    try:
        from core.universe import read_alias_registry
        registry = read_alias_registry()
    except Exception as error:                              # an enrichment, not a gate
        logger.warning("mubasher import: alias registry unavailable (%s)", error)
        return {}
    found = {}
    for alias, live in sorted(registry.items()):
        if live in scope:
            found.setdefault(live, []).append(alias)
    return found


def _stitch_predecessors(history, predecessors):
    """Prepend a retired ticker's earlier sessions to the ticker that replaced it.

    The terminal keeps a renamed company's history under its old table: AMII's
    table starts on 2026-07-26 with 33 sessions, while ARVA's holds 3,827 from
    2010 and ends on that same bar. Only a predecessor whose closes are
    identical to the live table on every session they share is stitched. EDBM
    and CRST share sessions on a 0.66 price basis, and joining those would put
    a false 34% move into the series.

    Returns ``(history, {live: {"from": retired, "sessions": n}})``.
    """

    if history.empty or not predecessors:
        return history, {}
    pieces, stitched = [history], {}
    for live, retired in sorted(predecessors.items()):
        current = history[history["ticker"] == live]
        if current.empty:
            continue
        first = current["session_date"].min()
        for old in retired:
            before = history[history["ticker"] == old]
            if before.empty:
                continue
            shared = before[["session_date", "close"]].merge(
                current[["session_date", "close"]], on="session_date",
                suffixes=("_old", "_new"))
            if shared.empty or not ((shared["close_old"] - shared["close_new"]).abs()
                                    <= 1e-9).all():
                continue
            earlier = before[before["session_date"] < first].assign(ticker=live)
            if len(earlier):
                pieces.append(earlier)
                stitched[live] = {"from": old, "sessions": int(len(earlier))}
            break
    return pd.concat(pieces, ignore_index=True), stitched


def _renamed(frame, aliases):
    if frame.empty or not aliases:
        return frame
    frame = frame.copy()
    frame["ticker"] = frame["ticker"].map(lambda ticker: aliases.get(ticker, ticker))
    return frame


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


def import_local(root=None, database=DEFAULT_DATABASE, suffix=".CA", symbols=None,
                 aliases=None, predecessors=None):
    """Refresh the measured store from the terminal's own databases.

    ``history.db`` is authoritative wherever it reaches; the minute store fills
    only the sessions after it, which is the part that used to require the
    export. Returns the metadata row it wrote, or raises if neither database
    produced a usable row -- a store silently replaced by nothing is the one
    outcome worth failing on.

    A scope ticker the terminal files under another name is read from that
    table and stored under the scope's ticker (``aliases``, resolved by ISIN
    when not given). Without it AIND, ALRA and MATD were absent from the store
    altogether, while the terminal held their full histories as AIHC, AIFI and
    MMAT.
    """

    base = find_root(root)
    if base is None:
        raise FileNotFoundError(
            "MubasherTrade PRO data not found; pass --root with the UserData folder")

    scope = ({str(s).split(".")[0].upper() for s in symbols} if symbols
             else default_scope(database))
    if aliases is None:
        aliases = _isin_aliases(base, scope)
    if predecessors is None:
        predecessors = _predecessors(scope)
    retired = {old for olds in predecessors.values() for old in olds}
    read_scope = set(scope) | set(aliases) | retired
    history = _renamed(read_history(base, symbols=read_scope), aliases)
    history, stitched = _stitch_predecessors(history, predecessors)
    intraday = _renamed(read_intraday(base, symbols=read_scope), aliases)
    # A retired table was read to be stitched, not to be stored under its own
    # name; only the scope's tickers are written.
    if not history.empty:
        history = history[history["ticker"].isin(scope)]
    if not intraday.empty:
        intraday = intraday[intraday["ticker"].isin(scope)]

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
        "isin_resolved": {stored: twin for twin, stored in sorted(aliases.items())},
        "predecessors_stitched": stitched,
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            f"INSERT OR REPLACE INTO {METADATA_TABLE} VALUES (?, ?)",
            (metadata["imported_at"], json.dumps(metadata, sort_keys=True)))
    return metadata
