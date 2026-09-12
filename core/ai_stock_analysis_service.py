"""Orchestrator for AI Stock Analysis On Demand (single manually-requested symbol).

This service wires the deterministic backend together for **exactly one** EGX symbol per
call. It never scans the universe, never touches strategy/threshold/TP-SL/execution
logic, and never uses Yahoo operationally.

Flow for one request:
  1. Resolve the EGX market phase (Cairo, holiday/auction aware).
  2. Load CURRENT_RESEARCH_V2 daily history via the research router (EODHD, or the
     validated local seed + Rubix Daily Bridge for EODHD-unsupported symbols). A genuine
     data block yields a well-formed DATA_INSUFFICIENT result rather than an exception.
  3. Optionally attach a Rubix live overlay quote (best-effort; degrades to unavailable).
  4. Build Layer-1 numeric evidence (``core.ai_analysis_evidence``).
  5. Build a Layer-2 Arabic narrative (``core.ai_analysis_narrative``) — AI if a safe
     generator is supplied and validates, else the deterministic fallback.
  6. Optionally append a durable, append-only history record.

Every external dependency is injectable so the whole path is deterministic under test.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from datetime import datetime, time, timezone
from typing import Callable
from pathlib import Path

import pandas as pd

from core import ai_analysis_evidence as evidence
from core.ai_analysis_narrative import NarrativeGenerator, generate_narrative
from core.ai_narrative_provider import (
    AINarrativeProvider,
    NarrativeConfig,
    build_narrative,
)
from core.ai_stock_analysis_contract import (
    AnalysisRequest,
    AnalysisResult,
    DataStatus,
    MarketPhase,
    NarrativeResult,
)
from core.ai_stock_analysis_history import AnalysisHistoryStore
from core.egx_session import (
    AUCTION_END,
    CAIRO,
    CONTINUOUS_CLOSE,
    REGULAR_OPEN,
    cairo_now,
    egx_session_phase,
)

HistoryLoader = Callable[[str], pd.DataFrame]
LiveQuoteProvider = Callable[[str], dict | None]
IntradayProvider = Callable[[str], pd.DataFrame | None]

# Router status → contract DataStatus for the data-block (no-frame) path.
_ROUTER_STATUS_TO_DATA_STATUS = {
    "DATA_INSUFFICIENT": DataStatus.HISTORY_INSUFFICIENT,
    "DATA_UNAVAILABLE": DataStatus.DATA_UNAVAILABLE,
    "EXCLUDED_NON_EQUITY": DataStatus.DATA_UNAVAILABLE,
    "VOLUME_POLICY_UNRESOLVED": DataStatus.VOLUME_UNSAFE,
}


class AnalysisResponse:
    """Bundle returned to callers: evidence result + narrative + optional history record."""

    __slots__ = ("result", "narrative", "history_record")

    def __init__(self, result, narrative, history_record=None):
        self.result = result
        self.narrative = narrative
        self.history_record = history_record


# --------------------------------------------------------------------------- #
# Market phase
# --------------------------------------------------------------------------- #

def resolve_market_phase(now: datetime | None = None) -> MarketPhase:
    """Map the current EGX session (Cairo) onto the contract ``MarketPhase``.

    Auction-aware boundaries (never classify the 14:25–14:30 tail as CONTINUOUS):

      * before 10:00                → PRE_SESSION
      * 10:00 .. before 14:15       → CONTINUOUS
      * 14:15 .. before 14:25       → CLOSING_AUCTION
      * 14:25 and later             → CLOSED

    Weekend/holiday validity is checked first, so the clock never overrides a non-trading
    day. The closing auction is a distinct phase and must not be folded into continuous
    high/low, expected-range, scalping, or live-readiness logic downstream.
    """
    phase = egx_session_phase(now)          # holiday/weekend aware
    if phase == "WEEKEND":
        return MarketPhase.WEEKEND
    if phase == "HOLIDAY":
        return MarketPhase.HOLIDAY
    # Trading day → band strictly by Cairo time-of-day.
    local_t = cairo_now(now).timetz().replace(tzinfo=None)
    if local_t < REGULAR_OPEN:              # < 10:00
        return MarketPhase.PRE_SESSION
    if local_t < CONTINUOUS_CLOSE:          # 10:00 .. < 14:15
        return MarketPhase.CONTINUOUS
    if local_t < AUCTION_END:               # 14:15 .. < 14:25
        return MarketPhase.CLOSING_AUCTION
    return MarketPhase.CLOSED               # >= 14:25 (incl. 14:25–14:30)


# --------------------------------------------------------------------------- #
# Default dependency implementations (all replaceable)
# --------------------------------------------------------------------------- #

def _default_history_loader(symbol: str) -> pd.DataFrame:
    """History for ONE symbol, including the tiers held for manual review.

    ``allow_held`` is on here and nowhere else in this service's callers. This
    page is the case the option was written for: one symbol, asked for by hand,
    for display and analysis only, with production execution disabled. Sixteen
    symbols -- JUFO and MTIE among them -- were classified TIER_D on
    2026-08-27 and have a complete current EODHD series sitting unused behind
    that classification; refusing to show it here reported "insufficient data"
    about a symbol with 3,551 sessions.

    The frame comes back marked ``automatic_use_permitted: False`` with a
    ``held_reason``, both of which travel into ``DataQualitySummary`` and are
    rendered on the page. Nothing automatic may act on it.
    """
    from core.research_router import get_current_research_history
    return get_current_research_history(symbol, allow_held=True)


def _rubix_provider():
    """Build the existing read-only provider from centralized deployment settings."""
    from providers.rubix_sqlite_provider import RubixSQLiteProvider

    db_path = os.getenv("RUBIX_DB_PATH")
    if not db_path:
        settings_path = Path(__file__).resolve().parents[1] / "config" / "settings.json"
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            db_path = settings.get("market_data", {}).get("rubix_db_path")
        except (OSError, ValueError, TypeError):
            db_path = None
    return RubixSQLiteProvider(db_path=db_path)


def _default_live_quote(symbol: str) -> dict | None:
    """Best-effort Rubix overlay. Any failure (no config/DB) degrades to None."""
    try:
        overlay = _rubix_provider().quote_overlay(symbol)
        return overlay if isinstance(overlay, dict) else None
    except Exception:
        return None


def _default_intraday(symbol: str) -> pd.DataFrame | None:
    """Best-effort read-only Rubix minute series; never contacts a network."""
    try:
        return _rubix_provider().load_history(symbol, "5d", "1m")
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def _validate_single_symbol(symbol) -> str:
    """Enforce the one-symbol-only contract; reject collections and empties."""
    if isinstance(symbol, (list, tuple, set, frozenset)) or (
            isinstance(symbol, Iterable) and not isinstance(symbol, str)):
        raise ValueError("analyze_symbol handles exactly one symbol; got a collection")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    return symbol.strip().upper()


def analyze_symbol(
    symbol: str,
    *,
    as_of: str | None = None,
    now: datetime | None = None,
    lookback_days: int = 250,
    include_live: bool = True,
    language: str = "ar",
    requested_by: str | None = None,
    request_id: str | None = None,
    history_loader: HistoryLoader | None = None,
    live_quote_provider: LiveQuoteProvider | None = None,
    intraday_provider: IntradayProvider | None = None,
    narrative_generator: NarrativeGenerator | None = None,
    narrative_model: str | None = None,
    narrative_config: NarrativeConfig | None = None,
    narrative_provider: AINarrativeProvider | None = None,
    history_store: AnalysisHistoryStore | None = None,
) -> AnalysisResponse:
    """Analyze exactly one manually-requested EGX symbol.

    Returns an :class:`AnalysisResponse` (evidence result + Arabic narrative + optional
    history record). Never scans the universe and never raises on a data block — an
    unavailable/insufficient symbol yields a DATA_INSUFFICIENT result.
    """
    sym = _validate_single_symbol(symbol)
    market_phase = resolve_market_phase(now)
    generated_at = _now_iso(now)
    request = AnalysisRequest(
        symbol=sym,
        request_id=request_id or f"req-{uuid.uuid4().hex[:12]}",
        as_of=as_of or generated_at,
        market_phase=market_phase,
        lookback_days=int(lookback_days),
        include_live=bool(include_live),
        language=language,
        requested_by=requested_by,
    )

    loader = history_loader or _default_history_loader
    try:
        frame = loader(sym)
    except Exception as error:  # ResearchDataUnavailable and any loader failure
        result = _insufficient_from_error(request, market_phase, generated_at, error)
        return _finish(result, request, language, narrative_generator, narrative_model,
                       history_store, generated_at, narrative_config=narrative_config,
                       narrative_provider=narrative_provider)

    if frame is None or getattr(frame, "empty", True):
        result = evidence.build_insufficient_evidence(
            request, market_phase=market_phase, generated_at=generated_at,
            status=DataStatus.DATA_UNAVAILABLE, reason="loader returned no history")
        return _finish(result, request, language, narrative_generator, narrative_model,
                       history_store, generated_at, narrative_config=narrative_config,
                       narrative_provider=narrative_provider)

    live_quote = None
    intraday_frame = None
    if include_live:
        provider = live_quote_provider or _default_live_quote
        try:
            live_quote = provider(sym)
        except Exception:
            live_quote = None
        minute_loader = intraday_provider or _default_intraday
        try:
            intraday_frame = minute_loader(sym)
        except Exception:
            intraday_frame = None

    # A stored quote is not "live" outside the continuous/auction session and an older
    # session's last print is never presented as current. The intraday chart may still
    # show the completed Rubix session, clearly typed as historical chart evidence.
    if not _live_quote_is_current(live_quote, market_phase, generated_at):
        live_quote = None

    result = evidence.build_evidence(
        request, frame, market_phase=market_phase, live_quote=live_quote,
        intraday_frame=intraday_frame, generated_at=generated_at)
    return _finish(result, request, language, narrative_generator, narrative_model,
                   history_store, generated_at, narrative_config=narrative_config,
                   narrative_provider=narrative_provider)


def regenerate_narrative(
    response: AnalysisResponse,
    *,
    narrative_config: NarrativeConfig | None = None,
    narrative_provider: AINarrativeProvider | None = None,
    language: str | None = None,
    force_refresh: bool = True,
    history_store: AnalysisHistoryStore | None = None,
    now: datetime | None = None,
) -> AnalysisResponse:
    """Rebuild the narrative for an ALREADY-COMPUTED analysis. Nothing else re-runs.

    No indicator is recomputed, no provider is contacted and the evidence object is reused
    unchanged — only Layer 2 is produced again. ``force_refresh`` bypasses the narrative
    cache so a manual button can genuinely ask again for the same evidence hash; leave it
    False to honour an already-validated cached narrative.
    """
    result = response.result
    narrative = build_narrative(
        result, config=narrative_config, provider=narrative_provider,
        language=language or result.request.language, force_refresh=force_refresh)
    record = response.history_record
    if history_store is not None:
        record = history_store.record_analysis(result, narrative,
                                               created_at=_now_iso(now))
    return AnalysisResponse(result=result, narrative=narrative, history_record=record)


def _finish(result, request, language, narrative_generator, narrative_model,
            history_store, generated_at, narrative_config=None,
            narrative_provider=None, force_narrative_refresh=False) -> AnalysisResponse:
    """Attach the narrative, then optionally append one immutable history record.

    A caller-supplied ``narrative_generator`` keeps the original V1 contract (a callable
    returning a field dict, validated by ``generate_narrative``). Otherwise the guarded
    external-AI path runs; it degrades to the deterministic fallback on every failure, so
    the analysis itself always completes.
    """
    if narrative_generator is not None:
        narrative = generate_narrative(
            result, generator=narrative_generator, model=narrative_model,
            language=language)
    else:
        narrative = build_narrative(
            result, config=narrative_config, provider=narrative_provider,
            language=language, force_refresh=force_narrative_refresh)
    record = None
    if history_store is not None:
        record = history_store.record_analysis(result, narrative, created_at=generated_at)
    return AnalysisResponse(result=result, narrative=narrative, history_record=record)


def _insufficient_from_error(request, market_phase, generated_at, error) -> AnalysisResult:
    status = _ROUTER_STATUS_TO_DATA_STATUS.get(
        getattr(error, "status", None), DataStatus.DATA_UNAVAILABLE)
    reason = str(error) or "current-research history unavailable"
    provider = "eodhd"
    return evidence.build_insufficient_evidence(
        request, market_phase=market_phase, generated_at=generated_at, status=status,
        reason=reason, provider=provider)


def _now_iso(now: datetime | None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(CAIRO).isoformat()


def _live_quote_is_current(live_quote, market_phase, generated_at):
    if not live_quote or market_phase not in (
            MarketPhase.CONTINUOUS, MarketPhase.CLOSING_AUCTION):
        return False
    raw = live_quote.get("quote_timestamp") or live_quote.get("timestamp")
    if not raw:
        return False
    try:
        quote_date = pd.Timestamp(raw)
        if quote_date.tzinfo is None:
            quote_date = quote_date.tz_localize("UTC")
        request_date = pd.Timestamp(generated_at)
        if request_date.tzinfo is None:
            request_date = request_date.tz_localize(CAIRO)
        return quote_date.tz_convert(CAIRO).date() == request_date.tz_convert(CAIRO).date()
    except (TypeError, ValueError):
        return False
