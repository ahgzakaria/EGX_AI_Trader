"""Write frozen Yahoo snapshot rows for a test, the way they exist on disk.

``LocalCacheProvider.store`` refuses ``yahoo`` rows, because the program must
never write the snapshot. A test that needs one creates it here, outside the
program, with plain SQL against the same schema.
"""

from datetime import datetime, timezone
import json
import sqlite3

import pandas as pd

from providers.local_cache_provider import LocalCacheProvider


def write_snapshot(cache_path, symbol, frame, *, period="10y", interval="1d",
                   fetched_at="2026-07-24T14:18:10+03:00"):
    LocalCacheProvider(cache_path)          # creates the schema if needed
    records = [
        (
            "yahoo", symbol, period, interval, pd.Timestamp(ts).isoformat(),
            float(row["Open"]), float(row["High"]), float(row["Low"]),
            float(row["Close"]),
            float(row["Adj Close"]) if "Adj Close" in frame else None,
            float(row["Volume"]),
        )
        for ts, row in frame.iterrows()
    ]
    with sqlite3.connect(cache_path) as connection:
        connection.executemany(
            "INSERT INTO market_data_candles (provider, symbol, period, interval, "
            "timestamp, open, high, low, close, adj_close, volume) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            records,
        )
        connection.execute(
            "INSERT OR REPLACE INTO market_data_entries "
            "(provider, symbol, period, interval, fetched_at, metadata_json) "
            "VALUES (?,?,?,?,?,?)",
            ("yahoo", symbol, period, interval, fetched_at,
             json.dumps({"provider": "yahoo", "received_timestamp": fetched_at})),
        )


def expired_timestamp(days=60):
    return (datetime.now(timezone.utc) - pd.Timedelta(days=days)).isoformat()
