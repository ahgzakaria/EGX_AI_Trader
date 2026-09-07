"""Real EGX turnover, as the exchange reported it, instead of a proxy.

``turnover_series`` derives turnover as ``(High + Low + Close) / 3 x Volume``
because the daily providers this project uses do not carry a turnover column.
That is a reasonable estimate and mostly a good one: measured against 9,721
sessions of MubasherTrade PRO's reported turnover, the median error is 0.37% and
the ninetieth percentile is under 2.4%.

Its worst case is 248%.

The error is not noise. It is largest exactly where a session was unusual --
where trading clustered far from the day's typical price -- and sector share is
a ratio of one symbol's turnover to the market's, so a single large symbol
mis-measured by that much moves every sector's share for that session. The
number this store replaces is the one every ranking on the Sector Liquidity
page is computed from.

The prices in the same export are raw and unadjusted, so nothing here touches
them. Turnover in EGP is not adjusted by a split: a hundred thousand pounds
traded is a hundred thousand pounds traded whatever the share count became
afterwards. That is why this file imports one column and leaves the rest.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DATABASE = "data/measured_turnover.db"
TABLE = "measured_turnover"
METADATA_TABLE = "measured_turnover_metadata"

#: The export's own header. Read by name, never by position: the columns are
#: whatever the terminal wrote, and a silently reordered file must fail rather
#: than load turnover out of the volume column.
REQUIRED_COLUMNS = ("Symbol", "Date Range", "Volume", "Turnover")

#: Also imported, for the symbols the daily provider has nothing at all for.
#: Split-adjusted and not dividend-adjusted, so a return spanning an ex-date
#: shows a drop that was a payment rather than a loss. Across 1,353 dividends
#: in 746,434 sessions that is 0.18% of bars, which is acceptable for counting
#: advancers and decliners and is not acceptable for anything cumulative --
#: which is why nothing here reaches an indicator or a backtest.
PRICE_COLUMNS = {"Open": "open", "High": "high", "Low": "low", "Closed": "close"}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    ticker TEXT NOT NULL,
    session_date TEXT NOT NULL,
    turnover REAL NOT NULL,
    volume REAL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    PRIMARY KEY (ticker, session_date)
);
CREATE TABLE IF NOT EXISTS {METADATA_TABLE} (
    imported_at TEXT PRIMARY KEY,
    metadata_json TEXT NOT NULL
);
"""


def read_export(path):
    """Return one file's turnover rows, or an empty frame.

    Rows with no turnover are dropped rather than stored as zero. A session
    where nothing traded and a session the export could not describe look
    identical once a zero is written, and the second must fall back to the
    estimate rather than claim the market was still.
    """

    path = Path(path)
    frame = pd.read_csv(path, encoding="utf-8-sig")
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"{path.name} is missing columns: {missing}")

    session = pd.to_datetime(frame["Date Range"], errors="coerce")
    turnover = pd.to_numeric(frame["Turnover"], errors="coerce")
    volume = pd.to_numeric(frame["Volume"], errors="coerce")

    columns = {
        "ticker": frame["Symbol"].astype(str).str.strip().str.upper(),
        "session_date": session.dt.strftime("%Y-%m-%d"),
        "turnover": turnover,
        "volume": volume,
    }
    for source, stored_as in PRICE_COLUMNS.items():
        columns[stored_as] = (pd.to_numeric(frame[source], errors="coerce")
                              if source in frame.columns else float("nan"))
    tidy = pd.DataFrame(columns)
    tidy = tidy[tidy["session_date"].notna() & (tidy["turnover"] > 0)]
    # One export per symbol, but a terminal that repeats a session would
    # otherwise decide which row wins by insertion order.
    return tidy.drop_duplicates(subset=["ticker", "session_date"], keep="first")


def import_directory(directory, database=DEFAULT_DATABASE, suffix=".CA"):
    """Load every CSV in ``directory`` into the store, replacing what is there.

    Replacing rather than appending: a re-export of the same symbol is a
    correction, and two versions of one session with no way to tell them apart
    is worse than either.
    """

    directory = Path(directory)
    files = sorted(directory.glob("*.csv"))
    if not files:
        raise ValueError(f"No CSV files in {directory}")

    frames, failures = [], []
    for path in files:
        try:
            rows = read_export(path)
        except Exception as error:                      # one bad file, not a batch
            logger.warning("measured turnover: %s unreadable (%s)", path.name, error)
            failures.append({"file": path.name, "error": f"{type(error).__name__}: {error}"})
            continue
        if not rows.empty:
            frames.append(rows)

    if not frames:
        raise ValueError(f"No usable turnover rows in {directory}")

    stored = pd.concat(frames, ignore_index=True)
    stored["ticker"] = stored["ticker"] + suffix

    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        # Dropped, not emptied. CREATE TABLE IF NOT EXISTS leaves an existing
        # table's columns alone, so a store written before this file carried
        # prices survives the import and then rejects every row with "no column
        # named open". The import replaces the contents anyway.
        connection.execute(f"DROP TABLE IF EXISTS {TABLE}")
        connection.executescript(SCHEMA)
        stored.to_sql(TABLE, connection, if_exists="append", index=False)

    metadata = {
        "imported_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source_directory": str(directory),
        "files_read": len(files),
        "files_failed": len(failures),
        "failures": failures,
        "symbols": int(stored["ticker"].nunique()),
        "sessions": int(stored["session_date"].nunique()),
        "rows": int(len(stored)),
        "first_session": stored["session_date"].min(),
        "last_session": stored["session_date"].max(),
    }
    with sqlite3.connect(database) as connection:
        import json
        connection.execute(
            f"INSERT OR REPLACE INTO {METADATA_TABLE} VALUES (?, ?)",
            (metadata["imported_at"], json.dumps(metadata, sort_keys=True)),
        )
    return metadata


def load_turnover(ticker, database=DEFAULT_DATABASE):
    """Return ``{session_date: turnover}`` for one ticker, or an empty dict.

    A missing store is not an error. This is an enrichment: without it the
    proxy still produces a history, and a machine that has never run the
    import must still be able to build one.
    """

    if not Path(database).exists():
        return {}
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                f"SELECT session_date, turnover FROM {TABLE} WHERE ticker=?",
                (str(ticker).upper(),),
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(date): float(value) for date, value in rows}


def attach(frame, ticker, database=DEFAULT_DATABASE):
    """Return ``frame`` with a ``Turnover`` column where the store has one.

    Only where the store has one. A session the export does not cover keeps
    ``NaN`` so the caller falls back to the estimate for that session alone,
    rather than the whole symbol -- the export ends on the day it was taken,
    and every session after it would otherwise be lost.
    """

    if frame is None or getattr(frame, "empty", True):
        return frame
    measured = load_turnover(ticker, database)
    if not measured:
        return frame

    index = pd.to_datetime(frame.index, errors="coerce")
    keys = pd.Series(index).dt.strftime("%Y-%m-%d")
    enriched = frame.copy()
    enriched["Turnover"] = keys.map(measured).to_numpy()
    return enriched


#: How far ``turnover / (volume x close)`` may sit from 1 before a symbol is
#: not being quoted in the currency its turnover is reported in.
#:
#: For an EGP symbol that ratio is the price, divided by the price: COMI and
#: ABUK sit at 1.0004 and 0.9996 across five thousand sessions each. For a
#: symbol priced in dollars and reported in pounds it is the exchange rate --
#: 5.77 in 2005, 8.77 in 2016, 48.36 in 2024 -- and it tracks the currency year
#: by year rather than drifting.
#:
#: Eleven symbols were like that and all eleven were in the tradeable universe
#: marked EGP, including one whose own company_name reads "Faisal Islamic Bank
#: of Egypt - In US Dollars". The estimate they fed sector flow was a dollar
#: figure counted as pounds, understating them by about fifty, and on 5% of
#: sessions since 2020 that moved a sector's share by more than 35 points.
FX_QUOTE_TOLERANCE = 0.25


def implied_quote_ratio(frame):
    """Return the median of ``turnover / (volume x close)`` for one symbol.

    Near 1 means turnover and price agree about the currency. Far from it means
    they do not, and the number is the rate between them.
    """

    import numpy as np

    needed = {"Closed", "Volume", "Turnover"}
    if frame is None or frame.empty or needed - set(frame.columns):
        return float("nan")
    close = pd.to_numeric(frame["Closed"], errors="coerce")
    volume = pd.to_numeric(frame["Volume"], errors="coerce")
    turnover = pd.to_numeric(frame["Turnover"], errors="coerce")
    usable = (close > 0) & (volume > 0) & (turnover > 0)
    if not usable.any():
        return float("nan")
    return float((turnover[usable] / volume[usable] / close[usable]).median())


def foreign_quoted_symbols(directory, tolerance=FX_QUOTE_TOLERANCE,
                           min_sessions=20, since="2024-01-01"):
    """Return ``{symbol: ratio}`` for exports whose price is not in EGP.

    Recent sessions only, because two different things push this ratio off 1
    and only one of them is a currency.

    A split moves it as well: the export back-adjusts price for splits but
    reports volume and turnover as traded, so before a 25-for-1 the ratio sits
    at 0.040 and after it at 1.000. ASPI does exactly that on 2021-10-11, with
    the close continuous at 0.289 either side. Judged over all history four
    such symbols look foreign; judged over the last two years none of them do,
    and the eleven that are quoted in dollars still sit near fifty.

    The split case needs no action anyway. Those symbols are EGP and correct
    today, and the historical estimate they broke is the very thing the
    measured turnover replaces.

    ``min_sessions`` is 20 rather than anything comfortable because SAIB traded
    on 27 days in two years and is quoted in euros. A thinly traded symbol is
    still in the universe and still lands in a sector, so a threshold that
    reads well would have missed the one symbol here that is not dollars.
    """

    found = {}
    for path in sorted(Path(directory).glob("*.csv")):
        try:
            frame = pd.read_csv(path, encoding="utf-8-sig")
        except Exception:
            continue
        frame.columns = [str(column).strip() for column in frame.columns]
        if "Date Range" in frame.columns and since:
            recent = pd.to_datetime(frame["Date Range"], errors="coerce") >= since
            frame = frame[recent]
        if len(frame) < min_sessions:
            continue
        ratio = implied_quote_ratio(frame)
        if ratio == ratio and abs(ratio - 1.0) > tolerance:
            found[path.stem.upper()] = ratio
    return found


def frame_for(ticker, database=DEFAULT_DATABASE):
    """Return a daily frame for one ticker, or ``None``.

    For the symbols the daily provider returns nothing for. Four of them --
    ACGC, NCCW, JUFO, EDBM -- are UNAVAILABLE in every coverage report this
    project has produced, so they contribute nothing to any sector's turnover
    and appear in no scan, while this export holds 5,527, 4,769, 3,851 and
    3,811 sessions for them going back to 2003.

    The frame carries a real Turnover column, so nothing downstream needs to
    estimate one from these prices.
    """

    if not Path(database).exists():
        return None
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = pd.read_sql(
                f"SELECT session_date, open, high, low, close, volume, turnover "
                f"FROM {TABLE} WHERE ticker=? ORDER BY session_date",
                connection, params=(str(ticker).upper(),))
    except Exception:
        return None
    if rows.empty or rows["close"].notna().sum() == 0:
        return None

    frame = pd.DataFrame({
        "Open": rows["open"], "High": rows["high"], "Low": rows["low"],
        "Close": rows["close"], "Volume": rows["volume"],
        "Turnover": rows["turnover"],
    })
    frame.index = pd.to_datetime(rows["session_date"])
    frame.index.name = "Date"
    frame.attrs["market_data"] = {
        "effective_provider": "mubasher_export",
        "automatic_use_permitted": False,   # records only; never an entry
        "freshness_status": "MEASURED_EXPORT",
    }
    return frame.dropna(subset=["Close"])


def fill_missing_volume(frame, ticker, database=DEFAULT_DATABASE):
    """Fill only the volumes the provider left empty. Prices are never touched."""

    if frame is None or getattr(frame, "empty", True) or "Volume" not in frame:
        return frame
    supplement = frame_for(ticker, database)
    if supplement is None:
        return frame
    keys = pd.Series(pd.to_datetime(frame.index, errors="coerce")).dt.strftime("%Y-%m-%d")
    lookup = dict(zip(supplement.index.strftime("%Y-%m-%d"), supplement["Volume"]))
    filled = frame.copy()
    missing = pd.to_numeric(filled["Volume"], errors="coerce").isna()
    if missing.any():
        values = keys.map(lookup).to_numpy()
        filled.loc[missing.to_numpy(), "Volume"] = values[missing.to_numpy()]
    return filled
