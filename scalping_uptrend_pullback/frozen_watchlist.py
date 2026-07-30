"""Immutable, versioned frozen watchlist for UPTREND_PULLBACK_SCALPING.

This store is deliberately separate from the Stable Range-Bound watchlist: its
own database file, its own schema, and a strategy identity
(``UPTREND_PULLBACK_SCALPING_V1``) baked into every identity key, header and
member row. A Range-Bound snapshot and an Uptrend-Pullback snapshot for the same
session can never collide or be read as one another.

Membership and ranking are computed from completed EODHD Daily history through
the D-1 cutoff and published atomically. SQL triggers make published rows
immutable, so live Rubix movement during the session cannot alter the frozen
list. Presentation code reads READY records only.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Callable, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from core.universe import active_symbols

from scalping_uptrend_pullback.config import UptrendPullbackSelectionConfig
from scalping_uptrend_pullback.selection import (
    FrozenUptrendWatchlist,
    UptrendPullbackResult,
    _base,
    _clean_completed_daily,
    _frame_fingerprint,
    _has_eodhd_provenance,
    build_frozen_uptrend_watchlist,
    load_eodhd_daily_history,
)
from scalping_uptrend_pullback.states import (
    EODHD_DAILY,
    SELECTION_INSUFFICIENT,
    SELECTION_STALE,
    SELECTION_UNAVAILABLE,
    STRATEGY_IDENTITY,
)


SCHEMA_VERSION = 1
DEFAULT_DATABASE_PATH = Path("data/uptrend_pullback_watchlists.db")
DEFAULT_UNIVERSE_MANIFEST = Path("data/eodhd/historical_symbol_routing.json")
DATABASE_PATH_ENVIRONMENT_KEY = "UPTREND_PULLBACK_WATCHLIST_DB"

WATCHLIST_READY = "WATCHLIST_READY"
WATCHLIST_NOT_GENERATED = "WATCHLIST_NOT_GENERATED"
INSUFFICIENT_DAILY_HISTORY = "INSUFFICIENT_DAILY_HISTORY"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
PROVENANCE_REJECTED = "PROVENANCE_REJECTED"
CONFIG_VERSION_MISMATCH = "CONFIG_VERSION_MISMATCH"
STRATEGY_IDENTITY_MISMATCH = "STRATEGY_IDENTITY_MISMATCH"
SOURCE_FINGERPRINT_CHANGED = "SOURCE_FINGERPRINT_CHANGED"
GENERATION_FAILED = "GENERATION_FAILED"
NO_ELIGIBLE_SYMBOLS = "NO_ELIGIBLE_SYMBOLS"

GENERATING = "GENERATING"
READY = "READY"
FAILED = "FAILED"
SOURCE_FINGERPRINT_VERIFIED = "SOURCE_FINGERPRINT_VERIFIED"

CAIRO = ZoneInfo("Africa/Cairo")
EGX_WEEKDAYS = {0, 1, 2, 3, 6}


SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;

CREATE TABLE IF NOT EXISTS uptrend_watchlist_schema (
 schema_version INTEGER PRIMARY KEY,
 installed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS uptrend_watchlist_headers (
 watchlist_id TEXT PRIMARY KEY,
 identity_key TEXT NOT NULL UNIQUE,
 strategy_identity TEXT NOT NULL,
 schema_version INTEGER NOT NULL,
 target_session_date TEXT NOT NULL,
 historical_data_cutoff TEXT NOT NULL,
 provider TEXT NOT NULL,
 lookback_sessions INTEGER NOT NULL,
 minimum_valid_sessions INTEGER NOT NULL,
 metric_version TEXT NOT NULL,
 config_version TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL,
 source_fingerprint_status TEXT NOT NULL,
 universe_fingerprint TEXT NOT NULL,
 snapshot_id TEXT NOT NULL DEFAULT '',
 candidate_limit INTEGER NOT NULL CHECK(candidate_limit > 0),
 eligible_count INTEGER NOT NULL DEFAULT 0 CHECK(eligible_count >= 0),
 displayed_count INTEGER NOT NULL DEFAULT 0 CHECK(displayed_count >= 0),
 generated_at TEXT NOT NULL,
 generation_run_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('GENERATING','READY','FAILED')),
 generation_reason TEXT NOT NULL,
 failure_code TEXT,
 failure_detail TEXT
);

CREATE TABLE IF NOT EXISTS uptrend_watchlist_members (
 watchlist_id TEXT NOT NULL
  REFERENCES uptrend_watchlist_headers(watchlist_id) ON DELETE RESTRICT,
 symbol TEXT NOT NULL,
 strategy_identity TEXT NOT NULL,
 eligible_rank INTEGER NOT NULL CHECK(eligible_rank > 0),
 scoreable_rank INTEGER NOT NULL CHECK(scoreable_rank > 0),
 displayed_candidate INTEGER NOT NULL CHECK(displayed_candidate IN (0,1)),
 candidate_state TEXT NOT NULL,
 total_score REAL NOT NULL,
 trend_quality_component REAL NOT NULL,
 support_component REAL NOT NULL,
 pullback_component REAL NOT NULL,
 liquidity_component REAL NOT NULL,
 upside_component REAL NOT NULL,
 last_close REAL NOT NULL,
 ema5 REAL NOT NULL,
 ema10 REAL NOT NULL,
 ema5_slope REAL NOT NULL,
 ema10_slope REAL NOT NULL,
 support_zone_lower REAL NOT NULL,
 support_zone_upper REAL NOT NULL,
 support_zone_centre REAL NOT NULL,
 distance_from_support_percent REAL NOT NULL,
 support_strength REAL NOT NULL,
 support_sources TEXT NOT NULL,
 support_touch_proxy_count INTEGER NOT NULL,
 support_reaction_proxy_count INTEGER NOT NULL,
 invalidation_level REAL NOT NULL,
 first_research_target REAL,
 available_upside_percent REAL,
 reward_risk_ratio REAL,
 pullback_depth_percent REAL NOT NULL,
 liquidity_score REAL NOT NULL,
 valid_session_count INTEGER NOT NULL,
 readiness_status TEXT NOT NULL,
 volume_history_status TEXT NOT NULL,
 deterministic_reasons TEXT NOT NULL,
 source TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL,
 latest_session TEXT NOT NULL,
 data_cutoff TEXT NOT NULL,
 metric_version TEXT NOT NULL,
 config_version TEXT NOT NULL,
 first_touch_order_available INTEGER NOT NULL CHECK(first_touch_order_available = 0),
 PRIMARY KEY(watchlist_id, symbol),
 UNIQUE(watchlist_id, eligible_rank)
);

CREATE INDEX IF NOT EXISTS idx_uptrend_headers_session
 ON uptrend_watchlist_headers(target_session_date, status, generated_at);
CREATE INDEX IF NOT EXISTS idx_uptrend_headers_ready
 ON uptrend_watchlist_headers(status, target_session_date);
CREATE INDEX IF NOT EXISTS idx_uptrend_members_rank
 ON uptrend_watchlist_members(watchlist_id, eligible_rank);

CREATE TRIGGER IF NOT EXISTS uptrend_header_insert_generating
BEFORE INSERT ON uptrend_watchlist_headers
WHEN NEW.status <> 'GENERATING'
BEGIN
 SELECT RAISE(ABORT, 'uptrend watchlist headers must start GENERATING');
END;

CREATE TRIGGER IF NOT EXISTS uptrend_header_strategy_identity
BEFORE INSERT ON uptrend_watchlist_headers
WHEN NEW.strategy_identity <> 'UPTREND_PULLBACK_SCALPING_V1'
BEGIN
 SELECT RAISE(ABORT, 'strategy identity must be UPTREND_PULLBACK_SCALPING_V1');
END;

CREATE TRIGGER IF NOT EXISTS uptrend_header_no_delete
BEFORE DELETE ON uptrend_watchlist_headers
BEGIN
 SELECT RAISE(ABORT, 'uptrend watchlist headers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS uptrend_header_completed_no_update
BEFORE UPDATE ON uptrend_watchlist_headers
WHEN OLD.status <> 'GENERATING'
BEGIN
 SELECT RAISE(ABORT, 'completed uptrend watchlist headers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS uptrend_header_identity_no_update
BEFORE UPDATE ON uptrend_watchlist_headers
WHEN
 NEW.watchlist_id <> OLD.watchlist_id OR
 NEW.identity_key <> OLD.identity_key OR
 NEW.strategy_identity <> OLD.strategy_identity OR
 NEW.schema_version <> OLD.schema_version OR
 NEW.target_session_date <> OLD.target_session_date OR
 NEW.historical_data_cutoff <> OLD.historical_data_cutoff OR
 NEW.provider <> OLD.provider OR
 NEW.lookback_sessions <> OLD.lookback_sessions OR
 NEW.minimum_valid_sessions <> OLD.minimum_valid_sessions OR
 NEW.metric_version <> OLD.metric_version OR
 NEW.config_version <> OLD.config_version OR
 NEW.source_fingerprint <> OLD.source_fingerprint OR
 NEW.source_fingerprint_status <> OLD.source_fingerprint_status OR
 NEW.universe_fingerprint <> OLD.universe_fingerprint OR
 NEW.candidate_limit <> OLD.candidate_limit OR
 NEW.generated_at <> OLD.generated_at OR
 NEW.generation_run_id <> OLD.generation_run_id OR
 NEW.generation_reason <> OLD.generation_reason
BEGIN
 SELECT RAISE(ABORT, 'uptrend watchlist identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS uptrend_member_generating_only
BEFORE INSERT ON uptrend_watchlist_members
WHEN (
 SELECT status FROM uptrend_watchlist_headers
 WHERE watchlist_id=NEW.watchlist_id
) <> 'GENERATING'
BEGIN
 SELECT RAISE(ABORT, 'members can be inserted only while GENERATING');
END;

CREATE TRIGGER IF NOT EXISTS uptrend_member_no_update
BEFORE UPDATE ON uptrend_watchlist_members
BEGIN
 SELECT RAISE(ABORT, 'uptrend watchlist members are immutable');
END;

CREATE TRIGGER IF NOT EXISTS uptrend_member_no_delete
BEFORE DELETE ON uptrend_watchlist_members
BEGIN
 SELECT RAISE(ABORT, 'uptrend watchlist members are immutable');
END;
"""


MEMBER_COLUMNS = (
    "watchlist_id",
    "symbol",
    "strategy_identity",
    "eligible_rank",
    "scoreable_rank",
    "displayed_candidate",
    "candidate_state",
    "total_score",
    "trend_quality_component",
    "support_component",
    "pullback_component",
    "liquidity_component",
    "upside_component",
    "last_close",
    "ema5",
    "ema10",
    "ema5_slope",
    "ema10_slope",
    "support_zone_lower",
    "support_zone_upper",
    "support_zone_centre",
    "distance_from_support_percent",
    "support_strength",
    "support_sources",
    "support_touch_proxy_count",
    "support_reaction_proxy_count",
    "invalidation_level",
    "first_research_target",
    "available_upside_percent",
    "reward_risk_ratio",
    "pullback_depth_percent",
    "liquidity_score",
    "valid_session_count",
    "readiness_status",
    "volume_history_status",
    "deterministic_reasons",
    "source",
    "source_fingerprint",
    "latest_session",
    "data_cutoff",
    "metric_version",
    "config_version",
    "first_touch_order_available",
)


@dataclass(frozen=True)
class UptrendWatchlistIdentity:
    strategy_identity: str
    target_session_date: str
    historical_data_cutoff: str
    provider: str
    lookback_sessions: int
    minimum_valid_sessions: int
    metric_version: str
    config_version: str
    source_fingerprint: str
    universe_fingerprint: str
    candidate_limit: int
    identity_key: str
    watchlist_id: str


@dataclass(frozen=True)
class StoredUptrendWatchlist:
    header: dict
    members: tuple[dict, ...]

    @property
    def displayed(self) -> tuple[dict, ...]:
        return tuple(
            member for member in self.members if member["displayed_candidate"]
        )


@dataclass(frozen=True)
class UptrendWatchlistServiceResult:
    status: str
    record: StoredUptrendWatchlist | None = None
    detail: str | None = None
    reused: bool = False
    snapshot: FrozenUptrendWatchlist | None = None


class UptrendWatchlistRepository:
    """WAL-safe repository with SQL-enforced immutability."""

    def __init__(self, path: str | Path = DEFAULT_DATABASE_PATH):
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
            connection.execute(
                """INSERT OR IGNORE INTO uptrend_watchlist_schema
                   (schema_version, installed_at) VALUES (?, ?)""",
                (SCHEMA_VERSION, _iso_now()),
            )

    def integrity_check(self) -> dict:
        with self.connect() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        return {
            "status": str(integrity),
            "journal_mode": str(journal).upper(),
            "foreign_keys": bool(foreign_keys),
            "schema_version": SCHEMA_VERSION,
            "strategy_identity": STRATEGY_IDENTITY,
            "path": str(self.path),
        }

    def claim(
        self,
        identity: UptrendWatchlistIdentity,
        *,
        generated_at: str,
        generation_run_id: str,
        generation_reason: str,
    ) -> tuple[dict, bool]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM uptrend_watchlist_headers WHERE identity_key=?",
                (identity.identity_key,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return dict(existing), False
            connection.execute(
                """INSERT INTO uptrend_watchlist_headers (
                   watchlist_id, identity_key, strategy_identity, schema_version,
                   target_session_date, historical_data_cutoff, provider,
                   lookback_sessions, minimum_valid_sessions, metric_version,
                   config_version, source_fingerprint,
                   source_fingerprint_status, universe_fingerprint,
                   candidate_limit, eligible_count, displayed_count,
                   generated_at, generation_run_id, status, generation_reason
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identity.watchlist_id,
                    identity.identity_key,
                    identity.strategy_identity,
                    SCHEMA_VERSION,
                    identity.target_session_date,
                    identity.historical_data_cutoff,
                    identity.provider,
                    identity.lookback_sessions,
                    identity.minimum_valid_sessions,
                    identity.metric_version,
                    identity.config_version,
                    identity.source_fingerprint,
                    SOURCE_FINGERPRINT_VERIFIED,
                    identity.universe_fingerprint,
                    identity.candidate_limit,
                    0,
                    0,
                    generated_at,
                    generation_run_id,
                    GENERATING,
                    generation_reason,
                ),
            )
            connection.commit()
            return self.header(identity.watchlist_id), True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def publish(
        self,
        watchlist_id: str,
        members: tuple[dict, ...],
        *,
        snapshot_id: str,
    ) -> StoredUptrendWatchlist:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            header = connection.execute(
                "SELECT * FROM uptrend_watchlist_headers WHERE watchlist_id=?",
                (watchlist_id,),
            ).fetchone()
            if header is None or header["status"] != GENERATING:
                raise ValueError("uptrend watchlist is not in GENERATING state")
            _validate_member_rows(members, int(header["candidate_limit"]))
            placeholders = ",".join("?" for _ in MEMBER_COLUMNS)
            connection.executemany(
                f"""INSERT INTO uptrend_watchlist_members
                    ({",".join(MEMBER_COLUMNS)}) VALUES ({placeholders})""",
                [
                    tuple(member[column] for column in MEMBER_COLUMNS)
                    for member in members
                ],
            )
            displayed_count = sum(
                bool(member["displayed_candidate"]) for member in members
            )
            connection.execute(
                """UPDATE uptrend_watchlist_headers
                   SET eligible_count=?, displayed_count=?, snapshot_id=?,
                       status='READY'
                   WHERE watchlist_id=? AND status='GENERATING'""",
                (len(members), displayed_count, str(snapshot_id), watchlist_id),
            )
            stored, distinct_ranks, displayed = connection.execute(
                """SELECT COUNT(*), COUNT(DISTINCT eligible_rank),
                          COALESCE(SUM(displayed_candidate),0)
                   FROM uptrend_watchlist_members WHERE watchlist_id=?""",
                (watchlist_id,),
            ).fetchone()
            if (
                stored != len(members)
                or distinct_ranks != len(members)
                or displayed != displayed_count
            ):
                raise ValueError("published member verification failed")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        record = self.get_by_id(watchlist_id)
        if record is None or record.header["status"] != READY:
            raise ValueError("READY publication verification failed")
        return record

    def fail(self, watchlist_id: str, code: str, detail: str | None = None):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE uptrend_watchlist_headers
                   SET status='FAILED', failure_code=?, failure_detail=?
                   WHERE watchlist_id=? AND status='GENERATING'""",
                (str(code), _sanitize_detail(detail), watchlist_id),
            )

    def header(self, watchlist_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM uptrend_watchlist_headers WHERE watchlist_id=?",
                (watchlist_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_by_id(self, watchlist_id: str) -> StoredUptrendWatchlist | None:
        with self.connect() as connection:
            header = connection.execute(
                "SELECT * FROM uptrend_watchlist_headers WHERE watchlist_id=?",
                (watchlist_id,),
            ).fetchone()
            if header is None:
                return None
            members = connection.execute(
                """SELECT * FROM uptrend_watchlist_members WHERE watchlist_id=?
                   ORDER BY eligible_rank, symbol""",
                (watchlist_id,),
            ).fetchall()
        return StoredUptrendWatchlist(
            dict(header),
            tuple(_typed_member(dict(member)) for member in members),
        )

    def by_identity(self, identity_key: str) -> StoredUptrendWatchlist | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT watchlist_id FROM uptrend_watchlist_headers
                   WHERE identity_key=?""",
                (identity_key,),
            ).fetchone()
        return self.get_by_id(row[0]) if row else None

    def latest_for_session(
        self, target_session_date: str, *, candidate_limit: int | None = None
    ) -> StoredUptrendWatchlist | None:
        sql = (
            "SELECT watchlist_id FROM uptrend_watchlist_headers "
            "WHERE target_session_date=? AND strategy_identity=? "
        )
        parameters: list[object] = [target_session_date, STRATEGY_IDENTITY]
        if candidate_limit is not None:
            sql += "AND candidate_limit=? "
            parameters.append(int(candidate_limit))
        sql += (
            "ORDER BY CASE status WHEN 'READY' THEN 0 WHEN 'GENERATING' THEN 1 "
            "ELSE 2 END, generated_at DESC LIMIT 1"
        )
        with self.connect() as connection:
            row = connection.execute(sql, tuple(parameters)).fetchone()
        return self.get_by_id(row[0]) if row else None

    def latest_ready(self) -> StoredUptrendWatchlist | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT watchlist_id FROM uptrend_watchlist_headers
                   WHERE status='READY' AND strategy_identity=?
                   ORDER BY target_session_date DESC, generated_at DESC
                   LIMIT 1""",
                (STRATEGY_IDENTITY,),
            ).fetchone()
        return self.get_by_id(row[0]) if row else None


class FrozenUptrendWatchlistService:
    """Session-aware service. Generation is explicit; reading is read-only."""

    def __init__(
        self,
        repository: UptrendWatchlistRepository | None = None,
        *,
        database_path: str | Path | None = None,
        config: UptrendPullbackSelectionConfig | None = None,
        calendar=None,
        history_loader: Callable | None = None,
        snapshot_builder: Callable = build_frozen_uptrend_watchlist,
    ):
        self.config = config or UptrendPullbackSelectionConfig()
        chosen_path = (
            database_path
            or os.getenv(DATABASE_PATH_ENVIRONMENT_KEY)
            or self.config.database_path
            or str(DEFAULT_DATABASE_PATH)
        )
        self.repository = repository or UptrendWatchlistRepository(chosen_path)
        self.calendar = calendar or _DefaultCalendar()
        self.history_loader = history_loader or load_validated_eodhd_histories
        self.snapshot_builder = snapshot_builder

    def target_and_cutoff(
        self,
        target_session_date: date | str | None = None,
        *,
        now: datetime | None = None,
    ) -> tuple[date, date]:
        """Resolve the target session and its D-1 historical cutoff."""

        target = resolve_target_session(
            target_session_date, now=now, calendar=self.calendar
        )
        return target, previous_trading_session(target, calendar=self.calendar)

    def prepare_for_session(
        self,
        target_session_date: date | str | None = None,
        *,
        histories: Mapping[str, pd.DataFrame | None] | None = None,
        unavailable_symbols: set[str] | frozenset[str] = frozenset(),
        candidate_limit: int | None = None,
        generated_at: datetime | str | None = None,
        generation_run_id: str | None = None,
        generation_reason: str = "SESSION_PREPARATION",
        now: datetime | None = None,
    ) -> UptrendWatchlistServiceResult:
        target, cutoff = self.target_and_cutoff(target_session_date, now=now)
        limit = int(candidate_limit or self.config.candidate_display_limit)
        cfg = replace(self.config, candidate_display_limit=limit)

        if histories is None:
            try:
                loaded = self.history_loader(data_cutoff=cutoff, config=cfg)
                if isinstance(loaded, tuple):
                    histories, unavailable_symbols = loaded
                else:
                    histories = loaded
            except Exception as error:
                return UptrendWatchlistServiceResult(
                    SOURCE_UNAVAILABLE,
                    detail=_sanitize_detail(f"{type(error).__name__}: {error}"),
                )

        normalized = {
            _base(symbol): frame for symbol, frame in (histories or {}).items()
        }
        unavailable = {_base(symbol) for symbol in unavailable_symbols}
        all_symbols = sorted(set(normalized) | unavailable)
        if not all_symbols:
            return UptrendWatchlistServiceResult(
                SOURCE_UNAVAILABLE, detail="No EODHD Daily histories supplied"
            )

        rejected = sorted(
            symbol
            for symbol, frame in normalized.items()
            if frame is not None and not _has_eodhd_provenance(frame)
        )
        if rejected:
            return UptrendWatchlistServiceResult(
                PROVENANCE_REJECTED,
                detail=(
                    "Non-EODHD or missing provider provenance: "
                    + ", ".join(rejected[:10])
                ),
            )

        identity = make_uptrend_watchlist_identity(
            target_session_date=target,
            historical_data_cutoff=cutoff,
            config=cfg,
            source_fingerprint=fingerprint_histories(
                normalized,
                unavailable_symbols=unavailable,
                data_cutoff=cutoff,
                lookback_sessions=cfg.lookback_sessions,
            ),
            universe_fingerprint=fingerprint_universe(all_symbols),
            candidate_limit=limit,
        )
        stamp = _iso_timestamp(generated_at)
        run_id = generation_run_id or str(uuid4())
        try:
            header, created = self.repository.claim(
                identity,
                generated_at=stamp,
                generation_run_id=run_id,
                generation_reason=generation_reason,
            )
        except Exception as error:
            return UptrendWatchlistServiceResult(
                GENERATION_FAILED,
                detail=_sanitize_detail(f"{type(error).__name__}: {error}"),
            )
        if not created:
            return _result_for_existing(
                self.repository.get_by_id(header["watchlist_id"])
            )

        try:
            snapshot: FrozenUptrendWatchlist = self.snapshot_builder(
                normalized,
                data_cutoff=cutoff,
                unavailable_symbols=unavailable,
                generated_at=stamp,
                config=cfg,
            )
            eligible = sorted(
                (result for result in snapshot.results if result.eligible),
                key=lambda result: (result.eligible_rank or 10**9, result.symbol),
            )
            if not eligible:
                code = _empty_result_code(snapshot)
                self.repository.fail(identity.watchlist_id, code)
                return UptrendWatchlistServiceResult(
                    code,
                    snapshot=snapshot,
                )
            members = tuple(
                _member_from_result(identity.watchlist_id, result)
                for result in eligible
            )
            record = self.repository.publish(
                identity.watchlist_id,
                members,
                snapshot_id=snapshot.snapshot_id,
            )
            return UptrendWatchlistServiceResult(
                WATCHLIST_READY,
                record,
                snapshot=snapshot,
            )
        except Exception as error:
            self.repository.fail(
                identity.watchlist_id,
                GENERATION_FAILED,
                f"{type(error).__name__}: {error}",
            )
            return UptrendWatchlistServiceResult(
                GENERATION_FAILED,
                detail=_sanitize_detail(f"{type(error).__name__}: {error}"),
            )

    def get_for_session(
        self,
        target_session_date: date | str | None = None,
        *,
        candidate_limit: int | None = None,
        expected_source_fingerprint: str | None = None,
        now: datetime | None = None,
    ) -> UptrendWatchlistServiceResult:
        target, cutoff = self.target_and_cutoff(target_session_date, now=now)
        limit = int(candidate_limit or self.config.candidate_display_limit)
        record = self.repository.latest_for_session(
            target.isoformat(), candidate_limit=limit
        )
        if record is None:
            return UptrendWatchlistServiceResult(WATCHLIST_NOT_GENERATED)
        return self._validated(
            record,
            expected_cutoff=cutoff.isoformat(),
            expected_source_fingerprint=expected_source_fingerprint,
        )

    def get_latest_ready(self) -> UptrendWatchlistServiceResult:
        record = self.repository.latest_ready()
        if record is None:
            return UptrendWatchlistServiceResult(WATCHLIST_NOT_GENERATED)
        return self._validated(record)

    def _validated(
        self,
        record: StoredUptrendWatchlist,
        *,
        expected_cutoff: str | None = None,
        expected_source_fingerprint: str | None = None,
    ) -> UptrendWatchlistServiceResult:
        header = record.header
        if header["strategy_identity"] != self.config.strategy_identity or any(
            member["strategy_identity"] != self.config.strategy_identity
            for member in record.members
        ):
            return UptrendWatchlistServiceResult(
                STRATEGY_IDENTITY_MISMATCH,
                record,
                "Stored rows belong to a different strategy identity",
            )
        if (
            header["schema_version"] != SCHEMA_VERSION
            or header["metric_version"] != self.config.metric_version
            or header["config_version"] != self.config.config_version
            or header["lookback_sessions"] != self.config.lookback_sessions
            or (
                header["minimum_valid_sessions"]
                != self.config.minimum_valid_sessions
            )
        ):
            return UptrendWatchlistServiceResult(
                CONFIG_VERSION_MISMATCH,
                record,
                "Stored selector version is incompatible with this config",
            )
        if header["provider"] != EODHD_DAILY or any(
            member["source"] != EODHD_DAILY
            or member["data_cutoff"] != header["historical_data_cutoff"]
            or member["metric_version"] != header["metric_version"]
            or member["config_version"] != header["config_version"]
            or not str(member["source_fingerprint"]).startswith("sha256:")
            or member["first_touch_order_available"]
            for member in record.members
        ):
            return UptrendWatchlistServiceResult(
                PROVENANCE_REJECTED,
                record,
                "Stored provenance is incomplete or is not EODHD_DAILY",
            )
        if (
            expected_cutoff is not None
            and header["historical_data_cutoff"] != expected_cutoff
        ) or (
            expected_source_fingerprint is not None
            and header["source_fingerprint"] != expected_source_fingerprint
        ):
            return UptrendWatchlistServiceResult(
                SOURCE_FINGERPRINT_CHANGED,
                record,
                "Stored cutoff or source identity does not match this session",
            )
        if not str(header["source_fingerprint"]).startswith("sha256:") or (
            header["source_fingerprint_status"] != SOURCE_FINGERPRINT_VERIFIED
        ):
            return UptrendWatchlistServiceResult(
                SOURCE_FINGERPRINT_CHANGED,
                record,
                "Stored source fingerprint is missing or unverified",
            )
        return _result_for_existing(record)


# ---------------------------------------------------------------------------
# Identity, fingerprints, universe
# ---------------------------------------------------------------------------


def make_uptrend_watchlist_identity(
    *,
    target_session_date: date | str,
    historical_data_cutoff: date | str,
    config: UptrendPullbackSelectionConfig,
    source_fingerprint: str,
    universe_fingerprint: str,
    candidate_limit: int,
) -> UptrendWatchlistIdentity:
    """Deterministic identity, always namespaced by the strategy identity."""

    target = _to_date(target_session_date).isoformat()
    cutoff = _to_date(historical_data_cutoff).isoformat()
    if cutoff >= target:
        raise ValueError(
            "historical cutoff must be strictly before the target session (D-1)"
        )
    dimensions = {
        "strategy_identity": config.strategy_identity,
        "schema_version": SCHEMA_VERSION,
        "target_session_date": target,
        "historical_data_cutoff": cutoff,
        "provider": config.source_provider,
        "lookback_sessions": config.lookback_sessions,
        "minimum_valid_sessions": config.minimum_valid_sessions,
        "metric_version": config.metric_version,
        "config_version": config.config_version,
        "source_fingerprint": str(source_fingerprint),
        "universe_fingerprint": str(universe_fingerprint),
        "candidate_limit": int(candidate_limit),
    }
    identity_key = _sha256_json(dimensions)
    return UptrendWatchlistIdentity(
        config.strategy_identity,
        target,
        cutoff,
        config.source_provider,
        config.lookback_sessions,
        config.minimum_valid_sessions,
        config.metric_version,
        config.config_version,
        str(source_fingerprint),
        str(universe_fingerprint),
        int(candidate_limit),
        identity_key,
        f"UPS-{identity_key[:24]}",
    )


def fingerprint_universe(symbols) -> str:
    return f"sha256:{_sha256_json(sorted({_base(symbol) for symbol in symbols}))}"


def fingerprint_histories(
    histories: Mapping[str, pd.DataFrame | None],
    *,
    unavailable_symbols=(),
    data_cutoff: date | str,
    lookback_sessions: int,
) -> str:
    cutoff = _to_date(data_cutoff)
    unavailable = {_base(symbol) for symbol in unavailable_symbols}
    normalized = {_base(symbol): frame for symbol, frame in histories.items()}
    rows = []
    for symbol in sorted(set(normalized) | unavailable):
        frame = normalized.get(symbol)
        fingerprint = None
        if frame is not None:
            selected = _clean_completed_daily(frame, cutoff).tail(
                int(lookback_sessions)
            )
            if not selected.empty:
                fingerprint = _frame_fingerprint(selected)
        rows.append((symbol, fingerprint or "UNAVAILABLE"))
    return f"sha256:{_sha256_json(rows)}"


def validated_eodhd_symbols(manifest_path=None) -> tuple[str, ...]:
    """The Uptrend Pullback selector universe — the ACTIVE authoritative list.

    Membership comes from :mod:`core.universe` (the official EODHD EGX active
    tickers) and from nowhere else. ``historical_symbol_routing.json`` continues
    to carry per-symbol PROVIDER ROUTING, which this migration leaves untouched;
    it is no longer a competing source of universe membership.

    ``manifest_path`` is accepted for call compatibility and is unused.
    """

    return tuple(sorted(active_symbols()))


def load_validated_eodhd_histories(
    *,
    data_cutoff: date | str,
    config: UptrendPullbackSelectionConfig | None = None,
    client=None,
    symbols=None,
    max_workers: int = 8,
) -> tuple[dict[str, pd.DataFrame | None], set[str]]:
    """Load the validated EODHD universe with no provider fallback."""

    cfg = config or UptrendPullbackSelectionConfig()
    chosen = tuple(symbols or validated_eodhd_symbols())
    if not chosen:
        raise ValueError("Validated EODHD universe is empty")
    if client is None:
        from providers.eodhd_client import EODHDClient

        client = EODHDClient()

    def load(symbol):
        return load_eodhd_daily_history(
            symbol, client=client, data_cutoff=data_cutoff, config=cfg
        )

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        loaded = tuple(pool.map(load, chosen))
    return (
        {result.symbol: result.frame for result in loaded},
        {result.symbol for result in loaded if result.frame is None},
    )


def resolve_target_session(
    requested: date | str | None = None,
    *,
    now: datetime | None = None,
    calendar=None,
) -> date:
    chosen = calendar or _DefaultCalendar()
    if requested is None:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        candidate = moment.astimezone(CAIRO).date()
    else:
        candidate = _to_date(requested)
    if chosen.is_trading_day(candidate):
        return candidate
    return chosen.next_trading_session(candidate)


def previous_trading_session(day: date | str, *, calendar=None) -> date:
    chosen = calendar or _DefaultCalendar()
    candidate = _to_date(day) - timedelta(days=1)
    for _ in range(370):
        if chosen.is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise ValueError("Could not resolve previous EGX trading session")


class _DefaultCalendar:
    def is_trading_day(self, day):
        from core.egx_calendar import holiday_dates

        value = _to_date(day)
        return value.weekday() in EGX_WEEKDAYS and value not in holiday_dates()

    def next_trading_session(self, day):
        candidate = _to_date(day) + timedelta(days=1)
        for _ in range(370):
            if self.is_trading_day(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise ValueError("Could not resolve next EGX trading session")


# ---------------------------------------------------------------------------
# Member rows
# ---------------------------------------------------------------------------


def _member_from_result(
    watchlist_id: str, result: UptrendPullbackResult
) -> dict:
    required = {
        "trend profile": result.trend,
        "support zone": result.support,
        "pullback profile": result.pullback,
        "liquidity profile": result.liquidity,
        "upside profile": result.upside,
        "total score": result.total_score,
        "eligible rank": result.eligible_rank,
        "scoreable rank": result.historical_rank,
        "latest session": result.latest_session,
        "source fingerprint": result.source_data_fingerprint,
        "distance from support": result.distance_from_support_percent,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(
            f"{result.symbol} eligible member missing " + ", ".join(missing)
        )
    if result.source_provider != EODHD_DAILY:
        raise ValueError(f"{result.symbol} provider is not EODHD_DAILY")
    if result.strategy_identity != STRATEGY_IDENTITY:
        raise ValueError(f"{result.symbol} strategy identity is not {STRATEGY_IDENTITY}")
    if result.first_touch_order_available:
        raise ValueError(
            f"{result.symbol} claims intraday first-touch ordering, which daily "
            "history cannot support"
        )
    return {
        "watchlist_id": watchlist_id,
        "symbol": result.symbol,
        "strategy_identity": result.strategy_identity,
        "eligible_rank": int(result.eligible_rank),
        "scoreable_rank": int(result.historical_rank),
        "displayed_candidate": int(bool(result.selected_candidate)),
        "candidate_state": result.candidate_state,
        "total_score": float(result.total_score),
        "trend_quality_component": float(result.trend_quality_component),
        "support_component": float(result.support_component),
        "pullback_component": float(result.pullback_component),
        "liquidity_component": float(result.liquidity_component),
        "upside_component": float(result.upside_component),
        "last_close": float(result.last_close),
        "ema5": float(result.ema5),
        "ema10": float(result.ema10),
        "ema5_slope": float(result.ema5_slope),
        "ema10_slope": float(result.ema10_slope),
        "support_zone_lower": float(result.support_zone_lower),
        "support_zone_upper": float(result.support_zone_upper),
        "support_zone_centre": float(result.support_zone_centre),
        "distance_from_support_percent": float(
            result.distance_from_support_percent
        ),
        "support_strength": float(result.support_strength),
        "support_sources": json.dumps(list(result.support_sources)),
        "support_touch_proxy_count": int(
            result.support.support_touch_proxy_count
        ),
        "support_reaction_proxy_count": int(
            result.support.support_reaction_proxy_count
        ),
        "invalidation_level": float(result.invalidation_level),
        "first_research_target": result.first_research_target,
        "available_upside_percent": result.upside.available_upside_percent,
        "reward_risk_ratio": result.upside.reward_risk_ratio,
        "pullback_depth_percent": float(result.pullback_depth_percent),
        "liquidity_score": float(result.liquidity_score),
        "valid_session_count": int(result.valid_session_count),
        "readiness_status": result.readiness.status,
        "volume_history_status": result.volume_history_status,
        "deterministic_reasons": json.dumps(list(result.deterministic_reasons)),
        "source": result.source_provider,
        "source_fingerprint": result.source_data_fingerprint,
        "latest_session": result.latest_session,
        "data_cutoff": result.data_cutoff,
        "metric_version": result.metric_version,
        "config_version": result.config_version,
        "first_touch_order_available": 0,
    }


def _validate_member_rows(members: tuple[dict, ...], candidate_limit: int):
    if not members:
        raise ValueError("READY watchlist requires at least one member")
    ranks = [int(member["eligible_rank"]) for member in members]
    if ranks != list(range(1, len(members) + 1)):
        raise ValueError("eligible ranks must be contiguous from 1")
    symbols = [member["symbol"] for member in members]
    if len(symbols) != len(set(symbols)):
        raise ValueError("duplicate watchlist symbol")
    # The candidate limit is a maximum, never a quota to fill.
    expected_displayed = min(int(candidate_limit), len(members))
    if (
        sum(bool(member["displayed_candidate"]) for member in members)
        != expected_displayed
    ):
        raise ValueError("displayed candidate count does not match the maximum")
    for member in members:
        if bool(member["displayed_candidate"]) != (
            int(member["eligible_rank"]) <= int(candidate_limit)
        ):
            raise ValueError("displayed marker is inconsistent with rank")
        if member["strategy_identity"] != STRATEGY_IDENTITY:
            raise ValueError("member strategy identity is not this strategy")
        if member["source"] != EODHD_DAILY:
            raise ValueError("member provider is not EODHD_DAILY")
        if not str(member["source_fingerprint"]).startswith("sha256:"):
            raise ValueError("member source fingerprint is missing")
        if not (
            0
            < float(member["invalidation_level"])
            <= float(member["support_zone_lower"])
            <= float(member["support_zone_centre"])
            <= float(member["support_zone_upper"])
        ):
            raise ValueError("member support zone geometry is invalid")
        if float(member["last_close"]) < float(member["invalidation_level"]):
            raise ValueError("member close is below its own invalidation level")
        if float(member["ema5"]) <= float(member["ema10"]):
            raise ValueError("member EMA alignment is not bullish")
        # The threshold itself lives in UpsideRiskConfig; this is the structural
        # guarantee that no member is published without a real target above it.
        if member["first_research_target"] is None or (
            float(member["first_research_target"]) <= float(member["last_close"])
        ):
            raise ValueError(
                "member has no research target above its latest completed close"
            )
        if member["available_upside_percent"] is None or (
            float(member["available_upside_percent"]) <= 0
        ):
            raise ValueError("member available upside is missing or non-positive")


def _empty_result_code(snapshot: FrozenUptrendWatchlist) -> str:
    statuses = {result.readiness.status for result in snapshot.results}
    if SELECTION_STALE in statuses:
        return SOURCE_UNAVAILABLE
    if statuses and statuses <= {SELECTION_INSUFFICIENT, SELECTION_UNAVAILABLE}:
        return INSUFFICIENT_DAILY_HISTORY
    return NO_ELIGIBLE_SYMBOLS


def _result_for_existing(
    record: StoredUptrendWatchlist | None,
) -> UptrendWatchlistServiceResult:
    if record is None:
        return UptrendWatchlistServiceResult(WATCHLIST_NOT_GENERATED)
    status = record.header["status"]
    if status == READY:
        return UptrendWatchlistServiceResult(WATCHLIST_READY, record, reused=True)
    if status == FAILED:
        code = record.header.get("failure_code") or GENERATION_FAILED
        if code not in {
            INSUFFICIENT_DAILY_HISTORY,
            SOURCE_UNAVAILABLE,
            PROVENANCE_REJECTED,
            NO_ELIGIBLE_SYMBOLS,
            GENERATION_FAILED,
        }:
            code = GENERATION_FAILED
        return UptrendWatchlistServiceResult(
            code, record, record.header.get("failure_detail"), reused=True
        )
    return UptrendWatchlistServiceResult(
        WATCHLIST_NOT_GENERATED,
        record,
        "Watchlist generation is already in progress",
        reused=True,
    )


def _typed_member(member: dict) -> dict:
    member["displayed_candidate"] = bool(member["displayed_candidate"])
    member["first_touch_order_available"] = bool(
        member["first_touch_order_available"]
    )
    for column in ("support_sources", "deterministic_reasons"):
        try:
            member[column] = tuple(json.loads(member[column]))
        except (TypeError, json.JSONDecodeError):
            member[column] = ()
    return member


def _sanitize_detail(detail: str | None) -> str | None:
    if detail is None:
        return None
    text = str(detail).replace("\r", " ").replace("\n", " ")
    lowered = text.lower()
    for marker in ("api_token=", "api_key=", "authorization:"):
        index = lowered.find(marker)
        if index >= 0:
            text = text[:index] + marker + "***REDACTED***"
            lowered = text.lower()
    return text[:500]


def _to_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _sha256_json(value) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_timestamp(value: datetime | str | None) -> str:
    if value is None:
        return _iso_now()
    parsed = (
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if isinstance(value, str)
        else value
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
