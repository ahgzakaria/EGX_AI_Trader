"""Central provider router with two explicitly versioned data domains.

  * CURRENT_RESEARCH_V2 — current scans, indicators, pre-session, Expected Range,
    Watchlist, forward tests. Sources: **EODHD** (Tier A/B/C-clean), or validated
    **local history + Rubix Daily Bridge** for EODHD-unsupported symbols.
    **Yahoo is never used here and is never an operational fallback.**
  * LEGACY_BACKTEST_V1 — reproduction of approved historical baselines only, served
    from the FROZEN Yahoo snapshot (local cache, **no network**), immutable.

Bars from the two domains are never mixed in one dataset: every frame carries a
``data_domain`` in ``attrs['market_data']``. Nothing here changes strategy, indicators,
scoring, thresholds, TP/SL, Rubix, or the production flags.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from core.universe import read_symbol_frame

CURRENT_RESEARCH_V2 = "CURRENT_RESEARCH_V2"
LEGACY_BACKTEST_V1 = "LEGACY_BACKTEST_V1"
FROZEN_YAHOO_SNAPSHOT = "FROZEN_YAHOO_SNAPSHOT"

REVIEW_PATH = Path("data/eodhd/historical_symbol_routing_review.json")
RECON_PATH = Path("reports/eodhd/corporate_action_reconciliation.csv")

# current-research states
EODHD_OPERATIONAL_CLEAN_WINDOW = "EODHD_OPERATIONAL_CLEAN_WINDOW"
EODHD_RESEARCH_REVIEW_REQUIRED = "EODHD_RESEARCH_REVIEW_REQUIRED"
LOCAL_PLUS_RUBIX_READY = "LOCAL_PLUS_RUBIX_READY"
LOCAL_PLUS_RUBIX_STALE = "LOCAL_PLUS_RUBIX_STALE"
LOCAL_SEED_ONLY_STALE = "LOCAL_SEED_ONLY_STALE"
LOCAL_PLUS_RUBIX_BUILDING_HISTORY = "LOCAL_PLUS_RUBIX_BUILDING_HISTORY"
BRIDGE_CONFLICT = "BRIDGE_CONFLICT"
VOLUME_POLICY_UNRESOLVED = "VOLUME_POLICY_UNRESOLVED"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
EXCLUDED_NON_EQUITY = "EXCLUDED_NON_EQUITY"

_TIER_CACHE = {"mtime": None, "map": {}}
_RECON_CACHE = {"mtime": None, "map": {}}


class ResearchDataUnavailable(RuntimeError):
    """Raised when current research cannot be served WITHOUT falling back to Yahoo."""

    def __init__(self, symbol, status, detail=""):
        super().__init__(f"{symbol}: {status} {detail}".strip())
        self.symbol = symbol
        self.status = status
        self.detail = detail


def _base(symbol):
    return str(symbol).strip().upper().split(".")[0]


def tier_map():
    """symbol -> tier entry from the routing review (hot-reloaded on file change)."""
    try:
        mtime = REVIEW_PATH.stat().st_mtime
    except OSError:
        return {}
    if _TIER_CACHE["mtime"] != mtime:
        try:
            data = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
            _TIER_CACHE["map"] = {str(e["symbol"]).upper(): e for e in data.get("symbols", [])}
            _TIER_CACHE["mtime"] = mtime
        except (OSError, json.JSONDecodeError):
            return _TIER_CACHE["map"]
    return _TIER_CACHE["map"]


def symbol_tier(symbol):
    entry = tier_map().get(_base(symbol))
    return (entry or {}).get("tier", "TIER_D_UNSUPPORTED_OR_MANUAL")


def unresolved_action_date(symbol):
    """Latest corporate action whose split/bonus convention is NOT cleanly resolved."""
    try:
        mtime = RECON_PATH.stat().st_mtime
    except OSError:
        return None
    if _RECON_CACHE["mtime"] != mtime:
        try:
            df = read_symbol_frame(RECON_PATH)
            m = {}
            for _, r in df.iterrows():
                if str(r.get("classified_action")) != "SPLIT":      # unresolved convention
                    sym = str(r["symbol"]).upper()
                    d = str(r["effective_date"])[:10]
                    if sym not in m or d > m[sym]:
                        m[sym] = d
            _RECON_CACHE["map"] = m
            _RECON_CACHE["mtime"] = mtime
        except Exception:
            return _RECON_CACHE["map"].get(_base(symbol))
    return _RECON_CACHE["map"].get(_base(symbol))


# --- EODHD current-research history -----------------------------------------


# --------------------------------------------------------------------------- #
# Scan-scoped reuse
# --------------------------------------------------------------------------- #
#
# A universe scan asked the same three questions 265 times over: it built a new EODHD
# client per symbol, recomputed the expected completed session per symbol, and re-parsed
# and re-adjusted the same unchanged ten-year cache file per symbol. None of that changes
# between symbols within one scan, so it is computed once and reused.
#
# Nothing here alters a value: the adjusted frame handed back is the same frame the engine
# received before, and the cache is keyed on the identity of the source file so a refreshed
# cache file is never served from a stale entry.

ADJUSTED_HISTORY_CACHE_VERSION = "eodhd_adjusted@1"

# A ten-year adjusted frame is roughly 3 000 rows; the bound keeps the cache to a
# predictable ceiling instead of growing with every symbol ever requested. Both limits
# are configurable so an operator can trade memory for warm-scan speed deliberately.
_DEFAULT_CACHE_MAX_ENTRIES = 300
_DEFAULT_CACHE_MAX_BYTES = 256 * 1024 * 1024


def _cache_limit(name, default):
    import os
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class _BoundedFrameCache:
    """A small LRU of adjusted frames with an entry bound AND a byte-estimate bound.

    Entries are evicted least-recently-used first. Callers never receive the stored
    object: :meth:`get` returns an isolated shallow copy with its own ``attrs`` mapping,
    so a caller mutating the frame it was handed cannot corrupt the cached entry.
    """

    def __init__(self, max_entries=None, max_bytes=None):
        from collections import OrderedDict
        self._entries = OrderedDict()
        self._bytes = 0
        self.max_entries = max_entries or _cache_limit(
            "EODHD_HISTORY_CACHE_MAX_ENTRIES", _DEFAULT_CACHE_MAX_ENTRIES)
        self.max_bytes = max_bytes or _cache_limit(
            "EODHD_HISTORY_CACHE_MAX_BYTES", _DEFAULT_CACHE_MAX_BYTES)
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @staticmethod
    def _isolate(frame):
        copy = frame.copy(deep=False)
        copy.attrs = {key: dict(value) if isinstance(value, dict) else value
                      for key, value in frame.attrs.items()}
        return copy

    @staticmethod
    def _estimate_bytes(frame):
        try:
            return int(frame.memory_usage(index=True, deep=False).sum())
        except Exception:
            return 0

    def get(self, key):
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return self._isolate(entry[0])

    def put(self, key, frame):
        size = self._estimate_bytes(frame)
        if key in self._entries:
            self._bytes -= self._entries[key][1]
            del self._entries[key]
        self._entries[key] = (self._isolate(frame), size)
        self._bytes += size
        while self._entries and (len(self._entries) > self.max_entries
                                 or self._bytes > self.max_bytes):
            _, evicted = self._entries.popitem(last=False)
            self._bytes -= evicted[1]
            self.evictions += 1

    def clear(self):
        self._entries.clear()
        self._bytes = 0

    def stats(self):
        """Safe metrics only — no symbol data, no file paths."""
        return {
            "entries": len(self._entries),
            "retained_bytes": int(self._bytes),
            "max_entries": self.max_entries,
            "max_bytes": self.max_bytes,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }


_SHARED_CLIENT = {"client": None}
_EXPECTED_SESSION_CACHE = {"value": None, "set": False}
_ADJUSTED_HISTORY_CACHE = _BoundedFrameCache()


def adjusted_history_cache_stats():
    """Bounded-cache metrics for diagnostics and the scan report."""
    return _ADJUSTED_HISTORY_CACHE.stats()


def shared_eodhd_client():
    """A process-level EODHD client for NON-SCAN callers (diagnostics, one-off reads).

    A scan must not use this: it builds its own client through
    :class:`core.scan_context.ScanDataContext` so that each scan observes current
    configuration and owns the lifetime of its session. This accessor exists only so
    callers outside a scan keep working unchanged.
    """
    from providers.eodhd_client import EODHDClient

    if _SHARED_CLIENT["client"] is None:
        _SHARED_CLIENT["client"] = EODHDClient()
    return _SHARED_CLIENT["client"]


def reset_research_caches():
    """Drop scan-scoped reuse. Called at the start of a scan and by tests."""
    _SHARED_CLIENT["client"] = None
    _EXPECTED_SESSION_CACHE.update({"value": None, "set": False})
    _ADJUSTED_HISTORY_CACHE.clear()


def _expected_completed_session():
    """The expected completed EGX session, resolved once per scan."""
    if _EXPECTED_SESSION_CACHE["set"]:
        return _EXPECTED_SESSION_CACHE["value"]
    value = _compute_expected_completed_session()
    _EXPECTED_SESSION_CACHE.update({"value": value, "set": True})
    return value


def _compute_expected_completed_session():
    """The completed EGX session, from the exchange calendar in Cairo.

    Deliberately not ``expected_latest_completed_session``: that answers what
    a provider should have published, and using it here made a finished
    session look unfinished. See ``authoritative_completed_session``.
    """
    from core.egx_calendar import effective_holidays
    from core.egx_session import authoritative_completed_session
    try:
        return authoritative_completed_session(holidays=effective_holidays())
    except Exception:
        return None


def _cache_identity(client, base):
    """Identity of the EODHD cache file backing ``base`` — path, size and mtime.

    A changed or refreshed cache file produces a different identity, so an adjusted frame
    is reused only while its source bytes are unchanged.
    """
    try:
        path = client._cache_path(f"eod/{base}.EGX",
                                 {"period": "d", "order": "a", "fmt": "json"})
        stat = path.stat()
        return (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    except Exception:
        return None


def _freshness(available_date, expected=None):
    """{'status', 'lag'} for a completed-session date vs the expected EGX session."""
    from core.egx_calendar import effective_holidays
    from core.egx_session import classify_history_freshness
    try:
        f = classify_history_freshness(available_date, holidays=effective_holidays())
        return {"status": f.status, "lag": f.lag}
    except Exception:
        if expected is None:
            return {"status": "UNKNOWN", "lag": None}
        lag = (expected - available_date).days if available_date else None
        return {"status": "HISTORY_CURRENT" if available_date and available_date >= expected
                else "HISTORY_STALE", "lag": lag}


def eodhd_history(symbol, *, min_bars=250, force_refresh=False, client=None,
                  scan_budget=None):
    """Split-adjusted EODHD daily history in the load_history frame contract.

    Adjustment is unchanged: the same split-adjusted prices and the same event-specific
    operational volume as before. What changed is that the client is supplied by the
    caller (a scan passes its own scan-scoped session), the parsed and adjusted result is
    reused while the source cache file is byte-identical, and a stale series triggers AT
    MOST ONE bounded refresh instead of an open recursion.
    """
    from core.history_frame_adapter import to_load_history_frame
    from providers.eodhd_adjustment import adjust
    from providers.eodhd_client import EODHDError

    base = _base(symbol)
    client = client if client is not None else shared_eodhd_client()
    bars = max(1, int(min_bars))

    identity = None if force_refresh else _cache_identity(client, base)
    if identity is not None:
        cached = _ADJUSTED_HISTORY_CACHE.get(
            (base, bars, identity, ADJUSTED_HISTORY_CACHE_VERSION))
        if cached is not None:
            return cached

    # A refresh inside a scan is bounded: one attempt, a total deadline, and
    # cancellation checked either side of the network call. Reads served from the local
    # cache are unaffected — the bound only applies when bytes actually move.
    from providers.eodhd_client import (
        SCAN_RETRIES,
        SCAN_TOTAL_DEADLINE,
        EODHDAuthFailed,
        EODHDCancelled,
        EODHDConnectionFailed,
        EODHDRateLimited,
        EODHDTimeout,
    )

    breaker = None if scan_budget is None else scan_budget.get("breaker")
    bounded = {} if scan_budget is None else {
        "deadline_seconds": scan_budget.get("deadline_seconds", SCAN_TOTAL_DEADLINE),
        "max_attempts": scan_budget.get("max_attempts", SCAN_RETRIES),
        "cancel": scan_budget.get("cancel"),
    }
    # An open breaker stops NEW network refreshes. Valid local cache still serves, so a
    # provider outage degrades coverage instead of failing the whole universe — and it
    # never reaches for Yahoo.
    if force_refresh and breaker is not None and breaker.is_open:
        raise ResearchDataUnavailable(
            base, "EODHD_PROVIDER_UNAVAILABLE",
            f"circuit breaker open: {breaker.reason}")
    try:
        raw = client.eod(f"{base}.EGX", order="a", cache_ttl_seconds=6 * 3600,
                         force=force_refresh, **bounded)
    except EODHDCancelled:
        raise
    except (EODHDTimeout, EODHDConnectionFailed, EODHDRateLimited,
            EODHDAuthFailed) as error:
        # Typed network conditions keep their class so the scanner can report the real
        # cause. Only these BROAD provider failures move the breaker.
        if breaker is not None:
            breaker.record_broad_failure(error)
        raise
    except EODHDError as error:
        raise ResearchDataUnavailable(base, DATA_UNAVAILABLE, str(error)) from error
    if force_refresh and breaker is not None:
        # Only a real network request proves the provider is reachable again.
        breaker.record_success()
    rows = [r for r in (raw or []) if isinstance(r, dict) and r.get("close")]
    if not rows:
        raise ResearchDataUnavailable(base, DATA_UNAVAILABLE, "EODHD returned no bars")
    # One vectorised date parse for the whole series. The previous form called
    # ``pd.to_datetime`` once per row — roughly three thousand scalar conversions per
    # symbol — which was 96% of the cost of loading a symbol. The resulting column is
    # identical: the same ``datetime.date`` values in the same order.
    frame = pd.DataFrame({
        "Date": pd.to_datetime([r["date"] for r in rows]).date,
        "Open": [r["open"] for r in rows],
        "High": [r["high"] for r in rows],
        "Low": [r["low"] for r in rows],
        "Close": [r["close"] for r in rows],
        "Volume": [r.get("volume", 0) for r in rows],
    })
    # At most ONE bounded refresh when the cached series is behind the expected completed
    # session. ``force_refresh`` is the terminal branch, so a symbol whose data simply has
    # not been published yet costs one attempt, never a retry cascade across the universe.
    expected = _expected_completed_session()
    if (not force_refresh and expected and not frame.empty
            and frame["Date"].max() < expected):
        return eodhd_history(symbol, min_bars=min_bars, force_refresh=True,
                             client=client, scan_budget=scan_budget)
    try:
        splits = client.get_json(f"splits/{base}.EGX", cache_ttl_seconds=7 * 86400)
    except Exception:
        splits = []
    from providers.eodhd_volume_adjustment import resolve_operational_volume

    adjusted = adjust(frame, splits).frame            # split-adjusted PRICES (unchanged)
    # Event-specific VOLUME: replaces the previous universal Volume x Split Factor.
    served_volume, vmeta = resolve_operational_volume(
        base, adjusted["Date"], adjusted["Raw Volume"], splits)
    adjusted = adjusted.copy()
    adjusted["Volume"] = served_volume.to_numpy()
    out = to_load_history_frame(adjusted, symbol=symbol, purpose="current_research")
    out.attrs.setdefault("market_data", {})
    out.attrs["volume_meta"] = vmeta
    if len(out) < bars:
        raise ResearchDataUnavailable(base, DATA_INSUFFICIENT,
                                      f"{len(out)} bars < required {min_bars}")

    # Cache against the identity of the file the result was actually derived from.
    final_identity = _cache_identity(client, base)
    if final_identity is not None:
        _ADJUSTED_HISTORY_CACHE.put(
            (base, bars, final_identity, ADJUSTED_HISTORY_CACHE_VERSION), out)
    return out


# --- unsupported symbols: validated local history + Rubix Daily Bridge -------


def local_plus_rubix_history(symbol, *, period="10y", interval="1d", min_bars=250,
                             bridge_cache=None):
    """Frozen Yahoo seed + REAL Rubix Daily Bridge append, with an honest readiness gate.

    Returns a canonical frame whose ``attrs['market_data']`` carries full seed/bridge
    provenance, an ``operational_status`` and ``freshness`` fields. Raises
    ResearchDataUnavailable only for a genuinely missing seed. Staleness does NOT raise —
    it is reported via ``operational_status`` so the symbol is visibly blocked, never
    silently treated as fresh.
    """
    from core.local_rubix_history import build_local_rubix_history

    base = _base(symbol)
    frame, prov = build_local_rubix_history(base, period=period, interval=interval,
                                            bridge_cache=bridge_cache)
    if frame is None:
        raise ResearchDataUnavailable(base, DATA_UNAVAILABLE,
                                      "no validated local seed and EODHD unsupported")

    expected = _expected_completed_session()
    effective = pd.Timestamp(prov["effective_latest_session"]).date()
    appended = int(prov["bridge_sessions_appended"])
    conflicts = int(prov["conflict_count"])
    rows = int(len(frame))
    fresh = _freshness(effective, expected)
    at_expected = expected is not None and effective >= expected

    if conflicts > 0:
        status = BRIDGE_CONFLICT
    elif rows < max(1, int(min_bars)):
        status = LOCAL_PLUS_RUBIX_BUILDING_HISTORY if appended > 0 else DATA_INSUFFICIENT
    elif at_expected:
        # Seed already current, or a real bridge bar brought it current.
        status = LOCAL_PLUS_RUBIX_READY
    elif appended > 0:
        status = LOCAL_PLUS_RUBIX_STALE          # bridge contributing but still behind
    else:
        status = LOCAL_SEED_ONLY_STALE           # frozen seed behind, no bridge bar yet

    md = dict(frame.attrs.get("market_data", {}))
    md.update({
        "operational_status": status,
        "history_sufficient": rows >= int(min_bars),
        "freshness_status": fresh.get("status"),
        "expected_completed_session": expected.isoformat() if expected else None,
        "session_lag": fresh.get("lag"),
    })
    frame.attrs["market_data"] = md
    return frame, status, md


# --- Tier C clean-window gate -----------------------------------------------


def clean_window_status(symbol, *, min_bars=250):
    """(state, detail) for a Tier C symbol's operational usability."""
    base = _base(symbol)
    unresolved = unresolved_action_date(base)
    try:
        frame = eodhd_history(base, min_bars=1)
    except ResearchDataUnavailable as error:
        return (error.status, error.detail)
    if not unresolved:
        return (EODHD_OPERATIONAL_CLEAN_WINDOW, "no unresolved corporate action")
    after = frame[frame.index > pd.Timestamp(unresolved)]
    if len(after) >= min_bars:
        return (EODHD_OPERATIONAL_CLEAN_WINDOW,
                f"{len(after)} clean sessions after {unresolved} (need {min_bars})")
    return (EODHD_RESEARCH_REVIEW_REQUIRED,
            f"only {len(after)} clean sessions after unresolved action {unresolved} "
            f"(need {min_bars}) — lookback crosses the event")


# --- the router --------------------------------------------------------------


def get_current_research_history(symbol, *, period="10y", interval="1d", min_bars=250,
                                 scan_context=None):
    """CURRENT_RESEARCH_V2 history. Never a Yahoo network call. Raises when unusable.

    A stale local seed does NOT masquerade as fresh: it raises ResearchDataUnavailable
    with a LOCAL_*_STALE / DATA_INSUFFICIENT / BRIDGE_CONFLICT status so the caller (and
    the manifest) sees an explicit block instead of a silently-current symbol.
    """
    base = _base(symbol)
    tier = symbol_tier(base)
    entry = tier_map().get(base, {})
    if tier == "TIER_D_UNSUPPORTED_OR_MANUAL" and str(entry.get("evidence_status")) == "non_equity":
        raise ResearchDataUnavailable(base, EXCLUDED_NON_EQUITY, "non-equity excluded")

    expected = _expected_completed_session()
    volume_meta = {}

    if tier in ("TIER_A_FORWARD_SAFE", "TIER_B_FORWARD_EODHD_NO_FALLBACK",
                "TIER_C_HISTORICAL_REVIEW"):
        if tier == "TIER_C_HISTORICAL_REVIEW":
            state, detail = clean_window_status(base, min_bars=min_bars)
            if state != EODHD_OPERATIONAL_CLEAN_WINDOW:
                raise ResearchDataUnavailable(base, state, detail)
        frame = eodhd_history(
            base, min_bars=min_bars,
            client=getattr(scan_context, "eodhd_client", None),
            scan_budget=(scan_context.request_budget()
                         if scan_context is not None else None))
        volume_meta = dict(frame.attrs.get("volume_meta", {}))
        provider, series = "eodhd", "SPLIT_ADJUSTED"
        effective = pd.Timestamp(frame.index[-1]).date()
        fresh = _freshness(effective, expected)
        if not volume_meta.get("volume_safe_for_lookback", True):
            raise ResearchDataUnavailable(
                base, VOLUME_POLICY_UNRESOLVED,
                f"unresolved/event-specific corporate action in volume lookback "
                f"({volume_meta.get('latest_action_in_lookback')})")
        state = EODHD_OPERATIONAL_CLEAN_WINDOW
        seed_present, price_policy = False, "SPLIT_ADJUSTED_ALL_EVENTS"
    else:
        # EODHD-unsupported / manual: frozen Yahoo seed + REAL Rubix Daily Bridge.
        frame, state, local_md = local_plus_rubix_history(
            base, period=period, interval=interval, min_bars=min_bars)
        provider, series = "local_plus_rubix", "PROJECT_LOCAL_SEED_PLUS_RUBIX"
        effective = pd.Timestamp(frame.index[-1]).date()
        fresh = {"status": local_md.get("freshness_status"), "lag": local_md.get("session_lag")}
        seed_present, price_policy = True, "FROZEN_YAHOO_SEED_NATIVE"
        if state not in (LOCAL_PLUS_RUBIX_READY,):
            raise ResearchDataUnavailable(base, state, _local_block_detail(local_md, expected))

    md = dict(frame.attrs.get("market_data", {}))
    md.update({
        "data_domain": CURRENT_RESEARCH_V2, "provider": provider,
        "effective_provider": provider, "requested_provider": provider,
        "provider_symbol": f"{base}.EGX" if provider == "eodhd" else base,
        "price_series": series, "price_adjustment_policy": price_policy,
        "corporate_action_policy_version": volume_meta.get(
            "corporate_action_policy_version", "n/a"),
        "volume_series": volume_meta.get("volume_series",
                                         "FROZEN_YAHOO_SEED" if seed_present else "RAW_EODHD"),
        "volume_adjustment_policy": volume_meta.get("volume_adjustment_policy", "NONE"),
        "volume_safe_for_lookback": volume_meta.get("volume_safe_for_lookback", True),
        "latest_action_in_lookback": volume_meta.get("latest_action_in_lookback"),
        "routing_tier": tier, "fallback_used": False,
        "yahoo_network_used": False, "yahoo_seed_present": seed_present,
        "latest_completed_session": effective.isoformat(),
        "expected_completed_session": expected.isoformat() if expected else None,
        "freshness_status": fresh.get("status"), "session_lag": fresh.get("lag"),
        "history_sufficient": len(frame) >= min_bars,
        "data_quality_status": state,
    })
    frame.attrs["market_data"] = md
    return frame


def _local_block_detail(local_md, expected):
    return (f"seed_latest={local_md.get('seed_latest_session')} "
            f"effective={local_md.get('effective_latest_session')} "
            f"expected={expected.isoformat() if expected else None} "
            f"bridge_appended={local_md.get('bridge_sessions_appended')} "
            f"conflicts={local_md.get('conflict_count')}")


def get_legacy_backtest_history(symbol, *, period="10y", interval="1d",
                                snapshot_version="v1"):
    """LEGACY_BACKTEST_V1 — frozen Yahoo snapshot from local cache, NO network."""
    from providers.local_cache_provider import LocalCacheProvider
    cache = LocalCacheProvider()
    frame = cache.load_cached("yahoo", symbol, period, interval, allow_expired=True)
    if frame is None or getattr(frame, "empty", True):
        raise ResearchDataUnavailable(_base(symbol), DATA_UNAVAILABLE,
                                      "no frozen Yahoo snapshot for this symbol/range")
    frame = frame.copy()
    md = dict(frame.attrs.get("market_data", {}))
    md.update({"data_domain": LEGACY_BACKTEST_V1, "provider": FROZEN_YAHOO_SNAPSHOT,
               "effective_provider": FROZEN_YAHOO_SNAPSHOT, "snapshot_version": snapshot_version,
               "immutable": True, "yahoo_network_used": False})
    frame.attrs["market_data"] = md
    return frame


def get_history_provenance(frame):
    return dict((frame.attrs or {}).get("market_data", {}))
