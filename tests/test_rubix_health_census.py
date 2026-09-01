"""health() must stay honest while it stops scanning 23 million rows a heartbeat.

The collector supervisor calls health() every two seconds to write a telemetry
file, and health() was reading the whole quotes table four times over. Measured
on 2026-09-01 the supervisor had burned 8,512 CPU-seconds in 369 minutes -- 38%
of a core, sustained, against the database the collector was writing to -- and
never once reached its sleep.

No query could fix that. Every exact route still has to walk the index for all
23.5 million rows, and the best measured 59 seconds against the original 115.
So the expensive half is read at most once a minute and reused.

What these tests protect is the line between what may age and what may not:

* the census -- last-seen per ticker, the row counts -- may be up to
  CENSUS_TTL_SECONDS old;
* the tip -- is the feed alive right now -- may not, and is read every call;
* the verdict -- updating / stale / missing -- may not, and is recomputed
  against the current clock from the cached observation, so a symbol still goes
  stale on time.

They also pin MAX(id) over MAX(received_at). received_at is TEXT and unindexed,
so SQLite compares it lexically: on the live database its "maximum" disagreed
with the newest actual arrival for 4 of 265 tickers, once by 12.4 hours.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from providers import rubix_sqlite_provider as module
from providers.rubix_sqlite_provider import RubixSQLiteProvider, reset_census_cache

BASE = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)

SCHEMA = """
CREATE TABLE quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, last_price REAL,
    bid REAL, ask REAL, volume REAL, market_timestamp TEXT NOT NULL,
    received_at TEXT NOT NULL
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


@pytest.fixture(autouse=True)
def clean_cache():
    reset_census_cache()
    yield
    reset_census_cache()


def _append(path, ticker, minutes, price=100.0):
    stamp = (BASE + timedelta(minutes=minutes)).isoformat()
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO quotes (ticker, last_price, bid, ask, volume, "
        "market_timestamp, received_at) VALUES (?,?,?,?,?,?,?)",
        (ticker, price, price - 0.1, price + 0.1, 100.0, stamp, stamp))
    connection.commit()
    connection.close()


def _database(path, rows=(("COMI", 0), ("ABUK", 0))):
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.commit()
    connection.close()
    for ticker, minutes in rows:
        _append(path, ticker, minutes)
    return path


def _provider(path, at_minutes=1, ttl=None, symbols=("COMI", "ABUK")):
    return RubixSQLiteProvider(
        db_path=path, expected_symbols=symbols,
        census_ttl_seconds=ttl,
        now=lambda: BASE + timedelta(minutes=at_minutes))


# --- what may age ------------------------------------------------------------

def test_the_census_is_read_once_within_the_window(tmp_path, monkeypatch):
    path = _database(tmp_path / "r.db")
    provider = _provider(path)
    reads = []
    original = RubixSQLiteProvider._read_census
    monkeypatch.setattr(RubixSQLiteProvider, "_read_census",
                        lambda self, c: (reads.append(1), original(self, c))[1])
    for _ in range(6):
        provider.health()
    assert len(reads) == 1, "six heartbeats must not be six table scans"


def test_the_census_is_re_read_once_the_window_passes(tmp_path, monkeypatch):
    path = _database(tmp_path / "r.db")
    provider = _provider(path, ttl=60.0)
    reads = []
    original = RubixSQLiteProvider._read_census
    monkeypatch.setattr(RubixSQLiteProvider, "_read_census",
                        lambda self, c: (reads.append(1), original(self, c))[1])

    clock = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    provider.health()
    clock[0] += 59.0
    provider.health()
    assert len(reads) == 1
    clock[0] += 2.0
    provider.health()
    assert len(reads) == 2


def test_a_zero_window_always_re_reads(tmp_path, monkeypatch):
    path = _database(tmp_path / "r.db")
    provider = _provider(path, ttl=0)
    reads = []
    original = RubixSQLiteProvider._read_census
    monkeypatch.setattr(RubixSQLiteProvider, "_read_census",
                        lambda self, c: (reads.append(1), original(self, c))[1])
    provider.health()
    provider.health()
    assert len(reads) == 2


def test_two_databases_do_not_share_a_census(tmp_path):
    first = _database(tmp_path / "a.db", [("COMI", 0)])
    second = _database(tmp_path / "b.db", [("COMI", 0), ("ABUK", 0)])
    assert _provider(first, symbols=("COMI",)).health()["symbol_count"] == 1
    assert _provider(second).health()["symbol_count"] == 2


def test_reset_drops_the_cache(tmp_path, monkeypatch):
    path = _database(tmp_path / "r.db")
    provider = _provider(path)
    reads = []
    original = RubixSQLiteProvider._read_census
    monkeypatch.setattr(RubixSQLiteProvider, "_read_census",
                        lambda self, c: (reads.append(1), original(self, c))[1])
    provider.health()
    reset_census_cache()
    provider.health()
    assert len(reads) == 2


# --- what may NOT age --------------------------------------------------------

def test_the_tip_is_read_every_call(tmp_path):
    """"Is the feed alive right now" is one row through the primary key, and it
    is never allowed to be a minute old."""

    path = _database(tmp_path / "r.db")
    provider = _provider(path, at_minutes=30)
    first = provider.health()["latest_received_timestamp"]

    _append(path, "COMI", 20)          # a newer arrival, inside the TTL window
    second = provider.health()["latest_received_timestamp"]

    assert first == (BASE + timedelta(minutes=0)).isoformat()
    assert second == (BASE + timedelta(minutes=20)).isoformat()


def test_a_symbol_goes_stale_on_time_from_a_cached_observation(tmp_path):
    """The observation may be a minute old; the verdict drawn from it may not.

    Nothing is re-read between these two calls -- the census is cached and the
    database is untouched. Only the clock moves, and the classification must
    move with it.

    Both readings are taken inside the open session, where the rule is elapsed
    time against ``stale_after_minutes``. After the close the rule deliberately
    becomes "same session, therefore still current", which would hide exactly
    the movement this is testing for.
    """

    path = _database(tmp_path / "r.db", [("COMI", 0), ("ABUK", 0)])
    fresh = _provider(path, at_minutes=1).health()
    assert fresh["symbols_updating"] == 2
    assert fresh["symbols_stale"] == 0

    # +60 minutes, still mid-session, against a 5-minute staleness threshold.
    later = _provider(path, at_minutes=60).health()
    assert later["symbols_updating"] == 0
    assert later["symbols_stale"] == 2


def test_a_symbol_never_seen_is_missing_not_stale(tmp_path):
    path = _database(tmp_path / "r.db", [("COMI", 0)])
    health = _provider(path, symbols=("COMI", "NOPE")).health()
    assert health["missing_symbols"] == ["NOPE"]
    assert "NOPE" not in health["stale_symbols"]


# --- the counts stay exact ---------------------------------------------------

def test_counts_are_real_counts(tmp_path):
    path = _database(tmp_path / "r.db", [("COMI", 0), ("COMI", 1), ("ABUK", 2)])
    health = _provider(path, at_minutes=3).health()
    assert health["symbol_count"] == 2
    assert health["quote_count"] == 3


def test_an_empty_database_is_refused_not_reported_as_healthy(tmp_path):
    path = tmp_path / "r.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.commit()
    connection.close()
    health = _provider(path).health()
    assert health["collector_status"] != "OK"
    assert health["latest_received_timestamp"] is None


# --- the query itself --------------------------------------------------------

def test_the_newest_row_is_found_by_id_not_by_received_at():
    """received_at is TEXT and unindexed, so SQLite's MAX over it is lexical.

    On the live database that maximum disagreed with the newest actual arrival
    for 4 of 265 tickers, once by 12.4 hours. MAX(id) is the row the collector
    wrote last, and it agreed with the true newest arrival everywhere checked.
    """

    for sql in (module._NEWEST_ROW, module._NEWEST_PER_TICKER,
                module._NEWEST_ID_FOR_TICKER):
        assert "MAX(received_at)" not in sql
    assert "ORDER BY id DESC LIMIT 1" in module._NEWEST_ROW
    assert "MAX(id)" in module._NEWEST_PER_TICKER
    assert "MAX(id)" in module._NEWEST_ID_FOR_TICKER


def test_no_whole_table_received_at_aggregate_survives():
    import ast
    import inspect
    import textwrap

    for name in ("_snapshot", "_read_census", "_coverage_snapshot", "_symbol_snapshot"):
        function = ast.parse(textwrap.dedent(
            inspect.getsource(getattr(RubixSQLiteProvider, name)))).body[0]
        docstring = ast.get_docstring(function, clean=False)
        for node in ast.walk(function):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value != docstring):
                assert "MAX(received_at)" not in node.value, name
