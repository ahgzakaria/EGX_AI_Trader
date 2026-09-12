"""The live daily history, read from MubasherTrade PRO's own record.

The live scanner reads EODHD's split-adjusted history for 217 of the 230 active
symbols, with Mubasher's measured sessions appended after EODHD's last date.
Measured against a third source, Mubasher's record is the better-supported of
the two (docs/audits/providers/MUBASHER_VS_EODHD_DAILY_HISTORY.md), and it is
the one without a subscription. This module serves the whole history from it,
in the frame the router already serves, so the router can be pointed at it with
one setting (``live_history_source``) once a shadow period has shown the two
agreeing in practice.

It reads ``data/measured_turnover.db``, which ``scripts/import_mubasher_local.py``
rebuilds from ``history.db`` and the live minute store on every run. A rebuild
re-reads the terminal's back-adjusted history whole, so a split applied since
the last run reaches every past bar and not only the new ones -- which is why
nothing here appends to a stored series.

What it states rather than hides:

* ``Open`` is the previous session's close. The record's daily rows carry no
  traded open (the terminal's ``OP`` is the previous close); the minute-built
  sessions do, but one convention for every bar is the honest one, and the
  rules the scanner runs do not read the open.
* A session rebuilt from minute bars that stopped before the closing auction has
  a last price, not an official close. It is kept -- dropping it would leave the
  series short of the session just closed -- and counted in the provenance.
* Nothing past ``not_after`` is served. The daily guard expects a session only
  after its settlement grace, and running ahead of it is a regression the
  EODHD tail path already paid for once.
"""

from __future__ import annotations

import pandas as pd

LIVE_SOURCE = "mubasher"
PROVIDER = "mubasher_live"
SERIES = "MUBASHER_SPLIT_ADJUSTED"
PRICE_POLICY = "MUBASHER_SPLIT_ADJUSTED_NOT_DIVIDEND_ADJUSTED"
OPEN_POLICY = "PREVIOUS_CLOSE_NOT_A_TRADED_OPEN"

#: The market index the strategy's market filter reads, and the provider name
#: its frames carry. It is not an equity and no tier routes it.
INDEX_SYMBOL = "^CASE30"
INDEX_PROVIDER = "mubasher_index"

READY = "MUBASHER_LIVE_READY"
STALE = "MUBASHER_LIVE_STALE"
INSUFFICIENT = "DATA_INSUFFICIENT"
UNAVAILABLE = "DATA_UNAVAILABLE"

CONTRACT_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]

#: How many sessions the market index may be behind the session being scanned
#: before it is refused. The index is daily only -- the minute store holds an
#: ETF that tracks it, not the index -- so it is as fresh as the terminal's
#: last history download and is routinely a session or three behind.
#:
#: Measured on 2,947 sessions of EGX30 from 2014-07-21: a stale index and the
#: current one give the same block/allow decision on 97.8% of sessions at one
#: session behind, 95.1% at three, 88.4% at ten and 84.1% at twenty. Failing
#: open instead -- which is what happened for as long as nothing served the
#: index -- agrees with the current index on 78.5%, because the gate blocks on
#: 21.5% of sessions. So a stale index beats no index at every lag measured,
#: and this bound is where that margin stops being worth the claim rather than
#: where it disappears. It is a judgement, and the numbers to revisit it are
#: right here.
INDEX_LAG_LIMIT_SESSIONS = 20

#: How many of the most recent unconfirmed-close dates the provenance lists.
#: The count covers the whole series; the dates are for reading, not auditing.
RECENT_UNCONFIRMED = 20


def _base(symbol) -> str:
    return str(symbol).strip().upper().split(".")[0]


_STORE_LAST = {}


def store_last_session(database):
    """The newest session the measured store holds for any symbol, or ``None``.

    Cached per file identity, so a rebuilt store is read again.
    """
    from pathlib import Path
    import sqlite3

    from sector_flow import measured_turnover

    path = Path(database)
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if key not in _STORE_LAST:
        try:
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
                value = connection.execute(
                    f"SELECT MAX(session_date) FROM {measured_turnover.TABLE}").fetchone()[0]
        except sqlite3.Error:
            return None
        _STORE_LAST[key] = pd.Timestamp(value).date() if value else None
    return _STORE_LAST[key]


def mubasher_live_history(symbol, *, min_bars=250, not_after=None, database=None):
    """``(frame, status, provenance)`` for one symbol from the measured store.

    ``frame`` is ``None`` when the store holds nothing for the symbol. ``status``
    is ``READY``, ``STALE`` (the store as a whole ends before ``not_after``),
    ``INSUFFICIENT`` (fewer than ``min_bars`` sessions) or ``UNAVAILABLE``. The
    caller decides what each means; this only reports it.

    Stale is a property of the record, not of one symbol. A symbol that did not
    trade on the session has no bar for it here, while EODHD prints a
    zero-volume bar that the scanner's own cleaning then drops -- so both end on
    the same traded session, and refusing the symbol here would be a false
    disagreement. The first version did exactly that to EPPK, GPPL, MATD and
    SAIB on 2026-09-10.
    """
    from sector_flow import measured_turnover

    base = _base(symbol)
    database = database or measured_turnover.DEFAULT_DATABASE
    provenance = {
        "provider": PROVIDER, "effective_provider": PROVIDER, "symbol": base,
        "store": str(database), "open_policy": OPEN_POLICY,
        "price_adjustment": PRICE_POLICY,
    }
    raw = measured_turnover.frame_for(f"{base}.CA", database=database)
    if raw is None or raw.empty:
        provenance["status"] = UNAVAILABLE
        return None, UNAVAILABLE, provenance

    raw = raw[pd.to_numeric(raw["Close"], errors="coerce") > 0]
    raw = raw[~raw.index.duplicated(keep="last")].sort_index()
    withheld = 0
    if not_after is not None:
        ceiling = pd.Timestamp(not_after)
        withheld = int((raw.index > ceiling).sum())
        raw = raw[raw.index <= ceiling]
    if raw.empty:
        provenance.update(status=UNAVAILABLE, sessions_withheld_ahead=withheld)
        return None, UNAVAILABLE, provenance

    close = pd.to_numeric(raw["Close"], errors="coerce")
    frame = pd.DataFrame({
        "Open": close.shift(1).to_numpy(),
        "High": pd.to_numeric(raw["High"], errors="coerce").to_numpy(),
        "Low": pd.to_numeric(raw["Low"], errors="coerce").to_numpy(),
        "Close": close.to_numpy(),
        "Adj Close": close.to_numpy(),
        "Volume": pd.to_numeric(raw["Volume"], errors="coerce").to_numpy(),
    }, index=pd.DatetimeIndex(raw.index, name="Date"))

    flags = (pd.to_numeric(raw["CloseConfirmed"], errors="coerce")
             if "CloseConfirmed" in raw.columns else pd.Series(1, index=raw.index))
    # A row imported before the flag existed came from history.db, whose close
    # is the auction's, so a missing flag reads as confirmed.
    flags = flags.fillna(1).astype(int)
    unconfirmed = [pd.Timestamp(day) for day, flag in flags.items() if flag == 0]
    effective = frame.index[-1].date()
    provenance.update(
        rows=int(len(frame)),
        first_session=frame.index[0].date().isoformat(),
        effective_latest_session=effective.isoformat(),
        sessions_withheld_ahead=withheld,
        unconfirmed_close_count=len(unconfirmed),
        recent_unconfirmed_close_dates=tuple(
            day.date().isoformat() for day in unconfirmed[-RECENT_UNCONFIRMED:]),
        last_close_confirmed=bool(flags.iloc[-1]),
    )

    store_last = store_last_session(database)
    ceiling = pd.Timestamp(not_after).date() if not_after is not None else None
    provenance.update(
        store_last_session=store_last.isoformat() if store_last else None,
        traded_on_expected_session=None if ceiling is None else effective >= ceiling,
    )
    if len(frame) < max(1, int(min_bars)):
        status = INSUFFICIENT
    elif ceiling is not None and (store_last is None or store_last < ceiling):
        # The record is behind: the download or the import was not run.
        status = STALE
    else:
        status = READY
    provenance["status"] = status
    frame.attrs["market_data"] = dict(provenance)
    return frame, status, provenance


def _exchange_sessions_between(database, after, through):
    """How many sessions the measured store holds in ``(after, through]``.

    The store's own equity sessions are the exchange calendar here: counting
    calendar days would call a weekend and a feast a lag.
    """
    from pathlib import Path
    import sqlite3

    from sector_flow import measured_turnover

    try:
        with sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True) as db:
            return int(db.execute(
                f"SELECT COUNT(DISTINCT session_date) FROM {measured_turnover.TABLE} "
                "WHERE session_date > ? AND session_date <= ?",
                (str(after), str(through))).fetchone()[0])
    except sqlite3.Error:
        return 0


def mubasher_live_index(symbol=INDEX_SYMBOL, *, min_bars=250, not_after=None,
                        database=None):
    """``(frame, status, provenance)`` for a market index from the same store.

    The index lives in its own table, never among the equities: sector share is
    a ratio of one symbol's turnover to the market's, and an index row there
    would be counted as a company.

    ``STALE`` here means further behind than ``INDEX_LAG_LIMIT_SESSIONS``, not
    merely behind. See that constant for why the line is where it is; the lag
    is reported in the provenance either way, so a caller that wants a stricter
    rule has the number to apply it with.
    """
    from pathlib import Path
    import sqlite3

    from sector_flow import mubasher_local, measured_turnover

    name = str(symbol).strip().upper()
    database = database or measured_turnover.DEFAULT_DATABASE
    provenance = {
        "provider": INDEX_PROVIDER, "effective_provider": INDEX_PROVIDER,
        "symbol": name, "store": str(database), "open_policy": OPEN_POLICY,
        "price_adjustment": "INDEX_LEVEL_NOT_ADJUSTED",
        "index_intraday_available": False,
    }
    try:
        with sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True) as db:
            rows = pd.read_sql(
                f"SELECT session_date, high, low, close, volume FROM "
                f"{mubasher_local.INDEX_TABLE} WHERE symbol = ? ORDER BY session_date",
                db, params=(name,))
    except Exception:
        # No table at all: a store written before the index was imported.
        provenance["status"] = UNAVAILABLE
        return None, UNAVAILABLE, provenance
    if rows.empty:
        provenance["status"] = UNAVAILABLE
        return None, UNAVAILABLE, provenance

    close = pd.to_numeric(rows["close"], errors="coerce")
    frame = pd.DataFrame({
        "Open": close.shift(1).to_numpy(),
        "High": pd.to_numeric(rows["high"], errors="coerce").to_numpy(),
        "Low": pd.to_numeric(rows["low"], errors="coerce").to_numpy(),
        "Close": close.to_numpy(),
        "Adj Close": close.to_numpy(),
        "Volume": pd.to_numeric(rows["volume"], errors="coerce").to_numpy(),
    }, index=pd.DatetimeIndex(pd.to_datetime(rows["session_date"]), name="Date"))
    frame = frame[frame["Close"] > 0]
    withheld = 0
    if not_after is not None:
        ceiling = pd.Timestamp(not_after)
        withheld = int((frame.index > ceiling).sum())
        frame = frame[frame.index <= ceiling]
    if frame.empty:
        provenance.update(status=UNAVAILABLE, sessions_withheld_ahead=withheld)
        return None, UNAVAILABLE, provenance

    effective = frame.index[-1].date()
    lag = (0 if not_after is None
           else _exchange_sessions_between(database, effective,
                                           pd.Timestamp(not_after).date()))
    provenance.update(
        rows=int(len(frame)),
        first_session=frame.index[0].date().isoformat(),
        effective_latest_session=effective.isoformat(),
        sessions_withheld_ahead=withheld,
        index_session_lag=lag,
        index_lag_limit_sessions=INDEX_LAG_LIMIT_SESSIONS,
    )
    if len(frame) < max(1, int(min_bars)):
        status = INSUFFICIENT
    elif lag > INDEX_LAG_LIMIT_SESSIONS:
        status = STALE
    else:
        status = READY
    provenance["status"] = status
    frame.attrs["market_data"] = dict(provenance)
    return frame, status, provenance
