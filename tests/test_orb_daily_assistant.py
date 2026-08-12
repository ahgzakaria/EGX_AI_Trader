"""Focused invariants for the sector context + ORB daily assistant.

Scope is deliberately narrow. These protect properties whose violation would
either corrupt research evidence, silently change strategy behaviour, or drop a
real signal from the owner's view. Getters, formatting helpers and dataclass
field presence are not tested.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3

import pytest

from core import sector_context as sector_module
from core.sector_context import UNKNOWN_SECTOR_ID, sector_context
from services import orb_daily_assistant as assistant
from services.orb_daily_assistant import (
    AssistantSourceUnavailable,
    NOT_PERSISTED,
    load_daily_report,
)

def _resolve_rubix_db():
    """Locate the real Rubix database so the read-only guarantee is verified.

    A worktree has no ``data/*.db`` of its own (they are untracked), so a bare
    relative path would silently skip the two most important tests here. Honour
    ``RUBIX_DB_PATH`` the way the provider does, then fall back to the primary
    checkout.
    """

    import os

    candidates = []
    configured = os.getenv("RUBIX_DB_PATH", "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path("data/rubix_live_market.db"))
    candidates.append(Path(r"F:\EGX_AI_Trader\data\rubix_live_market.db"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


RUBIX_DB = _resolve_rubix_db()

SECTOR_MAP = (
    "canonical_ticker,company_name,sector_id,sector_name,sector_rank,"
    "market_cap_egp,source,source_as_of,workbook_sha256\n"
    "MFPC,Misr Fertilizers,basic_resources,Basic Resources,2,106121189731,t,d,h\n"
    "EGAL,Egypt Aluminum,basic_resources,Basic Resources,1,124987500000,t,d,h\n"
    "JUFO,Juhayna Food,food_and_beverages,Food and Beverages,3,39170000000,t,d,h\n"
)


@pytest.fixture(autouse=True)
def _clear_sector_cache():
    sector_module.clear_cache()
    yield
    sector_module.clear_cache()


@pytest.fixture
def sector_map(tmp_path) -> Path:
    path = tmp_path / "egx_sector_map.csv"
    path.write_text(SECTOR_MAP, encoding="utf-8")
    return path


def _build_session(path: Path, *, with_qualification: bool, tickers=("MFPC", "JUFO")):
    """A minimal Lane A session database shaped like the real one."""

    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE orb_schema_meta (version INTEGER, name TEXT);
        CREATE TABLE orb_shadow_runs (
            run_id TEXT, session_date TEXT, mode TEXT, started_at_utc TEXT,
            artifact_lane TEXT, stop_reason TEXT, engine_version TEXT,
            strategy_fingerprint TEXT
        );
        CREATE TABLE orb_shadow_live_states (
            live_state_id TEXT, run_id TEXT, session_date TEXT,
            canonical_ticker TEXT, final_state TEXT, observed_at_utc TEXT,
            opening_range_version_identity TEXT, evidence_fingerprint TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO orb_schema_meta VALUES (?,?)",
        (9 if with_qualification else 8, "test"),
    )
    connection.execute(
        "INSERT INTO orb_shadow_runs VALUES (?,?,?,?,?,?,?,?)",
        ("run-a", "2026-08-12", "FOLLOW", "2026-08-12T07:00:00+00:00",
         "LANE_A", "CONTINUOUS_END_REACHED", "engine/1.0.0", "fp-1"),
    )
    # A reconstruction run must never be picked up as the live lane.
    connection.execute(
        "INSERT INTO orb_shadow_runs VALUES (?,?,?,?,?,?,?,?)",
        ("run-b", "2026-08-12", "RECONSTRUCT", "2026-08-12T12:00:00+00:00",
         "LANE_B", "RECONSTRUCTION_COMPLETE", "engine/1.0.0", "fp-1"),
    )
    for index, ticker in enumerate(tickers):
        for episode in range(2):
            connection.execute(
                "INSERT INTO orb_shadow_live_states VALUES (?,?,?,?,?,?,?,?)",
                (f"{ticker}-{episode}", "run-a", "2026-08-12", ticker,
                 "ENTRY_READY_RESEARCH",
                 f"2026-08-12T07:{40 + index * 5 + episode:02d}:00+00:00",
                 "or-identity", "evidence-fp"),
            )
    # Noise the reader must exclude: a non-signal state, and a signal belonging
    # to the reconstruction run.
    connection.execute(
        "INSERT INTO orb_shadow_live_states VALUES (?,?,?,?,?,?,?,?)",
        ("noise-1", "run-a", "2026-08-12", "ZZZZ", "WAIT_BREAKOUT",
         "2026-08-12T07:30:00+00:00", "or-identity", "evidence-fp"),
    )
    connection.execute(
        "INSERT INTO orb_shadow_live_states VALUES (?,?,?,?,?,?,?,?)",
        ("noise-2", "run-b", "2026-08-12", "EGAL", "ENTRY_READY_RESEARCH",
         "2026-08-12T07:45:00+00:00", "or-identity", "evidence-fp"),
    )

    if with_qualification:
        connection.executescript(
            """
            CREATE TABLE orb_signal_qualification (
                run_id TEXT, canonical_ticker TEXT, detection_at_utc TEXT,
                trigger_price REAL, proposed_stop REAL, stop_basis TEXT,
                risk_per_share REAL, target_1 REAL, target_2 REAL,
                target_1_r_multiple REAL, target_2_r_multiple REAL,
                usable_target REAL, effective_reward_risk REAL,
                atr_value REAL, atr_status TEXT, daily_resistance_status TEXT,
                qualification_status TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO orb_signal_qualification VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("run-a", "MFPC", "2026-08-12T07:40:00+00:00",
             37.5, 36.9, "PULLBACK_LOW_WITH_BUFFER", 0.6, 38.7, 39.3,
             2.0, 4.0, 38.7, 2.0, 0.31, "INTRADAY_ATR_AVAILABLE",
             "D1_CONTEXT_UNAVAILABLE", "QUALIFIED_RESEARCH"),
        )
    connection.commit()
    connection.close()


# --- 1. Rubix remains read-only -------------------------------------------

@pytest.mark.skipif(not RUBIX_DB.is_file(), reason="Rubix database not present")
def test_rubix_is_never_opened_writable_by_the_assistant():
    """The assistant must refuse a protected operational database outright."""

    with pytest.raises(AssistantSourceUnavailable, match="protected"):
        load_daily_report(RUBIX_DB)


@pytest.mark.skipif(not RUBIX_DB.is_file(), reason="Rubix database not present")
def test_rubix_read_only_connection_rejects_a_write():
    """`mode=ro` + `query_only` must make a write impossible, not merely absent.

    This asserts the guarantee at the SQLite level rather than trusting that no
    caller happens to issue a write.
    """

    uri = f"file:{RUBIX_DB.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only=ON")
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "CREATE TABLE _assistant_should_never_create (x INTEGER)"
            )
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM quotes WHERE 0")
    finally:
        connection.close()


def test_assistant_module_never_opens_a_database_writable():
    """No connection string in the module may omit `mode=ro`."""

    source = Path(assistant.__file__).read_text(encoding="utf-8")
    assert "mode=ro" in source
    assert "PRAGMA query_only=ON" in source
    for forbidden in ("mode=rwc", "mode=rw?", "INSERT INTO", "UPDATE ", "DROP "):
        assert forbidden not in source, f"assistant must not contain {forbidden!r}"


# --- 2. Sector enrichment does not affect qualification -------------------

def test_sector_enrichment_does_not_alter_persisted_qualification(tmp_path, sector_map):
    """Levels must be byte-identical whether or not sector context resolves.

    Guards the boundary that matters most: sector is presentation. If enrichment
    could nudge a trigger or a stop, the research record would be corrupted.
    """

    database = tmp_path / "orb_full_shadow_2026-08-12.db"
    _build_session(database, with_qualification=True)

    with_sector = load_daily_report(database, sector_map_path=sector_map)
    # An empty map makes every ticker UNKNOWN — the strongest available contrast.
    empty_map = tmp_path / "egx_sector_map.csv.empty"
    empty_map.write_text(SECTOR_MAP.splitlines()[0] + "\n", encoding="utf-8")
    sector_module.clear_cache()
    without_sector = load_daily_report(database, sector_map_path=empty_map)

    assert [s.sector_id for s in without_sector.signals] == [UNKNOWN_SECTOR_ID] * 2

    def levels(report):
        return [
            (s.canonical_ticker, s.trigger_price, s.proposed_stop, s.stop_basis,
             s.target_1, s.target_2, s.risk_per_share, s.effective_reward_risk,
             s.target_1_r_multiple, s.target_2_r_multiple, s.usable_target,
             s.atr_value, s.atr_status, s.qualification_status)
            for s in report.signals
        ]

    assert levels(with_sector) == levels(without_sector)


def test_signal_set_is_identical_regardless_of_sector_mapping(tmp_path, sector_map):
    """Enrichment must never add or drop a signal."""

    database = tmp_path / "orb_full_shadow_2026-08-12.db"
    _build_session(database, with_qualification=True)

    mapped = load_daily_report(database, sector_map_path=sector_map)
    sector_module.clear_cache()
    missing = load_daily_report(database, sector_map_path=tmp_path / "absent.csv")

    assert [s.canonical_ticker for s in mapped.signals] == ["MFPC", "JUFO"]
    assert [s.canonical_ticker for s in missing.signals] == ["MFPC", "JUFO"]


# --- 3. Known ticker resolves to the expected sector ---------------------

def test_known_ticker_resolves_to_expected_sector(sector_map):
    context = sector_context("MFPC", sector_map)
    assert context.sector_id == "basic_resources"
    assert context.sector_name == "Basic Resources"
    assert context.sector_rank == 2
    assert context.market_cap_egp == pytest.approx(106_121_189_731)
    assert context.is_known


@pytest.mark.parametrize("spelling", ["MFPC", "mfpc", "MFPC.EGX", "MFPC.CA", "CASE~MFPC"])
def test_accepted_ticker_spellings_resolve_identically(sector_map, spelling):
    """Rubix keys and provider suffixes must not fragment sector lookup."""

    assert sector_context(spelling, sector_map).sector_id == "basic_resources"


# --- 4. Unknown ticker renders safely -----------------------------------

@pytest.mark.parametrize("value", ["ZZZZ", "", None, float("nan"), "NOT_A_TICKER"])
def test_unknown_ticker_degrades_to_unknown_without_raising(sector_map, value):
    context = sector_context(value, sector_map)
    assert context.sector_id == UNKNOWN_SECTOR_ID
    assert context.sector_name == "Unknown"
    assert context.sector_rank is None
    assert context.market_cap_egp is None
    assert not context.is_known


def test_missing_sector_map_file_does_not_raise(tmp_path):
    """An absent map is a display gap, never a pipeline failure."""

    context = sector_context("MFPC", tmp_path / "does_not_exist.csv")
    assert context.sector_id == UNKNOWN_SECTOR_ID


def test_unknown_sector_signal_is_still_listed(tmp_path, sector_map):
    """A signal with no sector must appear, not be filtered out."""

    database = tmp_path / "orb_full_shadow_2026-08-12.db"
    _build_session(database, with_qualification=False, tickers=("MFPC", "NOSECTOR"))
    report = load_daily_report(database, sector_map_path=sector_map)

    tickers = [s.canonical_ticker for s in report.signals]
    assert "NOSECTOR" in tickers
    unknown = next(s for s in report.signals if s.canonical_ticker == "NOSECTOR")
    assert unknown.sector_id == UNKNOWN_SECTOR_ID


# --- 5. Signal rows carry the required fields ---------------------------

def test_signal_row_exposes_every_required_assistant_field(tmp_path, sector_map):
    database = tmp_path / "orb_full_shadow_2026-08-12.db"
    _build_session(database, with_qualification=True)
    report = load_daily_report(database, sector_map_path=sector_map)

    signal = next(s for s in report.signals if s.canonical_ticker == "MFPC")
    assert signal.company_name == "Misr Fertilizers"
    assert signal.sector_name == "Basic Resources"
    assert signal.sector_rank == 2
    assert signal.market_cap_billions == pytest.approx(106.121189731)
    assert signal.signal_state == "ENTRY_READY_RESEARCH"
    assert signal.detection_time_label == "10:40"      # 07:40 UTC -> Cairo
    assert signal.trigger_price == pytest.approx(37.5)
    assert signal.proposed_stop == pytest.approx(36.9)
    assert signal.target_1 == pytest.approx(38.7)
    assert signal.target_2 == pytest.approx(39.3)
    assert signal.risk_per_share == pytest.approx(0.6)
    assert signal.effective_reward_risk == pytest.approx(2.0)
    assert signal.atr_value == pytest.approx(0.31)
    assert signal.qualification_status == "QUALIFIED_RESEARCH"
    assert signal.has_levels


def test_levels_are_absent_not_invented_when_never_persisted(tmp_path, sector_map):
    """A pre-qualification session must report absence, never a derived number."""

    database = tmp_path / "orb_full_shadow_2026-08-12.db"
    _build_session(database, with_qualification=False)
    report = load_daily_report(database, sector_map_path=sector_map)

    assert not report.qualification_available
    assert report.levels_available_count == 0
    for signal in report.signals:
        assert signal.qualification_status == NOT_PERSISTED
        assert signal.trigger_price is None
        assert signal.proposed_stop is None
        assert signal.target_1 is None
        assert signal.target_2 is None
        assert not signal.has_levels


# --- 6. Sector summary reconciles with the signal rows -----------------

def test_sector_summary_reconciles_with_signal_rows(tmp_path, sector_map):
    database = tmp_path / "orb_full_shadow_2026-08-12.db"
    _build_session(database, with_qualification=False,
                   tickers=("MFPC", "EGAL", "JUFO"))
    report = load_daily_report(database, sector_map_path=sector_map)

    assert report.signal_count == 3
    assert sum(t.signal_count for t in report.sector_summary) == report.signal_count
    tallies = {t.sector_name: t.signal_count for t in report.sector_summary}
    assert tallies == {"Basic Resources": 2, "Food and Beverages": 1}
    # Ordered by descending count so the concentrated sector reads first.
    assert report.sector_summary[0].sector_name == "Basic Resources"


# --- 7. Engine/strategy evidence is untouched -------------------------

def test_reader_selects_the_live_lane_only(tmp_path, sector_map):
    """Reconstruction-lane signals must not leak into the live view."""

    database = tmp_path / "orb_full_shadow_2026-08-12.db"
    _build_session(database, with_qualification=False)
    report = load_daily_report(database, sector_map_path=sector_map)

    assert report.run_mode == "FOLLOW"
    assert report.artifact_lane == "LANE_A"
    assert report.run_id == "run-a"
    # EGAL exists only on the RECONSTRUCT run.
    assert "EGAL" not in [s.canonical_ticker for s in report.signals]


def test_reader_excludes_non_signal_states(tmp_path, sector_map):
    database = tmp_path / "orb_full_shadow_2026-08-12.db"
    _build_session(database, with_qualification=False)
    report = load_daily_report(database, sector_map_path=sector_map)

    assert "ZZZZ" not in [s.canonical_ticker for s in report.signals]
    assert all(s.signal_state == "ENTRY_READY_RESEARCH" for s in report.signals)


def test_strategy_modules_are_untouched_by_this_change():
    """The assistant must not have edited engine or strategy code.

    Compares the four protected modules against the branch's base commit.
    """

    import subprocess

    protected = [
        "scalping_orb/engine.py",
        "scalping_orb/states.py",
        "scalping_orb/opening_range.py",
        "scalping_orb/strategy_config.py",
        "scalping_orb/qualification.py",
        "scalping_orb/performance/signal_outcomes.py",
        "scalping_orb/performance/qualified_outcomes.py",
    ]
    result = subprocess.run(
        ["git", "diff", "--name-only", "c3168d9", "--", *protected],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        f"protected strategy modules were modified: {result.stdout}"
    )


# --- no execution vocabulary ------------------------------------------

def test_no_execution_or_fill_vocabulary_is_introduced():
    """This is an assistant: a quote observation must never read as a fill."""

    banned = ("filled_price", "fill_price", "execution_price", "actual_entry",
              "place_order", "submit_order", "order_id")
    for module in (assistant, sector_module):
        source = Path(module.__file__).read_text(encoding="utf-8").lower()
        for term in banned:
            assert term not in source, f"{module.__name__} contains {term!r}"

    page = Path("dashboard/orb_signals.py").read_text(encoding="utf-8").lower()
    for term in banned:
        assert term not in page, f"orb_signals page contains {term!r}"


# --- session-directory resolution is deterministic --------------------

def test_session_directory_precedence_is_explicit_then_env_then_default(
    tmp_path, monkeypatch
):
    explicit = tmp_path / "explicit"
    from_env = tmp_path / "from_env"

    monkeypatch.setenv(assistant.SESSION_DIR_ENV, str(from_env))
    assert assistant.session_directory(explicit) == explicit
    assert assistant.session_directory() == from_env

    monkeypatch.delenv(assistant.SESSION_DIR_ENV, raising=False)
    default = assistant.session_directory()
    assert default.name == "orb_full_shadow"
    assert default.is_absolute()


def test_a_populated_other_directory_is_never_selected_implicitly(
    tmp_path, monkeypatch, sector_map
):
    """The bug this guards: a sibling checkout's data hiding the real one.

    Resolution must depend only on configuration, never on which candidate
    directory happens to contain files.
    """

    populated = tmp_path / "somewhere_else"
    populated.mkdir()
    _build_session(
        populated / "orb_full_shadow_2026-08-12.db", with_qualification=False
    )

    empty = tmp_path / "configured_but_empty"
    empty.mkdir()
    monkeypatch.setenv(assistant.SESSION_DIR_ENV, str(empty))

    directory, sessions = assistant.discover_sessions()
    assert directory == empty
    assert sessions == ()

    # And the populated one is reachable only by naming it.
    directory, sessions = assistant.discover_sessions(populated)
    assert directory == populated
    assert [str(d) for d, _ in sessions] == ["2026-08-12"]


def test_session_date_is_taken_from_the_filename(tmp_path, sector_map):
    database = tmp_path / "orb_full_shadow_2026-08-12.db"
    _build_session(database, with_qualification=False)
    assert load_daily_report(database, sector_map_path=sector_map).session_date == (
        date(2026, 8, 12)
    )
