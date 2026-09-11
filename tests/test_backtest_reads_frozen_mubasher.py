"""Backtests read the frozen Mubasher record, and reach Yahoo only by name.

Pins the switch from three sides: what the default resolves to, what a
backtest load actually serves, and that nothing falls back quietly -- not a
tampered file, not a missing symbol, not an unknown setting. Purposes with no
route of their own must not follow the backtest source, and the research panel
must keep building from the Yahoo archive its documented findings came from.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from config.settings_manager import DEFAULT_SETTINGS, settings
from core import data_provider as routing
from core import frozen_mubasher_store as store
import core.research_router as research_router
from providers.base_provider import ProviderError


def make_store(tmp_path, ticker="ABCD", sessions=400):
    """A one-symbol frozen store in the export format, newest row first."""
    dates = pd.bdate_range("2014-01-01", periods=sessions)
    lines = [store.EXPORT_HEADER]
    for i in reversed(range(sessions)):
        close = round(10 + i * 0.01, 3)
        volume = 1000 + i
        lines.append(f"{ticker},{dates[i].date()},{close + 0.1},{close + 0.1},"
                     f"{close - 0.1},{close},0,0,-1,{volume},{volume * close}")
    root = tmp_path / "frozen"
    (root / "export").mkdir(parents=True)
    path = root / "export" / f"{ticker}.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {"frozen_at": "2026-09-11T00:00:00+00:00", "symbols": {ticker: {
        "symbol": ticker, "file": f"export/{ticker}.csv", "format": store.EXPORT_FORMAT,
        "sha256": store.sha256_file(path), "source": "test export"}}}
    (root / store.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return root, path


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    root, path = make_store(tmp_path)
    monkeypatch.setattr(store, "FROZEN_DIR", root)
    monkeypatch.setitem(settings.data, "backtest_provider", store.PROVIDER_KEY)

    def yahoo_must_not_be_read(*_args, **_kwargs):
        raise AssertionError("the Yahoo archive was read without being named")

    monkeypatch.setattr(research_router, "get_legacy_backtest_history", yahoo_must_not_be_read)
    return root, path


def test_the_default_backtest_source_is_the_frozen_mubasher_record():
    assert DEFAULT_SETTINGS["backtest_provider"] == store.PROVIDER_KEY
    assert settings.data.get("backtest_provider") == store.PROVIDER_KEY
    assert routing.provider_name_for("backtest") == store.PROVIDER_KEY


def test_a_backtest_load_serves_the_frozen_record(frozen):
    frame = routing.load_history("ABCD.CA", purpose="backtest", period="max")
    md = frame.attrs["market_data"]
    assert md["provider"] == store.PROVIDER
    assert md["data_domain"] == store.DATA_DOMAIN
    assert md["open_policy"] == store.OPEN_POLICY
    assert md["network_used"] is False
    assert frame.index.is_monotonic_increasing
    # The first session has no previous close, so the engine's cleaning drops it.
    assert len(frame) == 399
    assert not frame[["Open", "High", "Low", "Close", "Volume"]].isna().any().any()
    assert (frame["Volume"] > 0).all()
    assert np.allclose(frame["Open"].iloc[1:].to_numpy(), frame["Close"].iloc[:-1].to_numpy())


def test_period_keeps_the_last_years_of_the_record(frozen):
    full = routing.load_history("ABCD.CA", purpose="backtest", period="max")
    year = routing.load_history("ABCD.CA", purpose="backtest", period="1y", min_bars=100)
    assert year.index[-1] == full.index[-1]
    assert year.index[0] >= full.index[-1] - pd.DateOffset(years=1)
    assert len(year) < len(full)


def test_a_changed_file_fails_instead_of_falling_back(frozen):
    _, path = frozen
    path.write_bytes(path.read_bytes().replace(b"-1,", b"-2,", 1))
    with pytest.raises(ProviderError, match="refused"):
        routing.load_history("ABCD.CA", purpose="backtest")


def test_a_symbol_the_record_lacks_fails_loudly(frozen):
    with pytest.raises(ProviderError, match="holds no"):
        routing.load_history("ZZZZ.CA", purpose="backtest")


def test_an_unknown_backtest_source_is_refused(frozen, monkeypatch):
    monkeypatch.setitem(settings.data, "backtest_provider", "eodhd")
    with pytest.raises(ProviderError, match="not a backtest record"):
        routing.load_history("ABCD.CA", purpose="backtest")


def test_an_intraday_backtest_is_refused(frozen):
    with pytest.raises(ProviderError, match="daily"):
        routing.load_history("ABCD.CA", purpose="backtest", interval="1h")


def test_the_yahoo_archive_is_read_when_named(frozen, monkeypatch):
    index = pd.bdate_range("2016-07-18", periods=300, name="Date")
    archive = pd.DataFrame({"Open": 1.0, "High": 1.1, "Low": 0.9, "Close": 1.0,
                            "Adj Close": 1.0, "Volume": 100.0}, index=index)
    archive.attrs["market_data"] = {"data_domain": "LEGACY_BACKTEST_V1",
                                    "provider": "FROZEN_YAHOO_SNAPSHOT"}
    calls = []

    def legacy(symbol, **_kwargs):
        calls.append(symbol)
        return archive.copy()

    monkeypatch.setattr(research_router, "get_legacy_backtest_history", legacy)
    with routing.backtest_source("yahoo"):
        assert routing.provider_name_for("backtest") == "yahoo"
        frame = routing.load_history("ABCD.CA", purpose="backtest")
    assert calls == ["ABCD.CA"]
    assert frame.attrs["market_data"]["data_domain"] == "LEGACY_BACKTEST_V1"
    # The override ends with its block.
    assert routing.provider_name_for("backtest") == store.PROVIDER_KEY


def test_purposes_without_a_route_do_not_follow_the_backtest_source(frozen):
    assert routing.provider_name_for("analysis") == settings.data.get("fallback_provider", "yahoo")
    assert routing.provider_name_for("analysis") != store.PROVIDER_KEY


def test_the_research_panel_builds_from_the_yahoo_archive_unless_named(monkeypatch):
    from scripts.research import panel

    seen = []
    index = pd.bdate_range("2016-07-18", periods=300, name="Date")
    rng = np.random.default_rng(0)
    close = 10 + np.cumsum(rng.normal(0, 0.1, len(index)))
    frame = pd.DataFrame({"Open": close, "High": close + 0.2, "Low": close - 0.2,
                          "Close": close, "Adj Close": close,
                          "Volume": 1000.0}, index=index)

    def fake_load_history(symbol, purpose=None, **_kwargs):
        seen.append(routing.provider_name_for(purpose))
        return frame.copy()

    monkeypatch.setattr(panel, "load_history", fake_load_history)
    panel.build(symbols=["ABCD.CA"])
    panel.build(symbols=["ABCD.CA"], source=store.PROVIDER_KEY)
    assert seen == ["yahoo", store.PROVIDER_KEY]
    assert panel.cache_path() == panel.CACHE
    assert panel.cache_path(store.PROVIDER_KEY) != panel.CACHE


def test_an_isolated_backtest_records_which_source_it_read():
    from scripts.research import isolated_backtest

    assert "backtest_provider" in isolated_backtest.RECORDED_SECTIONS
