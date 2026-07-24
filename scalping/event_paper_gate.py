"""Phase 6 — paper-forward-test activation gate + immutable signal recorder.

The event-driven Range Scanner stays DISABLED for production. This module only
decides whether a symbol may have a *paper-only* signal recorded, and records it
immutably (original signals are never rewritten; outcomes are appended later).

A signal is permitted for paper recording ONLY when ALL hold (no threshold here
is new — they reuse the event-quality gate):
  * valid continuous-session classification (session was phase-classified),
  * no fatal continuous-trading connection outage above 300 s,
  * EVENT_DATA_VALID,
  * RANGE_CONFIRMED,
  * fresh Last/Bid/Ask,
  * acceptable spread,
  * acceptable liquidity/turnover,
  * a valid entry / stop / targets / net RR.

No automatic trading. No production activation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


FATAL_OUTAGE_LIMIT_SECONDS = 300.0   # reuses the RANGE_CONFIRMED gate; never lowered
DEFAULT_PAPER_DB = "data/scalping_event_paper.db"


@dataclass(frozen=True)
class PaperGateDecision:
    permitted: bool
    reasons: tuple = field(default_factory=tuple)


def paper_recording_permitted(
    *,
    session_phase_valid: bool,
    max_fatal_outage_seconds: float | None,
    event_status: str,
    range_status: str,
    quote_age_seconds: float | None,
    spread_ok: bool,
    liquidity_ok: bool,
    entry_valid: bool,
    net_rr: float | None,
    max_quote_age_seconds: float = 90.0,
    min_net_rr: float = 1.5,
):
    """Return a :class:`PaperGateDecision`. All conditions must pass."""

    reasons = []
    if not session_phase_valid:
        reasons.append("session not phase-classified (continuous vs auction)")
    if max_fatal_outage_seconds is None or max_fatal_outage_seconds > FATAL_OUTAGE_LIMIT_SECONDS:
        reasons.append(
            f"fatal continuous outage {max_fatal_outage_seconds}s > {FATAL_OUTAGE_LIMIT_SECONDS}s")
    if event_status != "EVENT_DATA_VALID":
        reasons.append(f"event status {event_status} != EVENT_DATA_VALID")
    if range_status != "RANGE_CONFIRMED":
        reasons.append(f"range status {range_status} != RANGE_CONFIRMED")
    if quote_age_seconds is None or quote_age_seconds > max_quote_age_seconds:
        reasons.append(f"stale quote ({quote_age_seconds}s)")
    if not spread_ok:
        reasons.append("spread not acceptable")
    if not liquidity_ok:
        reasons.append("liquidity/turnover not acceptable")
    if not entry_valid:
        reasons.append("no valid entry/stop/targets")
    if net_rr is None or net_rr < min_net_rr:
        reasons.append(f"net RR {net_rr} < {min_net_rr}")
    return PaperGateDecision(permitted=not reasons, reasons=tuple(reasons))


class ImmutablePaperSignalStore:
    """Append-only paper signals; outcomes are added, originals never rewritten."""

    def __init__(self, path=DEFAULT_PAPER_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_signals (
                    signal_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    decision_timestamp TEXT NOT NULL,
                    genuine_events INTEGER,
                    connection_status TEXT,
                    range_levels_json TEXT,
                    entry REAL, stop REAL, target1 REAL, target2 REAL, target3 REAL,
                    net_rr REAL, spread_percent REAL, quote_age_seconds REAL,
                    event_quality_score REAL, range_confidence_score REAL, liquidity_score REAL,
                    recorded_at TEXT NOT NULL,
                    frozen INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS paper_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    price REAL, mfe REAL, mae REAL,
                    stop_hit INTEGER, target_hit INTEGER,
                    execution_feasible INTEGER,
                    evaluated_at TEXT NOT NULL,
                    UNIQUE(signal_id, horizon_minutes)
                );
                """
            )

    def record_signal(self, signal: dict):
        """Insert a signal BEFORE its outcome is known. Refuses to overwrite."""

        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM paper_signals WHERE signal_id=?", (signal["signal_id"],)
            ).fetchone()
            if exists:
                raise ValueError(f"signal {signal['signal_id']} already recorded (immutable)")
            conn.execute(
                """INSERT INTO paper_signals
                (signal_id, symbol, session_date, decision_timestamp, genuine_events,
                 connection_status, range_levels_json, entry, stop, target1, target2,
                 target3, net_rr, spread_percent, quote_age_seconds, event_quality_score,
                 range_confidence_score, liquidity_score, recorded_at, frozen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    signal["signal_id"], signal["symbol"], signal["session_date"],
                    signal["decision_timestamp"], signal.get("genuine_events"),
                    signal.get("connection_status"),
                    json.dumps(signal.get("range_levels", {}), default=str),
                    signal.get("entry"), signal.get("stop"), signal.get("target1"),
                    signal.get("target2"), signal.get("target3"), signal.get("net_rr"),
                    signal.get("spread_percent"), signal.get("quote_age_seconds"),
                    signal.get("event_quality_score"), signal.get("range_confidence_score"),
                    signal.get("liquidity_score"),
                    datetime.now(timezone.utc).astimezone().isoformat(),
                ),
            )
        return signal["signal_id"]

    def record_outcome(self, signal_id, horizon_minutes, outcome: dict):
        """Append a later outcome (1/3/5/10/20-min). Never edits the signal."""

        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM paper_signals WHERE signal_id=?", (signal_id,)).fetchone():
                raise ValueError(f"unknown signal_id {signal_id}")
            conn.execute(
                """INSERT OR IGNORE INTO paper_outcomes
                (signal_id, horizon_minutes, price, mfe, mae, stop_hit, target_hit,
                 execution_feasible, evaluated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    signal_id, int(horizon_minutes), outcome.get("price"),
                    outcome.get("mfe"), outcome.get("mae"),
                    int(bool(outcome.get("stop_hit"))), int(bool(outcome.get("target_hit"))),
                    int(bool(outcome.get("execution_feasible"))),
                    datetime.now(timezone.utc).astimezone().isoformat(),
                ),
            )

    def signal(self, signal_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM paper_signals WHERE signal_id=?", (signal_id,)).fetchone()
        return dict(row) if row else None

    def count(self):
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM paper_signals").fetchone()[0]
