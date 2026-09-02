"""The batched Rubix overlay read, which a Daily Dashboard scan cannot run without.

It had no tests, and on 2026-08-31 a scan sat in PREPARING_RUBIX at 0/241 for a
quarter of an hour before it was killed. The method was running three
``GROUP BY ticker`` aggregates over a table that has grown to 22 million rows
across 7.3 GB, and only one of the three could use the single index,
``idx_quotes_ticker_time (ticker, market_timestamp)``.

``received_at`` is in no index. Measured on the real database, narrowing it to a
single ticker did not save it: ``MAX(received_at) WHERE ticker=?`` took **8.7
seconds per symbol**, because the index locates the ticker's rows and the column
must then be fetched from every one of them. For 241 symbols that is 35 minutes.

So the receipt time now comes from the newest row by exchange time, which the
index finds instantly. That is a real change of definition and these tests pin
it, along with the properties that must NOT have changed: exact counts, typed
absence, the collector's database never written to, and the whole-table
aggregates staying gone.

After the change the same 241-symbol universe loaded in 7.2 seconds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from providers.rubix_sqlite_provider import RubixSQLiteProvider

BASE = datetime(2026, 8, 31, 8, 30, tzinfo=timezone.utc)

SCHEMA = """
CREATE TABLE quotes (
    id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, last_price REAL,
    bid REAL, ask REAL, volume REAL, market_timestamp TEXT NOT NULL,
    received_at TEXT NOT NULL, exchange TEXT, sequence INTEGER,
    change_percent REAL, has_feed_timestamp INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE candles_1m (
    ticker TEXT NOT NULL, minute TEXT NOT NULL, open REAL NOT NULL,
    high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 0, updates INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (ticker, minute)
);
CREATE TABLE feed_metrics (
    id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
    event TEXT NOT NULL, ticker TEXT, value REAL, detail TEXT
);
CREATE INDEX idx_quotes_ticker_time ON quotes (ticker, market_timestamp);
"""


def _database(path, quotes, candles=()):
    """quotes: (ticker, minutes_offset_market, minutes_offset_received, price)."""
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    for index, (ticker, market_offset, received_offset, price) in enumerate(quotes, 1):
        connection.execute(
            "INSERT INTO quotes (id, ticker, last_price, bid, ask, volume, "
            "market_timestamp, received_at) VALUES (?,?,?,?,?,?,?,?)",
            (index, ticker, price, price - 0.1, price + 0.1, 1000.0 * index,
             (BASE + timedelta(minutes=market_offset)).isoformat(),
             (BASE + timedelta(minutes=received_offset)).isoformat()))
    for ticker, minute_offset in candles:
        connection.execute(
            "INSERT INTO candles_1m (ticker, minute, open, high, low, close) "
            "VALUES (?,?,?,?,?,?)",
            (ticker, (BASE + timedelta(minutes=minute_offset)).isoformat(),
             1.0, 2.0, 0.5, 1.5))
    connection.commit()
    connection.close()
    return path


def _provider(path, now_offset=1):
    return RubixSQLiteProvider(
        db_path=path, now=lambda: BASE + timedelta(minutes=now_offset))


# --- what it returns ---------------------------------------------------------

def test_a_present_symbol_gets_a_composed_overlay(tmp_path):
    path = _database(tmp_path / "r.db",
                     [("COMI", 0, 0, 100.0), ("COMI", 5, 5, 101.0)],
                     [("COMI", 5)])
    overlays = _provider(path, now_offset=6).load_latest_quote_overlays(["COMI.CA"])
    overlay = overlays["COMI.CA"]
    assert overlay["available"] is True
    assert overlay["mapped_symbol"] == "COMI"
    assert overlay["quote_count"] == 2
    assert overlay["minute_bar_count"] == 1


def test_a_symbol_the_collector_never_saw_is_typed_not_raised(tmp_path):
    """One unknown ticker must never fail a 241-name universe."""

    path = _database(tmp_path / "r.db", [("COMI", 0, 0, 100.0)])
    overlays = _provider(path).load_latest_quote_overlays(["COMI.CA", "NOPE.CA"])
    assert overlays["COMI.CA"]["available"] is True
    assert overlays["NOPE.CA"]["available"] is False
    assert overlays["NOPE.CA"]["operational_state"] == RubixSQLiteProvider.UNAVAILABLE


def test_no_symbols_asked_reads_nothing(tmp_path):
    path = _database(tmp_path / "r.db", [("COMI", 0, 0, 100.0)])
    assert _provider(path).load_latest_quote_overlays([]) == {}


def test_the_price_comes_from_the_newest_row_by_exchange_time(tmp_path):
    path = _database(tmp_path / "r.db",
                     [("COMI", 0, 0, 100.0), ("COMI", 9, 9, 109.0),
                      ("COMI", 4, 4, 104.0)])
    overlay = _provider(path, now_offset=10).load_latest_quote_overlays(
        ["COMI.CA"])["COMI.CA"]
    assert overlay["last"] == 109.0


def test_counts_are_exact_not_estimated(tmp_path):
    path = _database(tmp_path / "r.db",
                     [("COMI", i, i, 100.0 + i) for i in range(7)],
                     [("COMI", i) for i in range(3)])
    overlay = _provider(path, now_offset=7).load_latest_quote_overlays(
        ["COMI.CA"])["COMI.CA"]
    assert overlay["quote_count"] == 7
    assert overlay["minute_bar_count"] == 3


def test_one_ticker_does_not_borrow_another_ticker_count(tmp_path):
    path = _database(tmp_path / "r.db",
                     [("COMI", 0, 0, 100.0), ("COMI", 1, 1, 101.0),
                      ("ABUK", 2, 2, 50.0)])
    overlays = _provider(path, now_offset=3).load_latest_quote_overlays(
        ["COMI.CA", "ABUK.CA"])
    assert overlays["COMI.CA"]["quote_count"] == 2
    assert overlays["ABUK.CA"]["quote_count"] == 1


# --- the change of definition, pinned ----------------------------------------

def test_the_receipt_time_is_that_of_the_newest_row_not_the_maximum(tmp_path):
    """The one substantive difference from quote_overlay, and its safe direction.

    Row 2 is newest by exchange time but was received earlier than row 3, which
    arrived out of order. Taking row 2's receipt makes the feed look older than
    a MAX(received_at) would -- never fresher. A quote wrongly called stale
    withholds a recommendation; the opposite error acts on a price that is gone.
    """

    path = _database(tmp_path / "r.db",
                     [("COMI", 0, 0, 100.0),
                      ("COMI", 9, 5, 109.0),      # newest by exchange time
                      ("COMI", 3, 8, 103.0)])     # received latest, older quote
    overlay = _provider(path, now_offset=10).load_latest_quote_overlays(
        ["COMI.CA"])["COMI.CA"]
    received = overlay["received_timestamp"]
    assert received == (BASE + timedelta(minutes=5)).isoformat()
    assert received != (BASE + timedelta(minutes=8)).isoformat()
    assert overlay["age_seconds"] == pytest.approx(300.0)   # not 120.0


def test_the_whole_table_aggregates_are_gone(tmp_path):
    """A behavioural test cannot catch this: the old aggregates returned correct
    answers, they just took twenty-five minutes. The cost is the defect."""

    import inspect

    source = inspect.getsource(RubixSQLiteProvider.load_latest_quote_overlays)
    code = source[source.index('"""', source.index('"""') + 3):]
    assert "MAX(received_at)" not in code
    assert "MAX(received_at), COUNT(*) FROM quotes" not in code

    # The normalization check still runs, but from _known_tickers, which holds
    # its whole-table GROUP BY behind the census TTL. Inline it was paid on
    # every call -- 3.3 seconds, once a day for the scan and every 45 seconds
    # once the portfolio page began refreshing through the same method.
    helper = inspect.getsource(RubixSQLiteProvider._known_tickers)
    assert "GROUP BY ticker" in helper
    assert "_TICKER_CACHE" in helper, "it must not be re-read on every call"


def test_every_quote_read_is_narrowed_to_one_ticker(tmp_path):
    """The index is (ticker, market_timestamp); a read without ticker= cannot use it."""

    from providers import rubix_sqlite_provider as module

    for sql in (module._LATEST_QUOTE, module._QUOTE_COUNT, module._MINUTE_SUMMARY):
        assert "WHERE ticker=?" in sql


# --- what must not have changed ----------------------------------------------

def test_the_collectors_database_is_never_written(tmp_path):
    path = _database(tmp_path / "r.db",
                     [("COMI", 0, 0, 100.0), ("ABUK", 1, 1, 50.0)],
                     [("COMI", 0)])
    before = sqlite3.connect(path).execute(
        "SELECT COUNT(*) FROM quotes").fetchone()[0]
    schema_before = sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master ORDER BY name").fetchall()

    _provider(path, now_offset=2).load_latest_quote_overlays(["COMI.CA", "ABUK.CA"])

    assert sqlite3.connect(path).execute(
        "SELECT COUNT(*) FROM quotes").fetchone()[0] == before
    assert sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master ORDER BY name").fetchall() == schema_before


def test_a_lowercase_ticker_in_the_collector_is_reported(tmp_path):
    """The check reads every ticker written, not the ones asked for: those were
    upper-cased on the way in, so checking them would check our own work."""

    path = _database(tmp_path / "r.db",
                     [("COMI", 0, 0, 100.0), ("comi_bad", 0, 0, 1.0)])
    overlay = _provider(path, now_offset=1).load_latest_quote_overlays(
        ["COMI.CA"])["COMI.CA"]
    assert RubixSQLiteProvider.QUOTE_TICKER_NOT_NORMALIZED in overlay.get(
        "data_quality_warning", "")


def test_a_clean_collector_raises_no_warning(tmp_path):
    path = _database(tmp_path / "r.db", [("COMI", 0, 0, 100.0)])
    overlay = _provider(path, now_offset=1).load_latest_quote_overlays(
        ["COMI.CA"])["COMI.CA"]
    assert "data_quality_warning" not in overlay


def test_the_ticker_list_is_cached_between_calls(tmp_path):
    """The portfolio page refreshes every 45 seconds through this method. A
    whole-table GROUP BY per call is a permanent scan of a growing table.

    Proved by what the cache hides: a ticker written after the first call is
    not seen by the second, because the second never re-reads.
    """

    from providers import rubix_sqlite_provider as module

    module.reset_census_cache()
    path = _database(tmp_path / "r.db", [("COMI", 0, 0, 100.0)])
    provider = _provider(path, now_offset=2)

    connection = sqlite3.connect(path)
    try:
        first = provider._known_tickers(connection)
        connection.execute(
            "INSERT INTO quotes (id, ticker, last_price, bid, ask, volume, "
            "market_timestamp, received_at) VALUES (99,'NEWT',1,1,1,1,?,?)",
            (BASE.isoformat(), BASE.isoformat()))
        connection.commit()
        second = provider._known_tickers(connection)
    finally:
        connection.close()

    assert "NEWT" not in first
    assert second == first, "the second call must not have re-read the table"


def test_a_zero_ttl_still_re_reads(tmp_path):
    """Tests and forced refreshes must be able to bypass the cache."""

    from providers import rubix_sqlite_provider as module

    module.reset_census_cache()
    path = _database(tmp_path / "r.db", [("COMI", 0, 0, 100.0)])
    provider = RubixSQLiteProvider(
        db_path=path, census_ttl_seconds=0,
        now=lambda: BASE + timedelta(minutes=2))
    connection = sqlite3.connect(path)
    try:
        first = provider._known_tickers(connection)
        connection.execute(
            "INSERT INTO quotes (id, ticker, last_price, bid, ask, volume, "
            "market_timestamp, received_at) VALUES (99,'NEWT',1,1,1,1,?,?)",
            (BASE.isoformat(), BASE.isoformat()))
        connection.commit()
        second = provider._known_tickers(connection)
    finally:
        connection.close()
    assert "NEWT" not in first and "NEWT" in second
