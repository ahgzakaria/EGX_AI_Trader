"""Read-only daily assistant view over ORB shadow-session evidence.

Answers one question for a human: *what did the ORB engine find today, and what
were its own levels?* It computes no strategy value of its own.

Hard properties:

* **Read-only.** Every connection is ``mode=ro`` with ``PRAGMA query_only=ON``.
  Session databases are opened without ``immutable=1`` so a session still being
  written is read correctly rather than from a stale snapshot.
* **Never recomputes a level.** Trigger, stop, targets, risk-per-share, R
  multiples, reward/risk and ATR are read from ``orb_signal_qualification`` —
  the values the engine itself produced — or reported as absent. Deriving them
  from bars afterwards would invent numbers the engine never used.
* **Never writes to Rubix** and never opens it at all: signal evidence lives in
  the session database, so this module has no reason to touch the live feed.
* **Sector is context.** Enrichment happens after the signal is read and
  changes nothing about it. An unmapped ticker renders UNKNOWN and is still
  listed.
* **No execution vocabulary.** There is no fill, no position, no order. Prices
  here are engine-proposed levels, not fills.

Qualification availability is explicit rather than silently blank. The frozen
2026-08-04…08-12 sessions were recorded at schema v8, before the qualification
table existed, so their levels were never persisted and cannot be recovered —
they report ``QUALIFICATION_NOT_PERSISTED``. Sessions recorded with the
qualification migration present carry real levels.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from core.sector_context import SectorContext, sector_context
from scalping_orb.repository import PROTECTED_DATABASE_NAMES

#: Lane A — the live-follow run. Matches ``signal_outcomes.LIVE_LANE_MODE``;
#: reconstruction runs (``RECONSTRUCT``) are a separate lane and are not the
#: assistant's subject.
LIVE_LANE_MODE = "FOLLOW"

ENTRY_READY_STATE = "ENTRY_READY_RESEARCH"

DEFAULT_SESSION_DIR = "data/research/orb_full_shadow"
SESSION_FILENAME_PREFIX = "orb_full_shadow_"

#: Override the session location with an explicit directory.
SESSION_DIR_ENV = "ORB_SESSION_DIR"

CAIRO = ZoneInfo("Africa/Cairo")

QUALIFICATION_TABLE = "orb_signal_qualification"

#: The session predates the qualification table entirely — the levels were
#: never written and are not reconstructible.
NOT_PERSISTED = "QUALIFICATION_NOT_PERSISTED"
#: The table exists but holds no row for this signal.
MISSING_FOR_SIGNAL = "QUALIFICATION_MISSING_FOR_SIGNAL"


class AssistantSourceUnavailable(RuntimeError):
    """The session evidence could not be read. Not raised for zero signals."""


@dataclass(frozen=True)
class SignalRow:
    """One ENTRY_READY_RESEARCH signal, with engine levels when persisted."""

    canonical_ticker: str
    company_name: str
    sector_id: str
    sector_name: str
    sector_rank: int | None
    market_cap_egp: float | None

    signal_state: str
    first_detected_utc: datetime | None
    episode_count: int

    qualification_status: str
    trigger_price: float | None = None
    proposed_stop: float | None = None
    stop_basis: str | None = None
    risk_per_share: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    target_1_r_multiple: float | None = None
    target_2_r_multiple: float | None = None
    usable_target: float | None = None
    effective_reward_risk: float | None = None
    atr_value: float | None = None
    atr_status: str | None = None
    daily_resistance_status: str | None = None
    opening_range_version_identity: str = ""
    evidence_fingerprint: str = ""

    @property
    def has_levels(self) -> bool:
        return self.trigger_price is not None and self.proposed_stop is not None

    @property
    def first_detected_cairo(self) -> datetime | None:
        if self.first_detected_utc is None:
            return None
        return self.first_detected_utc.astimezone(CAIRO)

    @property
    def detection_time_label(self) -> str:
        """``10:46`` in exchange local time, or ``—``."""

        local = self.first_detected_cairo
        return local.strftime("%H:%M") if local else "—"

    @property
    def market_cap_billions(self) -> float | None:
        if self.market_cap_egp is None:
            return None
        return self.market_cap_egp / 1_000_000_000


@dataclass(frozen=True)
class SectorTally:
    sector_id: str
    sector_name: str
    signal_count: int


@dataclass(frozen=True)
class DailyAssistantReport:
    session_date: date
    database_path: str
    run_id: str
    run_mode: str
    artifact_lane: str | None
    engine_version: str
    strategy_fingerprint: str
    stop_reason: str | None
    schema_version: int | None
    qualification_available: bool
    signals: tuple[SignalRow, ...]

    @property
    def signal_count(self) -> int:
        return len(self.signals)

    @property
    def sector_summary(self) -> tuple[SectorTally, ...]:
        """Signals per sector. Descriptive only — never feeds qualification.

        Reconciles with :attr:`signals` by construction: one increment per row.
        """

        counts = Counter(
            (row.sector_id, row.sector_name) for row in self.signals
        )
        return tuple(
            SectorTally(sector_id=key[0], sector_name=key[1], signal_count=value)
            for key, value in sorted(
                counts.items(), key=lambda item: (-item[1], item[0][1])
            )
        )

    @property
    def levels_available_count(self) -> int:
        return sum(1 for row in self.signals if row.has_levels)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    """The only database entry point. Refuses anything but a read."""

    resolved = Path(path)
    if not resolved.is_file():
        raise AssistantSourceUnavailable(f"session database not found: {resolved}")
    if resolved.name.lower() in PROTECTED_DATABASE_NAMES:
        # A protected operational database is never a session-evidence source.
        # Failing loudly beats reading the wrong file read-only and reporting
        # nothing.
        raise AssistantSourceUnavailable(
            f"'{resolved.name}' is a protected operational database and is not "
            "an ORB session source"
        )
    connection = sqlite3.connect(
        f"file:{resolved.resolve().as_posix()}?mode=ro", uri=True, timeout=30
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _parse_aware(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def session_date_of(path) -> date:
    """Session date from the conventional filename."""

    stem = Path(path).stem
    match = re.fullmatch(rf"{SESSION_FILENAME_PREFIX}(\d{{4}}-\d{{2}}-\d{{2}})", stem)
    if match is None:
        raise ValueError(f"cannot infer a session date from '{Path(path).name}'")
    return date.fromisoformat(match.group(1))


def available_sessions(directory=DEFAULT_SESSION_DIR):
    """Discovered ``(session_date, path)`` pairs, newest first.

    A directory that does not exist yields nothing rather than raising: the
    assistant should render an empty state, not a stack trace.
    """

    base = Path(directory)
    if not base.is_dir():
        return ()
    found = []
    for path in base.glob(f"{SESSION_FILENAME_PREFIX}*.db"):
        try:
            found.append((session_date_of(path), path))
        except ValueError:
            continue
    return tuple(sorted(found, key=lambda item: item[0], reverse=True))


def session_directory(explicit=None) -> Path:
    """The ONE directory session databases are read from. Deterministic.

    Precedence is fixed and does not depend on what happens to exist on disk:

    1. an explicit argument,
    2. the ``ORB_SESSION_DIR`` environment variable,
    3. this project's own ``data/research/orb_full_shadow``.

    Resolution deliberately never probes a list of candidates and never falls
    through to another checkout's data directory just because that one has
    files in it. A search that picks whichever location is populated makes the
    answer to "which session am I looking at?" depend on unrelated state — and
    it silently hid the frozen evidence the moment this worktree grew a session
    of its own. Pointing at another location is a decision the operator makes
    explicitly, via the environment variable.
    """

    import os

    if explicit:
        return Path(explicit)
    configured = os.getenv(SESSION_DIR_ENV, "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / DEFAULT_SESSION_DIR


def discover_sessions(explicit=None):
    """``(directory, sessions)`` for the one resolved directory.

    ``sessions`` is empty when that directory holds none, so a caller renders
    an empty state instead of failing — and instead of quietly showing a
    different directory's sessions.
    """

    directory = session_directory(explicit)
    return directory, available_sessions(directory)


def _schema_version(connection) -> int | None:
    if not _table_exists(connection, "orb_schema_meta"):
        return None
    row = connection.execute("SELECT max(version) FROM orb_schema_meta").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _live_run(connection):
    """The most recent Lane A / FOLLOW run in this database."""

    rows = connection.execute(
        """
        SELECT run_id, mode, session_date, artifact_lane, stop_reason,
               engine_version, strategy_fingerprint, started_at_utc
        FROM orb_shadow_runs
        WHERE mode = ?
        ORDER BY started_at_utc DESC
        """,
        (LIVE_LANE_MODE,),
    ).fetchall()
    if not rows:
        raise AssistantSourceUnavailable(
            f"no {LIVE_LANE_MODE} (live lane) run in this session database"
        )
    return rows[0]


def _qualification_index(connection, run_id, available):
    if not available:
        return {}
    rows = connection.execute(
        f"""
        SELECT canonical_ticker, trigger_price, proposed_stop, stop_basis,
               risk_per_share, target_1, target_2, target_1_r_multiple,
               target_2_r_multiple, usable_target, effective_reward_risk,
               atr_value, atr_status, daily_resistance_status,
               qualification_status, detection_at_utc
        FROM {QUALIFICATION_TABLE}
        WHERE run_id = ?
        ORDER BY detection_at_utc
        """,
        (run_id,),
    ).fetchall()
    index = {}
    for row in rows:
        # First qualification per ticker matches the first-detection semantics
        # used for the signal row itself.
        index.setdefault(row["canonical_ticker"], row)
    return index


def load_daily_report(database_path, sector_map_path=None) -> DailyAssistantReport:
    """Build the assistant view for one ORB session database."""

    path = Path(database_path)
    connection = _connect_read_only(path)
    try:
        run = _live_run(connection)
        schema_version = _schema_version(connection)
        qualification_available = _table_exists(connection, QUALIFICATION_TABLE)
        qualification = _qualification_index(
            connection, run["run_id"], qualification_available
        )

        rows = connection.execute(
            """
            SELECT canonical_ticker,
                   MIN(observed_at_utc)               AS first_detected_utc,
                   COUNT(*)                           AS episode_count,
                   MIN(opening_range_version_identity) AS or_identity,
                   MIN(evidence_fingerprint)          AS evidence_fingerprint
            FROM orb_shadow_live_states
            WHERE run_id = ? AND final_state = ?
            GROUP BY canonical_ticker
            ORDER BY first_detected_utc, canonical_ticker
            """,
            (run["run_id"], ENTRY_READY_STATE),
        ).fetchall()

        signals = []
        for row in rows:
            ticker = row["canonical_ticker"]
            context: SectorContext = sector_context(ticker, sector_map_path)
            qualification_row = qualification.get(ticker)
            if qualification_row is None:
                status = MISSING_FOR_SIGNAL if qualification_available else NOT_PERSISTED
                signals.append(SignalRow(
                    canonical_ticker=ticker,
                    company_name=context.company_name,
                    sector_id=context.sector_id,
                    sector_name=context.sector_name,
                    sector_rank=context.sector_rank,
                    market_cap_egp=context.market_cap_egp,
                    signal_state=ENTRY_READY_STATE,
                    first_detected_utc=_parse_aware(row["first_detected_utc"]),
                    episode_count=int(row["episode_count"]),
                    qualification_status=status,
                    opening_range_version_identity=str(row["or_identity"] or ""),
                    evidence_fingerprint=str(row["evidence_fingerprint"] or ""),
                ))
                continue

            signals.append(SignalRow(
                canonical_ticker=ticker,
                company_name=context.company_name,
                sector_id=context.sector_id,
                sector_name=context.sector_name,
                sector_rank=context.sector_rank,
                market_cap_egp=context.market_cap_egp,
                signal_state=ENTRY_READY_STATE,
                first_detected_utc=_parse_aware(row["first_detected_utc"]),
                episode_count=int(row["episode_count"]),
                qualification_status=str(qualification_row["qualification_status"]),
                trigger_price=qualification_row["trigger_price"],
                proposed_stop=qualification_row["proposed_stop"],
                stop_basis=qualification_row["stop_basis"],
                risk_per_share=qualification_row["risk_per_share"],
                target_1=qualification_row["target_1"],
                target_2=qualification_row["target_2"],
                target_1_r_multiple=qualification_row["target_1_r_multiple"],
                target_2_r_multiple=qualification_row["target_2_r_multiple"],
                usable_target=qualification_row["usable_target"],
                effective_reward_risk=qualification_row["effective_reward_risk"],
                atr_value=qualification_row["atr_value"],
                atr_status=qualification_row["atr_status"],
                daily_resistance_status=qualification_row["daily_resistance_status"],
                opening_range_version_identity=str(row["or_identity"] or ""),
                evidence_fingerprint=str(row["evidence_fingerprint"] or ""),
            ))

        return DailyAssistantReport(
            session_date=session_date_of(path),
            database_path=str(path),
            run_id=str(run["run_id"]),
            run_mode=str(run["mode"]),
            artifact_lane=run["artifact_lane"],
            engine_version=str(run["engine_version"] or ""),
            strategy_fingerprint=str(run["strategy_fingerprint"] or ""),
            stop_reason=run["stop_reason"],
            schema_version=schema_version,
            qualification_available=qualification_available,
            signals=tuple(signals),
        )
    finally:
        connection.close()
