"""Read-only intraday data sources for the isolated scalping backtest.

The source layer only prepares minute OHLCV frames.  It never changes the
scalping strategy, its configuration, or the external Rubix adapter database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd

from providers.symbol_mapping import to_engine_symbol, to_rubix_symbol


CAIRO = ZoneInfo("Africa/Cairo")
REQUIRED_COLUMNS = {"ticker", "minute", "open", "high", "low", "close", "volume"}


class ScalpingDataSourceError(RuntimeError):
    """Friendly data-source failure that is safe to display in Streamlit."""


@dataclass(frozen=True)
class RubixArchiveStatus:
    database_path: str
    database_status: str
    session_count: int
    symbol_count: int
    minute_bar_count: int
    latest_minute: str | None


class RubixScalpingDataSource:
    """Consume the collector's minute candles through SQLite read-only mode."""

    def __init__(self, database_path):
        self.path = Path(database_path).expanduser()

    def status(self) -> RubixArchiveStatus:
        with self._connect() as connection:
            self._validate_schema(connection)
            row = connection.execute(
                """SELECT COUNT(*),COUNT(DISTINCT ticker),COUNT(DISTINCT substr(minute,1,10)),
                          MAX(minute) FROM candles_1m"""
            ).fetchone()
        return RubixArchiveStatus(
            database_path=str(self.path.resolve()),
            database_status="READ_ONLY_OK",
            minute_bar_count=int(row[0] or 0),
            symbol_count=int(row[1] or 0),
            session_count=int(row[2] or 0),
            latest_minute=str(row[3]) if row[3] else None,
        )

    def session_dates(self) -> tuple[date, ...]:
        """Return stored Cairo trading dates, newest first."""

        with self._connect() as connection:
            self._validate_schema(connection)
            rows = connection.execute(
                "SELECT DISTINCT substr(minute,1,10) value FROM candles_1m ORDER BY value DESC"
            ).fetchall()
        dates = []
        for row in rows:
            try:
                # EGX trading hours never cross the UTC date boundary, but
                # conversion is kept explicit for a reproducible UI contract.
                dates.append(pd.Timestamp(row[0], tz="UTC").tz_convert(CAIRO).date())
            except (TypeError, ValueError):
                continue
        return tuple(dict.fromkeys(dates))

    def symbols(self, session_date) -> tuple[str, ...]:
        start, end = _utc_session_bounds(session_date)
        with self._connect() as connection:
            self._validate_schema(connection)
            rows = connection.execute(
                """SELECT DISTINCT UPPER(ticker) FROM candles_1m
                   WHERE minute>=? AND minute<? ORDER BY UPPER(ticker)""",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return tuple(to_engine_symbol(row[0]) for row in rows if row[0])

    def load_session(self, session_date, symbols) -> dict[str, pd.DataFrame]:
        """Load selected symbols for one Cairo session without writing SQLite."""

        requested = tuple(dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()))
        if not requested:
            raise ScalpingDataSourceError("Select at least one symbol.")
        mapped = tuple(to_rubix_symbol(symbol) for symbol in requested)
        start, end = _utc_session_bounds(session_date)
        placeholders = ",".join("?" for _ in mapped)
        query = f"""SELECT UPPER(ticker) ticker,minute,open,high,low,close,volume
                    FROM candles_1m
                    WHERE minute>=? AND minute<? AND UPPER(ticker) IN ({placeholders})
                    ORDER BY minute,UPPER(ticker)"""
        with self._connect() as connection:
            self._validate_schema(connection)
            rows = connection.execute(query, (start.isoformat(), end.isoformat(), *mapped)).fetchall()
        if not rows:
            raise ScalpingDataSourceError("No Rubix one-minute candles match the selected date and symbols.")

        source = pd.DataFrame([dict(row) for row in rows])
        source["minute"] = pd.to_datetime(source["minute"], utc=True, errors="coerce")
        source = source.dropna(subset=["minute"])
        frames = {}
        for ticker, group in source.groupby("ticker", sort=True):
            frame = group.rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            }).set_index("minute")[["Open", "High", "Low", "Close", "Volume"]]
            frame.index = frame.index.tz_convert(CAIRO)
            frame.index.name = "Date"
            frames[to_engine_symbol(ticker)] = frame.sort_index()
        if not frames:
            raise ScalpingDataSourceError("Rubix candles were present but none had valid timestamps.")
        return frames

    def _connect(self):
        if not self.path.is_file():
            raise ScalpingDataSourceError(f"Rubix database was not found: {self.path}")
        try:
            uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            return connection
        except (OSError, sqlite3.Error) as error:
            raise ScalpingDataSourceError(f"Rubix database cannot be opened read-only: {error}") from error

    @staticmethod
    def _validate_schema(connection):
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='candles_1m'"
            ).fetchone()
            if not table:
                raise ScalpingDataSourceError("Rubix database does not contain candles_1m.")
            columns = {str(row[1]).lower() for row in connection.execute("PRAGMA table_info(candles_1m)")}
        except sqlite3.Error as error:
            raise ScalpingDataSourceError(f"Rubix candle schema cannot be read: {error}") from error
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ScalpingDataSourceError(
                "Rubix candles_1m is missing columns: " + ", ".join(sorted(missing))
            )


def load_csv_frames(files) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Normalize uploaded or archived CSVs using the legacy UI rules."""

    frames, errors = {}, []
    for source in files or ():
        name = getattr(source, "name", None) or Path(source).name
        try:
            frame = pd.read_csv(source)
            time_column = next(
                (item for item in ("Timestamp", "timestamp", "Date", "date", "minute") if item in frame),
                None,
            )
            if time_column is None:
                raise ValueError("missing Timestamp/Date column")
            frame[time_column] = pd.to_datetime(frame[time_column], errors="raise")
            ticker_column = next(
                (item for item in ("Ticker", "ticker", "Symbol", "symbol") if item in frame),
                None,
            )
            if ticker_column:
                for ticker, group in frame.groupby(ticker_column):
                    frames[to_engine_symbol(ticker)] = group.drop(columns=[ticker_column]).set_index(time_column)
            else:
                frames[to_engine_symbol(Path(name).stem)] = frame.set_index(time_column)
        except Exception as error:
            errors.append(f"{name}: {error}")
    return frames, errors


def historical_archive_files(root="data/scalping_archive") -> tuple[Path, ...]:
    """Return local CSV evidence without creating or mutating the archive."""

    directory = Path(root)
    if not directory.is_dir():
        return ()
    return tuple(sorted(directory.glob("*.csv")))


def _utc_session_bounds(value) -> tuple[datetime, datetime]:
    selected = value if isinstance(value, date) else date.fromisoformat(str(value))
    local_start = datetime.combine(selected, time.min, tzinfo=CAIRO)
    return local_start.astimezone(timezone.utc), (local_start + timedelta(days=1)).astimezone(timezone.utc)
