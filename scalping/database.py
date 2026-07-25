"""Dedicated WAL-backed persistence for append-only scalping evidence."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from scalping.models import Opportunity


SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;

CREATE TABLE IF NOT EXISTS signals (
 signal_id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
 session_date TEXT NOT NULL, observed_at TEXT NOT NULL, ticker TEXT NOT NULL,
 setup TEXT NOT NULL, signal_price REAL NOT NULL, bid REAL NOT NULL,
 ask REAL NOT NULL, volume REAL NOT NULL, spread_percent REAL NOT NULL,
 score REAL NOT NULL, reasons_json TEXT NOT NULL, freshness TEXT NOT NULL,
 actionable INTEGER NOT NULL, blocked_reason TEXT, ai_probability REAL
);
CREATE TRIGGER IF NOT EXISTS scalping_signals_no_update BEFORE UPDATE ON signals
BEGIN SELECT RAISE(ABORT,'scalping signals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scalping_signals_no_delete BEFORE DELETE ON signals
BEGIN SELECT RAISE(ABORT,'scalping signals are immutable'); END;

CREATE TABLE IF NOT EXISTS entry_attempts (
 attempt_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL REFERENCES signals(signal_id),
 attempted_at TEXT NOT NULL, requested_price REAL, quantity INTEGER,
 status TEXT NOT NULL, reason TEXT
);
CREATE TABLE IF NOT EXISTS fills (
 fill_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL REFERENCES signals(signal_id),
 position_id TEXT NOT NULL UNIQUE, filled_at TEXT NOT NULL, side TEXT NOT NULL,
 signal_price REAL NOT NULL, requested_price REAL NOT NULL,
 actual_fill REAL NOT NULL, quantity INTEGER NOT NULL,
 target_price REAL NOT NULL, stop_price REAL NOT NULL,
 commission REAL NOT NULL, slippage_cost REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS scalping_fills_no_update BEFORE UPDATE ON fills
BEGIN SELECT RAISE(ABORT,'scalping fills are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scalping_fills_no_delete BEFORE DELETE ON fills
BEGIN SELECT RAISE(ABORT,'scalping fills are immutable'); END;
CREATE TABLE IF NOT EXISTS open_positions (
 position_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL UNIQUE REFERENCES signals(signal_id),
 ticker TEXT NOT NULL, setup TEXT NOT NULL, opened_at TEXT NOT NULL,
 entry_fill REAL NOT NULL, target_price REAL NOT NULL, stop_price REAL NOT NULL,
 quantity INTEGER NOT NULL, entry_cost REAL NOT NULL, status TEXT NOT NULL,
 current_bid REAL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exits (
 exit_id TEXT PRIMARY KEY, position_id TEXT NOT NULL UNIQUE,
 exited_at TEXT NOT NULL, status TEXT NOT NULL, exit_fill REAL NOT NULL,
 gross_return_percent REAL NOT NULL, trading_costs REAL NOT NULL,
 slippage_cost REAL NOT NULL, net_return_percent REAL NOT NULL,
 realized_pnl REAL NOT NULL, ambiguous_same_bar INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS scalping_exits_no_update BEFORE UPDATE ON exits
BEGIN SELECT RAISE(ABORT,'scalping exits are immutable'); END;
CREATE TRIGGER IF NOT EXISTS scalping_exits_no_delete BEFORE DELETE ON exits
BEGIN SELECT RAISE(ABORT,'scalping exits are immutable'); END;
CREATE TABLE IF NOT EXISTS rejected_opportunities (
 rejection_id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
 observed_at TEXT NOT NULL, ticker TEXT NOT NULL, setup TEXT NOT NULL,
 reason TEXT NOT NULL, details_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_risk_state (
 session_date TEXT PRIMARY KEY, starting_equity REAL NOT NULL,
 realized_pnl REAL NOT NULL, trades_count INTEGER NOT NULL,
 open_positions INTEGER NOT NULL, consecutive_losses INTEGER NOT NULL,
 portfolio_heat REAL NOT NULL, symbol_exposure_json TEXT NOT NULL,
 last_exit_at_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_summaries (
 summary_id TEXT PRIMARY KEY, session_date TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL, metrics_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
 alert_id TEXT PRIMARY KEY, alert_key TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL, alert_type TEXT NOT NULL, ticker TEXT,
 message TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scalping_signals_date ON signals(session_date);
CREATE INDEX IF NOT EXISTS idx_scalping_positions_status ON open_positions(status);
CREATE INDEX IF NOT EXISTS idx_scalping_exits_time ON exits(exited_at);
CREATE INDEX IF NOT EXISTS idx_scalping_alerts_time ON alerts(created_at);
"""


class ScalpingDatabase:
    def __init__(self, path="data/scalping.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
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

    def integrity_check(self):
        with self.connect() as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        return {"status": result, "journal_mode": str(mode).upper(), "path": str(self.path)}

    def insert_signal(self, opportunity: Opportunity):
        minute = opportunity.timestamp.replace(second=0, microsecond=0).isoformat()
        dedupe = f"{opportunity.ticker}|{opportunity.setup.value}|{minute}"
        signal_id = str(uuid4())
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    signal_id, dedupe, opportunity.timestamp.date().isoformat(),
                    opportunity.timestamp.isoformat(), opportunity.ticker,
                    opportunity.setup.value, opportunity.signal_price,
                    opportunity.bid, opportunity.ask, opportunity.volume,
                    opportunity.spread_percent, opportunity.score,
                    json.dumps(opportunity.reasons), opportunity.freshness,
                    int(opportunity.actionable), opportunity.blocked_reason,
                    opportunity.ai_probability,
                ),
            )
            inserted = cursor.rowcount == 1
            if not inserted:
                row = connection.execute(
                    "SELECT signal_id FROM signals WHERE dedupe_key=?", (dedupe,)
                ).fetchone()
                signal_id = row[0]
        return signal_id, inserted

    def record_rejection(self, opportunity: Opportunity, reason):
        minute = opportunity.timestamp.replace(second=0, microsecond=0).isoformat()
        key = f"{opportunity.ticker}|{opportunity.setup.value}|{minute}|{reason}"
        with self.transaction() as connection:
            return connection.execute(
                """INSERT OR IGNORE INTO rejected_opportunities
                   VALUES (?,?,?,?,?,?,?)""",
                (str(uuid4()), key, opportunity.timestamp.isoformat(),
                 opportunity.ticker, opportunity.setup.value, str(reason),
                 json.dumps({"score": opportunity.score, "reasons": opportunity.reasons})),
            ).rowcount == 1

    def record_entry_attempt(self, signal_id, attempted_at, requested_price, quantity, status, reason=None):
        attempt_id = str(uuid4())
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO entry_attempts VALUES (?,?,?,?,?,?,?)",
                (attempt_id, signal_id, attempted_at.isoformat(), requested_price,
                 int(quantity), status, reason),
            )
        return attempt_id

    def record_fill_and_position(self, signal_id, ticker, setup, timestamp, fill):
        position_id = str(uuid4())
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid4()), signal_id, position_id, timestamp.isoformat(), "BUY",
                 fill.signal_price, fill.requested_entry_price,
                 fill.actual_entry_fill, fill.quantity, fill.target_price,
                 fill.stop_price, fill.entry_cost, fill.slippage_cost),
            )
            connection.execute(
                "INSERT INTO open_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (position_id, signal_id, ticker, str(setup), timestamp.isoformat(),
                 fill.actual_entry_fill, fill.target_price, fill.stop_price,
                 fill.quantity, fill.entry_cost, "OPEN", fill.actual_entry_fill,
                 timestamp.isoformat()),
            )
        return position_id

    def close_position(self, position_id, result):
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO exits VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid4()), position_id, result.exited_at.isoformat(),
                 result.status.value, result.exit_fill, result.gross_return_percent,
                 result.trading_costs, result.slippage_cost,
                 result.net_return_percent, result.realized_pnl,
                 int(result.ambiguous_same_bar)),
            )
            connection.execute(
                "UPDATE open_positions SET status=?,current_bid=?,updated_at=? WHERE position_id=?",
                (result.status.value, result.exit_fill, result.exited_at.isoformat(), position_id),
            )

    def update_position_bid(self, position_id, bid, timestamp):
        with self.transaction() as connection:
            connection.execute(
                "UPDATE open_positions SET current_bid=?,updated_at=? WHERE position_id=? AND status='OPEN'",
                (float(bid), timestamp.isoformat(), position_id),
            )

    def add_alert(self, alert_type, message, ticker=None, key=None):
        created = datetime.now(timezone.utc).astimezone().isoformat()
        key = key or f"{alert_type}|{ticker or ''}|{created}"
        with self.transaction() as connection:
            return connection.execute(
                "INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?,?,0)",
                (str(uuid4()), key, created, alert_type, ticker, message),
            ).rowcount == 1

    def save_session_summary(self, session_date, metrics):
        """Append one immutable end/session snapshot per EGX date."""

        with self.transaction() as connection:
            return connection.execute(
                "INSERT OR IGNORE INTO session_summaries VALUES (?,?,?,?)",
                (str(uuid4()), str(session_date),
                 datetime.now(timezone.utc).astimezone().isoformat(),
                 json.dumps(metrics, default=str)),
            ).rowcount == 1

    def rows(self, sql, parameters=()):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def row(self, sql, parameters=()):
        with self.connect() as connection:
            value = connection.execute(sql, parameters).fetchone()
            return dict(value) if value else None

    def open_positions(self):
        return self.rows("SELECT * FROM open_positions WHERE status='OPEN' ORDER BY opened_at")

    def save_risk_state(self, state):
        payload = (
            state.session_date, state.starting_equity, state.realized_pnl,
            state.trades_count, state.open_positions, state.consecutive_losses,
            state.portfolio_heat, json.dumps(state.symbol_exposure),
            json.dumps({key: value.isoformat() for key, value in state.last_exit_at.items()}),
            datetime.now(timezone.utc).astimezone().isoformat(),
        )
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO daily_risk_state VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_date) DO UPDATE SET
                   starting_equity=excluded.starting_equity,
                   realized_pnl=excluded.realized_pnl,trades_count=excluded.trades_count,
                   open_positions=excluded.open_positions,
                   consecutive_losses=excluded.consecutive_losses,
                   portfolio_heat=excluded.portfolio_heat,
                   symbol_exposure_json=excluded.symbol_exposure_json,
                   last_exit_at_json=excluded.last_exit_at_json,
                   updated_at=excluded.updated_at""", payload,
            )

    def load_risk_state(self, session_date, starting_equity):
        from scalping.models import DailyRiskState

        row = self.row(
            "SELECT * FROM daily_risk_state WHERE session_date=?", (session_date,)
        )
        if row is None:
            return DailyRiskState(session_date, float(starting_equity))
        exits = {
            key: datetime.fromisoformat(value)
            for key, value in json.loads(row["last_exit_at_json"] or "{}").items()
        }
        return DailyRiskState(
            session_date=row["session_date"], starting_equity=row["starting_equity"],
            realized_pnl=row["realized_pnl"], trades_count=row["trades_count"],
            open_positions=row["open_positions"],
            consecutive_losses=row["consecutive_losses"],
            portfolio_heat=row["portfolio_heat"],
            symbol_exposure=json.loads(row["symbol_exposure_json"] or "{}"),
            last_exit_at=exits,
        )
