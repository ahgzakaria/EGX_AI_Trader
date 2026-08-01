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

CONTRACT_VERSION = "ai_stock_analysis_contract@1.3.0"


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


class PullbackState(str, Enum):
    """Research-only lifecycle for the daily pullback-continuation scenario."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    DEVELOPING_PULLBACK = "DEVELOPING_PULLBACK"
    WAIT_REVERSAL_CONFIRMATION = "WAIT_REVERSAL_CONFIRMATION"
    HEALTHY_PULLBACK = "HEALTHY_PULLBACK"
    DEEP_PULLBACK = "DEEP_PULLBACK"
    CONFIRMED_PULLBACK_ENTRY = "CONFIRMED_PULLBACK_ENTRY"
    FAILED_PULLBACK = "FAILED_PULLBACK"


class DataStatus(str, Enum):
    CURRENT = "CURRENT"
    CACHE_MODE = "CACHE_MODE"
    LIVE_UNAVAILABLE = "LIVE_UNAVAILABLE"
    VOLUME_UNSAFE = "VOLUME_UNSAFE"
    HISTORY_INSUFFICIENT = "HISTORY_INSUFFICIENT"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class TrendState(str, Enum):
    STRONG_UPTREND = "STRONG_UPTREND"
    UPTREND = "UPTREND"
    SIDEWAYS = "SIDEWAYS"
    DOWNTREND = "DOWNTREND"
    STRONG_DOWNTREND = "STRONG_DOWNTREND"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


class MomentumState(str, Enum):
    STRONG_POSITIVE = "STRONG_POSITIVE"
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    STRONG_NEGATIVE = "STRONG_NEGATIVE"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


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
    """Calculated price evidence. Numbers only — no prose.

    Every value is a dedicated typed field. Missing values stay ``None`` — they are never
    zero-filled — so the UI can distinguish "unknown" from a genuine zero. The live block
    (``last``/``bid``/``ask``/``spread_percent``/``quote_timestamp``) is populated from the
    Rubix overlay only when a live quote is available; otherwise it stays ``None``.
    """
    symbol: str
    session_date: str | None = None              # ISO date of the latest completed session
    close: float | None = None                   # latest completed session close
    previous_close: float | None = None
    change_amount: float | None = None
    change_percent: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None                  # latest completed session volume
    turnover: float | None = None                # latest completed session turnover (EGP)
    currency: str = "EGP"
    price_series: str = "SPLIT_ADJUSTED"         # or PROJECT_LOCAL_SEED_PLUS_RUBIX
    price_adjustment_policy: str = "SPLIT_ADJUSTED_ALL_EVENTS"
    # Live Rubix overlay (None when the market is closed or no live quote exists).
    last: float | None = None                    # latest traded price (live)
    bid: float | None = None
    ask: float | None = None
    spread_percent: float | None = None
    quote_timestamp: str | None = None


@dataclass(frozen=True)
class IndicatorSummary:
    """Calculated indicator evidence — every technical number as a dedicated typed field.

    SMA and EMA are kept strictly separate; an EMA value never occupies an SMA field.
    Volume-derived fields (``average_volume_20``, ``volume_ratio``, ``obv``, ``turnover``)
    are ``None`` when provenance says volume is not lookback-safe (``volume_safe`` False),
    so they can never inflate confidence or a narrative. No consumer should parse numbers
    out of strings — all numbers live here. Volume provenance travels with the numbers.
    """
    symbol: str
    computed_from_sessions: int
    trend: TrendState = TrendState.DATA_INSUFFICIENT
    trend_strength: float | None = None           # 0..100 deterministic evidence score
    momentum: MomentumState = MomentumState.DATA_INSUFFICIENT
    momentum_strength: float | None = None        # 0..100 deterministic evidence score
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    ema_20: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None
    rsi_14: float | None = None
    macd: float | None = None                    # MACD line (EMA_fast - EMA_slow)
    macd_signal: float | None = None
    macd_histogram: float | None = None
    atr_14: float | None = None
    average_volume_20: float | None = None
    volume_ratio: float | None = None            # latest volume / average_volume_20
    obv: float | None = None                     # on-balance volume (None when unsafe)
    expected_range_position: float | None = None  # 0..100 within the recent H/L channel
    turnover: float | None = None                # average turnover EGP over 20 sessions
    volume_safe: bool = True                      # mirrors volume_safe_for_lookback
    volume_series: str = "RAW_EODHD"             # never a universal-multiplied series
    volume_adjustment_policy: str = "NONE"
    latest_action_in_lookback: str | None = None


@dataclass(frozen=True)
class KeyLevel:
    """One calculated price level. ``basis`` records how it was derived (auditable)."""
    kind: str                                    # SUPPORT / RESISTANCE / PIVOT / ENTRY / TARGET / STOP
    price: float
    basis: str
    timeframe: str = "1D"
    touches: int | None = None
    last_touch_date: str | None = None
    distance_percent: float | None = None
    strength: float | None = None                 # 0..1, calculated by the Core
    confidence: float | None = None

    @property
    def distance_pct(self):
        """Compatibility accessor for pre-v1.1 consumers; no value is recalculated."""
        return self.distance_percent


@dataclass(frozen=True)
class ChartPoint:
    """One provider-supplied OHLCV point; the UI must not derive or mutate it."""
    timestamp: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    source: str = ""
    session_phase: str | None = None


@dataclass(frozen=True)
class ChartSeries:
    """Typed chart payload with closing-auction points explicitly separated."""
    timeframe: str
    session_date: str | None
    points: tuple[ChartPoint, ...] = ()
    continuous_points: tuple[ChartPoint, ...] = ()
    auction_points: tuple[ChartPoint, ...] = ()
    latest_completed_session: str | None = None
    source: str = ""
    data_status: str = "UNAVAILABLE"


@dataclass(frozen=True)
class ScenarioResult:
    """One calculated decision scenario. Every price/level is a dedicated typed field.

    ``confirmation_requirements`` and ``invalidation_conditions`` are machine-fact string
    tuples describing *what* must happen — never numbers encoded as prose. The numbers a UI
    needs (trigger, entry band, target, stop, remaining room, R:R, scenario confidence) are
    all typed fields here.
    """
    scenario_id: str
    title: str
    state: ScenarioState
    trigger: float | None = None                 # price that activates the scenario
    entry_low: float | None = None
    entry_high: float | None = None
    target: float | None = None
    stop: float | None = None                    # invalidation / protective stop price
    remaining_room_percent: float | None = None  # % from last close to target
    risk_reward: float | None = None
    confidence: float | None = None              # 0..1 scenario-level confidence
    confirmation_requirements: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class PullbackScenarioResult:
    """Typed, deterministic D-1 evidence for the research-only pullback scenario.

    Missing or unreliable measurements remain ``None`` and are named in
    ``missing_measurements``.  The scenario is independent of the production decision and
    of the EMA5/EMA10 scalping strategy.
    """

    state: PullbackState
    research_only: bool = True
    research_score: float | None = None
    prior_trend_status: str = "UNAVAILABLE"
    historical_data_cutoff: str | None = None
    swing_high: float | None = None
    swing_high_date: str | None = None
    swing_high_confirmation_date: str | None = None
    impulse_low: float | None = None
    impulse_low_date: str | None = None
    current_price: float | None = None
    pullback_percent: float | None = None
    pullback_atr: float | None = None
    impulse_retracement_percent: float | None = None
    correction_bars: int | None = None
    impulse_strength_atr: float | None = None
    impulse_duration_bars: int | None = None
    ema20_slope_percent_per_bar: float | None = None
    ema50_slope_percent_per_bar: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    price_vs_ema20_percent: float | None = None
    price_vs_ema50_percent: float | None = None
    ema_alignment_status: str = "UNAVAILABLE"
    structure_status: str = "UNRELIABLE"
    depth_classification: str = "UNAVAILABLE"
    support_zone_low: float | None = None
    support_zone_high: float | None = None
    support_reached: bool = False
    support_confluence: tuple[str, ...] = ()
    support_distances_percent: tuple[tuple[str, float], ...] = ()
    volume_behaviour: str = "UNAVAILABLE"
    impulse_average_volume: float | None = None
    correction_average_volume: float | None = None
    current_volume_ratio: float | None = None
    selling_volume_ratio: float | None = None
    correction_to_impulse_volume_ratio: float | None = None
    confirmation_status: str = "NOT_CONFIRMED"
    confirmation_reasons: tuple[str, ...] = ()
    entry_trigger: float | None = None
    stop_loss: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    major_resistance: float | None = None
    minor_pivot_resistance: float | None = None
    meaningful_structural_target: float | None = None
    broader_structural_target: float | None = None
    risk_amount: float | None = None
    reward_risk: float | None = None
    reward_risk_meaningful_target: float | None = None
    reward_risk_broader_target: float | None = None
    invalidation_reason: str | None = None
    explanation_ar: str = ""
    explanation_en: str = ""
    evidence: tuple[str, ...] = ()
    missing_measurements: tuple[str, ...] = ()
    gate_results: tuple[tuple[str, str], ...] = ()
    primary_rejection_reason: str | None = None
    rejection_reasons: tuple[str, ...] = ()


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
    daily_chart_series: ChartSeries | None = None
    intraday_chart_series: ChartSeries | None = None
    evidence_hash: str | None = None               # fingerprint for reproducibility/history
    pullback_scenario: PullbackScenarioResult | None = None


# --------------------------------------------------------------------------- #
# Layer 2 — AI-WRITTEN NARRATIVE (never a source of numbers)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class NarrativeProvenance:
    """Honest origin of one narrative — auditable, and free of secret material.

    ``source`` is one of ``AI_NARRATIVE`` (an external model answered AND passed every
    validation), ``DETERMINISTIC_FALLBACK`` (the evidence-only writer produced the text),
    or ``AI_UNAVAILABLE`` (no narrative at all). It is never optimistic: an external call
    that failed validation is recorded as a fallback, with the safe rejection reason in
    ``fallback_reason``. No API key, prompt body, or raw provider message may be stored in
    any field here.
    """
    source: str = "DETERMINISTIC_FALLBACK"
    provider: str = "none"
    model: str = ""
    prompt_version: str = ""
    evidence_hash: str = ""
    generated_at: str = ""
    validation_status: str = "NOT_ATTEMPTED"
    latency_ms: int | None = None
    cached: bool = False
    fallback_reason: str = ""


@dataclass(frozen=True)
class NarrativeResult:
    """AI-written text describing an AnalysisResult.

    Invariant: narrative NEVER originates numeric values. Any figure it mentions is a
    rendering of an evidence field from the AnalysisResult identified by
    ``derived_from_evidence_version``. ``contains_no_original_numbers`` documents this
    contract; the core narrative engine is responsible for upholding it.

    ``sections`` carries the optional structured Arabic sections produced by an external
    model (executive summary, technical read, positive/negative scenarios, confirmation and
    invalidation conditions, risk notes, data limitations) as ordered ``(name, text)``
    pairs. It is empty for the deterministic fallback. ``provenance`` records where the
    text actually came from; it is ``None`` only for pre-v1.2 callers.
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
    sections: tuple[tuple[str, str], ...] = ()
    provenance: NarrativeProvenance | None = None


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
    company_name: str = ""
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
    # Honest narrative origin for the printed card line; see ``NarrativeProvenance``.
    narrative_source: str = ""


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AnalysisHistoryRecord:
    """A durable record of one completed analysis (evidence + narrative provenance).

    The narrative block is metadata only — origin, provider, model, prompt version,
    validation outcome, latency and (when applicable) the safe fallback reason. It never
    stores an API key, a prompt, or a rejected narrative body.
    """
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
    narrative_source: str = "DETERMINISTIC_FALLBACK"
    narrative_provider: str = "none"
    narrative_prompt_version: str = ""
    narrative_validation_status: str = "NOT_ATTEMPTED"
    narrative_generated_at: str = ""
    narrative_latency_ms: int | None = None
    narrative_fallback_reason: str = ""


# The public contract surface. Import from here; do not redefine these elsewhere.
__all__ = [
    "CONTRACT_VERSION",
    "MarketPhase", "Recommendation", "ScenarioState", "PullbackState", "DataStatus",
    "TrendState", "MomentumState",
    "AnalysisRequest", "PriceSummary", "IndicatorSummary", "KeyLevel",
    "ChartPoint", "ChartSeries",
    "ScenarioResult", "PullbackScenarioResult",
    "ConfidenceComponent", "ConfidenceBreakdown",
    "DataQualitySummary", "AnalysisResult", "NarrativeProvenance", "NarrativeResult",
    "CardPayload",
    "AnalysisHistoryRecord",
]
