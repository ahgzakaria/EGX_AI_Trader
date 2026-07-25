"""Tests for the exportable Arabic PNG analysis card.

Covers the self-contained Arabic text engine (joining forms + bidi reordering), the system
font discovery, PNG generation and dimensions, and the mandatory safety labelling.
"""

from __future__ import annotations

import io
from dataclasses import replace

import pytest
from PIL import Image

from core.ai_stock_analysis_contract import CardPayload, MarketPhase
from core.analysis_card_generator import (
    ARABIC_FONT_CANDIDATES,
    CARD_SIZES,
    DEFAULT_CARD_SIZE,
    CardChartData,
    FontUnavailableError,
    bidi_reorder,
    display_text,
    narrative_source_label,
    render_card_png,
    resolve_font_family,
    shape_arabic,
)
from dashboard.ai_stock_analysis_components import (
    build_card_chart,
    build_card_payload,
    fixture_analysis,
    generate_card_bytes,
)

# Presentation-form code points used by the expectations below.
ALEF_ISOLATED = "\ufe8d"
LAM_ALEF_ISOLATED = "\ufefb"          # ﻻ
LAM_ALEF_HAMZA_BELOW_FINAL = "\ufefa"  # ﻺ (لإ in final position)


@pytest.fixture
def bundle():
    return fixture_analysis("COMI")


# --------------------------------------------------------------------------- #
# Arabic shaping
# --------------------------------------------------------------------------- #

def test_shaping_picks_contextual_forms():
    shaped = shape_arabic("تحليل")
    assert shaped == "\ufe97\ufea4\ufee0\ufef4\ufede"
    # initial · medial · medial · medial · final — no isolated form in the middle
    assert len(shaped) == 5


def test_shaping_produces_the_lam_alef_ligature():
    assert LAM_ALEF_ISOLATED in shape_arabic("لا")
    # الاصطناعي: alef, then a lam-alef ligature, then the rest
    shaped = shape_arabic("الاصطناعي")
    assert shaped.startswith(ALEF_ISOLATED + LAM_ALEF_ISOLATED)
    assert "\u0644" not in shaped and "\u0627" not in shaped[1:]


def test_right_joining_letters_never_connect_to_the_following_letter():
    # dal is right-joining, so nothing in "دار" connects: three isolated forms.
    assert shape_arabic("دار") == "ﺩﺍﺭ"
    # beh is dual-joining, so in "بار" it connects forward and alef goes final.
    assert shape_arabic("بار") == "ﺑﺎﺭ"


def test_non_joining_hamza_stays_isolated():
    shaped = shape_arabic("بالذكاء")
    assert shaped.endswith("\u0621"), "a bare hamza has no contextual form"


def test_transparent_marks_do_not_break_a_join():
    # A fatha between two dual-joining letters must not turn them into isolated forms.
    with_mark = shape_arabic("بَت")
    without_mark = shape_arabic("بت")
    assert with_mark.replace("\u064e", "") == without_mark


def test_text_without_arabic_is_returned_untouched():
    assert display_text("SMA 20 / EMA 20") == "SMA 20 / EMA 20"
    assert shape_arabic("") == ""


# --------------------------------------------------------------------------- #
# Bidi reordering
# --------------------------------------------------------------------------- #

def test_rtl_reordering_reverses_arabic_but_keeps_numbers_left_to_right():
    visual = display_text("الإغلاق 92.40 جنيه")
    assert "92.40" in visual, "a number must keep its left-to-right digit order"
    # The trailing Arabic word is drawn first (leftmost) in a right-to-left line.
    assert visual.index("92.40") > 0
    assert visual.startswith(shape_arabic("جنيه")[::-1])


def test_signed_percentages_stay_intact_next_to_arabic():
    assert "+1.43%" in display_text("التغير +1.43%")
    assert "-2.90%" in display_text("المسافة -2.90%")


def test_latin_words_keep_their_order_inside_an_arabic_line():
    visual = display_text("COMI يقترب من كسر المقاومة")
    assert visual.endswith("COMI")
    assert "IMOC" not in visual


def test_ltr_base_direction_is_supported():
    assert display_text("EGP 92.40 السعر", base_rtl=False).startswith("EGP 92.40")


def test_bidi_mirrors_brackets_in_a_right_to_left_run():
    assert bidi_reorder("(نص)", base_rtl=True).startswith("(")


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #

def test_an_installed_arabic_capable_system_font_is_found():
    regular, bold = resolve_font_family()
    assert regular and bold
    import os
    assert os.path.exists(regular) and os.path.exists(bold)


def test_no_font_file_is_bundled_with_the_repository():
    """Fonts are discovered on the system; none are committed to the project."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    bundled = [path for path in root.rglob("*.ttf")
               if ".git" not in path.parts and "venv" not in path.parts]
    bundled += [path for path in root.rglob("*.otf")
                if ".git" not in path.parts and "venv" not in path.parts]
    assert bundled == [], f"font files must not be committed: {bundled}"


def test_font_candidates_are_bare_filenames_not_paths():
    for regular, bold in ARABIC_FONT_CANDIDATES:
        assert "/" not in regular and "\\" not in regular
        assert "/" not in bold and "\\" not in bold


def test_an_explicit_missing_font_path_is_reported():
    with pytest.raises(FontUnavailableError):
        resolve_font_family("C:/definitely/not/a/font.ttf")


# --------------------------------------------------------------------------- #
# PNG generation + dimensions
# --------------------------------------------------------------------------- #

def test_png_is_generated_from_fixture_evidence(bundle):
    data = generate_card_bytes(bundle.result, bundle.narrative)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 10_000


def test_card_labels_deterministic_narrative_source_honestly(bundle, monkeypatch):
    from core import analysis_card_generator as card_module
    from core.ai_analysis_narrative import FALLBACK_MODEL

    drawn = []
    original = card_module._Canvas.text_rtl

    def recording_text(self, right, y, text, *args, **kwargs):
        drawn.append(str(text))
        return original(self, right, y, text, *args, **kwargs)

    monkeypatch.setattr(card_module._Canvas, "text_rtl", recording_text)
    fallback_narrative = replace(bundle.narrative, model=FALLBACK_MODEL)
    generate_card_bytes(bundle.result, fallback_narrative)

    assert narrative_source_label(fallback_narrative.model) == (
        "Narrative  Deterministic Fallback"
    )
    assert any("Narrative  Deterministic Fallback" in text for text in drawn)
    assert not any("ChatGPT" in text or "external AI" in text for text in drawn)


@pytest.mark.parametrize("size,expected", [("POST", (1080, 1350)), ("STORY", (1080, 1920))])
def test_png_dimensions_match_the_declared_card_sizes(bundle, size, expected):
    data = generate_card_bytes(bundle.result, bundle.narrative, size=size)
    assert Image.open(io.BytesIO(data)).size == expected
    assert CARD_SIZES[size] == expected


def test_the_required_post_size_is_the_default():
    assert DEFAULT_CARD_SIZE == "POST"
    assert CARD_SIZES["POST"] == (1080, 1350)


def test_an_unknown_card_size_is_rejected(bundle):
    payload = build_card_payload(bundle.result, bundle.narrative)
    with pytest.raises(ValueError):
        render_card_png(payload, build_card_chart(bundle.result), size="BILLBOARD")


def test_arabic_renders_as_real_glyphs_not_empty_boxes(bundle):
    """A smoke check that Arabic actually reaches the bitmap.

    The card is rendered twice — once normally, once with every Arabic string emptied. The
    two images must differ, which they cannot if the Arabic drew nothing.
    """
    payload = build_card_payload(bundle.result, bundle.narrative)
    chart = build_card_chart(bundle.result)
    with_arabic = render_card_png(payload, chart)

    def _strip(value):
        return "".join(char for char in str(value)
                       if not ("\u0600" <= char <= "\u06ff"))

    stripped = replace(
        payload,
        title=_strip(payload.title),
        recommendation_label=_strip(payload.recommendation_label),
        price_rows=tuple((_strip(label), _strip(value)) for label, value in payload.price_rows),
        level_rows=tuple((_strip(label), _strip(value)) for label, value in payload.level_rows),
        scenario_rows=tuple((_strip(label), _strip(value))
                            for label, value in payload.scenario_rows),
        narrative_headline=_strip(payload.narrative_headline),
        narrative_summary=_strip(payload.narrative_summary),
        disclaimer=_strip(payload.disclaimer),
    )
    without_arabic = render_card_png(stripped, chart)
    assert with_arabic != without_arabic


def test_arabic_glyphs_have_real_bitmaps_in_the_resolved_font():
    from PIL import ImageFont
    regular, _ = resolve_font_family()
    font = ImageFont.truetype(regular, 32)
    notdef = (font.getmask("\ue000").size, bytes(font.getmask("\ue000")))
    for char in shape_arabic("تحليل سهم بالذكاء الاصطناعي"):
        if char.isspace():
            continue
        mask = font.getmask(char)
        assert (mask.size, bytes(mask)) != notdef, f"missing glyph for {char!r}"


# --------------------------------------------------------------------------- #
# Card content
# --------------------------------------------------------------------------- #

def test_card_payload_carries_the_required_content(bundle):
    payload = build_card_payload(bundle.result, bundle.narrative,
                                 company_name="Commercial International Bank")
    assert payload.symbol == "COMI"
    assert "Commercial International Bank" in payload.title
    assert "Continuous Session" in payload.title          # market status
    assert payload.as_of_label
    assert payload.recommendation_label == "قريب من التفعيل"
    assert payload.confidence_label == "63 / 100"
    assert payload.evidence_version == bundle.result.evidence_version
    assert payload.narrative_model == "claude-opus-4-8"

    price_labels = [label for label, _ in payload.price_rows]
    for required in ("السعر", "التغير", "الافتتاح", "الأعلى", "الأدنى", "الإغلاق",
                     "الاتجاه", "الزخم"):
        assert required in price_labels

    scenario_labels = [label for label, _ in payload.scenario_rows]
    for required in ("الحالة", "التفعيل", "الهدف", "الوقف"):
        assert required in scenario_labels

    # Provider and data timestamp travel with the card.
    assert "eodhd" in payload.data_quality_label
    assert "2026-07-22" in payload.data_quality_label


def test_card_chart_carries_only_supplied_numbers(bundle):
    chart = build_card_chart(bundle.result)
    price = bundle.result.price
    assert (chart.low, chart.high) == (price.low, price.high)
    assert (chart.open, chart.close) == (price.open, price.close)
    assert chart.last == price.last
    assert chart.has_axis() is True


def test_a_card_without_an_axis_still_renders(bundle):
    payload = build_card_payload(bundle.result, bundle.narrative)
    data = render_card_png(payload, CardChartData())
    assert Image.open(io.BytesIO(data)).size == (1080, 1350)


def test_a_card_renders_with_no_chart_and_no_narrative():
    payload = CardPayload(symbol="XXXX", title="—", as_of_label="—",
                          recommendation_label="البيانات غير كافية")
    data = render_card_png(payload)
    assert Image.open(io.BytesIO(data)).size == (1080, 1350)


def test_a_very_long_narrative_never_overruns_the_card(bundle):
    payload = replace(build_card_payload(bundle.result, bundle.narrative),
                      narrative_summary="السعر أعلى من المتوسطات القصيرة والمتوسطة. " * 40,
                      narrative_headline="عنوان طويل جداً يتجاوز عرض البطاقة بكثير " * 6)
    data = render_card_png(payload, build_card_chart(bundle.result))
    assert Image.open(io.BytesIO(data)).size == (1080, 1350)


def test_auction_phase_keeps_the_live_print_off_the_session_range(bundle):
    auction = replace(bundle.result, market_phase=MarketPhase.CLOSING_AUCTION,
                      request=replace(bundle.result.request,
                                      market_phase=MarketPhase.CLOSING_AUCTION))
    assert build_card_chart(auction).last is None
    data = generate_card_bytes(auction, bundle.narrative)
    assert Image.open(io.BytesIO(data)).size == (1080, 1350)


def test_the_card_never_shows_an_unconditional_buy(bundle):
    payload = build_card_payload(bundle.result, bundle.narrative)
    text = " ".join([payload.recommendation_label, payload.title,
                     payload.narrative_headline, payload.narrative_summary]
                    + [f"{label} {value}" for label, value in payload.scenario_rows])
    for forbidden in ("BUY", "اشترِ", "اشتر الآن", "شراء الآن"):
        assert forbidden not in text


def test_production_disabled_and_research_mode_are_drawn_on_every_card():
    """The mandatory safety badges are hard-coded in the renderer, not caller-supplied."""
    from pathlib import Path
    import core.analysis_card_generator as card
    source = Path(card.__file__).read_text(encoding="utf-8")
    assert "Production Disabled" in source
    assert "Research / Paper Mode" in source
    assert "Decision Support Only" in source


def test_the_card_renders_all_three_safety_badges_visibly(bundle):
    """Removing the badge block must change the bitmap — proof they are actually drawn."""
    import core.analysis_card_generator as card
    payload = build_card_payload(bundle.result, bundle.narrative)
    chart = build_card_chart(bundle.result)
    normal = render_card_png(payload, chart)

    original = card._Canvas.pill_rtl
    try:
        card._Canvas.pill_rtl = lambda self, right_x, *a, **k: right_x
        without_badges = render_card_png(payload, chart)
    finally:
        card._Canvas.pill_rtl = original
    assert normal != without_badges


def test_card_generation_is_deterministic_for_the_same_evidence(bundle):
    first = generate_card_bytes(bundle.result, bundle.narrative)
    second = generate_card_bytes(bundle.result, bundle.narrative)
    assert first == second
