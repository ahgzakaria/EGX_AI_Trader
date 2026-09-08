"""Effective-cost banker — the measure, the gates, and what it refuses to say.

No test reads the real Time & Sales archive, the production bank, or any market
database. Every archive here is a temporary SQLite file the test wrote itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.bank_effective_cost import (
    BUCKET_SECONDS,
    FEES_PERCENT,
    MINIMUM_BUCKETS,
    archive_directory,
    bank_session,
    bucket_of,
    measure_session,
    open_bank,
    session_of,
)

BANKER_SOURCE = Path("scripts/bank_effective_cost.py")


def build_archive(path: Path, trades) -> Path:
    """trades: (symbol, HHMMSS, price, quantity, side) with side "1" = buy."""

    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE TRADES (SEQUENCE INTEGER, SYMBOL TEXT, TRADETIME TEXT, "
        "TRADEPRICE TEXT, TRADEQUANTITY TEXT, TRANSACTIONTYPE TEXT)"
    )
    connection.executemany(
        "INSERT INTO TRADES (SEQUENCE, SYMBOL, TRADETIME, TRADEPRICE, "
        "TRADEQUANTITY, TRANSACTIONTYPE) VALUES (?,?,?,?,?,?)",
        [(index, *trade) for index, trade in enumerate(trades)],
    )
    connection.commit()
    connection.close()
    return path


def two_sided(symbol, buckets, buy_price, sell_price, size=100):
    """One buy and one sell in each of `buckets` distinct five-minute windows."""

    trades = []
    for index in range(buckets):
        seconds = 25200 + index * BUCKET_SECONDS
        stamp = f"{seconds // 3600:02d}{seconds % 3600 // 60:02d}{seconds % 60:02d}"
        trades.append((symbol, stamp, str(buy_price), str(size), "1"))
        trades.append((symbol, stamp, str(sell_price), str(size), "0"))
    return trades


# --- filenames and buckets ---------------------------------------------------

@pytest.mark.parametrize("stem, expected", [
    ("20260908", "2026-09-08"),
    ("20260819", "2026-08-19"),
    ("notadate", ""),
    ("2026090", ""),
])
def test_session_is_read_from_the_filename(tmp_path, stem, expected):
    assert session_of(tmp_path / f"{stem}.db") == expected


def test_buckets_are_five_minutes_of_the_trading_day():
    assert bucket_of("100000") == bucket_of("100459")
    assert bucket_of("100000") != bucket_of("100500")


@pytest.mark.parametrize("stamp", ["", "10000", "1000000", "10:00:00", "abcdef"])
def test_a_malformed_timestamp_is_refused_rather_than_guessed(stamp):
    assert bucket_of(stamp) is None


# --- the measure -------------------------------------------------------------

def test_cost_is_the_gap_between_what_buyers_paid_and_sellers_received(tmp_path):
    archive = build_archive(tmp_path / "20260908.db",
                            two_sided("COMI", 10, 100.5, 99.5))
    row = measure_session(archive)["COMI"]
    # 1.0 on a mid of 100.0 is exactly 1%.
    assert row["effective_cost_percent"] == pytest.approx(1.0)
    assert row["round_trip_percent"] == pytest.approx(FEES_PERCENT + 1.0)


def test_a_symbol_trading_at_one_price_costs_nothing_to_cross(tmp_path):
    archive = build_archive(tmp_path / "20260908.db",
                            two_sided("ABUK", 10, 50.0, 50.0))
    assert measure_session(archive)["ABUK"]["effective_cost_percent"] == 0.0


def test_a_trend_inside_the_session_is_not_counted_as_cost(tmp_path):
    """The reason the measure is bucketed at all.

    Buyers and sellers trade at the same price in every window, but the price
    climbs 20% across the session. A day-wide comparison of buyer VWAP against
    seller VWAP would report a cost here; there is none.
    """

    trades = []
    for index in range(10):
        seconds = 25200 + index * BUCKET_SECONDS
        stamp = f"{seconds // 3600:02d}{seconds % 3600 // 60:02d}{seconds % 60:02d}"
        price = 100.0 + index * 2
        # Sellers early in the window, buyers late, so a day-wide average puts
        # buyers at higher prices purely because the stock rose.
        trades.append(("HRHO", stamp, str(price), "100", "0"))
        trades.append(("HRHO", stamp, str(price), "100", "1"))
    archive = build_archive(tmp_path / "20260908.db", trades)
    assert measure_session(archive)["HRHO"]["effective_cost_percent"] == pytest.approx(0.0)


def test_a_one_sided_bucket_is_skipped_not_scored(tmp_path):
    # Buys only in the extra window: there is no other side to measure against.
    trades = two_sided("SWDY", MINIMUM_BUCKETS, 10.1, 9.9)
    trades.append(("SWDY", "120000", "12.0", "100", "1"))
    archive = build_archive(tmp_path / "20260908.db", trades)
    row = measure_session(archive)["SWDY"]
    assert row["buckets_measured"] == MINIMUM_BUCKETS
    assert row["effective_cost_percent"] == pytest.approx(2.0)


def test_too_few_windows_leaves_the_cost_unstated(tmp_path):
    archive = build_archive(tmp_path / "20260908.db",
                            two_sided("OCDI", MINIMUM_BUCKETS - 1, 10.1, 9.9))
    row = measure_session(archive)["OCDI"]
    assert row["effective_cost_percent"] is None
    assert row["round_trip_percent"] is None


def test_a_thin_symbol_still_banks_its_volume(tmp_path):
    """The cost is withheld; the traded value is not a guess and stays."""

    archive = build_archive(tmp_path / "20260908.db",
                            two_sided("OCDI", 2, 10.0, 10.0, size=500))
    row = measure_session(archive)["OCDI"]
    assert row["effective_cost_percent"] is None
    assert row["trades"] == 4
    assert row["traded_value"] == pytest.approx(4 * 10.0 * 500)


def test_the_biggest_print_is_reported_as_a_share_of_the_day(tmp_path):
    trades = two_sided("EFIC", 10, 10.0, 10.0, size=100)      # 20,000 in total
    trades.append(("EFIC", "120000", "10.0", "8000", "1"))    # 80,000 in one
    archive = build_archive(tmp_path / "20260908.db", trades)
    row = measure_session(archive)["EFIC"]
    assert row["largest_print_value"] == pytest.approx(80_000)
    assert row["largest_print_share"] == pytest.approx(80.0)


def test_the_two_sides_of_the_tape_are_kept_apart(tmp_path):
    archive = build_archive(tmp_path / "20260908.db",
                            two_sided("TMGH", 10, 10.0, 10.0, size=100))
    row = measure_session(archive)["TMGH"]
    assert row["buy_initiated_value"] == pytest.approx(10_000)
    assert row["sell_initiated_value"] == pytest.approx(10_000)


# --- input the archive can actually contain ----------------------------------

@pytest.mark.parametrize("spelling", ["GRCA", "GRCA.CA", "CASE~GRCA", "grca"])
def test_every_spelling_the_export_uses_lands_on_one_ticker(tmp_path, spelling):
    archive = build_archive(tmp_path / "20260908.db",
                            two_sided(spelling, 10, 10.1, 9.9))
    assert "GRCA" in measure_session(archive)


@pytest.mark.parametrize("price, size", [
    ("0", "100"), ("-5", "100"), ("10", "0"), ("10", "-1"),
    ("", "100"), ("10", ""), ("abc", "100"), ("10", "abc"),
])
def test_an_unusable_print_is_dropped_not_counted_as_zero(tmp_path, price, size):
    trades = two_sided("ETEL", 10, 10.0, 10.0)
    trades.append(("ETEL", "120000", price, size, "1"))
    archive = build_archive(tmp_path / "20260908.db", trades)
    assert measure_session(archive)["ETEL"]["trades"] == 20


def test_an_empty_session_measures_nothing_rather_than_failing(tmp_path):
    assert measure_session(build_archive(tmp_path / "20260908.db", [])) == {}


# --- banking -----------------------------------------------------------------

def test_banking_the_same_session_twice_replaces_rather_than_doubles(tmp_path):
    archive = build_archive(tmp_path / "20260908.db",
                            two_sided("COMI", 10, 100.5, 99.5))
    bank = open_bank(tmp_path / "bank.db")
    bank_session(bank, "2026-09-08", archive)
    bank_session(bank, "2026-09-08", archive)
    assert bank.execute("SELECT COUNT(*) FROM effective_cost").fetchone()[0] == 1


def test_a_banked_row_carries_the_file_it_came_from(tmp_path):
    archive = build_archive(tmp_path / "20260908.db",
                            two_sided("COMI", 10, 100.5, 99.5))
    bank = open_bank(tmp_path / "bank.db")
    bank_session(bank, "2026-09-08", archive)
    source, banked = bank.execute(
        "SELECT source_file, banked_at FROM effective_cost").fetchone()
    assert source == "20260908.db"
    assert banked


def test_two_sessions_of_one_symbol_are_two_rows(tmp_path):
    bank = open_bank(tmp_path / "bank.db")
    for day in ("20260907", "20260908"):
        archive = build_archive(tmp_path / f"{day}.db",
                                two_sided("COMI", 10, 100.5, 99.5))
        bank_session(bank, session_of(archive), archive)
    assert bank.execute("SELECT COUNT(*) FROM effective_cost").fetchone()[0] == 2


# --- boundaries this script must not cross -----------------------------------

def test_the_archive_is_opened_read_only(tmp_path):
    archive = build_archive(tmp_path / "20260908.db",
                            two_sided("COMI", 10, 100.5, 99.5))
    before = archive.read_bytes()
    measure_session(archive)
    assert archive.read_bytes() == before

    source = BANKER_SOURCE.read_text(encoding="utf-8")
    assert "mode=ro" in source
    assert "query_only=ON" in source


def test_the_banker_never_drives_the_broker_terminal():
    """It reads files a human exported. It must not automate the export itself."""

    source = BANKER_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("subprocess", "Popen", "requests", "urllib", "selenium",
                      "pyautogui", "win32", "SendKeys"):
        assert forbidden not in source, f"the banker must not reference {forbidden}"


def test_a_missing_archive_says_so_instead_of_banking_nothing(tmp_path):
    with pytest.raises(SystemExit):
        archive_directory(str(tmp_path / "absent"))
