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
    Recommendation,
)

FALLBACK_MODEL = "deterministic-fallback@1.0.0"

# Structural constants a narrative may reference without them being "market numbers":
# indicator periods, the 0-100 percentage/confidence scale.
_STRUCTURAL_NUMBERS = {0, 9, 12, 14, 20, 26, 50, 100, 200}

# A narrative token matches an evidence number only when it equals one of the evidence
# value's fixed-precision renderings (0..4 dp) — i.e. a formatted version of a KNOWN
# number — or lies within a tiny absolute epsilon. There is deliberately NO broad
# relative tolerance, so an unrelated figure can never "match" a large volume value.
_ROUND_DPS = (0, 1, 2, 3, 4)
_ABS_TOL = 0.05
_EPS = 1e-6

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
    for a in expanded:
        if abs(token - a) <= _EPS:      # exact match to a formatted rendering of a known value
            return True
    for a in allowed:
        if abs(token - a) <= _ABS_TOL:  # tiny absolute epsilon only — no relative widening
            return True
    return False


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

    if result.recommendation == Recommendation.DATA_INSUFFICIENT:
        headline = f"{symbol}: بيانات غير كافية للتحليل"
        summary = ("لا يتوفر تاريخ بحثي كافٍ ضمن نطاق CURRENT_RESEARCH_V2 لإنتاج تحليل موثوق؛ "
                   "لم يتم توليد أي أرقام.")
        rationale = "التحليل يتطلب تاريخاً يومياً كافياً وحجماً موثوقاً ضمن نافذة المراجعة."
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
                 f"{_fmt(ind.atr_14)}. الأسباب الآلية: {reasons_txt}.")

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

    allowed = allowed_numbers(result)
    fields = {}
    for name in _NARRATIVE_FIELDS:
        text = produced.get(name)
        if not isinstance(text, str) or not text.strip():
            return build_fallback_narrative(result, language=language)
        ok, offending = validate_no_original_numbers(text, allowed)
        if not ok:
            # AI introduced a number with no evidence source → reject the whole narrative.
            return build_fallback_narrative(result, language=language)
        fields[name] = text.strip()

    return NarrativeResult(
        request_id=result.request.request_id, symbol=result.request.symbol,
        language=language, headline=fields["headline"], summary=fields["summary"],
        rationale=fields["rationale"], risks=fields["risks"], disclaimer=DISCLAIMER_AR,
        model=str(model or produced.get("model") or "ai-narrative"),
        derived_from_evidence_version=result.evidence_version,
        contains_no_original_numbers=True)
