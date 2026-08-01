"""Pullback Health card: no raw enums, and no layout-collapsing CSS.

Measured root cause (RAYA, 1920x1080, dark, RTL): ``pullback_scenario_view`` passed
three INTERNAL enums straight to the UI — ``prior_trend_status``
(``INVALID_PRIOR_TREND``), ``ema_alignment_status`` (``EMA20_ABOVE_EMA50``) and the
deliberately concatenated ``· INVALID_PRIOR_UPTREND``. Rendered in a ``metric_card``
``.v`` at 1.45rem inside a ~201px content box, those unbreakable 17-19 character
tokens hit ``overflow-wrap: break-word`` and force-broke mid-token, taking the two
six-column rows from 84px to 108px per card (row height 92px vs the 68px every
comparable metric row uses). The narrative ``_kv_table`` value cell compounded it by
being ``direction: ltr`` while holding Arabic, with a ``white-space: nowrap`` key
column that refused to yield width.

No provider call, no network, no database.
"""

from __future__ import annotations

import inspect
import re

import pytest

import dashboard.ai_stock_analysis as page
from dashboard.ai_stock_analysis_components import (
    PULLBACK_EMA_LABELS,
    PULLBACK_REASON_LABELS,
    PULLBACK_TREND_LABELS,
    pullback_scenario_view,
)

RAW_ENUMS = ("INVALID_PRIOR_UPTREND", "INVALID_PRIOR_TREND", "EMA20_ABOVE_EMA50",
             "HIGHER_HIGH_HIGHER_LOW")


class _Scenario:
    """The RAYA shape, as returned by the real engine."""

    state = None
    prior_trend_status = "INVALID_PRIOR_TREND"
    structure_status = "HIGHER_HIGH_HIGHER_LOW"
    ema_alignment_status = "EMA20_ABOVE_EMA50"
    invalidation_reason = "INVALID_PRIOR_UPTREND"
    volume_behaviour = "SELLING_VOLUME_CONTRACTING"
    confirmation_status = "WAITING"
    confirmation_reasons = ()
    support_zone_low, support_zone_high = 7.3246, 7.4116
    swing_high, impulse_low = 8.49, 7.68
    swing_high_date = "2026-06-18"
    swing_high_confirmation_date = "2026-06-25"
    impulse_low_date = "2026-05-11"
    pullback_percent, pullback_atr = 11.66, 3.42
    impulse_retracement_percent = 122.22
    correction_bars = 12
    support_reached = True
    support_confluence = ("EMA50",)
    research_score = None
    ema20, ema50 = 7.66, 7.37
    entry_trigger = stop_loss = target_1 = target_2 = None
    major_resistance = minor_pivot_resistance = None
    meaningful_structural_target = broader_structural_target = None
    reward_risk = reward_risk_meaningful_target = reward_risk_broader_target = None
    explanation_ar = "الاتجاه السابق لا يحقق شروط الاتجاه الصاعد البحثية."
    explanation_en = "The prior move does not satisfy the research uptrend rules."
    evidence = ()
    missing_measurements = ()
    historical_data_cutoff = "2026-07-30"


@pytest.fixture
def view():
    return pullback_scenario_view(_Scenario())


# --------------------------------------------------------------------------- #
# No raw enum in user-facing text
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("field", ["invalidation", "trend", "ema_relation", "structure"])
def test_no_raw_enum_appears_in_a_user_facing_field(view, field):
    for enum in RAW_ENUMS:
        assert enum not in view[field], f"{field} leaked the internal enum {enum}"


def test_the_localized_reason_is_what_the_user_sees(view):
    assert view["invalidation"] == PULLBACK_REASON_LABELS["INVALID_PRIOR_UPTREND"]
    assert view["trend"] == PULLBACK_TREND_LABELS["INVALID_PRIOR_TREND"]
    assert view["ema_relation"] == PULLBACK_EMA_LABELS["EMA20_ABOVE_EMA50"]


def test_the_internal_codes_remain_available_for_diagnostics(view):
    """They must still be inspectable — just not in the headline sentence."""

    assert view["invalidation_code"] == "INVALID_PRIOR_UPTREND"
    assert view["trend_code"] == "INVALID_PRIOR_TREND"
    assert view["ema_relation_code"] == "EMA20_ABOVE_EMA50"
    assert view["structure_code"] == "HIGHER_HIGH_HIGHER_LOW"


def test_the_diagnostic_codes_are_rendered_only_inside_a_collapsed_expander():
    source = inspect.getsource(page._pullback_section)
    assert "invalidation_code" in source
    marker = source.index("invalidation_code")
    expander = source.rindex("st.expander", 0, marker)
    assert "expanded=False" in source[expander:marker]


# --------------------------------------------------------------------------- #
# Layout rules
# --------------------------------------------------------------------------- #

def _page_css():
    """The page CSS with /* comments */ stripped, so prose never masks a rule."""

    return re.sub(r"/\*.*?\*/", "", inspect.getsource(page._page_style), flags=re.S)


def test_no_one_character_word_breaking_css_is_applied():
    css = _page_css()
    assert "break-all" not in css
    assert "overflow-wrap:anywhere" not in css.replace(" ", "")


def test_the_narrative_value_cell_is_rtl_and_wraps_normally():
    css = _page_css()
    block = css[css.index(".egx-kv td.v {"):css.index(".egx-kv td.v.num")]
    for rule in ("direction:rtl", "text-align:right", "white-space:normal",
                 "word-break:normal", "overflow-wrap:break-word"):
        assert rule in block.replace(" ", "").replace("\n", "").replace(
            "direction:rtl", "direction:rtl") or rule.replace(":", ":") in block, rule


def test_the_key_column_no_longer_pins_itself_with_nowrap():
    css = _page_css()
    key_block = css[css.index(".egx-kv td.k {"):css.index(".egx-kv td.k .en")]
    assert "white-space:nowrap" not in key_block.replace(" ", "")
    assert "white-space:normal" in key_block.replace(" ", "")


def test_a_phrase_valued_metric_uses_the_smaller_wrapping_style():
    css = _page_css()
    assert ".egx-metric .v.text" in css
    block = css[css.index(".egx-metric .v.text"):]
    compact = block.replace(" ", "").replace("\n", "")
    assert "white-space:normal" in compact
    assert "word-break:normal" in compact
    assert "font-size:1.45rem" not in compact       # never the oversized numeric style


def test_technical_identifiers_are_bidi_isolated():
    rendered = page._isolate_identifiers("EMA20 أعلى من EMA50")
    assert '<bdi dir="ltr">EMA20</bdi>' in rendered
    assert '<bdi dir="ltr">EMA50</bdi>' in rendered
    # Arabic itself is never wrapped in an LTR isolate.
    assert "<bdi" not in rendered.split("EMA20</bdi>")[1].split("<bdi")[0]


def test_numeric_values_keep_the_ltr_tabular_presentation():
    assert page._is_numeric_value("7.32 – 7.41")
    assert page._is_numeric_value("122.22%")
    assert page._is_numeric_value("3.42 ATR")
    assert not page._is_numeric_value("الاتجاه الصاعد السابق لم يستوفِ الشروط")


def test_the_pullback_card_does_not_rely_on_a_zero_width_column():
    """Every column weight must be positive, or a cell collapses to nothing."""

    source = inspect.getsource(page._pullback_section)
    for spec in re.findall(r"st\.columns\(\[([^\]]+)\]\)", source):
        weights = [float(x.strip()) for x in spec.split(",")]
        assert all(w > 0 for w in weights), spec


def test_missing_values_render_as_an_em_dash_not_zero():
    class Empty(_Scenario):
        prior_trend_status = None
        ema_alignment_status = None
        structure_status = None
        invalidation_reason = ""
        pullback_percent = pullback_atr = None
        swing_high = impulse_low = None
        support_zone_low = support_zone_high = None

    empty = pullback_scenario_view(Empty())
    for field in ("trend", "ema_relation", "structure", "invalidation",
                  "swing_high", "impulse_low", "support_zone"):
        assert empty[field] == "—", f"{field} was {empty[field]!r}, expected an em dash"
        assert empty[field] != "0"
        assert empty[field] != 0


# --------------------------------------------------------------------------- #
# Permanently visible content
# --------------------------------------------------------------------------- #

def test_state_explanation_measurements_and_research_warning_stay_visible():
    source = inspect.getsource(page._pullback_section)
    head = source[:source.index("Research simulation levels")]
    assert "state_ar" in head                       # pullback state
    assert "Research Only" in head                  # research warning
    assert "pullback_percent" in head               # main measurements
    assert "support_zone" in head
    assert "st.warning(" in head


def test_only_the_simulation_levels_stay_collapsed():
    source = inspect.getsource(page._pullback_section)
    collapsed = re.findall(r'st\.expander\(\s*\n?\s*"([^"]+)"', source)
    assert any("simulation levels" in title for title in collapsed)
    for title in collapsed:
        assert ("simulation levels" in title) or ("Diagnostic details" in title), title


# --------------------------------------------------------------------------- #
# The two defects that survived the first fix
# --------------------------------------------------------------------------- #

def test_the_narrative_paragraph_never_leaks_a_reason_code():
    """A SECOND reason map lived in core.ai_analysis_narrative and lacked
    INVALID_PRIOR_UPTREND, so the raw enum still reached the reader through the
    narrative sentence after the dashboard copy was fixed. One map now serves both.
    """

    from core.ai_pullback_labels import PULLBACK_REASON_AR, localize_pullback_reason
    from dashboard.ai_stock_analysis_components import PULLBACK_REASON_LABELS

    assert PULLBACK_REASON_LABELS is PULLBACK_REASON_AR      # one shared source
    assert "INVALID_PRIOR_UPTREND" in PULLBACK_REASON_AR

    narrative = inspect.getsource(
        __import__("core.ai_analysis_narrative", fromlist=["x"]))
    assert "_PULLBACK_REASON_AR = {" not in narrative        # duplicate map is gone
    assert "localize_pullback_reason" in narrative

    for code in list(PULLBACK_REASON_AR) + ["A_BRAND_NEW_UNMAPPED_REASON"]:
        localized = localize_pullback_reason(code)
        assert code not in localized, f"{code} leaked into user text"
        assert localized


def test_a_narrative_length_fact_value_can_wrap():
    """`.egx-narr-facts .val` was `white-space: nowrap` against a
    `minmax(0, 1fr)` label column: one long value became an unbreakable line that
    overflowed the card and squeezed the label to ~0 — the one-glyph-per-line column.
    """

    from dashboard.ai_stock_analysis_components import NARRATIVE_CSS

    def rule(selector):
        start = NARRATIVE_CSS.index(selector + " {")
        return NARRATIVE_CSS[start:NARRATIVE_CSS.index("}", start)]

    value = rule(".egx-narr-facts .val")
    assert "white-space: normal" in value
    assert "word-break: normal" in value
    assert "break-all" not in value and "anywhere" not in value
    # Only the numeric variant stays on one line.
    assert "white-space: nowrap" in rule(".egx-narr-facts .val.num")
    # The label column can no longer collapse to zero width.
    row = rule(".egx-narr-facts .row")
    assert "minmax(0, 1fr) auto" not in row
    assert "minmax(8ch" in row
