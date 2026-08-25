"""Provider contract and strict OHLCV normalization.

Every source must cross this boundary before its candles can reach indicators or
trading code.  The validator changes representation only; it never calculates,
fills, resamples, or otherwise invents market values.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import pandas as pd

from core.daily_open_integrity import classify_open


REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
OPTIONAL_COLUMNS = ("Adj Close",)


class ProviderError(RuntimeError):
    """Base class for a provider failure eligible for explicit fallback."""


class ProviderConfigurationError(ProviderError):
    """Raised when an authorized provider endpoint is not configured."""


class ProviderConnectionError(ProviderError):
    """Raised for timeout, connection, or remote endpoint failures."""


class ProviderSchemaError(ProviderError):
    """Raised when remote data cannot satisfy the frozen engine schema."""


class ProviderDataError(ProviderError):
    """Raised when a symbol is missing or history is incomplete."""


class MarketDataProvider(ABC):
    """Small provider interface shared by live and historical callers."""

    name = "base"
    delayed = False
    delay_minutes = 0

    @abstractmethod
    def load_history(self, symbol, period, interval):
        """Return a normalized DatetimeIndex OHLCV DataFrame."""

    def health(self):
        """Return non-invasive provider readiness information."""

        return {"provider": self.name, "status": "available"}


def normalize_history(frame, symbol, provider):
    """Normalize column names and index while preserving source candle values."""

    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ProviderDataError(f"{provider}: no data found for {symbol}")

    normalized = frame.copy()
    if isinstance(normalized.columns, pd.MultiIndex):
        normalized.columns = normalized.columns.get_level_values(0)

    aliases = {
        "date": "Date", "datetime": "Date", "timestamp": "Date",
        "time": "Date", "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "adjclose": "Adj Close",
        "adj_close": "Adj Close", "adjusted_close": "Adj Close",
        "volume": "Volume",
    }
    rename = {}
    for column in normalized.columns:
        key = str(column).strip().lower().replace(" ", "_")
        compact = key.replace("_", "")
        canonical = aliases.get(key) or aliases.get(compact)
        if canonical:
            rename[column] = canonical
    normalized.rename(columns=rename, inplace=True)

    if not isinstance(normalized.index, pd.DatetimeIndex):
        date_column = next(
            (name for name in ("Date", "Datetime", "Timestamp")
             if name in normalized.columns),
            None,
        )
        if date_column is None:
            raise ProviderSchemaError(
                f"{provider}: {symbol} has no datetime index or timestamp column"
            )
        normalized.index = _datetime_index(normalized.pop(date_column))
    else:
        normalized.index = _datetime_index(normalized.index)

    normalized = normalized[~normalized.index.isna()].copy()
    normalized.index.name = "Date"

    missing = [column for column in REQUIRED_COLUMNS if column not in normalized]
    if missing:
        raise ProviderSchemaError(
            f"{provider}: {symbol} missing columns: {', '.join(missing)}"
        )

    columns = [*REQUIRED_COLUMNS]
    if "Adj Close" in normalized:
        columns.insert(4, "Adj Close")
    normalized = normalized[columns].copy()
    for column in columns:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    normalized = normalized.sort_index()
    normalized = normalized[~normalized.index.duplicated(keep="last")]
    if normalized.empty:
        raise ProviderDataError(f"{provider}: no usable candles for {symbol}")

    normalized.attrs["market_data"] = {
        "provider": provider,
        "symbol": symbol,
        "received_timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    # Judged once here, where every provider's frame passes through, rather than
    # per bar in the hot path. This records what the Open column is; it does not
    # repair it, substitute it, or change a single candle value.
    normalized.attrs["open_integrity"] = classify_open(normalized)
    return normalized


def _datetime_index(values):
    """Preserve Cairo exchange wall time while returning a timezone-naive index."""

    try:
        parsed = pd.DatetimeIndex(pd.to_datetime(values, errors="coerce"))
    except (TypeError, ValueError):
        # Mixed offsets are normalized first, then presented in Cairo time.
        parsed = pd.DatetimeIndex(pd.to_datetime(values, errors="coerce", utc=True))
    if parsed.tz is not None:
        parsed = parsed.tz_convert("Africa/Cairo").tz_localize(None)
    return parsed
