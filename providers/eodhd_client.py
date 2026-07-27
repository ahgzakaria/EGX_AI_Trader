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
import socket
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


# Typed failures. A scan classifies a symbol from the exception CLASS, so a bounded
# network condition is never reported as an internal defect.
class EODHDTimeout(EODHDError):
    """The request exceeded its per-attempt timeout or its total deadline."""


class EODHDConnectionFailed(EODHDError):
    """The endpoint could not be reached (DNS, refused, reset, unreachable)."""


class EODHDRateLimited(EODHDError):
    """The provider answered 429."""


class EODHDAuthFailed(EODHDError):
    """The provider rejected the credential (401/403)."""


class EODHDCancelled(EODHDError):
    """The scan was cancelled; no further request was attempted."""


# Bounded network policy.
#
# ``SCAN_ATTEMPT_TIMEOUT`` is the socket timeout for one attempt: an EODHD EOD document
# for a single EGX symbol is a few hundred kilobytes from a CDN-backed REST endpoint, so
# a healthy response lands well inside one second. Eight seconds is generous enough to
# absorb a slow link while being far below anything an operator would call a stall.
#
# ``SCAN_TOTAL_DEADLINE`` bounds the whole refresh for one symbol including any retry.
# Twelve seconds allows one full attempt plus a short margin, so a single symbol can add
# at most ~12 s to a scan instead of the previous worst case of three 25-second attempts
# separated by exponential backoff (~80 s).
#
# ``SCAN_RETRIES = 1`` implements "at most one refresh attempt per symbol": inside a scan
# there is no retry cascade across 265 symbols. Non-scan callers keep the original
# retrying defaults.
SCAN_ATTEMPT_TIMEOUT = 8
SCAN_TOTAL_DEADLINE = 12
SCAN_RETRIES = 1


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

    def get_json(self, path, params=None, *, cache_ttl_seconds=86400, force=False,
                 deadline_seconds=None, max_attempts=None, cancel=None):
        """GET {BASE}/{path} with caching. Returns parsed JSON (list/dict).

        cache_ttl_seconds=None caches forever; force=True bypasses the cache read.

        ``deadline_seconds`` bounds the TOTAL time for this request including retries
        and backoff; ``max_attempts`` caps attempts (a scan passes 1); ``cancel`` is a
        predicate checked immediately before and after the bounded network call.
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

        retries = self.retries if max_attempts is None else max(1, int(max_attempts))
        started = time.monotonic()

        def remaining():
            """Seconds left in the total deadline, or ``None`` when unbounded."""
            if deadline_seconds is None:
                return None
            return deadline_seconds - (time.monotonic() - started)

        def check_cancelled():
            if cancel is not None and cancel():
                raise EODHDCancelled(f"cancelled before EODHD request '{path}'")

        last_error = None
        last_class = EODHDError
        for attempt in range(1, retries + 1):
            # Cancellation is checked immediately BEFORE the bounded request…
            check_cancelled()
            left = remaining()
            if left is not None and left <= 0:
                self.stats.errors += 1
                raise EODHDTimeout(
                    f"EODHD deadline exceeded for '{path}' after "
                    f"{deadline_seconds:g}s")
            attempt_timeout = self.timeout if left is None else max(0.5, min(self.timeout, left))
            try:
                req = urlrequest.Request(url, headers={"User-Agent": "EGX-Trader-EODHD/1.0"})
                with urlrequest.urlopen(req, timeout=attempt_timeout) as resp:  # noqa: S310
                    body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(body)
                self.stats.live_calls += 1
                self.stats._bump("live", path)
                self._write_cache(cache_path, data)
                # …and immediately AFTER it returns, so a cancelled scan stops here.
                check_cancelled()
                return data
            except EODHDCancelled:
                raise
            except urlerror.HTTPError as e:
                code = e.code
                last_error = _redact(f"HTTP {code}", token)
                if code == 429:
                    last_class = EODHDRateLimited
                elif code in (401, 403):
                    # Deterministic credential rejection: retrying cannot fix it.
                    self.stats.errors += 1
                    raise EODHDAuthFailed(
                        f"EODHD rejected the credential for '{path}': {last_error}")
                else:
                    last_class = EODHDConnectionFailed
                if code in (429, 500, 502, 503, 504) and attempt < retries:
                    backoff = min(2 ** attempt, 8)
                    left = remaining()
                    if left is not None and backoff >= left:
                        break              # a backoff that would blow the deadline
                    time.sleep(backoff)
                    continue
                break
            except (TimeoutError, socket.timeout) as e:
                last_error = _redact(f"{type(e).__name__}: {e}", token)
                last_class = EODHDTimeout
                break                       # a timeout is already the bound; do not retry
            except (urlerror.URLError, json.JSONDecodeError) as e:
                last_error = _redact(f"{type(e).__name__}: {e}", token)
                last_class = (EODHDTimeout
                              if isinstance(getattr(e, "reason", None), (TimeoutError,))
                              else EODHDConnectionFailed)
                if attempt < retries:
                    backoff = min(2 ** attempt, 8)
                    left = remaining()
                    if left is not None and backoff >= left:
                        break
                    time.sleep(backoff)
                    continue
                break
        self.stats.errors += 1
        raise last_class(f"EODHD request failed for '{path}': {last_error}")

    # -- typed endpoints -----------------------------------------------------

    def user(self):
        return self.get_json("user", cache_ttl_seconds=3600)

    def exchange_symbols(self, exchange="EGX"):
        return self.get_json(f"exchange-symbol-list/{exchange}", cache_ttl_seconds=7 * 86400)

    def search(self, query, *, limit=30):
        """EODHD symbol search — returns candidate securities across exchanges."""
        return self.get_json(f"search/{query}", {"limit": limit}, cache_ttl_seconds=7 * 86400)

    def eod(self, symbol, *, from_date=None, to_date=None, period="d", order="a",
            cache_ttl_seconds=43200, force=False, deadline_seconds=None,
            max_attempts=None, cancel=None):
        params = {"period": period, "order": order}
        if from_date:
            params["from"] = str(from_date)
        if to_date:
            params["to"] = str(to_date)
        return self.get_json(f"eod/{symbol}", params, cache_ttl_seconds=cache_ttl_seconds,
                             force=force, deadline_seconds=deadline_seconds,
                             max_attempts=max_attempts, cancel=cancel)
