"""The live Mubasher reader, the router switch, and the shadow's bookkeeping.

The switch must change nothing until it is set, must serve Mubasher's record
through the same frame and metadata when it is, and must refuse -- never fall
back -- when that record cannot serve a symbol on time.
"""

from __future__ import annotations

from datetime import date
import sqlite3

import numpy as np
import pandas as pd
import pytest

from config.settings_manager import DEFAULT_SETTINGS, settings
import core.research_router as router
from core import mubasher_live_history as live
from sector_flow import measured_turnover


def make_store(tmp_path, ticker="COMI.CA", sessions=300, end="2026-09-10",
               unconfirmed_last=0):
    """A measured store with one ticker; the last `unconfirmed_last` closes unconfirmed."""
    path = tmp_path / "measured.db"
    dates = pd.bdate_range(end=end, periods=sessions)
    with sqlite3.connect(path) as connection:
        connection.executescript(measured_turnover.SCHEMA)
        rows = []
        for i, day in enumerate(dates):
            close = round(20 + i * 0.05, 3)
            confirmed = 0 if i >= sessions - unconfirmed_last else 1
            rows.append((ticker, day.date().isoformat(), 1_000_000.0 + i, 50_000.0 + i,
                         None, close + 0.2, close - 0.2, close, confirmed))
        connection.executemany(
            f"INSERT INTO {measured_turnover.TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return path, dates


# --- the reader ---------------------------------------------------------------

def test_the_reader_serves_the_store_oldest_first_with_the_previous_close_as_open(tmp_path):
    path, dates = make_store(tmp_path)
    frame, status, provenance = live.mubasher_live_history(
        "COMI", min_bars=250, not_after=dates[-1].date(), database=path)
    assert status == live.READY
    assert list(frame.columns) == live.CONTRACT_COLUMNS
    assert frame.index.is_monotonic_increasing and len(frame) == 300
    assert np.isnan(frame["Open"].iloc[0])
    assert np.allclose(frame["Open"].iloc[1:].to_numpy(), frame["Close"].iloc[:-1].to_numpy())
    assert (frame["Adj Close"] == frame["Close"]).all()
    assert provenance["open_policy"] == live.OPEN_POLICY
    assert frame.attrs["market_data"]["provider"] == live.PROVIDER


def test_nothing_past_not_after_is_served(tmp_path):
    path, dates = make_store(tmp_path)
    frame, status, provenance = live.mubasher_live_history(
        "COMI", min_bars=250, not_after=dates[-3].date(), database=path)
    assert frame.index[-1] == dates[-3]
    assert provenance["sessions_withheld_ahead"] == 2
    assert status == live.READY


def test_a_store_behind_the_expected_session_is_stale(tmp_path):
    path, dates = make_store(tmp_path, end="2026-09-08")
    _, status, _ = live.mubasher_live_history(
        "COMI", min_bars=250, not_after=date(2026, 9, 10), database=path)
    assert status == live.STALE


def test_a_symbol_that_did_not_trade_on_the_session_is_not_stale(tmp_path):
    """Stale is the record being behind, not one name having no trade that day."""
    path, dates = make_store(tmp_path, end="2026-09-08")
    with sqlite3.connect(path) as connection:
        connection.execute(f"INSERT INTO {measured_turnover.TABLE} VALUES "
                           "('SWDY.CA', '2026-09-10', 1.0, 1.0, NULL, 1.0, 1.0, 1.0, 1)")
    frame, status, provenance = live.mubasher_live_history(
        "COMI", min_bars=250, not_after=date(2026, 9, 10), database=path)
    assert status == live.READY
    assert provenance["traded_on_expected_session"] is False
    assert provenance["store_last_session"] == "2026-09-10"
    assert frame.index[-1].date() == date(2026, 9, 8)


def test_too_little_history_is_insufficient_and_nothing_is_unavailable(tmp_path):
    path, dates = make_store(tmp_path, sessions=100)
    _, status, _ = live.mubasher_live_history(
        "COMI", min_bars=250, not_after=dates[-1].date(), database=path)
    assert status == live.INSUFFICIENT
    frame, status, _ = live.mubasher_live_history("ZZZZ", database=path)
    assert frame is None and status == live.UNAVAILABLE


def test_unconfirmed_closes_are_kept_and_counted(tmp_path):
    path, dates = make_store(tmp_path, unconfirmed_last=2)
    frame, _, provenance = live.mubasher_live_history(
        "COMI", min_bars=250, not_after=dates[-1].date(), database=path)
    assert len(frame) == 300
    assert provenance["unconfirmed_close_count"] == 2
    assert provenance["last_close_confirmed"] is False
    assert provenance["recent_unconfirmed_close_dates"] == (
        dates[-2].date().isoformat(), dates[-1].date().isoformat())


# --- the router switch --------------------------------------------------------

class EodhdReached(Exception):
    pass


@pytest.fixture
def store(tmp_path, monkeypatch):
    path, dates = make_store(tmp_path)
    monkeypatch.setattr(measured_turnover, "DEFAULT_DATABASE", str(path))
    monkeypatch.setattr(router, "_expected_completed_session", lambda: dates[-1].date())

    def eodhd_must_not_be_read(*_args, **_kwargs):
        raise EodhdReached("EODHD was read")

    monkeypatch.setattr(router, "eodhd_history", eodhd_must_not_be_read)
    return path, dates


def test_the_live_source_is_eodhd_until_it_is_switched():
    assert DEFAULT_SETTINGS["live_history_source"] == "eodhd"
    assert settings.data.get("live_history_source") == "eodhd"
    assert router.live_history_source() == "eodhd"


def test_the_default_path_still_reads_eodhd(store):
    with pytest.raises(EodhdReached):
        router.get_current_research_history("COMI")


def test_switched_to_mubasher_the_router_serves_its_record(store):
    _, dates = store
    with router.live_source("mubasher"):
        frame = router.get_current_research_history("COMI")
    md = frame.attrs["market_data"]
    assert md["provider"] == "mubasher_live"
    assert md["price_series"] == live.SERIES
    assert md["data_quality_status"] == live.READY
    assert md["latest_completed_session"] == dates[-1].date().isoformat()
    assert md["automatic_use_permitted"] is True
    assert len(frame) == 300
    assert router.live_history_source() == "eodhd"      # the scope ended


def test_a_stale_mubasher_record_is_refused_not_replaced_by_eodhd(tmp_path, monkeypatch):
    path, dates = make_store(tmp_path, end="2026-09-08")
    monkeypatch.setattr(measured_turnover, "DEFAULT_DATABASE", str(path))
    monkeypatch.setattr(router, "_expected_completed_session", lambda: date(2026, 9, 10))
    monkeypatch.setattr(router, "eodhd_history",
                        lambda *a, **k: (_ for _ in ()).throw(EodhdReached("EODHD was read")))
    with router.live_source("mubasher"):
        with pytest.raises(router.ResearchDataUnavailable) as refused:
            router.get_current_research_history("COMI")
    assert refused.value.status == live.STALE


def test_an_unknown_live_source_is_refused(monkeypatch):
    monkeypatch.setitem(settings.data, "live_history_source", "yahoo")
    with pytest.raises(ValueError, match="live_history_source"):
        router.live_history_source()


# --- the shadow's bookkeeping -------------------------------------------------

def _frames(dates, bump=0.0, attrs=None):
    close = pd.Series(np.linspace(10, 12, len(dates)) + bump, index=dates)
    frame = pd.DataFrame({"Open": close, "High": close + 0.1, "Low": close - 0.1,
                          "Close": close, "Adj Close": close, "Volume": 1000.0})
    frame.attrs["market_data"] = attrs or {}
    return frame


def test_cut_keeps_only_symbols_with_a_bar_on_the_session():
    from scripts.record_live_source_shadow import cut

    dates = pd.bdate_range("2026-08-01", "2026-09-10")
    histories = {"ON": _frames(dates, attrs={"k": 1}), "BEHIND": _frames(dates[:-1])}
    kept = cut(histories, dates[-1])
    assert list(kept) == ["ON"]
    assert kept["ON"].attrs["market_data"] == {"k": 1}
    earlier = cut(histories, dates[-2])
    assert set(earlier) == {"ON", "BEHIND"}
    assert earlier["ON"].index[-1] == dates[-2]


def test_compare_symbols_measures_the_gap_and_names_unconfirmed_closes():
    from scripts.record_live_source_shadow import compare_symbols

    dates = pd.bdate_range("2026-08-01", "2026-09-10")
    eodhd = {"A": _frames(dates)}
    mubasher = {"A": _frames(dates, bump=0.5, attrs={
        "recent_unconfirmed_close_dates": (dates[-1].date().isoformat(),)})}
    row, missing = compare_symbols(eodhd, mubasher, {"eodhd": {}, "mubasher": {"B": "stale"}},
                                   dates[-1], ["A", "B"])
    assert row["close_diff_percent"] == pytest.approx((12.5 / 12 - 1) * 100, abs=1e-3)
    assert row["mubasher_close_confirmed"] == 0
    assert row["closes_within_1pct"] < 100
    assert missing["mubasher_error"] == "stale" and missing["mubasher_close"] is None


def test_recording_a_session_twice_replaces_its_rows(tmp_path):
    from scripts import record_live_source_shadow as shadow

    dates = pd.bdate_range("2026-08-01", "2026-09-10")
    histories = {"A": _frames(dates), "B": _frames(dates)}
    connection = sqlite3.connect(tmp_path / "shadow.db")
    connection.executescript(shadow.SCHEMA)
    fake = {"eodhd": {"confirmed_breakout": {"A"}, "breakout_watch": set(), "swing_breakout": {"B"}},
            "mubasher": {"confirmed_breakout": {"A", "B"}, "breakout_watch": set(), "swing_breakout": set()}}
    calls = iter([fake["eodhd"], fake["mubasher"], fake["eodhd"], fake["mubasher"]])
    for _ in range(2):
        summary = shadow.record(connection, dates[-1], "daily", ["A", "B"],
                                {"eodhd": 2, "mubasher": 2}, histories, histories,
                                {"eodhd": {}, "mubasher": {}}, rules=lambda _h: next(calls))
    assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 2
    signals = dict(((r, s), (e, m)) for r, s, e, m in connection.execute(
        "SELECT rule, symbol, on_eodhd, on_mubasher FROM signals"))
    assert signals[("confirmed_breakout", "B")] == (0, 1)
    assert signals[("swing_breakout", "B")] == (1, 0)
    assert summary["confirmed_breakout"] == (1, 0, 1)


def test_sessions_to_record_starts_at_the_expected_session():
    from scripts.record_live_source_shadow import sessions_to_record

    dates = pd.bdate_range("2026-08-01", "2026-09-10")
    histories = {"A": _frames(dates), "B": _frames(dates)}
    chosen = sessions_to_record(dates[-2].date(), histories, backfill=3)
    assert chosen[0] == dates[-2]
    assert chosen[1:] == [dates[-3], dates[-4], dates[-5]]
