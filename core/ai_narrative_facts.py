"""Typed fact registry — the binding between a narrative sentence and a real number.

The exact numeric allow-list stops the model inventing values, but it cannot stop
**semantic misattribution**: a risk/reward of 1.9 is a legitimate evidence number, so
"الهدف 1.90" would pass a value-only check while being flatly wrong. The fix is to stop
the model emitting numbers at all.

Architecture — **AI narrative with deterministic facts**:

  * the external model writes qualitative Arabic prose and cites ``fact_id`` references;
  * this module owns every number the reader sees: the raw typed value, its rounding, its
    Arabic label, its unit, and the order the fact lines are printed in;
  * a fact may only be cited from a section that is allowed to talk about it, so a target
    can never appear in the technical read and a stop can never headline the positive
    scenario.

Facts are built ONLY from typed contract fields. Nothing is parsed out of prose, a level
``basis`` string, or a machine reason. A fact whose value is missing is simply absent from
the registry, so it cannot be referenced at all — and volume-derived facts are withheld
whenever provenance says volume is not lookback-safe.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.ai_narrative_numbers import (
    KIND_COUNT,
    KIND_INDICATOR,
    KIND_PERCENT,
    KIND_PRICE,
    KIND_RATIO,
    KIND_SCORE,
    KIND_UNIT_RATIO,
    KIND_VOLUME,
)
from core.ai_stock_analysis_contract import AnalysisResult

KIND_LABEL = "LABEL"          # a categorical evidence state (no number at all)

# The eight narrative sections, in render order.
SECTIONS = (
    "executive_summary_ar",
    "technical_read_ar",
    "positive_scenario_ar",
    "negative_scenario_ar",
    "confirmation_conditions_ar",
    "invalidation_conditions_ar",
    "risk_notes_ar",
    "data_limitations_ar",
)

_EXEC = "executive_summary_ar"
_TECH = "technical_read_ar"
_POS = "positive_scenario_ar"
_NEG = "negative_scenario_ar"
_CONF = "confirmation_conditions_ar"
_INVAL = "invalidation_conditions_ar"
_RISK = "risk_notes_ar"
_DATA = "data_limitations_ar"


# --------------------------------------------------------------------------- #
# Deterministic formatting — the ONLY place a market number becomes text
# --------------------------------------------------------------------------- #

CURRENCY_AR = "جنيه"


def format_value(value, kind: str, *, signed: bool = False) -> str:
    """Render one typed value. Rounding, separators and units live here alone."""
    if kind == KIND_LABEL:
        return str(value)
    number = float(value)
    if kind == KIND_PRICE:
        return f"{number:{'+' if signed else ''}.2f} {CURRENCY_AR}"
    if kind == KIND_PERCENT:
        return f"{number:{'+' if signed else ''}.2f}٪"
    if kind in (KIND_RATIO, KIND_INDICATOR):
        return f"{number:.2f}"
    if kind == KIND_UNIT_RATIO:
        return f"{number * 100:.0f} / 100"
    if kind == KIND_SCORE:
        return f"{number:.0f} / 100"
    if kind == KIND_VOLUME:
        return f"{number:,.0f}"
    return f"{number:.2f}"


@dataclass(frozen=True)
class Fact:
    """One citable evidence fact. ``formatted`` is what the reader actually sees."""
    fact_id: str
    path: str                       # source field path on the AnalysisResult
    value: object                   # raw typed value (float, or a label string)
    kind: str
    formatted: str
    label_ar: str
    sections: tuple[str, ...]       # sections permitted to cite this fact

    @property
    def is_numeric(self) -> bool:
        return self.kind != KIND_LABEL


class FactRegistry:
    """The facts available for ONE analysis, with their section permissions."""

    def __init__(self, facts):
        self._facts = {fact.fact_id: fact for fact in facts}
        self._order = tuple(fact.fact_id for fact in facts)

    # -- lookup -------------------------------------------------------------- #

    def __contains__(self, fact_id) -> bool:
        return fact_id in self._facts

    def __len__(self) -> int:
        return len(self._facts)

    def get(self, fact_id: str) -> Fact | None:
        return self._facts.get(fact_id)

    @property
    def fact_ids(self) -> tuple[str, ...]:
        """Every available fact id, in deterministic registry order."""
        return self._order

    def for_section(self, section: str) -> tuple[str, ...]:
        """The fact ids a given section is allowed to cite."""
        return tuple(fid for fid in self._order if section in self._facts[fid].sections)

    def permits(self, section: str, fact_id: str) -> bool:
        fact = self._facts.get(fact_id)
        return bool(fact and section in fact.sections)

    # -- rendering ----------------------------------------------------------- #

    def fact_lines(self, fact_ids) -> tuple[str, ...]:
        """Render cited facts as ``label: value`` lines.

        Order is the registry's, never the model's; duplicates collapse; unknown ids are
        dropped (the validator rejects them long before this point).
        """
        wanted = {fid for fid in fact_ids or ()}
        return tuple(f"{self._facts[fid].label_ar}: {self._facts[fid].formatted}"
                     for fid in self._order if fid in wanted)

    def compose(self, qualitative_text: str, fact_ids) -> str:
        """One finished section: model prose, then deterministic fact lines."""
        lines = self.fact_lines(fact_ids)
        prose = str(qualitative_text or "").strip()
        return "\n".join((prose, *lines)) if lines else prose


# --------------------------------------------------------------------------- #
# Registry construction (typed fields only)
# --------------------------------------------------------------------------- #

def _fact(fact_id, path, value, kind, label, sections, *, signed=False) -> Fact | None:
    if value is None or isinstance(value, bool) and kind != KIND_LABEL:
        return None
    if kind != KIND_LABEL:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if value != value:                        # NaN
            return None
    return Fact(fact_id=fact_id, path=path, value=value, kind=kind,
                formatted=format_value(value, kind, signed=signed), label_ar=label,
                sections=tuple(sections))


_TREND_AR = {
    "STRONG_UPTREND": "اتجاه صاعد قوي", "UPTREND": "اتجاه صاعد", "SIDEWAYS": "اتجاه عرضي",
    "DOWNTREND": "اتجاه هابط", "STRONG_DOWNTREND": "اتجاه هابط قوي",
    "DATA_INSUFFICIENT": "بيانات غير كافية",
}
_MOMENTUM_AR = {
    "STRONG_POSITIVE": "زخم إيجابي قوي", "POSITIVE": "زخم إيجابي", "NEUTRAL": "زخم محايد",
    "NEGATIVE": "زخم سلبي", "STRONG_NEGATIVE": "زخم سلبي قوي",
    "DATA_INSUFFICIENT": "بيانات غير كافية",
}
_RECOMMENDATION_AR = {
    "READY_WITH_CONDITIONS": "جاهز بشروط", "NEAR_READY": "قريب من الجاهزية",
    "WATCH": "مراقبة", "WAIT": "انتظار", "AVOID": "تجنّب",
    "DATA_INSUFFICIENT": "بيانات غير كافية",
}
_STATUS_AR = {
    "CURRENT": "محدّث", "CACHE_MODE": "من الذاكرة المؤقتة",
    "LIVE_UNAVAILABLE": "لا يوجد سعر حي", "VOLUME_UNSAFE": "حجم غير موثوق",
    "HISTORY_INSUFFICIENT": "تاريخ غير كافٍ", "DATA_UNAVAILABLE": "بيانات غير متاحة",
}


def _token(value) -> str:
    return str(getattr(value, "value", value) or "")


def build_fact_registry(result: AnalysisResult) -> FactRegistry:
    """Build the citable facts for ``result``. Typed fields only; missing values omitted."""
    price = result.price
    ind = result.indicators
    volume_safe = bool(ind.volume_safe and result.data_quality.volume_safe_for_lookback)

    supports = sorted((lv for lv in result.key_levels if lv.kind == "SUPPORT"),
                      key=lambda lv: -float(lv.price))
    resistances = sorted((lv for lv in result.key_levels if lv.kind == "RESISTANCE"),
                         key=lambda lv: float(lv.price))
    by_kind = {lv.kind: lv for lv in result.key_levels}
    primary = result.scenarios[0] if result.scenarios else None

    candidates = [
        # -- classification / decision state (no numbers) --------------------- #
        _fact("classification.trend", "indicators.trend",
              _TREND_AR.get(_token(ind.trend), _token(ind.trend)) or None, KIND_LABEL,
              "الاتجاه", (_EXEC, _TECH)),
        _fact("classification.momentum", "indicators.momentum",
              _MOMENTUM_AR.get(_token(ind.momentum), _token(ind.momentum)) or None,
              KIND_LABEL, "الزخم", (_EXEC, _TECH, _CONF)),
        _fact("recommendation", "recommendation",
              _RECOMMENDATION_AR.get(_token(result.recommendation),
                                     _token(result.recommendation)) or None,
              KIND_LABEL, "التوصية العامة", (_EXEC,)),

        # -- price ------------------------------------------------------------ #
        _fact("price.close", "price.close", price.close, KIND_PRICE,
              "آخر إغلاق مكتمل", (_EXEC, _TECH)),
        _fact("price.change_percent", "price.change_percent", price.change_percent,
              KIND_PERCENT, "نسبة التغير", (_EXEC,), signed=True),
        _fact("price.change_amount", "price.change_amount", price.change_amount,
              KIND_PRICE, "قيمة التغير", (_EXEC,), signed=True),
        _fact("price.volume", "price.volume", price.volume if volume_safe else None,
              KIND_VOLUME, "حجم التداول", (_TECH, _DATA)),
        _fact("price.turnover", "price.turnover", price.turnover if volume_safe else None,
              KIND_VOLUME, "قيمة التداول بالجنيه", (_TECH, _DATA)),

        # -- indicators -------------------------------------------------------- #
        _fact("indicator.sma_20", "indicators.sma_20", ind.sma_20, KIND_PRICE,
              "المتوسط المتحرك البسيط 20", (_TECH,)),
        _fact("indicator.sma_50", "indicators.sma_50", ind.sma_50, KIND_PRICE,
              "المتوسط المتحرك البسيط 50", (_TECH,)),
        _fact("indicator.sma_200", "indicators.sma_200", ind.sma_200, KIND_PRICE,
              "المتوسط المتحرك البسيط 200", (_TECH,)),
        _fact("indicator.ema_20", "indicators.ema_20", ind.ema_20, KIND_PRICE,
              "المتوسط المتحرك الأُسّي 20", (_TECH,)),
        _fact("indicator.ema_50", "indicators.ema_50", ind.ema_50, KIND_PRICE,
              "المتوسط المتحرك الأُسّي 50", (_TECH,)),
        _fact("indicator.ema_200", "indicators.ema_200", ind.ema_200, KIND_PRICE,
              "المتوسط المتحرك الأُسّي 200", (_TECH,)),
        _fact("indicator.rsi_14", "indicators.rsi_14", ind.rsi_14, KIND_INDICATOR,
              "مؤشر القوة النسبية 14", (_TECH,)),
        _fact("indicator.macd", "indicators.macd", ind.macd, KIND_INDICATOR,
              "خط الماكد", (_TECH,)),
        _fact("indicator.macd_signal", "indicators.macd_signal", ind.macd_signal,
              KIND_INDICATOR, "إشارة الماكد", (_TECH,)),
        _fact("indicator.macd_histogram", "indicators.macd_histogram", ind.macd_histogram,
              KIND_INDICATOR, "هيستوجرام الماكد", (_TECH,)),
        _fact("indicator.atr_14", "indicators.atr_14", ind.atr_14, KIND_PRICE,
              "متوسط المدى الحقيقي 14", (_TECH, _RISK)),
        _fact("indicator.expected_range_position", "indicators.expected_range_position",
              ind.expected_range_position, KIND_SCORE, "موضع المدى المتوقع", (_TECH,)),
        # Volume-derived facts exist only when volume is lookback-safe.
        _fact("indicator.volume_ratio", "indicators.volume_ratio",
              ind.volume_ratio if volume_safe else None, KIND_RATIO,
              "نسبة الحجم", (_TECH, _CONF)),
        _fact("indicator.average_volume_20", "indicators.average_volume_20",
              ind.average_volume_20 if volume_safe else None, KIND_VOLUME,
              "متوسط الحجم 20", (_TECH,)),
        _fact("indicator.sessions_used", "indicators.computed_from_sessions",
              ind.computed_from_sessions, KIND_COUNT, "عدد الجلسات المستخدمة", (_DATA,)),

        # -- key levels -------------------------------------------------------- #
        _fact("level.support_1", "key_levels[SUPPORT][0].price",
              supports[0].price if supports else None, KIND_PRICE,
              "الدعم الأول", (_NEG, _INVAL)),
        _fact("level.support_2", "key_levels[SUPPORT][1].price",
              supports[1].price if len(supports) > 1 else None, KIND_PRICE,
              "الدعم الثاني", (_NEG,)),
        _fact("level.resistance_1", "key_levels[RESISTANCE][0].price",
              resistances[0].price if resistances else None, KIND_PRICE,
              "المقاومة الأولى", (_POS,)),
        _fact("level.resistance_2", "key_levels[RESISTANCE][1].price",
              resistances[1].price if len(resistances) > 1 else None, KIND_PRICE,
              "المقاومة الثانية", (_POS,)),
        _fact("level.breakout", "key_levels[BREAKOUT].price",
              by_kind["BREAKOUT"].price if "BREAKOUT" in by_kind else None, KIND_PRICE,
              "نقطة الاختراق", (_POS, _CONF)),
        _fact("level.invalidation", "key_levels[STOP].price",
              by_kind["STOP"].price if "STOP" in by_kind else None, KIND_PRICE,
              "مستوى الإبطال", (_NEG, _INVAL)),

        # -- primary scenario --------------------------------------------------- #
        _fact("scenario.primary.trigger", "scenarios[0].trigger",
              primary.trigger if primary else None, KIND_PRICE,
              "نقطة التفعيل", (_POS, _CONF)),
        _fact("scenario.primary.entry_low", "scenarios[0].entry_low",
              primary.entry_low if primary else None, KIND_PRICE,
              "بداية نطاق الدخول", (_POS,)),
        _fact("scenario.primary.entry_high", "scenarios[0].entry_high",
              primary.entry_high if primary else None, KIND_PRICE,
              "نهاية نطاق الدخول", (_POS,)),
        _fact("scenario.primary.target", "scenarios[0].target",
              primary.target if primary else None, KIND_PRICE,
              "الهدف المحسوب", (_POS,)),
        _fact("scenario.primary.stop", "scenarios[0].stop",
              primary.stop if primary else None, KIND_PRICE,
              "الوقف المحسوب", (_NEG, _INVAL)),
        _fact("scenario.primary.remaining_room_percent",
              "scenarios[0].remaining_room_percent",
              primary.remaining_room_percent if primary else None, KIND_PERCENT,
              "المسافة المتبقية إلى الهدف", (_POS,)),
        _fact("scenario.primary.risk_reward", "scenarios[0].risk_reward",
              primary.risk_reward if primary else None, KIND_RATIO,
              "العائد إلى المخاطرة", (_POS, _RISK)),
        _fact("scenario.primary.confidence", "scenarios[0].confidence",
              primary.confidence if primary else None, KIND_UNIT_RATIO,
              "ثقة السيناريو", (_POS, _RISK)),

        # -- confidence / data quality ------------------------------------------ #
        _fact("confidence.overall", "confidence.overall", result.confidence.overall,
              KIND_SCORE, "الثقة الإجمالية", (_EXEC, _RISK)),
        _fact("data.status", "data_quality.status",
              _STATUS_AR.get(_token(result.data_quality.status),
                             _token(result.data_quality.status)) or None,
              KIND_LABEL, "حالة البيانات", (_DATA,)),
        _fact("data.freshness", "data_quality.freshness_status",
              result.data_quality.freshness_status or None, KIND_LABEL,
              "حداثة البيانات", (_DATA,)),
        _fact("data.latest_session", "data_quality.latest_completed_session",
              result.data_quality.latest_completed_session or price.session_date or None,
              KIND_LABEL, "آخر جلسة مكتملة", (_DATA,)),
        _fact("data.provider", "data_quality.provider",
              (result.data_quality.provider or "").upper() or None, KIND_LABEL,
              "مزود البيانات", (_DATA,)),
        _fact("data.volume_safe", "data_quality.volume_safe_for_lookback",
              "حجم موثوق ضمن نافذة المراجعة" if volume_safe
              else "الحجم غير موثوق ضمن نافذة المراجعة", KIND_LABEL,
              "موثوقية الحجم", (_TECH, _RISK, _DATA, _CONF)),
    ]
    return FactRegistry([fact for fact in candidates if fact is not None])
