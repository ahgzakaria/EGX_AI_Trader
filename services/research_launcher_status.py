"""Launcher-facing status for the CURRENT_RESEARCH_V2 architecture.

Provides secret-free status dictionaries the Rubix Production Launcher renders:

  * Current Research (EODHD) — token configured?, authentication Valid/Unknown/Invalid,
    latest completed session, freshness, cache status. **The EODHD token is never
    returned, logged, or displayed** — only `core.environment.masked_eodhd_token_status`
    metadata is used.
  * Market — trading day / holiday / weekend, current phase, next session, via the
    centralized EGX calendar.
  * Safety — paper / production / broker-execution flags.

Yahoo is not an operational provider here; it never appears in any status returned by
this module.
"""

from __future__ import annotations

TOKEN_MISSING = "TOKEN_MISSING"
AUTH_VALID = "Valid"
AUTH_UNKNOWN = "Unknown"
AUTH_INVALID = "Invalid"


def eodhd_token_configured() -> bool:
    from core.environment import is_eodhd_configured
    return bool(is_eodhd_configured())


def verify_eodhd_authentication(*, online: bool = False, timeout_calls: int = 1) -> dict:
    """Return {'auth', 'mode', 'detail'} without ever exposing the token.

    ``online=False`` (default, used by Refresh Status) performs no network call and
    reports Unknown when a token is present. ``online=True`` (used by Start Research
    Only) attempts one cheap EODHD ``user`` probe: success -> Valid, an authentication
    rejection -> Invalid, any transient/network error -> Unknown (cache may still serve).
    """
    if not eodhd_token_configured():
        return {"auth": AUTH_INVALID, "mode": TOKEN_MISSING,
                "detail": "EODHD_API_TOKEN not configured in .env"}
    if not online:
        return {"auth": AUTH_UNKNOWN, "mode": "UNVERIFIED",
                "detail": "Token present; not verified against EODHD this refresh"}
    try:
        from providers.eodhd_client import EODHDClient, EODHDError
        client = EODHDClient(max_live_calls=max(1, int(timeout_calls)))
        try:
            client.user()
            return {"auth": AUTH_VALID, "mode": "LIVE", "detail": "EODHD authenticated"}
        except EODHDError as error:
            text = str(error).lower()
            if any(code in text for code in ("401", "403", "unauthorized", "forbidden",
                                             "invalid", "not configured")):
                return {"auth": AUTH_INVALID, "mode": "AUTH_REJECTED",
                        "detail": "EODHD rejected the token"}
            return {"auth": AUTH_UNKNOWN, "mode": "UNREACHABLE",
                    "detail": "EODHD not reachable; cache may still be current"}
    except Exception:
        return {"auth": AUTH_UNKNOWN, "mode": "UNREACHABLE",
                "detail": "EODHD probe unavailable; cache may still be current"}


def current_research_status(*, online: bool = False) -> dict:
    """Current-research (EODHD) panel: provider, token, auth, latest session, freshness,
    cache status. Never Yahoo, never the token itself."""
    configured = eodhd_token_configured()
    auth = verify_eodhd_authentication(online=online)
    status = {
        "provider": "EODHD",
        "token_configured": configured,
        "token": "Configured" if configured else "Missing",
        "authentication": auth["auth"],
        "auth_mode": auth["mode"],
        "latest_completed_session": None,
        "expected_completed_session": None,
        "freshness": "UNKNOWN",
        "cache_status": "UNKNOWN",
        "data_mode": "DATA_UNAVAILABLE" if not configured else "UNKNOWN",
    }
    if not configured:
        status["cache_status"] = "No EODHD token"
        return status
    # Cache-only freshness probe against a known EODHD-supported symbol (no network
    # required if cached). Failure never falls back to Yahoo — it reports UNAVAILABLE.
    try:
        import core.research_router as router
        expected = router._expected_completed_session()
        status["expected_completed_session"] = expected.isoformat() if expected else None
        frame = router.eodhd_history("COMI", min_bars=1)
        latest = frame.index[-1].date()
        fresh = router._freshness(latest, expected)
        status["latest_completed_session"] = latest.isoformat()
        status["freshness"] = fresh.get("status")
        current = expected is not None and latest >= expected
        status["cache_status"] = "Current" if current else "Behind expected session"
        status["data_mode"] = ("LIVE" if auth["mode"] == "LIVE" and current
                               else "CACHE_MODE" if current else "STALE")
    except Exception as error:
        status["cache_status"] = f"Unavailable ({type(error).__name__})"
        status["freshness"] = "DATA_UNAVAILABLE"
        status["data_mode"] = "DATA_UNAVAILABLE"
    return status


def market_status(now=None) -> dict:
    """Trading day / holiday / weekend, current phase, next session (central calendar)."""
    from core.egx_calendar import effective_holidays, next_trading_session, session_status
    from core.egx_session import cairo_now, egx_session_phase, is_regular_trading_day
    current = cairo_now(now)
    holidays = effective_holidays()
    trading = is_regular_trading_day(current.date(), holidays)
    weekday = current.weekday()          # 4=Fri, 5=Sat weekend in Egypt
    if trading:
        day_kind = "Trading day"
    elif weekday in (4, 5):
        day_kind = "Weekend"
    else:
        day_kind = "Holiday"
    try:
        sess = session_status(current)
        status_label = getattr(sess, "status", None)
    except Exception:
        status_label = None
    try:
        nxt = next_trading_session(current.date())
        next_session = nxt.isoformat() if nxt else None
    except Exception:
        next_session = None
    return {
        "day_kind": day_kind,
        "is_trading_day": bool(trading),
        "market_closed": not trading,
        "phase": egx_session_phase(current, holidays) if trading else "MARKET_CLOSED",
        "session_status": status_label,
        "next_session": next_session,
    }


def safety_status() -> dict:
    """Paper / production / broker-execution flags (read-only)."""
    from scalping_expected_range.config import ExpectedRangeConfig
    cfg = ExpectedRangeConfig.load()
    return {
        "paper_mode": bool(cfg.paper_enabled),
        "production_enabled": bool(cfg.production_enabled),
        "automatic_execution": bool(cfg.automatic_execution),
        "broker_orders_enabled": bool(cfg.broker_orders_enabled),
    }
