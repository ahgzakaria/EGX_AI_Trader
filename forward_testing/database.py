"""SQLite persistence for append-only forward-testing evidence.

Signals, evaluations, events, alerts, and snapshots are historical facts and
are never updated. Only the current paper-position projection is mutable; its
complete transition history remains available in ``paper_events``.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


DEFAULT_DATABASE = Path("data/forward_testing.db")


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;

CREATE TABLE IF NOT EXISTS live_sessions (
    session_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    session_date TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    settings_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    signal_count INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    signal_date TEXT NOT NULL,
    signal_time TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    price REAL,
    score REAL,
    confidence REAL,
    rr REAL,
    ai_probability REAL,
    market_regime TEXT,
    ranking REAL,
    reason TEXT,
    indicators_json TEXT NOT NULL,
    settings_hash TEXT NOT NULL,
    run_id TEXT NOT NULL,
    buy_low REAL,
    buy_high REAL,
    stop_loss REAL,
    target1 REAL,
    target2 REAL
);

CREATE TRIGGER IF NOT EXISTS signals_immutable_update
BEFORE UPDATE ON signals BEGIN
    SELECT RAISE(ABORT, 'signals are immutable');
END;

CREATE TRIGGER IF NOT EXISTS signals_immutable_delete
BEFORE DELETE ON signals BEGIN
    SELECT RAISE(ABORT, 'signals are immutable');
END;

CREATE TABLE IF NOT EXISTS signal_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    horizon_days INTEGER NOT NULL,
    evaluated_at TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    bars_available INTEGER NOT NULL,
    return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    hit_tp INTEGER NOT NULL,
    hit_sl INTEGER NOT NULL,
    status TEXT NOT NULL,
    UNIQUE(signal_id, horizon_days)
);

CREATE TRIGGER IF NOT EXISTS evaluations_immutable_update
BEFORE UPDATE ON signal_evaluations BEGIN
    SELECT RAISE(ABORT, 'evaluations are immutable');
END;

CREATE TRIGGER IF NOT EXISTS evaluations_immutable_delete
BEFORE DELETE ON signal_evaluations BEGIN
    SELECT RAISE(ABORT, 'evaluations are immutable');
END;

CREATE TABLE IF NOT EXISTS signal_events (
    event_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES signals(signal_id),
    event_type TEXT NOT NULL,
    event_time TEXT NOT NULL,
    details_json TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    alert_key TEXT NOT NULL UNIQUE,
    signal_id TEXT REFERENCES signals(signal_id),
    alert_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    message TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paper_positions (
    position_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL UNIQUE REFERENCES signals(signal_id),
    ticker TEXT NOT NULL,
    sector TEXT NOT NULL DEFAULT 'Unknown',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    entry_date TEXT,
    entry_price REAL,
    shares INTEGER NOT NULL DEFAULT 0,
    position_value REAL NOT NULL DEFAULT 0,
    risk_amount REAL NOT NULL DEFAULT 0,
    stop_loss REAL,
    target1 REAL,
    target2 REAL,
    exit_date TEXT,
    exit_price REAL,
    exit_reason TEXT,
    realized_profit REAL NOT NULL DEFAULT 0,
    holding_days INTEGER
);

CREATE TABLE IF NOT EXISTS paper_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL REFERENCES paper_positions(position_id),
    event_type TEXT NOT NULL,
    event_time TEXT NOT NULL,
    details_json TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    snapshot_date TEXT NOT NULL,
    snapshot_time TEXT NOT NULL,
    run_id TEXT NOT NULL,
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    exposure_pct REAL NOT NULL,
    current_drawdown_pct REAL NOT NULL,
    open_risk REAL NOT NULL,
    open_positions INTEGER NOT NULL,
    closed_trades INTEGER NOT NULL,
    sector_allocation_json TEXT NOT NULL,
    UNIQUE(snapshot_date, run_id)
);

CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_evaluations_signal ON signal_evaluations(signal_id);
CREATE INDEX IF NOT EXISTS idx_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
"""


class ForwardDatabase:
    """Small transactional repository that survives application restarts."""

    def __init__(self, path=DEFAULT_DATABASE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self):
        with self.connect() as connection:
            connection.executescript(SCHEMA)

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

    def rows(self, sql, parameters=()):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def row(self, sql, parameters=()):
        with self.connect() as connection:
            value = connection.execute(sql, parameters).fetchone()
            return dict(value) if value else None

