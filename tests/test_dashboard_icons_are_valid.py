"""Icons handed to Streamlit must be emoji it accepts.

``st.info(..., icon="○")`` raises ``StreamlitAPIException`` at render time, not
at import, so an invalid icon sits in the source until the exact branch holding
it runs. On 2026-08-23 that branch was the Swing Breakout page's "nothing met
all three conditions" path — the *common* outcome, since the strategy fires
about fifty times a year — and the page crashed instead of saying so.

The rule applies only where the icon reaches Streamlit. ``page_header`` and
``empty_state`` in :mod:`dashboard.ui` take an ``icon`` too, but they escape it
into an HTML span, so the geometric marks they use throughout (``○`` for an
empty state, ``⌕`` for a search prompt) are correct there and must not be
"fixed" into emoji. A first pass at this test ignored the callee, reported
nineteen failures, and a blanket repair broke ``empty_state``'s signature while
leaving its body referencing the argument it had just removed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from streamlit.string_util import validate_icon_or_emoji

DASHBOARD = Path("dashboard")

#: Local helpers that render ``icon`` as HTML text. Streamlit never sees these.
HTML_HELPERS = frozenset({"page_header", "empty_state", "section_header"})


def _streamlit_icon_arguments():
    """Every literal ``icon=`` that reaches Streamlit, with its location.

    Parsed rather than pattern-matched, so a keyword inside a string or comment
    cannot masquerade as a real argument. A call counts as Streamlit's when it
    is an attribute call (``st.info``, ``column.metric``) and the attribute is
    not one of the local HTML helpers above.
    """
    for path in sorted(DASHBOARD.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr in HTML_HELPERS:
                continue
            for keyword in node.keywords:
                if keyword.arg != "icon":
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(
                    keyword.value.value, str
                ):
                    yield path.name, keyword.value.lineno, keyword.value.value


def test_the_scan_finds_the_icons_it_is_meant_to_guard():
    """A guard that silently matches nothing passes forever."""
    found = list(_streamlit_icon_arguments())
    assert len(found) >= 8, f"expected the alert icons across the pages, got {found}"
    assert any(module == "swing_signals.py" for module, _, _ in found)


def test_every_icon_handed_to_streamlit_is_accepted():
    rejected = []
    for module, line, icon in _streamlit_icon_arguments():
        try:
            validate_icon_or_emoji(icon)
        except Exception as error:  # noqa: BLE001 - the type is Streamlit's
            rejected.append(f"{module}:{line} icon={icon!r} — {error}")

    assert not rejected, (
        "these icons crash the page that renders them:\n  "
        + "\n  ".join(rejected)
    )


@pytest.mark.parametrize("shape", ["○", "◫", "⌕", "☆", "▲", "✓", "→"])
def test_geometric_marks_really_are_rejected(shape):
    """The guard is only worth having if these genuinely fail.

    Each reads as a clean icon in an editor and is not an emoji. If a future
    Streamlit starts accepting them this fails, which is the right moment to
    relax the rule rather than discover it by accident.
    """
    with pytest.raises(Exception):
        validate_icon_or_emoji(shape)
