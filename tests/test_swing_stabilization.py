"""Regression tests for strict Swing history/live-overlay separation."""

from datetime import datetime, timezone
import sqlite3

import pandas as pd

import core.data_provider as routing
from config.settings_manager import settings
from indicators.technical import calculate_indicators
from providers.local_cache_provider import LocalCacheProvider
from providers.rubix_sqlite_provider import RubixSQLiteProvider
from services.swing_coverage_audit import classify_failure
from tests.fixtures.frozen_snapshot import write_snapshot


def _daily(rows=300):
    index = pd.date_range("2024-01-01", periods=rows, freq="B", name="Date")
    values = pd.Series(range(rows), index=index, dtype=float)
    return pd.DataFrame({
        "Open": 10 + values * 0.01,
        "High": 10.5 + values * 0.01,
        "Low": 9.5 + values * 0.01,
        "Close": 10.2 + values * 0.01,
        "Adj Close": 10.2 + values * 0.01,
        "Volume": 1000 + values,
    }, index=index)


def _rubix(path, *, low=0.0):
    stamp = "2026-07-16T11:30:00+00:00"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE quotes (
              id INTEGER PRIMARY KEY, ticker TEXT, last_price REAL, bid REAL,
              ask REAL, volume REAL, market_timestamp TEXT, received_at TEXT
            );
            CREATE TABLE candles_1m (
              ticker TEXT, minute TEXT, open REAL, high REAL, low REAL,
              close REAL, volume REAL, updates INTEGER,
              PRIMARY KEY(ticker, minute)
            );
            CREATE TABLE feed_metrics (
              id INTEGER PRIMARY KEY, observed_at TEXT, event TEXT,
              ticker TEXT, value REAL, detail TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO quotes VALUES (1,'COMI',100,99.9,100.1,5000,?,?)",
            (stamp, stamp),
        )
        connection.execute(
            "INSERT INTO candles_1m VALUES ('COMI',?,100,101,?,100,100,1)",
            (stamp, low),
        )
        connection.execute(
            "INSERT INTO feed_metrics VALUES (1,?,'connected',NULL,1,NULL)",
            (stamp,),
        )


class _Yahoo:
    name = "yahoo"
    delayed = True
    delay_minutes = None

    def load_history(self, *_args):
        raise AssertionError("Yahoo must never be called for current research")

    def health(self):
        return {"status": "available"}


def _pin_current_research(monkeypatch, frame, provider="eodhd"):
    """Serve CURRENT_RESEARCH_V2 history deterministically (no network).

    After the EODHD migration the swing daily history comes from the research router,
    never Yahoo; these regression tests pin that source so the invariant under test
    (the Rubix quote overlay must never mutate completed daily history) still holds.
    """
    import core.research_router as router

    def _fake(symbol, **_kw):
        out = frame.copy()
        out.attrs["market_data"] = {
            "data_domain": router.CURRENT_RESEARCH_V2, "provider": provider,
            "provider_symbol": "COMI.EGX", "price_series": "SPLIT_ADJUSTED",
            "adjustment_policy": "SPLIT_ONLY_EVENT_SPECIFIC",
            "routing_tier": "TIER_A_FORWARD_SAFE", "history_sufficient": True,
            "data_quality_status": router.EODHD_OPERATIONAL_CLEAN_WINDOW,
            "latest_completed_session": str(out.index[-1])[:10], "yahoo_used": False,
        }
        return out

    monkeypatch.setattr(router, "get_current_research_history", _fake)


def test_a_rubix_database_neither_changes_history_nor_attaches_a_quote(tmp_path, monkeypatch):
    """The feed was retired on 2026-09-10. Even a database holding a quote that
    looks current is not read: the row carries no live quote."""
    original = _daily()
    cache = LocalCacheProvider(tmp_path / "cache.sqlite", source_provider="yahoo")
    write_snapshot(cache.path, "COMI.CA", original)
    database = tmp_path / "rubix.db"
    _rubix(database, low=0.0)
    rubix = RubixSQLiteProvider(
        database,
        now=lambda: datetime(2026, 7, 16, 11, 30, tzinfo=timezone.utc),
    )
    monkeypatch.setitem(settings.data, "dashboard_provider", "rubix")
    monkeypatch.setattr(routing, "_PROVIDER_INSTANCES", {
        "rubix": rubix, "yahoo": _Yahoo(), "local_cache": cache,
    })
    _pin_current_research(monkeypatch, original)

    loaded = routing.load_history("COMI.CA", purpose="dashboard")

    pd.testing.assert_frame_equal(loaded, original, check_freq=False)
    metadata = loaded.attrs["market_data"]
    assert metadata["historical_provider"] == "eodhd"          # current research, not Yahoo
    assert metadata["data_domain"] == "CURRENT_RESEARCH_V2"
    assert metadata["yahoo_used"] is False
    assert metadata["live_quote_provider"] == "unavailable"
    assert metadata["live_quote_last"] is None
    assert metadata["rubix_overlay_mutates_history"] is False
    assert loaded.index.max() == original.index.max()
    pd.testing.assert_frame_equal(
        calculate_indicators(loaded.copy()),
        calculate_indicators(original.copy()),
        check_freq=False,
    )


def test_disconnected_rubix_does_not_erase_valid_swing_history(tmp_path, monkeypatch):
    original = _daily()
    cache = LocalCacheProvider(tmp_path / "cache.sqlite", source_provider="yahoo")
    write_snapshot(cache.path, "COMI.CA", original)
    rubix = RubixSQLiteProvider(tmp_path / "missing.db")
    monkeypatch.setitem(settings.data, "scanner_provider", "rubix")
    monkeypatch.setattr(routing, "_PROVIDER_INSTANCES", {
        "rubix": rubix, "yahoo": _Yahoo(), "local_cache": cache,
    })
    _pin_current_research(monkeypatch, original)

    loaded = routing.load_history("COMI.CA", purpose="scanner")

    assert len(loaded) == len(original)
    assert loaded.attrs["market_data"]["live_quote_available"] is False
    assert loaded.attrs["market_data"]["fallback_active"] is True


def test_data_failures_have_non_strategy_statuses():
    assert classify_failure("ABC: not enough history (20 bars; minimum 250)") == (
        "insufficient lookback", "DATA_INSUFFICIENT"
    )
    assert classify_failure("yahoo: no data found for ABC.CA") == (
        "missing historical data", "DATA_INSUFFICIENT"
    )
    assert classify_failure("provider connection timeout") == (
        "provider exception", "PROVIDER_FAILED"
    )


def test_scalping_intraday_still_reads_rubix_minutes(tmp_path):
    database = tmp_path / "rubix.db"
    _rubix(database, low=99.0)
    provider = RubixSQLiteProvider(
        database,
        now=lambda: datetime(2026, 7, 16, 11, 30, tzinfo=timezone.utc),
    )
    frame = provider.load_history("COMI.CA", "1d", "1m")
    assert len(frame) == 1
    assert frame.iloc[0]["Low"] == 99.0
    assert frame.attrs["market_data"]["provider"] == "rubix"
