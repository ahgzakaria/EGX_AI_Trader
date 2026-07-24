"""Historical-provider mode declaration (shadow-safe).

Declares the four provider modes and reads the configured mode. IMPORTANT: in
`EODHD_SHADOW` (the current mode) the ACTIVE historical provider is still Yahoo — this
module never switches provider selection. Wiring EODHD into the live data path is a
deliberate, separate step to be taken only after the shadow validation is reviewed.
"""

from __future__ import annotations

YAHOO_ONLY = "YAHOO_ONLY"
EODHD_SHADOW = "EODHD_SHADOW"
EODHD_PRIMARY_YAHOO_FALLBACK = "EODHD_PRIMARY_YAHOO_FALLBACK"
EODHD_ONLY = "EODHD_ONLY"

MODES = (YAHOO_ONLY, EODHD_SHADOW, EODHD_PRIMARY_YAHOO_FALLBACK, EODHD_ONLY)


def current_mode() -> str:
    try:
        from config.settings_manager import settings
        mode = (settings.get("provider_mode") or {}).get("mode")
    except Exception:
        mode = None
    return mode if mode in MODES else YAHOO_ONLY


def active_historical_provider() -> str:
    """The provider actually feeding strategy decisions.

    Shadow and Yahoo-only modes both keep Yahoo active. EODHD only becomes active in
    the EODHD_* live modes — which are NOT enabled yet and require the deliberate
    switch step. Until then this always returns 'yahoo'.
    """
    mode = current_mode()
    if mode in (EODHD_PRIMARY_YAHOO_FALLBACK, EODHD_ONLY):
        return "eodhd"      # not reachable while mode == EODHD_SHADOW
    return "yahoo"


def is_shadow() -> bool:
    return current_mode() == EODHD_SHADOW
