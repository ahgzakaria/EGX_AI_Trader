"""Daily-flow banker — what it records, and what it refuses to call a split.

No test reads the real MubasherTrade store or the production bank. Every
history database here is a temporary file the test built itself.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from scripts.bank_daily_flow import (
    as_session,
    bank,
    history_database,
    measure_row,
    open_bank,
)

BANKER_SOURCE = Path("scripts/bank_daily_flow.py")

COLUMNS = ("DATE, CLS, VOL, TOVR, NOTR, VWAP, CIT, CIV, CITR, COT, COV, COTR")


def build_history(path: Path, rows_by_symbol) -> Path:
    """rows_by_symbol: {SYMBOL: [(DATE, CLS, VOL, TOVR, NOTR, VWAP,
                                  CIT, CIV, CITR, COT, COV, COTR)]}"""

    connection = sqlite3.connect(path)
    for symbol, rows in rows_by_symbol.items():
        connection.execute(
            f"CREATE TABLE [_{symbol}] (INS TEXT, DATE TEXT, OP TEXT, HIG TEXT, "
            f"LOW TEXT, CLS TEXT, VOL TEXT, TOVR TEXT, NOTR TEXT, VWAP TEXT, "
            f"CIT TEXT, CIV TEXT, CITR TEXT, COT TEXT, COV TEXT, COTR TEXT)"
        )
        connection.executemany(
            f"INSERT INTO [_{symbol}] ({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [tuple(str(v) for v in row) for row in rows],
        )
    connection.commit()
    connection.close()
    return path


def modern_row(day="20260907", close=100.0, volume=1000.0, turnover=100_000.0,
               trades=50, vwap=100.0, buy=60_000.0, sell=40_000.0,
               buy_vol=600.0, sell_vol=400.0, buy_trades=30, sell_trades=20):
    return (day, close, volume, turnover, trades, vwap,
            buy, buy_vol, buy_trades, sell, sell_vol, sell_trades)


def measured(row):
    return measure_row(row[1], row[2], row[3], row[4], row[5],
                       row[6], row[9], row[7], row[10], row[8], row[11])


# --- dates -------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("20260907", "2026-09-07"),
    ("20031103", "2003-11-03"),
    ("2026-09-07", None),
    ("", None),
    ("2026090", None),
    ("abcdefgh", None),
])
def test_a_session_date_is_parsed_or_refused(raw, expected):
    assert as_session(raw) == expected


# --- the split ---------------------------------------------------------------

def test_a_two_sided_day_records_its_buy_share():
    row = measured(modern_row(buy=60_000.0, sell=40_000.0))
    assert row["flow_measured"] == 1
    assert row["buy_share"] == pytest.approx(0.60)


@pytest.mark.parametrize("buy, sell", [(0.0, 100_000.0), (100_000.0, 0.0), (0.0, 0.0)])
def test_a_one_sided_day_is_unmeasured_not_a_day_of_pure_selling(buy, sell):
    """The pre-2009 placeholder. Reading it as flow builds a bucket of nothing.

    Before mid-2009 every row puts the whole turnover on one side; coverage is
    0% through 2008 and 78-95% from 2010. A study that treated those as
    sell-side days produced a quintile made entirely of missing measurement.
    """

    row = measured(modern_row(buy=buy, sell=sell, turnover=100_000.0))
    assert row["flow_measured"] == 0
    assert row["buy_share"] is None
    assert row["buy_value"] is None and row["sell_value"] is None


def test_a_negative_side_is_not_a_split():
    assert measured(modern_row(buy=-1.0, sell=100_000.0))["flow_measured"] == 0


def test_the_bar_survives_even_when_the_split_does_not():
    """Volume and turnover are facts on those rows; only the split is absent."""

    row = measured(modern_row(buy=0.0, sell=100_000.0))
    assert row["turnover"] == pytest.approx(100_000.0)
    assert row["volume"] == pytest.approx(1000.0)
    assert row["close"] == pytest.approx(100.0)


# --- the identity ------------------------------------------------------------

def test_a_split_that_reproduces_the_total_is_marked_consistent():
    assert measured(modern_row(buy=60_000.0, sell=40_000.0,
                               turnover=100_000.0))["identity_holds"] == 1


def test_a_split_that_does_not_reproduce_the_total_is_flagged():
    assert measured(modern_row(buy=60_000.0, sell=40_000.0,
                               turnover=250_000.0))["identity_holds"] == 0


def test_an_unmeasured_row_makes_no_claim_about_the_identity():
    assert measured(modern_row(buy=0.0, sell=100_000.0))["identity_holds"] is None


# --- the other three columns -------------------------------------------------

def test_average_trade_size_is_turnover_over_trades():
    row = measured(modern_row(turnover=100_000.0, trades=50))
    assert row["average_trade"] == pytest.approx(2000.0)


def test_a_session_with_no_trade_count_has_no_average():
    assert measured(modern_row(trades=0))["average_trade"] is None
    assert measured(modern_row(trades=-1))["trades"] is None


def test_an_unquoted_vwap_is_absent_rather_than_zero():
    assert measured(modern_row(vwap=0.0))["vwap"] is None
    assert measured(modern_row(vwap=-1.0))["vwap"] is None


def test_the_two_sides_of_volume_and_trade_count_are_kept():
    row = measured(modern_row(buy_vol=600.0, sell_vol=400.0,
                              buy_trades=30, sell_trades=20))
    assert (row["buy_volume"], row["sell_volume"]) == (600.0, 400.0)
    assert (row["buy_trades"], row["sell_trades"]) == (30, 20)


# --- rows that cannot be a bar -----------------------------------------------

@pytest.mark.parametrize("close, volume, turnover", [
    (0.0, 1000.0, 100.0), (-1.0, 1000.0, 100.0),
    ("", 1000.0, 100.0), (100.0, "", 100.0), (100.0, 1000.0, ""),
    ("abc", 1000.0, 100.0), (100.0, -5.0, 100.0), (100.0, 1000.0, -5.0),
])
def test_an_unusable_row_is_dropped_rather_than_banked_as_zero(close, volume, turnover):
    assert measure_row(close, volume, turnover, 10, 100.0,
                       60.0, 40.0, 6.0, 4.0, 6, 4) is None


# --- banking -----------------------------------------------------------------

def test_symbols_are_banked_under_their_canonical_ticker(tmp_path):
    history = build_history(tmp_path / "h.db", {"GRCA": [modern_row()]})
    bank_db = open_bank(tmp_path / "bank.db")
    bank(history, bank_db)
    assert bank_db.execute(
        "SELECT canonical_symbol FROM daily_flow").fetchone()[0] == "GRCA"


def test_banking_twice_replaces_rather_than_doubles(tmp_path):
    history = build_history(tmp_path / "h.db", {"COMI": [modern_row()]})
    bank_db = open_bank(tmp_path / "bank.db")
    bank(history, bank_db)
    bank(history, bank_db)
    assert bank_db.execute("SELECT COUNT(*) FROM daily_flow").fetchone()[0] == 1


def test_since_limits_what_is_read(tmp_path):
    history = build_history(tmp_path / "h.db", {"COMI": [
        modern_row(day="20200101"), modern_row(day="20260907")]})
    bank_db = open_bank(tmp_path / "bank.db")
    bank(history, bank_db, since="2026-01-01")
    rows = [r[0] for r in bank_db.execute("SELECT session_date FROM daily_flow")]
    assert rows == ["2026-09-07"]


def test_the_counts_separate_banked_rows_from_measured_ones(tmp_path):
    history = build_history(tmp_path / "h.db", {"COMI": [
        modern_row(day="20050101", buy=0.0, sell=100_000.0),
        modern_row(day="20260907"),
    ]})
    counts = bank(history, open_bank(tmp_path / "bank.db"))
    assert counts["rows"] == 2
    assert counts["with_flow"] == 1


def test_several_symbols_land_in_one_table(tmp_path):
    history = build_history(tmp_path / "h.db", {
        "COMI": [modern_row()], "ABUK": [modern_row()], "FIRE": [modern_row()]})
    bank_db = open_bank(tmp_path / "bank.db")
    counts = bank(history, bank_db)
    assert counts["symbols"] == 3
    assert bank_db.execute("SELECT COUNT(*) FROM daily_flow").fetchone()[0] == 3


# --- boundaries --------------------------------------------------------------

def test_the_store_is_opened_read_only(tmp_path):
    history = build_history(tmp_path / "h.db", {"COMI": [modern_row()]})
    before = history.read_bytes()
    bank(history, open_bank(tmp_path / "bank.db"))
    assert history.read_bytes() == before

    source = BANKER_SOURCE.read_text(encoding="utf-8")
    assert "mode=ro" in source
    assert "query_only=ON" in source


def test_the_banker_never_drives_the_terminal():
    source = BANKER_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("subprocess", "Popen", "requests", "urllib", "selenium",
                      "pyautogui", "win32", "SendKeys"):
        assert forbidden not in source, f"the banker must not reference {forbidden}"


def test_a_missing_store_says_so_rather_than_banking_nothing(tmp_path):
    with pytest.raises(SystemExit):
        history_database(str(tmp_path / "absent.db"))
