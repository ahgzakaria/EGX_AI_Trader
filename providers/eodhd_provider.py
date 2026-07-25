"""Historical-only EODHD market-data provider.

This module is deliberately passive: registering the provider does not alter
the active routing policy. Authentication is read from the process environment
and is never persisted or included in provider metadata or exception messages.
"""

from __future__ import annotations

from datetime import date
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from providers.base_provider import (
    MarketDataProvider,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderDataError,
    ProviderSchemaError,
    normalize_history,
)
from providers.symbol_mapping import to_egx_code, to_eodhd_symbol


class EODHDProvider(MarketDataProvider):
    """Serve EODHD daily history without participating in default routing."""

    name = "eodhd"
    delayed = True
    delay_minutes = None
    exchange = "EGX"
    exchange_timezone = "Africa/Cairo"

    def __init__(
        self,
        api_token=None,
        base_url="https://eodhd.com/api",
        timeout=30,
        transport=None,
    ):
        self._api_token = (
            api_token
            or os.getenv("EODHD_API_TOKEN")
            or os.getenv("EODHD_API_KEY")
        )
        self.base_url = str(base_url).rstrip("/")
        self.timeout = max(1, int(timeout))
        self._transport = transport
        self._exchange_symbols = None

    @staticmethod
    def map_symbol(symbol):
        return to_eodhd_symbol(symbol)

    def health(self):
        configured = bool(self._api_token)
        return {
            "provider": self.name,
            "status": "configured" if configured else "unconfigured",
            "configured": configured,
            "historical_only": True,
            "exchange": self.exchange,
            "exchange_timezone": self.exchange_timezone,
            "reason": None if configured else (
                "EODHD_API_TOKEN or EODHD_API_KEY is not configured"
            ),
        }

    def load_history(self, symbol, period, interval):
        interval_key = str(interval).strip().lower()
        if interval_key not in {"1d", "1day", "day", "d"}:
            raise ProviderDataError(
                "EODHD Phase 1 supports historical daily OHLCV only"
            )

        source_symbol = self.map_symbol(symbol)
        params = {"fmt": "json", "period": "d", "order": "a"}
        start = _period_start(period)
        if start is not None:
            params["from"] = start.isoformat()
        payload = self._request_json(f"eod/{source_symbol}", params)
        if not isinstance(payload, list) or not payload:
            raise ProviderDataError(f"eodhd: no data found for {source_symbol}")

        frame = pd.DataFrame(payload).rename(columns={
            "date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "adjusted_close": "Adj Close",
            "volume": "Volume",
        })
        normalized = normalize_history(frame, symbol, self.name)
        diagnostics = validate_daily_candles(normalized)
        if diagnostics["invalid_candles"]:
            raise ProviderSchemaError(
                f"eodhd: {source_symbol} contains "
                f"{diagnostics['invalid_candles']} invalid daily candles"
            )
        metadata = dict(normalized.attrs.get("market_data", {}))
        metadata.update({
            "source_symbol": source_symbol,
            "exchange": self.exchange,
            "exchange_timezone": self.exchange_timezone,
            "historical_only": True,
            "status": "end_of_day",
            "source_latest_timestamp": pd.Timestamp(normalized.index[-1]).isoformat(),
            "invalid_candles": 0,
        })
        normalized.attrs["market_data"] = metadata
        return normalized

    def list_exchange_symbols(self, exchange="EGX"):
        """Return EODHD's supported instruments for one exchange."""

        payload = self._request_json(
            f"exchange-symbol-list/{str(exchange).strip().upper()}",
            {"fmt": "json"},
        )
        if not isinstance(payload, list):
            raise ProviderSchemaError("eodhd: exchange symbol response is not a list")
        return payload

    def lookup_symbol(self, symbol):
        """Look up one engine symbol in the authoritative EGX instrument list."""

        if self._exchange_symbols is None:
            self._exchange_symbols = self.list_exchange_symbols(self.exchange)
        code = to_egx_code(symbol)
        return next(
            (
                row for row in self._exchange_symbols
                if str(row.get("Code", row.get("code", ""))).upper() == code
            ),
            None,
        )

    def search_symbols(self, query):
        payload = self._request_json(
            f"search/{str(query).strip()}", {"fmt": "json"}
        )
        if not isinstance(payload, list):
            raise ProviderSchemaError("eodhd: symbol search response is not a list")
        return payload

    def corporate_actions(self, symbol, start=None, end=None):
        """Return dividends and splits when the account plan exposes them."""

        source_symbol = self.map_symbol(symbol)
        params = {"fmt": "json"}
        if start:
            params["from"] = str(start)
        if end:
            params["to"] = str(end)
        dividends = self._request_json(f"div/{source_symbol}", params)
        splits = self._request_json(f"splits/{source_symbol}", params)
        if not isinstance(dividends, list) or not isinstance(splits, list):
            raise ProviderSchemaError(
                f"eodhd: invalid corporate-action response for {source_symbol}"
            )
        return {"dividends": dividends, "splits": splits}

    def _request_json(self, path, params):
        token = self._require_token()
        safe_params = dict(params)
        safe_params["api_token"] = token
        if self._transport is not None:
            try:
                return self._transport(path, dict(safe_params))
            except (ProviderDataError, ProviderSchemaError):
                raise
            except Exception as error:
                raise ProviderConnectionError(
                    f"EODHD request failed for {path}: {type(error).__name__}"
                ) from error

        url = f"{self.base_url}/{path.lstrip('/')}?{urlencode(safe_params)}"
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            if error.code in {401, 403}:
                raise ProviderConfigurationError(
                    f"EODHD rejected authentication or entitlement for {path} "
                    f"(HTTP {error.code})"
                ) from error
            raise ProviderConnectionError(
                f"EODHD HTTP {error.code} for {path}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise ProviderConnectionError(
                f"EODHD connection failed for {path}: {type(error).__name__}"
            ) from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProviderSchemaError(
                f"EODHD returned invalid JSON for {path}"
            ) from error
        if isinstance(payload, dict) and any(
            key in payload for key in ("error", "message", "errors")
        ):
            # The remote message is intentionally not included because some
            # gateways reflect request parameters containing the API token.
            raise ProviderDataError(f"EODHD returned an error for {path}")
        return payload

    def _require_token(self):
        if not self._api_token:
            raise ProviderConfigurationError(
                "EODHD_API_TOKEN or EODHD_API_KEY is not configured"
            )
        return self._api_token


def validate_daily_candles(frame):
    """Report impossible OHLCV rows without filling or changing source data."""

    prices = frame[["Open", "High", "Low", "Close"]]
    numeric_missing = prices.isna().any(axis=1) | frame["Volume"].isna()
    non_positive = prices.le(0).any(axis=1)
    negative_volume = frame["Volume"].lt(0)
    range_error = (
        frame["High"].lt(prices[["Open", "Close", "Low"]].max(axis=1))
        | frame["Low"].gt(prices[["Open", "Close", "High"]].min(axis=1))
    )
    invalid = numeric_missing | non_positive | negative_volume | range_error
    return {
        "row_count": int(len(frame)),
        "invalid_candles": int(invalid.sum()),
        "missing_values": int(numeric_missing.sum()),
        "non_positive_prices": int(non_positive.sum()),
        "negative_volume": int(negative_volume.sum()),
        "ohlc_range_errors": int(range_error.sum()),
    }


def _period_start(period, today=None):
    value = str(period or "max").strip().lower()
    if value in {"max", "all", "full", ""}:
        return None
    today = pd.Timestamp(today or date.today()).normalize()
    if value == "ytd":
        return date(today.year, 1, 1)
    units = (("y", "years"), ("mo", "months"))
    for suffix, unit in units:
        if value.endswith(suffix):
            try:
                amount = int(value[: -len(suffix)])
            except ValueError as error:
                raise ProviderDataError(f"Unsupported EODHD period: {period}") from error
            return (today - pd.DateOffset(**{unit: amount})).date()
    raise ProviderDataError(f"Unsupported EODHD period: {period}")
