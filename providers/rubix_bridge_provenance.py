"""Phase E: per-candle provenance for the Rubix completed-daily bridge.

Writes only to an application-owned SQLite database.  It never touches the
external, read-only Rubix adapter database and never influences trading logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3


DEFAULT_PROVENANCE_DB = "data/rubix_bridge_provenance.db"


class BridgeProvenanceStore:
    """Append-only audit log of every daily-candle source decision."""

    def __init__(self, path=DEFAULT_PROVENANCE_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS candle_provenance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    symbol TEXT NOT NULL,
                    mapped_symbol TEXT,
                    trading_date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    valid INTEGER NOT NULL,
                    reason TEXT,
                    aggregation_method TEXT,
                    source_row_count INTEGER,
                    valid_row_count INTEGER,
                    expected_minutes INTEGER,
                    coverage_ratio REAL,
                    first_source_timestamp TEXT,
                    last_source_timestamp TEXT,
                    open REAL, high REAL, low REAL, close REAL, volume REAL,
                    volume_candle_sum REAL,
                    volume_quote_cumulative REAL,
                    volume_reliability REAL,
                    created_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """
            )

    def record(self, rows, *, run_id=None):
        """Persist a batch of provenance rows (dicts from DailyCandleResult)."""

        recorded_at = datetime.now(timezone.utc).astimezone().isoformat()
        payload = []
        for row in rows:
            payload.append((
                run_id, row.get("symbol"), row.get("mapped_symbol"),
                row.get("trading_date"), row.get("source"), int(row.get("valid", 0)),
                row.get("reason"), row.get("aggregation_method"),
                row.get("source_row_count"), row.get("valid_row_count"),
                row.get("expected_minutes"), row.get("coverage_ratio"),
                row.get("first_source_timestamp"), row.get("last_source_timestamp"),
                row.get("open"), row.get("high"), row.get("low"), row.get("close"),
                row.get("volume"), row.get("volume_candle_sum"),
                row.get("volume_quote_cumulative"), row.get("volume_reliability"),
                row.get("created_at"), recorded_at,
            ))
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO candle_provenance
                (run_id, symbol, mapped_symbol, trading_date, source, valid, reason,
                 aggregation_method, source_row_count, valid_row_count,
                 expected_minutes, coverage_ratio, first_source_timestamp,
                 last_source_timestamp, open, high, low, close, volume,
                 volume_candle_sum, volume_quote_cumulative, volume_reliability,
                 created_at, recorded_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                payload,
            )
        return len(payload)

    def latest_valid_by_symbol(self):
        """Return {symbol -> latest valid RUBIX_COMPLETED_DAILY trading_date}."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, MAX(trading_date) AS latest
                FROM candle_provenance
                WHERE valid=1 AND source='RUBIX_COMPLETED_DAILY'
                GROUP BY symbol
                """
            ).fetchall()
        return {row["symbol"]: row["latest"] for row in rows}
