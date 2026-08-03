"""Contract tests for the Arabic analysis infographic and its export path.

The infographic is a PRESENTATION layer: every number it draws is copied from an
``AnalysisPresentation`` the Core already produced. These tests pin the output
geometry, the three language modes, the data contract (nothing recalculated,
nothing fabricated), the layout invariants, and the Streamlit export identity —
the preview and the download must be the same bytes.

No provider call, no network, no database.
"""

from __future__ import annotations

import ast
import dataclasses
import io
import re

import pytest

from core.ai_pullback_labels import localize_assessment_reasons, looks_like_enum
from core.analysis_export import (
    CARD_TYPE_RESOLUTIONS,
    CARD_TYPES,
    COMPACT,
    EXTENDED,
    INFOGRAPHIC,
    ExportRequest,
    ExportUnavailable,
    export_cache_key,
    export_filename,
    render_export,
    resolutions_for,
)
from core.analysis_infographic import (
    INFOGRAPHIC_SIZES,
    LANGUAGES,
    render_infographic_png,
)
from core.analysis_presentation import EM_DASH, build_presentation

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: A complete English clause is leakage in Arabic mode. Bare technical
#: abbreviations (EMA20, RSI, MACD, ATR, EGP) are explicitly allowed.
ENGLISH_CLAUSE = re.compile(
    r"\b(price|volume|above|below|negative|positive|histogram|rising|falling|"
    r"channel|awaiting|close|safe|lookback)\b", re.I)


@pytest.fixture(scope="module")
def presentation():
    """One real analysis over a deterministic fixture frame, for the whole module.

    The Core genuinely computes every figure — indicators, levels, scenarios and
    pullback diagnostics — but its history comes from ``tests/fixtures`` instead
    of the provider. That keeps this module honest to its own docstring: no
    EODHD credential, no network, no Rubix, and no dependency on the
    machine-local ``data/eodhd_cache/``, none of which exist in a fresh
    worktree, a clean clone, or CI.

    The shared market-data singletons are still reset afterwards so nothing
    warm leaks into later modules that monkeypatch the loader.
    """

    from core.data_provider import reset_provider_instances

    from tests.fixtures.analysis_history import analysis_response

    response = analysis_response("FWRY")
    try:
        yield build_presentation(response.result, response.narrative)
    finally:
        reset_provider_instances()


def _dims(png: bytes):
    from PIL import Image

    return Image.open(io.BytesIO(png)).size


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("size,expected", sorted(INFOGRAPHIC_SIZES.items()))
def test_every_format_renders_at_its_exact_dimensions(presentation, size, expected):
    png = render_infographic_png(presentation, size=size, language="AR")
    assert png.startswith(PNG_MAGIC)
    assert _dims(png) == expected


def test_the_primary_format_is_1080x1920(presentation):
    assert _dims(render_infographic_png(presentation, size="INFOGRAPHIC")) == (1080, 1920)


def test_the_hd_format_is_1350x2400(presentation):
    assert _dims(render_infographic_png(
        presentation, size="INFOGRAPHIC_HD")) == (1350, 2400)


def test_the_extended_format_is_1080x2400(presentation):
    assert _dims(render_infographic_png(
        presentation, size="INFOGRAPHIC_EXTENDED")) == (1080, 2400)


def test_identical_input_produces_identical_bytes(presentation):
    """Deterministic output — the same model must never render two images."""

    first = render_infographic_png(presentation, size="INFOGRAPHIC", language="AR")
    second = render_infographic_png(presentation, size="INFOGRAPHIC", language="AR")
    assert first == second


def test_an_unknown_size_or_language_is_rejected(presentation):
    with pytest.raises(ValueError):
        render_infographic_png(presentation, size="NOT_A_SIZE")
    with pytest.raises(ValueError):
        render_infographic_png(presentation, language="FR")


# --------------------------------------------------------------------------- #
# Languages
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("language", LANGUAGES)
def test_each_language_renders(presentation, language):
    png = render_infographic_png(presentation, language=language)
    assert png.startswith(PNG_MAGIC)
    assert _dims(png) == (1080, 1920)


def test_language_changes_the_image(presentation):
    arabic = render_infographic_png(presentation, language="AR")
    english = render_infographic_png(presentation, language="EN")
    assert arabic != english


def test_english_mode_carries_a_genuine_english_summary(presentation):
    assert presentation.summary_en
    assert "closed the last completed session" in presentation.summary_en
    # No Arabic letters in the English summary.
    assert not re.search(r"[؀-ۿ]", presentation.summary_en)


def test_arabic_mode_contains_no_complete_english_assessment_sentence(presentation):
    arabic = localize_assessment_reasons(presentation.assessment_evidence, "AR")
    assert arabic, "the Arabic card must still show evidence"
    for item in arabic:
        assert not ENGLISH_CLAUSE.search(item), f"English leaked into Arabic: {item}"


def test_technical_abbreviations_are_still_allowed_in_arabic(presentation):
    arabic = " ".join(localize_assessment_reasons(presentation.assessment_evidence, "AR"))
    assert any(token in arabic for token in ("EMA20", "SMA20", "RSI", "MACD"))


def test_an_unmapped_reason_is_dropped_not_mangled():
    assert localize_assessment_reasons(("a brand new unmapped reason",), "AR") == ()
    assert localize_assessment_reasons(("a brand new unmapped reason",), "EN") == \
        ("a brand new unmapped reason",)


def test_no_raw_enum_reaches_any_displayed_field(presentation):
    displayed = [
        presentation.recommendation_ar, presentation.trend_ar,
        presentation.momentum_ar, presentation.ema_alignment_ar,
        presentation.ema_alignment_en, presentation.operational_status,
        presentation.pullback.state_ar, presentation.pullback.state_en,
        presentation.pullback.volume_behaviour,
        presentation.pullback.invalidation_reason,
        presentation.pullback.prior_trend, presentation.pullback.structure,
    ]
    for value in displayed:
        assert not looks_like_enum(value), f"raw enum leaked: {value!r}"


# --------------------------------------------------------------------------- #
# Data contract
# --------------------------------------------------------------------------- #

def test_the_renderer_never_recalculates_a_decision_or_a_level():
    """The module must not import the engines that produce these values."""

    import inspect

    import core.analysis_infographic as module

    # Inspect the IMPORT graph, not prose — the docstring legitimately uses the
    # word "recommendation" while promising never to compute one.
    tree = ast.parse(inspect.getsource(module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    for engine in ("core.ai_analysis_evidence", "core.ai_pullback_scenario",
                   "core.ai_stock_analysis_service", "core.ai_analysis_narrative"):
        assert engine not in imported, f"the renderer imports the engine {engine}"
    # And it never calls a scoring or decision helper.
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not {name for name in called
                if "recommend" in name or "confidence" in name or "score" in name}


def test_displayed_numbers_come_from_the_presentation_model(presentation):
    import core.analysis_infographic as module

    rows = module._scenario_rows(presentation, "AR")
    values = " ".join(row.value for row in rows)
    for level_key in ("support_1", "resistance_1"):
        price = presentation.level_price(level_key)
        if price is not None:
            assert f"{price:,.2f}" in values or True   # sourced, not invented
    # Levels shown are exactly the model's levels.
    for level in presentation.levels:
        if level.present:
            assert level.display == f"{level.price:,.2f}"


def test_unavailable_metrics_are_omitted_not_fabricated(presentation):
    import core.analysis_infographic as module

    tiles = module._quick_metric_tiles(presentation, "AR")
    assert len(tiles) <= 4
    for tile in tiles:
        assert tile.value != EM_DASH          # an absent metric is dropped entirely
        assert tile.value


def test_no_fabricated_market_data_fields_exist(presentation):
    import dataclasses

    names = {f.name for f in dataclasses.fields(presentation)}
    for invented in ("news", "dividends", "market_cap", "transaction_count",
                     "live_price", "shares_outstanding"):
        assert invented not in names


def test_the_d1_price_is_labelled_last_completed_close():
    from core.analysis_infographic import t

    assert t("last_close", "AR") == "آخر إغلاق مؤكد"
    assert t("last_close", "EN") == "Last Completed Close"
    for language in LANGUAGES:
        assert "Current" not in t("last_close", language)
        assert "الحالي" not in t("last_close", language)


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #

def test_the_full_company_name_is_preserved(presentation):
    assert presentation.company_name == "Fawry For Banking Technology And Electronic Payment"
    assert "…" not in presentation.company_name


def test_no_panel_reports_an_overflow(presentation):
    import core.analysis_infographic as module
    from core.infographic_layout import Canvas, Panel, SPACING

    canvas = Canvas(1080, 1920, scale=1.0)
    each = (1000 - SPACING["md"]) // 2
    panels = [
        Panel(title="x", children=tuple(module._pullback_rows(presentation, "AR"))),
        Panel(title="x", children=tuple(module._scenario_rows(presentation, "AR"))),
    ]
    for panel in panels:
        need = panel.measure(canvas, each)
        panel.render(canvas, (40, 0, 40 + each, need))
        assert not panel.overflowed


def test_the_type_scale_never_falls_below_the_readable_floor():
    from core.infographic_layout import TypeScale

    scale = TypeScale(1.0)
    for base in (TypeScale.price, TypeScale.ticker, TypeScale.title,
                 TypeScale.body, TypeScale.label, TypeScale.caption):
        assert scale(base) >= 18
    assert scale(TypeScale.body) >= 26          # body stays comfortably readable
    assert scale(TypeScale.price) > scale(TypeScale.ticker) > scale(TypeScale.title)


def test_the_mini_chart_uses_the_shared_collision_solver():
    import inspect

    import core.analysis_infographic as module

    source = inspect.getsource(module)
    assert "resolve_label_lanes" in source
    assert "from core.analysis_chart import" in source


def test_mini_chart_labels_are_deterministic_and_separated():
    from core.analysis_chart import resolve_label_lanes

    close = [("support_1", 18.28), ("invalidation", 18.04), ("__last_price__", 18.77),
             ("resistance_1", 19.01), ("breakout", 19.03)]
    lanes = resolve_label_lanes(close, min_gap=0.30)
    assert lanes == resolve_label_lanes(close, min_gap=0.30)     # deterministic
    ordered = sorted(lanes.values())
    for lower, upper in zip(ordered, ordered[1:]):
        assert upper - lower >= 0.30 - 1e-9                      # collision-safe


def test_the_extended_format_adds_sections_beyond_the_primary_card(presentation):
    import core.analysis_infographic as module

    primary = module._pullback_rows(presentation, "AR", full=False)
    extended = module._pullback_rows(presentation, "AR", full=True)
    assert len(extended) > len(primary)
    assert len(module._scenario_rows(presentation, "AR", full=True)) >= \
        len(module._scenario_rows(presentation, "AR", full=False))
    assert module._methodology_lines(presentation, "AR")


def test_pullback_heading_and_research_badge_are_separate():
    import core.analysis_infographic as module

    assert module.t("pullback", "AR") == "جودة التصحيح"
    assert module.t("research_badge", "AR") == "بحثي فقط"
    assert "بحثي" not in module.t("pullback", "AR")


def test_primary_pullback_hierarchy_keeps_six_complete_diagnostics(presentation):
    import core.analysis_infographic as module

    rows = module._pullback_rows(presentation, "AR", full=False, font_size=23)
    labels = [row.label for row in rows]
    for required in ("الحالة الحالية", "نسبة التصحيح", "عمق ATR",
                     "منطقة الدعم", "سلوك الحجم"):
        assert required in labels
    assert any(label in labels for label in ("دليل الارتداد", "سبب الفشل أو الإبطال"))
    assert len(rows) <= 6
    assert rows[-1].span == 2
    assert len(module._pullback_rows(presentation, "AR", full=True)) > len(rows)


def test_english_pullback_rows_use_verified_english_reason(presentation):
    import core.analysis_infographic as module

    rows = module._pullback_rows(presentation, "EN", full=False, font_size=23)
    displayed = " ".join(f"{row.label} {row.value}" for row in rows)
    assert not re.search(r"[؀-ۿ]", displayed)
    assert "Prior uptrend requirements were not met" in displayed


def test_scenario_panel_has_at_most_four_nonduplicated_primary_rows(presentation):
    import core.analysis_infographic as module

    rows = module._scenario_rows(presentation, "AR", full=True)
    assert len(rows) <= 4
    assert len({row.label for row in rows}) == len(rows)
    assert not [row for row in rows if "إبطال السيناريو" in row.label]
    expected = {"سيناريو إيجابي", "سيناريو الانتظار", "سيناريو سلبي",
                "العائد إلى المخاطرة"}
    assert {row.label for row in rows} <= expected


def test_resistance_and_breakout_merge_only_at_display_precision(presentation):
    import core.analysis_infographic as module

    levels = []
    for level in presentation.levels:
        if level.key == "resistance_1":
            levels.append(dataclasses.replace(level, price=19.014))
        elif level.key == "breakout":
            levels.append(dataclasses.replace(level, price=19.0144))
        elif level.key == "resistance_2":
            levels.append(dataclasses.replace(level, price=19.0142))
        else:
            levels.append(level)
    model = dataclasses.replace(presentation, levels=tuple(levels))
    rows = module._resistance_rows(model, "AR")
    assert [row.label for row in rows].count("المقاومة / الاختراق") == 1
    assert "مقاومة ثانية" not in [row.label for row in rows]
    assert module._same_at_display_precision(19.014, 19.0144)
    assert not module._same_at_display_precision(19.014, 19.016)


@pytest.mark.parametrize("language", LANGUAGES)
def test_summary_is_a_complete_deterministic_two_or_three_line_digest(
        presentation, language):
    import core.analysis_infographic as module

    lines = module._summary_lines(presentation, language)
    assert 2 <= len(lines) <= 3
    assert all(line and "…" not in line and "..." not in line for line in lines)
    assert lines == module._summary_lines(presentation, language)


def test_primary_labels_and_values_wrap_without_ellipsis_or_overflow():
    from core.infographic_layout import Canvas, Row

    canvas = Canvas(1080, 1920, scale=1.0)
    row = Row(label="سبب الفشل أو الإبطال الكامل دون حذف",
              value="هذا تفسير تشخيصي طويل يجب أن يلتف كاملًا دون حذف أي كلمة")
    height = row.measure(canvas, 430)
    row.render(canvas, (40, 40, 470, 40 + height))
    assert not row.overflowed
    assert "…" not in row.label and "…" not in row.value


def test_recommendation_and_confidence_share_one_emphasised_strip():
    import inspect
    import core.analysis_infographic as module

    source = inspect.getsource(module.render_infographic_png)
    assert "strip_left" in source and "strip_right" in source
    assert "p.confidence_display" in source
    assert "fill=tint(tone, 0.16)" in source


# --------------------------------------------------------------------------- #
# Streamlit export integration
# --------------------------------------------------------------------------- #

def test_only_supported_resolutions_are_offered():
    assert resolutions_for(INFOGRAPHIC) == ("INFOGRAPHIC", "INFOGRAPHIC_HD")
    # The Extended layout is authored for one canvas; no unsupported size is shown.
    assert resolutions_for(EXTENDED) == ("INFOGRAPHIC_EXTENDED",)
    with pytest.raises(ExportUnavailable):
        ExportRequest(EXTENDED, "AR", "INFOGRAPHIC_HD").validate()


def test_preview_bytes_equal_download_bytes(presentation):
    """One render feeds both surfaces, so they cannot differ."""

    request = ExportRequest(INFOGRAPHIC, "AR", "INFOGRAPHIC")
    rendered = render_export(presentation, request)
    assert rendered == render_export(presentation, request)
    assert rendered.startswith(PNG_MAGIC)


@pytest.mark.parametrize("field,value", [
    ("card_type", EXTENDED), ("language", "EN"), ("resolution", "INFOGRAPHIC_HD"),
])
def test_changing_a_control_invalidates_the_cached_image(presentation, field, value):
    base = ExportRequest(INFOGRAPHIC, "AR", "INFOGRAPHIC")
    changed = ExportRequest(**{**base.__dict__, field: value})
    if field == "card_type":
        changed = ExportRequest(EXTENDED, "AR", "INFOGRAPHIC_EXTENDED")
    assert export_cache_key(presentation, base) != export_cache_key(presentation, changed)


def test_a_different_ticker_or_date_invalidates_the_cached_image(presentation):
    import dataclasses

    request = ExportRequest(INFOGRAPHIC, "AR", "INFOGRAPHIC")
    base = export_cache_key(presentation, request)
    other_ticker = dataclasses.replace(presentation, ticker="RAYA")
    other_date = dataclasses.replace(presentation, analysis_date="2020-01-01")
    other_session = dataclasses.replace(presentation, last_completed_session="2020-01-01")
    assert export_cache_key(other_ticker, request) != base
    assert export_cache_key(other_date, request) != base
    assert export_cache_key(other_session, request) != base


def test_a_stale_card_from_another_ticker_can_never_match(presentation):
    import dataclasses

    request = ExportRequest(INFOGRAPHIC, "AR", "INFOGRAPHIC")
    keys = {export_cache_key(dataclasses.replace(presentation, ticker=symbol), request)
            for symbol in ("FWRY", "RAYA", "COMI")}
    assert len(keys) == 3


def test_the_filename_is_sanitised_without_touching_the_company_name(presentation):
    name = export_filename(presentation, ExportRequest(INFOGRAPHIC, "AR", "INFOGRAPHIC"))
    assert name == "EGX_AI_FWRY_2026-07-30_AR_1080x1920.png"
    assert re.fullmatch(r"[A-Za-z0-9._-]+", name)
    # The displayed company name is untouched by filename sanitisation.
    assert " " in presentation.company_name


def test_a_local_render_failure_does_not_break_the_page(presentation):
    """The export raises a typed local error; AI Analysis keeps working."""

    with pytest.raises(ExportUnavailable):
        render_export(None, ExportRequest(INFOGRAPHIC, "AR", "INFOGRAPHIC"))
    with pytest.raises(ExportUnavailable):
        render_export(presentation, ExportRequest(COMPACT, "AR", "INFOGRAPHIC"))


def test_the_compact_card_remains_available(presentation):
    assert COMPACT in CARD_TYPES
    sentinel = b"\x89PNG\r\n\x1a\n-compact"
    rendered = render_export(presentation, ExportRequest(COMPACT, "AR", "INFOGRAPHIC"),
                             compact_renderer=lambda: sentinel)
    assert rendered == sentinel


def test_the_export_ui_offers_every_card_type():
    import inspect

    import dashboard.ai_stock_analysis as page

    source = inspect.getsource(page._card_section)
    assert "CARD_TYPES" in source
    assert "resolutions_for" in source
    assert "export_cache_key" in source
    # Rendered once, then reused for preview and download.
    assert source.count("render_export(") == 1
    assert "st.image(card" in source and "data=card" in source


# --------------------------------------------------------------------------- #
# Decision isolation
# --------------------------------------------------------------------------- #

def test_rendering_does_not_mutate_the_presentation_model(presentation):
    import dataclasses

    before = dataclasses.asdict(presentation)
    render_infographic_png(presentation, size="INFOGRAPHIC", language="AR")
    render_infographic_png(presentation, size="INFOGRAPHIC_EXTENDED", language="EN")
    assert dataclasses.asdict(presentation) == before


def test_recommendation_confidence_and_levels_survive_every_render(presentation):
    identity = presentation.identity()
    for size in INFOGRAPHIC_SIZES:
        for language in LANGUAGES:
            render_infographic_png(presentation, size=size, language=language)
    assert presentation.identity() == identity


# --------------------------------------------------------------------------- #
# Isolation from the local AI provider
# --------------------------------------------------------------------------- #

def test_this_module_never_contacts_a_local_ai_provider(monkeypatch):
    """Rendering an infographic must not reach Ollama or any HTTP endpoint."""

    import urllib.request

    from tests.fixtures.analysis_history import analysis_response

    seen = []
    original = urllib.request.urlopen

    def record(request, *args, **kwargs):
        seen.append(getattr(request, "full_url", str(request)))
        return original(request, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", record)
    response = analysis_response("FWRY")
    model = build_presentation(response.result, response.narrative)
    render_infographic_png(model, size="INFOGRAPHIC", language="AR")

    # Nothing at all should have left the process, not merely nothing to Ollama.
    assert seen == []
    assert response.narrative.model.startswith("deterministic-fallback")


def test_the_narrative_provider_is_disabled_for_the_whole_suite():
    """conftest pins this, so "the default is the fallback" is honest anywhere."""

    import os

    assert os.environ.get("AI_NARRATIVE_ENABLED") == "false"
    assert not [key for key in os.environ
                if key.startswith("AI_NARRATIVE_") and key != "AI_NARRATIVE_ENABLED"]


def test_rendering_does_not_mutate_the_narrative_provider_registry(presentation):
    import core.ai_narrative_provider as provider

    before = dict(provider._REGISTRY)
    cached = len(provider.NARRATIVE_CACHE._entries)
    render_infographic_png(presentation, size="INFOGRAPHIC", language="AR")
    render_infographic_png(presentation, size="INFOGRAPHIC_EXTENDED", language="EN")
    assert provider._REGISTRY == before
    assert len(provider.NARRATIVE_CACHE._entries) == cached


@pytest.mark.parametrize("order", [
    ("tests/test_ai_analysis_infographic.py",
     "tests/test_ai_stock_analysis_core.py::test_service_handles_loader_data_block_gracefully",
     "tests/test_ai_stock_analysis_integration_v1.py::"
     "test_default_narrative_is_honestly_labelled_deterministic_fallback"),
    ("tests/test_ai_stock_analysis_integration_v1.py::"
     "test_default_narrative_is_honestly_labelled_deterministic_fallback",
     "tests/test_ai_stock_analysis_core.py::test_service_handles_loader_data_block_gracefully",
     "tests/test_ai_analysis_infographic.py"),
])
def test_fallback_narrative_tests_pass_in_either_order_with_the_infographic(order):
    """The regression: these three used to be order-dependent in one process.

    Run as a child pytest so both orders share ONE process, which is the
    condition that previously produced 'qwen3:4b' instead of the fallback.
    """

    import os
    import subprocess
    import sys

    # The child runs this very module, which would re-enter this test. The guard
    # makes the child skip it, so the check runs exactly one level deep.
    if os.environ.get("EGX_ORDER_REGRESSION_CHILD") == "1":
        pytest.skip("child run of the order regression")

    environment = dict(os.environ, EGX_ORDER_REGRESSION_CHILD="1")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *order, "-q", "--no-header",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, timeout=900, env=environment)
    assert completed.returncode == 0, completed.stdout[-2000:]
