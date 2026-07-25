"""Operational safety applied after the frozen strategy decision.

The strategy signal is never rewritten. This layer only decides whether a new
BUY may be acted on using the data-source evidence attached to its candles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from core.egx_session import egx_session_phase


@dataclass(frozen=True)
class LiveActionability:
    operational_status: str
    final_actionability: str
    reason: str

    @property
    def actionable(self):
        return self.final_actionability == "ACTIONABLE"


def assess_live_actionability(strategy_signal, metadata, now: datetime | None = None):
    """Classify one decision from operational evidence, never strategy values."""

    metadata = dict(metadata or {})
    signal = str(strategy_signal or "UNKNOWN").upper()
    # Swing candles come from historical Yahoo/cache, while actionability is
    # based only on the independent Rubix quote overlay.
    provider = str(
        metadata.get("live_quote_provider")
        or metadata.get("effective_provider")
        or metadata.get("provider")
        or ""
    ).lower()
    fallback = bool(metadata.get("fallback_active"))
    phase = str(metadata.get("session_phase") or egx_session_phase(now)).upper()
    freshness = str(
        metadata.get("live_quote_freshness")
        or metadata.get("freshness")
        or metadata.get("freshness_status")
        or metadata.get("provider_status")
        or "UNAVAILABLE"
    ).upper()

    if fallback or provider != "rubix":
        reason = metadata.get("fallback_reason") or "Rubix is unavailable; Yahoo is daily fallback only"
        return LiveActionability("DAILY_FALLBACK", "NON_LIVE / NON_ACTIONABLE", str(reason))
    if phase != "OPEN":
        return LiveActionability(
            "MARKET_CLOSED", "NON_ACTIONABLE",
            "EGX session is closed; analysis is end-of-session only",
        )
    if "SYMBOL_MISSING" in freshness:
        return LiveActionability("SYMBOL_MISSING", "NON_ACTIONABLE", "Rubix has no current quote for this symbol")
    if "DELAYED" in freshness:
        reason = metadata.get("freshness_warning") or "Rubix exchange timestamp is delayed"
        return LiveActionability("DATA_DELAYED", "NON_ACTIONABLE", str(reason))
    if "FRESH" not in freshness:
        reason = metadata.get("freshness_warning") or metadata.get("fallback_reason") or freshness
        return LiveActionability("DATA_STALE", "NON_ACTIONABLE", str(reason))
    if signal == "BUY":
        return LiveActionability("LIVE_DATA_OK", "ACTIONABLE", "Fresh Rubix data during the open EGX session")
    return LiveActionability("LIVE_DATA_OK", "ANALYSIS_ONLY", "Fresh data; strategy did not issue BUY")


def actionability_fields(strategy_signal, frame, now=None):
    """Return additive fields used by Dashboard and forward/paper safeguards."""

    metadata = dict(getattr(frame, "attrs", {}).get("market_data", {}))
    decision = assess_live_actionability(strategy_signal, metadata, now=now)
    source_time = (
        metadata.get("live_quote_timestamp")
        or metadata.get("latest_exchange_timestamp")
        or metadata.get("source_latest_timestamp")
    )
    received_time = (
        metadata.get("live_quote_received_timestamp")
        or metadata.get("received_timestamp")
    )
    age_seconds = metadata.get("age_seconds")
    if age_seconds is None and received_time:
        try:
            current = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
            current = current.tz_localize("UTC") if current.tzinfo is None else current.tz_convert("UTC")
            received = pd.Timestamp(received_time)
            received = received.tz_localize("UTC") if received.tzinfo is None else received.tz_convert("UTC")
            age_seconds = max(0.0, (current - received).total_seconds())
        except Exception:
            age_seconds = None
    return {
        "StrategySignal": strategy_signal,
        "OperationalStatus": decision.operational_status,
        "FinalActionability": decision.final_actionability,
        "Actionable": decision.actionable,
        "DataSource": metadata.get("historical_provider") or metadata.get("provider") or "unknown",
        "HistoricalDataSource": metadata.get("historical_provider") or "unknown",
        "LiveQuoteSource": metadata.get("live_quote_provider") or "unavailable",
        "LiveQuoteLast": metadata.get("live_quote_last"),
        "LiveQuoteBid": metadata.get("live_quote_bid"),
        "LiveQuoteAsk": metadata.get("live_quote_ask"),
        "LiveQuoteVolume": metadata.get("live_quote_volume"),
        "LiveQuoteSpreadPercent": metadata.get("live_quote_spread_percent"),
        "LatestCompletedCandle": metadata.get("latest_completed_candle"),
        "DataTimestamp": source_time,
        "DataAgeSeconds": round(float(age_seconds), 1) if age_seconds is not None else None,
        "OperationalReason": decision.reason,
    }
