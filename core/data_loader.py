"""Backward-compatible import shim for the unified provider entry point."""

from core.data_provider import load_history, reset_provider_instances


def load_data(symbol, period=None, interval=None, purpose=None, **kwargs):
    return load_history(
        symbol, period=period, interval=interval, purpose=purpose, **kwargs
    )


def reset_cache():
    """Refresh provider objects without deleting the persistent SQLite cache."""

    reset_provider_instances()
