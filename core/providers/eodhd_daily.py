"""Isolated EODHD EGX daily adapter (audit/validation only — never production).

A thin, read-only client for EODHD's EGX daily data used solely to audit EODHD as
a historical backfill / corporate-action / reconciliation / future-fallback source.
It is NOT wired into any strategy, the normalized cache, or production routing.

Safety: the API token is read from the environment (`EODHD_API_TOKEN` or
`EODHD_API_KEY`) and is NEVER returned, logged, printed, or placed in an error
message or a URL that is surfaced. Every failure is classified into an explicit
taxonomy so a missing key or exhausted plan is reported honestly rather than
producing fabricated data. A minimum inter-request interval respects rate limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from core.environment import load_project_environment
from providers.symbol_mapping import to_eodhd_symbol

BASE = "https://eodhd.com/api"

# Failure taxonomy (Phase 1).
API_KEY_MISSING = "API_KEY_MISSING"
INVALID_KEY = "INVALID_KEY"
PLAN_NOT_ENTITLED = "PLAN_NOT_ENTITLED"
RATE_LIMITED = "RATE_LIMITED"
SYMBOL_UNSUPPORTED = "SYMBOL_UNSUPPORTED"
ENDPOINT_UNAVAILABLE = "ENDPOINT_UNAVAILABLE"
NETWORK_ERROR = "NETWORK_ERROR"
MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
OK = "OK"


@dataclass
class EodhdResult:
    ok: bool
    status: str                         # OK or a failure-taxonomy value
    data: object = None
    http_status: int | None = None
    detail: str = ""
    response_hash: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EodhdDailyClient:
    def __init__(self, api_token=None, min_interval_seconds=1.0, timeout=20):
        # The token lives in .env, and reading os.getenv without loading it
        # first finds nothing. providers/eodhd_client.py has always called this;
        # this class never did, so key_configured returned False in any process
        # that had not loaded the environment for its own reasons -- and the
        # finalization observer, which runs standalone, recorded API_KEY_MISSING
        # every time from 2026-07-22 onward while the key sat in the file.
        # A missing key and an unloaded environment are not the same thing, and
        # only one of them is the operator's problem.
        load_project_environment()
        # Never stored anywhere that is surfaced; only used to sign requests.
        self._token = api_token or os.getenv("EODHD_API_TOKEN") or os.getenv("EODHD_API_KEY")
        self.min_interval = float(min_interval_seconds)
        self.timeout = timeout
        self._last_call = 0.0

    # -- disclosure-safe status ------------------------------------------

    @property
    def key_configured(self) -> bool:
        return bool(self._token)

    def _throttle(self):
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, path, params=None) -> EodhdResult:
        if not self._token:
            return EodhdResult(False, API_KEY_MISSING,
                               detail="EODHD_API_TOKEN/EODHD_API_KEY not configured")
        query = dict(params or {})
        query["api_token"] = self._token
        query.setdefault("fmt", "json")
        url = f"{BASE}/{path}?{urllib.parse.urlencode(query)}"
        self._throttle()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "egx-audit/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                http = resp.status
        except urllib.error.HTTPError as e:
            return self._classify_http(e)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Never include the URL (it carries the token) in the surfaced detail.
            return EodhdResult(False, NETWORK_ERROR, detail=f"{type(e).__name__}")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return EodhdResult(False, MALFORMED_RESPONSE, http_status=http,
                               detail="non-JSON response")
        return EodhdResult(True, OK, data=data, http_status=http,
                           response_hash=hashlib.sha256(raw).hexdigest()[:16])

    @staticmethod
    def _classify_http(e) -> EodhdResult:
        code = getattr(e, "code", None)
        mapping = {401: INVALID_KEY, 403: PLAN_NOT_ENTITLED, 402: PLAN_NOT_ENTITLED,
                   429: RATE_LIMITED, 404: SYMBOL_UNSUPPORTED}
        status = mapping.get(code, ENDPOINT_UNAVAILABLE)
        return EodhdResult(False, status, http_status=code, detail=f"HTTP {code}")

    # -- endpoints --------------------------------------------------------

    def entitlement(self) -> EodhdResult:
        """Phase 1 — account tier / allowance / entitlement (GET /user)."""
        return self._get("user")

    def eod(self, engine_symbol, date_from=None, date_to=None) -> EodhdResult:
        params = {}
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to
        return self._get(f"eod/{to_eodhd_symbol(engine_symbol)}", params)

    def dividends(self, engine_symbol, date_from=None) -> EodhdResult:
        params = {"from": date_from} if date_from else {}
        return self._get(f"div/{to_eodhd_symbol(engine_symbol)}", params)

    def splits(self, engine_symbol, date_from=None) -> EodhdResult:
        params = {"from": date_from} if date_from else {}
        return self._get(f"splits/{to_eodhd_symbol(engine_symbol)}", params)

    def exchange_symbols(self, exchange="EGX") -> EodhdResult:
        return self._get(f"exchange-symbol-list/{exchange}")

    def bulk_last_day(self, exchange="EGX") -> EodhdResult:
        return self._get(f"eod-bulk-last-day/{exchange}")


def probe_summary(client: "EodhdDailyClient") -> dict:
    """Phase 1 entitlement probe — honest status, never the token."""
    if not client.key_configured:
        return {"status": API_KEY_MISSING, "key_configured": False,
                "detail": "No EODHD API key in the environment; live measurement not possible.",
                "network_note": "eodhd.com reachability is checked separately."}
    r = client.entitlement()
    out = {"status": r.status, "key_configured": True, "http_status": r.http_status}
    if r.ok and isinstance(r.data, dict):
        d = r.data
        out.update({
            "account_tier": d.get("subscriptionType") or d.get("name"),
            "daily_api_allowance": d.get("dailyRateLimit") or d.get("apiRequests"),
            "requests_used": d.get("apiRequests"),
            "extra_limit": d.get("extraLimit"),
        })
    else:
        out["detail"] = r.detail
    return out
