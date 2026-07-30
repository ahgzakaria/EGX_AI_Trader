"""Regression checks for Streamlit's native page navigation metadata."""

import ast
from pathlib import Path

from streamlit.string_util import validate_icon_or_emoji


def _page_titles():
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    titles = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "Page":
            continue
        for keyword in node.keywords:
            if keyword.arg == "title" and isinstance(keyword.value, ast.Constant):
                titles.append(keyword.value.value)
    return titles


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


def test_primary_navigation_is_the_simplified_trader_workflow():
    assert _page_titles() == [
        "Daily Dashboard",
        "Watchlist",
        "Stock Details",
        "Scalping Dashboard",
        "Active Trades",
        "History",
        "AI Analysis",
        "System Health",
        "Settings",
    ]


def test_overlapping_scalping_pages_are_not_primary_routes():
    titles = _page_titles()
    assert "Opportunities" not in titles
    assert "Expected Range Scalper" not in titles


def test_legacy_routes_are_reachable_only_from_system_health():
    source = Path("dashboard/system_health.py").read_text(encoding="utf-8")
    assert "Legacy Research Tools" in source
    assert "Legacy / Research Only" in source
    assert "dashboard.opportunities" in source
    assert "dashboard.expected_range_scalper" in source
