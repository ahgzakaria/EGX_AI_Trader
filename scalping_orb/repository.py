"""Independent WAL research persistence for ORB Phase 2A evidence."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Sequence
from uuid import uuid4

from scalping_orb.bars import CompletedBar
from scalping_orb.capabilities import (
    HistoricalBarCapability,
    LiveDecisionCapability,
    PriceReferenceCapabilities,
)
from scalping_orb.events import (
    DataQualityEvent,
    FeedTimestampQuality,
    HistoricalReplayStatus,
    LiveFreshnessStatus,
    MarketTimeStatus,
    NormalizedIntradayEvent,
    SourceEventType,
    SpreadCapability,
    UniverseMembershipStatus,
    VolumeCapability,
    VolumeDeltaStatus,
)
from scalping_orb.opening_range import OpeningRangeResult, OpeningRangeStatus
from scalping_orb.session import OrbSessionPhase


SCHEMA_VERSION = 6
PROTECTED_DATABASE_NAMES = frozenset(
    {
        "rubix_live_market.db",
        "rubix_bridge_provenance.db",
        "scalping.db",
        "scalping_historical_watchlists.db",
        "uptrend_pullback_watchlists.db",
        "expected_range_paper_state.db",
        "forward_testing.db",
        "decision_support.db",
        "normalized_daily_cache.db",
        "market_data_cache.sqlite",
        "tickerchart_quotes.sqlite3",
    }
)

MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS orb_schema_meta (
    version INTEGER PRIMARY KEY,
    migration_name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orb_collection_runs (
    run_id TEXT PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    mode TEXT NOT NULL CHECK (mode='SHADOW'),
    status TEXT NOT NULL,
    source_database TEXT,
    config_hash TEXT NOT NULL,
    detail_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orb_sessions (
    session_id TEXT PRIMARY KEY,
    session_date TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    first_event_utc TEXT,
    last_event_utc TEXT,
    observed_symbols INTEGER NOT NULL DEFAULT 0,
    completed_one_minute_bars INTEGER NOT NULL DEFAULT 0,
    completed_five_minute_bars INTEGER NOT NULL DEFAULT 0,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(session_date, config_hash)
);

CREATE TABLE IF NOT EXISTS orb_normalized_events (
    source_identity TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES orb_sessions(session_id),
    canonical_ticker TEXT NOT NULL,
    verified_rubix_symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    market_timestamp_utc TEXT NOT NULL,
    receive_timestamp_utc TEXT NOT NULL,
    sequence INTEGER,
    last_price REAL,
    cumulative_volume REAL,
    bid REAL,
    ask REAL,
    source_event_type TEXT NOT NULL,
    feed_timestamp_quality TEXT NOT NULL,
    quote_age_seconds REAL NOT NULL,
    spread_absolute REAL,
    spread_percent REAL,
    spread_capability TEXT NOT NULL,
    volume_capability TEXT NOT NULL,
    volume_delta REAL,
    volume_delta_status TEXT NOT NULL,
    sequence_gap INTEGER NOT NULL,
    duplicate_status TEXT NOT NULL,
    out_of_order_status TEXT NOT NULL,
    session_phase TEXT NOT NULL,
    quality_flags_json TEXT NOT NULL,
    persisted_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orb_bars (
    session_id TEXT NOT NULL REFERENCES orb_sessions(session_id),
    canonical_ticker TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL CHECK (interval_minutes IN (1,5)),
    session_date TEXT NOT NULL,
    bar_start_utc TEXT NOT NULL,
    bar_end_utc TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    update_count INTEGER NOT NULL,
    first_sequence INTEGER,
    last_sequence INTEGER,
    data_quality_flags_json TEXT NOT NULL,
    completed INTEGER NOT NULL CHECK (completed IN (0,1)),
    session_phase TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    component_bar_count INTEGER NOT NULL,
    persisted_at_utc TEXT NOT NULL,
    PRIMARY KEY(session_id, canonical_ticker, interval_minutes, bar_start_utc)
);

CREATE TABLE IF NOT EXISTS orb_opening_ranges (
    opening_range_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES orb_sessions(session_id),
    canonical_ticker TEXT NOT NULL,
    session_date TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    opening_range_high REAL,
    opening_range_low REAL,
    opening_range_mid REAL,
    width_absolute REAL,
    width_percent REAL,
    completed_bar_count INTEGER NOT NULL,
    expected_bar_count INTEGER NOT NULL,
    coverage_ratio REAL NOT NULL,
    valid_volume_total REAL,
    first_valid_timestamp_utc TEXT,
    last_valid_timestamp_utc TEXT,
    data_quality_flags_json TEXT NOT NULL,
    frozen_at_utc TEXT,
    source_identity TEXT,
    is_frozen INTEGER NOT NULL CHECK (is_frozen IN (0,1)),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(session_id, canonical_ticker, revision)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_orb_one_frozen_range
ON orb_opening_ranges(session_id, canonical_ticker) WHERE is_frozen=1;

CREATE TABLE IF NOT EXISTS orb_data_quality_events (
    quality_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES orb_sessions(session_id),
    canonical_ticker TEXT,
    session_date TEXT,
    observed_at_utc TEXT NOT NULL,
    code TEXT NOT NULL,
    detail TEXT,
    source_identity TEXT,
    dedupe_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS orb_capabilities (
    capability_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES orb_sessions(session_id),
    canonical_ticker TEXT NOT NULL,
    assessed_at_utc TEXT NOT NULL,
    opening_range_high REAL,
    opening_range_low REAL,
    current_bid REAL,
    current_ask REAL,
    spread_status TEXT NOT NULL,
    volume_status TEXT NOT NULL,
    true_vwap_status TEXT NOT NULL,
    bar_weighted_typical_price_proxy REAL,
    proxy_status TEXT NOT NULL,
    time_of_day_rvol_status TEXT NOT NULL,
    intraday_history_sessions INTEGER NOT NULL,
    UNIQUE(session_id, canonical_ticker, assessed_at_utc)
);

CREATE INDEX IF NOT EXISTS idx_orb_events_symbol_time
ON orb_normalized_events(session_id, canonical_ticker, market_timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_orb_events_session_time
ON orb_normalized_events(session_date, market_timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_orb_bars_symbol_time
ON orb_bars(session_id, canonical_ticker, interval_minutes, bar_start_utc);
CREATE INDEX IF NOT EXISTS idx_orb_quality_session_code
ON orb_data_quality_events(session_id, code, observed_at_utc);
CREATE INDEX IF NOT EXISTS idx_orb_capabilities_session_symbol
ON orb_capabilities(session_id, canonical_ticker, assessed_at_utc);
"""

MIGRATION_2 = """
ALTER TABLE orb_normalized_events
ADD COLUMN universe_membership_status TEXT NOT NULL DEFAULT 'UNKNOWN_UNMAPPED_IDENTIFIER';

ALTER TABLE orb_normalized_events
ADD COLUMN operationally_eligible INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_orb_events_eligibility
ON orb_normalized_events(session_id, operationally_eligible, canonical_ticker);
"""

MIGRATION_3 = """
ALTER TABLE orb_normalized_events
ADD COLUMN market_time_status TEXT NOT NULL DEFAULT 'MARKET_TIME_UNRELIABLE';

ALTER TABLE orb_normalized_events
ADD COLUMN historical_replay_status TEXT NOT NULL DEFAULT 'HISTORICAL_REPLAY_ACCEPTED';

ALTER TABLE orb_normalized_events
ADD COLUMN live_freshness_status TEXT NOT NULL DEFAULT 'COLLECTOR_FRESHNESS_UNAVAILABLE';

ALTER TABLE orb_normalized_events
ADD COLUMN receive_lag_seconds REAL;

ALTER TABLE orb_normalized_events
ADD COLUMN collector_age_seconds REAL;

ALTER TABLE orb_capabilities
ADD COLUMN historical_bar_capability TEXT NOT NULL DEFAULT 'HISTORICAL_BAR_UNAVAILABLE';

ALTER TABLE orb_capabilities
ADD COLUMN live_decision_capability TEXT NOT NULL DEFAULT 'LIVE_DECISION_DISABLED_COLLECTOR_FRESHNESS_UNAVAILABLE';

ALTER TABLE orb_capabilities
ADD COLUMN live_decision_reason TEXT NOT NULL DEFAULT 'migrated without live freshness evidence';

CREATE INDEX IF NOT EXISTS idx_orb_events_live_freshness
ON orb_normalized_events(session_id, live_freshness_status, canonical_ticker);
"""

#: Phase 2B Core research evidence. Additive only — no Phase 2A table is
#: altered. There is deliberately no order, execution, position, trade, P&L or
#: broker table: the furthest this schema can record is a research candidate.
MIGRATION_4 = """
CREATE TABLE IF NOT EXISTS orb_candidates (
    candidate_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES orb_sessions(session_id),
    canonical_ticker TEXT NOT NULL,
    session_date TEXT NOT NULL,
    opening_range_revision INTEGER NOT NULL,
    opening_range_version_identity TEXT NOT NULL,
    opening_range_version_mode TEXT NOT NULL,
    evaluation_mode TEXT NOT NULL,
    strategy_fingerprint TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    final_state TEXT NOT NULL,
    terminal INTEGER NOT NULL CHECK (terminal IN (0,1)),
    rejection_reasons_json TEXT NOT NULL,
    evidence_fingerprint TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(session_id, canonical_ticker, opening_range_revision,
           evaluation_mode, strategy_fingerprint)
);

CREATE TABLE IF NOT EXISTS orb_state_transitions (
    transition_row_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES orb_candidates(candidate_id),
    transition_id TEXT NOT NULL,
    sequence_index INTEGER NOT NULL,
    canonical_ticker TEXT NOT NULL,
    session_date TEXT NOT NULL,
    opening_range_version_identity TEXT NOT NULL,
    opening_range_revision INTEGER NOT NULL,
    prior_state TEXT NOT NULL,
    new_state TEXT NOT NULL,
    exchange_timestamp_utc TEXT,
    as_of_timestamp_utc TEXT NOT NULL,
    rule_code TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    frozen_references_json TEXT NOT NULL,
    market_time_status TEXT NOT NULL,
    historical_replay_status TEXT NOT NULL,
    live_freshness_status TEXT NOT NULL,
    live_decision_capability TEXT NOT NULL,
    evaluation_mode TEXT NOT NULL,
    strategy_fingerprint TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(candidate_id, transition_id)
);

CREATE TABLE IF NOT EXISTS orb_breakouts (
    breakout_row_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES orb_candidates(candidate_id),
    breakout_identity TEXT NOT NULL,
    bar_start_utc TEXT NOT NULL,
    bar_end_utc TEXT NOT NULL,
    close REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    opening_range_high REAL NOT NULL,
    distance_above_or_high REAL NOT NULL,
    distance_above_or_high_percent REAL NOT NULL,
    bar_range REAL NOT NULL,
    bar_range_percent REAL NOT NULL,
    bar_range_atr REAL,
    extension_atr REAL,
    update_count INTEGER NOT NULL,
    distance_to_daily_resistance REAL,
    distance_to_daily_resistance_percent REAL,
    intraday_atr_status TEXT NOT NULL,
    intraday_atr_value REAL,
    zone_lower REAL NOT NULL,
    zone_upper REAL NOT NULL,
    zone_construction_rule TEXT NOT NULL,
    accepted INTEGER NOT NULL CHECK (accepted IN (0,1)),
    too_extended INTEGER NOT NULL CHECK (too_extended IN (0,1)),
    extension_reasons_json TEXT NOT NULL,
    rejection_reasons_json TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(candidate_id, breakout_identity)
);

CREATE TABLE IF NOT EXISTS orb_pullbacks (
    pullback_row_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES orb_candidates(candidate_id),
    pullback_ordinal INTEGER NOT NULL,
    start_bar_start_utc TEXT NOT NULL,
    low REAL NOT NULL,
    low_bar_start_utc TEXT NOT NULL,
    post_breakout_high REAL NOT NULL,
    depth_from_high REAL NOT NULL,
    depth_from_high_percent REAL NOT NULL,
    depth_atr REAL,
    depth_versus_or_high_percent REAL NOT NULL,
    bars_since_breakout INTEGER NOT NULL,
    duration_bars INTEGER NOT NULL,
    closes_below_or_high INTEGER NOT NULL,
    structural_breach_bars INTEGER NOT NULL,
    touched_or_high INTEGER NOT NULL CHECK (touched_or_high IN (0,1)),
    entered_breakout_zone INTEGER NOT NULL CHECK (entered_breakout_zone IN (0,1)),
    ema9_five_minute REAL,
    ema20_five_minute REAL,
    ema9_one_minute REAL,
    breakout_bar_volume REAL,
    pullback_volume_total REAL,
    volume_assessed INTEGER NOT NULL CHECK (volume_assessed IN (0,1)),
    price_only INTEGER NOT NULL CHECK (price_only IN (0,1)),
    healthy INTEGER NOT NULL CHECK (healthy IN (0,1)),
    rejection_reasons_json TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(candidate_id, pullback_ordinal)
);

CREATE TABLE IF NOT EXISTS orb_reclaims (
    reclaim_row_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES orb_candidates(candidate_id),
    reclaim_identity TEXT NOT NULL,
    reclaim_rule TEXT NOT NULL,
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0,1)),
    confirmation_bar_start_utc TEXT,
    confirmation_bar_end_utc TEXT,
    trigger_price REAL,
    opening_range_high REAL NOT NULL,
    zone_lower REAL NOT NULL,
    zone_upper REAL NOT NULL,
    pullback_low REAL NOT NULL,
    bars_elapsed_since_pullback_low INTEGER NOT NULL,
    rejection_reasons_json TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(candidate_id, reclaim_identity)
);

CREATE TABLE IF NOT EXISTS orb_research_setups (
    candidate_id TEXT PRIMARY KEY REFERENCES orb_candidates(candidate_id),
    trigger_price REAL NOT NULL,
    proposed_stop REAL NOT NULL,
    stop_basis TEXT NOT NULL,
    raw_pullback_low REAL NOT NULL,
    buffer_applied REAL NOT NULL,
    buffer_basis TEXT NOT NULL,
    stop_distance_absolute REAL NOT NULL,
    stop_distance_percent REAL NOT NULL,
    stop_distance_atr REAL,
    risk_per_share REAL NOT NULL,
    target_1 REAL NOT NULL,
    target_2 REAL NOT NULL,
    nearest_daily_resistance REAL,
    usable_target REAL NOT NULL,
    effective_reward_risk REAL NOT NULL,
    research_only INTEGER NOT NULL CHECK (research_only = 1),
    recorded_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orb_candidates_session_state
ON orb_candidates(session_id, final_state, canonical_ticker);
CREATE INDEX IF NOT EXISTS idx_orb_transitions_candidate_sequence
ON orb_state_transitions(candidate_id, sequence_index);
"""

#: Phase 2C Shadow integration evidence. Additive only — no Phase 2A or 2B
#: table is altered. Still no order, execution, position, trade, P&L, broker or
#: notification table: the furthest this schema records is a research state and
#: the quality of the observation that produced it.
#:
#: Lane A (`orb_shadow_live_states`) is append-only by construction: its unique
#: key includes the observation cycle, so a later correction is a *new row*, not
#: an update. Lane B (`orb_shadow_reconstruction_states`) is keyed without a
#: cycle so an identical reconstruction is idempotent.
MIGRATION_5 = """
CREATE TABLE IF NOT EXISTS orb_shadow_runs (
    run_id TEXT PRIMARY KEY,
    session_date TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    runner_started_before_open INTEGER NOT NULL CHECK (runner_started_before_open IN (0,1)),
    stop_reason TEXT,
    session_classification TEXT,
    source_path_identity TEXT NOT NULL,
    config_identity TEXT NOT NULL,
    strategy_fingerprint TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    research_only INTEGER NOT NULL CHECK (research_only = 1),
    production_disabled INTEGER NOT NULL CHECK (production_disabled = 1),
    recorded_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orb_shadow_cursors (
    run_id TEXT NOT NULL REFERENCES orb_shadow_runs(run_id),
    source_table TEXT NOT NULL,
    last_source_id INTEGER NOT NULL,
    last_market_timestamp_utc TEXT,
    last_receive_timestamp_utc TEXT,
    rows_observed INTEGER NOT NULL,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (run_id, source_table)
);

CREATE TABLE IF NOT EXISTS orb_shadow_cycles (
    cycle_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES orb_shadow_runs(run_id),
    cycle_index INTEGER NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    cursor_low_source_id INTEGER NOT NULL,
    cursor_high_source_id INTEGER NOT NULL,
    source_rows_read INTEGER NOT NULL,
    normalized_events INTEGER NOT NULL,
    session_loads INTEGER NOT NULL,
    symbols_in_snapshot INTEGER NOT NULL,
    symbols_evaluated INTEGER NOT NULL,
    snapshot_identity TEXT NOT NULL,
    live_status TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    source_failure TEXT,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(run_id, cycle_index)
);

CREATE TABLE IF NOT EXISTS orb_shadow_heartbeats (
    run_id TEXT NOT NULL REFERENCES orb_shadow_runs(run_id),
    observed_at_utc TEXT NOT NULL,
    live_status TEXT NOT NULL,
    last_source_id INTEGER NOT NULL,
    latest_market_timestamp_utc TEXT,
    observed_receive_lag_seconds REAL,
    PRIMARY KEY (run_id, observed_at_utc)
);

CREATE TABLE IF NOT EXISTS orb_shadow_session_quality (
    run_id TEXT PRIMARY KEY REFERENCES orb_shadow_runs(run_id),
    session_date TEXT NOT NULL,
    classification TEXT NOT NULL,
    classification_reasons_json TEXT NOT NULL,
    source_rows_observed INTEGER NOT NULL,
    normalized_events INTEGER NOT NULL,
    exact_redeliveries_removed INTEGER NOT NULL,
    same_timestamp_distinct_retained INTEGER NOT NULL,
    source_polling_failures INTEGER NOT NULL,
    maximum_polling_gap_seconds REAL,
    median_receive_lag_seconds REAL,
    p90_receive_lag_seconds REAL,
    p95_receive_lag_seconds REAL,
    percent_above_freshness_budget REAL,
    negative_lag_events INTEGER NOT NULL,
    out_of_order_events INTEGER NOT NULL,
    late_events_after_cutoff INTEGER NOT NULL,
    active_mapped_symbols INTEGER NOT NULL,
    completed_one_minute_bars INTEGER NOT NULL,
    completed_five_minute_bars INTEGER NOT NULL,
    opening_ranges_ready INTEGER NOT NULL,
    live_evaluations INTEGER NOT NULL,
    reconstruction_evaluations INTEGER NOT NULL,
    heartbeat_count INTEGER NOT NULL,
    process_runtime_seconds REAL NOT NULL,
    quality_json TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orb_shadow_live_states (
    live_state_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES orb_shadow_runs(run_id),
    cycle_id TEXT NOT NULL REFERENCES orb_shadow_cycles(cycle_id),
    session_date TEXT NOT NULL,
    canonical_ticker TEXT NOT NULL,
    opening_range_revision INTEGER NOT NULL,
    opening_range_version_identity TEXT NOT NULL,
    final_state TEXT NOT NULL,
    terminal INTEGER NOT NULL CHECK (terminal IN (0,1)),
    rejection_reasons_json TEXT NOT NULL,
    evidence_fingerprint TEXT NOT NULL,
    candidate_identity TEXT NOT NULL,
    live_status TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    exchange_watermark_utc TEXT,
    observed_receive_lag_seconds REAL,
    research_only INTEGER NOT NULL CHECK (research_only = 1),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(run_id, cycle_id, canonical_ticker, opening_range_version_identity)
);

CREATE TABLE IF NOT EXISTS orb_shadow_reconstruction_states (
    reconstruction_state_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES orb_shadow_runs(run_id),
    session_date TEXT NOT NULL,
    canonical_ticker TEXT NOT NULL,
    opening_range_revision INTEGER NOT NULL,
    opening_range_version_identity TEXT NOT NULL,
    final_state TEXT NOT NULL,
    terminal INTEGER NOT NULL CHECK (terminal IN (0,1)),
    rejection_reasons_json TEXT NOT NULL,
    evidence_fingerprint TEXT NOT NULL,
    candidate_identity TEXT NOT NULL,
    evaluation_mode TEXT NOT NULL,
    research_only INTEGER NOT NULL CHECK (research_only = 1),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(run_id, canonical_ticker, opening_range_version_identity, evaluation_mode)
);

CREATE TABLE IF NOT EXISTS orb_shadow_live_replay_comparison (
    comparison_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES orb_shadow_runs(run_id),
    session_date TEXT NOT NULL,
    canonical_ticker TEXT NOT NULL,
    live_state TEXT,
    reconstruction_state TEXT,
    live_opening_range_version_identity TEXT,
    reconstruction_opening_range_version_identity TEXT,
    opening_range_revised INTEGER NOT NULL CHECK (opening_range_revised IN (0,1)),
    states_match INTEGER NOT NULL CHECK (states_match IN (0,1)),
    difference_reason TEXT NOT NULL,
    evaluable_live INTEGER NOT NULL CHECK (evaluable_live IN (0,1)),
    evaluable_historically INTEGER NOT NULL CHECK (evaluable_historically IN (0,1)),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(run_id, canonical_ticker)
);

CREATE INDEX IF NOT EXISTS idx_orb_shadow_cycles_run
ON orb_shadow_cycles(run_id, cycle_index);
CREATE INDEX IF NOT EXISTS idx_orb_shadow_live_states_run_ticker
ON orb_shadow_live_states(run_id, canonical_ticker, observed_at_utc);
CREATE INDEX IF NOT EXISTS idx_orb_shadow_reconstruction_run_ticker
ON orb_shadow_reconstruction_states(run_id, canonical_ticker);
"""

#: Full-shadow run controls. Additive only: two nullable columns, one new
#: comparison table that supersedes the single-run one, and discovery indexes.
#: No historical Phase 2C row is mutated or deleted.
#:
#: `orb_shadow_live_replay_comparison` keyed on one run cannot express a
#: comparison *between* two runs, so cross-run results go to a new table that
#: names both sides. The original table is left exactly as it is.
MIGRATION_6 = """
ALTER TABLE orb_shadow_runs ADD COLUMN active_universe_only INTEGER;
ALTER TABLE orb_shadow_runs ADD COLUMN compared_live_run_id TEXT;

CREATE TABLE IF NOT EXISTS orb_shadow_cross_run_comparison (
    comparison_id TEXT PRIMARY KEY,
    live_run_id TEXT NOT NULL REFERENCES orb_shadow_runs(run_id),
    reconstruction_run_id TEXT NOT NULL REFERENCES orb_shadow_runs(run_id),
    session_date TEXT NOT NULL,
    canonical_ticker TEXT NOT NULL,
    live_state TEXT,
    reconstruction_state TEXT,
    live_opening_range_version_identity TEXT,
    reconstruction_opening_range_version_identity TEXT,
    live_status TEXT,
    live_rejection_reasons_json TEXT NOT NULL,
    opening_range_revised INTEGER NOT NULL CHECK (opening_range_revised IN (0,1)),
    states_match INTEGER NOT NULL CHECK (states_match IN (0,1)),
    difference_reason TEXT NOT NULL,
    difference_evidence_json TEXT NOT NULL,
    evaluable_live INTEGER NOT NULL CHECK (evaluable_live IN (0,1)),
    evaluable_historically INTEGER NOT NULL CHECK (evaluable_historically IN (0,1)),
    research_only INTEGER NOT NULL CHECK (research_only = 1),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(live_run_id, reconstruction_run_id, canonical_ticker)
);

CREATE INDEX IF NOT EXISTS idx_orb_shadow_runs_discovery
ON orb_shadow_runs(session_date, mode, source_path_identity, config_identity);
CREATE INDEX IF NOT EXISTS idx_orb_shadow_cross_run_pair
ON orb_shadow_cross_run_comparison(live_run_id, reconstruction_run_id);
"""

MIGRATIONS = {
    1: ("phase2a_initial", MIGRATION_1),
    2: ("phase2a_universe_membership", MIGRATION_2),
    3: ("phase2a_latency_and_live_readiness", MIGRATION_3),
    4: ("phase2b_core_research_evidence", MIGRATION_4),
    5: ("phase2c_shadow_integration", MIGRATION_5),
    6: ("phase2c_full_shadow_run_controls", MIGRATION_6),
}


@dataclass(frozen=True)
class RetentionResult:
    normalized_events_deleted: int
    derived_sessions_deleted: int


def _enum_value(value) -> str:
    """Persist an enum by its VALUE, never its repr.

    On Python 3.11+ ``str(member)`` of a ``str``-mixin Enum returns
    ``"ClassName.MEMBER"``, not the member value. Writing that to the database
    silently corrupts every stored status and breaks any later equality check
    against the declared vocabulary.
    """

    return str(getattr(value, "value", value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _execute_script_transactionally(
    connection: sqlite3.Connection, sql: str
) -> None:
    """Execute complete SQLite statements without ``executescript`` auto-commit."""

    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                connection.execute(statement)
    if buffer.strip():
        raise sqlite3.OperationalError("Incomplete ORB migration statement")


class OrbResearchRepository:
    """Write only to the dedicated ORB research database."""

    def __init__(self, path, *, read_only: bool = False, target_schema_version=None):
        self.path = Path(path).expanduser()
        self.read_only = bool(read_only)
        latest_schema_version = max(MIGRATIONS)
        self.target_schema_version = (
            latest_schema_version
            if target_schema_version is None
            else int(target_schema_version)
        )
        if not 1 <= self.target_schema_version <= latest_schema_version:
            raise ValueError("Unsupported ORB target schema version")
        self._assert_safe_target()
        self.write_transaction_count = 0
        #: Instrumentation for the replay-performance blocker. A session-scoped
        #: load reads every event row, so `event_rows_read` growing as the
        #: square of the symbol count is the signature of a per-symbol reload.
        self.event_load_count = 0
        self.event_rows_read = 0
        if self.read_only:
            if not self.path.is_file():
                raise FileNotFoundError(f"ORB research database not found: {self.path}")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate(target_version=self.target_schema_version)

    def _assert_safe_target(self) -> None:
        """Fail closed before ``migrate`` can write to anything but an ORB database.

        The name deny-list catches the known production files. The content probe
        catches every other case, because constructing the repository immediately
        creates tables, switches journal mode and stamps ``user_version`` — so a
        mistyped path must never reach an existing foreign database or data file.
        """

        if self.path.name.lower() in PROTECTED_DATABASE_NAMES:
            raise ValueError(f"Refusing protected/production database path: {self.path}")
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return
        probe = sqlite3.connect(
            f"file:{self.path.resolve().as_posix()}?mode=ro", uri=True, timeout=10
        )
        try:
            probe.execute("PRAGMA query_only=ON")
            names = {
                row[0]
                for row in probe.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.DatabaseError as error:
            raise ValueError(
                f"Refusing non-SQLite ORB research path: {self.path}"
            ) from error
        finally:
            probe.close()
        if names and not any(name.startswith("orb_") for name in names):
            raise ValueError(
                f"Refusing foreign database without ORB tables: {self.path}"
            )

    def connect(self) -> sqlite3.Connection:
        if self.read_only:
            connection = sqlite3.connect(
                f"file:{self.path.resolve().as_posix()}?mode=ro",
                uri=True,
                timeout=30,
            )
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def migrate(self, *, target_version=None) -> None:
        if self.read_only:
            raise RuntimeError("Read-only ORB repository cannot migrate")
        target = self.target_schema_version if target_version is None else int(target_version)
        if not 1 <= target <= max(MIGRATIONS):
            raise ValueError("Unsupported ORB target schema version")
        connection = self.connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS orb_schema_meta (
                   version INTEGER PRIMARY KEY, migration_name TEXT NOT NULL,
                   checksum TEXT NOT NULL, applied_at_utc TEXT NOT NULL)"""
            )
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > target:
                raise RuntimeError(
                    f"Refusing ORB schema downgrade from {current} to {target}"
                )
            for version, (name, sql) in sorted(MIGRATIONS.items()):
                if version > target:
                    continue
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                row = connection.execute(
                    "SELECT checksum FROM orb_schema_meta WHERE version=?", (version,)
                ).fetchone()
                if row:
                    if row["checksum"] != checksum:
                        raise RuntimeError(f"ORB migration checksum mismatch: {version}")
                    continue
                _execute_script_transactionally(connection, sql)
                connection.execute(
                    "INSERT INTO orb_schema_meta VALUES (?,?,?,?)",
                    (version, name, checksum, _utc_now()),
                )
            connection.execute(f"PRAGMA user_version={target}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        if self.read_only:
            raise RuntimeError("Read-only ORB repository cannot write")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self.write_transaction_count += 1
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def database_status(self) -> dict:
        with self.connect() as connection:
            return {
                "path": str(self.path.resolve()),
                "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0].upper(),
                "foreign_keys": bool(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
                "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            }

    def start_collection_run(
        self, *, config_hash: str, source_database: str | None = None
    ) -> str:
        run_id = str(uuid4())
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO orb_collection_runs VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    _utc_now(),
                    None,
                    "SHADOW",
                    "RUNNING",
                    source_database,
                    config_hash,
                    "{}",
                ),
            )
        return run_id

    def finish_collection_run(self, run_id: str, status: str, detail=None) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE orb_collection_runs SET completed_at_utc=?,status=?,detail_json=? WHERE run_id=?",
                (_utc_now(), str(status), _json(detail or {}), run_id),
            )

    def ensure_session(
        self, session_id: str, session_date: date, config_hash: str
    ) -> None:
        now = _utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO orb_sessions
                   (session_id,session_date,config_hash,status,created_at_utc,updated_at_utc)
                   VALUES (?,?,?,'COLLECTING',?,?)""",
                (session_id, session_date.isoformat(), config_hash, now, now),
            )

    def insert_events(
        self, session_id: str, events: Iterable[NormalizedIntradayEvent]
    ) -> int:
        inserted = 0
        now = _utc_now()
        values = tuple(events)
        with self.transaction() as connection:
            for event in values:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO orb_normalized_events VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event.source_identity,
                        session_id,
                        event.canonical_ticker,
                        event.verified_rubix_symbol,
                        event.session_date.isoformat(),
                        event.market_timestamp_utc.isoformat(),
                        event.receive_timestamp_utc.isoformat(),
                        event.sequence,
                        event.last_price,
                        event.cumulative_volume,
                        event.bid,
                        event.ask,
                        event.source_event_type.value,
                        event.feed_timestamp_quality.value,
                        event.quote_age_seconds,
                        event.spread_absolute,
                        event.spread_percent,
                        event.spread_capability.value,
                        event.volume_capability.value,
                        event.volume_delta,
                        event.volume_delta_status.value,
                        event.sequence_gap,
                        event.duplicate_status,
                        event.out_of_order_status,
                        event.session_phase.value,
                        _json(event.quality_flags),
                        now,
                        event.universe_membership_status.value,
                        int(event.operationally_eligible),
                        event.market_time_status.value,
                        event.historical_replay_status.value,
                        event.live_freshness_status.value,
                        event.receive_lag_seconds,
                        event.collector_age_seconds,
                    ),
                )
                inserted += int(cursor.rowcount == 1)
            if values:
                connection.execute(
                    """UPDATE orb_sessions SET
                       first_event_utc=COALESCE(first_event_utc,?),last_event_utc=?,
                       observed_symbols=(SELECT count(DISTINCT canonical_ticker)
                         FROM orb_normalized_events WHERE session_id=?),updated_at_utc=?
                       WHERE session_id=?""",
                    (
                        min(event.market_timestamp_utc for event in values).isoformat(),
                        max(event.market_timestamp_utc for event in values).isoformat(),
                        session_id,
                        now,
                        session_id,
                    ),
                )
        return inserted

    def insert_bars(self, session_id: str, bars: Iterable[CompletedBar]) -> int:
        inserted = 0
        now = _utc_now()
        with self.transaction() as connection:
            for bar in bars:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO orb_bars VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        session_id,
                        bar.canonical_ticker,
                        bar.interval_minutes,
                        bar.session_date.isoformat(),
                        bar.bar_start_utc.isoformat(),
                        bar.bar_end_utc.isoformat(),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.update_count,
                        bar.first_sequence,
                        bar.last_sequence,
                        _json(bar.data_quality_flags),
                        int(bar.completed),
                        bar.session_phase.value,
                        bar.source_identity,
                        bar.component_bar_count,
                        now,
                    ),
                )
                inserted += int(cursor.rowcount == 1)
                if cursor.rowcount != 1:
                    existing = connection.execute(
                        """SELECT source_identity FROM orb_bars
                           WHERE session_id=? AND canonical_ticker=?
                             AND interval_minutes=? AND bar_start_utc=?""",
                        (
                            session_id,
                            bar.canonical_ticker,
                            bar.interval_minutes,
                            bar.bar_start_utc.isoformat(),
                        ),
                    ).fetchone()
                    if existing and existing["source_identity"] != bar.source_identity:
                        detail = (
                            f"interval={bar.interval_minutes};"
                            f"bar_start={bar.bar_start_utc.isoformat()};"
                            f"original={existing['source_identity']};"
                            f"revised={bar.source_identity}"
                        )
                        dedupe = hashlib.sha256(
                            f"{session_id}|{bar.canonical_ticker}|BAR_REVISION|{detail}".encode(
                                "utf-8"
                            )
                        ).hexdigest()
                        connection.execute(
                            "INSERT OR IGNORE INTO orb_data_quality_events VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                str(uuid4()),
                                session_id,
                                bar.canonical_ticker,
                                bar.session_date.isoformat(),
                                now,
                                "FROZEN_COMPLETED_BAR_REVISION_DETECTED",
                                detail,
                                bar.source_identity,
                                dedupe,
                            ),
                        )
            connection.execute(
                """UPDATE orb_sessions SET
                   completed_one_minute_bars=(SELECT count(*) FROM orb_bars
                     WHERE session_id=? AND interval_minutes=1 AND completed=1),
                   completed_five_minute_bars=(SELECT count(*) FROM orb_bars
                     WHERE session_id=? AND interval_minutes=5 AND completed=1),
                   updated_at_utc=? WHERE session_id=?""",
                (session_id, session_id, now, session_id),
            )
        return inserted

    def insert_quality_events(
        self, session_id: str | None, events: Iterable[DataQualityEvent]
    ) -> int:
        inserted = 0
        with self.transaction() as connection:
            for event in events:
                dedupe = hashlib.sha256(
                    _json(
                        {
                            "session_id": session_id,
                            "ticker": event.canonical_ticker,
                            "session_date": event.session_date,
                            "observed": event.observed_at_utc,
                            "code": event.code,
                            "detail": event.detail,
                            "source": event.source_identity,
                        }
                    ).encode("utf-8")
                ).hexdigest()
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO orb_data_quality_events VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        session_id,
                        event.canonical_ticker,
                        event.session_date.isoformat() if event.session_date else None,
                        event.observed_at_utc.isoformat(),
                        event.code,
                        event.detail,
                        event.source_identity,
                        dedupe,
                    ),
                )
                inserted += int(cursor.rowcount == 1)
        return inserted

    def insert_opening_range(
        self, session_id: str, result: OpeningRangeResult, *, revision: int = 0
    ) -> bool:
        frozen = int(result.status == OpeningRangeStatus.READY and revision == 0)
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO orb_opening_ranges VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id,canonical_ticker,revision) DO UPDATE SET
                     status=excluded.status,
                     opening_range_high=excluded.opening_range_high,
                     opening_range_low=excluded.opening_range_low,
                     opening_range_mid=excluded.opening_range_mid,
                     width_absolute=excluded.width_absolute,
                     width_percent=excluded.width_percent,
                     completed_bar_count=excluded.completed_bar_count,
                     expected_bar_count=excluded.expected_bar_count,
                     coverage_ratio=excluded.coverage_ratio,
                     valid_volume_total=excluded.valid_volume_total,
                     first_valid_timestamp_utc=excluded.first_valid_timestamp_utc,
                     last_valid_timestamp_utc=excluded.last_valid_timestamp_utc,
                     data_quality_flags_json=excluded.data_quality_flags_json,
                     frozen_at_utc=excluded.frozen_at_utc,
                     source_identity=excluded.source_identity,
                     is_frozen=excluded.is_frozen,
                     recorded_at_utc=excluded.recorded_at_utc
                   WHERE orb_opening_ranges.is_frozen=0""",
                (
                    str(uuid4()),
                    session_id,
                    result.canonical_ticker,
                    result.session_date.isoformat(),
                    revision,
                    result.status.value,
                    result.opening_range_high,
                    result.opening_range_low,
                    result.opening_range_mid,
                    result.width_absolute,
                    result.width_percent,
                    result.completed_one_minute_bar_count,
                    result.expected_one_minute_bar_count,
                    result.coverage_ratio,
                    result.valid_volume_total,
                    result.first_valid_timestamp_utc.isoformat()
                    if result.first_valid_timestamp_utc
                    else None,
                    result.last_valid_timestamp_utc.isoformat()
                    if result.last_valid_timestamp_utc
                    else None,
                    _json(result.data_quality_flags),
                    result.frozen_at_utc.isoformat() if result.frozen_at_utc else None,
                    result.source_identity,
                    frozen,
                    _utc_now(),
                ),
            )
        return cursor.rowcount == 1

    def record_opening_range_revision(
        self, session_id: str, result: OpeningRangeResult
    ) -> int | None:
        """Persist a corrected range beside the untouched frozen decision-time row.

        Returns the new revision number, or ``None`` when this exact correction is
        already stored. The frozen ``revision=0`` row is never modified, so a
        reader can always recover the range version a decision was made against.
        """

        ticker = str(result.canonical_ticker).strip().upper()
        with self.connect() as connection:
            existing = connection.execute(
                """SELECT revision FROM orb_opening_ranges
                   WHERE session_id=? AND canonical_ticker=? AND source_identity IS ?""",
                (session_id, ticker, result.source_identity),
            ).fetchone()
            if existing is not None:
                return None
            row = connection.execute(
                """SELECT max(revision) FROM orb_opening_ranges
                   WHERE session_id=? AND canonical_ticker=?""",
                (session_id, ticker),
            ).fetchone()
        revision = int(row[0] or 0) + 1
        self.insert_opening_range(session_id, result, revision=revision)
        return revision

    def load_opening_range_revisions(
        self, session_id: str, canonical_ticker: str
    ) -> tuple[tuple[int, int, str | None], ...]:
        """Return ``(revision, is_frozen, source_identity)`` oldest revision first."""

        with self.connect() as connection:
            rows = connection.execute(
                """SELECT revision,is_frozen,source_identity FROM orb_opening_ranges
                   WHERE session_id=? AND canonical_ticker=? ORDER BY revision""",
                (session_id, str(canonical_ticker).strip().upper()),
            ).fetchall()
        return tuple(
            (int(row["revision"]), int(row["is_frozen"]), row["source_identity"])
            for row in rows
        )

    def insert_capabilities(
        self,
        session_id: str,
        canonical_ticker: str,
        assessed_at: datetime,
        capabilities: PriceReferenceCapabilities,
    ) -> bool:
        payload = asdict(capabilities)
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO orb_capabilities VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid4()),
                    session_id,
                    str(canonical_ticker).upper(),
                    assessed_at.astimezone(timezone.utc).isoformat(),
                    payload["opening_range_high"],
                    payload["opening_range_low"],
                    payload["current_bid"],
                    payload["current_ask"],
                    capabilities.spread_status.value,
                    capabilities.volume_status.value,
                    capabilities.true_vwap_status.value,
                    payload["bar_weighted_typical_price_proxy"],
                    capabilities.bar_weighted_typical_price_proxy_status.value,
                    capabilities.time_of_day_rvol_status.value,
                    capabilities.intraday_history_sessions,
                    capabilities.historical_bar_capability.value,
                    capabilities.live_decision_capability.value,
                    capabilities.live_decision_reason,
                ),
            )
        return cursor.rowcount == 1

    def count_intraday_sessions(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT count(DISTINCT session_date) FROM orb_normalized_events"
                ).fetchone()[0]
                or 0
            )

    def load_events(
        self, session_id: str, canonical_ticker: str | None = None
    ) -> tuple[NormalizedIntradayEvent, ...]:
        """Normalized events for a session, optionally for one symbol.

        The per-symbol form is served by ``idx_orb_normalized_events`` on
        ``(session_id, canonical_ticker, market_timestamp_utc)``. A caller that
        wants one symbol must pass it rather than loading the whole session and
        filtering in Python: at ~230 symbols a session that is 230 full-table
        reads of the same rows.
        """

        sql = "SELECT * FROM orb_normalized_events WHERE session_id=?"
        parameters: tuple = (session_id,)
        if canonical_ticker is not None:
            sql += " AND canonical_ticker=?"
            parameters += (str(canonical_ticker).strip().upper(),)
        sql += " ORDER BY market_timestamp_utc,canonical_ticker,source_identity"
        with self.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        self.event_load_count += 1
        self.event_rows_read += len(rows)
        return tuple(
            NormalizedIntradayEvent(
                canonical_ticker=row["canonical_ticker"],
                verified_rubix_symbol=row["verified_rubix_symbol"],
                session_date=date.fromisoformat(row["session_date"]),
                market_timestamp_utc=datetime.fromisoformat(row["market_timestamp_utc"]),
                receive_timestamp_utc=datetime.fromisoformat(row["receive_timestamp_utc"]),
                sequence=row["sequence"],
                last_price=row["last_price"],
                cumulative_volume=row["cumulative_volume"],
                bid=row["bid"],
                ask=row["ask"],
                source_event_type=SourceEventType(row["source_event_type"]),
                feed_timestamp_quality=FeedTimestampQuality(
                    row["feed_timestamp_quality"]
                ),
                source_identity=row["source_identity"],
                quote_age_seconds=row["quote_age_seconds"],
                spread_absolute=row["spread_absolute"],
                spread_percent=row["spread_percent"],
                spread_capability=SpreadCapability(row["spread_capability"]),
                volume_capability=VolumeCapability(row["volume_capability"]),
                sequence_gap=row["sequence_gap"],
                duplicate_status=row["duplicate_status"],
                out_of_order_status=row["out_of_order_status"],
                session_phase=OrbSessionPhase(row["session_phase"]),
                quality_flags=tuple(json.loads(row["quality_flags_json"])),
                volume_delta=row["volume_delta"],
                volume_delta_status=VolumeDeltaStatus(row["volume_delta_status"]),
                universe_membership_status=UniverseMembershipStatus(
                    row["universe_membership_status"]
                ),
                operationally_eligible=bool(row["operationally_eligible"]),
                market_time_status=MarketTimeStatus(row["market_time_status"]),
                historical_replay_status=HistoricalReplayStatus(
                    row["historical_replay_status"]
                ),
                live_freshness_status=LiveFreshnessStatus(
                    row["live_freshness_status"]
                ),
                receive_lag_seconds=row["receive_lag_seconds"],
                collector_age_seconds=row["collector_age_seconds"],
            )
            for row in rows
        )

    def get_frozen_opening_range(
        self, session_id: str, canonical_ticker: str
    ) -> OpeningRangeResult | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM orb_opening_ranges
                   WHERE session_id=? AND canonical_ticker=? AND is_frozen=1""",
                (session_id, str(canonical_ticker).upper()),
            ).fetchone()
        if row is None:
            return None
        return self._opening_range_from_row(row)

    def get_opening_range_version(
        self, session_id: str, canonical_ticker: str, revision: int
    ) -> OpeningRangeResult | None:
        """Load the exact immutable range version referenced by research state."""

        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM orb_opening_ranges
                   WHERE session_id=? AND canonical_ticker=? AND revision=?""",
                (
                    session_id,
                    str(canonical_ticker).strip().upper(),
                    int(revision),
                ),
            ).fetchone()
        return None if row is None else self._opening_range_from_row(row)

    @staticmethod
    def _opening_range_from_row(row: sqlite3.Row) -> OpeningRangeResult:
        return OpeningRangeResult(
            canonical_ticker=row["canonical_ticker"],
            session_date=date.fromisoformat(row["session_date"]),
            status=OpeningRangeStatus(row["status"]),
            opening_range_high=row["opening_range_high"],
            opening_range_low=row["opening_range_low"],
            opening_range_mid=row["opening_range_mid"],
            width_absolute=row["width_absolute"],
            width_percent=row["width_percent"],
            completed_one_minute_bar_count=row["completed_bar_count"],
            expected_one_minute_bar_count=row["expected_bar_count"],
            coverage_ratio=row["coverage_ratio"],
            valid_volume_total=row["valid_volume_total"],
            first_valid_timestamp_utc=(
                datetime.fromisoformat(row["first_valid_timestamp_utc"])
                if row["first_valid_timestamp_utc"]
                else None
            ),
            last_valid_timestamp_utc=(
                datetime.fromisoformat(row["last_valid_timestamp_utc"])
                if row["last_valid_timestamp_utc"]
                else None
            ),
            data_quality_flags=tuple(json.loads(row["data_quality_flags_json"])),
            frozen_at_utc=(
                datetime.fromisoformat(row["frozen_at_utc"])
                if row["frozen_at_utc"]
                else None
            ),
            source_identity=row["source_identity"],
        )

    # -- Phase 2B Core research evidence ----------------------------------

    def persist_research_evaluation(self, session_id: str, evaluation) -> str:
        """Store one research evaluation and all its evidence, idempotently.

        A second identical replay inserts nothing anywhere: the candidate,
        every transition, the breakout, the pullback, the reclaim and the setup
        are each keyed on content-derived identities. Research only — this
        method has no order, execution, position or P&L counterpart.
        """

        now = _utc_now()
        candidate_id = evaluation.candidate_identity
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO orb_candidates VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate_id,
                    session_id,
                    evaluation.canonical_ticker,
                    evaluation.session_date.isoformat(),
                    evaluation.opening_range_revision,
                    evaluation.opening_range_version_identity,
                    evaluation.opening_range_version_mode.value,
                    evaluation.evaluation_mode.value,
                    evaluation.strategy_fingerprint,
                    evaluation.engine_version,
                    evaluation.final_state.value,
                    int(evaluation.terminal),
                    _json([r.value for r in evaluation.rejection_reasons]),
                    evaluation.evidence_fingerprint,
                    evaluation.transitions[-1].as_of_timestamp_utc.isoformat()
                    if evaluation.transitions
                    else now,
                    now,
                ),
            )
            for transition in evaluation.transitions:
                row_id = hashlib.sha256(
                    f"{candidate_id}|{transition.transition_id}".encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """INSERT OR IGNORE INTO orb_state_transitions VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row_id,
                        candidate_id,
                        transition.transition_id,
                        transition.sequence_index,
                        transition.canonical_ticker,
                        transition.session_date.isoformat(),
                        transition.opening_range_version_identity,
                        transition.opening_range_revision,
                        transition.prior_state.value,
                        transition.new_state.value,
                        transition.exchange_timestamp_utc.isoformat()
                        if transition.exchange_timestamp_utc
                        else None,
                        transition.as_of_timestamp_utc.isoformat(),
                        transition.rule_code.value,
                        _json(list(transition.evidence)),
                        _json([list(item) for item in transition.frozen_references]),
                        transition.market_time_status.value,
                        transition.historical_replay_status.value,
                        transition.live_freshness_status.value,
                        transition.live_decision_capability.value,
                        transition.evaluation_mode.value,
                        transition.strategy_fingerprint,
                        transition.engine_version,
                        now,
                    ),
                )
            breakout = evaluation.breakout
            if breakout is not None:
                connection.execute(
                    """INSERT OR IGNORE INTO orb_breakouts VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        hashlib.sha256(
                            f"{candidate_id}|{breakout.breakout_identity}".encode("utf-8")
                        ).hexdigest(),
                        candidate_id,
                        breakout.breakout_identity,
                        breakout.bar_start_utc.isoformat(),
                        breakout.bar_end_utc.isoformat(),
                        breakout.close,
                        breakout.high,
                        breakout.low,
                        breakout.opening_range_high,
                        breakout.distance_above_or_high,
                        breakout.distance_above_or_high_percent,
                        breakout.bar_range,
                        breakout.bar_range_percent,
                        breakout.bar_range_atr,
                        breakout.extension_atr,
                        breakout.update_count,
                        breakout.distance_to_daily_resistance,
                        breakout.distance_to_daily_resistance_percent,
                        breakout.intraday_atr.status.value,
                        breakout.intraday_atr.value,
                        breakout.zone.lower,
                        breakout.zone.upper,
                        breakout.zone.construction_rule,
                        int(breakout.accepted),
                        int(breakout.too_extended),
                        _json([r.value for r in breakout.extension_reasons]),
                        _json([r.value for r in breakout.rejection_reasons]),
                        now,
                    ),
                )
            pullback = evaluation.pullback
            if pullback is not None:
                connection.execute(
                    """INSERT OR IGNORE INTO orb_pullbacks VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        hashlib.sha256(
                            f"{candidate_id}|pullback|{pullback.ordinal}".encode("utf-8")
                        ).hexdigest(),
                        candidate_id,
                        pullback.ordinal,
                        pullback.start_bar_start_utc.isoformat(),
                        pullback.low,
                        pullback.low_bar_start_utc.isoformat(),
                        pullback.post_breakout_high,
                        pullback.depth_from_high,
                        pullback.depth_from_high_percent,
                        pullback.depth_atr,
                        pullback.depth_versus_or_high_percent,
                        pullback.bars_since_breakout,
                        pullback.duration_bars,
                        pullback.closes_below_or_high,
                        pullback.structural_breach_bars,
                        int(pullback.touched_or_high),
                        int(pullback.entered_breakout_zone),
                        pullback.ema9_five_minute,
                        pullback.ema20_five_minute,
                        pullback.ema9_one_minute,
                        pullback.breakout_bar_volume,
                        pullback.pullback_volume_total,
                        int(pullback.volume_assessed),
                        int(pullback.price_only),
                        int(pullback.healthy),
                        _json([r.value for r in pullback.rejection_reasons]),
                        now,
                    ),
                )
            reclaim = evaluation.reclaim
            if reclaim is not None:
                reclaim_identity = hashlib.sha256(
                    "|".join(
                        [
                            candidate_id,
                            reclaim.rule.value,
                            reclaim.confirmation_bar_start_utc.isoformat()
                            if reclaim.confirmation_bar_start_utc
                            else "none",
                        ]
                    ).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """INSERT OR IGNORE INTO orb_reclaims VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        reclaim_identity,
                        candidate_id,
                        reclaim_identity,
                        reclaim.rule.value,
                        int(reclaim.confirmed),
                        reclaim.confirmation_bar_start_utc.isoformat()
                        if reclaim.confirmation_bar_start_utc
                        else None,
                        reclaim.confirmation_bar_end_utc.isoformat()
                        if reclaim.confirmation_bar_end_utc
                        else None,
                        reclaim.trigger_price,
                        reclaim.opening_range_high,
                        reclaim.zone_lower,
                        reclaim.zone_upper,
                        reclaim.pullback_low,
                        reclaim.bars_elapsed_since_pullback_low,
                        _json([r.value for r in reclaim.rejection_reasons]),
                        now,
                    ),
                )
            risk, targets = evaluation.risk, evaluation.targets
            if (
                evaluation.research_ready
                and risk is not None
                and targets is not None
                and risk.proposed_stop is not None
            ):
                connection.execute(
                    """INSERT OR IGNORE INTO orb_research_setups VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        candidate_id,
                        targets.trigger_price,
                        risk.proposed_stop,
                        risk.stop_basis,
                        risk.raw_pullback_low,
                        risk.buffer_applied,
                        risk.buffer_basis,
                        risk.stop_distance_absolute,
                        risk.stop_distance_percent,
                        risk.stop_distance_atr,
                        targets.risk_per_share,
                        targets.target_1,
                        targets.target_2,
                        targets.nearest_daily_resistance,
                        targets.usable_target,
                        targets.effective_reward_risk,
                        1,
                        now,
                    ),
                )
        return candidate_id

    def load_state_transitions(self, candidate_id: str) -> tuple[dict, ...]:
        """Ordered transition history for one candidate."""

        with self.connect() as connection:
            rows = connection.execute(
                """SELECT prior_state,new_state,rule_code,sequence_index,
                          exchange_timestamp_utc,transition_id
                   FROM orb_state_transitions WHERE candidate_id=?
                   ORDER BY sequence_index""",
                (candidate_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    # -- Phase 2C Shadow integration --------------------------------------

    def start_shadow_run(
        self,
        run_id: str,
        session_date: date,
        *,
        mode: str,
        started_at_utc: datetime,
        runner_started_before_open: bool,
        source_path_identity: str,
        config_identity: str,
        strategy_fingerprint: str,
        engine_version: str,
        active_universe_only: bool = False,
        compared_live_run_id: str | None = None,
    ) -> str:
        """Register one Shadow run. Research only, production disabled."""

        now = _utc_now()
        with self.transaction() as connection:
            connection.execute(
                # Columns named explicitly: a later additive migration must not
                # be able to break this insert by changing the column count.
                """INSERT OR IGNORE INTO orb_shadow_runs
                   (run_id, session_date, mode, started_at_utc, finished_at_utc,
                    runner_started_before_open, stop_reason, session_classification,
                    source_path_identity, config_identity, strategy_fingerprint,
                    engine_version, research_only, production_disabled,
                    recorded_at_utc, active_universe_only, compared_live_run_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    session_date.isoformat(),
                    _enum_value(mode),
                    started_at_utc.isoformat(),
                    None,
                    int(bool(runner_started_before_open)),
                    None,
                    None,
                    str(source_path_identity),
                    str(config_identity),
                    str(strategy_fingerprint),
                    str(engine_version),
                    1,
                    1,
                    now,
                    int(bool(active_universe_only)),
                    compared_live_run_id,
                ),
            )
        return run_id

    def finish_shadow_run(
        self,
        run_id: str,
        *,
        finished_at_utc: datetime,
        stop_reason: str,
        session_classification: str,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """UPDATE orb_shadow_runs
                   SET finished_at_utc=?, stop_reason=?, session_classification=?
                   WHERE run_id=?""",
                (
                    finished_at_utc.isoformat(),
                    str(stop_reason),
                    _enum_value(session_classification),
                    run_id,
                ),
            )

    def save_shadow_cursor(self, run_id: str, cursor, connection=None) -> None:
        """Persist the source cursor.

        ``connection`` exists so the cursor can be written **inside the same
        transaction** as the batch it describes. That is what makes restart
        exact: the cursor can never be ahead of committed data, nor behind it
        in a way that would replay state.
        """

        payload = (
            run_id,
            cursor.source_table,
            int(cursor.last_source_id),
            cursor.last_market_timestamp_utc.isoformat()
            if cursor.last_market_timestamp_utc
            else None,
            cursor.last_receive_timestamp_utc.isoformat()
            if cursor.last_receive_timestamp_utc
            else None,
            int(cursor.rows_observed),
            _utc_now(),
        )
        sql = """INSERT INTO orb_shadow_cursors VALUES (?,?,?,?,?,?,?)
                 ON CONFLICT(run_id,source_table) DO UPDATE SET
                   last_source_id=excluded.last_source_id,
                   last_market_timestamp_utc=excluded.last_market_timestamp_utc,
                   last_receive_timestamp_utc=excluded.last_receive_timestamp_utc,
                   rows_observed=excluded.rows_observed,
                   updated_at_utc=excluded.updated_at_utc"""
        if connection is not None:
            connection.execute(sql, payload)
            return
        with self.transaction() as own:
            own.execute(sql, payload)

    def load_shadow_cursor(self, run_id: str, source_table: str):
        """Restore a cursor, or ``None`` when the run has never committed one."""

        from scalping_orb.shadow_source import ShadowCursor

        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM orb_shadow_cursors
                   WHERE run_id=? AND source_table=?""",
                (run_id, source_table),
            ).fetchone()
        if row is None:
            return None
        return ShadowCursor(
            source_table=row["source_table"],
            last_source_id=int(row["last_source_id"]),
            last_market_timestamp_utc=(
                datetime.fromisoformat(row["last_market_timestamp_utc"])
                if row["last_market_timestamp_utc"]
                else None
            ),
            last_receive_timestamp_utc=(
                datetime.fromisoformat(row["last_receive_timestamp_utc"])
                if row["last_receive_timestamp_utc"]
                else None
            ),
            rows_observed=int(row["rows_observed"]),
        )

    def record_shadow_heartbeat(
        self,
        run_id: str,
        *,
        observed_at_utc: datetime,
        live_status: str,
        last_source_id: int,
        latest_market_timestamp_utc: datetime | None,
        observed_receive_lag_seconds: float | None,
    ) -> None:
        """Small independent transaction so a long cycle still shows liveness."""

        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO orb_shadow_heartbeats VALUES (?,?,?,?,?,?)",
                (
                    run_id,
                    observed_at_utc.isoformat(),
                    _enum_value(live_status),
                    int(last_source_id),
                    latest_market_timestamp_utc.isoformat()
                    if latest_market_timestamp_utc
                    else None,
                    observed_receive_lag_seconds,
                ),
            )

    def persist_shadow_cycle(
        self,
        run_id: str,
        cycle,
        live_evaluations=(),
        *,
        cursor=None,
    ) -> str:
        """One atomic cycle: metrics, Lane A states and the cursor together.

        Lane A rows are keyed on the cycle, so a later observation of the same
        symbol appends rather than overwrites. Live history stays immutable.
        """

        now = _utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO orb_shadow_cycles VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cycle.cycle_id,
                    run_id,
                    int(cycle.cycle_index),
                    cycle.started_at_utc.isoformat(),
                    cycle.finished_at_utc.isoformat(),
                    int(cycle.cursor_low_source_id),
                    int(cycle.cursor_high_source_id),
                    int(cycle.source_rows_read),
                    int(cycle.normalized_events),
                    int(cycle.session_loads),
                    int(cycle.symbols_in_snapshot),
                    int(cycle.symbols_evaluated),
                    str(cycle.snapshot_identity),
                    _enum_value(cycle.live_status),
                    float(cycle.duration_seconds),
                    cycle.source_failure,
                    now,
                ),
            )
            for record in live_evaluations:
                row_id = hashlib.sha256(
                    "|".join(
                        [
                            run_id,
                            cycle.cycle_id,
                            record.canonical_ticker,
                            record.opening_range_version_identity,
                        ]
                    ).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """INSERT OR IGNORE INTO orb_shadow_live_states VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row_id,
                        run_id,
                        cycle.cycle_id,
                        record.session_date.isoformat(),
                        record.canonical_ticker,
                        int(record.opening_range_revision),
                        record.opening_range_version_identity,
                        record.final_state,
                        int(record.terminal),
                        _json(list(record.rejection_reasons)),
                        record.evidence_fingerprint,
                        record.candidate_identity,
                        _enum_value(record.live_status),
                        record.observed_at_utc.isoformat(),
                        record.exchange_watermark_utc.isoformat()
                        if record.exchange_watermark_utc
                        else None,
                        record.observed_receive_lag_seconds,
                        1,
                        now,
                    ),
                )
            if cursor is not None:
                self.save_shadow_cursor(run_id, cursor, connection=connection)
        return cycle.cycle_id

    def persist_shadow_reconstruction(self, run_id: str, records=()) -> int:
        """Lane B. Idempotent: an identical reconstruction inserts nothing."""

        now = _utc_now()
        written = 0
        with self.transaction() as connection:
            for record in records:
                row_id = hashlib.sha256(
                    "|".join(
                        [
                            run_id,
                            record.canonical_ticker,
                            record.opening_range_version_identity,
                            record.evaluation_mode,
                        ]
                    ).encode("utf-8")
                ).hexdigest()
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO orb_shadow_reconstruction_states VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row_id,
                        run_id,
                        record.session_date.isoformat(),
                        record.canonical_ticker,
                        int(record.opening_range_revision),
                        record.opening_range_version_identity,
                        record.final_state,
                        int(record.terminal),
                        _json(list(record.rejection_reasons)),
                        record.evidence_fingerprint,
                        record.candidate_identity,
                        _enum_value(record.evaluation_mode),
                        1,
                        now,
                    ),
                )
                written += int(cursor.rowcount == 1)
        return written

    def persist_shadow_comparison(self, run_id: str, rows=()) -> int:
        now = _utc_now()
        written = 0
        with self.transaction() as connection:
            for row in rows:
                row_id = hashlib.sha256(
                    f"{run_id}|{row.canonical_ticker}".encode("utf-8")
                ).hexdigest()
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO orb_shadow_live_replay_comparison VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row_id,
                        run_id,
                        row.session_date.isoformat(),
                        row.canonical_ticker,
                        row.live_state,
                        row.reconstruction_state,
                        row.live_opening_range_version_identity,
                        row.reconstruction_opening_range_version_identity,
                        int(row.opening_range_revised),
                        int(row.states_match),
                        _enum_value(row.difference_reason),
                        int(row.evaluable_live),
                        int(row.evaluable_historically),
                        now,
                    ),
                )
                written += int(cursor.rowcount == 1)
        return written

    def persist_shadow_session_quality(self, run_id: str, quality) -> None:
        now = _utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO orb_shadow_session_quality VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    quality.session_date.isoformat(),
                    _enum_value(quality.classification),
                    _json(list(quality.classification_reasons)),
                    int(quality.source_rows_observed),
                    int(quality.normalized_events),
                    int(quality.exact_redeliveries_removed),
                    int(quality.same_timestamp_distinct_retained),
                    int(quality.source_polling_failures),
                    quality.maximum_polling_gap_seconds,
                    quality.median_receive_lag_seconds,
                    quality.p90_receive_lag_seconds,
                    quality.p95_receive_lag_seconds,
                    quality.percent_above_freshness_budget,
                    int(quality.negative_lag_events),
                    int(quality.out_of_order_events),
                    int(quality.late_events_after_cutoff),
                    int(quality.active_mapped_symbols),
                    int(quality.completed_one_minute_bars),
                    int(quality.completed_five_minute_bars),
                    int(quality.opening_ranges_ready),
                    int(quality.live_evaluations),
                    int(quality.reconstruction_evaluations),
                    int(quality.heartbeat_count),
                    float(quality.process_runtime_seconds),
                    _json(quality.detail),
                    now,
                ),
            )

    def find_shadow_runs(
        self,
        *,
        session_date: date | None = None,
        source_path_identity: str | None = None,
        config_identity: str | None = None,
        modes: Sequence[str] | None = None,
        require_live_rows: bool = False,
    ) -> tuple[dict, ...]:
        """List Shadow runs with their Lane A / Lane B row counts.

        Read-only and deterministic: ordered by ``started_at_utc`` then
        ``run_id`` so two callers always see the same sequence. Exposes no
        credential and no quote payload — the source is identified only by its
        opaque path hash.
        """

        clauses: list[str] = []
        parameters: list = []
        if session_date is not None:
            clauses.append("r.session_date=?")
            parameters.append(session_date.isoformat())
        if source_path_identity is not None:
            clauses.append("r.source_path_identity=?")
            parameters.append(str(source_path_identity))
        if config_identity is not None:
            clauses.append("r.config_identity=?")
            parameters.append(str(config_identity))
        if modes:
            clauses.append(f"r.mode IN ({','.join('?' for _ in modes)})")
            parameters.extend(str(mode) for mode in modes)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT r.run_id, r.session_date, r.mode, r.started_at_utc,
                   r.finished_at_utc, r.stop_reason, r.session_classification,
                   r.source_path_identity, r.config_identity,
                   r.strategy_fingerprint, r.engine_version,
                   r.runner_started_before_open, r.research_only,
                   r.production_disabled, r.active_universe_only,
                   r.compared_live_run_id,
                   (SELECT count(*) FROM orb_shadow_live_states l
                      WHERE l.run_id=r.run_id) AS lane_a_rows,
                   (SELECT count(*) FROM orb_shadow_reconstruction_states s
                      WHERE s.run_id=r.run_id) AS lane_b_rows,
                   (SELECT count(*) FROM orb_shadow_cycles c
                      WHERE c.run_id=r.run_id) AS cycles
            FROM orb_shadow_runs r{where}
            ORDER BY r.started_at_utc, r.run_id"""
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        results = [dict(row) for row in rows]
        if require_live_rows:
            results = [row for row in results if row["lane_a_rows"] > 0]
        return tuple(results)

    def persist_cross_run_comparison(
        self, live_run_id: str, reconstruction_run_id: str, rows=()
    ) -> int:
        """Store a comparison naming *both* runs. Idempotent on rerun.

        Neither run is merged or mutated: this is a third record about the pair.
        """

        now = _utc_now()
        written = 0
        with self.transaction() as connection:
            for row in rows:
                row_id = hashlib.sha256(
                    f"{live_run_id}|{reconstruction_run_id}|{row.canonical_ticker}".encode(
                        "utf-8"
                    )
                ).hexdigest()
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO orb_shadow_cross_run_comparison VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row_id,
                        live_run_id,
                        reconstruction_run_id,
                        row.session_date.isoformat(),
                        row.canonical_ticker,
                        row.live_state,
                        row.reconstruction_state,
                        row.live_opening_range_version_identity,
                        row.reconstruction_opening_range_version_identity,
                        _enum_value(row.live_status) if row.live_status else None,
                        _json(list(row.live_rejection_reasons)),
                        int(row.opening_range_revised),
                        int(row.states_match),
                        _enum_value(row.difference_reason),
                        _json(list(row.difference_evidence)),
                        int(row.evaluable_live),
                        int(row.evaluable_historically),
                        1,
                        now,
                    ),
                )
                written += int(cursor.rowcount == 1)
            connection.execute(
                "UPDATE orb_shadow_runs SET compared_live_run_id=? WHERE run_id=?",
                (live_run_id, reconstruction_run_id),
            )
        return written

    def load_cross_run_comparison(
        self, live_run_id: str, reconstruction_run_id: str
    ) -> tuple[dict, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT canonical_ticker, live_state, reconstruction_state,
                          difference_reason, difference_evidence_json,
                          opening_range_revised, states_match
                   FROM orb_shadow_cross_run_comparison
                   WHERE live_run_id=? AND reconstruction_run_id=?
                   ORDER BY canonical_ticker""",
                (live_run_id, reconstruction_run_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def load_shadow_live_states_full(self, run_id: str) -> tuple[dict, ...]:
        """Every stored field of a run's Lane A rows, for cross-run comparison."""

        with self.connect() as connection:
            rows = connection.execute(
                """SELECT canonical_ticker, session_date, final_state, terminal,
                          live_status, rejection_reasons_json,
                          opening_range_revision, opening_range_version_identity,
                          evidence_fingerprint, candidate_identity,
                          observed_at_utc, exchange_watermark_utc,
                          observed_receive_lag_seconds
                   FROM orb_shadow_live_states WHERE run_id=?
                   ORDER BY observed_at_utc, canonical_ticker""",
                (run_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def load_shadow_live_states(self, run_id: str) -> tuple[dict, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT canonical_ticker,final_state,live_status,observed_at_utc,
                          opening_range_version_identity,cycle_id
                   FROM orb_shadow_live_states WHERE run_id=?
                   ORDER BY observed_at_utc,canonical_ticker""",
                (run_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def load_shadow_reconstruction_states(self, run_id: str) -> tuple[dict, ...]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT canonical_ticker,final_state,opening_range_version_identity,
                          evaluation_mode
                   FROM orb_shadow_reconstruction_states WHERE run_id=?
                   ORDER BY canonical_ticker""",
                (run_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def table_count(self, table: str) -> int:
        allowed = {
            "orb_sessions",
            "orb_normalized_events",
            "orb_bars",
            "orb_opening_ranges",
            "orb_data_quality_events",
            "orb_capabilities",
            "orb_collection_runs",
            "orb_candidates",
            "orb_state_transitions",
            "orb_breakouts",
            "orb_pullbacks",
            "orb_reclaims",
            "orb_research_setups",
            "orb_shadow_runs",
            "orb_shadow_cursors",
            "orb_shadow_cycles",
            "orb_shadow_heartbeats",
            "orb_shadow_session_quality",
            "orb_shadow_live_states",
            "orb_shadow_reconstruction_states",
            "orb_shadow_live_replay_comparison",
            "orb_shadow_cross_run_comparison",
        }
        if table not in allowed:
            raise ValueError("Unsupported ORB table")
        with self.connect() as connection:
            return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])

    def prune_normalized_events_before(self, cutoff_utc: datetime, batch_size: int) -> int:
        cutoff = cutoff_utc.astimezone(timezone.utc).isoformat()
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT source_identity FROM orb_normalized_events WHERE market_timestamp_utc<? ORDER BY market_timestamp_utc LIMIT ?",
                (cutoff, max(1, int(batch_size))),
            ).fetchall()
            if not rows:
                return 0
            connection.executemany(
                "DELETE FROM orb_normalized_events WHERE source_identity=?",
                ((row[0],) for row in rows),
            )
            return len(rows)

    def prune_derived_sessions_before(self, cutoff_date: date, batch_size: int) -> int:
        """Delete complete old research sessions in bounded, atomic batches."""

        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT session_id FROM orb_sessions WHERE session_date<?
                   ORDER BY session_date,session_id LIMIT ?""",
                (cutoff_date.isoformat(), max(1, int(batch_size))),
            ).fetchall()
            session_ids = tuple(row[0] for row in rows)
            if not session_ids:
                return 0
            for session_id in session_ids:
                for table in (
                    "orb_capabilities",
                    "orb_opening_ranges",
                    "orb_bars",
                    "orb_data_quality_events",
                    "orb_normalized_events",
                ):
                    connection.execute(
                        f"DELETE FROM {table} WHERE session_id=?", (session_id,)
                    )
                connection.execute(
                    "DELETE FROM orb_sessions WHERE session_id=?", (session_id,)
                )
            return len(session_ids)

    def run_retention(
        self,
        *,
        evaluated_at: datetime,
        event_retention_days: int,
        derived_data_retention_days: int,
        batch_size: int,
    ) -> RetentionResult:
        """Execute the configured bounded retention policy at an ingestion boundary."""

        evaluated = evaluated_at.astimezone(timezone.utc)
        event_cutoff = evaluated - timedelta(days=max(1, int(event_retention_days)))
        derived_cutoff = (
            evaluated - timedelta(days=max(1, int(derived_data_retention_days)))
        ).date()
        return RetentionResult(
            normalized_events_deleted=self.prune_normalized_events_before(
                event_cutoff, batch_size
            ),
            derived_sessions_deleted=self.prune_derived_sessions_before(
                derived_cutoff, batch_size
            ),
        )
