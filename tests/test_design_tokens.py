"""The design layer: one token set, and three states for every check.

These pin the findings from docs/design/stitch/AUDIT.md that would otherwise
come back the next time a screen is restyled:

* the semantic colours are the design system's, not Tailwind's defaults that
  the generated screens drifted to,
* a check renders as PASS / FAIL / UNAVAILABLE and an unrecognised state is
  never drawn as a pass,
* a value that was never measured is not drawn as a zero or an empty cell,
* Arabic has a face loaded for it.

Presentation only. Nothing here reads a price or changes a decision.
"""

from __future__ import annotations

import re

import pytest

from dashboard import ui


@pytest.fixture(scope="module")
def stylesheet(monkeypatch_module=None):
    """The global stylesheet, captured as text."""
    captured = []
    original = ui.st.markdown
    ui.st.markdown = lambda body, **kwargs: captured.append(body)
    try:
        ui.apply_global_style()
    finally:
        ui.st.markdown = original
    return captured[0]


# --- tokens -------------------------------------------------------------------

#: The design system's semantic palette, and the Tailwind default each
#: generated screen drifted to. The spec's values win: they were derived from
#: this app's own palette, and the substitution was the generator's.
SPEC_VS_DRIFT = [
    ("--green", "#34d399", "#10b981"),
    ("--red", "#f87171", "#f43f5e"),
    ("--blue", "#60a5fa", "#3b82f6"),
    ("--amber", "#fbbf24", "#f59e0b"),
]


def _declarations(stylesheet):
    """The stylesheet with comments removed.

    A hex named in a comment that explains the drift is not the drift.
    """
    return re.sub(r"/\*.*?\*/", "", stylesheet, flags=re.S)


@pytest.mark.parametrize("token, spec, drift", SPEC_VS_DRIFT)
def test_the_semantic_colours_are_the_specs_not_the_generators(stylesheet, token, spec, drift):
    css = _declarations(stylesheet)
    assert re.search(rf"{token}\s*:\s*{spec}\b", css), f"{token} is not {spec}"
    assert drift not in css, f"{drift} drifted back into the stylesheet"


@pytest.mark.parametrize("tone", sorted(ui._TONE_COLOUR))
def test_a_badge_tone_is_one_colour_at_three_opacities(tone):
    """Not three related hues.

    "green" was the spec's #34d399 text over a #10b981 background with a
    #10b981 border -- the design system's green wearing Tailwind's -- and amber,
    red and blue were the same. Deriving all three from one value makes that
    unrepresentable rather than merely fixed.
    """
    background, border, text = ui._TONE[tone]
    r, g, b = ui._TONE_COLOUR[tone]
    assert background == f"rgba({r},{g},{b},.12)"
    assert border == f"rgba({r},{g},{b},.30)"
    assert text == f"#{r:02x}{g:02x}{b:02x}"


def test_every_token_the_components_use_is_defined(stylesheet):
    """A var() with no declaration renders as nothing at all, silently."""
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", stylesheet))
    used = set(re.findall(r"var\((--[a-z0-9-]+)", stylesheet))
    assert not (used - declared), f"undeclared: {sorted(used - declared)}"


def test_unknown_is_not_a_shade_of_grey(stylesheet):
    """Grey reads as disabled. Never-measured is a different fact."""
    assert re.search(r"--unknown\s*:\s*#c084fc", stylesheet)
    assert ui._TONE["unknown"][2] != ui._TONE["gray"][2]


def test_there_are_no_drop_shadows(stylesheet):
    """Depth is one rung on the surface ladder plus a border, by decision.

    `inset` is allowed and is not elevation: the sidebar's current-page rail is
    drawn with one, which is a mark on an edge rather than a lift off the page.
    """
    shadows = re.findall(r"box-shadow\s*:\s*([^;}]+)", _declarations(stylesheet))
    assert shadows, "the rail uses one; if it stopped, update this test"
    for value in shadows:
        assert "inset" in value, f"drop shadow: {value.strip()}"


# --- typography ---------------------------------------------------------------

def test_an_arabic_face_is_loaded_and_bound(stylesheet):
    """Half this interface is Arabic and nothing loaded a face for it."""
    assert "IBM+Plex+Sans+Arabic" in stylesheet
    assert re.search(r"--font-ar\s*:\s*\"IBM Plex Sans Arabic\"", stylesheet)
    assert "var(--font-ar)" in stylesheet


def test_numbers_are_tabular_everywhere_they_stack(stylesheet):
    assert "tabular-nums" in stylesheet
    assert '"tnum" 1' in stylesheet


def test_keyboard_focus_reaches_more_than_buttons(stylesheet):
    """Four of the twelve generated screens had no focus state at all."""
    block = re.search(r":where\((.*?)\):focus-visible", stylesheet, re.S)
    assert block, "no shared focus-visible rule"
    for element in ("a", "button", "input", "select", "textarea"):
        assert re.search(rf"\b{element}\b", block.group(1))


def test_reduced_motion_is_respected(stylesheet):
    assert "prefers-reduced-motion" in stylesheet


# --- the three-state gate -----------------------------------------------------

def test_a_gate_renders_each_of_its_three_states_distinctly():
    classes = {ui.gate_html(state).split('egx-gate ')[1].split('"')[0]
               for state in (ui.GATE_PASS, ui.GATE_FAIL, ui.GATE_UNAVAILABLE)}
    assert classes == {"pass", "fail", "na"}


def test_an_unrecognised_state_is_unavailable_and_never_a_pass():
    """The whole point: a result nobody could compute must not read as PASS."""
    for state in ("", None, "maybe", "ok", True, "PASSED?"):
        rendered = ui.gate_html(state)
        assert "egx-gate na" in rendered
        assert ui.GATE_UNAVAILABLE in rendered
        assert ">PASS<" not in rendered


def test_a_gate_carries_its_label_and_tooltip():
    rendered = ui.gate_html(ui.GATE_FAIL, label="Liquidity", title="turnover below floor")
    assert "Liquidity FAIL" in rendered
    assert 'title="turnover below floor"' in rendered


def test_the_unavailable_state_is_hatched_not_filled(stylesheet):
    """An absence of a result should not look like a result."""
    rule = re.search(r"\.egx-gate\.na\s*\{(.*?)\}", stylesheet, re.S)
    assert rule and "repeating-linear-gradient" in rule.group(1)


# --- unknown, provenance, advisory --------------------------------------------

def test_an_unmeasured_value_is_neither_zero_nor_blank():
    rendered = ui.unknown_html()
    assert "egx-unknown" in rendered
    assert "0" not in re.sub(r"[^0-9]", "", rendered or "0".replace("0", ""))
    assert rendered.strip() != ""


def test_the_unknown_marker_says_why_on_hover():
    assert 'title="never priced"' in ui.unknown_html("never priced")


def test_provenance_names_its_source():
    assert "mubasher_index" in ui.provenance_html("mubasher_index")
    assert "egx-prov" in ui.provenance_html("x")


def test_advisory_is_blue_not_green(stylesheet):
    """Green means a position made money. An advisory must not borrow it."""
    rule = re.search(r"\.egx-advisory\s*\{(.*?)\}", stylesheet, re.S)
    assert rule and "var(--blue)" in rule.group(1)
    assert "var(--green)" not in rule.group(1)


# --- escaping -----------------------------------------------------------------

@pytest.mark.parametrize("render", [
    lambda v: ui.gate_html(ui.GATE_PASS, label=v),
    lambda v: ui.gate_html(ui.GATE_PASS, title=v),
    lambda v: ui.unknown_html(v),
    lambda v: ui.provenance_html(v),
    lambda v: ui.advisory_html(v),
    lambda v: ui.badge_html(v, "unknown"),
])
def test_nothing_injects_markup(render):
    assert "<script>" not in render('<script>alert(1)</script>')
