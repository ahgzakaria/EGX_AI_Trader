"""The frozen Yahoo snapshot can be read, and nothing in the program can change it.

Every backtest here was measured on the ``yahoo`` rows of the market-data cache.
Six of them were replaced after the freeze by a live download stored over the
top. These tests pin the three ways the program reaches those rows -- the
backtest path, the Rubix/TickerChart warm-up seed, and the ``yahoo`` fallback --
to reading only: no download, and not one byte of the cache file changed.
"""

from __future__ import annotations

import hashlib
import inspect

import pandas as pd
import pytest
import yfinance

import core.data_provider as routing
import core.research_router as router
import providers.local_cache_provider as local_cache_module
from config.settings_manager import settings
from providers.base_provider import ProviderDataError, ProviderError
from providers.frozen_yahoo_snapshot import FrozenYahooSnapshotProvider
from providers.local_cache_provider import LocalCacheProvider
from providers.yahoo_provider import YahooProvider
from tests.fixtures.frozen_snapshot import expired_timestamp, write_snapshot

FREEZE = "2026-07-22"


def _bars(start, end):
    index = pd.bdate_range(start, end, name="Date")
    values = pd.Series(range(len(index)), index=index, dtype=float)
    return pd.DataFrame({
        "Open": 10 + values * 0.01, "High": 10.5 + values * 0.01,
        "Low": 9.5 + values * 0.01, "Close": 10.2 + values * 0.01,
        "Adj Close": 10.2 + values * 0.01, "Volume": 1000 + values,
    }, index=index)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    """Two symbols frozen on FREEZE, one rewritten past it; every entry expired.

    The backtest path opens the cache at its default location, so that
    constructor is pointed here too.
    """

    path = tmp_path / "market_data_cache.sqlite"
    for symbol in ("SWDY.CA", "ETEL.CA"):
        write_snapshot(path, symbol, _bars("2025-01-01", FREEZE),
                       fetched_at=expired_timestamp())
    write_snapshot(path, "COMI.CA", _bars("2025-02-03", "2026-09-06"),
                   fetched_at=expired_timestamp())
    monkeypatch.setattr(local_cache_module, "LocalCacheProvider",
                        lambda *_a, **_k: LocalCacheProvider(path))
    return path


@pytest.fixture
def no_yahoo_download(monkeypatch):
    attempts = []

    def forbidden(*args, **_kwargs):
        attempts.append(args)
        raise AssertionError("a Yahoo download was attempted")

    monkeypatch.setattr(yfinance, "download", forbidden)
    monkeypatch.setattr(YahooProvider, "load_history", forbidden)
    return attempts


@pytest.fixture
def providers(snapshot, tmp_path, monkeypatch):
    """The real provider instances, built from settings pointed at the snapshot."""

    market = settings.get("market_data")
    monkeypatch.setitem(market, "cache_path", str(snapshot))
    monkeypatch.setitem(market, "rubix_db_path", str(tmp_path / "no-rubix.db"))
    monkeypatch.setitem(market, "tickerchart_db_path", "")
    monkeypatch.setenv("RUBIX_DB_PATH", str(tmp_path / "no-rubix.db"))
    monkeypatch.delenv("TICKERCHART_DB_PATH", raising=False)
    monkeypatch.setattr(routing, "_PROVIDER_INSTANCES", None)
    return routing._provider_instances()


def test_the_cache_refuses_to_write_yahoo_rows(snapshot):
    before = _digest(snapshot)

    with pytest.raises(ProviderDataError, match="frozen"):
        LocalCacheProvider(snapshot).store(
            "yahoo", "SWDY.CA", "10y", "1d", _bars("2016-01-01", "2026-09-10"))

    assert _digest(snapshot) == before


def test_routing_holds_no_yahoo_downloader(providers):
    assert isinstance(providers["yahoo"], FrozenYahooSnapshotProvider)
    assert "YahooProvider" not in inspect.getsource(routing)


def test_the_backtest_path_cannot_download_or_write(providers, snapshot,
                                                    no_yahoo_download):
    before = _digest(snapshot)

    frame = routing.load_history("SWDY.CA", purpose="backtest")
    assert frame.attrs["market_data"]["data_domain"] == router.LEGACY_BACKTEST_V1
    with pytest.raises(ProviderError):
        routing.load_history("ABUK.CA", purpose="backtest")     # not in the snapshot

    assert no_yahoo_download == []
    assert _digest(snapshot) == before


def test_the_warm_up_seed_cannot_download_or_write(providers, snapshot,
                                                   no_yahoo_download):
    before = _digest(snapshot)

    for name in ("rubix", "tickerchart"):
        seed = providers[name]._history_loader
        # Expired entries used to trigger a download; now they are served as is.
        assert seed("SWDY.CA", "10y", "1d").index.max() == pd.Timestamp(FREEZE)
        with pytest.raises(ProviderDataError):
            seed("ABUK.CA", "10y", "1d")

    overlay = _bars("2026-07-23", "2026-07-23")
    merged = providers["rubix"]._merge_historical_seed("COMI.CA", "10y", "1d", overlay)
    assert list(merged.index[merged.index > pd.Timestamp(FREEZE)]) == [
        pd.Timestamp("2026-07-23")]

    assert no_yahoo_download == []
    assert _digest(snapshot) == before


def test_the_yahoo_fallback_cannot_download_or_write(providers, snapshot,
                                                     no_yahoo_download, monkeypatch):
    # TickerChart is unconfigured here, so the dashboard route falls over to "yahoo".
    monkeypatch.setitem(settings.data, "dashboard_provider", "tickerchart")
    monkeypatch.setitem(settings.data, "fallback_provider", "yahoo")
    before = _digest(snapshot)

    frame = routing.load_history("SWDY.CA", purpose="dashboard")
    assert frame.attrs["market_data"]["effective_provider"] == "yahoo"
    assert frame.attrs["market_data"]["fallback_active"] is True
    with pytest.raises(ProviderError):
        routing.load_history("ABUK.CA", purpose="dashboard")

    assert no_yahoo_download == []
    assert _digest(snapshot) == before


def test_rows_after_the_freeze_are_ignored_not_deleted(snapshot):
    rewritten = router.get_legacy_backtest_history("COMI.CA")
    metadata = rewritten.attrs["market_data"]
    assert rewritten.index.max() == pd.Timestamp(FREEZE)
    assert metadata["snapshot_freeze_session"] == FREEZE
    assert metadata["snapshot_rows_after_freeze_ignored"] == len(
        pd.bdate_range("2026-07-23", "2026-09-06"))

    control = router.get_legacy_backtest_history("SWDY.CA")
    assert control.attrs["market_data"]["snapshot_rows_after_freeze_ignored"] == 0
    assert len(control) == len(_bars("2025-01-01", FREEZE))

    on_disk = LocalCacheProvider(snapshot).inspect_cached("yahoo", "COMI.CA", "10y", "1d")
    assert pd.Timestamp(on_disk["maximum_date"]) == _bars("2025-02-03", "2026-09-06").index.max()


def test_the_freeze_is_the_session_most_symbols_end_on(tmp_path):
    path = tmp_path / "cache.sqlite"
    for symbol, end in (("A.CA", "2026-07-22"), ("B.CA", "2026-07-22"),
                        ("C.CA", "2026-07-22"), ("D.CA", "2026-09-06")):
        write_snapshot(path, symbol, _bars("2026-01-01", end))
    assert FrozenYahooSnapshotProvider(LocalCacheProvider(path)).freeze_session(
        "10y", "1d").startswith("2026-07-22")

    tied = tmp_path / "tied.sqlite"
    for symbol, end in (("A.CA", "2026-07-22"), ("B.CA", "2026-07-16")):
        write_snapshot(tied, symbol, _bars("2026-01-01", end))
    # An ambiguous snapshot is cut short, never extended.
    assert FrozenYahooSnapshotProvider(LocalCacheProvider(tied)).freeze_session(
        "10y", "1d").startswith("2026-07-16")


def test_the_retired_refresh_script_refuses_before_any_download(no_yahoo_download):
    from scripts import refresh_yahoo_cache

    with pytest.raises(SystemExit, match="retired"):
        refresh_yahoo_cache.refresh()
    assert no_yahoo_download == []
