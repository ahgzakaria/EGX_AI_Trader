"""SQLite candle cache used transparently by provider routing."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pandas as pd

from providers.base_provider import (
    MarketDataProvider,
    ProviderDataError,
    normalize_history,
)


class LocalCacheProvider(MarketDataProvider):
    name = "local_cache"

    def __init__(self, path="data/market_data_cache.sqlite", source_provider=None):
        self.path = Path(path)
        self.source_provider = source_provider
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_data_entries (
                    provider TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    period TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (provider, symbol, period, interval)
                );
                CREATE TABLE IF NOT EXISTS market_data_candles (
                    provider TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    period TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    adj_close REAL,
                    volume REAL,
                    PRIMARY KEY (provider, symbol, period, interval, timestamp)
                );
                """
            )

    @staticmethod
    def ttl_for(interval, provider=None):
        # A TickerChart daily row is the changing current-session candle, so it
        # uses the intraday TTL. Yahoo daily history retains the original 1-day
        # cache policy and therefore backtest behavior remains unchanged.
        if str(provider).strip().lower() in {"tickerchart", "rubix"}:
            return timedelta(minutes=5)
        value = str(interval).strip().lower()
        return timedelta(days=1) if value in {"1d", "1wk", "1mo"} else timedelta(minutes=5)

    def load_history(self, symbol, period, interval):
        if not self.source_provider:
            raise ProviderDataError("Local cache requires a source_provider")
        return self.load_cached(self.source_provider, symbol, period, interval)

    def inspect_cached(self, provider, symbol, period, interval):
        """Return coverage evidence without loading or mutating cached candles."""

        with self._connect() as connection:
            entry = connection.execute(
                """
                SELECT fetched_at FROM market_data_entries
                WHERE provider=? AND symbol=? AND period=? AND interval=?
                """,
                (provider, symbol, period, interval),
            ).fetchone()
            row = connection.execute(
                """
                SELECT COUNT(*) AS row_count, MIN(timestamp) AS minimum_date,
                       MAX(timestamp) AS maximum_date
                FROM market_data_candles
                WHERE provider=? AND symbol=? AND period=? AND interval=?
                """,
                (provider, symbol, period, interval),
            ).fetchone()
        return {
            "available": bool(entry and row and row["row_count"]),
            "row_count": int(row["row_count"] or 0) if row else 0,
            "minimum_date": row["minimum_date"] if row else None,
            "maximum_date": row["maximum_date"] if row else None,
            "fetched_at": entry["fetched_at"] if entry else None,
        }

    def load_cached(self, provider, symbol, period, interval, allow_expired=False):
        with self._connect() as connection:
            entry = connection.execute(
                """
                SELECT fetched_at, metadata_json FROM market_data_entries
                WHERE provider=? AND symbol=? AND period=? AND interval=?
                """,
                (provider, symbol, period, interval),
            ).fetchone()
            if entry is None:
                raise ProviderDataError(f"cache miss: {provider}/{symbol}/{interval}")

            fetched_at = datetime.fromisoformat(entry["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            if not allow_expired and datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc) > self.ttl_for(interval, provider):
                raise ProviderDataError(f"cache expired: {provider}/{symbol}/{interval}")

            rows = connection.execute(
                """
                SELECT timestamp, open, high, low, close, adj_close, volume
                FROM market_data_candles
                WHERE provider=? AND symbol=? AND period=? AND interval=?
                ORDER BY timestamp
                """,
                (provider, symbol, period, interval),
            ).fetchall()

        if not rows:
            raise ProviderDataError(f"cache contains no candles: {provider}/{symbol}")
        frame = pd.DataFrame([dict(row) for row in rows]).rename(columns={
            "timestamp": "Date", "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "adj_close": "Adj Close", "volume": "Volume",
        })
        if frame["Adj Close"].isna().all():
            frame.drop(columns=["Adj Close"], inplace=True)
        normalized = normalize_history(frame, symbol, provider)
        metadata = json.loads(entry["metadata_json"] or "{}")
        metadata.update({
            "provider": provider,
            "cache_hit": True,
            "cache_fetched_at": entry["fetched_at"],
            "received_timestamp": entry["fetched_at"],
        })
        normalized.attrs["market_data"] = metadata
        return normalized

    def store(self, provider, symbol, period, interval, frame):
        normalized = normalize_history(frame, symbol, provider)
        metadata = dict(frame.attrs.get("market_data", {}))
        fetched_at = metadata.get("received_timestamp") or datetime.now(timezone.utc).astimezone().isoformat()
        records = []
        has_adjusted = "Adj Close" in normalized
        for timestamp, row in normalized.iterrows():
            records.append((
                provider, symbol, period, interval, pd.Timestamp(timestamp).isoformat(),
                _number(row.get("Open")), _number(row.get("High")),
                _number(row.get("Low")), _number(row.get("Close")),
                _number(row.get("Adj Close")) if has_adjusted else None,
                _number(row.get("Volume")),
            ))

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM market_data_candles WHERE provider=? AND symbol=? AND period=? AND interval=?",
                (provider, symbol, period, interval),
            )
            connection.executemany(
                """
                INSERT INTO market_data_candles
                (provider, symbol, period, interval, timestamp, open, high, low, close, adj_close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO market_data_entries
                (provider, symbol, period, interval, fetched_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (provider, symbol, period, interval, fetched_at, json.dumps(metadata, default=str)),
            )


def _number(value):
    return None if pd.isna(value) else float(value)
