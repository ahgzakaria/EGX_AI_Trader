"""Provider routing tests that never alter strategy or indicator behavior."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pandas as pd
import pytest

import core.data_provider as routing
from config.settings_manager import settings
from indicators.technical import calculate_indicators
import strategy.decision_engine as decision_engine
from providers.base_provider import (
    ProviderConnectionError,
    ProviderDataError,
    ProviderSchemaError,
    normalize_history,
)
from providers.local_cache_provider import LocalCacheProvider
from providers.provider_manager import ProviderManager
from providers.rubix_sqlite_provider import RubixSQLiteProvider
from providers.tickerchart_provider import TickerChartProvider
from providers.symbol_mapping import (
    to_egx_code,
    to_engine_symbol,
    to_rubix_symbol,
    to_tickerchart_symbol,
)
from providers.yahoo_provider import YahooProvider
from services.dataset_archive import (
    DatasetArchive,
    activate_archive,
    deactivate_archive,
)


def candle_frame(rows=300):
    index = pd.date_range("2025-01-01", periods=rows, freq="D", name="Date")
    values = pd.Series(range(rows), index=index, dtype=float)
    return pd.DataFrame({
        "Open": 100 + values * 0.1,
        "High": 101 + values * 0.1,
        "Low": 99 + values * 0.1,
        "Close": 100.5 + values * 0.1,
        "Adj Close": 100.5 + values * 0.1,
        "Volume": 1000 + values,
    }, index=index)


class FakeProvider:
    delayed = False
    delay_minutes = 0

    def __init__(self, name, frame=None, error=None):
        self.name = name
        self.frame = frame
        self.error = error
        self.calls = []

    def load_history(self, symbol, period, interval):
        self.calls.append((symbol, period, interval))
        if self.error:
            raise self.error
        result = self.frame.copy()
        result.attrs["market_data"] = {
            "provider": self.name,
            "received_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return result

    def health(self):
        return {"provider": self.name, "status": "available"}


def test_schema_normalization_preserves_exact_ohlcv_values():
    source = candle_frame(3).reset_index().rename(columns={
        "Date": "timestamp", "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
    })
    result = normalize_history(source, "TEST.CA", "test")
    assert isinstance(result.index, pd.DatetimeIndex)
    assert list(result.columns) == [
        "Open", "High", "Low", "Close", "Adj Close", "Volume"
    ]
    assert result.iloc[-1]["Close"] == source.iloc[-1]["close"]


def test_default_routes_use_rubix_live_and_eodhd_history():
    """Rubix owns every live route; EODHD owns history. Yahoo owns nothing.

    Rubix cannot take the historical route as well: it holds only a few weeks
    of intraday data, far short of what daily indicators and backtests read.
    """

    assert routing.provider_name_for("scanner") == "rubix"
    assert routing.provider_name_for("dashboard") == "rubix"
    assert routing.provider_name_for("forward_testing") == "rubix"
    assert routing.provider_name_for("backtest") == "eodhd"


def test_yahoo_is_not_routed_anywhere():
    """The operational rule, pinned: no route may resolve to Yahoo."""

    for route in ("scanner", "dashboard", "forward_testing", "backtest"):
        assert routing.provider_name_for(route) != "yahoo", route


def test_schema_validation_rejects_missing_volume():
    with pytest.raises(ProviderSchemaError, match="Volume"):
        normalize_history(candle_frame(3).drop(columns=["Volume"]), "X", "test")


def test_yahoo_provider_uses_injected_downloader_and_normalizes():
    calls = []

    def downloader(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return candle_frame(5)

    result = YahooProvider(downloader=downloader).load_history("COMI.CA", "10y", "1d")
    assert calls[0][0] == "COMI.CA"
    assert calls[0][1]["auto_adjust"] is False
    assert result.attrs["market_data"]["provider"] == "yahoo"


def _adapter_database(path, symbols=("COMI.EGY",), received=None, complete=True):
    received = received or datetime(2026, 7, 13, 10, 30, tzinfo=timezone.utc)
    exchange = received - timedelta(minutes=15)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE quotes (
                id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, last_price REAL,
                bid REAL, ask REAL, volume REAL, exchange_timestamp TEXT NOT NULL,
                received_at TEXT NOT NULL, latency_ms REAL NOT NULL,
                sequence INTEGER, raw_json TEXT NOT NULL
            );
            CREATE TABLE candles (
                ticker TEXT NOT NULL, interval_minutes INTEGER NOT NULL,
                bucket_start TEXT NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
                low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
                updates INTEGER NOT NULL
            );
            CREATE TABLE metrics (
                id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, name TEXT NOT NULL,
                ticker TEXT, value REAL, detail TEXT
            );
            """
        )
        for symbol in symbols:
            raw = {
                "topic": f"QO.{symbol}", "lasttradeprice": 101.5,
                "open": 100.0, "high": 102.0, "low": 99.0,
                "volume": 123456,
            }
            if not complete:
                raw.pop("high")
            connection.execute(
                """INSERT INTO quotes
                   (ticker,last_price,bid,ask,volume,exchange_timestamp,received_at,
                    latency_ms,sequence,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (symbol, 101.5, 101.4, 101.6, 123456, exchange.isoformat(),
                 received.isoformat(), 900000, None, json.dumps(raw)),
            )
            connection.execute(
                "INSERT INTO metrics(recorded_at,name,value) VALUES (?,?,?)",
                (received.isoformat(), "connect_success", 1),
            )
    return received


def test_symbol_mapping_is_central_and_bidirectional():
    for engine, code in (("COMI.CA", "COMI"), ("SWDY.CA", "SWDY"), ("FWRY.CA", "FWRY")):
        assert to_egx_code(engine) == code
        assert to_tickerchart_symbol(engine) == f"{code}.EGY"
        assert to_rubix_symbol(engine) == code
        assert to_engine_symbol(f"{code}.EGY") == engine


def _rubix_database(path, received=None, symbols=("COMI",), complete=True):
    received = received or datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
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
            """
        )
        if not complete:
            connection.execute("ALTER TABLE quotes RENAME TO invalid_quotes")
            return received
        for symbol in symbols:
            for offset, values in enumerate((
                (100.0, 101.0, 99.5, 100.5, 10.0),
                (100.5, 102.0, 100.0, 101.5, 20.0),
            )):
                stamp = received + timedelta(minutes=offset)
                connection.execute(
                    """INSERT INTO quotes
                       (ticker,last_price,bid,ask,volume,market_timestamp,
                        received_at,exchange,sequence,change_percent,has_feed_timestamp)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (symbol, values[3], values[3] - .01, values[3] + .01, 1000,
                     stamp.isoformat(), stamp.isoformat(), "CASE", offset, 0, 1),
                )
                connection.execute(
                    """INSERT INTO candles_1m
                       (ticker,minute,open,high,low,close,volume,updates)
                       VALUES (?,?,?,?,?,?,?,1)""",
                    (symbol, stamp.isoformat(), *values),
                )
        connection.execute(
            "INSERT INTO feed_metrics(observed_at,event,value) VALUES (?,?,?)",
            (received.isoformat(), "connect_success", 1),
        )
    return received


def test_rubix_sqlite_is_read_only_and_normalizes_daily_overlay(tmp_path):
    db = tmp_path / "rubix.db"
    received = _rubix_database(db)
    with sqlite3.connect(db) as connection:
        before = connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        tables_before = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()

    provider = RubixSQLiteProvider(
        db_path=db,
        now=lambda: received + timedelta(minutes=2),
        history_loader=lambda *_: candle_frame(),
    )
    health = provider.health()
    assert health["status"] == provider.FRESH
    assert health["database_status"] == "READ_ONLY_OK"
    result = provider.load_history("COMI.CA", "10y", "1d")
    assert result.iloc[-1][["Open", "High", "Low", "Close", "Volume"]].tolist() == [
        100.0, 102.0, 99.5, 101.5, 30.0,
    ]
    assert result.attrs["market_data"]["rubix_overlay_active"] is True
    assert result.attrs["market_data"]["adapter_interface"] == "sqlite_read_only"

    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == before
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall() == tables_before


def test_rubix_missing_symbol_schema_and_stale_are_explicit(tmp_path):
    db = tmp_path / "rubix.db"
    received = _rubix_database(db)
    fresh = RubixSQLiteProvider(
        db_path=db, now=lambda: received + timedelta(minutes=1)
    )
    with pytest.raises(ProviderDataError, match="no data for NONE"):
        fresh.load_history("NONE.CA", "10y", "1d")

    stale = RubixSQLiteProvider(
        db_path=db, now=lambda: received + timedelta(hours=2),
        stale_after_minutes=5,
    )
    assert stale.health()["status"] == stale.STALE
    stale_frame = stale.load_history("COMI.CA", "10y", "1d")
    assert stale_frame.attrs["market_data"]["freshness"] == "STALE"
    assert "during the open session" in stale_frame.attrs["market_data"]["freshness_warning"]

    bad = tmp_path / "bad.db"
    _rubix_database(bad, complete=False)
    assert RubixSQLiteProvider(db_path=bad).health()["schema_valid"] is False


def test_provider_manager_uses_rubix_only_when_newer_than_yahoo():
    class FrameProvider:
        def __init__(self, name, latest, source=None):
            self.name = name
            self.prefer_only_if_newer = name == "rubix"
            self.frame = candle_frame(2)
            self.frame.index = pd.DatetimeIndex([
                pd.Timestamp(latest) - pd.Timedelta(days=1), pd.Timestamp(latest)
            ])
            self.frame.attrs["market_data"] = {}
            if source:
                self.frame.attrs["market_data"]["source_latest_timestamp"] = source

    yahoo = FrameProvider("yahoo", "2026-07-09")
    rubix = FrameProvider(
        "rubix", "2026-07-13", source="2026-07-13T08:30:00+00:00"
    )
    manager = ProviderManager(
        {"rubix": rubix, "yahoo": yahoo}, loader=lambda provider: provider.frame.copy()
    )
    selected = manager.load_history("rubix", "yahoo")
    assert selected.effective == "rubix"
    assert selected.fallback_active is False

    rubix.frame.attrs["market_data"]["source_latest_timestamp"] = (
        "2026-07-08T08:30:00+00:00"
    )
    selected = manager.load_history("rubix", "yahoo")
    assert selected.effective == "yahoo"
    assert selected.fallback_active is True
    assert "not newer" in selected.reason


def test_tickerchart_adapter_discovery_and_sqlite_history(tmp_path):
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    for name in ("adapter.py", "protocol.py", "storage.py"):
        (adapter / name).write_text("# read-only fixture\n", encoding="utf-8")
    db = tmp_path / "quotes.sqlite3"
    received = _adapter_database(db, ("COMI.EGY", "SWDY.EGY", "FWRY.EGY"))
    provider = TickerChartProvider(
        db_path=db, adapter_path=adapter, now=lambda: received + timedelta(minutes=1),
        history_loader=lambda *_: candle_frame(),
    )

    discovery = provider.discover_adapter()
    assert discovery["available"] is True
    health = provider.health()
    assert health["connection_state"] == provider.ACTIVE
    assert health["operational_state"] == provider.DELAYED
    assert health["schema_valid"] is True
    assert health["symbol_count"] == 3

    result = provider.load_history("COMI.CA", "10y", "1d")
    assert list(result.columns) == [
        "Open", "High", "Low", "Close", "Adj Close", "Volume"
    ]
    assert result.iloc[-1]["Close"] == 101.5
    assert result.attrs["market_data"]["tickerchart_overlay_active"] is True
    assert result.attrs["market_data"]["delay_minutes"] == 15


def test_tickerchart_stale_missing_symbol_and_no_false_active(tmp_path):
    db = tmp_path / "stale.sqlite3"
    received = _adapter_database(db)
    stale = TickerChartProvider(
        db_path=db, now=lambda: received + timedelta(days=2),
        stale_after_minutes=60,
    )
    assert stale.health()["operational_state"] == stale.STALE
    with pytest.raises(ProviderDataError, match="minutes old"):
        stale.load_history("COMI.CA", "10y", "1d")

    fresh = TickerChartProvider(
        db_path=db, now=lambda: received + timedelta(minutes=1),
    )
    with pytest.raises(ProviderDataError, match="no data for NONE.EGY"):
        fresh.load_history("NONE.CA", "10y", "1d")

    empty = tmp_path / "empty.sqlite3"
    _adapter_database(empty, symbols=())
    empty_health = TickerChartProvider(db_path=empty).health()
    assert empty_health["connection_state"] == TickerChartProvider.UNAVAILABLE
    assert empty_health["authenticated_session_available"] is False


def test_tickerchart_health_reports_connected_preopen_wait_without_false_data(tmp_path):
    db = tmp_path / "waiting.sqlite3"
    connected = datetime(2026, 7, 13, 6, 29, tzinfo=timezone.utc)
    _adapter_database(db, received=connected)
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM quotes")

    provider = TickerChartProvider(
        db_path=db,
        now=lambda: datetime(2026, 7, 13, 6, 30, tzinfo=timezone.utc),
    )
    health = provider.health()
    assert health["operational_state"] == provider.WAITING
    assert health["connection_state"] == provider.WAITING
    assert health["quote_count"] == 0
    assert health["schema_valid"] is True
    with pytest.raises(ProviderDataError, match="no data"):
        provider.load_history("COMI.CA", "10y", "1d")


def test_tickerchart_schema_failure_is_explicit(tmp_path):
    db = tmp_path / "bad.sqlite3"
    received = _adapter_database(db, complete=False)
    provider = TickerChartProvider(
        db_path=db, now=lambda: received + timedelta(minutes=1),
    )
    with pytest.raises(ProviderSchemaError, match="complete daily OHLCV"):
        provider.load_history("COMI.CA", "10y", "1d")


def test_sqlite_cache_roundtrip_and_expiration(tmp_path):
    cache = LocalCacheProvider(tmp_path / "market.sqlite", source_provider="yahoo")
    frame = candle_frame(3)
    frame.attrs["market_data"] = {
        "provider": "yahoo",
        "received_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    cache.store("yahoo", "COMI.CA", "10y", "1d", frame)
    loaded = cache.load_cached("yahoo", "COMI.CA", "10y", "1d")
    pd.testing.assert_frame_equal(loaded, frame, check_freq=False, check_dtype=False)
    assert loaded.attrs["market_data"]["cache_hit"] is True

    expired = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with sqlite3.connect(cache.path) as connection:
        connection.execute("UPDATE market_data_entries SET fetched_at=?", (expired,))
    with pytest.raises(ProviderDataError, match="cache expired"):
        cache.load_cached("yahoo", "COMI.CA", "10y", "1d")


def test_dashboard_route_falls_back_and_backtest_stays_on_yahoo(tmp_path, monkeypatch):
    ticker = FakeProvider(
        "tickerchart", error=ProviderConnectionError("adapter unavailable")
    )
    ticker.delayed = True
    ticker.delay_minutes = 15
    yahoo = FakeProvider("yahoo", frame=candle_frame())
    yahoo.delayed = True
    yahoo.delay_minutes = None
    cache = LocalCacheProvider(tmp_path / "routing.sqlite", source_provider="tickerchart")
    monkeypatch.setitem(settings.data, "dashboard_provider", "tickerchart")
    monkeypatch.setitem(settings.data, "backtest_provider", "yahoo")
    monkeypatch.setitem(settings.data, "fallback_provider", "yahoo")
    monkeypatch.setattr(routing, "_PROVIDER_INSTANCES", {
        "tickerchart": ticker, "yahoo": yahoo, "local_cache": cache,
    })

    dashboard = routing.load_history("COMI.CA", purpose="dashboard")
    assert dashboard.attrs["market_data"]["effective_provider"] == "yahoo"
    assert dashboard.attrs["market_data"]["fallback_active"] is True
    assert len(ticker.calls) == 1 and len(yahoo.calls) == 1

    # After the EODHD migration the backtest purpose is served from the FROZEN Yahoo
    # snapshot (local cache, immutable, NO network) — the legacy domain.
    cache.store("yahoo", "SWDY.CA", "10y", "1d", candle_frame())
    import providers.local_cache_provider as local_cache_module

    monkeypatch.setattr(
        local_cache_module,
        "LocalCacheProvider",
        lambda *args, **kwargs: cache,
    )
    backtest = routing.load_history("SWDY.CA", purpose="backtest")
    md = backtest.attrs["market_data"]
    assert md["data_domain"] == "LEGACY_BACKTEST_V1"
    assert md["provider"] == "FROZEN_YAHOO_SNAPSHOT"
    assert md["immutable"] is True
    # no additional live Yahoo download happened for the legacy snapshot
    assert len(ticker.calls) == 1 and len(yahoo.calls) == 1


def test_dashboard_health_never_shows_false_tickerchart_active(tmp_path, monkeypatch):
    ticker = FakeProvider("tickerchart", error=ProviderConnectionError("offline"))
    ticker.delayed = True
    ticker.delay_minutes = 15
    ticker.health = lambda: {
        "provider": "tickerchart", "status": "TICKERCHART_UNAVAILABLE",
        "connection_state": "TICKERCHART_UNAVAILABLE", "reason": "DB missing",
        "delayed": True, "delay_minutes": 15,
    }
    yahoo = FakeProvider("yahoo", frame=candle_frame())
    cache = LocalCacheProvider(tmp_path / "status.sqlite", source_provider="tickerchart")
    monkeypatch.setitem(settings.data, "dashboard_provider", "tickerchart")
    monkeypatch.setitem(settings.data, "fallback_provider", "yahoo")
    monkeypatch.setattr(routing, "_PROVIDER_INSTANCES", {
        "tickerchart": ticker, "yahoo": yahoo, "local_cache": cache,
    })

    summary = routing.summarize_frames([], purpose="dashboard")
    assert summary["effective_provider"] == "yahoo"
    assert summary["fallback_active"] is True
    assert summary["provider_status"] == "YAHOO_FALLBACK"
    assert summary["fallback_reasons"] == ["DB missing"]


def test_empty_dashboard_summary_preserves_provider_health_timestamps(monkeypatch):
    exchange = "2026-07-14T11:34:31+00:00"
    received = "2026-07-14T11:35:12+00:00"
    rubix = FakeProvider("rubix", frame=candle_frame())
    rubix.health = lambda: {
        "provider": "rubix", "status": "RUBIX_FRESH",
        "freshness": "FRESH", "latest_exchange_timestamp": exchange,
        "latest_received_timestamp": received,
        "database_status": "READ_ONLY_OK",
    }
    monkeypatch.setitem(settings.data, "dashboard_provider", "rubix")
    monkeypatch.setattr(routing, "_PROVIDER_INSTANCES", {"rubix": rubix})

    summary = routing.summarize_frames([], purpose="dashboard")
    assert summary["latest_exchange_timestamp"] == exchange
    assert summary["latest_received_timestamp"] == received


def test_provider_comparison_archives_raw_candidates_without_collision(
    tmp_path, monkeypatch,
):
    rubix_frame = candle_frame()
    rubix_frame.index = rubix_frame.index + pd.Timedelta(days=10)
    rubix = FakeProvider("rubix", frame=rubix_frame)
    rubix.prefer_only_if_newer = True
    yahoo = FakeProvider("yahoo", frame=candle_frame())
    cache = LocalCacheProvider(tmp_path / "routing.sqlite", source_provider="rubix")
    monkeypatch.setitem(settings.data, "dashboard_provider", "rubix")
    monkeypatch.setitem(settings.data, "fallback_provider", "yahoo")
    monkeypatch.setattr(routing, "_PROVIDER_INSTANCES", {
        "rubix": rubix, "yahoo": yahoo, "local_cache": cache,
    })
    archive = DatasetArchive(tmp_path / "run", {}, symbols=("COMI.CA",))
    token = activate_archive(archive)
    try:
        result = routing.load_history("COMI.CA", purpose="dashboard")
        manifest = archive.finalize()
    finally:
        deactivate_archive(token)

    assert result.attrs["market_data"]["effective_provider"] == "rubix"
    raw_symbols = {
        record["symbol"] for record in manifest["records"]
        if record["stage"] == "raw"
    }
    assert raw_symbols == {
        "COMI.CA__source_rubix", "COMI.CA__source_yahoo",
    }
    normalized = [
        record for record in manifest["records"]
        if record["stage"] == "normalized"
    ]
    assert [record["symbol"] for record in normalized] == ["COMI.CA"]


def test_identical_candles_produce_identical_indicators_and_decisions(monkeypatch):
    original = candle_frame().drop(columns=["Adj Close"])
    normalized = normalize_history(original, "TEST.CA", "test")
    expected = calculate_indicators(original.copy())
    actual = calculate_indicators(normalized.copy())
    pd.testing.assert_frame_equal(actual, expected, check_freq=False)
    monkeypatch.setattr(
        decision_engine,
        "analyze_market",
        lambda *_args, **_kwargs: {
            "Passed": True, "Regime": "BULL", "Reasons": []
        },
    )
    assert decision_engine.evaluate(actual, len(actual) - 1) == decision_engine.evaluate(
        expected, len(expected) - 1
    )
