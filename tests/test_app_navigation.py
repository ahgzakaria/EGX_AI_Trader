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


def _section_titles():
    """Section heading -> page titles, read from the ``st.navigation`` dict."""

    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "navigation" or not node.args:
            continue
        mapping = node.args[0]
        if not isinstance(mapping, ast.Dict):
            continue
        sections = {}
        for key, value in zip(mapping.keys, mapping.values):
            titles = []
            for page in getattr(value, "elts", []):
                for keyword in getattr(page, "keywords", []):
                    if keyword.arg == "title" and isinstance(keyword.value, ast.Constant):
                        titles.append(keyword.value.value)
            sections[key.value] = titles
        return sections
    return {}


def test_navigation_is_three_workspaces_plus_tools():
    """One section per way of trading, and system tools kept apart from them.

    The three are different timeframes with different economics -- a swing
    hold pays the round trip once over weeks, a scalp pays it against an
    average intraday drift ten times smaller than the cost itself. Filing them
    together invites reading a number from one as if it came from the other.
    """

    sections = _section_titles()
    headings = list(sections)

    assert len(headings) == 3, headings
    assert "SWING" in headings[0]
    assert "AI ANALYSIS" in headings[1]
    assert "SYSTEM" in headings[2]

    # AI Analysis is its own workspace, not filed with the diagnostics.
    assert sections[headings[1]] == ["AI Analysis"]
    # System holds tools only; nothing that produces a trading signal.
    assert sections[headings[2]] == ["System Health", "Settings"]


def test_there_is_no_scalping_workspace_left():
    """ORB Signals joined the earlier scalping retirements on 2026-08-25.

    Not on preference. The round trip is 1,030% of the average intraday move
    on EGX, and across 48 live signals over six sessions only 46% ever saw a
    price covering their own cost -- with a perfect exit at the day's best
    tick. Of the 13 that reached their target, 5 made money.
    """

    sections = _section_titles()
    assert not [k for k in sections if "SCALPING" in k], sections

    titles = _page_titles()
    assert "ORB Signals" not in titles


def test_the_retired_orb_page_is_still_reachable_from_system_health():
    """Retiring a route is not deleting the work: the engine, the shadow
    sessions and six sessions of evidence all remain."""

    source = Path("dashboard/system_health.py").read_text(encoding="utf-8")
    assert "dashboard.orb_signals" in source
    assert "show_orb_signals" in source


def test_every_page_is_reachable_exactly_once():
    """The whole navigation, listed. Adding a page is a decision, not a drift.

    "Confirmed Breakout" joined on 2026-08-29. It sits beside Swing Breakout
    rather than inside it because the two are close relatives with measured
    differences -- a close-position gate, a calm gate, a stop, and a price-limit
    guard -- and because it is scored through the portfolio simulator, so its
    numbers are comparable to the Daily Dashboard strategy's line for line.
    See docs/audits/strategies/CONFIRMED_VOLUME_BREAKOUT.md.
    """
    titles = _page_titles()
    assert len(titles) == len(set(titles)), f"a page is listed twice: {titles}"
    assert set(titles) == {
        "Daily Dashboard", "Swing Breakout", "Confirmed Breakout",
        "Sector Liquidity", "Watchlist", "Stock Details", "AI Analysis",
        "System Health", "Settings",
    }


def test_retired_scalping_pages_are_not_navigable():
    titles = _page_titles()
    for retired in ("Scalping Dashboard", "Active Trades", "History",
                    "ORB Signals"):
        assert retired not in titles


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
