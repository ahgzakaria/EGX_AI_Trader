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

from core.ai_pullback_labels import localize, localize_assessment_reasons
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
    BulletList,
    Canvas,
    Panel,
    Row,
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
    "pullback":      ("تحليل جودة التصحيح — بحثي فقط", "Pullback Health — Research Only"),
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
    "waiting":       ("سيناريو الانتظار", "Waiting Range Restatement"),
    "negative":      ("سيناريو سلبي", "Negative"),
    "research_only": ("بحثي فقط — غير معتمد كإشارة دخول",
                      "Research Only — not an entry signal"),
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


def _level_rows(p: AnalysisPresentation, keys, language: str, role: str):
    rows = []
    for key in keys:
        level = p.level(key)
        if level is None or not level.present:
            continue
        rows.append(Row(label=t(key, language), value=level.display,
                        value_colour=ROLE_COLOUR[role if key.startswith(("support",))
                                                 else role]))
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


def _scenario_rows(p: AnalysisPresentation, language: str, *, full: bool = False):
    """Two real scenarios plus a clearly-labelled range RESTATEMENT."""

    rows = []
    positive = next((s for s in p.scenarios if "breakout" in s.scenario_id), None)
    negative = next((s for s in p.scenarios if "breakdown" in s.scenario_id), None)

    if positive is not None:
        rows.append(Row(label=t("positive", language),
                        value=f"{fmt_value(positive.trigger)} → {fmt_value(positive.target)}",
                        value_colour=PALETTE["green"]))
    support_1 = p.level_price("support_1")
    resistance_1 = p.level_price("resistance_1")
    if support_1 is not None and resistance_1 is not None:
        # A RESTATEMENT of two levels the Core already computed — not a strategy.
        rows.append(Row(label=t("waiting", language),
                        value=f"{fmt_value(support_1)} – {fmt_value(resistance_1)}",
                        value_colour=PALETTE["blue"]))
    if negative is not None:
        rows.append(Row(label=t("negative", language),
                        value=f"{fmt_value(negative.trigger)} ↓",
                        value_colour=PALETTE["red"]))
    if full:
        # Activation and invalidation levels for each real scenario.
        # Localized side labels — the raw scenario_id is English and must not
        # appear on an Arabic card.
        side = {"breakout": ("إبطال السيناريو الإيجابي", "Positive invalidation"),
                "breakdown": ("إبطال السيناريو السلبي", "Negative invalidation")}
        for scenario in p.scenarios[:2]:
            key = "breakout" if "breakout" in scenario.scenario_id else "breakdown"
            if scenario.stop is not None:
                rows.append(Row(label=_label_of(*side[key], language),
                                value=fmt_value(scenario.stop),
                                value_colour=PALETTE["red"]))
            if scenario.risk_reward is not None:
                rows.append(Row(
                    label=_label_of("العائد إلى المخاطرة", "Reward / risk", language),
                    value=fmt_value(scenario.risk_reward, 2)))
    return rows


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


def _pullback_rows(p: AnalysisPresentation, language: str, *, full: bool = False):
    pb = p.pullback
    rows = [Row(label=t("assessment", language),
                value=pb.state_ar if language != "EN" else pb.state_en,
                value_colour=PALETTE["purple"])]
    if pb.pullback_percent is not None:
        rows.append(Row(label="نسبة التصحيح" if language != "EN" else "Pullback %",
                        value=fmt_value(pb.pullback_percent, 2, "%")))
    if pb.pullback_atr is not None:
        rows.append(Row(label="عمق ATR" if language != "EN" else "ATR depth",
                        value=fmt_value(pb.pullback_atr, 2, " ATR")))
    if pb.support_zone_low is not None:
        rows.append(Row(label=t("pullback_zone", language),
                        value=pb.support_zone_display, value_colour=PALETTE["purple"]))
    if pb.volume_behaviour != EM_DASH:
        rows.append(Row(label="حجم التصحيح" if language != "EN" else "Correction volume",
                        value=_first_clause(pb.volume_behaviour, language)))
    if pb.invalidation_reason not in (EM_DASH, ""):
        rows.append(Row(label="سبب عدم القابلية" if language != "EN" else "Reason",
                        value=pb.invalidation_reason))
    if full:
        # Complete diagnostics — Extended only. Every value copied from the model.
        for label_ar, label_en, value in (
                ("جودة الاتجاه السابق", "Prior trend",
                 _first_clause(pb.prior_trend, language)),
                ("الهيكل السعري", "Structure", _first_clause(pb.structure, language)),
                ("آخر قمة", "Swing high", fmt_value(pb.swing_high)),
                ("قاع الموجة", "Impulse low", fmt_value(pb.impulse_low)),
                ("ارتداد الموجة", "Impulse retracement",
                 fmt_value(pb.impulse_retracement_percent, 2, "%")),
                ("تداخل الدعم", "Support confluence", pb.confluence),
                ("دليل الارتداد", "Reversal evidence",
                 _first_clause(pb.reversal_evidence, language))):
            if value not in (EM_DASH, "", None):
                rows.append(Row(label=_label_of(label_ar, label_en, language),
                                value=str(value)))
    # Extended shows the complete diagnostics, but still bounded so the
    # evidence and summary panels below it keep their place.
    return rows[:8] if full else rows[:4]


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
    for line in canvas.wrap(p.company_name or EM_DASH, canvas.type(TypeScale.caption),
                            ticker_right - mid - int(10 * s), max_lines=2):
        canvas.text_rtl(ticker_right, name_y, line, canvas.type(TypeScale.caption),
                        PALETTE["muted"])
        name_y += canvas.line_height(canvas.type(TypeScale.caption))

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
    canvas.badge(left + PANEL_PAD + badge_w, badge_y, rec, label_size,
                 _tone_colour(p.recommendation_tone), solid=True)
    canvas.text_ltr(left + PANEL_PAD + badge_w + SPACING["md"],
                    badge_y + int(8 * s),
                    f"{t('confidence', language)} {p.confidence_display}",
                    canvas.type(TypeScale.caption), PALETTE["muted"])
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
    sections = []
    technical = _technical_lines(p, language)
    if technical:
        sections.append(("technical_read",
                         Panel(title=t("technical", language),
                               children=(BulletList(items=technical),),
                               accent=PALETTE["blue"]), 1.0))

    support_rows = _level_rows(p, ("support_1", "support_2"), language, "support")
    if p.pullback.support_zone_low is not None:
        support_rows.append(Row(label=t("pullback_zone", language),
                                value=p.pullback.support_zone_display,
                                value_colour=PALETTE["purple"]))
    resistance_rows = _level_rows(p, ("resistance_1", "resistance_2", "breakout",
                                      "invalidation"), language, "resistance")
    for row in resistance_rows:
        if row.label == t("breakout", language):
            row.value_colour = ROLE_COLOUR["breakout"]
        if row.label == t("invalidation", language):
            row.value_colour = ROLE_COLOUR["invalidation"]

    indicator_rows = [
        Row(label=t("trend", language),
            value=p.trend_ar if language != "EN" else p.trend_en),
        Row(label=t("momentum", language),
            value=p.momentum_ar if language != "EN" else p.momentum_en),
        Row(label="EMA20", value=fmt_value(p.ema20)),
        Row(label="EMA50", value=fmt_value(p.ema50)),
        Row(label="EMA200", value=fmt_value(p.ema200)),
    ]
    if p.rsi14 is not None:
        indicator_rows.append(Row(label=t("rsi", language), value=fmt_value(p.rsi14, 1)))
    if p.trend_strength is not None:
        indicator_rows.append(Row(label=t("trend_strength", language),
                                  value=fmt_value(p.trend_strength, 0)))
    # The primary card caps this; Extended shows the complete table.
    if size != "INFOGRAPHIC_EXTENDED":
        indicator_rows = indicator_rows[:5]

    extended = size == "INFOGRAPHIC_EXTENDED"
    scenario_rows = _scenario_rows(p, language, full=extended)
    pullback_rows = _pullback_rows(p, language, full=extended)

    summary_text = (p.summary_ar if language == "AR"
                    else p.summary_en if language == "EN"
                    else (p.summary_ar or p.summary_en))
    if language == "EN" and not p.summary_en:
        summary_text = t("no_english", "EN")

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
    # Scenarios beside Pullback, then Evidence beside Summary. Pairing halves the
    # height these four need, which is what lets all twelve sections fit 1920.
    pairs = [
        (Panel(title=t("pullback", language), children=tuple(pullback_rows),
               accent=PALETTE["purple"], frame_colour=tint(PALETTE["purple"], 0.55)),
         Panel(title=t("technical", language),
               children=(BulletList(items=technical, max_items=3),),
               accent=PALETTE["blue"])),
        (Panel(title=t("evidence", language),
               children=(BulletList(
                   items=localize_assessment_reasons(p.assessment_evidence,
                                                     language),
                   max_items=4, max_lines_per_item=1),),
               accent=PALETTE["gray"]),
         Panel(title=t("summary", language),
               children=(BulletList(items=(summary_text,) if summary_text else (),
                                    max_items=3 if not extended else 6),),
               accent=PALETTE["blue"])),
    ]
    each = (content - SPACING["md"]) // 2
    stamp_limit = footer_y - canvas.badge_height(canvas.type(18)) - SPACING["lg"]
    for first, second in pairs:
        need = max(first.measure(canvas, each), second.measure(canvas, each))
        if y + need > stamp_limit:
            break
        y = _render_pair(canvas, left, right, y, content, first, second)

    # The research-only stamp is never optional. It sits just above the footer so
    # it is present whatever the section budget dropped.
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

    stamp_y = footer_y - canvas.badge_height(canvas.type(18)) - SPACING["sm"]
    canvas.badge(right, stamp_y, t("research_only", language), canvas.type(18),
                 PALETTE["purple"])

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
