"""Minimal, secure Twelve Data adapter structure ONLY.

Per AUTOMATED_PROVIDER_CAPABILITY_PROBE.md, no TWELVEDATA_API_KEY exists in
this environment and no real request has ever been made to Twelve Data from
this project. This module is deliberately NOT a full validated provider:

  * ``load_history`` refuses to run without a real key (never fabricates data).
  * The request/parsing logic below follows Twelve Data's public REST
    documentation (``/time_series``, ``/symbol_search``) but is UNVERIFIED
    against any real response. Treat every parsing assumption here as a
    hypothesis until Phase 1 (real capability probe) actually passes.
  * Nothing in this repository imports or registers this provider anywhere in
    production routing (core/data_provider.py is unchanged). It exists only so
    that, the moment a real TWELVEDATA_API_KEY is supplied, a capability probe
    script can immediately attempt a real request without writing this
    boilerplate under time pressure.

Do not extend this file with coverage/reconciliation/shadow-mode logic until
a real key has produced at least one successful, inspected response.
"""

from __future__ import annotations

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
from providers.symbol_mapping import to_egx_code


class TwelveDataProvider(MarketDataProvider):
    """UNVALIDATED Twelve Data adapter skeleton. See module docstring."""

    name = "twelvedata"
    delayed = None  # Undisclosed until a real response is inspected.
    delay_minutes = None
    default_exchange = "XCAI"  # EGX's MIC per ISO 10383; unverified against Twelve Data's own registry.

    def __init__(self, api_key=None, base_url="https://api.twelvedata.com", timeout=30):
        self._api_key = api_key or os.getenv("TWELVEDATA_API_KEY")
        self.base_url = str(base_url).rstrip("/")
        self.timeout = max(1, int(timeout))

    def health(self):
        configured = bool(self._api_key)
        return {
            "provider": self.name,
            "status": "configured" if configured else "BLOCKED_NO_API_KEY",
            "configured": configured,
            "tested": False,
            "reason": None if configured else (
                "TWELVEDATA_API_KEY is not set; no request has been made"
            ),
        }

    def load_history(self, symbol, period, interval):
        """Refuses to run without a real key. UNVERIFIED beyond that point."""

        self._require_key()
        raise ProviderDataError(
            "TwelveDataProvider is an unverified skeleton: a real "
            "TWELVEDATA_API_KEY is now configured, but no capability probe "
            "has run against a live response yet. Run the Phase 1 probe "
            "before trusting this path."
        )

    def symbol_search(self, query):
        """Official documented lookup endpoint -- unverified parsing."""

        payload = self._request_json("symbol_search", {"symbol": str(query)})
        if not isinstance(payload, dict) or "data" not in payload:
            raise ProviderSchemaError("twelvedata: unexpected symbol_search response shape")
        return payload["data"]

    def _request_json(self, path, params):
        key = self._require_key()
        query = dict(params)
        query["apikey"] = key
        url = f"{self.base_url}/{path.lstrip('/')}?{urlencode(query)}"
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            if error.code in {401, 403}:
                raise ProviderConfigurationError(
                    f"Twelve Data rejected authentication for {path} (HTTP {error.code})"
                ) from error
            raise ProviderConnectionError(f"Twelve Data HTTP {error.code} for {path}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise ProviderConnectionError(
                f"Twelve Data connection failed for {path}: {type(error).__name__}"
            ) from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProviderSchemaError(f"Twelve Data returned invalid JSON for {path}") from error
        if isinstance(payload, dict) and payload.get("status") == "error":
            # Remote message omitted deliberately -- some APIs echo request
            # parameters (including the key) back in error bodies.
            raise ProviderDataError(f"Twelve Data returned an error for {path}")
        return payload

    def _require_key(self):
        if not self._api_key:
            raise ProviderConfigurationError(
                "TWELVEDATA_API_KEY is not configured (BLOCKED_NO_API_KEY)"
            )
        return self._api_key
