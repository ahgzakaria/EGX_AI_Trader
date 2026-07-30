"""Immutable, versioned persistence for historical scalping watchlists.

Normal presentation code reads READY records only.  The explicit preparation
path is EODHD-Daily-only, claims one deterministic identity before calculation,
publishes members atomically, and never consumes Rubix or current-session
fields.
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

from scalping_expected_range.config import DailyHistoricalSelectionConfig
from scalping_expected_range.daily_historical_selection import (
    DAILY_SELECTION_INSUFFICIENT,
    DAILY_SELECTION_STALE,
    DAILY_SELECTION_UNAVAILABLE,
    EODHD_DAILY,
    FrozenDailyWatchlist,
    _clean_completed_daily,
    _frame_fingerprint,
    _has_eodhd_provenance,
    build_frozen_daily_watchlist,
    load_eodhd_daily_history,
)


SCHEMA_VERSION = 2
DEFAULT_DATABASE_PATH = Path("data/scalping_historical_watchlists.db")
DEFAULT_UNIVERSE_MANIFEST = Path("data/eodhd/historical_symbol_routing.json")

WATCHLIST_READY = "WATCHLIST_READY"
WATCHLIST_NOT_GENERATED = "WATCHLIST_NOT_GENERATED"
INSUFFICIENT_DAILY_HISTORY = "INSUFFICIENT_DAILY_HISTORY"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
PROVENANCE_REJECTED = "PROVENANCE_REJECTED"
CONFIG_VERSION_MISMATCH = "CONFIG_VERSION_MISMATCH"
SOURCE_FINGERPRINT_CHANGED = "SOURCE_FINGERPRINT_CHANGED"
GENERATION_FAILED = "GENERATION_FAILED"
NO_ELIGIBLE_SYMBOLS = "NO_ELIGIBLE_SYMBOLS"

GENERATING = "GENERATING"
READY = "READY"
FAILED = "FAILED"
SUPERSEDED = "SUPERSEDED"
HARD_ELIGIBLE = "HARD_ELIGIBLE"
SOURCE_FINGERPRINT_VERIFIED = "SOURCE_FINGERPRINT_VERIFIED"

CAIRO = ZoneInfo("Africa/Cairo")
EGX_WEEKDAYS = {0, 1, 2, 3, 6}


SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;

CREATE TABLE IF NOT EXISTS watchlist_schema (
 schema_version INTEGER PRIMARY KEY,
 installed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist_headers (
 watchlist_id TEXT PRIMARY KEY,
 identity_key TEXT NOT NULL UNIQUE,
 schema_version INTEGER NOT NULL,
 target_session_date TEXT NOT NULL,
 historical_data_cutoff TEXT NOT NULL,
 provider TEXT NOT NULL,
 primary_lookback INTEGER NOT NULL,
 recent_lookback INTEGER NOT NULL,
 metric_version TEXT NOT NULL,
 config_version TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL,
 source_fingerprint_status TEXT NOT NULL,
 universe_fingerprint TEXT NOT NULL,
 top_n INTEGER NOT NULL CHECK(top_n > 0),
 eligible_count INTEGER NOT NULL DEFAULT 0 CHECK(eligible_count >= 0),
 displayed_count INTEGER NOT NULL DEFAULT 0 CHECK(displayed_count >= 0),
 generated_at TEXT NOT NULL,
 generation_run_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('GENERATING','READY','FAILED','SUPERSEDED')),
 generation_reason TEXT NOT NULL,
 failure_code TEXT,
 failure_detail TEXT
);

CREATE TABLE IF NOT EXISTS watchlist_members (
 watchlist_id TEXT NOT NULL REFERENCES watchlist_headers(watchlist_id) ON DELETE RESTRICT,
 symbol TEXT NOT NULL,
 historical_rank INTEGER NOT NULL CHECK(historical_rank > 0),
 scoreable_rank INTEGER NOT NULL CHECK(scoreable_rank > 0),
 displayed_candidate INTEGER NOT NULL CHECK(displayed_candidate IN (0,1)),
 historical_score REAL NOT NULL,
 primary_historical_score REAL NOT NULL,
 movement_potential_score REAL NOT NULL,
 range_stability_score REAL NOT NULL,
 upper_zone_consistency_score REAL NOT NULL,
 lower_zone_consistency_score REAL NOT NULL,
 combined_zone_consistency_score REAL NOT NULL,
 zone_confidence_label TEXT NOT NULL,
 liquidity_score REAL NOT NULL,
 median_daily_range REAL NOT NULL,
 normal_range_lower REAL NOT NULL,
 normal_range_upper REAL NOT NULL,
 range_hit_2pct_frequency REAL NOT NULL,
 median_upper_excursion REAL NOT NULL,
 median_lower_excursion REAL NOT NULL,
 valid_sessions_primary INTEGER NOT NULL,
 valid_sessions_recent INTEGER NOT NULL,
 primary_readiness_status TEXT NOT NULL,
 recent_confirmation_status TEXT NOT NULL,
 recent_penalty REAL NOT NULL,
 eligibility_status TEXT NOT NULL,
 exclusion_reason TEXT,
 source TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL,
 latest_session TEXT NOT NULL,
 data_cutoff TEXT NOT NULL,
 metric_version TEXT NOT NULL,
 config_version TEXT NOT NULL,
 historical_explanation TEXT NOT NULL,
 range_bound_score REAL NOT NULL DEFAULT 0,
 primary_range_bound_score REAL NOT NULL DEFAULT 0,
 support_zone_low REAL NOT NULL DEFAULT 0,
 support_zone_high REAL NOT NULL DEFAULT 0,
 support_center REAL NOT NULL DEFAULT 0,
 resistance_zone_low REAL NOT NULL DEFAULT 0,
 resistance_zone_high REAL NOT NULL DEFAULT 0,
 resistance_center REAL NOT NULL DEFAULT 0,
 channel_center REAL NOT NULL DEFAULT 0,
 channel_width_percent REAL NOT NULL DEFAULT 0,
 channel_direction TEXT NOT NULL DEFAULT 'UNKNOWN',
 channel_center_slope REAL NOT NULL DEFAULT 0,
 support_zone_slope REAL NOT NULL DEFAULT 0,
 resistance_zone_slope REAL NOT NULL DEFAULT 0,
 horizontal_channel_stability_score REAL NOT NULL DEFAULT 0,
 support_stability_score REAL NOT NULL DEFAULT 0,
 resistance_stability_score REAL NOT NULL DEFAULT 0,
 containment_frequency REAL NOT NULL DEFAULT 0,
 broad_channel_consistency_frequency REAL NOT NULL DEFAULT 0,
 support_touch_proxy_count INTEGER NOT NULL DEFAULT 0,
 support_reaction_proxy_count INTEGER NOT NULL DEFAULT 0,
 resistance_touch_proxy_count INTEGER NOT NULL DEFAULT 0,
 resistance_rejection_proxy_count INTEGER NOT NULL DEFAULT 0,
 breakout_frequency REAL NOT NULL DEFAULT 0,
 breakdown_frequency REAL NOT NULL DEFAULT 0,
 event_outlier_count INTEGER NOT NULL DEFAULT 0,
 range_bound_status TEXT NOT NULL DEFAULT 'DATA_UNAVAILABLE',
 range_bound_reasons TEXT NOT NULL DEFAULT '[]',
 PRIMARY KEY(watchlist_id, symbol),
 UNIQUE(watchlist_id, historical_rank)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_headers_session
 ON watchlist_headers(target_session_date, status, generated_at);
CREATE INDEX IF NOT EXISTS idx_watchlist_headers_ready
 ON watchlist_headers(status, target_session_date);
CREATE INDEX IF NOT EXISTS idx_watchlist_members_rank
 ON watchlist_members(watchlist_id, historical_rank);
CREATE INDEX IF NOT EXISTS idx_watchlist_members_displayed
 ON watchlist_members(watchlist_id, displayed_candidate, historical_rank);

CREATE TRIGGER IF NOT EXISTS watchlist_header_insert_generating
BEFORE INSERT ON watchlist_headers
WHEN NEW.status <> 'GENERATING'
BEGIN
 SELECT RAISE(ABORT, 'watchlist headers must start GENERATING');
END;

CREATE TRIGGER IF NOT EXISTS watchlist_header_no_delete
BEFORE DELETE ON watchlist_headers
BEGIN
 SELECT RAISE(ABORT, 'watchlist headers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS watchlist_header_completed_no_update
BEFORE UPDATE ON watchlist_headers
WHEN OLD.status <> 'GENERATING'
BEGIN
 SELECT RAISE(ABORT, 'completed watchlist headers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS watchlist_header_identity_no_update
BEFORE UPDATE ON watchlist_headers
WHEN
 NEW.watchlist_id <> OLD.watchlist_id OR
 NEW.identity_key <> OLD.identity_key OR
 NEW.schema_version <> OLD.schema_version OR
 NEW.target_session_date <> OLD.target_session_date OR
 NEW.historical_data_cutoff <> OLD.historical_data_cutoff OR
 NEW.provider <> OLD.provider OR
 NEW.primary_lookback <> OLD.primary_lookback OR
 NEW.recent_lookback <> OLD.recent_lookback OR
 NEW.metric_version <> OLD.metric_version OR
 NEW.config_version <> OLD.config_version OR
 NEW.source_fingerprint <> OLD.source_fingerprint OR
 NEW.source_fingerprint_status <> OLD.source_fingerprint_status OR
 NEW.universe_fingerprint <> OLD.universe_fingerprint OR
 NEW.top_n <> OLD.top_n OR
 NEW.generated_at <> OLD.generated_at OR
 NEW.generation_run_id <> OLD.generation_run_id OR
 NEW.generation_reason <> OLD.generation_reason
BEGIN
 SELECT RAISE(ABORT, 'watchlist identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS watchlist_member_generating_only
BEFORE INSERT ON watchlist_members
WHEN (
 SELECT status FROM watchlist_headers WHERE watchlist_id=NEW.watchlist_id
) <> 'GENERATING'
BEGIN
 SELECT RAISE(ABORT, 'members can be inserted only while GENERATING');
END;

CREATE TRIGGER IF NOT EXISTS watchlist_member_no_update
BEFORE UPDATE ON watchlist_members
BEGIN
 SELECT RAISE(ABORT, 'watchlist members are immutable');
END;

CREATE TRIGGER IF NOT EXISTS watchlist_member_no_delete
BEFORE DELETE ON watchlist_members
BEGIN
 SELECT RAISE(ABORT, 'watchlist members are immutable');
END;
"""


MEMBER_COLUMNS = (
    "watchlist_id",
    "symbol",
    "historical_rank",
    "scoreable_rank",
    "displayed_candidate",
    "historical_score",
    "primary_historical_score",
    "movement_potential_score",
    "range_stability_score",
    "upper_zone_consistency_score",
    "lower_zone_consistency_score",
    "combined_zone_consistency_score",
    "zone_confidence_label",
    "liquidity_score",
    "median_daily_range",
    "normal_range_lower",
    "normal_range_upper",
    "range_hit_2pct_frequency",
    "median_upper_excursion",
    "median_lower_excursion",
    "valid_sessions_primary",
    "valid_sessions_recent",
    "primary_readiness_status",
    "recent_confirmation_status",
    "recent_penalty",
    "eligibility_status",
    "exclusion_reason",
    "source",
    "source_fingerprint",
    "latest_session",
    "data_cutoff",
    "metric_version",
    "config_version",
    "historical_explanation",
    "range_bound_score",
    "primary_range_bound_score",
    "support_zone_low",
    "support_zone_high",
    "support_center",
    "resistance_zone_low",
    "resistance_zone_high",
    "resistance_center",
    "channel_center",
    "channel_width_percent",
    "channel_direction",
    "channel_center_slope",
    "support_zone_slope",
    "resistance_zone_slope",
    "horizontal_channel_stability_score",
    "support_stability_score",
    "resistance_stability_score",
    "containment_frequency",
    "broad_channel_consistency_frequency",
    "support_touch_proxy_count",
    "support_reaction_proxy_count",
    "resistance_touch_proxy_count",
    "resistance_rejection_proxy_count",
    "breakout_frequency",
    "breakdown_frequency",
    "event_outlier_count",
    "range_bound_status",
    "range_bound_reasons",
)

RANGE_BOUND_MEMBER_MIGRATION_COLUMNS = {
    "range_bound_score": "REAL NOT NULL DEFAULT 0",
    "primary_range_bound_score": "REAL NOT NULL DEFAULT 0",
    "support_zone_low": "REAL NOT NULL DEFAULT 0",
    "support_zone_high": "REAL NOT NULL DEFAULT 0",
    "support_center": "REAL NOT NULL DEFAULT 0",
    "resistance_zone_low": "REAL NOT NULL DEFAULT 0",
    "resistance_zone_high": "REAL NOT NULL DEFAULT 0",
    "resistance_center": "REAL NOT NULL DEFAULT 0",
    "channel_center": "REAL NOT NULL DEFAULT 0",
    "channel_width_percent": "REAL NOT NULL DEFAULT 0",
    "channel_direction": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
    "channel_center_slope": "REAL NOT NULL DEFAULT 0",
    "support_zone_slope": "REAL NOT NULL DEFAULT 0",
    "resistance_zone_slope": "REAL NOT NULL DEFAULT 0",
    "horizontal_channel_stability_score": "REAL NOT NULL DEFAULT 0",
    "support_stability_score": "REAL NOT NULL DEFAULT 0",
    "resistance_stability_score": "REAL NOT NULL DEFAULT 0",
    "containment_frequency": "REAL NOT NULL DEFAULT 0",
    "broad_channel_consistency_frequency": "REAL NOT NULL DEFAULT 0",
    "support_touch_proxy_count": "INTEGER NOT NULL DEFAULT 0",
    "support_reaction_proxy_count": "INTEGER NOT NULL DEFAULT 0",
    "resistance_touch_proxy_count": "INTEGER NOT NULL DEFAULT 0",
    "resistance_rejection_proxy_count": "INTEGER NOT NULL DEFAULT 0",
    "breakout_frequency": "REAL NOT NULL DEFAULT 0",
    "breakdown_frequency": "REAL NOT NULL DEFAULT 0",
    "event_outlier_count": "INTEGER NOT NULL DEFAULT 0",
    "range_bound_status": "TEXT NOT NULL DEFAULT 'DATA_UNAVAILABLE'",
    "range_bound_reasons": "TEXT NOT NULL DEFAULT '[]'",
}


@dataclass(frozen=True)
class WatchlistIdentity:
    target_session_date: str
    historical_data_cutoff: str
    provider: str
    primary_lookback: int
    recent_lookback: int
    metric_version: str
    config_version: str
    source_fingerprint: str
    universe_fingerprint: str
    top_n: int
    identity_key: str
    watchlist_id: str


@dataclass(frozen=True)
class StoredWatchlist:
    header: dict
    members: tuple[dict, ...]

    @property
    def displayed(self) -> tuple[dict, ...]:
        return tuple(
            member for member in self.members if member["displayed_candidate"]
        )


@dataclass(frozen=True)
class WatchlistServiceResult:
    status: str
    record: StoredWatchlist | None = None
    detail: str | None = None
    reused: bool = False


@dataclass(frozen=True)
class WatchlistComparison:
    previous_watchlist_id: str
    current_watchlist_id: str
    previous_target_session: str
    current_target_session: str
    candidate_overlap: tuple[str, ...]
    additions: tuple[str, ...]
    removals: tuple[str, ...]
    rank_changes: tuple[dict, ...]
    previous_eligible_count: int
    current_eligible_count: int
    eligible_count_change: int
    top_n_turnover: float
    previous_data_cutoff: str
    current_data_cutoff: str
    metric_version_changed: bool
    config_version_changed: bool


class FrozenWatchlistRepository:
    """Small WAL-safe repository with SQL-enforced immutability."""

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
            installed_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(watchlist_members)"
                )
            }
            for name, definition in (
                RANGE_BOUND_MEMBER_MIGRATION_COLUMNS.items()
            ):
                if name not in installed_columns:
                    connection.execute(
                        f"ALTER TABLE watchlist_members "
                        f"ADD COLUMN {name} {definition}"
                    )
            connection.execute(
                """INSERT OR IGNORE INTO watchlist_schema
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
            "path": str(self.path),
        }

    def claim(
        self,
        identity: WatchlistIdentity,
        *,
        generated_at: str,
        generation_run_id: str,
        generation_reason: str,
    ) -> tuple[dict, bool]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM watchlist_headers WHERE identity_key=?",
                (identity.identity_key,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return dict(existing), False
            connection.execute(
                """INSERT INTO watchlist_headers (
                   watchlist_id, identity_key, schema_version,
                   target_session_date, historical_data_cutoff, provider,
                   primary_lookback, recent_lookback, metric_version,
                   config_version, source_fingerprint,
                   source_fingerprint_status, universe_fingerprint, top_n,
                   eligible_count, displayed_count, generated_at,
                   generation_run_id, status, generation_reason
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identity.watchlist_id,
                    identity.identity_key,
                    SCHEMA_VERSION,
                    identity.target_session_date,
                    identity.historical_data_cutoff,
                    identity.provider,
                    identity.primary_lookback,
                    identity.recent_lookback,
                    identity.metric_version,
                    identity.config_version,
                    identity.source_fingerprint,
                    SOURCE_FINGERPRINT_VERIFIED,
                    identity.universe_fingerprint,
                    identity.top_n,
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

    def publish(self, watchlist_id: str, members: tuple[dict, ...]) -> StoredWatchlist:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            header = connection.execute(
                "SELECT * FROM watchlist_headers WHERE watchlist_id=?",
                (watchlist_id,),
            ).fetchone()
            if header is None or header["status"] != GENERATING:
                raise ValueError("watchlist is not in GENERATING state")
            _validate_member_rows(members, int(header["top_n"]))
            placeholders = ",".join("?" for _ in MEMBER_COLUMNS)
            connection.executemany(
                f"""INSERT INTO watchlist_members
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
                """UPDATE watchlist_headers
                   SET eligible_count=?, displayed_count=?, status='READY'
                   WHERE watchlist_id=? AND status='GENERATING'""",
                (len(members), displayed_count, watchlist_id),
            )
            stored_count, distinct_ranks, displayed = connection.execute(
                """SELECT COUNT(*), COUNT(DISTINCT historical_rank),
                          COALESCE(SUM(displayed_candidate),0)
                   FROM watchlist_members WHERE watchlist_id=?""",
                (watchlist_id,),
            ).fetchone()
            if (
                stored_count != len(members)
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
        safe_detail = _sanitize_detail(detail)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE watchlist_headers
                   SET status='FAILED', failure_code=?, failure_detail=?
                   WHERE watchlist_id=? AND status='GENERATING'""",
                (str(code), safe_detail, watchlist_id),
            )

    def header(self, watchlist_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM watchlist_headers WHERE watchlist_id=?",
                (watchlist_id,),
            ).fetchone()
        return dict(row) if row else None

    def by_identity(self, identity_key: str) -> StoredWatchlist | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT watchlist_id FROM watchlist_headers WHERE identity_key=?",
                (identity_key,),
            ).fetchone()
        return self.get_by_id(row[0]) if row else None

    def get_by_id(self, watchlist_id: str) -> StoredWatchlist | None:
        with self.connect() as connection:
            header = connection.execute(
                "SELECT * FROM watchlist_headers WHERE watchlist_id=?",
                (watchlist_id,),
            ).fetchone()
            if header is None:
                return None
            members = connection.execute(
                """SELECT * FROM watchlist_members WHERE watchlist_id=?
                   ORDER BY historical_rank, symbol""",
                (watchlist_id,),
            ).fetchall()
        return StoredWatchlist(
            dict(header),
            tuple(_typed_member(dict(member)) for member in members),
        )

    def latest_for_session(
        self, target_session_date: str, *, top_n: int | None = None
    ) -> StoredWatchlist | None:
        sql = (
            "SELECT watchlist_id FROM watchlist_headers "
            "WHERE target_session_date=? "
        )
        parameters: list[object] = [target_session_date]
        if top_n is not None:
            sql += "AND top_n=? "
            parameters.append(int(top_n))
        sql += (
            "ORDER BY CASE status WHEN 'READY' THEN 0 WHEN 'GENERATING' THEN 1 "
            "ELSE 2 END, generated_at DESC LIMIT 1"
        )
        with self.connect() as connection:
            row = connection.execute(sql, tuple(parameters)).fetchone()
        return self.get_by_id(row[0]) if row else None

    def latest_ready(self) -> StoredWatchlist | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT watchlist_id FROM watchlist_headers
                   WHERE status='READY'
                   ORDER BY target_session_date DESC, generated_at DESC LIMIT 1"""
            ).fetchone()
        return self.get_by_id(row[0]) if row else None

    def ready_before(self, target_session_date: str) -> StoredWatchlist | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT watchlist_id FROM watchlist_headers
                   WHERE status='READY' AND target_session_date < ?
                   ORDER BY target_session_date DESC, generated_at DESC LIMIT 1""",
                (str(target_session_date),),
            ).fetchone()
        return self.get_by_id(row[0]) if row else None


class FrozenHistoricalWatchlistService:
    """Session-aware service; generation is explicit and presentation is read-only."""

    def __init__(
        self,
        repository: FrozenWatchlistRepository | None = None,
        *,
        database_path: str | Path | None = None,
        config: DailyHistoricalSelectionConfig | None = None,
        calendar=None,
        history_loader: Callable | None = None,
        snapshot_builder: Callable = build_frozen_daily_watchlist,
        publication_hook: Callable | None = None,
    ):
        chosen_path = database_path or os.getenv(
            "SCALPING_HISTORICAL_WATCHLIST_DB",
            str(DEFAULT_DATABASE_PATH),
        )
        self.repository = repository or FrozenWatchlistRepository(chosen_path)
        self.config = config or DailyHistoricalSelectionConfig()
        self.calendar = calendar or _DefaultCalendar()
        self.history_loader = history_loader or load_validated_eodhd_histories
        self.snapshot_builder = snapshot_builder
        self.publication_hook = publication_hook

    def target_and_cutoff(
        self,
        target_session_date: date | str | None = None,
        *,
        now: datetime | None = None,
    ) -> tuple[date, date]:
        target = resolve_target_session(
            target_session_date,
            now=now,
            calendar=self.calendar,
        )
        cutoff = previous_trading_session(target, calendar=self.calendar)
        return target, cutoff

    def prepare_for_session(
        self,
        target_session_date: date | str | None = None,
        *,
        histories: Mapping[str, pd.DataFrame | None] | None = None,
        unavailable_symbols: set[str] | frozenset[str] = frozenset(),
        top_n: int | None = None,
        generated_at: datetime | str | None = None,
        generation_run_id: str | None = None,
        generation_reason: str = "SESSION_PREPARATION",
        now: datetime | None = None,
    ) -> WatchlistServiceResult:
        target, cutoff = self.target_and_cutoff(target_session_date, now=now)
        selected_top_n = int(top_n or self.config.candidate_display_limit)
        cfg = replace(self.config, candidate_display_limit=selected_top_n)

        if histories is None:
            try:
                loaded = self.history_loader(
                    data_cutoff=cutoff,
                    config=cfg,
                )
                if isinstance(loaded, tuple):
                    histories, unavailable_symbols = loaded
                else:
                    histories = loaded
            except Exception as error:
                return WatchlistServiceResult(
                    SOURCE_UNAVAILABLE,
                    detail=_sanitize_detail(
                        f"{type(error).__name__}: {error}"
                    ),
                )

        normalized = {
            _base(symbol): frame for symbol, frame in (histories or {}).items()
        }
        unavailable = {
            _base(symbol) for symbol in unavailable_symbols
        }
        all_symbols = sorted(set(normalized) | unavailable)
        if not all_symbols:
            return WatchlistServiceResult(
                SOURCE_UNAVAILABLE, detail="No EODHD Daily histories supplied"
            )

        rejected = [
            symbol
            for symbol, frame in normalized.items()
            if frame is not None and not _has_eodhd_provenance(frame)
        ]
        if rejected:
            return WatchlistServiceResult(
                PROVENANCE_REJECTED,
                detail=(
                    "Non-EODHD or missing provider provenance: "
                    + ", ".join(sorted(rejected)[:10])
                ),
            )

        source_fingerprint = fingerprint_histories(
            normalized,
            unavailable_symbols=unavailable,
            data_cutoff=cutoff,
            lookback_sessions=cfg.lookback_sessions,
        )
        universe_fingerprint = fingerprint_universe(all_symbols)
        identity = make_watchlist_identity(
            target_session_date=target,
            historical_data_cutoff=cutoff,
            config=cfg,
            source_fingerprint=source_fingerprint,
            universe_fingerprint=universe_fingerprint,
            top_n=selected_top_n,
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
            return WatchlistServiceResult(
                GENERATION_FAILED,
                detail=_sanitize_detail(f"{type(error).__name__}: {error}"),
            )

        if not created:
            return _result_for_existing(
                self.repository.get_by_id(header["watchlist_id"])
            )

        try:
            snapshot: FrozenDailyWatchlist = self.snapshot_builder(
                normalized,
                data_cutoff=cutoff,
                unavailable_symbols=unavailable,
                generated_at=stamp,
                config=cfg,
            )
            eligible = tuple(
                result for result in snapshot.results if result.eligible
            )
            if not eligible:
                code = _empty_result_code(snapshot)
                self.repository.fail(identity.watchlist_id, code)
                return WatchlistServiceResult(code)
            members = tuple(
                _member_from_result(identity.watchlist_id, result, cfg)
                for result in sorted(
                    eligible,
                    key=lambda result: (
                        result.eligible_rank or 10**9,
                        result.symbol,
                    ),
                )
            )
            if self.publication_hook is not None:
                self.publication_hook(identity, snapshot, members)
            record = self.repository.publish(identity.watchlist_id, members)
            return WatchlistServiceResult(WATCHLIST_READY, record)
        except Exception as error:
            self.repository.fail(
                identity.watchlist_id,
                GENERATION_FAILED,
                f"{type(error).__name__}: {error}",
            )
            return WatchlistServiceResult(
                GENERATION_FAILED,
                detail=_sanitize_detail(f"{type(error).__name__}: {error}"),
            )

    def get_for_session(
        self,
        target_session_date: date | str | None = None,
        *,
        top_n: int | None = None,
        expected_source_fingerprint: str | None = None,
        now: datetime | None = None,
    ) -> WatchlistServiceResult:
        target, cutoff = self.target_and_cutoff(target_session_date, now=now)
        chosen_top_n = int(top_n or self.config.candidate_display_limit)
        record = self.repository.latest_for_session(
            target.isoformat(), top_n=chosen_top_n
        )
        if record is None:
            return WatchlistServiceResult(WATCHLIST_NOT_GENERATED)
        header = record.header
        if (
            header["schema_version"] != SCHEMA_VERSION
            or header["metric_version"] != self.config.metric_version
            or header["primary_lookback"] != self.config.lookback_sessions
            or (
                header["recent_lookback"]
                != self.config.recent_confirmation_sessions
            )
        ):
            return WatchlistServiceResult(
                CONFIG_VERSION_MISMATCH,
                record,
                "Stored selector metric version is incompatible",
            )
        if header["config_version"] != self.config.config_version:
            return WatchlistServiceResult(
                CONFIG_VERSION_MISMATCH,
                record,
                "Stored selector config version is incompatible",
            )
        if header["provider"] != EODHD_DAILY:
            return WatchlistServiceResult(
                PROVENANCE_REJECTED, record, "Stored provider is not EODHD_DAILY"
            )
        if (
            header["historical_data_cutoff"] != cutoff.isoformat()
            or not str(header["source_fingerprint"]).startswith("sha256:")
            or not str(header["universe_fingerprint"]).startswith("sha256:")
            or header["source_fingerprint_status"]
            != SOURCE_FINGERPRINT_VERIFIED
        ):
            return WatchlistServiceResult(
                SOURCE_FINGERPRINT_CHANGED,
                record,
                "Stored cutoff or source identity does not match this session",
            )
        if any(
            member["source"] != EODHD_DAILY
            or member["data_cutoff"] != header["historical_data_cutoff"]
            or member["metric_version"] != header["metric_version"]
            or member["config_version"] != header["config_version"]
            or not str(member["source_fingerprint"]).startswith("sha256:")
            for member in record.members
        ):
            return WatchlistServiceResult(
                PROVENANCE_REJECTED,
                record,
                "Stored member provenance is incomplete or mismatched",
            )
        if (
            expected_source_fingerprint is not None
            and header["source_fingerprint"] != expected_source_fingerprint
        ):
            return WatchlistServiceResult(
                SOURCE_FINGERPRINT_CHANGED,
                record,
                "Stored source fingerprint does not match current research input",
            )
        return _result_for_existing(record)

    def get_latest_ready(self) -> WatchlistServiceResult:
        record = self.repository.latest_ready()
        if record is None:
            return WatchlistServiceResult(WATCHLIST_NOT_GENERATED)
        header = record.header
        if (
            header["schema_version"] != SCHEMA_VERSION
            or header["provider"] != EODHD_DAILY
            or header["metric_version"] != self.config.metric_version
            or header["config_version"] != self.config.config_version
            or header["primary_lookback"] != self.config.lookback_sessions
            or (
                header["recent_lookback"]
                != self.config.recent_confirmation_sessions
            )
        ):
            return WatchlistServiceResult(
                CONFIG_VERSION_MISMATCH,
                record,
                "Latest stored watchlist is incompatible",
            )
        return WatchlistServiceResult(WATCHLIST_READY, record, reused=True)

    def rebuild_for_research(
        self,
        target_session_date: date | str | None = None,
        *,
        authorized: bool = False,
        **kwargs,
    ) -> WatchlistServiceResult:
        if not authorized:
            return WatchlistServiceResult(
                WATCHLIST_NOT_GENERATED,
                detail="Explicit research rebuild confirmation is required",
            )
        return self.prepare_for_session(
            target_session_date,
            generation_reason="AUTHORIZED_RESEARCH_REBUILD",
            **kwargs,
        )

    def compare_watchlists(
        self, previous_watchlist_id: str, current_watchlist_id: str
    ) -> WatchlistComparison:
        previous = self.repository.get_by_id(previous_watchlist_id)
        current = self.repository.get_by_id(current_watchlist_id)
        if previous is None or current is None:
            raise ValueError("Both stored watchlists are required")
        if (
            previous.header["status"] != READY
            or current.header["status"] != READY
        ):
            raise ValueError("Only READY watchlists can be compared")
        previous_displayed = {
            row["symbol"]: row["historical_rank"] for row in previous.displayed
        }
        current_displayed = {
            row["symbol"]: row["historical_rank"] for row in current.displayed
        }
        overlap = set(previous_displayed) & set(current_displayed)
        additions = set(current_displayed) - set(previous_displayed)
        removals = set(previous_displayed) - set(current_displayed)
        denominator = max(len(previous_displayed), len(current_displayed), 1)
        rank_changes = tuple(
            {
                "symbol": symbol,
                "previous_rank": previous_displayed[symbol],
                "current_rank": current_displayed[symbol],
                "rank_change": (
                    previous_displayed[symbol] - current_displayed[symbol]
                ),
            }
            for symbol in sorted(
                overlap,
                key=lambda symbol: (
                    current_displayed[symbol],
                    symbol,
                ),
            )
        )
        return WatchlistComparison(
            previous.header["watchlist_id"],
            current.header["watchlist_id"],
            previous.header["target_session_date"],
            current.header["target_session_date"],
            tuple(
                sorted(
                    overlap,
                    key=lambda symbol: (current_displayed[symbol], symbol),
                )
            ),
            tuple(
                sorted(
                    additions,
                    key=lambda symbol: (current_displayed[symbol], symbol),
                )
            ),
            tuple(
                sorted(
                    removals,
                    key=lambda symbol: (previous_displayed[symbol], symbol),
                )
            ),
            rank_changes,
            int(previous.header["eligible_count"]),
            int(current.header["eligible_count"]),
            (
                int(current.header["eligible_count"])
                - int(previous.header["eligible_count"])
            ),
            round(
                1.0 - len(overlap) / denominator,
                6,
            ),
            previous.header["historical_data_cutoff"],
            current.header["historical_data_cutoff"],
            (
                previous.header["metric_version"]
                != current.header["metric_version"]
            ),
            (
                previous.header["config_version"]
                != current.header["config_version"]
            ),
        )


def make_watchlist_identity(
    *,
    target_session_date: date | str,
    historical_data_cutoff: date | str,
    config: DailyHistoricalSelectionConfig,
    source_fingerprint: str,
    universe_fingerprint: str,
    top_n: int,
) -> WatchlistIdentity:
    target = _to_date(target_session_date).isoformat()
    cutoff = _to_date(historical_data_cutoff).isoformat()
    dimensions = {
        "schema_version": SCHEMA_VERSION,
        "target_session_date": target,
        "historical_data_cutoff": cutoff,
        "provider": config.source_provider,
        "primary_lookback": config.lookback_sessions,
        "recent_lookback": config.recent_confirmation_sessions,
        "metric_version": config.metric_version,
        "config_version": config.config_version,
        "source_fingerprint": str(source_fingerprint),
        "universe_fingerprint": str(universe_fingerprint),
        "top_n": int(top_n),
    }
    identity_key = _sha256_json(dimensions)
    return WatchlistIdentity(
        target,
        cutoff,
        config.source_provider,
        config.lookback_sessions,
        config.recent_confirmation_sessions,
        config.metric_version,
        config.config_version,
        str(source_fingerprint),
        str(universe_fingerprint),
        int(top_n),
        identity_key,
        f"FHW-{identity_key[:24]}",
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
    """The Stable Range-Bound selector universe — the ACTIVE authoritative list.

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
    config: DailyHistoricalSelectionConfig | None = None,
    client=None,
    symbols=None,
    max_workers: int = 8,
) -> tuple[dict[str, pd.DataFrame | None], set[str]]:
    """Load the validated 225-symbol EODHD universe with no provider fallback."""

    cfg = config or DailyHistoricalSelectionConfig()
    chosen = tuple(symbols or validated_eodhd_symbols())
    if not chosen:
        raise ValueError("Validated EODHD universe is empty")
    if client is None:
        from providers.eodhd_client import EODHDClient

        client = EODHDClient()

    def load(symbol):
        return load_eodhd_daily_history(
            symbol,
            client=client,
            data_cutoff=data_cutoff,
            config=cfg,
        )

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        loaded = tuple(pool.map(load, chosen))
    histories = {result.symbol: result.frame for result in loaded}
    unavailable = {
        result.symbol for result in loaded if result.frame is None
    }
    return histories, unavailable


def resolve_target_session(
    requested: date | str | None = None,
    *,
    now: datetime | None = None,
    calendar=None,
) -> date:
    chosen_calendar = calendar or _DefaultCalendar()
    if requested is None:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        candidate = moment.astimezone(CAIRO).date()
    else:
        candidate = _to_date(requested)
    if chosen_calendar.is_trading_day(candidate):
        return candidate
    return chosen_calendar.next_trading_session(candidate)


def previous_trading_session(day: date | str, *, calendar=None) -> date:
    chosen_calendar = calendar or _DefaultCalendar()
    candidate = _to_date(day) - timedelta(days=1)
    for _ in range(370):
        if chosen_calendar.is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise ValueError("Could not resolve previous EGX trading session")


class _DefaultCalendar:
    def is_trading_day(self, day):
        from core.egx_calendar import holiday_dates

        value = _to_date(day)
        return (
            value.weekday() in EGX_WEEKDAYS
            and value not in holiday_dates()
        )

    def next_trading_session(self, day):
        candidate = _to_date(day) + timedelta(days=1)
        for _ in range(370):
            if self.is_trading_day(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise ValueError("Could not resolve next EGX trading session")


def _member_from_result(
    watchlist_id: str,
    result,
    config: DailyHistoricalSelectionConfig,
) -> dict:
    metrics = result.metrics
    primary = result.zone_profile_60
    recent = result.zone_profile_30
    range_bound = result.range_bound_profile_60
    recent_range_bound = result.range_bound_profile_30
    required = {
        "metrics": metrics,
        "primary zone profile": primary,
        "recent zone profile": recent,
        "primary range-bound profile": range_bound,
        "recent range-bound profile": recent_range_bound,
        "source fingerprint": result.source_data_fingerprint,
        "confirmed score": (
            result.confirmed_range_bound_tradability_score
        ),
        "primary score": result.range_bound_tradability_score,
        "eligible rank": result.eligible_rank,
        "scoreable rank": result.historical_rank,
        "latest session": result.latest_session,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(
            f"{result.symbol} eligible member missing " + ", ".join(missing)
        )
    if result.source_provider != EODHD_DAILY:
        raise ValueError(f"{result.symbol} provider is not EODHD_DAILY")
    explanation = (
        f"{result.range_bound_status}; "
        f"{result.range_bound_confirmation_status}; "
        f"{range_bound.channel_direction} channel; "
        f"{range_bound.channel_width_percent:.2f}% width; "
        f"{range_bound.close_containment_frequency:.1%} containment; "
        f"{', '.join(range_bound.selection_reasons)}; "
        f"{range_bound.valid_sessions}-session primary; "
        f"{recent_range_bound.valid_sessions}-session recent confirmation; "
        "completed EODHD Daily history only"
    )
    return {
        "watchlist_id": watchlist_id,
        "symbol": result.symbol,
        "historical_rank": int(result.eligible_rank),
        "scoreable_rank": int(result.historical_rank),
        "displayed_candidate": int(
            int(result.eligible_rank) <= config.candidate_display_limit
        ),
        "historical_score": float(
            result.confirmed_range_bound_tradability_score
        ),
        "primary_historical_score": float(
            result.range_bound_tradability_score
        ),
        "movement_potential_score": float(
            metrics.daily_movement_potential_score
        ),
        "range_stability_score": float(
            range_bound.horizontal_channel_stability_score
        ),
        "upper_zone_consistency_score": float(
            range_bound.resistance_stability_score
        ),
        "lower_zone_consistency_score": float(
            range_bound.support_stability_score
        ),
        "combined_zone_consistency_score": float(
            range_bound.support_resistance_repeatability_score
        ),
        "zone_confidence_label": result.range_bound_status,
        "liquidity_score": float(metrics.daily_liquidity_score),
        "median_daily_range": float(metrics.median_daily_range_percent),
        "normal_range_lower": float(metrics.range_p25_percent),
        "normal_range_upper": float(metrics.range_p75_percent),
        "range_hit_2pct_frequency": float(
            metrics.range_hit_2pct_frequency
        ),
        "median_upper_excursion": float(
            metrics.median_upper_excursion_percent
        ),
        "median_lower_excursion": float(
            metrics.median_lower_excursion_percent
        ),
        "valid_sessions_primary": int(primary.valid_sessions),
        "valid_sessions_recent": int(recent.valid_sessions),
        "primary_readiness_status": result.readiness.status,
        "recent_confirmation_status": (
            result.range_bound_confirmation_status
        ),
        "recent_penalty": float(
            result.range_bound_confirmation_penalty
        ),
        "eligibility_status": HARD_ELIGIBLE,
        "exclusion_reason": None,
        "source": EODHD_DAILY,
        "source_fingerprint": result.source_data_fingerprint,
        "latest_session": result.latest_session,
        "data_cutoff": result.data_cutoff,
        "metric_version": result.metric_version,
        "config_version": result.config_version,
        "historical_explanation": explanation,
        "range_bound_score": float(
            result.confirmed_range_bound_tradability_score
        ),
        "primary_range_bound_score": float(
            result.range_bound_tradability_score
        ),
        "support_zone_low": float(range_bound.support_zone_low),
        "support_zone_high": float(range_bound.support_zone_high),
        "support_center": float(range_bound.support_center),
        "resistance_zone_low": float(range_bound.resistance_zone_low),
        "resistance_zone_high": float(range_bound.resistance_zone_high),
        "resistance_center": float(range_bound.resistance_center),
        "channel_center": float(range_bound.channel_center),
        "channel_width_percent": float(
            range_bound.channel_width_percent
        ),
        "channel_direction": range_bound.channel_direction,
        "channel_center_slope": float(
            range_bound.channel_center_slope
        ),
        "support_zone_slope": float(range_bound.support_zone_slope),
        "resistance_zone_slope": float(
            range_bound.resistance_zone_slope
        ),
        "horizontal_channel_stability_score": float(
            range_bound.horizontal_channel_stability_score
        ),
        "support_stability_score": float(
            range_bound.support_stability_score
        ),
        "resistance_stability_score": float(
            range_bound.resistance_stability_score
        ),
        "containment_frequency": float(
            range_bound.close_containment_frequency
        ),
        "broad_channel_consistency_frequency": float(
            range_bound.broad_channel_consistency_frequency
        ),
        "support_touch_proxy_count": int(
            range_bound.support_touch_count
        ),
        "support_reaction_proxy_count": int(
            range_bound.support_reaction_proxy_count
        ),
        "resistance_touch_proxy_count": int(
            range_bound.resistance_touch_count
        ),
        "resistance_rejection_proxy_count": int(
            range_bound.resistance_rejection_proxy_count
        ),
        "breakout_frequency": float(range_bound.breakout_frequency),
        "breakdown_frequency": float(range_bound.breakdown_frequency),
        "event_outlier_count": int(
            range_bound.event_dominated_outlier_count
        ),
        "range_bound_status": result.range_bound_status,
        "range_bound_reasons": json.dumps(
            range_bound.selection_reasons,
            separators=(",", ":"),
        ),
    }


def _validate_member_rows(members: tuple[dict, ...], top_n: int):
    if not members:
        raise ValueError("READY watchlist requires at least one member")
    ranks = [int(member["historical_rank"]) for member in members]
    if ranks != list(range(1, len(members) + 1)):
        raise ValueError("eligible historical ranks must be contiguous")
    symbols = [member["symbol"] for member in members]
    if len(symbols) != len(set(symbols)):
        raise ValueError("duplicate watchlist symbol")
    expected_displayed = min(int(top_n), len(members))
    actual_displayed = sum(
        bool(member["displayed_candidate"]) for member in members
    )
    if actual_displayed != expected_displayed:
        raise ValueError("displayed candidate count does not match Top-N")
    for member in members:
        expected = int(member["historical_rank"]) <= int(top_n)
        if bool(member["displayed_candidate"]) != expected:
            raise ValueError("displayed marker is inconsistent with rank")
        if member["source"] != EODHD_DAILY:
            raise ValueError("member provider is not EODHD_DAILY")
        if not str(member["source_fingerprint"]).startswith("sha256:"):
            raise ValueError("member source fingerprint is missing")
        if member["range_bound_status"] != "STABLE_RANGE_BOUND_CANDIDATE":
            raise ValueError("member is not an eligible range-bound candidate")
        if not (
            float(member["support_zone_low"])
            <= float(member["support_zone_high"])
            < float(member["resistance_zone_low"])
            <= float(member["resistance_zone_high"])
        ):
            raise ValueError("member support/resistance zones are invalid")
        if float(member["channel_width_percent"]) <= 0:
            raise ValueError("member channel width is invalid")


def _empty_result_code(snapshot: FrozenDailyWatchlist) -> str:
    statuses = {result.readiness.status for result in snapshot.results}
    if DAILY_SELECTION_STALE in statuses:
        return SOURCE_UNAVAILABLE
    if statuses and statuses <= {
        DAILY_SELECTION_INSUFFICIENT,
        DAILY_SELECTION_UNAVAILABLE,
    }:
        return INSUFFICIENT_DAILY_HISTORY
    return NO_ELIGIBLE_SYMBOLS


def _result_for_existing(record: StoredWatchlist | None) -> WatchlistServiceResult:
    if record is None:
        return WatchlistServiceResult(WATCHLIST_NOT_GENERATED)
    status = record.header["status"]
    if status == READY:
        return WatchlistServiceResult(
            WATCHLIST_READY, record, reused=True
        )
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
        return WatchlistServiceResult(
            code,
            record,
            record.header.get("failure_detail"),
            reused=True,
        )
    return WatchlistServiceResult(
        WATCHLIST_NOT_GENERATED,
        record,
        "Watchlist generation is already in progress",
        reused=True,
    )


def _typed_member(member: dict) -> dict:
    member["displayed_candidate"] = bool(member["displayed_candidate"])
    try:
        member["range_bound_reasons"] = tuple(
            json.loads(member["range_bound_reasons"])
        )
    except (TypeError, json.JSONDecodeError):
        member["range_bound_reasons"] = ()
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


def _base(symbol) -> str:
    return str(symbol).strip().upper().split(".")[0]


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
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
