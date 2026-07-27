"""Thin, cache-first EODHD HTTP client (token-safe).

Responsibilities: authenticated GETs to the EODHD REST API with persistent on-disk
caching (so Streamlit reruns never re-consume API budget), bounded retries with
backoff, timeouts, a per-process live-call budget, and TOTAL token redaction — the
token is read from the centralized environment loader, is never logged, returned, or
placed in a cache key or error string.

Read-only. Changes no provider selection, strategy, or execution behavior.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from core.environment import load_project_environment, masked_eodhd_token_status

BASE_URL = "https://eodhd.com/api"
CACHE_DIR = Path("data/eodhd_cache")
DEFAULT_TIMEOUT = 25
DEFAULT_RETRIES = 3


class EODHDError(RuntimeError):
    """Raised on an unrecoverable EODHD request failure (token already redacted)."""


class EODHDNotConfigured(EODHDError):
    pass


@dataclass
class CallStats:
    live_calls: int = 0
    cache_hits: int = 0
    errors: int = 0
    endpoints: dict = field(default_factory=dict)

    def _bump(self, kind, path):
        self.endpoints.setdefault(path, {"live": 0, "cache": 0})
        self.endpoints[path][kind] += 1


def _redact(text, token):
    s = str(text)
    if token:
        s = s.replace(token, "***REDACTED***")
    # also strip any api_token=... that may appear in a URL inside an error
    import re
    return re.sub(r"(api_token=)[^&\s]+", r"\1***REDACTED***", s)


class EODHDClient:
    """Cache-first EODHD client. One instance is cheap; share it across a run."""

    def __init__(self, *, cache_dir=None, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES,
                 max_live_calls=None):
        load_project_environment()
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.timeout = int(timeout)
        self.retries = int(retries)
        self.max_live_calls = max_live_calls
        self.stats = CallStats()

    # -- token (internal only) ----------------------------------------------

    def _token(self):
        import os
        token = os.getenv("EODHD_API_TOKEN") or os.getenv("EODHD_API_KEY")
        if not token:
            raise EODHDNotConfigured("EODHD_API_TOKEN/EODHD_API_KEY is not configured")
        return token

    def is_configured(self):
        import os
        load_project_environment()
        return bool(os.getenv("EODHD_API_TOKEN") or os.getenv("EODHD_API_KEY"))

    def token_status(self):
        return masked_eodhd_token_status()

    # -- caching -------------------------------------------------------------

    def _cache_path(self, path, params):
        # key excludes the token entirely
        safe = {k: v for k, v in sorted((params or {}).items()) if k != "api_token"}
        raw = path + "?" + urlparse.urlencode(safe)
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
        slug = path.replace("/", "_").strip("_")[:40]
        return self.cache_dir / f"{slug}.{digest}.json"

    def _read_cache(self, cache_path, ttl_seconds):
        if not cache_path.is_file():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if ttl_seconds is not None:
            age = time.time() - float(payload.get("_fetched_epoch", 0))
            if age > ttl_seconds:
                return None
        return payload.get("data")

    def peek_json(self, path, params=None):
        """Return cached data and safe cache metadata without any network call.

        Launcher health/status code uses this method so a stale or absent cache
        can be reported immediately instead of triggering the client's retry
        policy on the desktop UI path.
        """

        params = dict(params or {})
        params.setdefault("fmt", "json")
        cache_path = self._cache_path(path, params)
        if not cache_path.is_file():
            return None, {"available": False, "age_seconds": None}
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            fetched = float(payload.get("_fetched_epoch", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            return None, {"available": False, "age_seconds": None}
        return payload.get("data"), {
            "available": payload.get("data") is not None,
            "age_seconds": max(0.0, time.time() - fetched) if fetched else None,
        }

    def _write_cache(self, cache_path, data):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps({"_fetched_epoch": time.time(), "data": data},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache_path)

    # -- request -------------------------------------------------------------

    def get_json(self, path, params=None, *, cache_ttl_seconds=86400, force=False):
        """GET {BASE}/{path} with caching. Returns parsed JSON (list/dict).

        cache_ttl_seconds=None caches forever; force=True bypasses the cache read.
        """
        params = dict(params or {})
        params.setdefault("fmt", "json")
        cache_path = self._cache_path(path, params)
        if not force:
            cached = self._read_cache(cache_path, cache_ttl_seconds)
            if cached is not None:
                self.stats.cache_hits += 1
                self.stats._bump("cache", path)
                return cached

        if self.max_live_calls is not None and self.stats.live_calls >= self.max_live_calls:
            raise EODHDError(f"API budget reached ({self.max_live_calls} live calls)")

        token = self._token()
        query = dict(params)
        query["api_token"] = token
        url = f"{BASE_URL}/{path}?{urlparse.urlencode(query)}"

        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                req = urlrequest.Request(url, headers={"User-Agent": "EGX-Trader-EODHD/1.0"})
                with urlrequest.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body)
                self.stats.live_calls += 1
                self.stats._bump("live", path)
                self._write_cache(cache_path, data)
                return data
            except urlerror.HTTPError as e:
                code = e.code
                last_error = _redact(f"HTTP {code}", token)
                if code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                break
            except (urlerror.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_error = _redact(f"{type(e).__name__}: {e}", token)
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                break
        self.stats.errors += 1
        raise EODHDError(f"EODHD request failed for '{path}': {last_error}")

    # -- typed endpoints -----------------------------------------------------

    def user(self):
        return self.get_json("user", cache_ttl_seconds=3600)

    def exchange_symbols(self, exchange="EGX"):
        return self.get_json(f"exchange-symbol-list/{exchange}", cache_ttl_seconds=7 * 86400)

    def search(self, query, *, limit=30):
        """EODHD symbol search — returns candidate securities across exchanges."""
        return self.get_json(f"search/{query}", {"limit": limit}, cache_ttl_seconds=7 * 86400)

    def eod(self, symbol, *, from_date=None, to_date=None, period="d", order="a",
            cache_ttl_seconds=43200, force=False):
        params = {"period": period, "order": order}
        if from_date:
            params["from"] = str(from_date)
        if to_date:
            params["to"] = str(to_date)
        return self.get_json(f"eod/{symbol}", params, cache_ttl_seconds=cache_ttl_seconds,
                             force=force)
