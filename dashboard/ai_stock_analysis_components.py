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

import html
import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
    PullbackScenarioResult,
    PullbackState,
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
CAIRO = ZoneInfo("Africa/Cairo")

ARABIC_MONTHS = (
    "", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
)

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

PULLBACK_STATE_LABELS = {
    PullbackState.NOT_APPLICABLE: ("غير منطبق", "Not Applicable", "gray"),
    PullbackState.DEVELOPING_PULLBACK: ("تصحيح قيد التكوين", "Developing Pullback", "blue"),
    PullbackState.WAIT_REVERSAL_CONFIRMATION: (
        "انتظار تأكيد الارتداد", "Wait Reversal Confirmation", "amber"),
    PullbackState.HEALTHY_PULLBACK: ("تصحيح صحي", "Healthy Pullback", "blue"),
    PullbackState.DEEP_PULLBACK: ("تصحيح عميق", "Deep Pullback", "amber"),
    PullbackState.CONFIRMED_PULLBACK_ENTRY: (
        "تأكيد بحثي فقط — غير صالح للتنفيذ", "RESEARCH_CONFIRMATION_ONLY", "amber"),
    PullbackState.FAILED_PULLBACK: ("فشل التصحيح", "Failed Pullback", "red"),
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
NARRATIVE_SOURCE_LOCAL_AI = ("سرد ذكاء اصطناعي محلي", "Local AI", "blue")
NARRATIVE_SOURCE_FALLBACK = ("سرد استنتاجي حتمي", "Deterministic Fallback", "amber")
NARRATIVE_SOURCE_UNAVAILABLE = ("السرد غير متاح", "AI Unavailable", "red")

# Local-model readiness, shown only when the narrative provider is a local one.
LOCAL_AI_STATUS_LABELS = {
    "LOCAL_AI_READY": ("الذكاء المحلي جاهز", "Local AI Ready", "green"),
    "LOCAL_AI_MODEL_MISSING": ("نموذج الذكاء المحلي غير مثبّت", "Local AI Model Missing",
                               "amber"),
    "LOCAL_AI_UNAVAILABLE": ("الذكاء المحلي غير متاح", "Local AI Unavailable", "red"),
}

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


def pullback_scenario_view(scenario: PullbackScenarioResult | None):
    """Typed PullbackScenarioResult -> display strings; no level is recalculated here."""
    if scenario is None:
        return None
    arabic, english, tone = PULLBACK_STATE_LABELS.get(
        scenario.state, ("غير معروف", "Unknown", "gray"))
    zone = EM_DASH
    if scenario.support_zone_low is not None and scenario.support_zone_high is not None:
        zone = f"{fmt_price(scenario.support_zone_low)} – {fmt_price(scenario.support_zone_high)}"
    correction = EM_DASH
    if scenario.pullback_percent is not None or scenario.pullback_atr is not None:
        percent = dash(scenario.pullback_percent, fmt_percent)
        atr = (f"{scenario.pullback_atr:.2f} ATR"
               if scenario.pullback_atr is not None else EM_DASH)
        correction = f"{percent} / {atr}"
    confluence = " + ".join(scenario.support_confluence) or EM_DASH
    confirmation = scenario.confirmation_status or EM_DASH
    if scenario.confirmation_reasons:
        confirmation = f"{confirmation}: " + " + ".join(scenario.confirmation_reasons)
    return {
        "state_ar": arabic,
        "state_en": english,
        "tone": tone,
        "research_score": dash(scenario.research_score, fmt_score),
        "trend": scenario.prior_trend_status or EM_DASH,
        "structure": scenario.structure_status or EM_DASH,
        "swing_high": dash(scenario.swing_high),
        "swing_high_date": scenario.swing_high_date or EM_DASH,
        "swing_confirmation_date": scenario.swing_high_confirmation_date or EM_DASH,
        "impulse_low": dash(scenario.impulse_low),
        "impulse_low_date": scenario.impulse_low_date or EM_DASH,
        "correction": correction,
        "pullback_percent": dash(scenario.pullback_percent, fmt_percent),
        "pullback_atr": (f"{scenario.pullback_atr:.2f} ATR"
                         if scenario.pullback_atr is not None else EM_DASH),
        "retracement": dash(scenario.impulse_retracement_percent, fmt_percent),
        "correction_bars": (str(scenario.correction_bars)
                            if scenario.correction_bars is not None else EM_DASH),
        "support_zone": zone,
        "support_reached": "نعم · Yes" if scenario.support_reached else "لا · No",
        "confluence": confluence,
        "volume": scenario.volume_behaviour or EM_DASH,
        "confirmation": confirmation,
        "ema20": dash(scenario.ema20),
        "ema50": dash(scenario.ema50),
        "ema_relation": scenario.ema_alignment_status or EM_DASH,
        "trigger": dash(scenario.entry_trigger),
        "stop": dash(scenario.stop_loss),
        "target_1": dash(scenario.target_1),
        "target_2": dash(scenario.target_2),
        "major_resistance": dash(scenario.major_resistance),
        "minor_resistance": dash(scenario.minor_pivot_resistance),
        "meaningful_resistance": dash(scenario.meaningful_structural_target),
        "broader_resistance": dash(scenario.broader_structural_target),
        "risk_reward": fmt_ratio(scenario.reward_risk, 2),
        "risk_reward_meaningful": fmt_ratio(
            scenario.reward_risk_meaningful_target, 2),
        "risk_reward_broader": fmt_ratio(scenario.reward_risk_broader_target, 2),
        "invalidation": scenario.invalidation_reason or EM_DASH,
        "explanation_ar": scenario.explanation_ar,
        "explanation_en": scenario.explanation_en,
        "evidence": tuple(scenario.evidence),
        "missing": tuple(scenario.missing_measurements),
        "cutoff": scenario.historical_data_cutoff or EM_DASH,
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

def narrative_source_token(narrative: NarrativeResult | None) -> str:
    """The narrative's true origin as a token: AI_NARRATIVE / DETERMINISTIC_FALLBACK / AI_UNAVAILABLE.

    Provenance, when present, is authoritative — it is set to ``AI_NARRATIVE`` only after an
    external provider answered AND passed every validation. Without provenance (pre-v1.2)
    the model name is used, and an unknown model is never assumed to be AI.
    """
    if narrative is None or not str(narrative.summary or "").strip():
        return "AI_UNAVAILABLE"
    provenance = getattr(narrative, "provenance", None)
    if provenance is not None:
        source = str(getattr(provenance, "source", "") or "").strip().upper()
        if source in ("AI_NARRATIVE", "LOCAL_AI_NARRATIVE", "DETERMINISTIC_FALLBACK",
                      "AI_UNAVAILABLE"):
            return source
    model = str(narrative.model or "").strip()
    if not model:
        return "AI_UNAVAILABLE"
    if model == FALLBACK_MODEL or "fallback" in model.lower():
        return "DETERMINISTIC_FALLBACK"
    return "AI_NARRATIVE"


_NARRATIVE_SOURCE_BADGES = {
    "AI_NARRATIVE": NARRATIVE_SOURCE_AI,
    "LOCAL_AI_NARRATIVE": NARRATIVE_SOURCE_LOCAL_AI,
    "DETERMINISTIC_FALLBACK": NARRATIVE_SOURCE_FALLBACK,
    "AI_UNAVAILABLE": NARRATIVE_SOURCE_UNAVAILABLE,
}


def local_ai_status(config=None):
    """(state, arabic, english, tone) for the local model, or ``None`` when not in use.

    Returns ``None`` unless the narrative layer is enabled AND configured to a local
    provider, so the default (disabled) configuration performs no probe at all. The probe
    itself is read-only, loopback-only, short-timeout, and never raises — a missing or
    unreachable Ollama can never block or slow the deterministic analysis.
    """
    from core.ai_narrative_provider import LOCAL_PROVIDERS, NarrativeConfig, NarrativeMode

    settings = config or NarrativeConfig.from_env()
    if settings.mode is not NarrativeMode.EXTERNAL_AI:
        return None
    if settings.provider not in LOCAL_PROVIDERS:
        return None
    try:
        from core.ai_narrative_ollama import check_health
        health = check_health(model=settings.model or None)
    except Exception:
        return ("LOCAL_AI_UNAVAILABLE", *LOCAL_AI_STATUS_LABELS["LOCAL_AI_UNAVAILABLE"])
    return (health.state, *LOCAL_AI_STATUS_LABELS[health.state])


def narrative_source(narrative: NarrativeResult | None):
    """(arabic, english, tone) — the narrative's true origin, never a flattering guess."""
    return _NARRATIVE_SOURCE_BADGES[narrative_source_token(narrative)]


def narrative_technical_rows(narrative: NarrativeResult | None):
    """Technical provenance rows for the UI expander. Never exposes a key or a prompt.

    When a model was called and its answer was rejected, the provider and model are still
    reported here for diagnosis — but labelled as an ATTEMPT, next to the source that
    actually produced the visible text. The rejected prose itself is never stored, never
    returned and never shown; only the sanitized rejection reason is.
    """
    provenance = getattr(narrative, "provenance", None) if narrative else None
    if provenance is None:
        return (("المصدر", "Narrative Source", narrative_source_token(narrative)),
                ("النموذج", "Model", (narrative.model if narrative else EM_DASH) or EM_DASH))
    latency = getattr(provenance, "latency_ms", None)
    provider = str(provenance.provider or "").strip().lower()
    endpoint_rows = ()
    if provider == "ollama":
        # A loopback URL is not a secret; prompts and keys are still never shown.
        import os

        endpoint = (os.environ.get("AI_NARRATIVE_OLLAMA_URL", "").strip()
                    or "http://127.0.0.1:11434")
        endpoint_rows = (("نقطة الاتصال المحلية", "Local Endpoint", endpoint),)
    # A provider recorded against a fallback was tried and not used.
    attempted = (narrative_source_token(narrative) not in NARRATIVE_SOURCES_WITH_A_MODEL
                 and provider not in ("", "none"))
    provider_label = (("المزود المُحاوَل", "Attempted Provider") if attempted
                      else ("المزود", "Provider"))
    model_label = (("النموذج المُحاوَل", "Attempted Model") if attempted
                   else ("النموذج", "Model"))
    return (
        ("المصدر النهائي" if attempted else "المصدر", "Narrative Source",
         str(provenance.source or EM_DASH)),
        (*provider_label, str(provenance.provider or EM_DASH)),
        (*model_label, str(provenance.model or EM_DASH)),
        *endpoint_rows,
        ("إصدار التعليمات", "Prompt Version", str(provenance.prompt_version or EM_DASH)),
        ("بصمة الأدلة", "Evidence Hash", str(provenance.evidence_hash or EM_DASH)),
        ("وقت التوليد", "Generated At", str(provenance.generated_at or EM_DASH)),
        ("نتيجة التحقق", "Validation Result", str(provenance.validation_status or EM_DASH)),
        ("زمن الاستجابة", "Latency", EM_DASH if latency is None else f"{int(latency)} ms"),
        ("من الذاكرة المؤقتة", "Served From Cache", "نعم / yes" if provenance.cached else "لا / no"),
        ("سبب التراجع", "Fallback Reason", str(provenance.fallback_reason or EM_DASH)),
    )


# --------------------------------------------------------------------------- #
# Narrative presentation — one readable card per section
# --------------------------------------------------------------------------- #
#
# Presentation only. Nothing below generates, re-orders, re-rounds or re-labels a fact:
# the model still writes the prose, the fact binder still owns every number, its label,
# its unit and its print order. These helpers only split what the composer already joined
# so the page can lay prose and facts out as separate, readable blocks.

CURRENCY_AR = "جنيه"

# Section order, bilingual titles, subtle accent, and desktop grid width.
# ``full`` = one card per row; ``half`` = two cards side by side on desktop.
NARRATIVE_SECTION_META = (
    ("executive_summary_ar", "الخلاصة التنفيذية", "Executive Summary", "blue", "full"),
    ("technical_read_ar", "القراءة الفنية", "Technical Read", "violet", "full"),
    ("positive_scenario_ar", "السيناريو الإيجابي", "Positive Scenario", "green", "half"),
    ("negative_scenario_ar", "السيناريو السلبي", "Negative Scenario", "red", "half"),
    ("confirmation_conditions_ar", "شروط التأكيد", "Confirmation Conditions", "cyan", "half"),
    ("invalidation_conditions_ar", "شروط الإبطال", "Invalidation Conditions", "orange", "half"),
    ("risk_notes_ar", "ملاحظات المخاطر", "Risk Notes", "amber", "half"),
    ("data_limitations_ar", "حدود البيانات", "Data Limitations", "slate", "half"),
)

NARRATIVE_SECTION_ORDER = tuple(key for key, _, _, _, _ in NARRATIVE_SECTION_META)

# Subtle accents — an accent line per card, never a saturated card background.
NARRATIVE_ACCENTS = {
    "blue": "#60a5fa",
    "violet": "#a78bfa",
    "green": "#34d399",
    "red": "#f87171",
    "cyan": "#22d3ee",
    "orange": "#fb923c",
    "amber": "#fbbf24",
    "slate": "#94a3b8",
}

# The deterministic fallback has no structured sections. Its three prose fields are shown
# in exactly the same cards, so an unavailable model never brings the old text panel back.
NARRATIVE_FALLBACK_FIELDS = (
    ("executive_summary_ar", "summary"),
    ("technical_read_ar", "rationale"),
    ("risk_notes_ar", "risks"),
)

# Card header for the one source block shown above the cards (never repeated per card).
NARRATIVE_SOURCE_TITLES = {
    "LOCAL_AI_NARRATIVE": ("ذكاء اصطناعي محلي", "Local AI", "blue"),
    "AI_NARRATIVE": ("سرد بالذكاء الاصطناعي", "AI Narrative", "blue"),
    "DETERMINISTIC_FALLBACK": ("شرح حتمي", "Deterministic Fallback", "amber"),
    "AI_UNAVAILABLE": ("الذكاء الاصطناعي غير متاح", "AI Unavailable", "red"),
}

# Only these sources actually wrote the narrative the reader is looking at, so only these
# may name a provider and a model in the source strip. A rejected answer is not a source:
# when validation fails the deterministic writer produced every word on the page, and
# printing "Ollama · qwen3:4b" beside "Deterministic Fallback" would credit a model for
# text it did not write. The attempted provider is still recorded — in the technical
# expander, labelled as an attempt.
NARRATIVE_SOURCES_WITH_A_MODEL = frozenset({"LOCAL_AI_NARRATIVE", "AI_NARRATIVE"})

NARRATIVE_STATE_VALIDATED = ("جاهز وتم التحقق من السرد", "Validated", "green")
NARRATIVE_STATE_UNVERIFIED = ("لم يكتمل التحقق من السرد", "Not Validated", "amber")
NARRATIVE_STATE_EVIDENCE_ONLY = ("مبني على الأدلة وحدها", "Evidence Only", "amber")
NARRATIVE_STATE_MISSING = ("لا يوجد سرد", "No Narrative", "red")

PROVIDER_DISPLAY_NAMES = {"ollama": "Ollama", "openai": "OpenAI", "none": ""}

# A run of Latin text (EMA, RSI, MACD, "Ollama · qwen3:4b", "Local AI") that must not flip
# Arabic direction. Adjacent Latin words joined only by spaces or a middot stay one run, so
# a two-word English label is isolated as a phrase rather than word by word.
_LTR_RUN = re.compile(
    r"[A-Za-z][A-Za-z0-9_.:/@\-]*(?:[ ·]+[A-Za-z0-9][A-Za-z0-9_.:/@\-]*)*")

# The longest categorical state still readable as a chip. Anything longer, and anything
# carrying a digit, becomes an aligned label/value row instead.
CHIP_MAX_LENGTH = 34


def isolate_ltr(text) -> str:
    """HTML-escape ``text``, wrapping each Latin run in a direction-isolated span.

    Arabic prose that mentions ``RSI`` or ``MACD`` keeps its own direction: the abbreviation
    renders left-to-right inside its own isolate, and the sentence around it stays RTL.
    """
    raw = str(text)
    pieces, cursor = [], 0
    for match in _LTR_RUN.finditer(raw):
        pieces.append(html.escape(raw[cursor:match.start()]))
        pieces.append(f'<span class="ltr" dir="ltr">{html.escape(match.group())}</span>')
        cursor = match.end()
    pieces.append(html.escape(raw[cursor:]))
    return "".join(pieces)


def split_fact_line(line):
    """One composed fact line → ``(label, value)``.

    The fact binder prints every line as ``label: value``; this reverses that single join
    so the label and the value can sit in their own aligned columns.
    """
    text = str(line).strip()
    label, separator, value = text.partition(": ")
    return (label.strip(), value.strip()) if separator else ("", text)


def fact_is_categorical(value) -> bool:
    """True when a fact value is a short categorical state, so a chip is honest.

    Prices, indicators, ratios, scores and percentages all carry digits and are therefore
    never chips — they belong in aligned rows where they can be compared.
    """
    text = str(value).strip()
    return bool(text) and len(text) <= CHIP_MAX_LENGTH and not any(
        character.isdigit() for character in text)


def _section_card(key, prose, fact_lines, meta_by_key):
    title_ar, title_en, accent, width = meta_by_key.get(
        key, (str(key), "", "slate", "full"))
    rows, chips = [], []
    for line in fact_lines:
        if not str(line).strip():
            continue
        label, value = split_fact_line(line)
        if label and fact_is_categorical(value):
            chips.append(value)
        else:
            rows.append((label, value))
    return {
        "key": key,
        "title_ar": title_ar,
        "title_en": title_en,
        "accent": accent,
        "accent_color": NARRATIVE_ACCENTS.get(accent, NARRATIVE_ACCENTS["slate"]),
        "width": width,
        "prose": str(prose).strip(),
        "rows": tuple(rows),
        "chips": tuple(chips),
    }


def narrative_section_views(narrative: NarrativeResult | None):
    """Ordered per-section card views: header, AI prose, deterministic facts — separated.

    Returns one dict per card. ``prose`` is the model's qualitative sentence exactly as it
    was validated, and ``rows``/``chips`` are the application's own fact lines split back
    into label and value. No value is reformatted here and no fact is invented.
    """
    if narrative is None:
        return ()
    meta_by_key = {key: (title_ar, title_en, accent, width)
                   for key, title_ar, title_en, accent, width in NARRATIVE_SECTION_META}
    sections = tuple(getattr(narrative, "sections", ()) or ())
    if sections:
        supplied = {str(name): str(text) for name, text in sections}
        ordered = [name for name in NARRATIVE_SECTION_ORDER if name in supplied]
        ordered += [name for name in supplied if name not in meta_by_key]
        cards = []
        for name in ordered:
            prose, *fact_lines = supplied[name].split("\n")
            cards.append(_section_card(name, prose, fact_lines, meta_by_key))
        return tuple(cards)

    # Deterministic fallback: the same cards, fed from the evidence-only writer's fields.
    cards = []
    for key, attribute in NARRATIVE_FALLBACK_FIELDS:
        text = str(getattr(narrative, attribute, "") or "").strip()
        if text:
            cards.append(_section_card(key, text, (), meta_by_key))
    return tuple(cards)


def narrative_summary_cells(result: AnalysisResult):
    """The four summary-strip cells — separate cells, never one crowded RTL sentence.

    Every value is read from a typed evidence field, never parsed out of the headline.
    """
    recommendation_ar, recommendation_en, tone = RECOMMENDATION_LABELS.get(
        result.recommendation, ("البيانات غير كافية", "Data Insufficient", "red"))
    close = result.price.close
    return (
        ("السهم", "Symbol", str(result.request.symbol), "gray"),
        ("التوصية", "Recommendation", f"{recommendation_ar} · {recommendation_en}", tone),
        ("آخر إغلاق", "Last Close",
         EM_DASH if close is None else f"{fmt_price(close)} {CURRENCY_AR}", "gray"),
        ("الثقة", "Confidence", f"{fmt_score(result.confidence.overall, 0)} / 100", "blue"),
    )


def narrative_source_block(narrative: NarrativeResult | None):
    """The single source header shown above the cards — who actually wrote this text.

    The provider and model shown here describe the ACCEPTED narrative, not whatever was
    attempted. A model whose answer was rejected did not write the visible text, so it is
    not named here; ``narrative_technical_rows`` records the attempt instead.

    Reads the provenance the narrative already carries. It performs no health probe and
    contacts nothing, so a plain UI rerender never reaches a model or a provider.
    """
    token = narrative_source_token(narrative)
    title_ar, title_en, tone = NARRATIVE_SOURCE_TITLES[token]
    provenance = getattr(narrative, "provenance", None) if narrative else None
    detail = ""
    if token in NARRATIVE_SOURCES_WITH_A_MODEL:
        provider = str(getattr(provenance, "provider", "") or "").strip().lower()
        model = str(getattr(provenance, "model", "") or "").strip()
        if not model and narrative is not None:
            model = str(narrative.model or "").strip()
        provider_name = PROVIDER_DISPLAY_NAMES.get(
            provider, provider.title() if provider else "")
        detail = " · ".join(part for part in (provider_name, model) if part)

    validation = str(getattr(provenance, "validation_status", "") or "").strip().upper()
    if token == "AI_UNAVAILABLE":
        state = NARRATIVE_STATE_MISSING
    elif token == "DETERMINISTIC_FALLBACK":
        state = NARRATIVE_STATE_EVIDENCE_ONLY
    elif validation == "VALIDATED":
        state = NARRATIVE_STATE_VALIDATED
    else:
        state = NARRATIVE_STATE_UNVERIFIED
    return {
        "token": token,
        "title_ar": title_ar,
        "title_en": title_en,
        "tone": tone,
        "detail": detail,
        "state_ar": state[0],
        "state_en": state[1],
        "state_tone": state[2],
    }


# The narrative block's own stylesheet. Kept here (not inline in the page) so the layout
# contract — reading width, Arabic font stack, sizes, RTL, the desktop two-column grid and
# the tablet single-column collapse — is inspectable without a Streamlit runtime.
NARRATIVE_FONT_STACK = '"Segoe UI", Tahoma, Arial, sans-serif'

NARRATIVE_CSS = f"""
<style>
.egx-narrative {{
    direction: rtl; text-align: right;
    font-family: {NARRATIVE_FONT_STACK};
    max-width: 1080px; margin: 0 auto;
}}
/* Streamlit's theme sets its own font on every heading with a higher-specificity selector
   than anything scoped here can reach, so the Arabic-capable stack has to be restated for
   each descendant and marked important — otherwise card headings silently fall back. */
.egx-narrative * {{ font-family: {NARRATIVE_FONT_STACK} !important; }}
.egx-narrative .ltr {{ direction: ltr; unicode-bidi: isolate; }}

/* Keep the Streamlit widgets that belong to this block (the title/regenerate row and the
   technical-details expander) inside the same centred reading column as the cards. */
[data-testid="stHorizontalBlock"]:has(.egx-narr-title),
[data-testid="stExpander"]:has(.egx-narr-tech) {{
    max-width: 1080px; margin-inline: auto;
}}
.egx-narr-tech {{ display: none; }}

/* The selector carries the element name so it outranks Streamlit's own heading sizing. */
.egx-narrative h2.egx-narr-title {{ margin: .2rem 0 .1rem; font-size: 26px; font-weight: 800;
    color: var(--text); letter-spacing: -.01em; padding: 0; }}
.egx-narrative h2.egx-narr-title .en {{ display: block; font-size: 13px; font-weight: 600;
    color: var(--muted); direction: ltr; text-align: right; letter-spacing: .04em;
    text-transform: uppercase; }}

/* one source block — provider and model are never repeated inside a card */
.egx-narr-source {{ display: flex; flex-wrap: wrap; align-items: center; gap: .55rem .9rem;
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: .7rem .95rem; margin: .55rem 0 .7rem; }}
.egx-narr-source .who {{ font-size: 18px; font-weight: 700; color: var(--text); }}
.egx-narr-source .who .en {{ display: block; font-size: 13px; font-weight: 600;
    color: var(--muted); direction: ltr; text-align: right; }}
.egx-narr-source .meta {{ font-size: 13px; color: var(--muted); direction: ltr; }}
.egx-narr-source .state {{ margin-inline-start: auto; display: inline-flex; align-items: center;
    gap: .4rem; font-size: 15px; font-weight: 700; }}
.egx-narr-source .state .dot {{ width: 9px; height: 9px; border-radius: 50%; }}
.egx-narr-source .state .en {{ font-size: 13px; font-weight: 600; color: var(--muted);
    direction: ltr; }}

/* summary strip — four separate cells, never one run-on sentence */
.egx-narr-strip {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: .6rem; margin: 0 0 .8rem; }}
.egx-narr-strip .cell {{ background: var(--surface-2); border: 1px solid var(--border);
    border-radius: 11px; padding: .55rem .8rem; }}
.egx-narr-strip .k {{ font-size: 14px; font-weight: 600; color: var(--muted); }}
.egx-narr-strip .k .en {{ display: block; font-size: 12px; direction: ltr; text-align: right;
    text-transform: uppercase; letter-spacing: .04em; opacity: .8; }}
.egx-narr-strip .v {{ margin-top: .18rem; font-size: 19px; font-weight: 700; color: var(--text);
    font-variant-numeric: tabular-nums; }}

/* the cards */
.egx-narr-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: .8rem; align-items: start; }}
.egx-narr-card {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: 13px; padding: .85rem 1.05rem 1rem; height: 100%; }}
.egx-narr-card.full {{ grid-column: 1 / -1; }}
.egx-narr-card h3 {{ margin: 0; font-size: 19px; font-weight: 700; color: var(--text);
    padding-inline-start: .1rem; }}
.egx-narr-card h3 .en {{ display: block; font-size: 13px; font-weight: 600; color: var(--muted);
    direction: ltr; text-align: right; letter-spacing: .03em; }}
.egx-narr-card .accent {{ height: 3px; width: 58px; border-radius: 999px; margin: .5rem 0 .65rem; }}
.egx-narr-card p.prose {{ margin: 0; font-size: 17px; font-weight: 400; line-height: 1.8;
    color: var(--text); max-width: 78ch; }}

/* deterministic facts — a subtle secondary box, aligned label/value rows */
.egx-narr-facts {{ margin-top: .8rem; background: var(--surface-2); border: 1px solid var(--border);
    border-radius: 10px; padding: .5rem .7rem; }}
.egx-narr-facts .row {{ display: grid; grid-template-columns: minmax(0, 1fr) auto;
    gap: .5rem 1rem; align-items: baseline; padding: .3rem .1rem;
    border-bottom: 1px solid var(--border); }}
.egx-narr-facts .row:last-child {{ border-bottom: 0; }}
.egx-narr-facts .lbl {{ font-size: 15px; font-weight: 500; color: var(--muted); }}
.egx-narr-facts .val {{ font-size: 16px; font-weight: 700; color: var(--text);
    font-variant-numeric: tabular-nums; unicode-bidi: isolate; white-space: nowrap; }}
.egx-narr-chips {{ display: flex; flex-wrap: wrap; gap: .35rem .4rem; margin-top: .55rem; }}
.egx-narr-chips .chip {{ font-size: 14px; font-weight: 650; color: var(--text);
    background: var(--surface-2); border: 1px solid var(--border);
    border-radius: 999px; padding: .16rem .6rem; }}

.egx-narrative p.egx-narr-note {{ font-size: 13px; line-height: 1.7; color: var(--muted);
    margin: .75rem 0 .2rem; max-width: 78ch; }}

/* tablet and below — every card becomes a single column, nothing scrolls sideways.
   The threshold is the viewport width at which a two-column split would leave each Arabic
   paragraph too narrow to read comfortably, not a device class. */
@media (max-width: 1150px) {{
    .egx-narr-grid {{ grid-template-columns: 1fr; }}
    .egx-narr-card.full {{ grid-column: auto; }}
    .egx-narr-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    /* the title and the regenerate button stack, so the button keeps a full-width hit
       area instead of being squeezed into a narrow column */
    [data-testid="stHorizontalBlock"]:has(.egx-narr-title) {{ flex-wrap: wrap; }}
    [data-testid="stHorizontalBlock"]:has(.egx-narr-title) > div {{
        flex: 1 1 100%; min-width: 100%; }}
}}
@media (max-width: 520px) {{
    .egx-narr-strip {{ grid-template-columns: 1fr; }}
    .egx-narr-source .state {{ margin-inline-start: 0; }}
    .egx-narr-facts .row {{ grid-template-columns: 1fr; }}
    .egx-narr-facts .val {{ white-space: normal; }}
}}
</style>
"""


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

def _parse_display_datetime(value: str | None) -> datetime | None:
    """Parse a typed ISO date/time for presentation without changing its value."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CAIRO)
    return parsed.astimezone(CAIRO)


def format_arabic_date(value: str | None) -> str:
    """Render an ISO date as a concise Arabic calendar date."""
    parsed = _parse_display_datetime(value)
    if parsed is None:
        return str(value or EM_DASH)
    return f"{parsed.day} {ARABIC_MONTHS[parsed.month]} {parsed.year}"


def format_cairo_timestamp(value: str | None) -> str:
    """Render an ISO timestamp for people, explicitly in Cairo local time."""
    parsed = _parse_display_datetime(value)
    if parsed is None:
        return str(value or EM_DASH)
    hour = parsed.hour % 12 or 12
    period = "ص" if parsed.hour < 12 else "م"
    return (
        f"{format_arabic_date(parsed.isoformat())} — "
        f"{hour}:{parsed.minute:02d} {period} بتوقيت القاهرة"
    )


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
        support_2=price_of("support_2"),
        resistance=price_of("resistance_1"),
        resistance_2=price_of("resistance_2"),
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
    trend_ar, _, _, _ = trend_reading(result)
    momentum_ar, _, _, _ = momentum_reading(result)

    live_price_available = (
        result.data_quality.live_available
        and price.last is not None
        and result.market_phase in (MarketPhase.CONTINUOUS, MarketPhase.CLOSING_AUCTION)
    )
    headline_price = price.last if live_price_available else price.close
    headline_label = "السعر" if live_price_available else "آخر إغلاق"
    price_rows = [
        (headline_label, f"{dash(headline_price)} {price.currency}".strip()),
        ("التغير", f"{fmt_signed(price.change_amount)} ({fmt_signed_percent(price.change_percent)})"),
        ("الافتتاح", dash(price.open)),
        ("الأعلى", dash(price.high)),
        ("الأدنى", dash(price.low)),
        ("الإغلاق", dash(price.close)),
        ("الاتجاه", trend_ar),
        ("الزخم", momentum_ar),
    ]

    levels = {row["key"]: row for row in key_level_rows(result)}
    typed_levels = selected_levels(result)
    level_rows = []
    for key in ("support_1", "support_2"):
        if levels[key]["present"]:
            level_rows.append((levels[key]["label_ar"], levels[key]["value"]))

    resistance = typed_levels.get("resistance_1")
    breakout = typed_levels.get("breakout")
    if resistance and breakout and resistance["price"] == breakout["price"]:
        level_rows.append(("المقاومة / نقطة الاختراق", levels["resistance_1"]["value"]))
    else:
        if levels["resistance_1"]["present"]:
            level_rows.append((levels["resistance_1"]["label_ar"],
                               levels["resistance_1"]["value"]))
        if levels["breakout"]["present"]:
            level_rows.append((levels["breakout"]["label_ar"], levels["breakout"]["value"]))

    for key in ("resistance_2", "invalidation"):
        if levels[key]["present"]:
            level_rows.append((levels[key]["label_ar"], levels[key]["value"]))
    level_rows = level_rows[:5]

    # Ordered by importance: the card renderer truncates from the end when space is
    # tight, so state / trigger / target / stop always survive.
    scenario_rows = []
    if result.scenarios:
        view = scenario_view(result.scenarios[0])
        scenario_rows = [
            ("حالة السيناريو", view["state_ar"]),
            ("التفعيل", view["trigger"]),
            ("الهدف", view["target"]),
            ("الوقف", view["stop"]),
            ("العائد/المخاطرة", view["risk_reward"]),
            ("المسافة من آخر إغلاق إلى الهدف", view["remaining_room"]),
            ("ثقة السيناريو", view["confidence"]),
        ]

    # Public provenance deliberately contains no evidence hash/version or technical IDs.
    # It exposes only the provider and the latest completed research session.
    quality = result.data_quality
    completed_session = quality.latest_completed_session or price.session_date
    provider_label = str(quality.provider or EM_DASH)
    if provider_label != EM_DASH:
        provider_label = provider_label.upper()
    quality_bits = [
        f"Provider  {provider_label}",
        f"Last completed session  {format_arabic_date(completed_session)}",
    ]

    return CardPayload(
        symbol=result.request.symbol,
        title=f"{phase_ar} · {phase_en}",
        as_of_label=format_cairo_timestamp(as_of_label or result.generated_at),
        recommendation_label=recommendation_ar,
        company_name=str(company_name or "").strip(),
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
        narrative_source=narrative_source_token(narrative),
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
