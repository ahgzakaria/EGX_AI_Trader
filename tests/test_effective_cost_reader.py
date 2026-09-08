"""The cost reader, and the two pages that now show a signal beside its cost.

No test reads the production bank. Every bank here is a temporary file the test
built, so a machine that has never run the banker still runs these.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from core.effective_cost import (
    FEES_PERCENT,
    MINIMUM_SESSIONS,
    SymbolCost,
    load_symbol_costs,
    round_trip_for,
)
from scripts.bank_effective_cost import SCHEMA


def build_bank(path: Path, rows) -> Path:
    """rows: (symbol, session_date, effective_cost_percent | None)."""

    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO effective_cost VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(symbol, session, 100, 10, cost,
          None if cost is None else FEES_PERCENT + cost,
          0.0, 0.0, 1000.0, 100.0, 10.0, "x.db", "2026-09-08T00:00:00+00:00")
         for symbol, session, cost in rows],
    )
    connection.commit()
    connection.close()
    return path


def sessions_for(symbol, cost, count=MINIMUM_SESSIONS):
    return [(symbol, f"2026-09-{day + 1:02d}", cost) for day in range(count)]


# --- reading -----------------------------------------------------------------

def test_a_symbol_median_is_the_median_of_its_sessions(tmp_path):
    bank = build_bank(tmp_path / "c.db", [
        ("COMI", "2026-09-01", 0.10),
        ("COMI", "2026-09-02", 0.20),
        ("COMI", "2026-09-03", 0.60),
    ])
    costs = load_symbol_costs(path=bank)
    assert costs["COMI"].effective_cost_percent == pytest.approx(0.20)
    assert costs["COMI"].sessions_measured == 3


def test_the_round_trip_adds_the_fees_to_the_measured_crossing(tmp_path):
    bank = build_bank(tmp_path / "c.db", sessions_for("ABUK", 0.25))
    assert load_symbol_costs(path=bank)["ABUK"].round_trip_percent == pytest.approx(
        FEES_PERCENT + 0.25)


def test_a_symbol_measured_too_few_times_is_absent_not_averaged(tmp_path):
    """The one name nobody measured is where a wrong cost does the most harm."""

    bank = build_bank(tmp_path / "c.db",
                      sessions_for("THIN", 0.30, MINIMUM_SESSIONS - 1))
    assert "THIN" not in load_symbol_costs(path=bank)


def test_a_session_whose_cost_was_withheld_does_not_count_toward_the_minimum(tmp_path):
    rows = sessions_for("OCDI", 0.30, MINIMUM_SESSIONS - 1)
    rows.append(("OCDI", "2026-09-09", None))
    assert "OCDI" not in load_symbol_costs(path=build_bank(tmp_path / "c.db", rows))


def test_only_the_recent_window_is_taken(tmp_path):
    """A name whose liquidity changed stops being described by last quarter."""

    old = [("HRHO", f"2026-01-{day + 1:02d}", 5.0) for day in range(20)]
    recent = [("HRHO", f"2026-09-{day + 1:02d}", 0.10) for day in range(5)]
    costs = load_symbol_costs(path=build_bank(tmp_path / "c.db", old + recent),
                              recent_sessions=5)
    assert costs["HRHO"].effective_cost_percent == pytest.approx(0.10)
    assert costs["HRHO"].last_session == "2026-09-05"


def test_asking_for_some_symbols_returns_only_those(tmp_path):
    bank = build_bank(tmp_path / "c.db",
                      sessions_for("COMI", 0.1) + sessions_for("FIRE", 1.6))
    assert set(load_symbol_costs(["COMI"], path=bank)) == {"COMI"}


@pytest.mark.parametrize("spelling", ["comi", " COMI ", "COMI"])
def test_a_symbol_is_matched_however_the_caller_spells_it(tmp_path, spelling):
    bank = build_bank(tmp_path / "c.db", sessions_for("COMI", 0.1))
    assert load_symbol_costs([spelling], path=bank)
    assert round_trip_for(load_symbol_costs(path=bank), spelling) is not None


# --- absence is not zero -----------------------------------------------------

def test_a_bank_that_was_never_built_reads_as_empty_not_as_an_error(tmp_path):
    assert load_symbol_costs(path=tmp_path / "never_ran.db") == {}


def test_a_corrupt_bank_reads_as_empty_rather_than_taking_the_page_down(tmp_path):
    broken = tmp_path / "c.db"
    broken.write_bytes(b"not a database")
    assert load_symbol_costs(path=broken) == {}


def test_an_unmeasured_symbol_has_no_round_trip_rather_than_a_cheap_one(tmp_path):
    costs = load_symbol_costs(path=build_bank(tmp_path / "c.db",
                                              sessions_for("COMI", 0.1)))
    assert round_trip_for(costs, "UNKNOWN") is None


# --- what the number is for --------------------------------------------------

def test_covers_compares_a_move_against_this_name_own_round_trip():
    cheap = SymbolCost("COMI", 0.030, 14, "2026-09-08")
    dear = SymbolCost("FIRE", 1.624, 14, "2026-09-08")
    # Swing Breakout's median trade.
    assert cheap.covers(0.88) is True
    assert dear.covers(0.88) is False


def test_a_move_exactly_equal_to_the_cost_does_not_cover_it():
    record = SymbolCost("X", 0.5, 14, "2026-09-08")
    assert record.covers(record.round_trip_percent) is False


# --- the pages ---------------------------------------------------------------

def test_swing_breakout_shows_the_cost_beside_the_signal():
    source = Path("dashboard/swing_signals.py").read_text(encoding="utf-8")
    assert "from core.effective_cost import" in source
    assert '"Round trip %": round_trip_for(costs, c.symbol)' in source
    assert '"Round trip %": st.column_config.NumberColumn' in source


def test_confirmed_breakout_shows_the_cost_beside_the_signal():
    source = Path("dashboard/confirmed_breakout.py").read_text(encoding="utf-8")
    assert "from core.effective_cost import" in source
    assert '"Round trip %": round_trip_for(costs, signal.symbol)' in source
    assert '"Round trip %": st.column_config.NumberColumn' in source


def test_neither_page_drops_a_signal_for_being_expensive():
    """These are read-only research views. The cost is shown, never applied.

    Filtering here would be a decision taken in the presentation layer, which
    is the one thing both pages say in their own docstrings they never do.
    """

    for name in ("dashboard/swing_signals.py", "dashboard/confirmed_breakout.py"):
        source = Path(name).read_text(encoding="utf-8")
        for forbidden in ("covers(", "round_trip_percent >", "if cost >"):
            assert forbidden not in source, f"{name} appears to filter on cost"


def test_the_candidate_frame_carries_the_column_even_with_no_bank():
    """A machine that has never run the banker renders the page as before."""

    from dashboard.swing_signals import _candidate_frame

    class Candidate:
        symbol = "COMI"; close = 10.0; breakout_level = 9.0
        extension_percent = 1.0; volume_ratio = 2.0; momentum_12_1 = 5.0
        momentum_rank = 0.9; atr_percent = 1.5

    class Result:
        candidates = [Candidate()]

    frame = _candidate_frame(Result(), {})
    assert "Round trip %" in frame.columns
    assert frame["Round trip %"].isna().all()
