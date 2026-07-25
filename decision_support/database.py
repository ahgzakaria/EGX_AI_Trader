"""Persistent advisory observations, pins, and paper alerts only."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
CREATE TABLE IF NOT EXISTS observations (
 observation_id TEXT PRIMARY KEY, observation_key TEXT NOT NULL UNIQUE,
 observed_at TEXT NOT NULL, ticker TEXT NOT NULL, edge_score REAL NOT NULL,
 spread_percent REAL, liquidity_score REAL, provider TEXT,
 freshness TEXT, payload_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS ds_observations_no_update BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT,'decision-support observations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ds_observations_no_delete BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT,'decision-support observations are immutable'); END;
CREATE TABLE IF NOT EXISTS alerts (
 alert_id TEXT PRIMARY KEY, alert_key TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL, alert_type TEXT NOT NULL, ticker TEXT,
 message TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pinned_symbols (
 ticker TEXT PRIMARY KEY, pinned_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ds_observation_ticker ON observations(ticker,observed_at);
CREATE INDEX IF NOT EXISTS idx_ds_alert_time ON alerts(created_at);
"""


class DecisionSupportDatabase:
    def __init__(self, path="data/decision_support.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_observation(self, row):
        observed = str(row.get("ObservedAt") or datetime.now(timezone.utc).astimezone().isoformat())
        minute = observed[:16]
        key = f"{row['Ticker']}|{minute}"
        with self.transaction() as connection:
            previous = connection.execute(
                "SELECT * FROM observations WHERE ticker=? ORDER BY observed_at DESC LIMIT 1",
                (row["Ticker"],),
            ).fetchone()
            inserted = connection.execute(
                "INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?,?,?)",
                (str(uuid4()), key, observed, row["Ticker"], row["EdgeScore"],
                 row.get("SpreadPercent"), row.get("LiquidityScore"),
                 row.get("Provider"), row.get("Freshness"),
                 json.dumps(row, default=str)),
            ).rowcount == 1
        return dict(previous) if previous else None, inserted

    def alert(self, alert_type, message, ticker=None, key=None):
        now = datetime.now(timezone.utc).astimezone().isoformat()
        key = key or f"{alert_type}|{ticker or ''}|{now[:16]}"
        with self.transaction() as connection:
            return connection.execute(
                "INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?,?,0)",
                (str(uuid4()), key, now, alert_type, ticker, message),
            ).rowcount == 1

    def set_pinned(self, ticker, pinned=True):
        with self.transaction() as connection:
            if pinned:
                connection.execute(
                    "INSERT OR IGNORE INTO pinned_symbols VALUES (?,?)",
                    (ticker, datetime.now(timezone.utc).astimezone().isoformat()),
                )
            else:
                connection.execute("DELETE FROM pinned_symbols WHERE ticker=?", (ticker,))

    def rows(self, sql, parameters=()):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]
