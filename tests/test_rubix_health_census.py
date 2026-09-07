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


# --- the cache must not disengage under the load it exists for ---------------
#
# The TTL bounds how old an observation may be. It does not bound how often the
# database is read, and the two come apart exactly when it matters: a census
# costing more than its own TTL was stamped with the time the read *started*,
# so the entry was already expired when it was stored and the next call re-read
# at once. The protection vanished precisely on the slow database it was added
# for, and a permanent reader is what stops SQLite reusing the WAL, which is
# what made the database slow.

def _slow_census(monkeypatch, seconds, reads):
    def read(self, connection):
        reads.append(seconds)
        module.time.sleep(seconds)
        return {"last_seen": {}, "symbol_count": 0, "quote_count": 1,
                "event_counts": {}, "updates_last_minute": 0}
    monkeypatch.setattr(RubixSQLiteProvider, "_read_census", read)


def test_a_census_slower_than_its_ttl_is_still_reused(tmp_path, monkeypatch):
    """Four calls used to mean four reads. The entry expired before it existed."""

    reads = []
    _slow_census(monkeypatch, 0.05, reads)
    provider = _provider(_database(str(tmp_path / "r.db")), ttl=0.01)

    for _ in range(4):
        provider._census(None)

    assert len(reads) == 1, "the cache disengaged on the slow read it exists for"


def test_a_slow_database_is_read_less_often_not_more(tmp_path, monkeypatch):
    """The entry's life scales with what the read cost, so the duty cycle holds."""

    reads = []
    _slow_census(monkeypatch, 0.05, reads)
    provider = _provider(_database(str(tmp_path / "r.db")), ttl=0.01)
    provider._census(None)

    entry = module._CENSUS_CACHE[str(provider.db_path)]
    assert entry["ttl"] >= 0.05 * module.CENSUS_DUTY_FACTOR
    # And it is stamped when the value arrived, not when the read began.
    assert module.time.monotonic() - entry["at"] < 0.05


def test_a_fast_database_is_still_governed_by_the_ttl(tmp_path, monkeypatch):
    """The floor must not quietly lengthen the window on a healthy database."""

    reads = []
    _slow_census(monkeypatch, 0.0, reads)
    provider = _provider(_database(str(tmp_path / "r.db")), ttl=30.0)
    provider._census(None)

    assert module._CENSUS_CACHE[str(provider.db_path)]["ttl"] == 30.0


def test_the_ticker_list_is_protected_the_same_way(tmp_path, monkeypatch):
    """It is the same cache written twice; it had the same defect twice."""

    path = _database(str(tmp_path / "r.db"))
    provider = _provider(path, ttl=0.01)
    connection = sqlite3.connect(path)
    calls = []

    class Slow:
        def execute(self, sql, *args):
            calls.append(sql)
            module.time.sleep(0.05)
            return connection.execute(sql, *args)

    for _ in range(4):
        provider._known_tickers(Slow())
    assert len(calls) == 1


# --- what the verdict is allowed to depend on --------------------------------
#
# bar_age_seconds is the one number _state_from_snapshot branches on that came
# out of the census. Left there, a database slow enough to need the cache would
# age its own newest candle past bar_stale_after_minutes and report RUBIX_STALE
# -- switching off live scanning as a side effect of protecting the WAL. It
# costs 0.17 seconds against 8 GB.

def test_the_newest_candle_is_read_every_call(tmp_path):
    path = _database(str(tmp_path / "r.db"))
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO candles_1m (ticker, minute, open, high, low, close) "
        "VALUES (?,?,?,?,?,?)", ("COMI", BASE.isoformat(), 1, 1, 1, 1))
    connection.commit()

    provider = _provider(path, at_minutes=1, ttl=600.0)
    first = provider.health()
    assert first["latest_candle_timestamp"] == BASE.isoformat()

    later = (BASE + timedelta(minutes=1)).isoformat()
    connection.execute(
        "INSERT INTO candles_1m (ticker, minute, open, high, low, close) "
        "VALUES (?,?,?,?,?,?)", ("COMI", later, 1, 1, 1, 1))
    connection.commit()
    connection.close()

    second = provider.health()
    assert second["latest_candle_timestamp"] == later, (
        "a cached candle ages into RUBIX_STALE and turns off live scanning")
    # And the split, in the same breath: the count of bars is displayed and
    # nothing branches on it, so it stays behind the cache with the rest of the
    # census. Only the newest minute had to be bought back.
    assert second["minute_bar_count"] == 1


def test_no_query_the_verdict_reads_sits_behind_the_cache():
    """A guard on where the line is, not on how it is currently drawn."""

    import inspect

    census = inspect.getsource(RubixSQLiteProvider._read_census)
    assert "MAX(minute)" not in census and "_NEWEST_CANDLE" not in census, (
        "bar_age_seconds drives _state_from_snapshot; the newest minute may not "
        "be cached. COUNT(*) over the same table is fine -- it is displayed, "
        "and nothing branches on it.")


# --- the newest minute, and what it is allowed to cost -----------------------

def test_the_newest_minute_matches_a_plain_scan(tmp_path):
    """The skip-scan replaced MAX(minute); it must agree with it exactly."""

    path = _database(str(tmp_path / "r.db"))
    connection = sqlite3.connect(path)
    minutes = [(BASE + timedelta(minutes=n)).isoformat() for n in range(5)]
    for ticker in ("COMI", "ABUK", "ZZZZ"):
        for minute in minutes:
            connection.execute(
                "INSERT INTO candles_1m (ticker, minute, open, high, low, close) "
                "VALUES (?,?,?,?,?,?)", (ticker, minute, 1, 1, 1, 1))
    # A ticker whose newest minute is behind everyone else's: the maximum must
    # come from across the tickers, not from whichever one is walked last.
    connection.execute(
        "INSERT INTO candles_1m (ticker, minute, open, high, low, close) "
        "VALUES (?,?,?,?,?,?)", ("AAAA", minutes[0], 1, 1, 1, 1))
    connection.commit()

    scan = connection.execute("SELECT MAX(minute) FROM candles_1m").fetchone()[0]
    skip = connection.execute(module._NEWEST_CANDLE).fetchone()[0]
    connection.close()
    assert skip == scan == minutes[-1]


def test_the_newest_minute_is_found_by_seeking_not_scanning():
    """The reason this query is shaped the way it is."""

    path = ":memory:"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    plan = [row[3] for row in
            connection.execute("EXPLAIN QUERY PLAN " + module._NEWEST_CANDLE)]
    connection.close()
    assert not any("SCAN candles_1m" in step for step in plan), (
        "MAX(minute) cannot use a (ticker, minute) key and scans 1.4M rows; "
        f"every step must be a SEARCH. Got: {plan}")


def test_an_empty_candle_table_has_no_newest_minute(tmp_path):
    """The recursive walk must terminate on a table with no tickers at all."""

    path = str(tmp_path / "empty.db")
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.commit()
    assert connection.execute(module._NEWEST_CANDLE).fetchone()[0] is None
    connection.close()


# --- one answer per day, not one per symbol ----------------------------------
#
# session_lag is trading_session_lag(the timestamp's Cairo date, current), and
# both are fixed inside a single _coverage_snapshot call. 265 symbols across 3
# distinct dates were costing 265 calls at 1,065 us each -- 282 ms of pure CPU,
# 512 us of it re-resolving the holiday calendar to recompute one shared phase.

def test_the_closed_session_verdict_is_computed_once_per_date(tmp_path, monkeypatch):
    path = _database(str(tmp_path / "r.db"))
    provider = _provider(path, symbols=tuple(f"S{n}" for n in range(40)))

    calls = []
    real = module.assess_quote_freshness

    def counted(received, exchange, **kwargs):
        calls.append(received)
        return real(received, exchange, **kwargs)

    monkeypatch.setattr(module, "assess_quote_freshness", counted)
    # 40 symbols, two days between them, market closed.
    monkeypatch.setattr(module, "egx_session_phase", lambda *a, **k: "POST_CLOSE")
    latest = {f"S{n}": BASE - timedelta(days=n % 2) for n in range(40)}

    provider._coverage_snapshot(latest)
    assert len(calls) == 2, f"one call per distinct Cairo date, got {len(calls)}"


def test_memoising_does_not_change_who_is_updating(tmp_path, monkeypatch):
    """The equivalence, stated as a test rather than trusted."""

    path = _database(str(tmp_path / "r.db"))
    symbols = tuple(f"S{n}" for n in range(30))
    provider = _provider(path, symbols=symbols)
    monkeypatch.setattr(module, "egx_session_phase", lambda *a, **k: "POST_CLOSE")

    latest = {f"S{n}": BASE - timedelta(days=n % 4) for n in range(30)}

    from core.egx_session import assess_quote_freshness, cairo_now
    expected = sorted(
        symbol for symbol, stamp in latest.items()
        if assess_quote_freshness(
            stamp, stamp, value=cairo_now(provider._utc_now()),
            open_stale_after_minutes=provider.stale_after_minutes).session_lag == 0)

    assert provider._coverage_snapshot(latest)["updating_symbols"] == expected


# --- an age is how old the data was, not how long the reader took ------------
#
# The ages were measured against the clock at the end of _snapshot, so a slow
# call charged the feed for its own duration. On 2026-09-07 a cold call took 78
# seconds against 7.6 GB and reported age_seconds 248 and RUBIX_STALE while
# quotes were arriving 0 seconds old. The supervisor's stale threshold is 60.

def test_a_slow_read_does_not_age_the_quote_it_read(tmp_path, monkeypatch):
    path = _database(str(tmp_path / "r.db"))
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO candles_1m (ticker, minute, open, high, low, close) "
        "VALUES (?,?,?,?,?,?)", ("COMI", BASE.isoformat(), 1, 1, 1, 1))
    connection.commit()
    connection.close()

    # The clock advances by two minutes for every reading taken after the tip.
    ticks = [BASE + timedelta(seconds=30)]

    def creeping_clock():
        value = ticks[-1]
        ticks.append(value + timedelta(minutes=2))
        return value

    provider = RubixSQLiteProvider(db_path=path, expected_symbols=("COMI", "ABUK"),
                                   census_ttl_seconds=0, now=creeping_clock)
    health = provider.health()

    # The newest quote was written at BASE and read 30 seconds later.
    assert health["age_seconds"] == pytest.approx(30.0, abs=1.0), (
        "age_seconds must be measured when the tip was read, not after the "
        "census that followed it")


def test_every_age_uses_the_same_instant(tmp_path):
    """One reading, so bar age and quote age cannot disagree about 'now'."""

    import inspect

    source = inspect.getsource(RubixSQLiteProvider._snapshot)
    body = source.partition("observed_at = self._utc_now()")[2]
    assert "self._utc_now()" not in body, (
        "the snapshot must take one clock reading; a second one lets two ages "
        "in the same result describe two different moments")
