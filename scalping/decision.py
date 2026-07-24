"""Operational and microstructure gate for technically qualified setups."""

from __future__ import annotations

from core.egx_session import cairo_now
from scalping.config import ScalpingConfig
from scalping.models import MarketSnapshot


def spread_percent(bid, ask):
    if bid is None or ask is None or float(bid) <= 0 or float(ask) <= 0:
        return None
    midpoint = (float(bid) + float(ask)) / 2.0
    return (float(ask) - float(bid)) / midpoint * 100.0 if midpoint else None


def assess_actionability(snapshot: MarketSnapshot, config: ScalpingConfig):
    """Return an explicit actionability verdict; never manufacture fallback data."""

    reasons = []
    provider = str(snapshot.provider or "").lower()
    freshness = str(snapshot.freshness or "").upper()
    if not config.enabled:
        reasons.append("MODULE_DISABLED")
    if config.mode != "PAPER_ONLY":
        reasons.append("PAPER_ONLY_REQUIRED")
    if provider != "rubix":
        reasons.append("RUBIX_REQUIRED")
    if snapshot.collector_status and str(snapshot.collector_status).upper() != "CONNECTED":
        reasons.append("COLLECTOR_DISCONNECTED")
    if config.require_rubix_fresh and freshness not in {"FRESH", "RUBIX_FRESH"}:
        reasons.append(f"DATA_{freshness or 'UNAVAILABLE'}")
    if snapshot.session_phase != "OPEN":
        reasons.append("MARKET_CLOSED")
    if snapshot.quote_age_seconds is None or snapshot.quote_age_seconds > config.quote_max_age_seconds:
        reasons.append("QUOTE_STALE")
    if snapshot.bid is None or snapshot.ask is None:
        reasons.append("BID_ASK_MISSING")
    if snapshot.volume is None or snapshot.volume < config.minimum_liquidity:
        reasons.append("INSUFFICIENT_LIQUIDITY")
    spread = spread_percent(snapshot.bid, snapshot.ask)
    if spread is None or spread > config.max_spread_percent:
        reasons.append("EXCESSIVE_SPREAD")
    if cairo_now(snapshot.timestamp).time() >= config.entry_cutoff_time:
        reasons.append("ENTRY_CUTOFF_REACHED")
    return not reasons, tuple(dict.fromkeys(reasons))
