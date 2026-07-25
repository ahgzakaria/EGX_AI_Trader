"""Market-data provider implementations for EGX AI Trader."""

from providers.base_provider import (
    MarketDataProvider,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderDataError,
    ProviderError,
    ProviderSchemaError,
    normalize_history,
)
from providers.provider_manager import ProviderManager, ProviderSelection
from providers.rubix_sqlite_provider import RubixSQLiteProvider
from providers.yahoo_provider import YahooProvider

__all__ = [
    "MarketDataProvider",
    "ProviderConfigurationError",
    "ProviderConnectionError",
    "ProviderDataError",
    "ProviderError",
    "ProviderSchemaError",
    "normalize_history",
    "ProviderManager",
    "ProviderSelection",
    "RubixSQLiteProvider",
    "YahooProvider",
]
