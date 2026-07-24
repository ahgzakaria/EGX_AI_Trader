"""Tests for the minimal, unvalidated Twelve Data adapter skeleton.

No real TWELVEDATA_API_KEY exists in this environment. These tests only prove
the safe-failure contract (never fabricates a response without a key; never
silently proceeds), not any real API behavior.
"""

import pytest

from providers.base_provider import ProviderConfigurationError, ProviderDataError
from providers.twelvedata_provider import TwelveDataProvider


def test_health_reports_blocked_without_key():
    provider = TwelveDataProvider(api_key=None)
    health = provider.health()
    assert health["configured"] is False
    assert health["status"] == "BLOCKED_NO_API_KEY"
    assert health["tested"] is False


def test_health_reports_configured_with_key_but_still_untested():
    provider = TwelveDataProvider(api_key="fake-key-not-real")
    health = provider.health()
    assert health["configured"] is True
    assert health["tested"] is False


def test_load_history_refuses_without_key():
    provider = TwelveDataProvider(api_key=None)
    with pytest.raises(ProviderConfigurationError):
        provider.load_history("COMI.CA", "10y", "1d")


def test_load_history_refuses_even_with_fake_key_pending_real_probe():
    """A key alone must never be enough to claim a working data path."""

    provider = TwelveDataProvider(api_key="fake-key-not-real")
    with pytest.raises(ProviderDataError):
        provider.load_history("COMI.CA", "10y", "1d")


def test_never_reads_key_from_anywhere_but_constructor_or_env(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    provider = TwelveDataProvider()
    assert provider.health()["configured"] is False


def test_key_from_environment_variable(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "env-supplied-fake-key")
    provider = TwelveDataProvider()
    assert provider.health()["configured"] is True
