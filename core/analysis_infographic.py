"""The Arabic-first analysis infographic.

Renders an :class:`~core.analysis_presentation.AnalysisPresentation` — and only
that — into a portrait PNG. Nothing here computes a recommendation, a
confidence, a level, an indicator or a pullback measurement; every figure is
copied from the model, and a missing value stays an em dash.

Composed from the measured primitives in :mod:`core.infographic_layout`, so each
section reports the height it needs and the page is budgeted rather than
hand-positioned.

Deliberate deviations from the supplied references, both truthfulness-driven:

* the price is labelled **آخر إغلاق مؤكد** (last completed close), never
  "current price" — we hold a D-1 close, not a live quote;
* assessment evidence is shown NEUTRALLY, because ``recommendation_reasons`` is
  unsigned and classifying it here would be a renderer-side judgement.
"""

from __future__ import annotations

from core.ai_pullback_labels import bilingual, localize, localize_assessment_reasons
from core.analysis_chart import (
    DEFAULT_TIMEFRAME,
    LAST_PRICE_KEY,
    resolve_label_lanes,
    select_window,
)
from core.analysis_presentation import (
    EM_DASH,
    AnalysisPresentation,
    fmt_compact,
    fmt_signed,
    fmt_value,
)
from core.infographic_layout import (
    PALETTE,
    PANEL_PAD,
    ROLE_COLOUR,
    SPACING,
    BulletGrid,
    BulletList,
    Canvas,
    Panel,
    Row,
    RowGrid,
    Tile,
    TileRow,
    TypeScale,
    tint,
)

#: Portrait formats. Both exact; the HD variant is the same layout at 1.25x.
INFOGRAPHIC_SIZES = {
    "INFOGRAPHIC": (1080, 1920),
    "INFOGRAPHIC_HD": (1350, 2400),
    # Taller variant for readers who want the full evidence. The primary card
    # stays concise rather than compressing type to fit everything.
    "INFOGRAPHIC_EXTENDED": (1080, 2400),
}
DEFAULT_INFOGRAPHIC_SIZE = "INFOGRAPHIC"

INFOGRAPHIC_SIZE_LABELS = {
    "INFOGRAPHIC": "إنفوجرافيك 1080×1920 · Infographic 1080×1920",
    "INFOGRAPHIC_HD": "إنفوجرافيك عالي الدقة 1350×2400 · Infographic HD 1350×2400",
    "INFOGRAPHIC_EXTENDED": "إنفوجرافيك موسّع 1080×2400 · Extended 1080×2400",
}

LANGUAGES = ("AR", "EN", "BILINGUAL")
DEFAULT_LANGUAGE = "AR"
LANGUAGE_LABELS = {"AR": "العربية", "EN": "English", "BILINGUAL": "ثنائي اللغة · Bilingual"}

#: Sections, in draw order. Asserted by tests.
SECTION_KEYS = ("header", "quick_metrics", "chart", "technical_read", "assessment",
                "levels", "indicators", "scenarios", "evidence", "pullback",
                "summary", "footer")

#: Roughly 90 completed sessions keeps current structure legible on a phone.
CHART_SESSIONS = 90

_T = {
    "last_close":    ("آخر إغلاق مؤكد", "Last Completed Close"),
    "change":        ("التغير اليومي", "Daily Change"),
    "confidence":    ("الثقة", "Confidence"),
    "analysis_date": ("تاريخ التحليل", "Analysis date"),
    "session":       ("جلسة مكتملة", "Completed session"),
    "high":          ("أعلى", "High"),
    "low":           ("أدنى", "Low"),
    "volume":        ("الحجم", "Volume"),
    "avg_volume":    ("متوسط الحجم", "Avg volume"),
    "turnover":      ("قيمة التداول", "Turnover"),
    "atr":           ("ATR", "ATR"),
    "rsi":           ("RSI 14", "RSI 14"),
    "chart":         ("الرسم اليومي", "Daily Chart"),
    "technical":     ("نظرة فنية", "Technical Read"),
    "assessment":    ("التقييم", "Assessment"),
    "support":       ("مستويات الدعم", "Support"),
    "resistance":    ("مستويات المقاومة", "Resistance"),
    "indicators":    ("المؤشرات", "Indicators"),
    "scenarios":     ("السيناريوهات", "Scenarios"),
    "evidence":      ("أسباب التقييم", "Assessment Evidence"),
    "pullback":      ("جودة التصحيح", "Pullback Health Analysis"),
    "summary":       ("الخلاصة", "Summary"),
    "watch":         ("ما يجب متابعته", "Watch next"),
    "trend":         ("الاتجاه", "Trend"),
    "momentum":      ("الزخم", "Momentum"),
    "trend_strength": ("قوة الاتجاه", "Trend strength"),
    "support_1":     ("دعم أول", "Support 1"),
    "support_2":     ("دعم ثانٍ", "Support 2"),
    "pullback_zone": ("منطقة التصحيح البحثية", "Research pullback zone"),
    "resistance_1":  ("مقاومة أولى", "Resistance 1"),
    "resistance_2":  ("مقاومة ثانية", "Resistance 2"),
    "breakout":      ("نقطة الاختراق", "Breakout"),
    "invalidation":  ("مستوى الإبطال", "Invalidation"),
    "positive":      ("سيناريو إيجابي", "Positive"),
    "waiting":       ("سيناريو الانتظار", "Waiting range"),
    "negative":      ("سيناريو سلبي", "Negative"),
    "research_only": ("بحثي فقط — غير معتمد كإشارة دخول",
                      "Research Only — not an entry signal"),
    "research_badge": ("بحثي فقط", "Research Only"),
    "no_english":    ("الملخص الإنجليزي غير متاح", "English summary unavailable"),
    "decision_only": ("دعم قرار فقط", "Decision Support Only"),
    "paper":         ("بحثي / ورقي", "Research / Paper"),
    "production":    ("التنفيذ غير مفعّل", "Production Disabled"),
    "disclaimer":    ("تحليل تعليمي إرشادي — ليس توصية بالشراء أو البيع",
                      "Educational and informational only — not a buy or sell recommendation"),
}


def t(key: str, language: str) -> str:
    arabic, english = _T[key]
    if language == "AR":
        return arabic
    if language == "EN":
        return english
    return f"{arabic} · {english}"


def _tone_colour(tone: str):
    return PALETTE.get(tone if tone in PALETTE else "gray")


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def _quick_metric_tiles(p: AnalysisPresentation, language: str):
    """Only tiles whose value exists. An absent metric is dropped, not em-dashed."""

    candidates = [
        ("high", p.high, fmt_value, PALETTE["green"]),
        ("low", p.low, fmt_value, PALETTE["red"]),
        ("volume", p.volume, fmt_compact, PALETTE["text"]),
        ("turnover", p.turnover, fmt_compact, PALETTE["text"]),
        ("atr", p.atr, fmt_value, PALETTE["text"]),
    ]
    tiles = [Tile(label=t(key, language), value=formatter(value), value_colour=colour)
             for key, value, formatter, colour in candidates if value is not None]
    return tuple(tiles[:4])          # four at most; density hurts readability


def _technical_lines(p: AnalysisPresentation, language: str) -> tuple:
    """3–5 deterministic observations, each restating one existing field."""

    lines = []
    trend = p.trend_ar if language != "EN" else p.trend_en
    momentum = p.momentum_ar if language != "EN" else p.momentum_en
    if trend != EM_DASH:
        lines.append(f"{t('trend', language)}: {trend}")
    if momentum != EM_DASH:
        lines.append(f"{t('momentum', language)}: {momentum}")
    if p.ema_alignment_ar != EM_DASH:
        lines.append(p.ema_alignment_ar if language != "EN" else p.ema_alignment_en)
    if p.volume_ratio is not None:
        ratio = fmt_value(p.volume_ratio, 2)
        lines.append(f"{t('volume', language)} {ratio}× "
                     + ("من المتوسط" if language != "EN" else "of average"))
    if p.rsi14 is not None:
        lines.append(f"RSI 14: {fmt_value(p.rsi14, 1)}")
    return tuple(lines[:4])


def _level_rows(p: AnalysisPresentation, keys, language: str, role: str,
                *, font_size: int | None = None):
    rows = []
    for key in keys:
        level = p.level(key)
        if level is None or not level.present:
            continue
        rows.append(Row(label=t(key, language), value=level.display,
                        value_colour=ROLE_COLOUR[role if key.startswith(("support",))
                                                 else role],
                        font_size=font_size))
    return rows


def _same_at_display_precision(left, right, decimals: int = 2) -> bool:
    """Whether two stored numbers render as the same value at this precision."""

    if left is None or right is None:
        return False
    return fmt_value(left, decimals) == fmt_value(right, decimals)


def _resistance_rows(p: AnalysisPresentation, language: str, *,
                     font_size: int | None = None):
    """Display levels once without changing any stored price."""

    rows = []
    resistance_1 = p.level("resistance_1")
    resistance_2 = p.level("resistance_2")
    breakout = p.level("breakout")
    invalidation = p.level("invalidation")

    combined = (resistance_1 is not None and resistance_1.present
                and breakout is not None and breakout.present
                and _same_at_display_precision(resistance_1.price, breakout.price))
    if combined:
        rows.append(Row(
            label=_label_of("المقاومة / الاختراق", "Resistance / breakout", language),
            value=resistance_1.display, value_colour=ROLE_COLOUR["breakout"],
            font_size=font_size))
    else:
        if resistance_1 is not None and resistance_1.present:
            rows.append(Row(label=t("resistance_1", language),
                            value=resistance_1.display,
                            value_colour=ROLE_COLOUR["resistance"],
                            font_size=font_size))
        if breakout is not None and breakout.present:
            rows.append(Row(label=t("breakout", language), value=breakout.display,
                            value_colour=ROLE_COLOUR["breakout"],
                            font_size=font_size))

    if resistance_2 is not None and resistance_2.present:
        duplicate = any(_same_at_display_precision(resistance_2.price, level.price)
                        for level in (resistance_1, breakout)
                        if level is not None and level.present)
        if not duplicate:
            rows.append(Row(label=t("resistance_2", language),
                            value=resistance_2.display,
                            value_colour=ROLE_COLOUR["resistance"],
                            font_size=font_size))
    if invalidation is not None and invalidation.present:
        rows.append(Row(label=t("invalidation", language), value=invalidation.display,
                        value_colour=ROLE_COLOUR["invalidation"],
                        font_size=font_size))
    return rows


def _first_clause(text, language: str) -> str:
    """Take the side of a bilingual ``عربي · English`` value the card needs.

    Panel rows are narrow; a full bilingual value overflowed its column.
    """

    parts = [part.strip() for part in str(text or "").split("·") if part.strip()]
    if len(parts) < 2:
        return str(text or EM_DASH)
    return parts[-1] if language == "EN" else parts[0]


def _label_of(arabic: str, english: str, language: str) -> str:
    if language == "AR":
        return arabic
    if language == "EN":
        return english
    return f"{arabic} · {english}"


def _scenario_rows(p: AnalysisPresentation, language: str, *, full: bool = False,
                   font_size: int | None = None):
    """The three typed scenarios plus one diagnostic R/R, at most."""

    rows = []
    positive = next((s for s in p.scenarios if "breakout" in s.scenario_id), None)
    negative = next((s for s in p.scenarios if "breakdown" in s.scenario_id), None)

    if positive is not None:
        rows.append(Row(label=t("positive", language),
                        value=f"{fmt_value(positive.trigger)} → {fmt_value(positive.target)}",
                        value_colour=PALETTE["green"], font_size=font_size))
    support_1 = p.level_price("support_1")
    resistance_1 = p.level_price("resistance_1")
    if support_1 is not None and resistance_1 is not None:
        # A RESTATEMENT of two levels the Core already computed — not a strategy.
        rows.append(Row(label=t("waiting", language),
                        value=f"{fmt_value(support_1)} – {fmt_value(resistance_1)}",
                        value_colour=PALETTE["blue"], font_size=font_size))
    if negative is not None:
        rows.append(Row(label=t("negative", language),
                        value=f"{fmt_value(negative.trigger)} ↓",
                        value_colour=PALETTE["red"], font_size=font_size))
    risk_reward = next((scenario.risk_reward for scenario in (positive, negative)
                        if scenario is not None and scenario.risk_reward is not None), None)
    if risk_reward is not None:
        rows.append(Row(
            label=_label_of("العائد إلى المخاطرة", "Reward / risk", language),
            value=fmt_value(risk_reward, 2), font_size=font_size))
    return rows[:4]


def _methodology_lines(p: AnalysisPresentation, language: str) -> tuple:
    """Provenance the model already carries. Nothing here is invented."""

    return (
        _label_of(f"المصدر: EODHD · جلسة مكتملة {p.last_completed_session}",
                  f"Source: EODHD · completed session {p.last_completed_session}",
                  language),
        _label_of(f"أساس السعر: {p.price_series}", f"Price basis: {p.price_series}",
                  language),
        _label_of(f"حالة البيانات: {p.operational_status}",
                  f"Data status: {p.operational_status}", language),
        _label_of("المستويات محسوبة من شموع يومية مكتملة حتى D-1",
                  "Levels are computed from completed daily candles through D-1",
                  language),
        _label_of("تحليل جودة التصحيح تشخيصي بحثي ولا يغيّر التوصية",
                  "Pullback Health is a research diagnostic and never alters the "
                  "recommendation", language),
    )


def _pullback_rows(p: AnalysisPresentation, language: str, *, full: bool = False,
                   font_size: int | None = None):
    pb = p.pullback
    rows = [Row(label=_label_of("الحالة الحالية", "Current state", language),
                value=pb.state_ar if language != "EN" else pb.state_en,
                value_colour=PALETTE["purple"], font_size=font_size)]
    if pb.pullback_percent is not None:
        rows.append(Row(label="نسبة التصحيح" if language != "EN" else "Pullback %",
                        value=fmt_value(pb.pullback_percent, 2, "%"),
                        font_size=font_size))
    if pb.pullback_atr is not None:
        rows.append(Row(label="عمق ATR" if language != "EN" else "ATR depth",
                        value=fmt_value(pb.pullback_atr, 2, " ATR"),
                        font_size=font_size))
    if pb.support_zone_low is not None:
        rows.append(Row(label=_label_of("منطقة الدعم", "Support zone", language),
                        value=pb.support_zone_display, value_colour=PALETTE["purple"],
                        font_size=font_size))
    if pb.volume_behaviour != EM_DASH:
        rows.append(Row(label=_label_of("سلوك الحجم", "Volume behaviour", language),
                        value=_first_clause(pb.volume_behaviour, language),
                        font_size=font_size, span=2))
    diagnostic = None
    diagnostic_label = None
    if pb.invalidation_reason not in (EM_DASH, ""):
        if pb.invalidation_code not in (EM_DASH, ""):
            diagnostic = (bilingual(pb.invalidation_code, "invalidation_reason")
                          if language == "BILINGUAL" else
                          localize(pb.invalidation_code, "invalidation_reason", language))
        else:
            diagnostic = _first_clause(pb.invalidation_reason, language)
        diagnostic_label = (_label_of("السبب", "Reason", language)
                            if language == "BILINGUAL" else
                            _label_of("سبب الفشل أو الإبطال", "Failure / invalidation",
                                      language))
    elif pb.reversal_evidence not in (EM_DASH, ""):
        diagnostic = _first_clause(pb.reversal_evidence, language)
        diagnostic_label = _label_of("دليل الارتداد", "Reversal evidence", language)
    if diagnostic is not None:
        rows.append(Row(label=diagnostic_label, value=diagnostic,
                        font_size=font_size, span=2))
    if full:
        # Secondary diagnostics — Extended only. Every value is copied verbatim
        # from the immutable presentation model.
        for label_ar, label_en, value in (
                ("جودة الاتجاه السابق", "Prior trend",
                 _first_clause(pb.prior_trend, language)),
                ("آخر قمة مؤكدة", "Confirmed swing high", fmt_value(pb.swing_high)),
                ("قاع بداية الموجة", "Impulse start low", fmt_value(pb.impulse_low)),
                ("ارتداد الموجة", "Impulse retracement",
                 fmt_value(pb.impulse_retracement_percent, 2, "%")),
                ("جلسات التصحيح", "Correction sessions",
                 str(pb.correction_bars) if pb.correction_bars is not None else EM_DASH),
                ("تداخل الدعم", "Support confluence", pb.confluence),
                ("المقاومة الرئيسية", "Major resistance",
                 fmt_value(pb.major_resistance))):
            if value not in (EM_DASH, "", None):
                span = 2 if label_ar in ("جودة الاتجاه السابق", "تداخل الدعم") else 1
                rows.append(Row(label=_label_of(label_ar, label_en, language),
                                value=str(value), font_size=font_size, span=span))
    return rows


def _summary_lines(p: AnalysisPresentation, language: str) -> tuple[str, ...]:
    """A deterministic 2–3 line digest from existing typed fields only."""

    if language == "EN":
        conclusion = f"Conclusion: {p.recommendation_en}; {p.trend_en}."
        risk_label, next_label = "Risk", "Next"
        risk_suffix, next_suffix = "break", "confirmed close above"
    elif language == "BILINGUAL":
        conclusion = (f"الخلاصة: {p.recommendation_ar} · "
                      f"Conclusion: {p.recommendation_en}")
        risk_label, next_label = "الخطر · Risk", "التالي · Next"
        risk_suffix, next_suffix = "كسر · break", "إغلاق مؤكد فوق · close above"
    else:
        conclusion = f"الخلاصة: {p.recommendation_ar}؛ {p.trend_ar}."
        risk_label, next_label = "الخطر", "التالي"
        risk_suffix, next_suffix = "كسر", "إغلاق مؤكد فوق"

    lines = [conclusion]
    invalidation = p.level_price("invalidation")
    if invalidation is not None:
        lines.append(f"{risk_label}: {risk_suffix} {fmt_value(invalidation)}.")
    positive = next((s for s in p.scenarios if "breakout" in s.scenario_id), None)
    if positive is not None and positive.trigger is not None:
        lines.append(f"{next_label}: {next_suffix} {fmt_value(positive.trigger)}.")
    return tuple(lines[:3])


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #

def render_infographic_png(presentation: AnalysisPresentation, *,
                           size: str = DEFAULT_INFOGRAPHIC_SIZE,
                           language: str = DEFAULT_LANGUAGE,
                           font_path: str | None = None,
                           chart_png: bytes | None = None) -> bytes:
    """Render the infographic. Consumes the presentation model only."""

    if size not in INFOGRAPHIC_SIZES:
        raise ValueError(f"unknown size {size!r}; expected {sorted(INFOGRAPHIC_SIZES)}")
    if language not in LANGUAGES:
        raise ValueError(f"unknown language {language!r}; expected {LANGUAGES}")

    width, height = INFOGRAPHIC_SIZES[size]
    canvas = Canvas(width, height, font_path=font_path, scale=width / 1080.0)
    p = presentation
    s = canvas.scale
    margin = int(40 * s)
    left, right = margin, width - margin
    content = right - left

    # A soft top glow so the header reads as a distinct zone.
    for row in range(int(300 * s)):
        limit = int(300 * s)
        canvas.draw.line((0, row, width, row),
                         fill=tint(PALETTE["surface_2"], (1.0 - row / limit) * 0.85))

    y = margin

    # ---------------------------------------------------------- 1. header --
    # Two blocks: identity on the right, price on the left. Provider/session
    # metadata is demoted to a quiet strip so it cannot compete with the price.
    header_h = int(268 * s)
    canvas.panel((left, y, right, y + header_h), fill=PALETTE["surface"])
    mid = left + (right - left) // 2

    monogram = int(84 * s)
    canvas.draw.rounded_rectangle((right - PANEL_PAD - monogram, y + PANEL_PAD,
                                   right - PANEL_PAD, y + PANEL_PAD + monogram),
                                  radius=int(monogram * 0.28),
                                  fill=tint(PALETTE["blue"], 0.25),
                                  outline=tint(PALETTE["blue"], 0.55), width=3)
    canvas.text_centre(right - PANEL_PAD - monogram // 2,
                       y + PANEL_PAD + int(monogram * 0.24), p.ticker[:2],
                       canvas.type(34), PALETTE["blue"], bold=True, rtl=False)
    ticker_right = right - PANEL_PAD - monogram - int(18 * s)
    canvas.text_rtl(ticker_right, y + PANEL_PAD, p.ticker,
                    canvas.type(TypeScale.ticker), PALETTE["text"], bold=True)
    name_y = y + PANEL_PAD + canvas.line_height(canvas.type(TypeScale.ticker))
    name_size = canvas.type(TypeScale.caption)
    name_width = ticker_right - mid - int(10 * s)
    name_lines = canvas.wrap(p.company_name or EM_DASH, name_size, name_width)
    while len(name_lines) > 2 and name_size > canvas.type(18):
        name_size -= 1
        name_lines = canvas.wrap(p.company_name or EM_DASH, name_size, name_width)
    for line in name_lines:
        canvas.text_rtl(ticker_right, name_y, line, name_size,
                        PALETTE["muted"])
        name_y += canvas.line_height(name_size)

    # price block
    canvas.text_ltr(left + PANEL_PAD, y + PANEL_PAD, t("last_close", language),
                    canvas.type(TypeScale.caption), PALETTE["meta"])
    price_y = y + PANEL_PAD + canvas.line_height(canvas.type(TypeScale.caption))
    canvas.text_ltr(left + PANEL_PAD, price_y, f"{fmt_value(p.close)} {p.currency}",
                    canvas.type(TypeScale.price), PALETTE["text"], bold=True)
    change_tone = (PALETTE["green"] if (p.change_amount or 0) > 0
                   else PALETTE["red"] if (p.change_amount or 0) < 0 else PALETTE["muted"])
    arrow = "▲" if (p.change_amount or 0) > 0 else "▼" if (p.change_amount or 0) < 0 else "■"
    change_y = price_y + canvas.line_height(canvas.type(TypeScale.price))
    canvas.text_ltr(left + PANEL_PAD, change_y,
                    f"{arrow} {fmt_signed(p.change_amount)} "
                    f"({fmt_signed(p.change_percent, 2, '%')})",
                    canvas.type(TypeScale.label), change_tone, bold=True)

    rec = p.recommendation_ar if language != "EN" else p.recommendation_en
    label_size = canvas.type(TypeScale.label)
    badge_y = change_y + canvas.line_height(label_size) + SPACING["sm"]
    badge_w = int(canvas.text_width(rec, label_size, bold=True) + label_size * 1.5)
    strip_left = left + PANEL_PAD
    strip_right = mid - SPACING["md"]
    strip_h = canvas.badge_height(label_size) + int(6 * s)
    tone = _tone_colour(p.recommendation_tone)
    canvas.draw.rounded_rectangle((strip_left, badge_y, strip_right, badge_y + strip_h),
                                  radius=strip_h // 2, fill=tint(tone, 0.16),
                                  outline=tint(tone, 0.65), width=2)
    canvas.badge(strip_left + badge_w, badge_y + int(3 * s), rec, label_size,
                 tone, solid=True)
    canvas.text_rtl(strip_right - SPACING["md"], badge_y + int(9 * s),
                    f"{t('confidence', language)} {p.confidence_display}",
                    canvas.type(TypeScale.caption), PALETTE["text"], bold=True)
    y += header_h + SPACING["sm"]

    # quiet metadata strip
    canvas.text_rtl(right, y, f"{t('analysis_date', language)} {p.analysis_date}"
                    f"   ·   {t('session', language)} {p.last_completed_session}"
                    f"   ·   EODHD", canvas.type(TypeScale.footer), PALETTE["meta"])
    y += canvas.line_height(canvas.type(TypeScale.footer)) + SPACING["md"]

    # -------------------------------------------------- 2. quick metrics --
    tiles = _quick_metric_tiles(p, language)
    if tiles:
        row = TileRow(tiles=tiles)
        need = row.measure(canvas, content)
        row.render(canvas, (left, y, right, y + need))
        y += need + SPACING["lg"]

    # ----------------------------------------------------------- 3. chart --
    chart_h = int(352 * s)
    if chart_png:
        from io import BytesIO

        from PIL import Image

        chart = Image.open(BytesIO(chart_png)).convert("RGB")
        chart.thumbnail((content, chart_h), Image.LANCZOS)
        canvas.panel((left, y, right, y + chart_h), fill=PALETTE["surface"])
        canvas.image.paste(chart, (left + (content - chart.width) // 2,
                                   y + (chart_h - chart.height) // 2))
    else:
        _draw_price_panel(canvas, (left, y, right, y + chart_h), p, language)
    y += chart_h + SPACING["lg"]

    # ------------------------------------- 4-9. panels, budgeted to fit --
    technical = _technical_lines(p, language)
    extended = size == "INFOGRAPHIC_EXTENDED"
    row_font = None if extended else 18 if language == "BILINGUAL" else 23

    support_rows = _level_rows(p, ("support_1", "support_2"), language, "support",
                               font_size=row_font)
    if p.pullback.support_zone_low is not None:
        support_rows.append(Row(label=t("pullback_zone", language),
                                value=p.pullback.support_zone_display,
                                value_colour=PALETTE["purple"],
                                font_size=row_font))
    resistance_rows = _resistance_rows(p, language, font_size=row_font)

    indicator_rows = [
        Row(label=t("trend", language),
            value=p.trend_ar if language != "EN" else p.trend_en,
            font_size=row_font),
        Row(label=t("momentum", language),
            value=p.momentum_ar if language != "EN" else p.momentum_en,
            font_size=row_font),
        Row(label="EMA20", value=fmt_value(p.ema20), font_size=row_font),
        Row(label="EMA50", value=fmt_value(p.ema50), font_size=row_font),
        Row(label="EMA200", value=fmt_value(p.ema200), font_size=row_font),
    ]
    if p.rsi14 is not None:
        indicator_rows.append(Row(label=t("rsi", language), value=fmt_value(p.rsi14, 1),
                                  font_size=row_font))
    if p.trend_strength is not None:
        indicator_rows.append(Row(label=t("trend_strength", language),
                                  value=fmt_value(p.trend_strength, 0),
                                  font_size=row_font))
    # The primary card caps this; Extended shows the complete table.
    if size != "INFOGRAPHIC_EXTENDED":
        indicator_rows = indicator_rows[:4]

    scenario_rows = _scenario_rows(p, language, full=extended, font_size=row_font)
    pullback_rows = _pullback_rows(p, language, full=extended, font_size=row_font)
    summary_lines = _summary_lines(p, language)

    # Two-up: support beside resistance.
    y = _render_pair(canvas, left, right, y, content,
                     Panel(title=t("support", language), children=tuple(support_rows),
                           accent=PALETTE["green"]),
                     Panel(title=t("resistance", language),
                           children=tuple(resistance_rows), accent=PALETTE["amber"]))

    y = _render_pair(canvas, left, right, y, content,
                     Panel(title=t("indicators", language),
                           children=tuple(indicator_rows), accent=PALETTE["blue"]),
                     Panel(title=t("scenarios", language), children=tuple(scenario_rows),
                           accent=PALETTE["blue"]))

    footer_h = int(110 * s)
    footer_y = height - margin - footer_h
    pullback_panel = Panel(
        title=t("pullback", language),
        title_badge=t("research_badge", language),
        children=(RowGrid(rows=tuple(pullback_rows)),),
        accent=PALETTE["purple"], frame_colour=tint(PALETTE["purple"], 0.55))
    need = pullback_panel.measure(canvas, content)
    pullback_panel.render(canvas, (left, y, right, y + need))
    # A narrow visual gutter leaves the compact summary/evidence strip enough
    # room on the fixed 1920 canvas without shrinking text below its floor.
    y += need + SPACING["xs"]

    evidence_panel = Panel(
        title=t("evidence", language), compact=True,
        children=(BulletGrid(
            items=localize_assessment_reasons(p.assessment_evidence, language),
            max_items=4, font_size=18),), accent=PALETTE["gray"])
    summary_panel = Panel(
        title=t("summary", language), compact=True,
        children=(BulletList(items=summary_lines, max_items=3,
                             max_lines_per_item=None, font_size=18),),
        accent=PALETTE["blue"])
    each = (content - SPACING["md"]) // 2
    final_pair_need = max(evidence_panel.measure(canvas, each),
                          summary_panel.measure(canvas, each))
    if y + final_pair_need <= footer_y:
        y = _render_pair(canvas, left, right, y, content,
                         evidence_panel, summary_panel)

    if extended and y < footer_y - int(120 * s):
        # Provenance notes, drawn from fields the model already carries.
        note_size = canvas.type(TypeScale.caption)
        canvas.text_rtl(right, y, _label_of("مصدر البيانات والمنهجية",
                                            "Source & Method", language),
                        canvas.type(TypeScale.title), PALETTE["gray"], bold=True)
        y += canvas.line_height(canvas.type(TypeScale.title))
        for note in _methodology_lines(p, language):
            if y + canvas.line_height(note_size) > footer_y - int(60 * s):
                break
            for line in canvas.wrap(f"• {note}", note_size, content, max_lines=1):
                canvas.text_rtl(right, y, line, note_size, PALETTE["muted"])
            y += canvas.line_height(note_size)

    # ---------------------------------------------------------- research --
    _render_footer(canvas, left, right, footer_y, footer_h, p, language)
    return canvas.to_png()


def _render_pair(canvas: Canvas, left: int, right: int, y: int, content: int,
                 first: Panel, second: Panel) -> int:
    """Two panels side by side, both sized to the taller one."""

    gutter = SPACING["md"]
    each = (content - gutter) // 2
    need = max(first.measure(canvas, each), second.measure(canvas, each))
    first.render(canvas, (right - each, y, right, y + need))
    second.render(canvas, (left, y, left + each, y + need))
    return y + need + SPACING["md"]


def _draw_price_panel(canvas: Canvas, box, p: AnalysisPresentation, language: str):
    """Candles + volume from the SAME window selector the page chart uses."""

    x0, y0, x1, y1 = box
    canvas.panel(box, fill=PALETTE["surface"])
    canvas.text_rtl(x1 - PANEL_PAD, y0 + SPACING["sm"],
                    f"{t('chart', language)} · {CHART_SESSIONS} "
                    + ("جلسة مكتملة" if language != "EN" else "completed sessions"),
                    canvas.type(TypeScale.caption), PALETTE["muted"])

    candles = [c for c in p.candles[-CHART_SESSIONS:] if c.close is not None]
    if len(candles) < 2:
        canvas.text_centre((x0 + x1) // 2, (y0 + y1) // 2, EM_DASH,
                           canvas.type(TypeScale.body), PALETTE["muted"])
        return

    pad = PANEL_PAD
    head = SPACING["sm"] + canvas.line_height(canvas.type(TypeScale.caption))
    price_top = y0 + head + SPACING["sm"]
    volume_h = int((y1 - price_top - pad) * 0.22)
    price_bottom = y1 - pad - volume_h - SPACING["xs"]

    highs = [c.high for c in candles if c.high is not None]
    lows = [c.low for c in candles if c.low is not None]
    levels = [lv.price for lv in p.levels if lv.present
              and lv.key in ("support_1", "resistance_1", "breakout", "invalidation")]
    low = min(lows + levels) if lows else 0.0
    high = max(highs + levels) if highs else 1.0
    span = (high - low) or 1.0

    def y_of(value):
        return price_bottom - (float(value) - low) / span * (price_bottom - price_top)

    plot_left, plot_right = x0 + pad, x1 - pad - int(96 * canvas.scale)
    step = (plot_right - plot_left) / max(1, len(candles) - 1)
    body = max(1.5, step * 0.6)

    # levels behind the candles
    # Only the decision-relevant levels are drawn; the rest live in the panels.
    # Labels are placed by the SAME deterministic solver the page chart uses, so
    # the two surfaces cannot space nearby prices differently.
    drawn = [(key, role, p.level(key)) for key, role in
             (("support_1", "support"), ("resistance_1", "resistance"),
              ("breakout", "breakout"), ("invalidation", "invalidation"))
             if p.level(key) is not None and p.level(key).present]
    annotated = [(key, level.price) for key, _role, level in drawn]
    if p.close is not None:
        annotated.append((LAST_PRICE_KEY, p.close))
    label_px = canvas.type(16) * 1.35
    lanes = resolve_label_lanes(
        annotated, min_gap=span * (label_px / max(60.0, price_bottom - price_top)))

    for key, role, level in drawn:
        ly = y_of(level.price)                       # the LINE stays on its price
        canvas.draw.line((plot_left, ly, plot_right, ly),
                         fill=tint(ROLE_COLOUR[role], 0.75), width=2)
        canvas.text_ltr(plot_right + SPACING["xs"],
                        y_of(lanes.get(key, level.price)) - int(9 * canvas.scale),
                        level.display, canvas.type(16), ROLE_COLOUR[role], bold=True)

    pb = p.pullback
    if pb.support_zone_low is not None and pb.support_zone_high is not None:
        canvas.draw.rectangle((plot_left, y_of(pb.support_zone_high),
                               plot_right, y_of(pb.support_zone_low)),
                              fill=tint(PALETTE["purple"], 0.18))

    for index, candle in enumerate(candles):
        cx = plot_left + index * step
        up = (candle.close or 0) >= (candle.open or 0)
        colour = PALETTE["green"] if up else PALETTE["red"]
        if candle.high is not None and candle.low is not None:
            canvas.draw.line((cx, y_of(candle.high), cx, y_of(candle.low)),
                             fill=colour, width=1)
        if candle.open is not None and candle.close is not None:
            top, bottom = sorted((y_of(candle.open), y_of(candle.close)))
            canvas.draw.rectangle((cx - body / 2, top, cx + body / 2,
                                   max(bottom, top + 1)), fill=colour)

    volumes = [c.volume or 0 for c in candles]
    peak = max(volumes) or 1
    for index, candle in enumerate(candles):
        cx = plot_left + index * step
        bar = (candle.volume or 0) / peak * volume_h
        up = (candle.close or 0) >= (candle.open or 0)
        canvas.draw.rectangle((cx - body / 2, y1 - pad - bar, cx + body / 2, y1 - pad),
                              fill=tint(PALETTE["green"] if up else PALETTE["red"], 0.55))

    if p.close is not None:
        cy = y_of(p.close)
        canvas.draw.ellipse((plot_right - 5, cy - 5, plot_right + 5, cy + 5),
                            fill=PALETTE["text"])
        canvas.text_ltr(plot_right + SPACING["xs"],
                        y_of(lanes.get(LAST_PRICE_KEY, p.close))
                        - int(9 * canvas.scale), fmt_value(p.close),
                        canvas.type(16), PALETTE["text"], bold=True)


def _render_footer(canvas: Canvas, left: int, right: int, y: int, height: int,
                   p: AnalysisPresentation, language: str):
    canvas.divider(left, right, y)
    caption = canvas.type(TypeScale.caption)
    canvas.text_rtl(right, y + SPACING["sm"],
                    f"EODHD · {t('session', language)} {p.last_completed_session}",
                    caption, PALETTE["muted"])
    badge_y = y + SPACING["sm"] + canvas.line_height(caption)
    edge = canvas.badge(right, badge_y, t("decision_only", language), canvas.type(17),
                        PALETTE["blue"])
    edge = canvas.badge(edge - SPACING["sm"], badge_y, t("paper", language),
                        canvas.type(17), PALETTE["amber"])
    canvas.badge(edge - SPACING["sm"], badge_y, t("production", language),
                 canvas.type(17), PALETTE["gray"])
    canvas.text_rtl(right, badge_y + canvas.badge_height(canvas.type(17)) + SPACING["sm"],
                    t("disclaimer", language), canvas.type(16), PALETTE["muted"])


__all__ = [
    "CHART_SESSIONS", "DEFAULT_INFOGRAPHIC_SIZE", "DEFAULT_LANGUAGE",
    "INFOGRAPHIC_SIZES", "INFOGRAPHIC_SIZE_LABELS", "LANGUAGES", "LANGUAGE_LABELS",
    "SECTION_KEYS", "render_infographic_png", "t",
]
