"""Normalized daily cache with per-row provenance + versioning (no silent rewrite).

An application-owned SQLite store of NormalizedDailyBar rows. A normal re-run is
idempotent (same version, unchanged). A --force-rebuild inserts a NEW version for
the same symbol/date; earlier versions remain auditable. Existing rows are never
silently overwritten, and Rubix-derived rows coexist with external/Yahoo rows
(one active row per symbol/date/source, provenance preserved).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from core.daily_bridge.schema import CSV_FIELDS, FINAL, NormalizedDailyBar

DEFAULT_DB = "data/normalized_daily_cache.db"


class NormalizedDailyCache:
    def __init__(self, db_path=DEFAULT_DB):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self):
        cols = ", ".join(f"{c} TEXT" for c in CSV_FIELDS)
        with self._connect() as c:
            c.execute(f"""CREATE TABLE IF NOT EXISTS daily_bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                written_at TEXT,
                {cols},
                UNIQUE(canonical_symbol, session_date, source_type, version)
            )""")

    def upsert_bars(self, bars, force_rebuild=False):
        """Append bars idempotently. Returns (inserted, skipped, versioned)."""
        inserted = skipped = versioned = 0
        with self._connect() as c:
            for bar in bars:
                row = bar.as_row()
                key = (row["canonical_symbol"], row["session_date"], row["source_type"])
                existing = c.execute(
                    "SELECT version, source_row_hash, active FROM daily_bars "
                    "WHERE canonical_symbol=? AND session_date=? AND source_type=? "
                    "ORDER BY version DESC LIMIT 1", key).fetchone()
                if existing is None:
                    self._insert(c, row, version=1, active=1)
                    inserted += 1
                elif force_rebuild:
                    # new version; deactivate the prior active row (auditable history kept)
                    c.execute("UPDATE daily_bars SET active=0 WHERE canonical_symbol=? "
                              "AND session_date=? AND source_type=?", key)
                    self._insert(c, row, version=existing["version"] + 1, active=1)
                    versioned += 1
                elif existing["source_row_hash"] == row["source_row_hash"]:
                    skipped += 1                       # identical -> idempotent no-op
                else:
                    # content changed without force: keep old ACTIVE, record new as
                    # inactive shadow so nothing is silently overwritten.
                    self._insert(c, row, version=existing["version"] + 1, active=0)
                    skipped += 1
        return inserted, skipped, versioned

    def _insert(self, c, row, version, active):
        cols = ["version", "active", "written_at"] + CSV_FIELDS
        vals = [version, active, datetime.now(timezone.utc).isoformat()] + \
               [str(row.get(f)) if row.get(f) is not None else None for f in CSV_FIELDS]
        placeholders = ",".join("?" * len(cols))
        c.execute(f"INSERT INTO daily_bars ({','.join(cols)}) VALUES ({placeholders})", vals)

    def latest_final_session(self, source_type=None):
        """Most recent session_date that has any active FINAL bar."""
        q = ("SELECT MAX(session_date) d FROM daily_bars WHERE active=1 "
             "AND finalization_status=?")
        args = [FINAL]
        if source_type:
            q += " AND source_type=?"
            args.append(source_type)
        with self._connect() as c:
            row = c.execute(q, args).fetchone()
        return row["d"] if row and row["d"] else None

    def final_bars_after(self, after_date, source_type="RUBIX_DERIVED"):
        """Active FINAL bars strictly newer than ``after_date`` (for history overlay)."""
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM daily_bars WHERE active=1 AND finalization_status=? "
                "AND source_type=? AND session_date > ? ORDER BY session_date",
                (FINAL, source_type, after_date)).fetchall()
        return [dict(r) for r in rows]

    def symbol_final(self, canonical_symbol, session_date, source_type="RUBIX_DERIVED"):
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM daily_bars WHERE active=1 AND canonical_symbol=? "
                "AND session_date=? AND source_type=? ORDER BY version DESC LIMIT 1",
                (canonical_symbol, session_date, source_type)).fetchone()
        return dict(row) if row else None

    def all_active(self, session_date=None):
        q = "SELECT * FROM daily_bars WHERE active=1"
        args = ()
        if session_date:
            q += " AND session_date=?"
            args = (session_date,)
        with self._connect() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY session_date, canonical_symbol", args)]
