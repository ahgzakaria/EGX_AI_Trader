"""Historical EODHD integration tests; no network calls are performed."""

import pandas as pd
import pytest

from core import data_provider as routing
from providers.base_provider import (
    ProviderConfigurationError,
    ProviderDataError,
    ProviderSchemaError,
)
from providers.eodhd_provider import EODHDProvider, _period_start
from providers.symbol_mapping import to_eodhd_symbol


def _history():
    return [
        {
            "date": "2026-07-15", "open": 100, "high": 103,
            "low": 99, "close": 102, "adjusted_close": 101.5,
            "volume": 1000,
        },
        {
            "date": "2026-07-16", "open": 102, "high": 104,
            "low": 101, "close": 103, "adjusted_close": 102.5,
            "volume": 1200,
        },
    ]


def test_eodhd_normalizes_daily_history_and_cairo_metadata():
    calls = []

    def transport(path, params):
        calls.append((path, params))
        return _history()

    provider = EODHDProvider(api_token="test-token", transport=transport)
    frame = provider.load_history("COMI.CA", "10y", "1d")
    assert calls[0][0] == "eod/COMI.EGX"
    assert calls[0][1]["api_token"] == "test-token"
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert frame.index.tz is None
    assert list(frame.columns) == [
        "Open", "High", "Low", "Close", "Adj Close", "Volume"
    ]
    assert frame.attrs["market_data"]["exchange_timezone"] == "Africa/Cairo"
    assert frame.attrs["market_data"]["source_symbol"] == "COMI.EGX"


def test_eodhd_symbol_lookup_search_and_corporate_actions():
    def transport(path, _params):
        if path == "exchange-symbol-list/EGX":
            return [{"Code": "COMI", "Name": "Commercial International Bank"}]
        if path == "search/COMI":
            return [{"Code": "COMI.EGX"}]
        if path == "div/COMI.EGX":
            return [{"date": "2026-04-01", "value": 1.0}]
        if path == "splits/COMI.EGX":
            return [{"date": "2020-01-01", "split": "2.000000/1.000000"}]
        raise AssertionError(path)

    provider = EODHDProvider(api_token="test-token", transport=transport)
    assert provider.lookup_symbol("COMI.CA")["Code"] == "COMI"
    assert provider.lookup_symbol("NONE.CA") is None
    assert provider.search_symbols("COMI")[0]["Code"] == "COMI.EGX"
    actions = provider.corporate_actions("COMI.CA")
    assert len(actions["dividends"]) == 1
    assert len(actions["splits"]) == 1


def test_eodhd_rejects_missing_key_intraday_and_invalid_candles():
    missing = EODHDProvider(api_token=None, transport=lambda *_: _history())
    missing._api_token = None
    with pytest.raises(ProviderConfigurationError, match="EODHD_API"):
        missing.load_history("COMI.CA", "10y", "1d")

    provider = EODHDProvider(api_token="test-token", transport=lambda *_: _history())
    with pytest.raises(ProviderDataError, match="daily"):
        provider.load_history("COMI.CA", "1mo", "1m")

    invalid = _history()
    invalid[0]["low"] = 0
    bad = EODHDProvider(api_token="test-token", transport=lambda *_: invalid)
    with pytest.raises(ProviderSchemaError, match="invalid daily candles"):
        bad.load_history("COMI.CA", "10y", "1d")


def test_eodhd_is_registered_but_default_routing_is_unchanged(monkeypatch):
    routing.reset_provider_instances()
    providers = routing._provider_instances()
    assert "eodhd" in providers
    assert routing.provider_name_for("backtest") == "frozen_mubasher"
    assert routing.provider_name_for("scanner") != "eodhd"
    assert routing.provider_name_for("dashboard") != "eodhd"
    assert routing.provider_name_for("forward_testing") != "eodhd"
    routing.reset_provider_instances()


def test_eodhd_mapping_and_period_conversion_are_deterministic():
    assert to_eodhd_symbol("SWDY.CA") == "SWDY.EGX"
    assert _period_start("10y", today="2026-07-18").isoformat() == "2016-07-18"
    assert _period_start("max", today="2026-07-18") is None
