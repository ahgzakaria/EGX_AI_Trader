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

    # There are none now, and that is the point: a column of emoji down a
    # navigation is the clearest tell that a screen is a consumer app rather
    # than an instrument. The validation stays so that if one is ever added
    # back it still has to be something Streamlit accepts.
    assert icons == [], f"navigation icons are back: {icons}"
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


def test_navigation_is_the_portfolio_two_workspaces_and_tools():
    """One section per way of trading, the account itself above them, and system
    tools kept apart from all of it.

    The trading workspaces are different timeframes with different economics --
    a swing hold pays the round trip once over weeks, a scalp pays it against an
    average intraday drift ten times smaller than the cost itself. Filing them
    together invites reading a number from one as if it came from the other.

    PORTFOLIO joined on 2026-08-31 and is deliberately not one of them. It is
    not a way of trading and not a diagnostic: it is the positions actually
    held, where the result of every other page is finally decided. It sits
    first because it is the only page with real money on it.
    """

    sections = _section_titles()
    headings = list(sections)

    assert len(headings) == 4, headings
    assert "PORTFOLIO" in headings[0]
    assert "SWING" in headings[1]
    assert "AI ANALYSIS" in headings[2]
    assert "SYSTEM" in headings[3]

    # The portfolio is one page, not a workspace that grew a second view.
    assert sections[headings[0]] == ["My Portfolio"]
    # AI Analysis is its own workspace, not filed with the diagnostics.
    assert sections[headings[2]] == ["AI Analysis"]
    # System holds tools only; nothing that produces a trading signal.
    assert sections[headings[3]] == ["System Health", "Settings"]


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

    "Breakout Watch" joined on 2026-09-10, immediately before it. It is the
    same rule one session earlier -- what is approaching the trigger, against
    what fired -- and both read `strategy_momentum_breakout/signal.measure`, so
    the two cannot describe the rule differently. It is not folded into the
    existing "Watchlist" page: that one is the user's own list of names under
    the live scanner, this one is produced by a rule and replaced each week.
    """
    titles = _page_titles()
    assert len(titles) == len(set(titles)), f"a page is listed twice: {titles}"
    assert set(titles) == {
        "My Portfolio",
        "Daily Dashboard", "Swing Breakout", "Breakout Watch",
        "Confirmed Breakout", "Sector Liquidity", "Watchlist",
        "Stock Details", "AI Analysis", "System Health", "Settings",
    }


def test_breakout_watch_is_read_before_the_page_that_confirms_it():
    """Order carries the meaning: the set-up, then the confirmation.

    Filing them the other way round would put the fired signal above the names
    approaching one, which is not how a week is watched.
    """

    sections = _section_titles()
    swing = next(v for k, v in sections.items() if "SWING" in k)
    assert "Breakout Watch" in swing and "Confirmed Breakout" in swing
    assert swing.index("Breakout Watch") + 1 == swing.index("Confirmed Breakout")


def test_the_two_watchlists_do_not_collide():
    """Two pages with 'watch' in the name must differ in every routable field."""

    import ast as _ast

    source = Path("app.py").read_text(encoding="utf-8")
    tree = _ast.parse(source)
    pages = []
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute)
                and node.func.attr == "Page"):
            entry = {}
            for keyword in node.keywords:
                if isinstance(keyword.value, _ast.Constant):
                    entry[keyword.arg] = keyword.value.value
            pages.append(entry)

    titles = [p.get("title") for p in pages]
    paths = [p.get("url_path") for p in pages if p.get("url_path")]
    icons = [p.get("icon") for p in pages if p.get("icon")]
    assert len(paths) == len(set(paths)), f"a url_path is reused: {paths}"
    assert len(icons) == len(set(icons)), f"an icon is reused: {icons}"
    assert "Breakout Watch" in titles and "Watchlist" in titles


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
