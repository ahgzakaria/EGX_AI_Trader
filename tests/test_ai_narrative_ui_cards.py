"""Layout contract for the AI narrative panel — readable Arabic cards, not a debug dump.

These tests pin the *presentation* guarantees only. They assert nothing about how the
narrative is generated, which facts are bound to which section, how a number is rounded, or
what the analysis recommends — those belong to the core suites and are deliberately
untouched here.

What is pinned: eight sections become eight independent cards; model prose and
application-owned facts occupy separate blocks; the Arabic reading column is RTL,
right-aligned, at a readable size, in an installed system font stack; technical provenance
stays collapsed; the provider/model is named exactly once; and a plain rerender reaches no
provider, model or network.
"""

from __future__ import annotations

import re
import types
from dataclasses import replace

import pytest

from core.ai_narrative_facts import SECTIONS, build_fact_registry
from core.ai_stock_analysis_contract import NarrativeProvenance
from dashboard.ai_stock_analysis_components import (
    CHIP_MAX_LENGTH,
    NARRATIVE_ACCENTS,
    NARRATIVE_CSS,
    NARRATIVE_SECTION_META,
    fact_is_categorical,
    fixture_analysis,
    isolate_ltr,
    narrative_section_views,
    narrative_source_block,
    narrative_summary_cells,
    split_fact_line,
)

CARD_PATTERN = re.compile(r'<article class="egx-narr-card ([a-z]+)"')
PROSE_PATTERN = re.compile(r'<p class="prose">(.*?)</p>', re.S)
FACT_ROW_PATTERN = re.compile(
    r'<div class="row"><span class="lbl">(.*?)</span><span class="val">(.*?)</span></div>', re.S)
CHIP_PATTERN = re.compile(r'<span class="chip">(.*?)</span>', re.S)


# --------------------------------------------------------------------------- #
# Fixtures — one deterministic-fallback narrative and one validated Local AI one
# --------------------------------------------------------------------------- #

@pytest.fixture
def bundle():
    return fixture_analysis("RAYA")


@pytest.fixture
def result(bundle):
    return bundle.result


@pytest.fixture
def local_ai_narrative(bundle):
    """A validated Local-AI narrative built exactly the way the provider composes one.

    The prose is placeholder Arabic; every fact line comes from the real fact registry, so
    the composed ``label: value`` shape the page has to split is the production shape.
    """
    registry = build_fact_registry(bundle.result)
    sections = {}
    for name in SECTIONS:
        prose = f"جملة تحليلية عربية تصف حالة السهم في قسم {name} دون ذكر أي رقم."
        sections[name] = registry.compose(prose, registry.for_section(name)[:4])
    return replace(
        bundle.narrative,
        model="qwen3:4b",
        sections=tuple((name, sections[name]) for name in SECTIONS),
        provenance=NarrativeProvenance(
            source="LOCAL_AI_NARRATIVE", provider="ollama", model="qwen3:4b",
            prompt_version="ai-narrative@2", evidence_hash="deadbeef",
            generated_at="2026-07-27T10:00:00+03:00", validation_status="VALIDATED",
            latency_ms=1200, cached=False, fallback_reason=""),
    )


@pytest.fixture
def fallback_narrative(bundle):
    """The deterministic writer's narrative: no sections, honest fallback provenance."""
    return replace(
        bundle.narrative,
        sections=(),
        provenance=NarrativeProvenance(
            source="DETERMINISTIC_FALLBACK", provider="none", model="",
            prompt_version="ai-narrative@2", evidence_hash="deadbeef",
            generated_at="2026-07-27T10:00:00+03:00",
            validation_status="PROVIDER_UNAVAILABLE", latency_ms=None, cached=False,
            fallback_reason="local model unreachable"),
    )


# --------------------------------------------------------------------------- #
# Eight independent cards
# --------------------------------------------------------------------------- #

def test_the_eight_sections_render_as_eight_separate_cards(local_ai_narrative, result):
    markup = _render(local_ai_narrative, result).grid_html()
    assert len(CARD_PATTERN.findall(markup)) == 8
    for _, title_ar, _, _, _ in NARRATIVE_SECTION_META:
        assert markup.count(f"<h3>{title_ar}") == 1, f"{title_ar} is not its own card header"


def test_every_required_arabic_section_title_is_present(local_ai_narrative, result):
    markup = _render(local_ai_narrative, result).grid_html()
    for title in ("الخلاصة التنفيذية", "القراءة الفنية", "السيناريو الإيجابي",
                  "السيناريو السلبي", "شروط التأكيد", "شروط الإبطال",
                  "ملاحظات المخاطر", "حدود البيانات"):
        assert title in markup


def test_invalidation_uses_the_one_arabic_term_the_application_already_uses(
        local_ai_narrative, fallback_narrative, result):
    """One concept, one word: the page says الإبطال for invalidation everywhere.

    الإلغاء would read as a second, competing term next to the scenario section's
    "شروط الإبطال" and the "مستوى الإبطال" fact label. The section key is unaffected.
    """
    titles = dict((key, title_ar) for key, title_ar, _, _, _ in NARRATIVE_SECTION_META)
    assert titles["invalidation_conditions_ar"] == "شروط الإبطال"

    page = _page_source()
    components = _components_source()
    assert "شروط الإبطال" in page, "the scenario section's own wording moved"
    for source in (page, components):
        assert "الإلغاء" not in source

    for narrative in (local_ai_narrative, fallback_narrative):
        markup = _render(narrative, result).visible_html()
        assert "الإلغاء" not in markup


def test_the_cards_keep_the_contract_section_order(local_ai_narrative):
    keys = [view["key"] for view in narrative_section_views(local_ai_narrative)]
    assert keys == list(SECTIONS)


def test_the_single_giant_narrative_panel_is_gone():
    source = _page_source()
    assert ".egx-narr {" not in source, "the one-box narrative panel is still styled"
    assert "egx-facts" not in source, "the crowded chip flood is still rendered"
    assert 'class="egx-narr"' not in source
    # ...and nothing anywhere wraps the whole narrative in a single amber-bordered box.
    assert "border-right:3px solid var(--amber)" not in source


def test_each_card_can_be_read_on_its_own(local_ai_narrative, result):
    """Every card carries its own heading, accent and prose — no card is a continuation."""
    markup = _render(local_ai_narrative, result).grid_html()
    cards = markup.split('<article class="egx-narr-card ')[1:]
    assert len(cards) == 8
    for card in cards:
        assert "<h3>" in card and '<p class="prose">' in card
        assert 'class="accent"' in card


# --------------------------------------------------------------------------- #
# Prose and deterministic facts are separate blocks
# --------------------------------------------------------------------------- #

def test_ai_prose_and_deterministic_facts_live_in_separate_blocks(local_ai_narrative, result):
    markup = _render(local_ai_narrative, result).grid_html()
    prose_blocks = PROSE_PATTERN.findall(markup)
    assert len(prose_blocks) == 8
    for text in prose_blocks:
        assert "egx-narr-facts" not in text
        assert "chip" not in text
        assert ": " not in text, "a fact line leaked into the model's sentence"
    assert 'class="egx-narr-facts"' in markup


def test_a_fact_line_is_split_into_an_aligned_label_and_value(local_ai_narrative, result):
    markup = _render(local_ai_narrative, result).grid_html()
    rows = FACT_ROW_PATTERN.findall(markup)
    assert rows, "no aligned fact rows were rendered"
    for label, value in rows:
        assert label.strip() and value.strip()
        assert ": " not in value


def test_facts_are_not_joined_into_one_dotted_sentence(local_ai_narrative, result):
    markup = _render(local_ai_narrative, result).grid_html()
    assert " · " not in "".join(PROSE_PATTERN.findall(markup))
    for label, value in FACT_ROW_PATTERN.findall(markup):
        assert "·" not in value


def test_numeric_facts_render_as_rows_and_never_as_a_chip_row(local_ai_narrative, result):
    markup = _render(local_ai_narrative, result).grid_html()
    for chip in CHIP_PATTERN.findall(markup):
        assert not any(character.isdigit() for character in chip), (
            f"a numeric fact was rendered as a chip: {chip}")
        assert len(chip) <= CHIP_MAX_LENGTH
    numeric_rows = [value for _, value in FACT_ROW_PATTERN.findall(markup)
                    if any(character.isdigit() for character in value)]
    assert numeric_rows, "prices and indicators must appear as aligned rows"


def test_only_short_categorical_states_qualify_as_chips():
    assert fact_is_categorical("اتجاه صاعد") is True
    assert fact_is_categorical("بيانات محدثة") is True
    assert fact_is_categorical("EODHD") is True
    assert fact_is_categorical("8.49 جنيه") is False
    assert fact_is_categorical("0.53") is False
    assert fact_is_categorical("2026-07-23") is False
    assert fact_is_categorical("لا" * 40) is False


def test_a_composed_fact_line_splits_on_its_first_separator():
    assert split_fact_line("نقطة التفعيل: 8.49 جنيه") == ("نقطة التفعيل", "8.49 جنيه")
    assert split_fact_line("  الهدف المحسوب: 9.25 جنيه  ") == ("الهدف المحسوب", "9.25 جنيه")
    assert split_fact_line("بدون فاصل") == ("", "بدون فاصل")


def test_facts_are_never_reformatted_by_the_layout(local_ai_narrative, result):
    """Whatever the fact binder printed is what the row shows — character for character."""
    composed = dict(local_ai_narrative.sections)
    rendered = {value for _, value in
                FACT_ROW_PATTERN.findall(_render(local_ai_narrative, result).grid_html())}
    rendered |= set(CHIP_PATTERN.findall(_render(local_ai_narrative, result).grid_html()))
    for text in composed.values():
        for line in text.split("\n")[1:]:
            _, value = split_fact_line(line)
            assert value in rendered, f"the layout altered a fact value: {value}"


# --------------------------------------------------------------------------- #
# Typography, direction and reading width
# --------------------------------------------------------------------------- #

def test_the_narrative_block_is_rtl_and_right_aligned():
    assert "direction: rtl" in NARRATIVE_CSS
    assert "text-align: right" in NARRATIVE_CSS


def test_an_installed_arabic_capable_font_stack_is_used():
    assert '"Segoe UI", Tahoma, Arial, sans-serif' in NARRATIVE_CSS


def test_the_arabic_prose_is_at_least_16px_with_a_18_line_height():
    prose = _css_rule(".egx-narr-card p.prose")
    assert _px(prose, "font-size") >= 16
    assert "line-height: 1.8" in prose


@pytest.mark.parametrize("selector,low,high", [
    (".egx-narr-title", 24, 28),
    (".egx-narr-card h3", 18, 20),
    (".egx-narr-card p.prose", 16, 18),
    (".egx-narr-facts .lbl", 14, 15),
    (".egx-narr-facts .val", 15, 16),
])
def test_each_text_role_sits_in_its_required_size_band(selector, low, high):
    assert low <= _px(_css_rule(selector), "font-size") <= high


def test_technical_metadata_stays_in_the_12_to_13px_band():
    assert 12 <= _px(_css_rule(".egx-narr-note"), "font-size") <= 13


def test_the_card_heading_is_bold():
    assert "font-weight: 700" in _css_rule(".egx-narr-card h3")


def test_the_reading_column_is_capped_around_1000_to_1100px():
    assert 1000 <= _px(_css_rule(".egx-narrative"), "max-width") <= 1100
    assert "margin: 0 auto" in _css_rule(".egx-narrative")


def test_the_narrative_widgets_share_the_same_centred_reading_column():
    """The title row and the technical expander line up with the cards, not the page."""
    rule = _css_rule('[data-testid="stExpander"]:has(.egx-narr-tech)')
    assert 1000 <= _px(rule, "max-width") <= 1100
    assert "margin-inline: auto" in rule
    assert '[data-testid="stHorizontalBlock"]:has(.egx-narr-title)' in NARRATIVE_CSS


def test_the_arabic_font_stack_reaches_headings_too():
    """Streamlit sets a font on headings directly, so inheritance alone is not enough."""
    assert '"Segoe UI", Tahoma, Arial, sans-serif' in _css_rule(".egx-narrative *")


def test_the_prose_line_length_is_capped_for_readability():
    assert "max-width: 78ch" in _css_rule(".egx-narr-card p.prose")


def test_latin_abbreviations_are_isolated_so_arabic_direction_survives():
    markup = isolate_ltr("مؤشر RSI ومؤشر MACD عند مستوى محايد")
    assert '<span class="ltr" dir="ltr">RSI</span>' in markup
    assert '<span class="ltr" dir="ltr">MACD</span>' in markup
    assert "unicode-bidi: isolate" in NARRATIVE_CSS


def test_isolate_ltr_still_escapes_html():
    assert "<b>" not in isolate_ltr("<b>x</b>")
    assert "&lt;" in isolate_ltr("<b>x</b>")


def test_numeric_values_are_tabular_so_columns_line_up():
    assert "font-variant-numeric: tabular-nums" in _css_rule(".egx-narr-facts .val")


# --------------------------------------------------------------------------- #
# Responsive layout
# --------------------------------------------------------------------------- #

def test_the_desktop_grid_is_two_columns():
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in _css_rule(".egx-narr-grid")


def test_the_summary_and_technical_read_span_the_full_width(local_ai_narrative, result):
    widths = dict((view["key"], view["width"]) for view in
                  narrative_section_views(local_ai_narrative))
    assert widths["executive_summary_ar"] == "full"
    assert widths["technical_read_ar"] == "full"
    assert [key for key, width in widths.items() if width == "half"] == [
        "positive_scenario_ar", "negative_scenario_ar", "confirmation_conditions_ar",
        "invalidation_conditions_ar", "risk_notes_ar", "data_limitations_ar"]
    assert 'class="egx-narr-card full"' in _render(local_ai_narrative, result).grid_html()
    assert "grid-column: 1 / -1" in _css_rule(".egx-narr-card.full")


def test_tablet_and_mobile_collapse_to_a_single_column():
    tablet = _media_block("max-width: 1150px")
    assert "grid-template-columns: 1fr" in tablet
    assert "grid-column: auto" in tablet
    mobile = _media_block("max-width: 520px")
    assert "grid-template-columns: 1fr" in mobile


def test_nothing_forces_horizontal_scrolling():
    assert "overflow-x" not in NARRATIVE_CSS
    assert "white-space: nowrap" in _css_rule(".egx-narr-facts .val")
    # ...but a narrow screen must be allowed to wrap that value instead of clipping it.
    assert "white-space: normal" in _media_block("max-width: 520px")
    # No fixed pixel floor anywhere: a percentage min-width still shrinks with the screen,
    # a pixel one would push the page wider than the viewport.
    assert not re.search(r"min-width:\s*\d+px", NARRATIVE_CSS)


# --------------------------------------------------------------------------- #
# Source block, summary strip and technical details
# --------------------------------------------------------------------------- #

def test_the_local_ai_source_block_names_the_provider_and_model_once(local_ai_narrative,
                                                                     result):
    fake = _render(local_ai_narrative, result)
    visible = fake.visible_html()
    assert "ذكاء اصطناعي محلي" in visible and "Local AI" in visible
    assert "Ollama · qwen3:4b" in visible
    assert visible.count("qwen3:4b") == 1, "the model is repeated outside the source block"
    assert visible.count("Ollama") == 1
    assert "qwen3:4b" not in fake.grid_html(), "a card repeats the model name"


def test_a_validated_narrative_reports_that_state_in_words(local_ai_narrative):
    block = narrative_source_block(local_ai_narrative)
    assert block["title_ar"] == "ذكاء اصطناعي محلي" and block["title_en"] == "Local AI"
    assert block["detail"] == "Ollama · qwen3:4b"
    assert block["state_ar"] == "جاهز وتم التحقق من السرد"
    assert block["state_en"] == "Validated"


def test_the_summary_strip_uses_four_separate_cells(result, local_ai_narrative):
    cells = narrative_summary_cells(result)
    assert [label for label, _, _, _ in cells] == ["السهم", "التوصية", "آخر إغلاق", "الثقة"]
    values = [value for _, _, value, _ in cells]
    assert values[0] == result.request.symbol
    assert "جنيه" in values[2]
    assert values[3].endswith("/ 100")

    markup = _render(local_ai_narrative, result).visible_html()
    assert markup.count('<div class="cell">') == 4
    # the four values never collapse back into one run-on RTL sentence
    assert f"{values[0]} · {values[1]}" not in markup


def test_the_crowded_headline_line_is_no_longer_rendered(local_ai_narrative, result):
    markup = _render(local_ai_narrative, result).visible_html()
    assert local_ai_narrative.headline not in markup


def test_technical_details_are_collapsed_behind_one_expander(local_ai_narrative, result):
    fake = _render(local_ai_narrative, result)
    assert len(fake.expanders) == 1
    label, expanded = fake.expanders[0]
    assert expanded is False
    assert "تفاصيل تقنية للسرد" in label and "Narrative technical details" in label


@pytest.mark.parametrize("leak", ["derived from evidence", "deadbeef", "ai-narrative@2",
                                  "VALIDATED", "1200 ms"])
def test_debug_material_never_appears_in_the_reading_flow(local_ai_narrative, result, leak):
    fake = _render(local_ai_narrative, result)
    assert leak not in fake.visible_html()
    assert leak in fake.collapsed_html()


# --------------------------------------------------------------------------- #
# Deterministic fallback uses the same layout
# --------------------------------------------------------------------------- #

def test_the_fallback_shows_one_honest_source_badge(fallback_narrative):
    block = narrative_source_block(fallback_narrative)
    assert block["title_ar"] == "شرح حتمي"
    assert block["title_en"] == "Deterministic Fallback"
    assert block["detail"] == "", "a fallback must not advertise a provider or model"


def test_the_fallback_keeps_the_card_layout(fallback_narrative, result):
    fake = _render(fallback_narrative, result)
    markup = fake.grid_html()
    assert CARD_PATTERN.findall(markup), "the fallback fell back to a plain text panel"
    assert 'class="egx-narr-grid"' in markup
    assert PROSE_PATTERN.findall(markup)
    assert "الخلاصة التنفيذية" in markup and "القراءة الفنية" in markup
    assert markup.count('<div class="cell">') == 0  # the strip is its own block
    assert fake.visible_html().count('<div class="cell">') == 4


def test_local_ai_and_fallback_share_the_same_card_shape(local_ai_narrative,
                                                         fallback_narrative, result):
    def shape(narrative):
        markup = _render(narrative, result).grid_html()
        return (markup.count('class="egx-narr-grid"'),
                sorted(set(re.findall(r'class="([a-z\- ]+)"', markup))))

    ai_grid, ai_classes = shape(local_ai_narrative)
    fallback_grid, fallback_classes = shape(fallback_narrative)
    assert ai_grid == fallback_grid == 1
    # the fallback has no bound facts, so it renders a subset of the same class vocabulary
    assert set(fallback_classes) <= set(ai_classes)
    assert "egx-narr-card full" in fallback_classes


def test_a_missing_narrative_still_shows_an_honest_badge(result):
    fake = _render(None, result)
    assert "السرد غير متاح" in fake.visible_html()
    assert narrative_source_block(None)["title_en"] == "AI Unavailable"


# --------------------------------------------------------------------------- #
# Accents and accessibility
# --------------------------------------------------------------------------- #

def test_each_section_carries_its_own_subtle_accent(local_ai_narrative):
    accents = dict((view["key"], view["accent"])
                   for view in narrative_section_views(local_ai_narrative))
    assert accents == {
        "executive_summary_ar": "blue", "technical_read_ar": "violet",
        "positive_scenario_ar": "green", "negative_scenario_ar": "red",
        "confirmation_conditions_ar": "cyan", "invalidation_conditions_ar": "orange",
        "risk_notes_ar": "amber", "data_limitations_ar": "slate"}
    assert set(accents.values()) <= set(NARRATIVE_ACCENTS)


def test_the_accent_is_a_thin_line_not_a_saturated_card_background(local_ai_narrative,
                                                                   result):
    markup = _render(local_ai_narrative, result).grid_html()
    for accent in NARRATIVE_ACCENTS.values():
        assert f'background: {accent}' not in markup  # only the 3px rule may carry it
    assert "height: 3px" in _css_rule(".egx-narr-card .accent")
    assert "background: var(--surface)" in _css_rule(".egx-narr-card")


def test_headings_follow_a_semantic_hierarchy(local_ai_narrative, result):
    visible = _render(local_ai_narrative, result).visible_html()
    assert visible.count("<h2 ") == 1
    assert visible.count("<h3>") == 8
    assert "<h1" not in visible and "<h4" not in visible


def test_state_is_never_communicated_by_colour_alone(local_ai_narrative, result):
    visible = _render(local_ai_narrative, result).visible_html()
    assert "Validated" in visible and "جاهز وتم التحقق من السرد" in visible
    assert 'aria-hidden="true"' in visible  # the colour dot is decorative only


def test_the_regenerate_button_stays_next_to_the_narrative(local_ai_narrative, result,
                                                           bundle):
    fake = _render(local_ai_narrative, result, bundle=bundle)
    assert any("إعادة توليد الشرح بالذكاء الاصطناعي" in label for label in fake.buttons)


# --------------------------------------------------------------------------- #
# A UI-only rerender costs nothing
# --------------------------------------------------------------------------- #

def test_a_rerender_contacts_no_provider_model_or_network(local_ai_narrative, result,
                                                          bundle, monkeypatch):
    import socket

    def _boom(*args, **kwargs):
        raise AssertionError("the narrative panel opened a network socket")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    def _never(*args, **kwargs):
        raise AssertionError("a rerender re-ran the narrative generator")

    fake = _render(local_ai_narrative, result, bundle=bundle, regenerator=_never)
    assert fake.grid_html(), "the panel rendered nothing"


def test_the_panel_runs_no_local_model_health_probe():
    """The source block reads stored provenance; it never probes a model to draw a badge."""
    source = _page_source()
    for forbidden in ("local_ai_status", "check_health", "build_narrative",
                      "LOCAL_AI_STATUS_LABELS"):
        assert forbidden not in source, f"the page still probes a model: {forbidden}"


def test_regeneration_is_still_reachable_and_narrative_only(local_ai_narrative, result,
                                                            bundle, fallback_narrative):
    calls = []

    def _regenerator(supplied):
        calls.append(supplied)
        return types.SimpleNamespace(result=result, narrative=fallback_narrative)

    fake = _render(local_ai_narrative, result, bundle=bundle, regenerator=_regenerator,
                   button_returns=True)
    assert calls == [bundle], "regeneration must reuse the same evidence bundle"
    assert "شرح حتمي" in fake.visible_html()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _render(narrative, result, *, bundle=None, regenerator=None, button_returns=False):
    """Render only the narrative section against a recording Streamlit stand-in."""
    import dashboard.ai_stock_analysis as page

    fake = _FakeStreamlit(button_returns=button_returns)
    original_st, original_empty = page.st, page.empty_state
    page.st = fake
    page.empty_state = lambda title, message, **kwargs: fake.markdown(f"{title} {message}")
    try:
        page._narrative_section(narrative, result, bundle=bundle, regenerator=regenerator)
    finally:
        page.st, page.empty_state = original_st, original_empty
    return fake


def _page_source() -> str:
    from pathlib import Path
    import dashboard.ai_stock_analysis as page
    return Path(page.__file__).read_text(encoding="utf-8")


def _components_source() -> str:
    from pathlib import Path
    import dashboard.ai_stock_analysis_components as components
    return Path(components.__file__).read_text(encoding="utf-8")


def _strip_media_queries(css: str) -> str:
    """The stylesheet minus every ``@media`` block, so a rule lookup finds the base rule."""
    out, depth, index = [], 0, 0
    while index < len(css):
        if css.startswith("@media", index):
            depth = 0
            while index < len(css):
                if css[index] == "{":
                    depth += 1
                elif css[index] == "}":
                    depth -= 1
                    if depth == 0:
                        index += 1
                        break
                index += 1
            continue
        out.append(css[index])
        index += 1
    return "".join(out)


def _css_rule(selector: str) -> str:
    body = _strip_media_queries(NARRATIVE_CSS)
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", body)
    assert match, f"no CSS rule for {selector}"
    return match.group(1)


def _media_block(query: str) -> str:
    match = re.search(r"@media \(" + re.escape(query) + r"\) \{(.*?)\n\}", NARRATIVE_CSS, re.S)
    assert match, f"no @media block for {query}"
    return match.group(1)


def _px(rule: str, property_name: str) -> float:
    match = re.search(re.escape(property_name) + r":\s*([0-9.]+)px", rule)
    assert match, f"{property_name} is not declared in px: {rule}"
    return float(match.group(1))


class _FakeColumn:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeExpander:
    """Records everything written inside it separately — that content stays collapsed."""

    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        self._sink.depth += 1
        return self

    def __exit__(self, *exc):
        self._sink.depth -= 1
        return False


class _FakeSpinner:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeStreamlit(types.SimpleNamespace):
    def __init__(self, button_returns=False):
        super().__init__()
        self.session_state = {}
        self.written = []
        self.collapsed = []
        self.buttons = []
        self.expanders = []
        self.depth = 0
        self._button_returns = button_returns

    def _record(self, *args, **kwargs):
        for arg in args:
            if isinstance(arg, str):
                (self.collapsed if self.depth else self.written).append(arg)

    markdown = caption = write = text = _record
    error = warning = info = success = _record

    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        return [_FakeColumn(self) for _ in range(count)]

    def button(self, label, **kwargs):
        self.buttons.append(label)
        return self._button_returns

    def expander(self, label, expanded=True, **kwargs):
        self.expanders.append((label, expanded))
        return _FakeExpander(self)

    def spinner(self, *args, **kwargs):
        return _FakeSpinner()

    # --- assertions read through these ---
    def visible_html(self) -> str:
        return "\n".join(self.written)

    def collapsed_html(self) -> str:
        return "\n".join(self.collapsed)

    def grid_html(self) -> str:
        return "\n".join(chunk for chunk in self.written if "egx-narr-grid" in chunk)
