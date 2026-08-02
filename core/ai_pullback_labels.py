"""The ONE localization source for every internal analysis enum.

Two enum leaks reached readers before this module existed: the pullback
invalidation reason (fixed in 157756e) and ``EMA_ALIGNMENT_FAILED``, which the
infographic audit caught in ``ema_alignment_ar``. Both had the same shape — a
label map that did not cover every value, and a ``.get(code, code)`` fallback
that printed the raw code when it missed.

So this module does two things:

* it carries the complete Arabic AND English map for every status enum that can
  reach a presentation surface, and
* :func:`localize` never returns the code it was given. An unmapped value falls
  back to a readable generic phrase, so a NEW enum added upstream degrades to
  "unavailable" instead of leaking.

:func:`looks_like_enum` lets tests assert that no surface ever prints an
``ALL_CAPS_UNDERSCORE`` token.

Raw codes remain available to callers that want them for a COLLAPSED diagnostics
panel; they are never part of a user-facing sentence.
"""

from __future__ import annotations

import re

#: Generic fallbacks, per language. Never a raw code.
UNAVAILABLE_AR = "غير متاح"
UNAVAILABLE_EN = "Unavailable"

PULLBACK_REASON_AR = {
    "INSUFFICIENT_COMPLETED_DAILY_HISTORY": "التاريخ اليومي المكتمل غير كافٍ",
    "EODHD_COMPLETED_DAILY_REQUIRED": "يلزم تاريخ يومي مكتمل من EODHD حتى الجلسة السابقة",
    "ATR_UNAVAILABLE": "قياس ATR غير متاح",
    "NO_CONFIRMED_SWING_HIGH": "لا توجد قمة محورية مؤكدة داخل النافذة المتاحة",
    "NO_VALID_IMPULSE_LOW": "لا يوجد قاع صالح لبداية الموجة الصاعدة",
    "INVALID_PRIOR_UPTREND": "الاتجاه الصاعد السابق لم يستوفِ شروط القياس البحثية",
    "CLOSE_BELOW_STRUCTURAL_SUPPORT": "الإغلاق كسر الدعم الهيكلي",
    "CORRECTION_EXCEEDS_RESEARCH_DEPTH_LIMIT": "عمق التصحيح تجاوز الحد البحثي",
    "ESTABLISHED_DESCENDING_CHANNEL": "تكوّنت قناة هابطة بدل تصحيح داخل اتجاه صاعد",
    "EMA50_DECLINING_MATERIALLY": "ميل EMA50 أصبح هابطًا بصورة مؤثرة",
    "AGGRESSIVE_SELLING_VOLUME_EXPANSION": "حجم البيع يتوسع بصورة غير داعمة",
    "DEEP_PULLBACK_REQUIRES_STRONGER_CONFIRMATION": "التصحيح العميق يحتاج تأكيد ارتداد أقوى",
    "REWARD_RISK_BELOW_RESEARCH_THRESHOLD": "العائد إلى المخاطرة دون الحد البحثي",
    "SELLING_VOLUME_NOT_SUPPORTIVE": "سلوك حجم البيع غير داعم",
    "VOLUME_BEHAVIOUR_UNAVAILABLE": "سلوك الحجم غير متاح بصورة موثوقة",
}

PULLBACK_REASON_EN = {
    "INSUFFICIENT_COMPLETED_DAILY_HISTORY": "Not enough completed daily history",
    "EODHD_COMPLETED_DAILY_REQUIRED": "Completed EODHD daily history required through D-1",
    "ATR_UNAVAILABLE": "ATR measurement unavailable",
    "NO_CONFIRMED_SWING_HIGH": "No confirmed swing high in the available window",
    "NO_VALID_IMPULSE_LOW": "No valid impulse-start low",
    "INVALID_PRIOR_UPTREND": "Prior uptrend requirements were not met",
    "CLOSE_BELOW_STRUCTURAL_SUPPORT": "Close broke structural support",
    "CORRECTION_EXCEEDS_RESEARCH_DEPTH_LIMIT": "Correction exceeded the research depth limit",
    "ESTABLISHED_DESCENDING_CHANNEL": "A descending channel formed instead of a pullback",
    "EMA50_DECLINING_MATERIALLY": "EMA50 slope turned materially negative",
    "AGGRESSIVE_SELLING_VOLUME_EXPANSION": "Selling volume expanded unsupportively",
    "DEEP_PULLBACK_REQUIRES_STRONGER_CONFIRMATION": "A deep pullback needs stronger confirmation",
    "REWARD_RISK_BELOW_RESEARCH_THRESHOLD": "Reward/risk below the research threshold",
    "SELLING_VOLUME_NOT_SUPPORTIVE": "Selling-volume behaviour is not supportive",
    "VOLUME_BEHAVIOUR_UNAVAILABLE": "Volume behaviour is not reliably available",
}

TREND_STATUS_AR = {
    "VALID_UPTREND": "اتجاه صاعد مؤكد",
    "INVALID_PRIOR_TREND": "الاتجاه السابق غير مؤهل",
    "NOT_VALIDATED": "لم يتم التحقق",
    "UNAVAILABLE": UNAVAILABLE_AR,
}
TREND_STATUS_EN = {
    "VALID_UPTREND": "Valid uptrend",
    "INVALID_PRIOR_TREND": "Prior trend not qualified",
    "NOT_VALIDATED": "Not validated",
    "UNAVAILABLE": UNAVAILABLE_EN,
}

EMA_ALIGNMENT_AR = {
    "EMA20_ABOVE_EMA50": "EMA20 أعلى من EMA50",
    "EMA20_BELOW_EMA50": "EMA20 أدنى من EMA50",
    "EMA20_EQUALS_EMA50": "EMA20 يساوي EMA50",
    # The value that leaked: EMA20 is not above EMA50, or one of them is missing.
    "EMA_ALIGNMENT_FAILED": "ترتيب المتوسطات غير مكتمل",
    "UNAVAILABLE": UNAVAILABLE_AR,
}
EMA_ALIGNMENT_EN = {
    "EMA20_ABOVE_EMA50": "EMA20 above EMA50",
    "EMA20_BELOW_EMA50": "EMA20 below EMA50",
    "EMA20_EQUALS_EMA50": "EMA20 equals EMA50",
    "EMA_ALIGNMENT_FAILED": "EMA alignment not satisfied",
    "UNAVAILABLE": UNAVAILABLE_EN,
}

STRUCTURE_AR = {
    "HIGHER_HIGH_HIGHER_LOW": "قمم وقيعان صاعدة",
    "DESCENDING_STRUCTURE": "هيكل هابط",
    "UNRELIABLE": "الهيكل غير موثوق",
    "UNAVAILABLE": UNAVAILABLE_AR,
}
STRUCTURE_EN = {
    "HIGHER_HIGH_HIGHER_LOW": "Higher high, higher low",
    "DESCENDING_STRUCTURE": "Descending structure",
    "UNRELIABLE": "Structure unreliable",
    "UNAVAILABLE": UNAVAILABLE_EN,
}

VOLUME_BEHAVIOUR_AR = {
    "SELLING_VOLUME_CONTRACTING": "حجم البيع يتراجع",
    "SELLING_VOLUME_EXPANDING": "حجم البيع يتوسع",
    "AGGRESSIVE_SELLING_EXPANSION": "توسع قوي في حجم البيع",
    "AGGRESSIVE_VOLUME_EXPANSION": "توسع قوي في الحجم",
    "AGGRESSIVE_SELLING_VOLUME_EXPANSION": "توسع قوي في حجم البيع",
    "SELLING_VOLUME_NOT_SUPPORTIVE": "حجم البيع غير داعم",
    "MIXED_VOLUME": "سلوك حجم مختلط",
    "VOLUME_UNAVAILABLE": "الحجم غير متاح",
    "VOLUME_BEHAVIOUR_UNAVAILABLE": "سلوك الحجم غير متاح",
    "UNAVAILABLE": UNAVAILABLE_AR,
}
VOLUME_BEHAVIOUR_EN = {
    "SELLING_VOLUME_CONTRACTING": "Selling volume contracting",
    "SELLING_VOLUME_EXPANDING": "Selling volume expanding",
    "AGGRESSIVE_SELLING_EXPANSION": "Aggressive selling expansion",
    "AGGRESSIVE_VOLUME_EXPANSION": "Aggressive volume expansion",
    "AGGRESSIVE_SELLING_VOLUME_EXPANSION": "Aggressive selling volume expansion",
    "SELLING_VOLUME_NOT_SUPPORTIVE": "Selling volume not supportive",
    "MIXED_VOLUME": "Mixed volume",
    "VOLUME_UNAVAILABLE": "Volume unavailable",
    "VOLUME_BEHAVIOUR_UNAVAILABLE": "Volume behaviour unavailable",
    "UNAVAILABLE": UNAVAILABLE_EN,
}

CONFIRMATION_AR = {
    "CONFIRMED": "ظهر تأكيد ارتداد بحثي فقط",
    "REVERSAL_CANDLE_TRIGGER_PENDING": "ظهرت شمعة ارتداد والتفعيل البحثي لم يكتمل",
    "WAITING": "لم يظهر تأكيد ارتداد حتى الآن",
    "NOT_AT_SUPPORT": "لم يصل السعر بعد إلى منطقة الدعم",
    "NOT_CONFIRMED": "لم يظهر تأكيد ارتداد حتى الآن",
    "UNAVAILABLE": UNAVAILABLE_AR,
}
CONFIRMATION_EN = {
    "CONFIRMED": "Research reversal confirmation only",
    "REVERSAL_CANDLE_TRIGGER_PENDING": "Reversal candle formed, research trigger pending",
    "WAITING": "No reversal evidence yet",
    "NOT_AT_SUPPORT": "Price has not reached the support zone",
    "NOT_CONFIRMED": "No reversal evidence yet",
    "UNAVAILABLE": UNAVAILABLE_EN,
}

DEPTH_AR = {
    "SHALLOW": "تصحيح ضحل", "HEALTHY": "عمق صحي", "DEEP": "تصحيح عميق",
    "FAILED_DEPTH": "العمق تجاوز الحد", "UNAVAILABLE": UNAVAILABLE_AR,
}
DEPTH_EN = {
    "SHALLOW": "Shallow", "HEALTHY": "Healthy depth", "DEEP": "Deep",
    "FAILED_DEPTH": "Depth limit exceeded", "UNAVAILABLE": UNAVAILABLE_EN,
}

#: Every (arabic, english) pair, keyed by the field it localizes.
ENUM_MAPS = {
    "invalidation_reason": (PULLBACK_REASON_AR, PULLBACK_REASON_EN),
    "prior_trend_status": (TREND_STATUS_AR, TREND_STATUS_EN),
    "ema_alignment_status": (EMA_ALIGNMENT_AR, EMA_ALIGNMENT_EN),
    "structure_status": (STRUCTURE_AR, STRUCTURE_EN),
    "volume_behaviour": (VOLUME_BEHAVIOUR_AR, VOLUME_BEHAVIOUR_EN),
    "confirmation_status": (CONFIRMATION_AR, CONFIRMATION_EN),
    "depth_classification": (DEPTH_AR, DEPTH_EN),
}

#: An internal code: two or more uppercase segments joined by underscores. Used by
#: tests to prove no surface prints one.
_ENUM_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


def looks_like_enum(text) -> bool:
    """True when ``text`` contains an ALL_CAPS_UNDERSCORE internal code."""

    return bool(_ENUM_TOKEN.search(str(text or "")))


def localize(code, field: str, language: str = "AR") -> str:
    """Readable text for an internal code. NEVER returns the code itself.

    ``field`` selects the map. An unknown code — including one a future engine
    version introduces — degrades to the generic unavailable phrase rather than
    leaking, which is the failure mode that produced two user-visible bugs.
    """

    text = str(code or "").strip()
    fallback = UNAVAILABLE_EN if language == "EN" else UNAVAILABLE_AR
    if not text:
        return fallback
    if text.startswith("PULLBACK_DIAGNOSTIC_ERROR:"):
        return ("Pullback diagnostic could not be computed" if language == "EN"
                else "تعذر حساب تحليل جودة التصحيح")
    arabic, english = ENUM_MAPS.get(field, ({}, {}))
    mapping = english if language == "EN" else arabic
    resolved = mapping.get(text)
    if resolved:
        return resolved
    return fallback                       # unmapped: never echo the code


def localize_pullback_reason(reason, language: str = "AR") -> str:
    """Arabic (or English) reason for a pullback invalidation code."""

    if not str(reason or "").strip():
        return ""
    return localize(reason, "invalidation_reason", language)


def bilingual(code, field: str) -> str:
    """``عربي · English`` for a code, with neither side ever a raw enum."""

    arabic, english = localize(code, field, "AR"), localize(code, field, "EN")
    return f"{arabic} · {english}" if arabic != english else arabic


__all__ = [
    "CONFIRMATION_AR", "CONFIRMATION_EN", "DEPTH_AR", "DEPTH_EN",
    "EMA_ALIGNMENT_AR", "EMA_ALIGNMENT_EN", "ENUM_MAPS", "PULLBACK_REASON_AR",
    "PULLBACK_REASON_EN", "STRUCTURE_AR", "STRUCTURE_EN", "TREND_STATUS_AR",
    "TREND_STATUS_EN", "UNAVAILABLE_AR", "UNAVAILABLE_EN",
    "VOLUME_BEHAVIOUR_AR", "VOLUME_BEHAVIOUR_EN", "bilingual", "localize",
    "localize_pullback_reason", "looks_like_enum",
]


# --------------------------------------------------------------------------- #
# Assessment reasons
# --------------------------------------------------------------------------- #

#: ``recommendation_reasons`` is produced by ONE function over a CLOSED
#: vocabulary (core/ai_analysis_evidence.py). This is an exact-match table over
#: that vocabulary — not keyword replacement — so a phrase either has a verified
#: Arabic equivalent or is dropped. Technical abbreviations stay in Latin script,
#: which is how a trader reads them.
ASSESSMENT_REASON_AR = {
    "price above SMA20 and SMA50": "السعر أعلى SMA20 وSMA50",
    "price below SMA20 and SMA50": "السعر أدنى SMA20 وSMA50",
    "price above EMA20": "السعر أعلى EMA20",
    "price below EMA20": "السعر أدنى EMA20",
    "RSI14 strong_positive": "مؤشر RSI إيجابي قوي",
    "RSI14 positive": "مؤشر RSI إيجابي",
    "RSI14 neutral": "مؤشر RSI في منطقة محايدة",
    "RSI14 negative": "مؤشر RSI سلبي",
    "RSI14 strong_negative": "مؤشر RSI سلبي قوي",
    "RSI14 data_insufficient": "بيانات RSI غير كافية",
    "MACD histogram positive": "هيستوجرام MACD إيجابي",
    "MACD histogram negative": "هيستوجرام MACD سلبي",
    "volume safe for lookback": "حجم التداول موثوق خلال فترة المراجعة",
    "volume ratio at or above average": "حجم التداول عند المتوسط أو أعلى",
    "volume ratio below average": "حجم التداول أدنى من المتوسط",
    "OBV rising": "مؤشر OBV صاعد",
    "OBV falling": "مؤشر OBV هابط",
    "volume unsafe for lookback (OBV suppressed)": "حجم التداول غير موثوق — OBV معطّل",
    "upper half of the recent channel": "السعر في النصف الأعلى من القناة",
    "lower half of the recent channel": "السعر في النصف الأدنى من القناة",
    "awaiting close above resistance": "بانتظار إغلاق أعلى المقاومة",
    "insufficient history": "التاريخ غير كافٍ",
}


def localize_assessment_reasons(reasons, language: str = "AR") -> tuple:
    """Arabic assessment reasons, preserving meaning and order.

    English mode returns the source phrases unchanged. Arabic mode returns only
    reasons with a VERIFIED Arabic equivalent; an unrecognised phrase is dropped
    rather than machine-mangled, so an Arabic card never shows an English
    sentence and never shows an altered meaning.
    """

    items = tuple(str(r).strip() for r in (reasons or ()) if str(r).strip())
    if language == "EN":
        return items
    if language == "BILINGUAL":
        return tuple(f"{ASSESSMENT_REASON_AR[r]} · {r}" if r in ASSESSMENT_REASON_AR
                     else r for r in items)
    return tuple(ASSESSMENT_REASON_AR[r] for r in items if r in ASSESSMENT_REASON_AR)
