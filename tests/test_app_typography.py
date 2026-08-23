"""The app's typefaces must be set where Streamlit will honour them.

Injected CSS that sets ``font-family`` on ``.stApp`` looks correct and does
almost nothing: Streamlit writes its own ``font-family`` onto headings,
paragraphs, captions and navigation links, so inheritance never reaches the
elements that hold text. Measured in the running app, every heading, every
paragraph and every sidebar link was still on Source Sans while the rule was
present and appeared to be working. Only the buttons and the hand-written
``.egx-*`` components had changed.

The fix is the native theme config, which Streamlit applies to everything it
renders. These tests pin both halves: the faces are *loaded* by the stylesheet
and *applied* by the config, and neither half is any use alone.
"""

from __future__ import annotations

import inspect
import tomllib
from pathlib import Path

import pytest

CONFIG = Path(".streamlit/config.toml")

SANS = "IBM Plex Sans"
MONO = "IBM Plex Mono"


@pytest.fixture(scope="module")
def theme():
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))["theme"]


@pytest.fixture(scope="module")
def stylesheet():
    from dashboard.ui import apply_global_style

    return inspect.getsource(apply_global_style)


def test_the_body_typeface_is_set_in_the_theme_not_only_in_css(theme):
    assert SANS in theme["font"], (
        "font-family set only in injected CSS does not reach Streamlit's own "
        "headings, paragraphs or navigation links"
    )


def test_the_code_typeface_is_set_in_the_theme(theme):
    assert MONO in theme["codeFont"]


@pytest.mark.parametrize("key", ["font", "codeFont"])
def test_every_declared_stack_names_a_real_fallback(theme, key):
    """A morning when the font host is unreachable must cost only the typeface."""
    stack = theme[key]
    families = [part.strip().strip('"') for part in stack.split(",")]
    assert len(families) >= 2, f"{key} has no fallback: {stack}"
    generic = {"sans-serif", "serif", "monospace", "system-ui", "ui-monospace"}
    assert generic.intersection(families), f"{key} ends without a generic family: {stack}"


def test_the_faces_are_actually_loaded_by_the_stylesheet(stylesheet):
    """The theme names the families; something still has to fetch them."""
    assert "fonts.googleapis.com" in stylesheet
    assert "IBM+Plex+Sans" in stylesheet
    assert "IBM+Plex+Mono" in stylesheet


def test_the_theme_ground_matches_the_stylesheet_ground(theme, stylesheet):
    """Two sources of truth for one background is how a page ends up striped."""
    assert theme["backgroundColor"] == "#0b1220"
    assert "--bg: #0b1220" in stylesheet
    assert theme["secondaryBackgroundColor"] == "#131c30"
    assert "--surface: #131c30" in stylesheet
    assert theme["textColor"] == "#e6edf7"
    assert "--text: #e6edf7" in stylesheet


def test_digits_that_line_up_in_columns_are_set_in_tabular_figures(stylesheet):
    assert "tabular-nums" in stylesheet
