"""Presentation helpers for the AI Stock Analysis page (Layer 3 of the contract).

Everything here maps ALREADY-COMPUTED typed evidence onto display strings. Hard rules:

  * no provider access, no network, no universe scan, no indicator calculation;
  * every number rendered comes from a dedicated typed field on the contract objects —
    never parsed out of a narrative string or a machine recommendation reason;
  * a missing value renders as an em dash, never as ``0.00``;
  * SMA and EMA stay in separate rows; volume-derived fields are suppressed when the
    evidence says volume is not lookback-safe.

The module is deliberately Streamlit-free apart from the small render helpers at the end,
so the mapping logic can be unit-tested without a Streamlit runtime.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from core.ai_stock_analysis_contract import (
    AnalysisRequest,
    AnalysisResult,
    ConfidenceBreakdown,
    ConfidenceComponent,
    DataQualitySummary,
    DataStatus,
    IndicatorSummary,
    KeyLevel,
    MarketPhase,
    NarrativeResult,
    PriceSummary,
    Recommendation,
    ScenarioResult,
    ScenarioState,
)
from core.analysis_card_generator import (
    CARD_SIZES,
    CardChartData,
    DEFAULT_CARD_SIZE,
    render_card_png,
)
from dashboard.formatting import (
    EM_DASH,
    fmt_compact,
    fmt_percent,
    fmt_price,
    fmt_turnover,
    fmt_volume,
)

FIXTURE_PATH = Path("tests/fixtures/ai_stock_analysis_evidence.json")

# The deterministic narrative model id published by the core narrative engine.
try:  # pragma: no cover - the constant is stable; the guard keeps imports resilient
    from core.ai_analysis_narrative import FALLBACK_MODEL
except Exception:  # pragma: no cover
    FALLBACK_MODEL = "deterministic-fallback@1.0.0"


# --------------------------------------------------------------------------- #
# Labels — Arabic primary, English secondary
# --------------------------------------------------------------------------- #

# The ONLY recommendation labels this page may show. There is no unconditional
# "BUY" label anywhere in this map, by design.
RECOMMENDATION_LABELS = {
    Recommendation.WAIT: ("انتظار", "Wait", "gray"),
    Recommendation.WATCH: ("مراقبة", "Watch", "blue"),
    Recommendation.NEAR_READY: ("قريب من التفعيل", "Near Activation", "amber"),
    Recommendation.READY_WITH_CONDITIONS: ("جاهز بشروط", "Ready With Conditions", "green"),
    Recommendation.AVOID: ("تجنب حاليًا", "Avoid For Now", "red"),
    Recommendation.DATA_INSUFFICIENT: ("البيانات غير كافية", "Data Insufficient", "red"),
}
ALLOWED_RECOMMENDATION_LABELS_AR = frozenset(
    arabic for arabic, _, _ in RECOMMENDATION_LABELS.values())

SCENARIO_STATE_LABELS = {
    ScenarioState.READY_WITH_CONDITIONS: ("جاهز بشروط", "Ready With Conditions", "green"),
    ScenarioState.NEAR_READY: ("قريب من التفعيل", "Near Activation", "amber"),
    ScenarioState.WAIT: ("انتظار", "Wait", "gray"),
    ScenarioState.INVALID: ("ملغى", "Invalidated", "red"),
    ScenarioState.AVOID: ("تجنب حاليًا", "Avoid For Now", "red"),
    ScenarioState.DATA_INSUFFICIENT: ("البيانات غير كافية", "Data Insufficient", "red"),
}

MARKET_PHASE_LABELS = {
    MarketPhase.PRE_SESSION: ("ما قبل الجلسة", "Pre-session", "blue"),
    MarketPhase.CONTINUOUS: ("الجلسة المستمرة", "Continuous Session", "green"),
    MarketPhase.CLOSING_AUCTION: ("مزاد الإغلاق", "Closing Auction", "amber"),
    MarketPhase.CLOSED: ("مغلق", "Closed", "gray"),
    MarketPhase.HOLIDAY: ("عطلة رسمية", "Holiday", "blue"),
    MarketPhase.WEEKEND: ("عطلة نهاية الأسبوع", "Weekend", "blue"),
}

DATA_STATUS_LABELS = {
    DataStatus.CURRENT: ("بيانات محدثة", "Current", "green"),
    DataStatus.CACHE_MODE: ("وضع الذاكرة المؤقتة", "Cache Mode", "amber"),
    DataStatus.LIVE_UNAVAILABLE: ("البيانات الحية غير متاحة", "Live Data Unavailable", "red"),
    DataStatus.VOLUME_UNSAFE: ("حجم التداول غير موثوق", "Volume Unsafe", "amber"),
    DataStatus.HISTORY_INSUFFICIENT: ("التاريخ غير كافٍ", "History Insufficient", "red"),
    DataStatus.DATA_UNAVAILABLE: ("البيانات غير متاحة", "Data Unavailable", "red"),
}

# Data-mode chips — the four states the page must keep visibly distinct.
MODE_LIVE_QUOTE = ("تسعيرة حية", "Live Quote", "green")
MODE_LAST_SESSION = ("آخر جلسة مكتملة", "Last Completed Session", "blue")
MODE_CACHE = ("وضع الذاكرة المؤقتة", "Cache Mode", "amber")
MODE_LIVE_UNAVAILABLE = ("البيانات الحية غير متاحة", "Live Data Unavailable", "red")

NARRATIVE_SOURCE_AI = ("سرد الذكاء الاصطناعي", "AI Narrative", "blue")
NARRATIVE_SOURCE_FALLBACK = ("سرد استنتاجي حتمي", "Deterministic Fallback", "amber")
NARRATIVE_SOURCE_UNAVAILABLE = ("السرد غير متاح", "AI Unavailable", "red")

SAFETY_BADGES = (
    ("دعم قرار فقط", "Decision Support Only", "blue"),
    ("وضع بحثي / ورقي", "Research / Paper Mode", "amber"),
    ("التنفيذ الحقيقي غير مفعّل", "Production Disabled", "red"),
)

# Canonical confidence components, in display order. A component the engine did not
# supply renders as an em dash — the page never fabricates or recomputes a score.
CONFIDENCE_COMPONENTS = (
    ("trend", "الاتجاه", "Trend"),
    ("momentum", "الزخم", "Momentum"),
    ("volume", "الحجم", "Volume"),
    ("liquidity", "السيولة", "Liquidity"),
    ("freshness", "حداثة البيانات", "Freshness"),
    ("spread", "فرق السعر", "Spread"),
    ("history", "عمق التاريخ", "History"),
    ("scenario_quality", "جودة السيناريو", "Scenario Quality"),
    ("target_room", "مساحة الهدف", "Target Room"),
)

KEY_LEVEL_KIND_LABELS = {
    "SUPPORT": ("دعم", "Support"),
    "RESISTANCE": ("مقاومة", "Resistance"),
    "PIVOT": ("محور", "Pivot"),
    "ENTRY": ("دخول", "Entry"),
    "TARGET": ("هدف", "Target"),
    "STOP": ("وقف", "Stop"),
    "BREAKOUT": ("اختراق", "Breakout"),
}


# --------------------------------------------------------------------------- #
# Small display helpers
# --------------------------------------------------------------------------- #

def dash(value, formatter=fmt_price) -> str:
    """Format a value for display; a missing value is always an em dash, never 0.00."""
    return EM_DASH if value is None else formatter(value)


def fmt_signed(value, decimals=2) -> str:
    """A signed change amount (``+1.30`` / ``-0.45``); missing → em dash."""
    if value is None:
        return EM_DASH
    try:
        number = float(value)
    except (TypeError, ValueError):
        return EM_DASH
    return f"{number:+,.{decimals}f}"


def fmt_signed_percent(value, decimals=2) -> str:
    if value is None:
        return EM_DASH
    try:
        return f"{float(value):+.{decimals}f}%"
    except (TypeError, ValueError):
        return EM_DASH


def fmt_ratio(value, decimals=2) -> str:
    if value is None:
        return EM_DASH
    try:
        return f"{float(value):.{decimals}f}×"
    except (TypeError, ValueError):
        return EM_DASH


def fmt_score(value, decimals=0) -> str:
    if value is None:
        return EM_DASH
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return EM_DASH


def fmt_confidence_fraction(value) -> str:
    """A 0..1 scenario confidence as ``55 / 100``; missing → em dash."""
    if value is None:
        return EM_DASH
    try:
        return f"{float(value) * 100:.0f} / 100"
    except (TypeError, ValueError):
        return EM_DASH


def change_tone(value) -> str:
    if value is None:
        return "gray"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "gray"
    if number > 0:
        return "green"
    return "red" if number < 0 else "gray"


# --------------------------------------------------------------------------- #
# Market phase / data mode
# --------------------------------------------------------------------------- #

def market_phase_labels(phase: MarketPhase):
    return MARKET_PHASE_LABELS.get(phase, ("غير معروف", "Unknown", "gray"))


def include_live_in_session_range(phase: MarketPhase) -> bool:
    """Closing-auction prints are never folded into a continuous-session range.

    The auction is a separate price-formation mechanism; mixing its print into the
    continuous high/low/range would misstate the session. The auction value is still
    shown — in its own block, clearly labelled.
    """
    return phase == MarketPhase.CONTINUOUS


def is_auction(phase: MarketPhase) -> bool:
    return phase == MarketPhase.CLOSING_AUCTION


def data_mode(result: AnalysisResult):
    """(arabic, english, tone) for how the displayed prices were sourced."""
    quality = result.data_quality
    price = result.price
    if quality.status == DataStatus.CACHE_MODE:
        return MODE_CACHE
    if quality.status == DataStatus.LIVE_UNAVAILABLE:
        return MODE_LIVE_UNAVAILABLE
    live_now = result.market_phase in (MarketPhase.CONTINUOUS, MarketPhase.CLOSING_AUCTION)
    if live_now:
        if quality.live_available and price.last is not None:
            return MODE_LIVE_QUOTE
        return MODE_LIVE_UNAVAILABLE
    return MODE_LAST_SESSION


# --------------------------------------------------------------------------- #
# Price summary
# --------------------------------------------------------------------------- #

def price_summary_rows(result: AnalysisResult):
    """Typed PriceSummary → ``(arabic, english, value, tone, group)`` display rows.

    ``group`` is ``live`` for the Rubix overlay block and ``session`` for the latest
    completed session, so the page can keep an auction/live print visually separate from
    completed-session figures.
    """
    price = result.price
    currency = price.currency or ""
    live_label = "آخر سعر" if price.last is not None else "الإغلاق"
    live_label_en = "Last" if price.last is not None else "Close"
    headline_value = price.last if price.last is not None else price.close

    rows = [
        (live_label, live_label_en, dash(headline_value), "gray",
         "live" if price.last is not None else "session"),
        ("التغير", "Change", fmt_signed(price.change_amount),
         change_tone(price.change_amount), "session"),
        ("نسبة التغير", "Change %", fmt_signed_percent(price.change_percent),
         change_tone(price.change_percent), "session"),
        ("الافتتاح", "Open", dash(price.open), "gray", "session"),
        ("الأعلى", "High", dash(price.high), "gray", "session"),
        ("الأدنى", "Low", dash(price.low), "gray", "session"),
        ("إغلاق سابق", "Previous Close", dash(price.previous_close), "gray", "session"),
        ("حجم التداول", "Volume", dash(price.volume, fmt_volume), "gray", "session"),
        ("قيمة التداول", "Turnover", dash(price.turnover, fmt_turnover), "gray", "session"),
        ("أفضل شراء", "Bid", dash(price.bid), "gray", "live"),
        ("أفضل بيع", "Ask", dash(price.ask), "gray", "live"),
        ("فرق السعر", "Spread", dash(price.spread_percent, fmt_percent), "gray", "live"),
    ]
    if currency:
        rows[0] = (rows[0][0], f"{rows[0][1]} ({currency})", rows[0][2], rows[0][3], rows[0][4])
    return rows


# --------------------------------------------------------------------------- #
# Technical overview
# --------------------------------------------------------------------------- #

def volume_analysis_available(indicators: IndicatorSummary) -> bool:
    """Volume-derived readings are shown only when evidence says volume is safe."""
    return bool(indicators.volume_safe)


def indicator_groups(indicators: IndicatorSummary):
    """Typed IndicatorSummary → grouped display rows.

    SMA and EMA are separate groups so an EMA value can never be read as an SMA. When
    ``volume_safe`` is False the whole volume group is withheld — Volume Ratio and OBV are
    not rendered at all, rather than rendered as zero or as a stale figure.
    """
    groups = [
        ("المتوسطات المتحركة البسيطة", "Simple Moving Averages", [
            ("SMA 20", "SMA 20", dash(indicators.sma_20)),
            ("SMA 50", "SMA 50", dash(indicators.sma_50)),
            ("SMA 200", "SMA 200", dash(indicators.sma_200)),
        ]),
        ("المتوسطات المتحركة الأسية", "Exponential Moving Averages", [
            ("EMA 20", "EMA 20", dash(indicators.ema_20)),
            ("EMA 50", "EMA 50", dash(indicators.ema_50)),
            ("EMA 200", "EMA 200", dash(indicators.ema_200)),
        ]),
        ("الزخم", "Momentum", [
            ("مؤشر القوة النسبية 14", "RSI 14", fmt_score(indicators.rsi_14, 1)),
            ("MACD", "MACD", fmt_score(indicators.macd, 3)),
            ("إشارة MACD", "MACD Signal", fmt_score(indicators.macd_signal, 3)),
            ("هيستوجرام MACD", "MACD Histogram", fmt_score(indicators.macd_histogram, 3)),
        ]),
        ("التقلب والمدى", "Volatility & Range", [
            ("متوسط المدى الحقيقي 14", "ATR 14", dash(indicators.atr_14)),
            ("موضع المدى المتوقع", "Expected Range Position",
             dash(indicators.expected_range_position, fmt_percent)),
        ]),
    ]
    if volume_analysis_available(indicators):
        groups.append(("الحجم والسيولة", "Volume & Liquidity", [
            ("متوسط الحجم 20", "Average Volume 20",
             dash(indicators.average_volume_20, fmt_volume)),
            ("نسبة الحجم", "Volume Ratio", fmt_ratio(indicators.volume_ratio)),
            ("الحجم على الرصيد", "OBV", dash(indicators.obv, fmt_compact)),
            ("قيمة التداول", "Turnover", dash(indicators.turnover, fmt_turnover)),
        ]))
    return groups


VOLUME_UNAVAILABLE_LABELS = ("تحليل الحجم غير متاح", "Volume Analysis Unavailable")


def trend_reading(result: AnalysisResult):
    """Render the Core-owned typed trend. No comparison or classification occurs here."""
    value = getattr(result.indicators.trend, "value", result.indicators.trend)
    labels = {
        "STRONG_UPTREND": ("اتجاه صاعد قوي", "Strong Uptrend", "green"),
        "UPTREND": ("اتجاه صاعد", "Uptrend", "green"),
        "SIDEWAYS": ("اتجاه عرضي", "Sideways", "amber"),
        "DOWNTREND": ("اتجاه هابط", "Downtrend", "red"),
        "STRONG_DOWNTREND": ("اتجاه هابط قوي", "Strong Downtrend", "red"),
        "DATA_INSUFFICIENT": (EM_DASH, "Unavailable", "gray"),
    }
    arabic, english, tone = labels.get(value, (EM_DASH, "Unavailable", "gray"))
    strength = result.indicators.trend_strength
    basis = f"Core typed evidence · strength {strength:.0f}/100" if strength is not None else (
        "Core typed evidence")
    return arabic, english, tone, basis


def momentum_reading(result: AnalysisResult):
    """Render the Core-owned typed momentum. No indicator logic exists in the UI."""
    value = getattr(result.indicators.momentum, "value", result.indicators.momentum)
    labels = {
        "STRONG_POSITIVE": ("زخم إيجابي قوي", "Strong Positive", "green"),
        "POSITIVE": ("زخم إيجابي", "Positive", "green"),
        "NEUTRAL": ("زخم محايد", "Neutral", "amber"),
        "NEGATIVE": ("زخم سلبي", "Negative", "red"),
        "STRONG_NEGATIVE": ("زخم سلبي قوي", "Strong Negative", "red"),
        "DATA_INSUFFICIENT": (EM_DASH, "Unavailable", "gray"),
    }
    arabic, english, tone = labels.get(value, (EM_DASH, "Unavailable", "gray"))
    strength = result.indicators.momentum_strength
    basis = f"Core typed evidence · strength {strength:.0f}/100" if strength is not None else (
        "Core typed evidence")
    return arabic, english, tone, basis


# --------------------------------------------------------------------------- #
# Key levels
# --------------------------------------------------------------------------- #

KEY_LEVEL_SLOTS = (
    ("support_1", "دعم 1", "Support 1"),
    ("support_2", "دعم 2", "Support 2"),
    ("resistance_1", "مقاومة 1", "Resistance 1"),
    ("resistance_2", "مقاومة 2", "Resistance 2"),
    ("breakout", "نقطة الاختراق", "Breakout"),
    ("invalidation", "نقطة الإبطال", "Invalidation"),
)


def selected_levels(result: AnalysisResult) -> dict:
    """Pick the level for each display slot. Selection and ordering only — no calculation.

    Supports are ordered nearest-first (highest price), resistances nearest-first (lowest
    price). Breakout and invalidation prefer a dedicated ``KeyLevel``; when the engine
    supplied neither, the primary scenario's typed ``trigger`` / ``stop`` fields are used
    and the basis records that. Every consumer — the page and the exported card — reads
    this one selection, so the two can never disagree.
    """
    supports = sorted((lv for lv in result.key_levels if lv.kind == "SUPPORT"),
                      key=lambda lv: -float(lv.price))
    resistances = sorted((lv for lv in result.key_levels if lv.kind == "RESISTANCE"),
                         key=lambda lv: float(lv.price))
    by_kind = {lv.kind: lv for lv in result.key_levels}
    candidates = {
        "support_1": supports[0] if len(supports) > 0 else None,
        "support_2": supports[1] if len(supports) > 1 else None,
        "resistance_1": resistances[0] if len(resistances) > 0 else None,
        "resistance_2": resistances[1] if len(resistances) > 1 else None,
        "breakout": by_kind.get("BREAKOUT"),
        "invalidation": by_kind.get("STOP"),
    }

    chosen = {}
    for key, level in candidates.items():
        if level is not None:
            chosen[key] = {"price": level.price, "basis": level.basis,
                           "timeframe": level.timeframe, "touches": level.touches,
                           "last_touch_date": level.last_touch_date,
                           "strength": level.strength,
                           "distance": level.distance_percent}
        else:
            chosen[key] = None
    return chosen


def key_level_rows(result: AnalysisResult):
    """Display rows for Support 1/2, Resistance 1/2, Breakout and Invalidation.

    Every value is rendered from the typed Core contract; missing values remain em dashes.
    """
    chosen = selected_levels(result)
    rows = []
    for key, arabic, english in KEY_LEVEL_SLOTS:
        level = chosen.get(key)
        rows.append({
            "key": key,
            "label_ar": arabic,
            "label_en": english,
            "value": dash(level["price"]) if level else EM_DASH,
            "basis": (level["basis"] if level and level["basis"] else EM_DASH),
            "timeframe": (level["timeframe"] if level and level["timeframe"] else EM_DASH),
            "strength": fmt_confidence_fraction(level["strength"]) if level else EM_DASH,
            "touches": (str(level["touches"]) if level and level["touches"] is not None
                        else EM_DASH),
            "last_touch_date": (
                level["last_touch_date"] if level and level["last_touch_date"] else EM_DASH
            ),
            "distance": fmt_signed_percent(level["distance"]) if level else EM_DASH,
            "tone": change_tone(level["distance"]) if level else "gray",
            "present": level is not None,
        })
    return rows


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #

def scenario_view(scenario: ScenarioResult):
    """Typed ScenarioResult → a display dict. Every figure is a typed field, as supplied."""
    arabic, english, tone = SCENARIO_STATE_LABELS.get(
        scenario.state, ("غير معروف", "Unknown", "gray"))
    entry = EM_DASH
    if scenario.entry_low is not None and scenario.entry_high is not None:
        entry = f"{fmt_price(scenario.entry_low)} – {fmt_price(scenario.entry_high)}"
    elif scenario.entry_low is not None:
        entry = fmt_price(scenario.entry_low)
    return {
        "id": scenario.scenario_id,
        "title": scenario.title,
        "state_ar": arabic,
        "state_en": english,
        "tone": tone,
        "trigger": dash(scenario.trigger),
        "entry": entry,
        "target": dash(scenario.target),
        "stop": dash(scenario.stop),
        "remaining_room": dash(scenario.remaining_room_percent, fmt_percent),
        "risk_reward": fmt_ratio(scenario.risk_reward, 2),
        "confidence": fmt_confidence_fraction(scenario.confidence),
        "confirmations": tuple(scenario.confirmation_requirements),
        "invalidations": tuple(scenario.invalidation_conditions),
    }


# --------------------------------------------------------------------------- #
# Confidence
# --------------------------------------------------------------------------- #

def confidence_rows(confidence: ConfidenceBreakdown):
    """Canonical confidence rows. Supplied scores are shown as-is; the rest are em dashes."""
    supplied = {component.name.strip().lower(): component
                for component in confidence.components}
    rows = []
    for key, arabic, english in CONFIDENCE_COMPONENTS:
        component = supplied.pop(key, None)
        rows.append({
            "label_ar": arabic,
            "label_en": english,
            "score": fmt_score(component.score, 0) if component else EM_DASH,
            "weight": fmt_percent(component.weight * 100, 0) if component else EM_DASH,
            "value": component.score if component else None,
            "supplied": component is not None,
        })
    # Anything the engine supplied under a name outside the canonical list is still shown.
    for name, component in supplied.items():
        rows.append({
            "label_ar": name, "label_en": name,
            "score": fmt_score(component.score, 0),
            "weight": fmt_percent(component.weight * 100, 0),
            "value": component.score, "supplied": True,
        })
    return rows


# --------------------------------------------------------------------------- #
# Narrative source / provenance / warnings
# --------------------------------------------------------------------------- #

def narrative_source(narrative: NarrativeResult | None):
    """(arabic, english, tone) — the narrative's true origin, never a flattering guess."""
    if narrative is None or not str(narrative.summary or "").strip():
        return NARRATIVE_SOURCE_UNAVAILABLE
    model = str(narrative.model or "").strip()
    if not model:
        return NARRATIVE_SOURCE_UNAVAILABLE
    if model == FALLBACK_MODEL or "fallback" in model.lower():
        return NARRATIVE_SOURCE_FALLBACK
    return NARRATIVE_SOURCE_AI


FROZEN_SEED_LABEL = "Frozen historical bootstrap seed"


def provenance_rows(result: AnalysisResult):
    """Provenance exactly as supplied. Yahoo is never presented as a live or current source."""
    quality = result.data_quality
    rows = [
        ("نطاق البيانات", "Data Domain", quality.data_domain or EM_DASH),
        ("المزود", "Provider", quality.provider or EM_DASH),
        ("مزود البيانات الحية", "Live Provider",
         quality.live_provider if quality.live_available else
         f"{quality.live_provider or EM_DASH} (غير متاح)"),
        ("حداثة البيانات", "Data Freshness", quality.freshness_status or EM_DASH),
        ("آخر جلسة مكتملة", "Latest Completed Session",
         quality.latest_completed_session or EM_DASH),
        ("الجلسة المتوقعة", "Expected Completed Session",
         quality.expected_completed_session or EM_DASH),
        ("إصدار البحث", "Research Version", quality.data_domain or EM_DASH),
        ("إصدار الأدلة", "Evidence Version", result.evidence_version or EM_DASH),
        ("سلسلة السعر", "Price Series", result.price.price_series or EM_DASH),
        ("سلسلة الحجم", "Volume Series", result.indicators.volume_series or EM_DASH),
        ("استخدام شبكة ياهو", "Yahoo Network Used",
         "نعم / Yes" if quality.yahoo_network_used else "لا / No"),
    ]
    if quality.yahoo_seed_present:
        # A frozen seed is a historical bootstrap only — never a provider, never a comparison.
        rows.append(("بذرة تاريخية مجمّدة", FROZEN_SEED_LABEL, "موجودة / Present"))
    return rows


def data_quality_warnings(result: AnalysisResult):
    """(severity, arabic, english) warnings assembled from supplied provenance flags."""
    quality = result.data_quality
    warnings = []
    if quality.status in (DataStatus.DATA_UNAVAILABLE, DataStatus.HISTORY_INSUFFICIENT):
        arabic, english, _ = DATA_STATUS_LABELS[quality.status]
        warnings.append(("error", arabic, english))
    if not quality.volume_safe_for_lookback or not result.indicators.volume_safe:
        warnings.append(("warning", "تحليل الحجم غير متاح — الحجم غير موثوق ضمن نافذة المراجعة",
                         "Volume Analysis Unavailable — volume is not lookback-safe"))
    if not quality.history_sufficient:
        warnings.append(("warning", "عمق التاريخ غير كافٍ للتحليل الكامل",
                         "History depth insufficient for a full analysis"))
    if quality.status == DataStatus.CACHE_MODE:
        warnings.append(("warning", "البيانات من الذاكرة المؤقتة وليست محدثة الآن",
                         "Serving cached data — not a current read"))
    if not quality.live_available and result.market_phase in (
            MarketPhase.CONTINUOUS, MarketPhase.CLOSING_AUCTION):
        warnings.append(("warning", "التسعيرة الحية غير متاحة أثناء الجلسة",
                         "Live quote unavailable during the session"))
    if is_auction(result.market_phase):
        warnings.append(("info", "بيانات مزاد الإغلاق معروضة بشكل منفصل ولا تُدمج مع نطاق الجلسة المستمرة",
                         "Closing-auction data is shown separately and is not merged into the "
                         "continuous-session range"))
    if quality.yahoo_network_used:
        warnings.append(("error", "تم استخدام شبكة ياهو — مخالف لسياسة البيانات",
                         "Yahoo network was used — this violates the data policy"))
    for note in quality.notes:
        warnings.append(("info", str(note), str(note)))
    return warnings


# --------------------------------------------------------------------------- #
# Fixture-backed analysis (no providers, no network, one symbol)
# --------------------------------------------------------------------------- #

class UiAnalysisBundle:
    """Mirrors ``core.ai_stock_analysis_service.AnalysisResponse`` so the real service
    can be dropped in unchanged at integration time."""

    __slots__ = ("result", "narrative", "history_record", "source")

    def __init__(self, result, narrative, history_record=None, source="fixture"):
        self.result = result
        self.narrative = narrative
        self.history_record = history_record
        self.source = source


def normalize_symbol(symbol) -> str:
    """Accept exactly one non-empty symbol string. A collection is a contract violation."""
    if isinstance(symbol, (list, tuple, set, frozenset, dict)):
        raise ValueError("the AI Stock Analysis page analyzes exactly one symbol; "
                         "got a collection")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    return symbol.strip().upper()


def load_fixture(path=None) -> dict:
    """Read the shared evidence fixture (read-only; owned by the Core worktree)."""
    fixture_path = Path(path) if path else FIXTURE_PATH
    if not fixture_path.is_absolute():
        for base in (Path.cwd(), Path(__file__).resolve().parents[1]):
            candidate = base / fixture_path
            if candidate.exists():
                fixture_path = candidate
                break
    return json.loads(Path(fixture_path).read_text(encoding="utf-8"))


def _drop_meta(data: dict) -> dict:
    return {key: value for key, value in data.items() if not key.startswith("_")}


def fixture_analysis(symbol, *, path=None, phase: MarketPhase | None = None,
                     language="ar") -> UiAnalysisBundle:
    """Rebuild typed contract objects from the shared fixture for ONE symbol.

    This is the page's stand-in until the Core service is wired in. It reads a local JSON
    file and constructs dataclasses — it opens no network connection, contacts no provider,
    and touches exactly the one requested symbol.
    """
    ticker = normalize_symbol(symbol)
    raw = load_fixture(path)
    analysis = raw["analysis_result"]
    request_data = _drop_meta(raw["request"])
    request_data["market_phase"] = MarketPhase(request_data["market_phase"])
    request_data["symbol"] = ticker
    request = AnalysisRequest(**request_data)

    price = PriceSummary(**{**_drop_meta(analysis["price"]), "symbol": ticker})
    indicators = IndicatorSummary(**{**_drop_meta(analysis["indicators"]), "symbol": ticker})
    key_levels = tuple(KeyLevel(**_drop_meta(level)) for level in analysis["key_levels"])
    scenarios = tuple(
        ScenarioResult(**{**_drop_meta(scenario),
                          "state": ScenarioState(scenario["state"]),
                          "confirmation_requirements": tuple(scenario["confirmation_requirements"]),
                          "invalidation_conditions": tuple(scenario["invalidation_conditions"])})
        for scenario in analysis["scenarios"])
    confidence = ConfidenceBreakdown(
        overall=float(analysis["confidence"]["overall"]),
        method_version=analysis["confidence"]["method_version"],
        components=tuple(ConfidenceComponent(**component)
                         for component in analysis["confidence"]["components"]))
    quality_data = _drop_meta(analysis["data_quality"])
    quality_data["status"] = DataStatus(quality_data["status"])
    quality_data["notes"] = tuple(quality_data.get("notes", ()))
    data_quality = DataQualitySummary(**quality_data)

    market_phase = phase or MarketPhase(analysis["market_phase"])
    result = AnalysisResult(
        request=replace(request, market_phase=market_phase, language=language),
        price=price, indicators=indicators, confidence=confidence,
        data_quality=data_quality,
        recommendation=Recommendation(analysis["recommendation"]),
        market_phase=market_phase,
        evidence_version=analysis["evidence_version"],
        generated_at=analysis["generated_at"],
        key_levels=key_levels, scenarios=scenarios,
        recommendation_reasons=tuple(analysis["recommendation_reasons"]),
        evidence_hash=analysis.get("evidence_hash"))

    narrative_data = _drop_meta(raw["narrative_result"])
    narrative_data["symbol"] = ticker
    narrative = NarrativeResult(**narrative_data)
    return UiAnalysisBundle(result=result, narrative=narrative, source="fixture")


# --------------------------------------------------------------------------- #
# Card composition (Layer 4 input) — display strings only
# --------------------------------------------------------------------------- #

def build_card_chart(result: AnalysisResult) -> CardChartData:
    """Collect the already-computed numbers the compact card chart plots.

    During the closing auction the live print is withheld from the session-range chart so
    an auction price is never drawn inside a continuous-session range.
    """
    primary = result.scenarios[0] if result.scenarios else None
    levels = selected_levels(result)

    def price_of(key):
        level = levels.get(key)
        return level["price"] if level else None

    return CardChartData(
        low=result.price.low, high=result.price.high,
        open=result.price.open, close=result.price.close,
        previous_close=result.price.previous_close,
        last=result.price.last if include_live_in_session_range(result.market_phase) else None,
        support=price_of("support_1"),
        resistance=price_of("resistance_1"),
        trigger=price_of("breakout"),
        target=primary.target if primary else None,
        stop=price_of("invalidation"),
    )


def build_card_payload(result: AnalysisResult, narrative: NarrativeResult | None, *,
                       company_name: str | None = None, as_of_label: str | None = None):
    """Compose a contract ``CardPayload`` of pre-formatted strings from evidence+narrative."""
    from core.ai_stock_analysis_contract import CardPayload

    price = result.price
    recommendation_ar, recommendation_en, _ = RECOMMENDATION_LABELS.get(
        result.recommendation, ("البيانات غير كافية", "Data Insufficient", "red"))
    phase_ar, phase_en, _ = market_phase_labels(result.market_phase)
    mode_ar, mode_en, _ = data_mode(result)
    trend_ar, _, _, _ = trend_reading(result)
    momentum_ar, _, _, _ = momentum_reading(result)

    headline_price = price.last if price.last is not None else price.close
    price_rows = [
        ("السعر", f"{dash(headline_price)} {price.currency}".strip()),
        ("التغير", f"{fmt_signed(price.change_amount)} ({fmt_signed_percent(price.change_percent)})"),
        ("الافتتاح", dash(price.open)),
        ("الأعلى", dash(price.high)),
        ("الأدنى", dash(price.low)),
        ("الإغلاق", dash(price.close)),
        ("الاتجاه", trend_ar),
        ("الزخم", momentum_ar),
    ]

    levels = {row["key"]: row for row in key_level_rows(result)}
    level_rows = [(levels[key]["label_ar"], levels[key]["value"])
                  for key in ("support_1", "support_2", "resistance_1", "resistance_2",
                              "breakout", "invalidation")
                  if levels[key]["present"]][:5]

    # Ordered by importance: the card renderer truncates from the end when space is
    # tight, so state / trigger / target / stop always survive.
    scenario_rows = []
    if result.scenarios:
        view = scenario_view(result.scenarios[0])
        scenario_rows = [
            ("الحالة", view["state_ar"]),
            ("التفعيل", view["trigger"]),
            ("الهدف", view["target"]),
            ("الوقف", view["stop"]),
            ("ثقة السيناريو", view["confidence"]),
            ("العائد/المخاطرة", view["risk_reward"]),
        ]

    # Provenance line for the card footer: provider, mode and the data timestamp the
    # numbers belong to. Yahoo never appears here as a provider or comparison source.
    quality = result.data_quality
    timestamp = price.quote_timestamp or quality.latest_completed_session or EM_DASH
    quality_bits = [str(quality.provider or EM_DASH), mode_en, str(timestamp)]
    if quality.freshness_status:
        quality_bits.append(str(quality.freshness_status))
    if quality.yahoo_seed_present:
        quality_bits.append(FROZEN_SEED_LABEL)

    title_parts = [company_name] if company_name else []
    title_parts.append(f"{phase_ar} · {phase_en}")

    return CardPayload(
        symbol=result.request.symbol,
        title=" — ".join(part for part in title_parts if part),
        as_of_label=as_of_label or str(result.generated_at),
        recommendation_label=recommendation_ar,
        price_rows=tuple(price_rows),
        level_rows=tuple(level_rows),
        scenario_rows=tuple(scenario_rows),
        confidence_label=f"{fmt_score(result.confidence.overall, 0)} / 100",
        data_quality_label=" · ".join(quality_bits),
        narrative_headline=str(narrative.headline) if narrative else "",
        narrative_summary=str(narrative.summary) if narrative else "",
        disclaimer=str(narrative.disclaimer) if narrative else "",
        theme="dark", language=result.request.language, export_format="PNG",
        evidence_version=result.evidence_version,
        narrative_model=(narrative.model if narrative else None),
    )


def generate_card_bytes(result: AnalysisResult, narrative: NarrativeResult | None, *,
                        size: str = DEFAULT_CARD_SIZE, company_name: str | None = None,
                        as_of_label: str | None = None) -> bytes:
    """Compose and render the exportable Arabic PNG card in one step."""
    payload = build_card_payload(result, narrative, company_name=company_name,
                                 as_of_label=as_of_label)
    return render_card_png(payload, build_card_chart(result), size=size)


def card_cache_key(result: AnalysisResult, size: str) -> str:
    """Identity of a rendered card — the same evidence + size never re-renders on a rerun."""
    return f"{result.request.symbol}|{result.evidence_version}|{result.evidence_hash}|{size}"


CARD_SIZE_LABELS = {
    "POST": "بطاقة مربعة الطول 1080×1350 · Post 1080×1350",
    "STORY": "بطاقة طولية 1080×1920 · Story 1080×1920",
}
assert set(CARD_SIZE_LABELS) == set(CARD_SIZES)
