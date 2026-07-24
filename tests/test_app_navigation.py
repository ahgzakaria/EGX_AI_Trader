"""Regression checks for Streamlit's native page navigation metadata."""

import ast
from pathlib import Path

from streamlit.string_util import validate_icon_or_emoji


def test_every_navigation_icon_is_accepted_by_streamlit():
    """Validate the icon literals used by each real ``st.Page`` call."""

    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    icons = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "Page":
            continue
        for keyword in node.keywords:
            if keyword.arg == "icon" and isinstance(keyword.value, ast.Constant):
                icons.append(keyword.value.value)

    assert icons, "No Streamlit page icons were discovered in app.py"
    for icon in icons:
        validate_icon_or_emoji(icon)
