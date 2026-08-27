"""Single market-data entry point for Scanner, Dashboard, and Backtest.

Routing is purpose-specific and metadata travels in ``DataFrame.attrs`` so the
frozen trading engine continues to receive the same tabular candle contract.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import logging
import os

import pandas as pd

from config.settings_manager import settings
from core.egx_session import egx_session_phase, trading_session_lag
from core.symbols import SYMBOL_SOURCE, load_symbols
from providers.base_provider import ProviderDataError, ProviderError, REQUIRED_COLUMNS
from providers.eodhd_provider import EODHDProvider
from providers.local_cache_provider import LocalCacheProvider
from providers.provider_manager import ProviderManager
from providers.rubix_sqlite_provider import RubixSQLiteProvider
from providers.tickerchart_provider import TickerChartProvider
from providers.yahoo_provider import YahooProvider
from services.dataset_archive import (
    capture_active,
    replay_active,
    replay_frame,
)


logger = logging.getLogger(__name__)
_PURPOSE = ContextVar("market_data_purpose", default="backtest")
_PROVIDER_INSTANCES = None


def current_provider_purpose():
    return _PURPOSE.get()


@contextmanager
def provider_purpose(purpose):
    """Temporarily route nested market-index requests to the same provider."""

    token = _PURPOSE.set(purpose)
    try:
        yield
    finally:
        _PURPOSE.reset(token)


def _provider_instances():
    global _PROVIDER_INSTANCES
    if _PROVIDER_INSTANCES is None:
        market_cfg = settings.get("market_data")
        cache = LocalCacheProvider(
            market_cfg.get("cache_path", "data/market_data_cache.sqlite"),
            source_provider=market_cfg.get("cache_source_provider", "rubix"),
        )
        yahoo = YahooProvider()

        def yahoo_history_seed(symbol, period, interval):
            """Warm indicators with historical Yahoo candles, never live fallback."""

            try:
                return cache.load_cached("yahoo", symbol, period, interval)
            except ProviderError:
                frame = yahoo.load_history(symbol, period, interval)
                cache.store("yahoo", symbol, period, interval, frame)
                return frame

        _PROVIDER_INSTANCES = {
            "yahoo": yahoo,
            # Parallel historical provider only. No purpose routes to EODHD in
            # Phase 1; Yahoo remains the backtest and historical default.
            "eodhd": EODHDProvider(),
            "rubix": RubixSQLiteProvider(
                db_path=os.getenv("RUBIX_DB_PATH") or market_cfg.get("rubix_db_path", "data/rubix_live_market.db"),
                stale_after_minutes=float(os.getenv(
                    "RUBIX_QUOTE_STALE_SECONDS",
                    market_cfg.get("rubix_quote_stale_seconds", 60),
                )) / 60,
                bar_stale_after_minutes=float(os.getenv(
                    "RUBIX_BAR_STALE_SECONDS",
                    market_cfg.get("rubix_bar_stale_seconds", 120),
                )) / 60,
                history_loader=yahoo_history_seed,
                expected_symbols=load_symbols(SYMBOL_SOURCE),
            ),
            "tickerchart": TickerChartProvider(
                db_path=market_cfg.get("tickerchart_db_path") or os.getenv("TICKERCHART_DB_PATH"),
                adapter_path=market_cfg.get("tickerchart_adapter_path") or os.getenv("TICKERCHART_ADAPTER_PATH"),
                local_url=market_cfg.get("tickerchart_local_url") or os.getenv("TICKERCHART_LOCAL_URL"),
                stale_after_minutes=os.getenv(
                    "TICKERCHART_STALE_AFTER_MINUTES",
                    market_cfg.get("tickerchart_stale_after_minutes", 1440),
                ),
                history_loader=yahoo_history_seed,
            ),
            "local_cache": cache,
        }
    return _PROVIDER_INSTANCES


def reset_provider_instances():
    """Reset provider objects after settings/env changes; cached candles remain."""

    global _PROVIDER_INSTANCES
    _PROVIDER_INSTANCES = None


def provider_name_for(purpose):
    key = {
        "scanner": "scanner_provider",
        "dashboard": "dashboard_provider",
        "forward_testing": "forward_testing_provider",
        "backtest": "backtest_provider",
    }.get(purpose, "backtest_provider")
    return str(settings.data.get(key, "yahoo")).strip().lower()


def load_history(
    symbol, period=None, interval=None, purpose=None,
    require_positive_volume=True, min_bars=None, scan_context=None,
    allow_held=False,
):
    """Load validated candles using configured routing and transparent fallback."""

    purpose = purpose or current_provider_purpose()
    # A replay is intentionally disconnected from every external provider.
    # Missing archived symbols fail loudly and can never fall back to Yahoo.
    if replay_active():
        frame = replay_frame(symbol)
        capture_active(symbol, frame, "raw", {"replay": True})
        capture_active(symbol, frame, "normalized", {"replay": True})
        return frame
    data_cfg = settings.get("data")
    period = period or data_cfg.get("history_period", "10y")
    interval = interval or data_cfg.get("interval", "1d")
    min_bars = int(min_bars if min_bars is not None else data_cfg.get("min_bars", 250))
    requested_name = provider_name_for(purpose)
    fallback_name = str(settings.data.get("fallback_provider", "yahoo")).strip().lower()
    providers = _provider_instances()
    cache = providers["local_cache"]

    # LEGACY_BACKTEST_V1 — reproduction of approved baselines only, served from the
    # FROZEN Yahoo snapshot (local cache, no network). Immutable; never used for
    # current research and never used to judge current freshness.
    if purpose == "backtest":
        from core.research_router import (
            ResearchDataUnavailable,
            get_legacy_backtest_history,
        )
        try:
            snapshot = get_legacy_backtest_history(symbol, period=period, interval=interval)
        except ResearchDataUnavailable as error:
            raise ProviderError(
                f"legacy backtest snapshot unavailable for {symbol}: {error.status}"
            ) from error
        legacy_md = dict(snapshot.attrs.get("market_data", {}))
        frozen = _clean_for_engine(snapshot, symbol, min_bars, require_positive_volume)
        merged = dict(frozen.attrs.get("market_data", {}))
        merged.update(legacy_md)
        frozen.attrs["market_data"] = merged
        capture_active(symbol, frozen, "normalized")
        return frozen

    # Current-research daily candles come from the research router (EODHD / local +
    # Rubix Bridge). Rubix contributes quote metadata only and can never replace or
    # append a completed daily OHLCV row used by indicators.
    if (
        requested_name == "rubix"
        and purpose in {"scanner", "dashboard", "forward_testing"}
        and str(interval).strip().lower() in {"1d", "1day", "day"}
        and isinstance(providers.get("rubix"), RubixSQLiteProvider)
    ):
        finalized = _load_swing_daily_history(
            symbol, period, interval, min_bars, require_positive_volume,
            cache, providers["yahoo"], providers["rubix"], purpose,
            scan_context=scan_context, allow_held=allow_held,
        )
        capture_active(symbol, finalized, "normalized")
        return finalized

    manager = ProviderManager(
        providers,
        loader=lambda provider: _load_one(
            provider, cache, symbol, period, interval,
            min_bars, require_positive_volume,
        ),
    )
    selection = manager.load_history(requested_name, fallback_name)
    if selection.fallback_active:
        logger.warning(
            "Market data provider %s not selected for %s (%s); using %s",
            requested_name, symbol, selection.reason, selection.effective,
        )
    finalized = _finalize_metadata(
        selection.frame,
        selection.requested,
        selection.effective,
        purpose,
        selection.fallback_active,
        selection.reason,
    )
    capture_active(symbol, finalized, "normalized")
    return finalized


def _load_swing_daily_history(
    symbol, period, interval, min_bars, require_positive_volume,
    cache, yahoo, rubix, purpose, scan_context=None, allow_held=False,
):
    """Load completed daily history first, then attach a non-candle quote overlay.

    Inside a scan the live overlay comes from ``scan_context`` — one batched read for the
    whole universe — so this path opens no per-symbol Rubix connection. Outside a scan the
    single-symbol overlay is used exactly as before.
    """

    # CURRENT_RESEARCH_V2: EODHD (per routing tier) or validated local history +
    # Rubix Daily Bridge for EODHD-unsupported symbols. Yahoo is NEVER used for
    # current research and is never an operational fallback.
    from core.research_router import (
        ResearchDataUnavailable,
        get_current_research_history,
    )

    try:
        history = get_current_research_history(
            symbol, period=period, interval=interval, min_bars=min_bars,
            scan_context=scan_context, allow_held=allow_held,
        )
    except ResearchDataUnavailable as error:
        # Blocked symbols fail loudly (never silently omitted, never Yahoo-substituted).
        raise ProviderError(
            f"current research unavailable for {symbol}: {error.status} {error.detail}"
        ) from error

    research_md = dict(history.attrs.get("market_data", {}))
    loaded_from_cache = True
    capture_active(
        f"{symbol}__source_current_research", history, "raw",
        {
            "provider": research_md.get("provider"),
            "engine_symbol": symbol,
            "data_domain": research_md.get("data_domain"),
            "routing_tier": research_md.get("routing_tier"),
            "swing_daily_history": True,
        },
    )
    frame = _clean_for_engine(
        history, symbol, min_bars, require_positive_volume
    )
    completed = pd.Timestamp(frame.index[-1]).isoformat()
    metadata = dict(frame.attrs.get("market_data", {}))
    overlay_error = None
    if scan_context is not None:
        # Scan path: the overlay was already read for the whole universe in one bounded
        # batch. No connection is opened here, and a missing quote stays typed-missing —
        # it is never substituted from another provider.
        overlay = scan_context.overlay_for(symbol)
        if overlay is None:
            overlay = {
                "provider": "rubix",
                "available": False,
                "freshness": "UNAVAILABLE",
                "operational_state": scan_context.rubix_batch_status,
                "session_phase": egx_session_phase(),
            }
            overlay_error = (scan_context.rubix_batch_detail
                             or "no batched Rubix quote for this symbol")
        elif not overlay.get("available"):
            overlay_error = overlay.get("freshness_warning")
    else:
        try:
            overlay = rubix.quote_overlay(symbol)
        except Exception as error:  # Live evidence must never erase valid history.
            logger.warning("Rubix quote overlay unavailable for %s: %s", symbol, error)
            overlay_error = str(error)
            overlay = {
                "provider": "rubix",
                "available": False,
                "freshness": "UNAVAILABLE",
                "operational_state": "RUBIX_UNAVAILABLE",
                "session_phase": egx_session_phase(),
            }

    metadata.update({
        "data_domain": research_md.get("data_domain"),
        "provider": research_md.get("provider"),
        "requested_provider": "rubix",
        "effective_provider": research_md.get("provider"),
        "provider_symbol": research_md.get("provider_symbol"),
        "price_series": research_md.get("price_series"),
        "price_adjustment_policy": research_md.get("price_adjustment_policy"),
        "volume_series": research_md.get("volume_series"),
        "volume_adjustment_policy": research_md.get("volume_adjustment_policy"),
        "volume_safe_for_lookback": research_md.get("volume_safe_for_lookback"),
        "corporate_action_policy_version": research_md.get("corporate_action_policy_version"),
        "routing_tier": research_md.get("routing_tier"),
        "history_sufficient": research_md.get("history_sufficient"),
        "bridge_sessions_appended": research_md.get("bridge_sessions_appended", 0),
        "seed_provider": research_md.get("seed_provider"),
        "seed_latest_session": research_md.get("seed_latest_session"),
        "bridge_latest_session": research_md.get("bridge_latest_session"),
        "freshness_status": research_md.get("freshness_status"),
        "data_quality_status": research_md.get("data_quality_status"),
        "yahoo_network_used": False,
        "yahoo_seed_present": research_md.get("yahoo_seed_present", False),
        "purpose": purpose,
        "historical_provider": research_md.get("provider"),
        "historical_cache_hit": loaded_from_cache,
        "historical_fallback_active": False,
        "latest_completed_candle": completed,
        "source_latest_timestamp": completed,
        "latest_exchange_timestamp": completed,
        "live_quote_provider": "rubix" if overlay.get("available") else "unavailable",
        "live_quote_available": bool(overlay.get("available")),
        "live_quote_last": overlay.get("last"),
        "live_quote_bid": overlay.get("bid"),
        "live_quote_ask": overlay.get("ask"),
        "live_quote_volume": overlay.get("volume"),
        "live_quote_timestamp": overlay.get("quote_timestamp"),
        "live_quote_received_timestamp": overlay.get("received_timestamp"),
        "live_quote_spread_percent": overlay.get("spread_percent"),
        "live_quote_freshness": overlay.get("freshness"),
        "live_quote_status": overlay.get("operational_state"),
        "live_quote_error": overlay_error,
        "rubix_quote_count": overlay.get("quote_count", 0),
        "rubix_minute_bars_available": bool(
            overlay.get("minute_bars_available")
        ),
        "rubix_minute_bar_count": overlay.get("minute_bar_count", 0),
        "rubix_latest_minute_bar": overlay.get("latest_minute_bar"),
        # Fallback refers only to the current quote overlay. Yahoo remains a
        # historical source and is never represented as live market data.
        "fallback_active": not bool(overlay.get("available")),
        "fallback_reason": overlay_error,
        "provider_status": overlay.get("operational_state"),
        "operational_state": overlay.get("operational_state"),
        "connection_state": overlay.get("operational_state"),
        "freshness": overlay.get("freshness"),
        "freshness_status": overlay.get("freshness"),
        "freshness_warning": overlay.get("freshness_warning"),
        "received_timestamp": overlay.get("received_timestamp"),
        "age_seconds": overlay.get("age_seconds"),
        "session_phase": overlay.get("session_phase") or egx_session_phase(),
        "session_lag": overlay.get("session_lag"),
        "database_status": "READ_ONLY_OK" if overlay.get("available") else "UNAVAILABLE",
        "rubix_overlay_active": bool(overlay.get("available")),
        "rubix_overlay_mutates_history": False,
    })
    frame.attrs["market_data"] = metadata
    return frame


def symbol_data_coverage(symbol, period=None, interval=None):
    """Return read-only historical and Rubix evidence for one audit row."""

    data_cfg = settings.get("data")
    period = period or data_cfg.get("history_period", "10y")
    interval = interval or data_cfg.get("interval", "1d")
    providers = _provider_instances()
    cached = providers["local_cache"].inspect_cached(
        "yahoo", symbol, period, interval
    )
    rubix = providers.get("rubix")
    live = (
        rubix.symbol_availability(symbol)
        if isinstance(rubix, RubixSQLiteProvider)
        else {
            "rubix_quote_available": False,
            "rubix_minute_bars_available": False,
            "rubix_quote_count": 0,
            "rubix_minute_bar_count": 0,
            "rubix_quote_timestamp": None,
            "rubix_status": "NOT_CONFIGURED",
            "rubix_error": "Rubix provider is not configured",
        }
    )
    return {**cached, **live}


def _load_one(
    provider, cache, symbol, period, interval, min_bars,
    require_positive_volume,
):
    # A newer-source comparison loads both Rubix and Yahoo before selecting
    # one. Keep their raw evidence under provider-qualified archive keys so
    # Phase 8 does not mistake two legitimate candidates for a dataset
    # mutation. The selected, engine-ready frame is still archived once under
    # the original symbol at the normalized stage below.
    raw_archive_symbol = f"{symbol}__source_{provider.name}"
    raw_metadata = {"provider": provider.name, "engine_symbol": symbol}
    if provider.name == "local_cache":
        frame = provider.load_history(symbol, period, interval)
        capture_active(raw_archive_symbol, frame, "raw", raw_metadata)
        return _clean_for_engine(
            frame, symbol, min_bars, require_positive_volume
        )

    # Rubix is a local read-only database and freshness is part of the safety
    # contract. Reading it directly avoids presenting a five-minute cache hit
    # as a fresh 60-second quote; historical Yahoo seed caching is unchanged.
    if provider.name == "rubix":
        frame = provider.load_history(symbol, period, interval)
        capture_active(raw_archive_symbol, frame, "raw", raw_metadata)
        return _clean_for_engine(
            frame,
            symbol, min_bars, require_positive_volume,
        )

    try:
        frame = cache.load_cached(provider.name, symbol, period, interval)
        capture_active(raw_archive_symbol, frame, "raw", {
            **raw_metadata, "loaded_from_cache": True,
        })
    except ProviderError:
        frame = provider.load_history(symbol, period, interval)
        capture_active(raw_archive_symbol, frame, "raw", {
            **raw_metadata, "loaded_from_cache": False,
        })
        cache.store(provider.name, symbol, period, interval, frame)
        return _clean_for_engine(
            frame, symbol, min_bars, require_positive_volume
        )
    return _clean_for_engine(
        frame, symbol, min_bars, require_positive_volume
    )


def _clean_for_engine(frame, symbol, min_bars, require_positive_volume=True):
    """Preserve the legacy loader's dropna/positive-volume/min-bars behavior."""

    metadata = dict(frame.attrs.get("market_data", {}))
    columns = list(REQUIRED_COLUMNS)
    if "Adj Close" in frame.columns:
        columns.insert(4, "Adj Close")
    cleaned = frame[columns].copy()
    cleaned = cleaned.dropna(subset=list(REQUIRED_COLUMNS))
    if require_positive_volume:
        cleaned = cleaned[cleaned["Volume"] > 0]
    if len(cleaned) < min_bars:
        raise ProviderDataError(
            f"{symbol}: not enough history ({len(cleaned)} bars; minimum {min_bars})"
        )
    cleaned.index.name = "Date"
    cleaned.attrs["market_data"] = metadata
    return cleaned


def _finalize_metadata(
    frame, requested, effective, purpose, fallback_active, fallback_reason,
):
    metadata = dict(frame.attrs.get("market_data", {}))
    provider = _provider_instances()[effective]
    requested_provider = _provider_instances().get(requested)
    requested_health = (
        requested_provider.health()
        if fallback_active and requested_provider is not None
        else {}
    )
    latest_timestamp = pd.Timestamp(frame.index[-1])
    source_timestamp = pd.Timestamp(
        metadata.get("source_latest_timestamp")
        or metadata.get("latest_exchange_timestamp")
        or latest_timestamp
    )
    session_date = (
        source_timestamp.tz_localize("Africa/Cairo")
        if source_timestamp.tzinfo is None
        else source_timestamp.tz_convert("Africa/Cairo")
    ).date()
    session_lag = trading_session_lag(session_date)
    metadata.update({
        "requested_provider": requested,
        "effective_provider": effective,
        "purpose": purpose,
        "fallback_active": bool(fallback_active),
        "fallback_reason": fallback_reason,
        "delayed": bool(getattr(provider, "delayed", False)),
        "delay_minutes": getattr(provider, "delay_minutes", None),
        "provider_status": (
            "YAHOO_FALLBACK" if fallback_active else metadata.get("status", "available")
        ),
        "database_status": metadata.get("database_status")
        or requested_health.get("database_status"),
        "requested_provider_status": requested_health.get("status"),
        "comparison_latest_timestamp": metadata.get("comparison_latest_timestamp")
        or requested_health.get("latest_exchange_timestamp"),
        "latest_exchange_timestamp": source_timestamp.isoformat(),
        "received_timestamp": metadata.get("received_timestamp")
        or datetime.now(timezone.utc).astimezone().isoformat(),
        # Disclosure only: never filters candles or changes trading decisions.
        "session_phase": metadata.get("session_phase") or egx_session_phase(),
        "session_lag": metadata.get("session_lag", session_lag),
        "freshness_status": metadata.get("freshness") or (
            "CURRENT_SESSION" if session_lag == 0 else "SESSION_LAG"
        ),
    })
    if fallback_active:
        metadata["operational_state"] = "YAHOO_FALLBACK"
        metadata["freshness"] = "YAHOO_FALLBACK"
        metadata["freshness_status"] = "YAHOO_FALLBACK"
        if requested_health.get("status") == "RUBIX_STALE":
            metadata["fallback_reason"] = fallback_reason or requested_health.get("reason")
        elif "no data for" in str(fallback_reason).lower() or "symbol_missing" in str(fallback_reason).lower():
            metadata["requested_provider_status"] = "SYMBOL_MISSING"
    frame.attrs["market_data"] = metadata
    return frame


def provider_health(purpose):
    requested = provider_name_for(purpose)
    provider = _provider_instances().get(requested)
    if provider is None:
        return {"provider": requested, "status": "unknown"}
    return provider.health()


def summarize_frames(frames, purpose="dashboard"):
    """Create provider disclosure fields without changing scan report rows."""

    observations = [
        dict(frame.attrs.get("market_data", {}))
        for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty
    ]
    if not observations:
        health = provider_health(purpose)
        requested = provider_name_for(purpose)
        fallback_name = str(settings.data.get("fallback_provider", "yahoo")).strip().lower()
        unusable = health.get("status") in {
            "RUBIX_UNAVAILABLE", "RUBIX_STALE",
            "TICKERCHART_UNAVAILABLE", "TICKERCHART_STALE",
            "TICKERCHART_WAITING_FOR_SESSION", "unavailable", "stale"
        }
        fallback_active = bool(unusable and fallback_name != requested)
        effective = fallback_name if fallback_active else requested
        effective_provider = _provider_instances().get(effective)
        summary = dict(health)
        summary.update({
            "requested_provider": requested,
            "effective_provider": effective,
            "fallback_active": fallback_active,
            "delayed": (
                getattr(effective_provider, "delayed", False)
                if fallback_active else health.get("delayed")
            ),
            "delay_minutes": (
                getattr(effective_provider, "delay_minutes", None)
                if fallback_active else health.get("delay_minutes")
            ),
            "provider_status": "YAHOO_FALLBACK" if fallback_active else health.get("status", "unknown"),
            "connection_state": "YAHOO_FALLBACK" if fallback_active else health.get("connection_state"),
            # Preserve health timestamps before the first successful scan or
            # after an empty/failed scan. Clearing these values made a fresh
            # Rubix database appear to have no last update in the dashboard.
            "latest_exchange_timestamp": health.get("latest_exchange_timestamp"),
            "latest_received_timestamp": health.get("latest_received_timestamp"),
            "session_phase": health.get("session_phase") or egx_session_phase(),
            "session_lag": health.get("session_lag"),
            "freshness": health.get("freshness") or health.get("status"),
            "database_status": health.get("database_status"),
            "comparison_latest_timestamp": health.get("comparison_latest_timestamp"),
            "fallback_reasons": [health.get("reason")] if fallback_active and health.get("reason") else [],
            "historical_provider": "local_cache:yahoo",
            "latest_completed_candle": None,
            "live_quote_provider": requested if requested == "rubix" else "unavailable",
            "live_quote_timestamp": health.get("latest_exchange_timestamp"),
        })
        return summary

    effective = sorted({row.get("effective_provider", "unknown") for row in observations})
    received = [row.get("received_timestamp") for row in observations if row.get("received_timestamp")]
    exchange = [row.get("latest_exchange_timestamp") for row in observations if row.get("latest_exchange_timestamp")]
    delays = [row.get("delay_minutes") for row in observations if row.get("delay_minutes") is not None]
    session_lags = [
        int(row.get("session_lag")) for row in observations
        if row.get("session_lag") is not None
    ]
    database_states = [
        row.get("database_status") for row in observations
        if row.get("database_status")
    ]
    freshness_states = [
        row.get("freshness") or row.get("freshness_status")
        for row in observations
        if row.get("freshness") or row.get("freshness_status")
    ]
    comparison_times = [
        row.get("comparison_latest_timestamp") for row in observations
        if row.get("comparison_latest_timestamp")
    ]
    historical_sources = sorted({
        row.get("historical_provider") or row.get("provider") or "unknown"
        for row in observations
    })
    live_sources = sorted({
        row.get("live_quote_provider") or "unavailable"
        for row in observations
    })
    completed_candles = [
        row.get("latest_completed_candle")
        for row in observations if row.get("latest_completed_candle")
    ]
    live_quote_times = [
        row.get("live_quote_timestamp")
        for row in observations if row.get("live_quote_timestamp")
    ]
    # Global collector/coverage evidence comes directly from the read-only DB;
    # per-frame provider metadata supplies the actual source selection.
    health = provider_health(purpose)
    summary = dict(health)
    summary.update({
        "requested_provider": observations[0].get("requested_provider", provider_name_for(purpose)),
        "effective_provider": " + ".join(effective),
        "fallback_active": any(row.get("fallback_active") for row in observations),
        "delayed": any(row.get("delayed") for row in observations),
        "delay_minutes": max(delays) if delays else None,
        "provider_status": "YAHOO_FALLBACK" if any(row.get("fallback_active") for row in observations) else observations[0].get("operational_state", "available"),
        "connection_state": observations[0].get("connection_state"),
        "latest_exchange_timestamp": max(exchange) if exchange else None,
        "latest_received_timestamp": max(received) if received else None,
        "session_phase": observations[0].get("session_phase") or egx_session_phase(),
        "session_lag": max(session_lags) if session_lags else None,
        "freshness": ", ".join(sorted(set(freshness_states))) if freshness_states else None,
        "database_status": ", ".join(sorted(set(database_states))) if database_states else None,
        "comparison_latest_timestamp": max(comparison_times) if comparison_times else None,
        "fallback_reasons": sorted({
            row.get("fallback_reason") for row in observations if row.get("fallback_reason")
        }),
        "yahoo_fallback_count": sum(bool(row.get("fallback_active")) for row in observations),
        "historical_provider": " + ".join(historical_sources),
        "latest_completed_candle": max(completed_candles) if completed_candles else None,
        "live_quote_provider": " + ".join(live_sources),
        "live_quote_timestamp": max(live_quote_times) if live_quote_times else None,
        "live_quote_available_count": sum(
            bool(row.get("live_quote_available")) for row in observations
        ),
    })
    return summary
