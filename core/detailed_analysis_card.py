"""The tall Detailed Analysis Card.

Renders an :class:`~core.analysis_presentation.AnalysisPresentation` — and only
that — into a portrait PNG with nine sections. The compact card is unchanged and
still available; this is an additional export format for readers who want the
full evidence rather than a summary.

The renderer never recalculates anything. Every figure it draws is copied from
the presentation model, so the page, the compact card, this card and the chart
cannot disagree. A missing value stays an em dash and is never shown as ``0``.

Layout is measure-then-draw with a shared vertical cursor: each section reports
the height it needs, sections are drawn in order, and the footer is pinned to the
bottom, so no section can overrun another and no large empty band is left behind.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

from core.analysis_card_generator import (
    PALETTE,
    _Canvas,
    _Fonts,
    _identity_mark,
    _tint,
    _tone_colour,
    resolve_font_family,
)
from core.analysis_presentation import (
    EM_DASH,
    AnalysisPresentation,
    fmt_compact,
    fmt_signed,
    fmt_value,
)

#: Portrait formats. Both are exact; nothing is scaled or cropped afterwards.
DETAILED_CARD_SIZES = {
    "DETAILED": (1080, 1920),
    "DETAILED_HD": (1350, 2400),
}
DEFAULT_DETAILED_SIZE = "DETAILED"

#: Language modes. ``BILINGUAL`` renders the Arabic label with a smaller English
#: sub-label, which is how the page itself reads.
LANGUAGES = ("AR", "EN", "BILINGUAL")
DEFAULT_LANGUAGE = "BILINGUAL"

LANGUAGE_LABELS = {
    "AR": "العربية",
    "EN": "English",
    "BILINGUAL": "ثنائي اللغة · Bilingual",
}

DETAILED_SIZE_LABELS = {
    "DETAILED": "تفصيلية 1080×1920 · Detailed 1080×1920",
    "DETAILED_HD": "تفصيلية عالية الدقة 1350×2400 · Detailed HD 1350×2400",
}

#: Section identity, in draw order. Used by the renderer AND asserted by tests.
SECTION_KEYS = ("header", "trend", "price_volume", "pullback", "levels",
                "scenarios", "risk", "summary", "footer")

SECTION_TITLES = {
    "header":       ("بطاقة التحليل", "Analysis Card"),
    "trend":        ("الاتجاه", "Trend"),
    "price_volume": ("السعر وحجم التداول", "Price and Volume"),
    "pullback":     ("جودة التصحيح", "Pullback Health"),
    "levels":       ("المستويات", "Levels"),
    "scenarios":    ("السيناريوهات", "Scenarios"),
    "risk":         ("المخاطر", "Risk"),
    "summary":      ("الخلاصة", "Summary"),
    "footer":       ("المصدر", "Source"),
}


@dataclass(frozen=True)
class _Theme:
    """Type sizes scaled to the card width — never below a readable floor."""

    scale: float

    def size(self, base: int) -> int:
        # 15px at 1080 is the smallest type on the card; nothing goes under it.
        return max(15, int(round(base * self.scale)))


def _label(key_ar: str, key_en: str, language: str) -> str:
    if language == "AR":
        return key_ar
    if language == "EN":
        return key_en
    return f"{key_ar} · {key_en}"


def _rows_for(presentation: AnalysisPresentation, section: str, language: str):
    """The (label, value) rows a section shows. Pure lookup on the model."""

    p = presentation
    L = lambda ar, en: _label(ar, en, language)          # noqa: E731

    if section == "trend":
        return [
            (L("التصنيف", "Classification"),
             p.trend_ar if language != "EN" else p.trend_en),
            (L("قوة الاتجاه", "Trend Strength"), fmt_value(p.trend_strength, 0)),
            (L("الزخم", "Momentum"),
             p.momentum_ar if language != "EN" else p.momentum_en),
            (L("EMA20", "EMA20"), fmt_value(p.ema20)),
            (L("EMA50", "EMA50"), fmt_value(p.ema50)),
            (L("EMA200", "EMA200"), fmt_value(p.ema200)),
            (L("ترتيب المتوسطات", "EMA Alignment"), p.ema_alignment_ar),
        ]
    if section == "price_volume":
        return [
            (L("النطاق الأخير", "Recent Range"),
             f"{fmt_value(p.recent_low)} – {fmt_value(p.recent_high)}"),
            (L("حجم التداول", "Volume"), fmt_compact(p.volume)),
            (L("متوسط الحجم", "Average Volume"), fmt_compact(p.average_volume)),
            (L("الحجم مقابل المتوسط", "Volume vs Average"),
             fmt_value(p.volume_ratio, 2, suffix="x")),
            (L("التذبذب ATR", "ATR"), fmt_value(p.atr)),
            (L("ATR كنسبة", "ATR %"), fmt_value(p.atr_percent, 2, suffix="%")),
        ]
    if section == "pullback":
        pb = p.pullback
        return [
            (L("الحالة", "State"), pb.state_ar if language != "EN" else pb.state_en),
            (L("جودة الاتجاه السابق", "Prior Trend"), pb.prior_trend),
            (L("آخر قمة", "Swing High"), fmt_value(pb.swing_high)),
            (L("قاع الموجة", "Impulse Low"), fmt_value(pb.impulse_low)),
            (L("نسبة التصحيح", "Pullback %"),
             fmt_value(pb.pullback_percent, 2, suffix="%")),
            (L("عمق ATR", "Pullback ATR"), fmt_value(pb.pullback_atr, 2, suffix=" ATR")),
            (L("ارتداد الموجة", "Impulse Retracement"),
             fmt_value(pb.impulse_retracement_percent, 2, suffix="%")),
            (L("منطقة الدعم", "Support Zone"), pb.support_zone_display),
            (L("تداخل الدعم", "Support Confluence"), pb.confluence),
            (L("حجم التصحيح", "Correction Volume"), pb.volume_behaviour),
            (L("دليل الارتداد", "Reversal Evidence"), pb.reversal_evidence),
        ]
    if section == "levels":
        rows = []
        for item in p.levels:
            if item.key in ("research_trigger",):
                continue
            rows.append((_label(item.label_ar, item.label_en, language), item.display))
        return rows
    if section == "scenarios":
        rows = []
        for scenario in p.scenarios[:2]:
            rows.append((_label(scenario.title, scenario.scenario_id, language),
                         scenario.state_ar if language != "EN" else scenario.state_en))
            rows.append((L("التفعيل", "Trigger"), fmt_value(scenario.trigger)))
            rows.append((L("الهدف", "Target"), fmt_value(scenario.target)))
            rows.append((L("الإبطال", "Invalidation"), fmt_value(scenario.stop)))
        pb = p.pullback
        rows.append((L("تشخيص التصحيح", "Pullback Diagnostic"),
                     pb.state_ar if language != "EN" else pb.state_en))
        return rows
    if section == "risk":
        return [
            (L("المساحة الصاعدة", "Upside"),
             fmt_value(p.upside_percent, 2, suffix="%")),
            (L("المسافة إلى الإبطال", "Downside to Invalidation"),
             fmt_value(p.downside_percent, 2, suffix="%")),
            (L("العائد إلى المخاطرة", "Diagnostic R/R"),
             fmt_value(p.diagnostic_risk_reward, 2)),
        ]
    return []


def _summary_lines(presentation: AnalysisPresentation, language: str):
    p = presentation
    blocks = []
    conclusion = (p.summary_ar if language != "EN" else p.summary_en) or \
        (p.pullback.explanation_ar if language != "EN" else p.pullback.explanation_en)
    if conclusion:
        blocks.append((_label("الخلاصة", "Conclusion", language), [conclusion]))
    # recommendation_reasons is UNSIGNED, so it is never labelled "positive".
    if p.assessment_evidence:
        blocks.append((_label("أسباب التقييم", "Assessment Evidence", language),
                       list(p.assessment_evidence[:4])))
    if p.watch_next:
        blocks.append((_label("ما يجب متابعته", "Watch Next", language),
                       list(p.watch_next[:3])))
    return blocks


def _draw_sparkline(canvas: _Canvas, box, presentation: AnalysisPresentation):
    """A compact recent-price line drawn from the SAME candles the chart uses."""

    x0, y0, x1, y1 = box
    canvas.panel(box, fill=PALETTE["surface_2"])
    candles = [c for c in presentation.candles[-60:] if c.close is not None]
    if len(candles) < 2:
        canvas.text_centre((x0 + x1) // 2, y0 + (y1 - y0) // 2 - 10,
                           EM_DASH, 26, PALETTE["muted"])
        return
    closes = [float(c.close) for c in candles]
    low, high = min(closes), max(closes)
    span = (high - low) or 1.0
    pad = 16
    step = (x1 - x0 - pad * 2) / (len(closes) - 1)
    points = [(x0 + pad + i * step,
               y1 - pad - (value - low) / span * (y1 - y0 - pad * 2))
              for i, value in enumerate(closes)]
    canvas.draw.line(points, fill=PALETTE["blue"], width=3, joint="curve")
    last_x, last_y = points[-1]
    canvas.draw.ellipse((last_x - 6, last_y - 6, last_x + 6, last_y + 6),
                        fill=PALETTE["green"])


def render_detailed_card_png(presentation: AnalysisPresentation, *,
                             size: str = DEFAULT_DETAILED_SIZE,
                             language: str = DEFAULT_LANGUAGE,
                             font_path: str | None = None) -> bytes:
    """Render the detailed card. Consumes the presentation model only."""

    if size not in DETAILED_CARD_SIZES:
        raise ValueError(
            f"unknown detailed size {size!r}; expected one of {sorted(DETAILED_CARD_SIZES)}")
    if language not in LANGUAGES:
        raise ValueError(f"unknown language {language!r}; expected one of {LANGUAGES}")

    width, height = DETAILED_CARD_SIZES[size]
    theme = _Theme(scale=width / 1080.0)
    regular, bold = resolve_font_family(font_path)
    canvas = _Canvas(Image.new("RGB", (width, height), PALETTE["bg"]),
                     _Fonts(regular, bold))

    margin = int(44 * theme.scale)
    right, left = width - margin, margin
    content = right - left
    p = presentation

    for row in range(int(230 * theme.scale)):
        limit = int(230 * theme.scale)
        canvas.draw.line((0, row, width, row),
                         fill=_tint(PALETTE["surface_2"], (1.0 - row / limit) * 0.9))

    y = margin

    # ------------------------------------------------------------- header --
    _identity_mark(canvas, right, y, int(58 * theme.scale))
    canvas.text_rtl(right - int(74 * theme.scale), y + int(4 * theme.scale),
                    p.ticker, theme.size(46), PALETTE["text"], bold=True)
    y += int(70 * theme.scale)
    # The full company name wraps rather than being cut off.
    for line in canvas.wrap_rtl(p.company_name or EM_DASH, theme.size(24), content, 2):
        canvas.text_rtl(right, y, line, theme.size(24), PALETTE["muted"])
        y += int(theme.size(24) * 1.45)
    y += int(6 * theme.scale)

    recommendation = p.recommendation_ar if language != "EN" else p.recommendation_en
    canvas.pill_rtl(right, y, recommendation, theme.size(24),
                    tone=p.recommendation_tone, solid=True)
    canvas.text_ltr(left, y + int(6 * theme.scale),
                    f"{_label('الثقة', 'Confidence', language)}: {p.confidence_display}",
                    theme.size(22), PALETTE["muted"])
    y += int(56 * theme.scale)

    head_rows = [
        (_label("تاريخ التحليل", "Analysis Date", language), p.analysis_date),
        (_label("آخر جلسة مكتملة", "Last Completed Session", language),
         p.last_completed_session),
        (_label("آخر إغلاق", "Last Close", language),
         f"{fmt_value(p.close)} {p.currency}"),
        (_label("التغير", "Change", language), p.change_display),
        (_label("المزود", "Provider", language), str(p.provider).upper()),
    ]
    y = _draw_rows(canvas, theme, left, right, y, head_rows, content)
    y += int(14 * theme.scale)

    # ------------------------------------------------------- body sections --
    # Measure first: the footer is pinned, so the body gets a hard budget and the
    # summary is what absorbs whatever space is left. No section can overrun.
    footer_h = int(96 * theme.scale)
    footer_y = height - margin - footer_h
    body_budget = footer_y - int(24 * theme.scale) - y

    body = ("trend", "price_volume", "pullback", "levels", "scenarios", "risk")
    title_h = int(theme.size(24) * 1.55)
    chart_h = int(118 * theme.scale)
    gap = int(9 * theme.scale)
    band = _row_height(theme)

    planned = []
    fixed = 0
    for key in body:
        rows = _rows_for(p, key, language)
        if not rows:
            continue
        height_needed = title_h + -(-len(rows) // 2) * band + gap
        if key == "price_volume":
            height_needed += chart_h + int(12 * theme.scale)
        if key == "pullback":
            height_needed += int(theme.size(19) * 2.1) + int(10 * theme.scale)
        planned.append((key, rows, height_needed))
        fixed += height_needed

    summary_room = max(0, body_budget - fixed)

    for key, rows, _needed in planned:
        title_ar, title_en = SECTION_TITLES[key]
        y = _section_title(canvas, theme, right, y, _label(title_ar, title_en, language))
        if key == "price_volume":
            _draw_sparkline(canvas, (left, y, right, y + chart_h), p)
            y += chart_h + int(12 * theme.scale)
        y = _draw_rows(canvas, theme, left, right, y, rows, content)
        if key == "pullback":
            y = _research_only(canvas, theme, left, right, y, language)
        y += gap

    # ------------------------------------------------------------ summary --
    blocks = _summary_lines(p, language)
    if blocks and summary_room > title_h + band:
        limit = y + summary_room
        title_ar, title_en = SECTION_TITLES["summary"]
        y = _section_title(canvas, theme, right, y, _label(title_ar, title_en, language))
        heading_step = int(theme.size(20) * 1.5)
        line_step = int(theme.size(19) * 1.45)
        for heading, items in blocks:
            if y + heading_step + line_step > limit:
                break
            canvas.text_rtl(right, y, heading, theme.size(20), PALETTE["blue"], bold=True)
            y += heading_step
            for item in items:
                remaining = max(1, int((limit - y) // line_step))
                if remaining < 1:
                    break
                for line in canvas.wrap_rtl(f"• {item}", theme.size(19), content,
                                            min(2, remaining)):
                    if y + line_step > limit:
                        break
                    canvas.text_rtl(right, y, line, theme.size(19), PALETTE["text"])
                    y += line_step
            y += int(6 * theme.scale)

    # ------------------------------------------------------------- footer --
    canvas.rule(left, right, footer_y - int(14 * theme.scale))
    canvas.text_rtl(right, footer_y,
                    _label(f"المصدر: EODHD · جلسة {p.last_completed_session}",
                           f"Source: EODHD · session {p.last_completed_session}", language),
                    theme.size(20), PALETTE["muted"])
    badge_y = footer_y + int(34 * theme.scale)
    edge = canvas.pill_rtl(right, badge_y,
                           _label("دعم قرار / بحثي فقط", "Decision Support / Research Only",
                                  language), theme.size(18), tone="blue")
    canvas.pill_rtl(edge - int(12 * theme.scale), badge_y,
                    _label("التنفيذ غير مفعّل", "Production Disabled", language),
                    theme.size(18), tone="gray")

    buffer = io.BytesIO()
    canvas.image.save(buffer, format="PNG")
    return buffer.getvalue()


def _section_title(canvas: _Canvas, theme: _Theme, right: int, y: int, text: str) -> int:
    canvas.text_rtl(right, y, text, theme.size(24), PALETTE["text"], bold=True)
    return y + int(theme.size(24) * 1.55)


def _row_height(theme: _Theme) -> int:
    """Every row is one fixed band, so a section's height is exactly predictable."""

    return int(theme.size(22) * 1.62)


def _draw_rows(canvas: _Canvas, theme: _Theme, left: int, right: int, y: int,
               rows, content: int, *, columns: int = 2) -> int:
    """Label right, value left. Dense sections use two columns so the card fits.

    Each row occupies one fixed-height band and its label is ellipsised to the
    space actually left by the value, so a long label can never collide with the
    number beside it or bleed into the next section.
    """

    if not rows:
        return y
    label_size, value_size = theme.size(20), theme.size(21)
    band = _row_height(theme)
    gutter = int(26 * theme.scale)
    column_width = (content - gutter * (columns - 1)) // columns
    per_column = -(-len(rows) // columns)          # ceil

    for index, (label, value) in enumerate(rows):
        column = index // per_column
        row = index % per_column
        column_right = right - column * (column_width + gutter)
        column_left = column_right - column_width
        row_y = y + row * band

        value_text = EM_DASH if value in (None, "") else str(value)
        wide = len(value_text) > 12
        value_text = canvas.fit_rtl(value_text, value_size,
                                    int(column_width * (0.80 if wide else 0.62)))
        value_width = canvas.width_of(value_text, value_size, bold=True)
        label_space = int(column_width - value_width - 14 * theme.scale)
        canvas.text_ltr(column_left, row_y, value_text, value_size,
                        PALETTE["text"], bold=True)
        if label_space > 40:
            canvas.text_rtl(column_right, row_y + int(2 * theme.scale),
                            canvas.fit_rtl(str(label), label_size, label_space),
                            label_size, PALETTE["muted"])
    return y + per_column * band


def _research_only(canvas: _Canvas, theme: _Theme, left: int, right: int, y: int,
                   language: str) -> int:
    text = _label("بحثي فقط — ليست إشارة دخول",
                  "Research Only — not an entry signal", language)
    height = int(theme.size(19) * 2.1)
    canvas.draw.rounded_rectangle((left, y, right, y + height), radius=10,
                                  fill=_tint(PALETTE["amber"], 0.18),
                                  outline=_tint(PALETTE["amber"], 0.55), width=2)
    canvas.text_rtl(right - int(14 * theme.scale), y + int(height * 0.24), text,
                    theme.size(19), _tone_colour("amber"), bold=True)
    return y + height + int(10 * theme.scale)


__all__ = [
    "DEFAULT_DETAILED_SIZE", "DEFAULT_LANGUAGE", "DETAILED_CARD_SIZES",
    "DETAILED_SIZE_LABELS", "LANGUAGES", "LANGUAGE_LABELS", "SECTION_KEYS",
    "SECTION_TITLES", "render_detailed_card_png",
]
