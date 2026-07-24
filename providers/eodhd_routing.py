"""Per-symbol historical routing policy — resolution + failure behavior (INACTIVE).

Reads the proposed, operator-unapproved routing policy and decides which provider +
price series would serve a symbol, plus the safe failure behavior. NOTHING here
switches the live provider; it is consulted only by the dry-run simulator. A policy
entry becomes operational only when ``approved: true`` AND a future integration step
wires it in — neither happens in this phase.
"""

from __future__ import annotations

import json
from pathlib import Path

# policies
EODHD_PRIMARY = "EODHD_PRIMARY"
YAHOO_FALLBACK = "YAHOO_FALLBACK"
YAHOO_ONLY = "YAHOO_ONLY"
EODHD_ONLY = "EODHD_ONLY"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
MANUAL_REVIEW = "MANUAL_REVIEW"
EXCLUDED_NON_EQUITY = "EXCLUDED_NON_EQUITY"

POLICY_PATH = Path("data/eodhd/historical_symbol_routing.json")


def load_policy(path=None):
    p = Path(path) if path else POLICY_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(r["symbol"]).upper(): r for r in data.get("symbols", []) if r.get("symbol")}


def resolve_route(symbol, policy, *, eodhd_ok=True, yahoo_ok=True):
    """Return the routing decision for a symbol given provider availability.

    Pure and side-effect-free. Encodes the failure behavior:
      * EODHD failure with an approved Yahoo fallback → Yahoo, fallback_used=True.
      * EODHD failure without an approved fallback → DATA_UNAVAILABLE (never empty
        history, never zero-substitution).
      * A symbol whose policy forbids Yahoo (proven stale/mis-scaled, e.g. ORAS) is
        NEVER served by Yahoo, even on EODHD failure — it returns DATA_UNAVAILABLE.
    """
    entry = policy.get(str(symbol).upper())
    if entry is None:
        return {"provider": "yahoo", "series": "PROJECT_CURRENT", "policy": YAHOO_ONLY,
                "fallback_used": False, "reason": "no policy entry — default current behavior"}
    pol = entry.get("policy") or YAHOO_ONLY
    series = entry.get("price_series", "SPLIT_ADJUSTED")
    allow_yahoo = entry.get("fallback_provider") == "yahoo"
    yahoo_forbidden = bool(entry.get("yahoo_forbidden"))

    if pol in (MANUAL_REVIEW, DATA_UNAVAILABLE, EXCLUDED_NON_EQUITY):
        return {"provider": None, "series": None, "policy": pol, "fallback_used": False,
                "reason": entry.get("reason", pol)}
    if pol == YAHOO_ONLY:
        if yahoo_ok:
            return {"provider": "yahoo", "series": "PROJECT_CURRENT", "policy": pol,
                    "fallback_used": False, "reason": entry.get("reason", "")}
        return {"provider": None, "series": None, "policy": DATA_UNAVAILABLE,
                "fallback_used": False, "reason": "Yahoo unavailable, no EODHD coverage"}
    if pol == EODHD_ONLY:
        if eodhd_ok:
            return {"provider": "eodhd", "series": series, "policy": pol,
                    "fallback_used": False, "reason": entry.get("reason", "")}
        return {"provider": None, "series": None, "policy": DATA_UNAVAILABLE,
                "fallback_used": False, "reason": "EODHD unavailable, no approved fallback"}
    # EODHD_PRIMARY (± Yahoo fallback)
    if eodhd_ok:
        return {"provider": "eodhd", "series": series, "policy": pol,
                "fallback_used": False, "reason": entry.get("reason", "")}
    if allow_yahoo and yahoo_ok and not yahoo_forbidden:
        return {"provider": "yahoo", "series": "PROJECT_CURRENT", "policy": pol,
                "fallback_used": True, "reason": "EODHD failed; approved Yahoo fallback"}
    return {"provider": None, "series": None, "policy": DATA_UNAVAILABLE,
            "fallback_used": False,
            "reason": ("EODHD failed; Yahoo forbidden (known bad data)" if yahoo_forbidden
                       else "EODHD failed; no approved Yahoo fallback")}
