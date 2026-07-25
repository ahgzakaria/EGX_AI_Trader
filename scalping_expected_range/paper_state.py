"""Durable, restart-safe SQLite state for the live paper monitor.

Holds the event-processing cursor, per-(symbol,scenario) scenario state and
activation cycle, immutable READY signals (unique per activation cycle — atomic
INSERT OR IGNORE prevents duplicates after a restart), the transition log and
per-session counters. WAL mode + transactions give durability across restarts.

Records only — never places an order or changes any strategy parameter.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

DEFAULT_STATE_DB = "data/expected_range_paper_state.db"


class PaperStateStore:
    def __init__(self, db_path=DEFAULT_STATE_DB):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init(self):
        with self._connect() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS cursor (
                    session_date TEXT PRIMARY KEY,
                    last_received_at TEXT,
                    last_wall_clock TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS scenario_state (
                    session_date TEXT, symbol TEXT, scenario TEXT,
                    state TEXT, activation_cycle INTEGER,
                    ready_recorded_cycle INTEGER, updated_at TEXT,
                    PRIMARY KEY (session_date, symbol, scenario)
                );
                CREATE TABLE IF NOT EXISTS signals (
                    signal_uuid TEXT PRIMARY KEY,
                    session_date TEXT, symbol TEXT, scenario TEXT,
                    activation_cycle INTEGER, payload TEXT, created_at TEXT,
                    UNIQUE (session_date, symbol, scenario, activation_cycle)
                );
                CREATE TABLE IF NOT EXISTS transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_date TEXT, symbol TEXT, scenario TEXT,
                    from_state TEXT, to_state TEXT, activation_cycle INTEGER,
                    at_cairo TEXT, note TEXT
                );
                CREATE TABLE IF NOT EXISTS counters (
                    session_date TEXT, key TEXT, value INTEGER,
                    PRIMARY KEY (session_date, key)
                );
                CREATE TABLE IF NOT EXISTS feed_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_date TEXT, at_cairo TEXT, market_value_gap_seconds REAL,
                    status TEXT
                );
                """
            )

    # -- cursor -----------------------------------------------------------

    def get_cursor(self, session_date):
        with self._connect() as c:
            row = c.execute("SELECT last_received_at FROM cursor WHERE session_date=?",
                            (session_date,)).fetchone()
        return row["last_received_at"] if row else None

    def set_cursor(self, session_date, last_received_at):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            c.execute(
                "INSERT INTO cursor(session_date,last_received_at,last_wall_clock,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(session_date) DO UPDATE SET "
                "last_received_at=excluded.last_received_at, last_wall_clock=excluded.last_wall_clock, "
                "updated_at=excluded.updated_at",
                (session_date, last_received_at, now, now))

    # -- scenario state ---------------------------------------------------

    def get_state(self, session_date, symbol, scenario):
        with self._connect() as c:
            row = c.execute(
                "SELECT state, activation_cycle, ready_recorded_cycle FROM scenario_state "
                "WHERE session_date=? AND symbol=? AND scenario=?",
                (session_date, symbol, scenario)).fetchone()
        if not row:
            return {"state": "INIT", "activation_cycle": 0, "ready_recorded_cycle": -1}
        return {"state": row["state"], "activation_cycle": row["activation_cycle"],
                "ready_recorded_cycle": row["ready_recorded_cycle"]}

    def set_state(self, session_date, symbol, scenario, state, activation_cycle,
                  ready_recorded_cycle):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            c.execute(
                "INSERT INTO scenario_state(session_date,symbol,scenario,state,activation_cycle,"
                "ready_recorded_cycle,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(session_date,symbol,scenario) DO UPDATE SET state=excluded.state, "
                "activation_cycle=excluded.activation_cycle, "
                "ready_recorded_cycle=excluded.ready_recorded_cycle, updated_at=excluded.updated_at",
                (session_date, symbol, scenario, state, activation_cycle, ready_recorded_cycle, now))

    def record_transition(self, session_date, symbol, scenario, from_state, to_state,
                          activation_cycle, at_cairo, note=""):
        with self._connect() as c:
            c.execute(
                "INSERT INTO transitions(session_date,symbol,scenario,from_state,to_state,"
                "activation_cycle,at_cairo,note) VALUES(?,?,?,?,?,?,?,?)",
                (session_date, symbol, scenario, from_state, to_state, activation_cycle,
                 at_cairo, note))

    def try_record_signal(self, signal: dict) -> bool:
        """Atomically insert a signal; returns False if a duplicate already exists.

        The UNIQUE(session_date,symbol,scenario,activation_cycle) guard makes this
        idempotent across process restarts — the same activation can never produce
        two signals.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO signals(signal_uuid,session_date,symbol,scenario,"
                "activation_cycle,payload,created_at) VALUES(?,?,?,?,?,?,?)",
                (signal["SignalUUID"], signal["SessionDate"], signal["Symbol"],
                 signal["Scenario"], int(signal["ActivationCycle"]),
                 json.dumps(signal, default=str), now))
            return cur.rowcount == 1

    # -- counters + feed health ------------------------------------------

    def bump(self, session_date, key, n=1):
        with self._connect() as c:
            c.execute(
                "INSERT INTO counters(session_date,key,value) VALUES(?,?,?) "
                "ON CONFLICT(session_date,key) DO UPDATE SET value=value+excluded.value",
                (session_date, key, n))

    def counters(self, session_date):
        with self._connect() as c:
            rows = c.execute("SELECT key,value FROM counters WHERE session_date=?",
                             (session_date,)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def record_feed_health(self, session_date, at_cairo, gap_seconds, status):
        with self._connect() as c:
            c.execute("INSERT INTO feed_health(session_date,at_cairo,market_value_gap_seconds,status)"
                      " VALUES(?,?,?,?)", (session_date, at_cairo, gap_seconds, status))

    # -- readers ----------------------------------------------------------

    def signals(self, session_date=None):
        q = "SELECT payload FROM signals"
        args = ()
        if session_date:
            q += " WHERE session_date=?"
            args = (session_date,)
        with self._connect() as c:
            rows = c.execute(q + " ORDER BY created_at", args).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def transitions(self, session_date):
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM transitions WHERE session_date=? ORDER BY id", (session_date,)
            ).fetchall()
        return [dict(r) for r in rows]

    def states(self, session_date):
        with self._connect() as c:
            rows = c.execute("SELECT * FROM scenario_state WHERE session_date=?",
                             (session_date,)).fetchall()
        return [dict(r) for r in rows]
