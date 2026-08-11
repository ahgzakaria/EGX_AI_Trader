"""Persistence for measured signal outcomes, in its own database.

The outcomes live in a database of their own rather than inside the shadow
session files. Those files are the frozen record of what the lanes observed;
appending a later derived measurement into them would make the evidence and
the analysis of the evidence share a filename, and the next person to ask
"what did Lane A actually see?" would have to disentangle them.

Writes here are idempotent on `(session_date, canonical_ticker, lane_a_run_id)`,
so re-measuring a session replaces its rows rather than accumulating a second
opinion beside the first.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Sequence
from uuid import uuid4

from scalping_orb.performance.signal_outcomes import (
    OutcomeMeasurementConfig,
    SignalOutcome,
)
from scalping_orb.repository import PROTECTED_DATABASE_NAMES


SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS orb_signal_outcome_meta (
    version INTEGER PRIMARY KEY,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orb_signal_outcome_runs (
    measurement_run_id TEXT PRIMARY KEY,
    generated_at_utc TEXT NOT NULL,
    session_date TEXT NOT NULL,
    lane_a_run_id TEXT NOT NULL,
    shadow_database TEXT NOT NULL,
    price_source_database TEXT NOT NULL,
    session_end_utc TEXT NOT NULL,
    measurement_config_json TEXT NOT NULL,
    measurement_config_fingerprint TEXT NOT NULL,
    signals_measured INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orb_signal_outcomes (
    session_date TEXT NOT NULL,
    canonical_ticker TEXT NOT NULL,
    lane_a_run_id TEXT NOT NULL,
    measurement_run_id TEXT NOT NULL,
    detection_timestamp_utc TEXT NOT NULL,
    entry_ready_observation_count INTEGER NOT NULL,
    entry_ready_episode_count INTEGER NOT NULL,
    last_entry_ready_observed_at_utc TEXT NOT NULL,
    opening_range_version_identity TEXT,
    detection_evidence_fingerprint TEXT,
    session_end_utc TEXT NOT NULL,
    measurement_quality TEXT NOT NULL,
    measurement_reasons_json TEXT NOT NULL,
    tp_sl_status TEXT NOT NULL,
    entry_quote_market_timestamp_utc TEXT,
    entry_lag_seconds REAL,
    entry_price REAL,
    observation_count INTEGER NOT NULL,
    price_change_count INTEGER NOT NULL,
    last_observation_utc TEXT,
    maximum_observation_gap_seconds REAL,
    tail_gap_seconds REAL,
    maximum_favorable_price REAL,
    maximum_favorable_at_utc TEXT,
    maximum_favorable_excursion_absolute REAL,
    maximum_favorable_excursion_percent REAL,
    seconds_to_maximum_favorable REAL,
    maximum_adverse_price REAL,
    maximum_adverse_at_utc TEXT,
    maximum_adverse_excursion_absolute REAL,
    maximum_adverse_excursion_percent REAL,
    seconds_to_maximum_adverse REAL,
    session_end_price REAL,
    session_end_absolute REAL,
    session_end_percent REAL,
    recorded_at_utc TEXT NOT NULL,
    PRIMARY KEY (session_date, canonical_ticker, lane_a_run_id)
);

CREATE INDEX IF NOT EXISTS idx_orb_signal_outcomes_session
    ON orb_signal_outcomes(session_date);
CREATE INDEX IF NOT EXISTS idx_orb_signal_outcomes_quality
    ON orb_signal_outcomes(measurement_quality);
"""

_COLUMNS = (
    "session_date",
    "canonical_ticker",
    "lane_a_run_id",
    "measurement_run_id",
    "detection_timestamp_utc",
    "entry_ready_observation_count",
    "entry_ready_episode_count",
    "last_entry_ready_observed_at_utc",
    "opening_range_version_identity",
    "detection_evidence_fingerprint",
    "session_end_utc",
    "measurement_quality",
    "measurement_reasons_json",
    "tp_sl_status",
    "entry_quote_market_timestamp_utc",
    "entry_lag_seconds",
    "entry_price",
    "observation_count",
    "price_change_count",
    "last_observation_utc",
    "maximum_observation_gap_seconds",
    "tail_gap_seconds",
    "maximum_favorable_price",
    "maximum_favorable_at_utc",
    "maximum_favorable_excursion_absolute",
    "maximum_favorable_excursion_percent",
    "seconds_to_maximum_favorable",
    "maximum_adverse_price",
    "maximum_adverse_at_utc",
    "maximum_adverse_excursion_absolute",
    "maximum_adverse_excursion_percent",
    "seconds_to_maximum_adverse",
    "session_end_price",
    "session_end_absolute",
    "session_end_percent",
    "recorded_at_utc",
)


def _stamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat()


class OutcomeStore:
    """A small write-owned SQLite store for measured outcomes."""

    def __init__(self, path, *, busy_timeout_ms: int = 30_000):
        self.path = Path(path)
        if self.path.name in PROTECTED_DATABASE_NAMES:
            raise ValueError(
                f"{self.path.name} is a protected production database and is "
                "never written by outcome measurement"
            )
        self.busy_timeout_ms = int(busy_timeout_ms)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path, timeout=self.busy_timeout_ms / 1000.0
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> int:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO orb_signal_outcome_meta(version, applied_at_utc) "
                "VALUES (?, ?)",
                (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
        return SCHEMA_VERSION

    def record(
        self,
        outcomes: Sequence[SignalOutcome],
        *,
        session_date,
        lane_a_run_id: str,
        shadow_database,
        price_source_database,
        session_end_utc: datetime,
        config: OutcomeMeasurementConfig,
        measurement_run_id: str | None = None,
    ) -> str:
        """Persist one session's outcomes as a single atomic measurement run."""

        run_id = measurement_run_id or uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        placeholders = ", ".join("?" for _ in _COLUMNS)
        updates = ", ".join(
            f"{name}=excluded.{name}"
            for name in _COLUMNS
            if name not in ("session_date", "canonical_ticker", "lane_a_run_id")
        )
        statement = (
            f"INSERT INTO orb_signal_outcomes ({', '.join(_COLUMNS)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(session_date, canonical_ticker, lane_a_run_id) "
            f"DO UPDATE SET {updates}"
        )
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO orb_signal_outcome_meta(version, applied_at_utc) "
                "VALUES (?, ?)",
                (SCHEMA_VERSION, now),
            )
            with connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO orb_signal_outcome_runs (
                        measurement_run_id, generated_at_utc, session_date,
                        lane_a_run_id, shadow_database, price_source_database,
                        session_end_utc, measurement_config_json,
                        measurement_config_fingerprint, signals_measured
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        now,
                        str(session_date),
                        str(lane_a_run_id),
                        str(shadow_database),
                        str(price_source_database),
                        _stamp(session_end_utc),
                        json.dumps(config.as_dict(), sort_keys=True),
                        config.fingerprint,
                        len(outcomes),
                    ),
                )
                connection.executemany(
                    statement,
                    [self._row(outcome, run_id, now) for outcome in outcomes],
                )
        return run_id

    @staticmethod
    def _row(outcome: SignalOutcome, run_id: str, now: str) -> tuple:
        signal = outcome.signal
        return (
            signal.session_date.isoformat(),
            signal.canonical_ticker,
            signal.lane_a_run_id,
            run_id,
            _stamp(signal.detection_timestamp_utc),
            int(signal.entry_ready_observation_count),
            int(signal.entry_ready_episode_count),
            _stamp(signal.last_entry_ready_observed_at_utc),
            signal.opening_range_version_identity,
            signal.detection_evidence_fingerprint,
            _stamp(outcome.session_end_utc),
            outcome.measurement_quality.value,
            json.dumps([reason.value for reason in outcome.measurement_reasons]),
            outcome.tp_sl_status.value,
            _stamp(outcome.entry_quote_market_timestamp_utc),
            outcome.entry_lag_seconds,
            outcome.entry_price,
            int(outcome.observation_count),
            int(outcome.price_change_count),
            _stamp(outcome.last_observation_utc),
            outcome.maximum_observation_gap_seconds,
            outcome.tail_gap_seconds,
            outcome.maximum_favorable_price,
            _stamp(outcome.maximum_favorable_at_utc),
            outcome.maximum_favorable_excursion_absolute,
            outcome.maximum_favorable_excursion_percent,
            outcome.seconds_to_maximum_favorable,
            outcome.maximum_adverse_price,
            _stamp(outcome.maximum_adverse_at_utc),
            outcome.maximum_adverse_excursion_absolute,
            outcome.maximum_adverse_excursion_percent,
            outcome.seconds_to_maximum_adverse,
            outcome.session_end_price,
            outcome.session_end_absolute,
            outcome.session_end_percent,
            now,
        )

    # -- read back ---------------------------------------------------------

    def rows(self, session_date=None) -> tuple[sqlite3.Row, ...]:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            if session_date is None:
                cursor = connection.execute(
                    "SELECT * FROM orb_signal_outcomes "
                    "ORDER BY session_date, detection_timestamp_utc, canonical_ticker"
                )
            else:
                cursor = connection.execute(
                    "SELECT * FROM orb_signal_outcomes WHERE session_date = ? "
                    "ORDER BY detection_timestamp_utc, canonical_ticker",
                    (str(session_date),),
                )
            return tuple(cursor.fetchall())

    def quality_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            return {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT measurement_quality, COUNT(*) FROM orb_signal_outcomes "
                    "GROUP BY measurement_quality ORDER BY 2 DESC"
                )
            }


def outcome_rows_as_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]
