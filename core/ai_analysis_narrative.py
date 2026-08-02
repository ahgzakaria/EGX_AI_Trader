"""Layer 2 — AI-WRITTEN NARRATIVE for AI Stock Analysis On Demand.

The narrative describes an ``AnalysisResult`` in Arabic prose. It is bound by one hard
invariant from the shared contract: **it never originates a number.** Every figure that
appears in narrative text must already exist as an evidence field (or be rendered from
one). This module enforces that mechanically:

  * ``generate_narrative`` accepts an optional ``generator`` callable (the "AI"). Its
    output is validated field-by-field; any numeric token that is not traceable to the
    evidence set causes the whole AI narrative to be rejected.
  * On rejection — or when no generator is supplied — a fully deterministic Arabic
    fallback narrative is produced, composed only from evidence fields.

So the returned ``NarrativeResult`` always satisfies ``contains_no_original_numbers``.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Callable

from core.ai_stock_analysis_contract import (
    AnalysisResult,
    DataStatus,
    NarrativeResult,
    PullbackState,
    Recommendation,
)

FALLBACK_MODEL = "deterministic-fallback@1.0.0"

# Structural constants a narrative may reference without them being "market numbers":
# indicator periods, the 0-100 percentage/confidence scale.
_STRUCTURAL_NUMBERS = {0, 9, 12, 14, 20, 26, 50, 100, 200}

# LEGACY numeric surface (kept for pre-V2 callers and their tests). The PRODUCTION gate is
# :mod:`core.ai_narrative_numbers`, a field-aware EXACT allow-list with no tolerance of any
# kind — ``generate_narrative`` below uses that one. Matching here is exact against a
# value's fixed-precision renderings (0..4 dp); no absolute or relative epsilon remains.
_ROUND_DPS = (0, 1, 2, 3, 4)
_EPS = 1e-9

_NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")

# Arabic labels for recommendations / data statuses (display strings, not numbers).
_REC_AR = {
    Recommendation.READY_WITH_CONDITIONS: "جاهز بشروط",
    Recommendation.NEAR_READY: "قريب من الجاهزية",
    Recommendation.WATCH: "مراقبة",
    Recommendation.WAIT: "انتظار",
    Recommendation.AVOID: "تجنّب",
    Recommendation.DATA_INSUFFICIENT: "بيانات غير كافية",
}
_STATUS_AR = {
    DataStatus.CURRENT: "محدّث",
    DataStatus.CACHE_MODE: "من الذاكرة المؤقتة",
    DataStatus.LIVE_UNAVAILABLE: "لا يوجد سعر حي",
    DataStatus.VOLUME_UNSAFE: "حجم غير موثوق",
    DataStatus.HISTORY_INSUFFICIENT: "تاريخ غير كافٍ",
    DataStatus.DATA_UNAVAILABLE: "بيانات غير متاحة",
}

DISCLAIMER_AR = ("بحث تعليمي فقط — ليس نصيحة استثمارية. "
                 "التنفيذ الحقيقي والوسيط غير مفعّلين.")

_PULLBACK_STATE_AR = {
    PullbackState.NOT_APPLICABLE: "غير قابل للتقييم حاليًا",
    PullbackState.DEVELOPING_PULLBACK: "التصحيح ما زال يتطور",
    PullbackState.WAIT_REVERSAL_CONFIRMATION: (
        "وصل إلى منطقة مهمة وينتظر تأكيد الارتداد"),
    PullbackState.HEALTHY_PULLBACK: "تصحيح صحي داخل اتجاه صاعد",
    PullbackState.DEEP_PULLBACK: "تصحيح عميق ومخاطره أعلى",
    PullbackState.FAILED_PULLBACK: "التصحيح فشل وكسر البنية الصاعدة",
    PullbackState.CONFIRMED_PULLBACK_ENTRY: "ظهر تأكيد ارتداد بحثي فقط",
}

from core.ai_pullback_labels import localize_pullback_reason


def pullback_health_narrative(result: AnalysisResult) -> str:
    """Deterministic qualitative Pullback sentence; never an entry recommendation."""
    pullback = result.pullback_scenario
    if pullback is None:
        return "تحليل جودة التصحيح غير متاح، ولا يؤثر ذلك في التوصية العامة."

    clauses = [f"تحليل جودة التصحيح: {_PULLBACK_STATE_AR.get(pullback.state, pullback.state.value)}."]
    if pullback.prior_trend_status == "VALID_UPTREND":
        clauses.append("السهم يحافظ على بنية الاتجاه الصاعد رغم التصحيح.")
    if pullback.support_confluence:
        support = " و".join(pullback.support_confluence[:3])
        clauses.append(f"التصحيح يراقب دعمًا متداخلًا مع {support}.")
    if pullback.volume_behaviour == "SELLING_VOLUME_CONTRACTING":
        clauses.append("حجم البيع يتراجع أثناء التصحيح.")
    elif pullback.volume_behaviour == "AGGRESSIVE_SELLING_EXPANSION":
        clauses.append("حجم البيع يتوسع بصورة تضعف جودة التصحيح.")
    if pullback.confirmation_status in {"WAITING", "NOT_CONFIRMED", "NOT_AT_SUPPORT"}:
        clauses.append("لم يظهر تأكيد ارتداد حتى الآن.")
    elif pullback.confirmation_status == "CONFIRMED":
        clauses.append("ظهر تأكيد ارتداد بحثي فقط، ولا يمثل إشارة شراء.")
    if pullback.invalidation_reason and pullback.state == PullbackState.NOT_APPLICABLE:
        reason = localize_pullback_reason(pullback.invalidation_reason)
        if reason:
            clauses.append(f"سبب عدم قابلية التقييم: {reason}.")
    clauses.append("هذه قراءة بحثية تشخيصية ولا تغيّر التوصية العامة.")
    return " ".join(clauses)


# --------------------------------------------------------------------------- #
# Numeric traceability
# --------------------------------------------------------------------------- #

def _iter_numbers(obj) -> list[float]:
    """Recursively collect every float/int leaf from a nested structure."""
    out: list[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_iter_numbers(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_iter_numbers(v))
    elif isinstance(obj, str):
        for m in _NUMBER_RE.findall(obj):
            try:
                out.append(float(m.replace(",", "")))
            except ValueError:
                continue
    return out


def allowed_numbers(result: AnalysisResult) -> set[float]:
    """The full set of *base* numbers a narrative is permitted to mention.

    Collects every numeric evidence field from the newly-typed contract (price O/H/L/C,
    change amount/percent, volume/turnover, live quote; SMA/EMA 20/50/200; RSI; MACD line/
    signal/histogram; ATR; average_volume_20; volume_ratio; OBV; expected_range_position;
    scenario trigger/entry/target/stop/room/R:R/confidence; key-level prices; confidence
    components) plus numeric tokens inside auditable evidence strings (level ``basis``,
    scenario condition tuples, ``recommendation_reasons``) and the structural constants.
    Numbers embedded in timestamp/date strings (e.g. the session year) are included, so a
    year is distinguishable from a fabricated market value.
    """
    nums = set(_STRUCTURAL_NUMBERS)
    for section in (result.price, result.indicators, result.confidence,
                    result.data_quality):
        nums.update(_iter_numbers(asdict(section)))
    for level in result.key_levels:
        nums.update(_iter_numbers(asdict(level)))
    for scenario in result.scenarios:
        nums.update(_iter_numbers(asdict(scenario)))
    nums.update(_iter_numbers(list(result.recommendation_reasons)))
    return nums


def _expand(allowed: set[float]) -> set[float]:
    """Every allowed value at each fixed rendering precision (0..4 dp)."""
    expanded: set[float] = set()
    for a in allowed:
        for dp in _ROUND_DPS:
            expanded.add(round(a, dp))
    return expanded


def _is_traceable(token: float, allowed: set[float], expanded: set[float]) -> bool:
    """Exact match against a formatted rendering of a known value. No tolerance."""
    return any(abs(token - a) <= _EPS for a in expanded)


def validate_no_original_numbers(text: str, allowed: set[float]) -> tuple[bool, list[float]]:
    """Return (ok, offending_tokens). ``ok`` means every number is evidence-traceable."""
    expanded = _expand(allowed)
    offending = [t for t in _iter_numbers(text or "")
                 if not _is_traceable(t, allowed, expanded)]
    return (not offending, offending)


# --------------------------------------------------------------------------- #
# Deterministic Arabic fallback narrative (evidence-only)
# --------------------------------------------------------------------------- #

def _fmt(value, dp=2) -> str:
    return "—" if value is None else f"{value:.{dp}f}"


def build_fallback_narrative(result: AnalysisResult, *, language: str = "ar") -> NarrativeResult:
    """Deterministic narrative composed strictly from evidence fields (no AI)."""
    symbol = result.request.symbol
    rec_ar = _REC_AR.get(result.recommendation, result.recommendation.value)
    status_ar = _STATUS_AR.get(result.data_quality.status, result.data_quality.status.value)

    pullback_text = pullback_health_narrative(result)
    if result.recommendation == Recommendation.DATA_INSUFFICIENT:
        headline = f"{symbol}: بيانات غير كافية للتحليل"
        summary = ("لا يتوفر تاريخ بحثي كافٍ ضمن نطاق CURRENT_RESEARCH_V2 لإنتاج تحليل موثوق؛ "
                   "لم يتم توليد أي أرقام.")
        rationale = ("التحليل يتطلب تاريخاً يومياً كافياً وحجماً موثوقاً ضمن نافذة "
                     f"المراجعة. {pullback_text}")
        risks = "الاعتماد على بيانات ناقصة قد يعطي إشارات مضللة؛ لذلك أُوقف التحليل."
        return NarrativeResult(
            request_id=result.request.request_id, symbol=symbol, language=language,
            headline=headline, summary=summary, rationale=rationale, risks=risks,
            disclaimer=DISCLAIMER_AR, model=FALLBACK_MODEL,
            derived_from_evidence_version=result.evidence_version,
            contains_no_original_numbers=True)

    price = result.price
    ind = result.indicators
    conf = result.confidence
    primary = result.scenarios[0] if result.scenarios else None

    headline = (f"{symbol}: {rec_ar} — الإغلاق {_fmt(price.close)} "
                f"جنيه، الثقة {_fmt(conf.overall, 0)}/100")

    change_txt = ("" if price.change_percent is None
                  else f" بتغير {_fmt(price.change_percent)}٪")
    summary = (f"آخر إغلاق مكتمل {_fmt(price.close)} جنيه{change_txt}. "
               f"المتوسط المتحرك البسيط 20 عند {_fmt(ind.sma_20)} والأُسّي 20 عند "
               f"{_fmt(ind.ema_20)}. حالة البيانات: {status_ar}.")

    # Rationale is built from machine reasons, which are themselves evidence strings.
    reasons_txt = "؛ ".join(result.recommendation_reasons[:5])
    rationale = (f"مؤشر القوة النسبية {_fmt(ind.rsi_14)} ومدى التذبذب (ATR) "
                 f"{_fmt(ind.atr_14)}. الأسباب الآلية: {reasons_txt}. {pullback_text}")

    if primary is not None and primary.entry_low is not None:
        risks = (f"يبطل السيناريو بكسر {_fmt(primary.stop)}؛ "
                 f"الهدف عند {_fmt(primary.target)} ونسبة العائد/المخاطرة "
                 f"{_fmt(primary.risk_reward)}.")
    else:
        risks = "غياب تأكيد الإغلاق أو ضعف الحجم قد يبطل الفرصة."

    return NarrativeResult(
        request_id=result.request.request_id, symbol=symbol, language=language,
        headline=headline, summary=summary, rationale=rationale, risks=risks,
        disclaimer=DISCLAIMER_AR, model=FALLBACK_MODEL,
        derived_from_evidence_version=result.evidence_version,
        contains_no_original_numbers=True)


# --------------------------------------------------------------------------- #
# AI narrative interface (safe): validate, else fall back
# --------------------------------------------------------------------------- #

NarrativeGenerator = Callable[[AnalysisResult, str], dict]
_NARRATIVE_FIELDS = ("headline", "summary", "rationale", "risks")


def generate_narrative(
    result: AnalysisResult,
    *,
    generator: NarrativeGenerator | None = None,
    model: str | None = None,
    language: str = "ar",
) -> NarrativeResult:
    """Produce a NarrativeResult, preferring the AI generator but never trusting it blindly.

    ``generator(result, language)`` must return a dict with string fields
    ``headline``/``summary``/``rationale``/``risks``. Every numeric token in every field
    is validated against :func:`allowed_numbers`. If the generator is absent, raises, or
    emits any untraceable number, the deterministic fallback is returned instead — so the
    invariant ``contains_no_original_numbers`` always holds.
    """
    if generator is None:
        return build_fallback_narrative(result, language=language)

    try:
        produced = generator(result, language)
    except Exception:
        return build_fallback_narrative(result, language=language)

    if not isinstance(produced, dict):
        return build_fallback_narrative(result, language=language)

    # The production gate is the field-aware EXACT allow-list: no epsilon, no tolerance.
    from core.ai_narrative_numbers import build_number_allowlist, validate_numbers

    allowlist = build_number_allowlist(result)
    fields = {}
    for name in _NARRATIVE_FIELDS:
        text = produced.get(name)
        if not isinstance(text, str) or not text.strip():
            return build_fallback_narrative(result, language=language)
        ok, offending = validate_numbers(text, allowlist)
        if not ok:
            # AI introduced a number with no evidence source → reject the whole narrative.
            return build_fallback_narrative(result, language=language)
        fields[name] = text.strip()

    return NarrativeResult(
        request_id=result.request.request_id, symbol=result.request.symbol,
        language=language, headline=fields["headline"], summary=fields["summary"],
        rationale=f'{fields["rationale"]} {pullback_health_narrative(result)}',
        risks=fields["risks"], disclaimer=DISCLAIMER_AR,
        model=str(model or produced.get("model") or "ai-narrative"),
        derived_from_evidence_version=result.evidence_version,
        contains_no_original_numbers=True)


# --------------------------------------------------------------------------- #
# Deterministic English summary
# --------------------------------------------------------------------------- #

#: The narrative engine produces Arabic only. English mode previously received
#: ``summary_en`` populated from the Arabic headline, so an English card rendered
#: Arabic prose. This builder composes a genuine English summary from the SAME
#: typed evidence fields — no translation, no AI, no new numbers.
_REC_EN = {
    Recommendation.WAIT: "Wait",
    Recommendation.WATCH: "Watch",
    Recommendation.NEAR_READY: "Near activation",
    Recommendation.READY_WITH_CONDITIONS: "Ready with conditions",
    Recommendation.AVOID: "Avoid for now",
    Recommendation.DATA_INSUFFICIENT: "Data insufficient",
}

_TREND_EN = {
    "STRONG_UPTREND": "a strong uptrend", "UPTREND": "an uptrend",
    "SIDEWAYS": "a sideways trend", "DOWNTREND": "a downtrend",
    "STRONG_DOWNTREND": "a strong downtrend",
    "DATA_INSUFFICIENT": "an undetermined trend",
}

_MOMENTUM_EN = {
    "STRONG_POSITIVE": "strong positive momentum", "POSITIVE": "positive momentum",
    "NEUTRAL": "neutral momentum", "NEGATIVE": "negative momentum",
    "STRONG_NEGATIVE": "strong negative momentum",
    "DATA_INSUFFICIENT": "undetermined momentum",
}


def build_english_summary(result: AnalysisResult) -> str:
    """A deterministic English summary, or ``""`` when evidence is insufficient.

    Composed only from typed fields the Core already calculated. Returning an
    empty string is meaningful: the caller must then show an explicit
    "unavailable" marker rather than silently falling back to Arabic.
    """

    if result.recommendation == Recommendation.DATA_INSUFFICIENT:
        return ""

    price, indicators = result.price, result.indicators
    if price.close is None:
        return ""

    recommendation = _REC_EN.get(result.recommendation, "")
    if not recommendation:
        return ""

    trend = _TREND_EN.get(getattr(indicators.trend, "value", ""), "")
    momentum = _MOMENTUM_EN.get(getattr(indicators.momentum, "value", ""), "")

    parts = [
        f"{result.request.symbol} closed the last completed session at "
        f"{_fmt(price.close)} {price.currency}"
        + (f" ({_fmt(price.change_percent)}%)"
           if price.change_percent is not None else "") + "."
    ]
    if trend and momentum:
        parts.append(f"The daily structure shows {trend} with {momentum}.")
    if indicators.ema_20 is not None and indicators.ema_50 is not None:
        parts.append(f"EMA20 is at {_fmt(indicators.ema_20)} and EMA50 at "
                     f"{_fmt(indicators.ema_50)}.")
    parts.append(f"Overall assessment: {recommendation} at "
                 f"{_fmt(result.confidence.overall, 0)}/100 confidence.")
    return " ".join(parts)
