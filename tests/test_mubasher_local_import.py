"""The measured store fills itself from the terminal's own databases.

These are the facts the import depends on, held against fixtures rather than
against the machine's real installation. Every one of them was measured on
2026-09-10 and every one of them is a thing that could silently change if
MubasherTrade PRO ships a new build:

- ``TMIN`` is minutes from the Unix epoch in UTC, so a session is found by
  converting to Cairo and not by arithmetic on a raw integer.
- The auction is the block of minutes at one price after 14:15, and the close
  is only official when it is there.
- ``history.db``'s ``OP`` column is the previous close, so no session sourced
  from it may carry an open.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sector_flow import mubasher_local as local  # noqa: E402
from sector_flow import measured_turnover as store  # noqa: E402


def _tmin(day, hour, minute):
    """The TMIN a Cairo wall-clock time would be stored as."""
    cairo = datetime(day.year, day.month, day.day, hour, minute, tzinfo=local.CAIRO)
    return int((cairo - local.TMIN_EPOCH).total_seconds() // 60)


def _build_root(tmp_path, history_rows=(), intraday=(), account="99"):
    """Write a fake UserData tree in the shape the terminal keeps."""

    base = tmp_path / account
    hist = base / local.HISTORY_RELATIVE
    intra = base / local.INTRADAY_RELATIVE
    hist.parent.mkdir(parents=True, exist_ok=True)
    intra.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(hist) as connection:
        for ticker, rows in history_rows:
            connection.execute(
                f'CREATE TABLE "_{ticker}" (INS TEXT, DATE TEXT, OP TEXT, HIG TEXT, '
                f'LOW TEXT, CLS TEXT, VOL TEXT, TOVR TEXT, NOTR TEXT)')
            connection.executemany(
                f'INSERT INTO "_{ticker}" VALUES (?,?,?,?,?,?,?,?,?)', rows)

    with sqlite3.connect(intra) as connection:
        connection.execute("CREATE TABLE INTRADAY_MASTER (INTRADAYDATE INTEGER)")
        for ticker, rows in intraday:
            connection.execute(
                f'CREATE TABLE "_{ticker}" (INS TEXT, TMIN TEXT, OP TEXT, HIG TEXT, '
                f'LOW TEXT, CLS TEXT, VOL TEXT, TOVR TEXT, NOTR TEXT)')
            connection.executemany(
                f'INSERT INTO "_{ticker}" VALUES (?,?,?,?,?,?,?,?,?)', rows)
    return base


def _minute(tmin, price, volume=100.0, turnover=None):
    return ("0", str(tmin), str(price), str(price), str(price), str(price),
            str(volume), str(turnover if turnover is not None else price * volume), "1")


SESSION = datetime(2026, 9, 10).date()


def _session_with_auction(continuous_close, auction_price):
    """Minutes for one symbol: continuous dealing, a gap, then the cross."""
    rows = [_minute(_tmin(SESSION, 10, 0), 100.0),
            _minute(_tmin(SESSION, 12, 0), 105.0),
            _minute(_tmin(SESSION, 14, 14), continuous_close)]
    if auction_price is not None:
        rows.append(_minute(_tmin(SESSION, 14, 25), auction_price, volume=500.0))
    return rows


def test_tmin_resolves_to_the_cairo_session_it_belongs_to():
    """29,817,329 is 14:29 in Cairo on 2026-09-10 -- the auction's last minute."""

    day, minute = local.session_of(29817329)
    assert day == "2026-09-10"
    assert minute == 14 * 60 + 29
    assert minute >= local.AUCTION_FROM_MINUTE

    # And the boundary the other way: a bar one minute before continuous close
    # is not the auction, however close it looks.
    _, before = local.session_of(_tmin(SESSION, 14, 14))
    assert before < local.AUCTION_FROM_MINUTE


def test_the_close_is_the_auction_price_and_not_the_last_continuous_trade(tmp_path):
    """COMI dealt to 138.33 on 2026-09-10 and crossed at 138.17."""

    base = _build_root(tmp_path, intraday=[
        ("COMI", _session_with_auction(138.33, 138.17))])
    rows = local.read_intraday(base)

    assert len(rows) == 1
    bar = rows.iloc[0]
    assert bar["close"] == pytest.approx(138.17)
    assert bar["close_confirmed"] == 1
    # The open is the first minute of the session, which is the only true open
    # anywhere on the machine.
    assert bar["open"] == pytest.approx(100.0)
    assert bar["high"] == pytest.approx(138.33)
    assert bar["low"] == pytest.approx(100.0)


def test_a_session_whose_auction_never_printed_is_marked_unconfirmed(tmp_path):
    """Without a bar after 14:15 the last price is not a close, and says so.

    Reconstructing one anyway reproduced the official close for 30% of
    sessions, against 99.4% when the auction was there. The row is kept -- it
    is still the only record of that day -- and flagged.
    """

    base = _build_root(tmp_path, intraday=[
        ("ABUK", _session_with_auction(76.5, None))])
    bar = local.read_intraday(base).iloc[0]

    assert bar["close"] == pytest.approx(76.5)
    assert bar["close_confirmed"] == 0


def test_no_session_from_history_db_carries_an_open(tmp_path):
    """Its OP column is the previous close, not an open.

    122,719 of 126,299 rows have ``OP[d] == CLS[d-1]``, and the CSV export
    written from this file put the *high* in its "Open" column -- all 138,148
    exported rows have ``Open == High``. A wrong open makes every session look
    like one that opened at its top and fell.
    """

    base = _build_root(tmp_path, history_rows=[("COMI", [
        ("0", "20260906", "141.00", "142.50", "140.01", "142.00", "2556530", "3.6e8", "7017"),
        # OP repeats the previous CLS, which is exactly the trap.
        ("0", "20260907", "142.00", "142.80", "140.06", "140.06", "3867177", "5.4e8", "6465"),
    ])])

    rows = local.read_history(base)
    assert len(rows) == 2
    assert rows["open"].isna().all()
    assert rows["close"].tolist() == [142.00, 140.06]
    assert (rows["close_confirmed"] == 1).all()

    # And the export reader must not import that column either.
    assert "Open" not in store.PRICE_COLUMNS


def test_history_is_authoritative_and_the_minute_store_only_fills_after_it(tmp_path):
    """Minute sums run light, so they never overwrite a day history.db has.

    Against the daily record the median session is exact and 97.4% land within
    0.5%, but it is never over: the minute bars leave out something the daily
    record counts. A session history.db describes is taken from history.db.
    """

    base = _build_root(
        tmp_path,
        history_rows=[("COMI", [
            ("0", "20260910", "139.52", "139.58", "138.17", "138.17",
             "5297460", "7.35e8", "9000")])],
        intraday=[("COMI", _session_with_auction(138.33, 138.17))])

    database = str(tmp_path / "measured.db")
    metadata = local.import_local(base, database=database, symbols=["COMI"])

    assert metadata["intraday_rows_appended"] == 0
    with sqlite3.connect(database) as connection:
        volume = connection.execute(
            f"SELECT volume FROM {store.TABLE} WHERE session_date='2026-09-10'"
        ).fetchone()[0]
    assert volume == pytest.approx(5297460.0), "the daily record, not the minute sum"


def test_the_import_refuses_to_replace_a_store_with_nothing(tmp_path):
    """A store quietly replaced by an empty one is the outcome worth failing on."""

    base = _build_root(tmp_path)
    with pytest.raises(ValueError):
        local.import_local(base, database=str(tmp_path / "measured.db"))


def test_indices_and_bonds_are_not_imported_as_companies(tmp_path):
    """Sector share is a ratio; an index row would be counted as a company."""

    for name in ("EGX30", "EGX30 Capped", "EG012027", "EGT1504250426G0",
                 "T.Bond 1-3yr"):
        assert local.NOT_EQUITY.search(name), name
    for name in ("COMI", "ABUK", "SWDY", "AALR", "KZPC"):
        assert not local.NOT_EQUITY.search(name), name


def test_a_machine_without_the_terminal_is_not_an_error(tmp_path):
    """The store is an enrichment; everything else must still run without it."""

    assert local.find_root(tmp_path / "nowhere") is None
    assert local.read_history(tmp_path / "nowhere").empty
    assert local.read_intraday(tmp_path / "nowhere").empty


def test_the_import_never_writes_to_the_terminals_own_databases(tmp_path):
    """Read-only, because the terminal is running while this reads."""

    base = _build_root(tmp_path, history_rows=[("COMI", [
        ("0", "20260907", "142.00", "142.80", "140.06", "140.06",
         "3867177", "5.4e8", "6465")])])
    before = {p: p.stat().st_mtime_ns for p in base.rglob("*.db")}

    local.read_history(base)
    local.read_intraday(base)

    assert {p: p.stat().st_mtime_ns for p in base.rglob("*.db")} == before

    with local._open_read_only(base / local.HISTORY_RELATIVE) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE probe (x INTEGER)")


def test_the_scope_is_the_stores_own_and_not_every_table_in_the_file(tmp_path):
    """Widening it would pull in rights lines and treasury series."""

    database = str(tmp_path / "measured.db")
    with sqlite3.connect(database) as connection:
        connection.executescript(store.SCHEMA)
        connection.execute(
            f"INSERT INTO {store.TABLE} (ticker, session_date, turnover) "
            "VALUES ('ACGC.CA', '2003-11-03', 5000.0)")

    scope = local.default_scope(database)
    assert "ACGC" in scope, "a symbol already stored stays in scope"
    assert "COMI" in scope, "and the tradeable universe is in it too"
    assert not any(name.endswith("_r1") for name in scope)


def test_stale_history_is_reported_rather_than_assumed_current(tmp_path):
    """The metadata says how far each source reached, because they differ.

    history.db moves only when the terminal downloads history; the minute store
    moves by itself. The gap between them is the part that used to require
    typing 240 symbols into an export dialog.
    """

    base = _build_root(
        tmp_path,
        history_rows=[("COMI", [
            ("0", "20260907", "142.00", "142.80", "140.06", "140.06",
             "3867177", "5.4e8", "6465")])],
        intraday=[("COMI", _session_with_auction(138.33, 138.17))])

    metadata = local.import_local(base, database=str(tmp_path / "m.db"),
                                  symbols=["COMI"])
    assert metadata["history_last_session"] == "2026-09-07"
    assert metadata["last_session"] == "2026-09-10"
    assert metadata["intraday_sessions_appended"] == ["2026-09-10"]
    assert metadata["source"] == "mubasher_local"


# --- one session at a time, for the research that works in minutes ----------

def test_a_sessions_shape_comes_out_of_the_minute_store(tmp_path):
    """Open, close, high, low and the exchange's own turnover, per symbol."""

    base = _build_root(tmp_path, intraday=[
        ("COMI", _session_with_auction(138.33, 138.17))])

    shapes = local.session_minutes("2026-09-10", base)

    assert set(shapes) == {"COMI"}
    comi = shapes["COMI"]
    assert comi["open"] == pytest.approx(100.0), "the first traded minute"
    assert comi["close"] == pytest.approx(138.17), "the auction, not 138.33"
    assert comi["high"] == pytest.approx(138.33)
    assert comi["low"] == pytest.approx(100.0)
    assert comi["bars"] == 4
    assert comi["last_minute"] == "14:25"
    assert comi["close_confirmed"] is True


def test_a_session_the_store_does_not_hold_is_empty_not_an_error(tmp_path):
    base = _build_root(tmp_path, intraday=[
        ("COMI", _session_with_auction(138.33, 138.17))])

    assert local.session_minutes("2019-01-02", base) == {}
    assert local.session_opens("2019-01-02", base) == {}


def test_the_sessions_held_are_reported_in_order(tmp_path):
    base = _build_root(tmp_path)
    with sqlite3.connect(base / local.INTRADAY_RELATIVE) as connection:
        connection.executemany("INSERT INTO INTRADAY_MASTER VALUES (?)",
                               [(20260910,), (20260908,), (20260909,)])

    assert local.available_sessions(base) == ["2026-09-08", "2026-09-09", "2026-09-10"]


def test_the_open_is_the_first_traded_minute_and_never_invented(tmp_path):
    """The daily record's OP is the previous close; this one is a trade."""

    base = _build_root(tmp_path, intraday=[
        ("ABUK", _session_with_auction(76.5, 76.2))])

    opens = local.session_opens("2026-09-10", base)
    assert opens == {"ABUK": pytest.approx(100.0)}


# --- Roll's estimator, which must never be mistaken for a quoted spread -----

def _tape(base, session, trades):
    path = base / local.TAPE_RELATIVE / f"{session.replace('-', '')}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE TRADES (SEQUENCE INTEGER, SYMBOL TEXT, TRADETIME TEXT, "
            "TRADEPRICE TEXT, TRADEQUANTITY TEXT, ISODDLOTTRADE TEXT)")
        connection.executemany(
            "INSERT INTO TRADES VALUES (?,?,?,?,?,?)",
            [(i, sym, "070000", str(price), "100", "0")
             for i, (sym, price) in enumerate(trades)])
    return path


def test_a_bouncing_price_produces_an_estimate(tmp_path):
    """Roll reads the bid-ask bounce, and that bounce is the whole signal.

    Trades land at the bid or the ask at random around an unmoving mid, which
    is the model the estimator is derived from. Perfect alternation is the
    degenerate case and returns twice the spread, so it is not what a test of
    the estimator should feed it.
    """

    import random

    base = _build_root(tmp_path)
    rng = random.Random(7)
    spread = 0.5
    prices = [100.0 + rng.choice((-1, 1)) * spread / 2 for _ in range(4000)]
    _tape(base, "2026-09-10", [("COMI", p) for p in prices])

    estimates = local.roll_spread_estimates("2026-09-10", base)

    assert estimates["COMI"] is not None
    assert estimates["COMI"] == pytest.approx(100.0 * spread / 100.0, rel=0.1), (
        "a 0.5-wide book on a 100 price is a 0.5% spread")


def test_a_trending_price_yields_no_estimate_rather_than_zero(tmp_path):
    """Non-negative covariance is Roll's model declining to answer.

    Recording a 0 there would say "this symbol costs nothing to trade", which
    is the opposite of what the data supports.
    """

    base = _build_root(tmp_path)
    _tape(base, "2026-09-10", [("COMI", 100.0 + i * 0.1) for i in range(80)])

    # In exact arithmetic a constant drift has a covariance of zero; in this
    # one it came out at about -1e-30, which passed a bare `cov < 0` and became
    # a spread of 0.000000000001% -- a number that reads as "free to trade".
    assert local.roll_spread_estimates("2026-09-10", base)["COMI"] is None


def test_a_symbol_with_too_few_trades_is_not_estimated(tmp_path):
    base = _build_root(tmp_path)
    _tape(base, "2026-09-10", [("THIN", 10.0 + (i % 2) * 0.1) for i in range(10)])

    assert "THIN" not in local.roll_spread_estimates("2026-09-10", base)


def test_a_session_with_no_tape_is_empty_not_an_error(tmp_path):
    base = _build_root(tmp_path)
    assert local.roll_spread_estimates("2026-09-10", base) == {}


def _history_row(day, close, turnover=5000.0):
    return ("0", day, str(close), str(close + 1), str(close - 1), str(close),
            "100", str(turnover), "10")


def test_a_ticker_filed_under_another_name_is_stored_under_the_universes(tmp_path):
    """The terminal holds EODHD's AIND as AIHC; the store must carry it as AIND."""

    base = _build_root(tmp_path, history_rows=[
        ("AIHC", [_history_row("20260906", 1.10), _history_row("20260907", 1.20)]),
    ])
    database = str(tmp_path / "measured.db")
    metadata = local.import_local(base, database=database, symbols=["AIND"],
                                  aliases={"AIHC": "AIND"})
    assert metadata["isin_resolved"] == {"AIND": "AIHC"}
    frame = store.frame_for("AIND.CA", database=database)
    assert frame is not None and list(frame["Close"]) == [1.10, 1.20]
    assert store.frame_for("AIHC.CA", database=database) is None


def test_a_renamed_ticker_carries_the_retired_tickers_history(tmp_path):
    """AMII's table starts on the bar ARVA's ends; the store must hold both as AMII."""

    base = _build_root(tmp_path, history_rows=[
        ("ARVA", [_history_row("20260723", 12.00), _history_row("20260726", 12.47)]),
        ("AMII", [_history_row("20260726", 12.47), _history_row("20260727", 12.60)]),
    ])
    database = str(tmp_path / "measured.db")
    metadata = local.import_local(base, database=database, symbols=["AMII"],
                                  aliases={}, predecessors={"AMII": ["ARVA"]})
    frame = store.frame_for("AMII.CA", database=database)
    assert list(frame.index.strftime("%Y-%m-%d")) == ["2026-07-23", "2026-07-26", "2026-07-27"]
    assert metadata["predecessors_stitched"] == {"AMII": {"from": "ARVA", "sessions": 1}}
    assert store.frame_for("ARVA.CA", database=database) is None


def test_a_retired_ticker_on_another_price_basis_is_not_stitched(tmp_path):
    """EDBM and CRST share sessions at a 0.66 ratio; joining them would fake a move."""

    base = _build_root(tmp_path, history_rows=[
        ("EDBM", [_history_row("20260723", 1.00), _history_row("20260726", 1.00)]),
        ("CRST", [_history_row("20260726", 0.66), _history_row("20260727", 0.70)]),
    ])
    database = str(tmp_path / "measured.db")
    metadata = local.import_local(base, database=database, symbols=["CRST"],
                                  aliases={}, predecessors={"CRST": ["EDBM"]})
    frame = store.frame_for("CRST.CA", database=database)
    assert list(frame.index.strftime("%Y-%m-%d")) == ["2026-07-26", "2026-07-27"]
    assert metadata["predecessors_stitched"] == {}


def test_isin_resolution_names_only_tickers_the_terminal_lacks(tmp_path):
    base = _build_root(tmp_path, history_rows=[
        ("AIHC", [_history_row("20260907", 1.20)]),
        ("COMI", [_history_row("20260907", 140.0)]),
    ])
    by_isin = {"EGS21351C019": "AIHC", "EGS60121C018": "COMI"}
    isin_of = {"AIND": "EGS21351C019", "COMI": "EGS60121C018", "GONE": "NOPE"}
    aliases = local._isin_aliases(base, {"AIND", "COMI", "GONE"},
                                  isin_of=isin_of, by_isin=by_isin)
    assert aliases == {"AIHC": "AIND"}


def test_the_symbol_master_maps_isin_to_the_terminals_ticker(tmp_path):
    import json

    base = _build_root(tmp_path)
    cache = base.parent.parent / "Cache"
    cache.mkdir(parents=True, exist_ok=True)
    payload = {"HED": {"TD": "EXCHANGE|SYMBOL|ISIN_CODE"},
               "DAT": {"TD": ["CASE|AIHC|EGS21351C019", "CASE|MMAT|EGS70P91C010"]}}
    with sqlite3.connect(cache / "PrimarySystemMeta.db") as connection:
        connection.execute("CREATE TABLE SYMBOL_MASTER (EXCHANGE TEXT, VERSION TEXT, "
                           "LANGUAGE TEXT, JSON TEXT)")
        connection.execute("INSERT INTO SYMBOL_MASTER VALUES ('CASE', '1', 'EN', ?)",
                           (json.dumps(payload),))
    assert local.isin_ticker_map(base) == {"EGS21351C019": "AIHC", "EGS70P91C010": "MMAT"}


def test_odd_lot_trades_are_excluded_from_the_estimate(tmp_path):
    """They print away from the book and would widen every estimate."""

    import inspect

    source = inspect.getsource(local.roll_spread_estimates)
    assert "ISODDLOTTRADE='0'" in source


# --- the market index ---------------------------------------------------------

def _day(ticker, date, close):
    return ("0", date, str(close), str(close + 1), str(close - 1), str(close),
            "1000000", "5000000", "500")


def test_the_index_is_read_into_its_own_table_and_not_among_the_companies(tmp_path):
    """Sector share is a ratio to the market's turnover; an index there is a company."""

    base = _build_root(tmp_path, history_rows=[
        ("COMI", [_day("COMI", "20260907", 138)]),
        ("EGX30", [_day("EGX30", "20260906", 56676), _day("EGX30", "20260907", 56627)]),
    ])
    metadata = local.import_local(base, database=str(tmp_path / "m.db"),
                                  symbols=["COMI"])
    assert metadata["index_rows"] == 2
    assert metadata["index_symbols"] == ["^CASE30"]
    assert metadata["index_last_session"] == "2026-09-07"
    assert metadata["symbols"] == 1                      # the index is not one

    with sqlite3.connect(tmp_path / "m.db") as connection:
        companies = {row[0] for row in connection.execute(
            f"SELECT DISTINCT ticker FROM {store.TABLE}")}
        index = connection.execute(
            f"SELECT symbol, close FROM {local.INDEX_TABLE} ORDER BY session_date").fetchall()
    assert companies == {"COMI.CA"}
    assert index == [("^CASE30", 56676.0), ("^CASE30", 56627.0)]


def test_a_terminal_without_the_index_table_still_imports(tmp_path):
    base = _build_root(tmp_path, history_rows=[("COMI", [_day("COMI", "20260907", 138)])])
    metadata = local.import_local(base, database=str(tmp_path / "m.db"), symbols=["COMI"])
    assert metadata["index_rows"] == 0 and metadata["index_symbols"] == []


def test_the_index_table_is_rewritten_whole_on_every_import(tmp_path):
    """The terminal back-adjusts on download, so appending would mix two bases."""

    database = str(tmp_path / "m.db")
    rows = [("COMI", [_day("COMI", "20260907", 138)])]
    local.import_local(_build_root(tmp_path, history_rows=rows + [
        ("EGX30", [_day("EGX30", "20260906", 1), _day("EGX30", "20260907", 2)])]),
        database=database, symbols=["COMI"])
    local.import_local(_build_root(tmp_path, account="98", history_rows=rows + [
        ("EGX30", [_day("EGX30", "20260907", 9)])]), database=database, symbols=["COMI"])
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            f"SELECT session_date, close FROM {local.INDEX_TABLE}").fetchall() == [
                ("2026-09-07", 9.0)]
