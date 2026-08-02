"""The ONE presentation model every AI-Analysis surface reads.

The page, the compact export card, the detailed export card and the daily chart
annotations all render from :class:`AnalysisPresentation`. It is derived purely
from an already-computed ``AnalysisResult`` (plus its optional narrative and the
authoritative universe name) — nothing here recalculates a recommendation, a
confidence, an indicator, a key level or a pullback measurement.

Two rules make the surfaces impossible to disagree:

* every numeric field is carried as its ORIGINAL ``float | None``; a renderer may
  format it but may never substitute a fallback of its own;
* a missing value stays ``None`` and formats as an em dash — never ``0``.

This module performs no provider, network or database operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

EM_DASH = "—"

#: Data-freshness codes are internal; a surface must never print one.
_OPERATIONAL_STATUS_AR = {
    "HISTORY_CURRENT": "التاريخ محدث · History current",
    "HISTORY_LAG": "تأخر في التاريخ · History lag",
    "TODAY_CANDLE_NOT_YET_COMPLETE": "شمعة اليوم لم تكتمل · Today incomplete",
    "PROVIDER_FINALIZATION_PENDING": "في انتظار اعتماد المزود · Publish pending",
    "MISSING": "غير متاح · Missing",
}

#: Currency-free numeric formatting shared by every surface, so the page, the
#: cards and the chart cannot round the same number differently.
_PRICE_DECIMALS = 2


def fmt_value(value, decimals: int = _PRICE_DECIMALS, suffix: str = "") -> str:
    """Format a number for display; ``None`` becomes an em dash, never ``0``."""

    if value is None:
        return EM_DASH
    if isinstance(value, str):
        return value.strip() or EM_DASH
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number != number:                       # NaN
        return EM_DASH
    return f"{number:,.{decimals}f}{suffix}"


def fmt_signed(value, decimals: int = _PRICE_DECIMALS, suffix: str = "") -> str:
    if value is None:
        return EM_DASH
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number != number:
        return EM_DASH
    return f"{number:+,.{decimals}f}{suffix}"


def fmt_compact(value) -> str:
    """Volume-style compact formatting (1,234,567 -> 1.23M)."""

    if value is None:
        return EM_DASH
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number != number:
        return EM_DASH
    for divisor, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(number) >= divisor:
            return f"{number / divisor:.2f}{unit}"
    return f"{number:,.0f}"


@dataclass(frozen=True)
class LevelPoint:
    """One annotated price level, with the weight a renderer should give it."""

    key: str
    label_ar: str
    label_en: str
    price: float | None
    #: ``major`` (breakout / invalidation), ``medium`` (support / resistance),
    #: ``minor`` (secondary), ``diagnostic`` (research-only pullback).
    weight: str = "medium"
    basis: str = ""

    @property
    def present(self) -> bool:
        return self.price is not None

    @property
    def display(self) -> str:
        return fmt_value(self.price)


@dataclass(frozen=True)
class PullbackPresentation:
    """Research-only pullback evidence. Never an entry signal."""

    state_ar: str = EM_DASH
    state_en: str = EM_DASH
    tone: str = "gray"
    prior_trend: str = EM_DASH
    structure: str = EM_DASH
    ema_relation: str = EM_DASH
    swing_high: float | None = None
    swing_high_date: str = EM_DASH
    impulse_low: float | None = None
    impulse_low_date: str = EM_DASH
    pullback_percent: float | None = None
    pullback_atr: float | None = None
    impulse_retracement_percent: float | None = None
    correction_bars: int | None = None
    support_zone_low: float | None = None
    support_zone_high: float | None = None
    support_reached: bool = False
    confluence: str = EM_DASH
    volume_behaviour: str = EM_DASH
    reversal_evidence: str = EM_DASH
    invalidation_reason: str = EM_DASH
    #: Internal enums — diagnostics only, never a user-facing sentence.
    invalidation_code: str = EM_DASH
    trend_code: str = EM_DASH
    ema_relation_code: str = EM_DASH
    structure_code: str = EM_DASH
    research_trigger: float | None = None
    research_stop: float | None = None
    research_target_1: float | None = None
    research_target_2: float | None = None
    research_risk_reward: float | None = None
    major_resistance: float | None = None
    explanation_ar: str = ""
    explanation_en: str = ""
    missing: tuple[str, ...] = ()
    available: bool = False

    @property
    def support_zone_display(self) -> str:
        if self.support_zone_low is None or self.support_zone_high is None:
            return EM_DASH
        return f"{fmt_value(self.support_zone_low)} – {fmt_value(self.support_zone_high)}"


@dataclass(frozen=True)
class ScenarioPresentation:
    scenario_id: str
    title: str
    state_ar: str = EM_DASH
    state_en: str = EM_DASH
    tone: str = "gray"
    trigger: float | None = None
    target: float | None = None
    stop: float | None = None
    risk_reward: float | None = None
    confidence: float | None = None
    remaining_room_percent: float | None = None
    confirmations: tuple[str, ...] = ()
    invalidations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChartCandle:
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None


@dataclass(frozen=True)
class AnalysisPresentation:
    """Everything any surface is allowed to show, resolved exactly once."""

    # -- identity ----------------------------------------------------------
    ticker: str
    company_name: str
    analysis_date: str
    last_completed_session: str
    provider: str

    # -- price -------------------------------------------------------------
    close: float | None = None
    previous_close: float | None = None
    change_amount: float | None = None
    change_percent: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    currency: str = "EGP"
    price_series: str = ""

    # -- decision ----------------------------------------------------------
    recommendation_ar: str = EM_DASH
    recommendation_en: str = EM_DASH
    recommendation_tone: str = "gray"
    confidence: float | None = None

    # -- trend / momentum --------------------------------------------------
    trend_ar: str = EM_DASH
    trend_en: str = EM_DASH
    trend_strength: float | None = None
    momentum_ar: str = EM_DASH
    momentum_en: str = EM_DASH
    momentum_strength: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    ema_alignment_ar: str = EM_DASH
    ema_alignment_en: str = EM_DASH

    # -- volume / volatility ----------------------------------------------
    volume: float | None = None
    average_volume: float | None = None
    volume_ratio: float | None = None
    turnover: float | None = None
    atr: float | None = None
    atr_percent: float | None = None
    recent_low: float | None = None
    recent_high: float | None = None
    #: Already computed by the Core and previously unsurfaced. Never derived here.
    rsi14: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    macd_histogram: float | None = None

    # -- levels ------------------------------------------------------------
    levels: tuple[LevelPoint, ...] = ()

    # -- scenarios / pullback ---------------------------------------------
    scenarios: tuple[ScenarioPresentation, ...] = ()
    pullback: PullbackPresentation = field(default_factory=PullbackPresentation)

    # -- risk --------------------------------------------------------------
    upside_percent: float | None = None
    downside_percent: float | None = None
    diagnostic_risk_reward: float | None = None

    # -- narrative / evidence ---------------------------------------------
    summary_ar: str = ""
    summary_en: str = ""
    #: ``recommendation_reasons`` is UNSIGNED — it mixes supporting and opposing
    #: facts (audit: "price below SMA20" sat beside "OBV rising"). It is carried
    #: as neutral assessment evidence and must never be rendered as "positive".
    assessment_evidence: tuple[str, ...] = ()
    watch_next: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    operational_status: str = EM_DASH

    # -- chart -------------------------------------------------------------
    candles: tuple[ChartCandle, ...] = ()
    chart_source: str = ""

    # -- provenance --------------------------------------------------------
    evidence_version: str = ""
    evidence_hash: str = ""

    # ---------------------------------------------------------------- api --

    def level(self, key: str) -> LevelPoint | None:
        for item in self.levels:
            if item.key == key:
                return item
        return None

    def level_price(self, key: str) -> float | None:
        found = self.level(key)
        return found.price if found else None

    @property
    def display_label(self) -> str:
        """``RAYA — Raya Holding For Financial Investments``."""

        name = (self.company_name or "").strip()
        return f"{self.ticker} {EM_DASH} {name}" if name else self.ticker

    @property
    def change_display(self) -> str:
        if self.change_amount is None and self.change_percent is None:
            return EM_DASH
        return f"{fmt_signed(self.change_amount)} ({fmt_signed(self.change_percent, suffix='%')})"

    @property
    def confidence_display(self) -> str:
        return fmt_value(self.confidence, decimals=0, suffix="%")

    def identity(self) -> dict:
        """The cross-surface contract: every surface must agree on these."""

        return {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "analysis_date": self.analysis_date,
            "last_completed_session": self.last_completed_session,
            "close": self.close,
            "recommendation": self.recommendation_en,
            "confidence": self.confidence,
            "support_1": self.level_price("support_1"),
            "support_2": self.level_price("support_2"),
            "resistance_1": self.level_price("resistance_1"),
            "resistance_2": self.level_price("resistance_2"),
            "breakout": self.level_price("breakout"),
            "invalidation": self.level_price("invalidation"),
            "pullback_state": self.pullback.state_en,
            "pullback_support_zone": (self.pullback.support_zone_low,
                                      self.pullback.support_zone_high),
        }


def _levels_from(result: Any, selected: dict, pullback) -> tuple[LevelPoint, ...]:
    """Level slots with a deterministic visual weight; selection only."""

    spec = (
        ("support_1", "الدعم الأول", "Support 1", "medium"),
        ("support_2", "الدعم الثاني", "Support 2", "minor"),
        ("resistance_1", "المقاومة الأولى", "Resistance 1", "medium"),
        ("resistance_2", "المقاومة الثانية", "Resistance 2", "minor"),
        ("breakout", "نقطة الاختراق", "Breakout", "major"),
        ("invalidation", "مستوى الإبطال", "Invalidation", "major"),
    )
    points = []
    for key, label_ar, label_en, weight in spec:
        chosen = selected.get(key) or {}
        points.append(LevelPoint(key=key, label_ar=label_ar, label_en=label_en,
                                 price=chosen.get("price"), weight=weight,
                                 basis=str(chosen.get("basis") or "")))
    if pullback is not None and pullback.entry_trigger is not None:
        points.append(LevelPoint(
            key="research_trigger", label_ar="التفعيل البحثي",
            label_en="Research Trigger", price=pullback.entry_trigger,
            weight="diagnostic", basis="PULLBACK_RESEARCH"))
    if pullback is not None and pullback.major_resistance is not None:
        points.append(LevelPoint(
            key="major_resistance", label_ar="المقاومة الرئيسية",
            label_en="Major Resistance", price=pullback.major_resistance,
            weight="minor", basis="PULLBACK_RESEARCH"))
    return tuple(points)


def build_presentation(result, narrative=None, *, company_name: str | None = None
                       ) -> AnalysisPresentation:
    """Resolve an ``AnalysisResult`` into the single presentation model.

    Every figure is copied from evidence the Core already calculated. This
    function never derives a decision, level, indicator or pullback measurement.
    """

    # Imported lazily: the dashboard package pulls in Streamlit, and this model is
    # also used by headless renderers and tests.
    from core.ai_pullback_labels import localize
    from dashboard.ai_stock_analysis_components import (
        RECOMMENDATION_LABELS,
        momentum_reading,
        pullback_scenario_view,
        scenario_view,
        selected_levels,
        trend_reading,
    )

    price = result.price
    indicators = result.indicators
    quality = result.data_quality

    recommendation_ar, recommendation_en, tone = RECOMMENDATION_LABELS.get(
        result.recommendation, ("البيانات غير كافية", "Data Insufficient", "red"))
    trend_ar, trend_en, _tone, _strength = trend_reading(result)
    momentum_ar, momentum_en, _mtone, _mstrength = momentum_reading(result)

    scenario = getattr(result, "pullback_scenario", None)
    pullback_view = pullback_scenario_view(scenario) or {}
    pullback = PullbackPresentation(
        state_ar=pullback_view.get("state_ar", EM_DASH),
        state_en=pullback_view.get("state_en", EM_DASH),
        tone=pullback_view.get("tone", "gray"),
        prior_trend=pullback_view.get("trend", EM_DASH),
        structure=pullback_view.get("structure", EM_DASH),
        ema_relation=pullback_view.get("ema_relation", EM_DASH),
        swing_high=getattr(scenario, "swing_high", None),
        swing_high_date=pullback_view.get("swing_high_date", EM_DASH),
        impulse_low=getattr(scenario, "impulse_low", None),
        impulse_low_date=pullback_view.get("impulse_low_date", EM_DASH),
        pullback_percent=getattr(scenario, "pullback_percent", None),
        pullback_atr=getattr(scenario, "pullback_atr", None),
        impulse_retracement_percent=getattr(scenario, "impulse_retracement_percent", None),
        correction_bars=getattr(scenario, "correction_bars", None),
        support_zone_low=getattr(scenario, "support_zone_low", None),
        support_zone_high=getattr(scenario, "support_zone_high", None),
        support_reached=bool(getattr(scenario, "support_reached", False)),
        confluence=pullback_view.get("confluence", EM_DASH),
        volume_behaviour=pullback_view.get("volume", EM_DASH),
        reversal_evidence=pullback_view.get("confirmation", EM_DASH),
        invalidation_reason=pullback_view.get("invalidation", EM_DASH),
        invalidation_code=pullback_view.get("invalidation_code", EM_DASH),
        trend_code=pullback_view.get("trend_code", EM_DASH),
        ema_relation_code=pullback_view.get("ema_relation_code", EM_DASH),
        structure_code=pullback_view.get("structure_code", EM_DASH),
        research_trigger=getattr(scenario, "entry_trigger", None),
        research_stop=getattr(scenario, "stop_loss", None),
        research_target_1=getattr(scenario, "target_1", None),
        research_target_2=getattr(scenario, "target_2", None),
        research_risk_reward=getattr(scenario, "reward_risk", None),
        major_resistance=getattr(scenario, "major_resistance", None),
        explanation_ar=getattr(scenario, "explanation_ar", "") or "",
        explanation_en=getattr(scenario, "explanation_en", "") or "",
        missing=tuple(pullback_view.get("missing", ())),
        available=scenario is not None,
    )

    scenarios = []
    for item in result.scenarios or ():
        view = scenario_view(item)
        scenarios.append(ScenarioPresentation(
            scenario_id=item.scenario_id, title=item.title,
            state_ar=view.get("state_ar", EM_DASH), state_en=view.get("state_en", EM_DASH),
            tone=view.get("tone", "gray"), trigger=item.trigger, target=item.target,
            stop=item.stop, risk_reward=item.risk_reward, confidence=item.confidence,
            remaining_room_percent=item.remaining_room_percent,
            confirmations=tuple(item.confirmation_requirements or ()),
            invalidations=tuple(item.invalidation_conditions or ()),
        ))

    daily = result.daily_chart_series
    candles = tuple(
        ChartCandle(date=str(point.timestamp)[:10], open=point.open, high=point.high,
                    low=point.low, close=point.close, volume=point.volume)
        for point in (daily.points if daily else ())
    )
    recent = candles[-60:] if candles else ()
    highs = [c.high for c in recent if c.high is not None]
    lows = [c.low for c in recent if c.low is not None]

    if company_name is None:
        from core.universe import company_name as universe_company_name
        company_name = universe_company_name(result.request.symbol)

    # The narrative engine emits Arabic only. English mode gets a deterministic
    # English summary composed from the same typed evidence — never a renderer
    # translation, and never the Arabic text relabelled as English.
    from core.ai_analysis_narrative import build_english_summary

    narrative_language = str(getattr(narrative, "language", "") or "ar").lower()
    narrative_text = (getattr(narrative, "summary", "") or "") if narrative else ""
    arabic_summary = narrative_text if narrative_language.startswith("ar") else ""
    english_summary = (narrative_text if narrative_language.startswith("en")
                       else build_english_summary(result))

    alignment_code = getattr(scenario, "ema_alignment_status", "") or "UNAVAILABLE"

    primary = scenarios[0] if scenarios else None
    invalidation_price = next(
        (lv.get("price") for key, lv in selected_levels(result).items()
         if key == "invalidation" and lv), None)
    downside = None
    if invalidation_price is not None and price.close:
        downside = (float(invalidation_price) - float(price.close)) / float(price.close) * 100

    return AnalysisPresentation(
        ticker=result.request.symbol,
        company_name=company_name or "",
        analysis_date=str(result.request.as_of)[:10],
        last_completed_session=quality.latest_completed_session or EM_DASH,
        provider=quality.provider or EM_DASH,
        close=price.close, previous_close=price.previous_close,
        change_amount=price.change_amount, change_percent=price.change_percent,
        open=price.open, high=price.high, low=price.low,
        currency=price.currency, price_series=price.price_series,
        recommendation_ar=recommendation_ar, recommendation_en=recommendation_en,
        recommendation_tone=tone, confidence=result.confidence.overall,
        trend_ar=trend_ar, trend_en=trend_en, trend_strength=indicators.trend_strength,
        momentum_ar=momentum_ar, momentum_en=momentum_en,
        momentum_strength=indicators.momentum_strength,
        ema20=getattr(indicators, "ema_20", None),
        ema50=getattr(indicators, "ema_50", None),
        ema200=getattr(indicators, "ema_200", None),
        ema_alignment_ar=localize(alignment_code, "ema_alignment_status", "AR"),
        ema_alignment_en=localize(alignment_code, "ema_alignment_status", "EN"),
        volume=price.volume,
        average_volume=getattr(indicators, "average_volume_20", None),
        volume_ratio=getattr(indicators, "volume_ratio", None),
        turnover=getattr(price, "turnover", None),
        rsi14=getattr(indicators, "rsi_14", None),
        sma20=getattr(indicators, "sma_20", None),
        sma50=getattr(indicators, "sma_50", None),
        macd_histogram=getattr(indicators, "macd_histogram", None),
        atr=getattr(indicators, "atr_14", None),
        atr_percent=(float(indicators.atr_14) / float(price.close) * 100
                     if getattr(indicators, "atr_14", None) is not None and price.close
                     else None),
        recent_low=min(lows) if lows else None,
        recent_high=max(highs) if highs else None,
        levels=_levels_from(result, selected_levels(result), scenario),
        scenarios=tuple(scenarios), pullback=pullback,
        upside_percent=primary.remaining_room_percent if primary else None,
        downside_percent=downside,
        diagnostic_risk_reward=primary.risk_reward if primary else None,
        summary_ar=arabic_summary,
        summary_en=english_summary,
        assessment_evidence=tuple(getattr(result, "recommendation_reasons", ()) or ()),
        watch_next=tuple(primary.confirmations if primary else ()),
        warnings=tuple(getattr(quality, "warnings", ()) or ()),
        operational_status=_OPERATIONAL_STATUS_AR.get(
            getattr(quality, "freshness_status", ""), EM_DASH),
        candles=candles, chart_source=(daily.source if daily else ""),
        evidence_version=result.evidence_version, evidence_hash=result.evidence_hash,
    )


__all__ = [
    "EM_DASH", "AnalysisPresentation", "ChartCandle", "LevelPoint",
    "PullbackPresentation", "ScenarioPresentation", "build_presentation",
    "fmt_compact", "fmt_signed", "fmt_value",
]
