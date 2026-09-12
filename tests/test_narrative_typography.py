"""The longest Arabic prose in the app is set in an Arabic face.

This page carries the only long-form prose in the product, and it was rendered
in "Segoe UI", Tahoma, Arial -- whatever Windows had -- applied with
`!important` to every descendant, so it actively overrode the application's own
font. That was correct when it was written: nothing here loaded an Arabic face
at all. `dashboard/ui.py` loads IBM Plex Sans Arabic now.

Presentation only. No narrative text, field or number changes.
"""

from __future__ import annotations

import re

import pytest

from dashboard import ui
from dashboard.ai_stock_analysis_components import (NARRATIVE_CSS,
                                                    NARRATIVE_FONT_STACK)


def test_the_narrative_is_set_in_the_arabic_face_the_app_loads():
    assert NARRATIVE_FONT_STACK.startswith('"IBM Plex Sans Arabic"')


def test_the_app_actually_loads_that_face(monkeypatch):
    """A stack naming a font nobody fetched is a stack naming a fallback."""
    captured = []
    original = ui.st.markdown
    ui.st.markdown = lambda body, **kwargs: captured.append(body)
    try:
        ui.apply_global_style()
    finally:
        ui.st.markdown = original
    assert "IBM+Plex+Sans+Arabic" in captured[0]


def test_the_old_system_stack_is_kept_behind_it():
    """This page is read on the mornings the font link fails, too."""
    for fallback in ("Segoe UI", "Tahoma", "Arial", "sans-serif"):
        assert fallback in NARRATIVE_FONT_STACK


def test_the_stack_reaches_the_prose():
    assert NARRATIVE_FONT_STACK in NARRATIVE_CSS


def test_the_reading_measure_is_a_reading_measure():
    """78ch is a measure for a table. Arabic pays more than Latin for a long
    line: no capitals to anchor a line's start, so a reader who loses the
    return sweep has less to find it again with."""
    rule = re.search(r"\.egx-narr-card p\.prose \{(.*?)\}", NARRATIVE_CSS, re.S)
    assert rule, "the prose rule is gone"
    measure = int(re.search(r"max-width:\s*(\d+)ch", rule.group(1)).group(1))
    assert 50 <= measure <= 70, f"prose measure is {measure}ch"


def test_the_prose_keeps_its_line_height():
    rule = re.search(r"\.egx-narr-card p\.prose \{(.*?)\}", NARRATIVE_CSS, re.S).group(1)
    leading = float(re.search(r"line-height:\s*([\d.]+)", rule).group(1))
    assert leading >= 1.7, "Arabic ascenders and descenders need the room"


def test_arabic_is_never_broken_mid_word():
    """`break-all` and `break-anywhere` are what once shattered Arabic into one
    glyph per line. The file says so in a comment; this keeps it true in the
    declarations, which is where it matters."""
    declarations = re.sub(r"/\*.*?\*/", "", NARRATIVE_CSS, flags=re.S)
    assert "break-all" not in declarations
    assert "break-anywhere" not in declarations
