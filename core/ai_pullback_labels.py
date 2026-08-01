"""The ONE Arabic label map for pullback invalidation reasons.

This lived in two places — ``core.ai_analysis_narrative`` and the dashboard
components — and the narrative copy was missing ``INVALID_PRIOR_UPTREND``, so the
raw enum reached the reader through the narrative paragraph even after the
dashboard copy was fixed. Both now read this module.

``localize_pullback_reason`` never returns a bare enum: an unmapped code falls
back to a generic Arabic phrase, so a future reason cannot leak either.
"""

from __future__ import annotations

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

#: Shown when a reason has no mapping yet. Never the raw code.
UNMAPPED_REASON_AR = "شروط القياس البحثية غير مستوفاة"


def localize_pullback_reason(reason) -> str:
    """Arabic reason for a code. An unmapped or diagnostic code never leaks."""

    code = str(reason or "").strip()
    if not code:
        return ""
    if code.startswith("PULLBACK_DIAGNOSTIC_ERROR:"):
        return "تعذر حساب تحليل جودة التصحيح"
    return PULLBACK_REASON_AR.get(code, UNMAPPED_REASON_AR)


__all__ = ["PULLBACK_REASON_AR", "UNMAPPED_REASON_AR", "localize_pullback_reason"]
