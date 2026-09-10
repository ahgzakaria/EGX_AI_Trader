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
