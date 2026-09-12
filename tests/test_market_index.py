"""The market filter has an index to read.

``strategy.market_analyzer`` blocks new buys while EGX30 is below both of its
EMAs. Nothing in this project served ``^CASE30``, so that gate returned
``Available: False`` and allowed every bar of every run -- in the backtest and
in the live scanner alike. These pin where the index comes from in each, that
it is never counted as a company, and that a filter reading an index too old to
mean anything refuses rather than guesses.
"""

from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

from core import mubasher_live_history as live
from core import frozen_mubasher_store as store
from core.research_router import INDEX_SYMBOLS, is_index_symbol


def index_store(tmp_path, rows, equity_sessions=()):
    """A measured store holding one index series, and optionally later equity sessions."""
    from sector_flow import measured_turnover, mubasher_local

    path = tmp_path / "measured.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(measured_turnover.SCHEMA)
        connection.executescript(mubasher_local.INDEX_SCHEMA)
        connection.executemany(
            f"INSERT INTO {mubasher_local.INDEX_TABLE} VALUES (?,?,?,?,?,?,?)",
            [("^CASE30", date, close + 10, close - 10, close, 1e8, 1e9)
             for date, close in rows])
        connection.executemany(
            f"INSERT INTO {measured_turnover.TABLE} "
            "(ticker, session_date, turnover, volume, close) VALUES (?,?,?,?,?)",
            [("COMI.CA", date, 1e6, 1000, 10.0) for date in equity_sessions])
    return path


SERIES = [(day.strftime("%Y-%m-%d"), 100.0 + i)
          for i, day in enumerate(pd.bdate_range("2026-01-01", periods=300))]

#: Three sessions the equities have and the index does not.
AFTER = [day.strftime("%Y-%m-%d") for day in pd.bdate_range(
    pd.Timestamp(SERIES[-1][0]) + pd.Timedelta(days=1), periods=3)]


def test_the_index_is_served_with_its_own_provenance(tmp_path):
    frame, status, provenance = live.mubasher_live_index(
        not_after=SERIES[-1][0], database=index_store(tmp_path, SERIES))
    assert status == live.READY
    assert list(frame.columns) == live.CONTRACT_COLUMNS
    assert frame["Open"].iloc[1] == frame["Close"].iloc[0]      # no traded open exists
    assert provenance["provider"] == live.INDEX_PROVIDER
    assert provenance["index_session_lag"] == 0
    assert provenance["index_intraday_available"] is False


def test_being_a_few_sessions_behind_is_served_and_the_lag_is_stated(tmp_path):
    """The index is daily-only, so it is normally behind the equities beside it."""
    database = index_store(tmp_path, SERIES, equity_sessions=AFTER)
    frame, status, provenance = live.mubasher_live_index(
        not_after=AFTER[-1], database=database)
    assert status == live.READY
    assert provenance["index_session_lag"] == 3
    assert pd.Timestamp(frame.index[-1]).strftime("%Y-%m-%d") == SERIES[-1][0]


def test_an_index_further_behind_than_the_limit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "INDEX_LAG_LIMIT_SESSIONS", 2)
    _, status, provenance = live.mubasher_live_index(
        not_after=AFTER[-1], database=index_store(tmp_path, SERIES, equity_sessions=AFTER))
    assert status == live.STALE
    assert provenance["index_session_lag"] == 3


def test_sessions_after_the_expected_one_are_withheld(tmp_path):
    frame, _, provenance = live.mubasher_live_index(
        not_after=SERIES[-5][0], database=index_store(tmp_path, SERIES))
    assert provenance["sessions_withheld_ahead"] == 4
    assert pd.Timestamp(frame.index[-1]).strftime("%Y-%m-%d") == SERIES[-5][0]


def test_a_store_written_before_the_index_existed_is_unavailable_not_an_error(tmp_path):
    from sector_flow import measured_turnover

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(measured_turnover.SCHEMA)
    frame, status, _ = live.mubasher_live_index(database=path)
    assert frame is None and status == live.UNAVAILABLE


def test_too_little_history_is_insufficient_not_ready(tmp_path):
    _, status, _ = live.mubasher_live_index(
        min_bars=250, not_after=SERIES[9][0],
        database=index_store(tmp_path, SERIES[:10]))
    assert status == live.INSUFFICIENT


# --- it is not a company ------------------------------------------------------

def test_the_index_is_not_in_the_measured_turnover_table(tmp_path):
    """Sector share is a ratio to the market's turnover; an index row there is a company."""
    from sector_flow import measured_turnover

    database = index_store(tmp_path, SERIES, equity_sessions=[SERIES[-1][0]])
    with sqlite3.connect(database) as connection:
        tickers = {row[0] for row in connection.execute(
            f"SELECT DISTINCT ticker FROM {measured_turnover.TABLE}")}
    assert not any(t.startswith("^") for t in tickers)


def test_the_frozen_record_counts_the_index_apart_from_its_symbols(tmp_path):
    manifest = {"symbols": {
        "COMI": {"symbol": "COMI", "file": "export/COMI.csv", "format": store.EXPORT_FORMAT,
                 "sha256": "0" * 64},
        "^CASE30": {"symbol": "^CASE30", "file": "index/EGX30.csv",
                    "format": store.HISTORY_DB_FORMAT, "sha256": "0" * 64},
    }}
    (tmp_path / store.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    assert store.frozen_symbols(root=tmp_path) == ["COMI"]
    assert store.frozen_indices(root=tmp_path) == ["^CASE30"]


def test_the_index_symbol_is_recognised_with_or_without_a_suffix():
    assert is_index_symbol("^CASE30") and is_index_symbol(" ^case30 ")
    assert not is_index_symbol("COMI.CA")
    assert set(INDEX_SYMBOLS) == set(store.INDEX_TABLES)


# --- the record itself --------------------------------------------------------

@pytest.mark.skipif(not (store.FROZEN_DIR / store.MANIFEST_NAME).is_file(),
                    reason="the frozen record is not on this machine")
def test_the_shipped_record_holds_the_index_the_market_filter_asks_for():
    from strategy.market_analyzer import INDEX_SYMBOL

    frame = store.load_frozen(INDEX_SYMBOL)
    assert frame is not None, "the market filter's index is missing from the record"
    entry = store.verify(INDEX_SYMBOL)
    assert entry["file"].startswith("index/") and entry["tradeable"] is False
    # Enough history before the backtest window for an EMA200 that means something.
    assert len(frame) > 200
    assert (frame["Close"] > 0).all()


# --- routing ------------------------------------------------------------------

def test_a_backtest_reads_the_index_from_the_frozen_record_untrimmed(monkeypatch):
    """`period` bounds the stock being tested, not how much of the market's past existed."""
    from core import data_provider

    served = pd.DataFrame({
        "High": [2.0] * 400, "Low": [1.0] * 400, "Close": [1.5] * 400,
        "Volume": [10.0] * 400, "Turnover": [15.0] * 400,
        "Open": [1.5] * 400, "Adj Close": [1.5] * 400,
    }, index=pd.DatetimeIndex(pd.bdate_range("2020-01-01", periods=400), name="Date"))
    served.attrs["market_data"] = {"provider": "FROZEN_MUBASHER", "file": "index/EGX30.csv"}
    monkeypatch.setattr("core.frozen_mubasher_store.load_frozen",
                        lambda symbol, root=None: served.copy())
    with data_provider.backtest_source("frozen_mubasher"):
        frame = data_provider.load_history(
            "^CASE30", period="1y", interval="1d", purpose="backtest",
            require_positive_volume=False, min_bars=1)
    assert len(frame) == 400
    assert frame.attrs["market_data"]["provider"] == "FROZEN_MUBASHER"


def test_a_live_purpose_reads_the_index_without_asking_for_a_quote(monkeypatch):
    """A Rubix quote read costs 10-30 seconds and the index can never have one."""
    from core import data_provider

    index = pd.DataFrame({
        "Open": [1.4] * 300, "High": [2.0] * 300, "Low": [1.0] * 300,
        "Close": [1.5] * 300, "Adj Close": [1.5] * 300, "Volume": [10.0] * 300,
    }, index=pd.DatetimeIndex(pd.bdate_range("2025-01-01", periods=300), name="Date"))
    index.attrs["market_data"] = {"provider": "mubasher_index"}
    monkeypatch.setattr("core.research_router.get_market_index_history",
                        lambda symbol, **kwargs: index.copy())

    def refuse(*args, **kwargs):                    # any quote path is a defect here
        raise AssertionError("the index must not be sent down the quote path")

    monkeypatch.setattr(data_provider, "_load_swing_daily_history", refuse)
    frame = data_provider.load_history(
        "^CASE30", period="10y", interval="1d", purpose="scanner",
        require_positive_volume=False, min_bars=1)
    assert frame.attrs["market_data"]["provider"] == "mubasher_index"
    assert frame.attrs["market_data"]["purpose"] == "scanner"


def test_an_index_that_cannot_be_reached_fails_loudly(monkeypatch):
    """Nothing substitutes for it: one record holds it, or the caller is told."""
    from core import data_provider
    from core.research_router import ResearchDataUnavailable

    def unavailable(symbol, **kwargs):
        raise ResearchDataUnavailable(symbol, "DATA_UNAVAILABLE", "no index")

    monkeypatch.setattr("core.research_router.get_market_index_history", unavailable)
    with pytest.raises(data_provider.ProviderError, match="market index unavailable"):
        data_provider.load_history("^CASE30", period="10y", interval="1d",
                                   purpose="scanner", min_bars=1)


def test_the_live_index_does_not_follow_the_live_history_setting(monkeypatch, tmp_path):
    """EODHD has no ^CASE30, so the index comes from Mubasher in both modes."""
    from core import research_router

    database = index_store(tmp_path, SERIES)
    real = live.mubasher_live_index
    monkeypatch.setattr(
        "core.mubasher_live_history.mubasher_live_index",
        lambda symbol, **kwargs: real(symbol, **{**kwargs, "database": database}))
    for source in ("eodhd", "mubasher"):
        with research_router.live_source(source):
            frame = research_router.get_market_index_history(
                "^CASE30", min_bars=1, expected=pd.Timestamp(SERIES[-1][0]).date())
        assert frame.attrs["market_data"]["provider"] == live.INDEX_PROVIDER
        assert frame.attrs["market_data"]["routing_tier"] == "INDEX_NOT_AN_EQUITY"
        assert frame.attrs["market_data"]["tradeable"] is False
