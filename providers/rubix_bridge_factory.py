"""Construct the Rubix daily bridge from application settings.

Centralizes configuration reading so the shadow runner and the dashboard
disclosure layer build the bridge identically.  Returns a disabled/no-op-safe
bridge when configuration or the database is unavailable.
"""

from __future__ import annotations

from datetime import time
import os

from config.settings_manager import settings
from providers.rubix_completed_daily_bridge import RubixCompletedDailyBridge
from providers.rubix_daily_aggregator import RubixDailyAggregator


def bridge_config():
    cfg = dict(settings.get("rubix_daily_bridge") or {})
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "shadow_mode": bool(cfg.get("shadow_mode", True)),
        "close_safety_minutes": float(cfg.get("close_safety_minutes", 15)),
        "minimum_coverage_ratio": float(cfg.get("minimum_coverage_ratio", 0.90)),
        "minimum_volume_reliability": float(cfg.get("minimum_volume_reliability", 0.90)),
        "holidays": tuple(cfg.get("holidays", ()) or ()),
        "early_closes": _parse_early_closes(cfg.get("early_closes")),
    }


def rubix_db_path():
    market_cfg = settings.get("market_data")
    return os.getenv("RUBIX_DB_PATH") or market_cfg.get(
        "rubix_db_path", "data/rubix_live_market.db"
    )


def build_aggregator(now=None):
    cfg = bridge_config()
    return RubixDailyAggregator(
        db_path=rubix_db_path(),
        now=now,
        close_safety_minutes=cfg["close_safety_minutes"],
        minimum_coverage_ratio=cfg["minimum_coverage_ratio"],
        minimum_volume_reliability=cfg["minimum_volume_reliability"],
        holidays=cfg["holidays"],
        early_closes=cfg["early_closes"],
    )


def build_bridge(now=None):
    return RubixCompletedDailyBridge(build_aggregator(now=now))


def bridge_disclosure():
    """Return display-only disclosure fields for the dashboard (Phase H).

    This never influences routing or decisions.  Swing/Daily always analyses
    completed candles only, so the current forming session is always excluded
    from indicators regardless of bridge state.
    """

    cfg = bridge_config()
    active = cfg["enabled"] and not cfg["shadow_mode"]
    latest_valid = {}
    try:
        from providers.rubix_bridge_provenance import BridgeProvenanceStore

        latest_valid = BridgeProvenanceStore().latest_valid_by_symbol()
    except Exception:
        latest_valid = {}
    symbols_with_append = len(latest_valid)
    return {
        "bridge_enabled": cfg["enabled"],
        "bridge_shadow_mode": cfg["shadow_mode"],
        "bridge_active_in_production": active,
        "historical_warmup_source": (
            "Yahoo + Rubix Completed Daily Bridge"
            if active and symbols_with_append else "Yahoo / local cache"
        ),
        "latest_completed_candle_source": (
            "Rubix Completed Daily" if active and symbols_with_append else "Yahoo"
        ),
        "symbols_with_rubix_sessions": symbols_with_append,
        "current_session_included_in_indicators": "No",
        "mode_label": (
            "Active" if active else "Shadow" if cfg["shadow_mode"] else "Disabled"
        ),
    }


def _parse_early_closes(raw):
    result = {}
    if not isinstance(raw, dict):
        return result
    from datetime import date

    for key, value in raw.items():
        try:
            day = date.fromisoformat(str(key)[:10])
            hour, minute = (int(part) for part in str(value).split(":")[:2])
            result[day] = time(hour, minute)
        except (ValueError, TypeError):
            continue
    return result
