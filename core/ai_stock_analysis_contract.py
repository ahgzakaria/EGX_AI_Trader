"""Shared interface contract for the AI Stock Analysis On Demand feature.

This module is the SINGLE shared contract between the two parallel development
worktrees (Claude Core = evidence/narrative/service/history; Codex UI = dashboard +
card generator). It defines TYPES ONLY — no calculation, no I/O, no provider access,
no strategy logic. Neither worktree may change this file during parallel work; it is
integration-only.

Four layers are kept strictly separate, and they may never be collapsed:

  1. CALCULATED NUMERIC EVIDENCE  — every number the feature uses. Produced by the core
     evidence engine from EODHD (current research) + Rubix (live / daily bridge). This is
     the ONLY source of truth for numeric values.
  2. AI-WRITTEN NARRATIVE         — human-readable text describing the evidence. It cites
     evidence fields; it MUST NEVER originate, alter, or become the source of any numeric
     value. If a number appears in narrative text it is a rendering of an evidence field,
     never a new fact.
  3. UI PRESENTATION              — how evidence + narrative are laid out on screen.
  4. EXPORTED-CARD CONTENT        — the self-contained shareable card, composed from
     already-computed evidence and already-written narrative.

Data-architecture invariants reflected here (not enforced here — the core engine enforces
them): current research is EODHD, live is Rubix, unsupported symbols use the Rubix Daily
Bridge over a frozen Yahoo seed, Yahoo is never an operational or comparison source, and
production/broker execution stay disabled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

CONTRACT_VERSION = "ai_stock_analysis_contract@1.0.0"


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #

class MarketPhase(str, Enum):
    PRE_SESSION = "PRE_SESSION"
    CONTINUOUS = "CONTINUOUS"
    CLOSING_AUCTION = "CLOSING_AUCTION"
    CLOSED = "CLOSED"
    HOLIDAY = "HOLIDAY"
    WEEKEND = "WEEKEND"


class Recommendation(str, Enum):
    WAIT = "WAIT"
    WATCH = "WATCH"
    NEAR_READY = "NEAR_READY"
    READY_WITH_CONDITIONS = "READY_WITH_CONDITIONS"
    AVOID = "AVOID"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


class ScenarioState(str, Enum):
    READY_WITH_CONDITIONS = "READY_WITH_CONDITIONS"
    NEAR_READY = "NEAR_READY"
    WAIT = "WAIT"
    INVALID = "INVALID"
    AVOID = "AVOID"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


class DataStatus(str, Enum):
    CURRENT = "CURRENT"
    CACHE_MODE = "CACHE_MODE"
    LIVE_UNAVAILABLE = "LIVE_UNAVAILABLE"
    VOLUME_UNSAFE = "VOLUME_UNSAFE"
    HISTORY_INSUFFICIENT = "HISTORY_INSUFFICIENT"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AnalysisRequest:
    """One on-demand analysis request. Inputs only; carries no computed values."""
    symbol: str
    request_id: str
    as_of: str                                   # ISO-8601 Cairo timestamp
    market_phase: MarketPhase
    lookback_days: int = 250
    include_live: bool = True
    language: str = "ar"                         # narrative language preference
    requested_by: str | None = None


# --------------------------------------------------------------------------- #
# Layer 1 — CALCULATED NUMERIC EVIDENCE (the only source of numbers)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PriceSummary:
    """Calculated price evidence. Numbers only — no prose."""
    symbol: str
    latest_completed_session: str
    last_close: float
    prev_close: float | None = None
    change_pct: float | None = None
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    currency: str = "EGP"
    price_series: str = "SPLIT_ADJUSTED"         # or PROJECT_LOCAL_SEED_PLUS_RUBIX
    price_adjustment_policy: str = "SPLIT_ADJUSTED_ALL_EVENTS"
    live_quote_last: float | None = None
    live_quote_timestamp: str | None = None


@dataclass(frozen=True)
class IndicatorSummary:
    """Calculated indicator evidence. Volume provenance travels with the numbers."""
    symbol: str
    computed_from_sessions: int
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    rsi_14: float | None = None
    atr_14: float | None = None
    avg_volume_20: float | None = None
    avg_turnover_egp_20: float | None = None
    volume_series: str = "RAW_EODHD"             # never a universal-multiplied series
    volume_adjustment_policy: str = "NONE"
    volume_safe_for_lookback: bool = True
    latest_action_in_lookback: str | None = None


@dataclass(frozen=True)
class KeyLevel:
    """One calculated price level. ``basis`` records how it was derived (auditable)."""
    kind: str                                    # SUPPORT / RESISTANCE / PIVOT / ENTRY / TARGET / STOP
    price: float
    basis: str
    distance_pct: float | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class ScenarioResult:
    """One calculated decision scenario. Conditions are machine facts, not narrative."""
    scenario_id: str
    title: str
    state: ScenarioState
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    target: float | None = None
    invalidation: float | None = None
    reward_risk_ratio: float | None = None
    conditions: tuple[str, ...] = ()
    missing_confirmations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfidenceComponent:
    """One weighted contributor to overall confidence (numeric)."""
    name: str
    weight: float
    score: float                                 # 0..100


@dataclass(frozen=True)
class ConfidenceBreakdown:
    """Calculated confidence. ``overall`` is derived from ``components`` by the engine."""
    overall: float                               # 0..100
    method_version: str
    components: tuple[ConfidenceComponent, ...] = ()


@dataclass(frozen=True)
class DataQualitySummary:
    """Calculated data-quality/provenance evidence. Yahoo network use must be False."""
    status: DataStatus
    data_domain: str = "CURRENT_RESEARCH_V2"
    provider: str = "eodhd"                       # eodhd | local_plus_rubix
    freshness_status: str = "UNKNOWN"
    expected_completed_session: str | None = None
    latest_completed_session: str | None = None
    history_sufficient: bool = False
    volume_safe_for_lookback: bool = True
    live_provider: str = "rubix"
    live_available: bool = False
    yahoo_network_used: bool = False
    yahoo_seed_present: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnalysisResult:
    """Aggregate CALCULATED evidence — the single numeric source of truth for a request.

    ``evidence_version`` uniquely identifies this evidence set; narrative and card content
    reference it so a reader can prove which numbers a given narrative described.
    """
    request: AnalysisRequest
    price: PriceSummary
    indicators: IndicatorSummary
    confidence: ConfidenceBreakdown
    data_quality: DataQualitySummary
    recommendation: Recommendation
    market_phase: MarketPhase
    evidence_version: str
    generated_at: str
    key_levels: tuple[KeyLevel, ...] = ()
    scenarios: tuple[ScenarioResult, ...] = ()
    recommendation_reasons: tuple[str, ...] = ()   # machine reasons, not AI prose
    evidence_hash: str | None = None               # fingerprint for reproducibility/history


# --------------------------------------------------------------------------- #
# Layer 2 — AI-WRITTEN NARRATIVE (never a source of numbers)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class NarrativeResult:
    """AI-written text describing an AnalysisResult.

    Invariant: narrative NEVER originates numeric values. Any figure it mentions is a
    rendering of an evidence field from the AnalysisResult identified by
    ``derived_from_evidence_version``. ``contains_no_original_numbers`` documents this
    contract; the core narrative engine is responsible for upholding it.
    """
    request_id: str
    symbol: str
    language: str
    headline: str
    summary: str
    rationale: str
    risks: str
    disclaimer: str
    model: str
    derived_from_evidence_version: str
    contains_no_original_numbers: bool = True


# --------------------------------------------------------------------------- #
# Layer 3/4 — UI PRESENTATION + EXPORTED-CARD CONTENT
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CardPayload:
    """Self-contained exported-card content, composed from evidence + narrative.

    All values are PRE-FORMATTED display strings produced by the UI/card layer from the
    evidence and narrative — the card never re-derives numbers. Rows are (label, value)
    display pairs so the card renderer stays free of calculation.
    """
    symbol: str
    title: str
    as_of_label: str
    recommendation_label: str
    price_rows: tuple[tuple[str, str], ...] = ()
    level_rows: tuple[tuple[str, str], ...] = ()
    scenario_rows: tuple[tuple[str, str], ...] = ()
    confidence_label: str = ""
    data_quality_label: str = ""
    narrative_headline: str = ""
    narrative_summary: str = ""
    disclaimer: str = ""
    theme: str = "dark"
    language: str = "ar"
    export_format: str = "PNG"                    # PNG | SVG | HTML
    evidence_version: str = ""
    narrative_model: str | None = None


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AnalysisHistoryRecord:
    """A durable record of one completed analysis (evidence + narrative provenance)."""
    record_id: str
    symbol: str
    created_at: str
    market_phase: MarketPhase
    recommendation: Recommendation
    data_status: DataStatus
    evidence_version: str
    confidence_overall: float
    summary_snapshot: str
    evidence_hash: str
    narrative_model: str | None = None
    language: str = "ar"


# The public contract surface. Import from here; do not redefine these elsewhere.
__all__ = [
    "CONTRACT_VERSION",
    "MarketPhase", "Recommendation", "ScenarioState", "DataStatus",
    "AnalysisRequest", "PriceSummary", "IndicatorSummary", "KeyLevel",
    "ScenarioResult", "ConfidenceComponent", "ConfidenceBreakdown",
    "DataQualitySummary", "AnalysisResult", "NarrativeResult", "CardPayload",
    "AnalysisHistoryRecord",
]
