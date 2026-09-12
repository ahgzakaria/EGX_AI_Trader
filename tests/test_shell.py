"""The shell: a sidebar that always says whether anything is watching.

The health panel is the whole reason the sidebar is shaped the way it is. Every
failure this project has actually suffered was a silence, so these pin that the
panel speaks in four states rather than three, that "could not find out" is not
reported as "failed", and above all that a panel which breaks still renders --
a monitor that disappears when it breaks is indistinguishable from a healthy
morning, which is precisely the failure it exists to catch.

Presentation only.
"""

from __future__ import annotations

import re

import pytest

from dashboard import ui
from services.automation_status import (COMPLETED, FAILED, NEVER_RAN, RUNNING,
                                        UNREADABLE, AutomationStatus)


class FakeSidebar:
    """Captures what the panel writes, in place of Streamlit's sidebar."""

    def __init__(self):
        self.written = []

    def markdown(self, body, **kwargs):
        self.written.append(body)


@pytest.fixture
def sidebar(monkeypatch):
    fake = FakeSidebar()
    monkeypatch.setattr(ui.st, "sidebar", fake)
    return fake


def status(outcome, reason="", healthy=None):
    record = AutomationStatus(session_date="2026-09-12", outcome=outcome, reason=reason)
    if healthy is not None:                      # only where the test needs to force it
        object.__setattr__(record, "outcome", COMPLETED if healthy else outcome)
    return record


def render(monkeypatch, sidebar, record):
    monkeypatch.setattr("services.automation_status.read_status",
                        lambda day, **kwargs: record)
    ui.sidebar_health("2026-09-12")
    assert sidebar.written, "the panel rendered nothing at all"
    return sidebar.written[-1]


def tone_of(markup):
    return re.search(r'class="egx-health (\w+)"', markup).group(1)


# --- the four states ----------------------------------------------------------

def test_a_clean_run_reads_healthy(monkeypatch, sidebar):
    markup = render(monkeypatch, sidebar, status(COMPLETED, "224/224 synced"))
    assert tone_of(markup) == "ok"
    assert "224/224 synced" in markup


def test_a_run_that_never_happened_is_loud(monkeypatch, sidebar):
    assert tone_of(render(monkeypatch, sidebar, status(NEVER_RAN))) == "bad"


def test_a_run_in_flight_is_a_warning_not_a_failure(monkeypatch, sidebar):
    assert tone_of(render(monkeypatch, sidebar, status(RUNNING))) == "warn"


def test_a_failed_run_is_a_failure(monkeypatch, sidebar):
    assert tone_of(render(monkeypatch, sidebar, status(FAILED, "exit 137"))) == "bad"


def test_an_unreadable_status_is_unknown_and_not_a_failed_run(monkeypatch, sidebar):
    """These send the reader to different places. It read as FAILED until now:
    a corrupt status file looked exactly like a collector that died."""
    markup = render(monkeypatch, sidebar, status(UNREADABLE, "malformed json"))
    assert tone_of(markup) == "unknown"
    assert "unreadable" in markup.lower()


@pytest.mark.parametrize("outcome, tone", [
    (COMPLETED, "ok"), (RUNNING, "warn"), (NEVER_RAN, "bad"),
    (FAILED, "bad"), (UNREADABLE, "unknown"),
])
def test_every_outcome_maps_to_exactly_one_tone(monkeypatch, sidebar, outcome, tone):
    assert tone_of(render(monkeypatch, sidebar, status(outcome))) == tone


def test_the_four_tones_are_visually_distinct(monkeypatch):
    """Four rules, four appearances. Two tones that render alike are three."""
    captured = []
    monkeypatch.setattr(ui.st, "markdown", lambda body, **kw: captured.append(body))
    ui.apply_global_style()
    css = captured[0]
    seen = {}
    for tone in ("ok", "warn", "bad", "unknown"):
        rule = re.search(rf"\.egx-health\.{tone}\s*\{{(.*?)\}}", css, re.S)
        assert rule, f"no rule for the {tone} state"
        assert rule.group(1) not in seen, f"{tone} renders the same as {seen[rule.group(1)]}"
        seen[rule.group(1)] = tone


def test_unknown_wears_the_same_violet_as_every_other_unmeasured_value(monkeypatch):
    captured = []
    monkeypatch.setattr(ui.st, "markdown", lambda body, **kw: captured.append(body))
    ui.apply_global_style()
    rule = re.search(r"\.egx-health\.unknown\s*\{(.*?)\}", captured[0], re.S).group(1)
    assert "--unknown" in rule


# --- the panel that used to vanish --------------------------------------------

def test_a_panel_that_cannot_read_the_status_still_renders(monkeypatch, sidebar):
    """It used to log and return, leaving an ordinary-looking sidebar with
    nothing watching -- the exact silence the panel exists to break."""
    def explode(day, **kwargs):
        raise OSError("status directory is gone")

    monkeypatch.setattr("services.automation_status.read_status", explode)
    ui.sidebar_health("2026-09-12")
    assert sidebar.written, "the panel disappeared when it broke"
    markup = sidebar.written[-1]
    assert tone_of(markup) == "unknown"
    assert "not a healthy run" in markup


def test_an_import_failure_still_renders(monkeypatch, sidebar):
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "services.automation_status":
            raise ImportError("boom")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    ui.sidebar_health("2026-09-12")
    monkeypatch.undo()
    assert sidebar.written and tone_of(sidebar.written[-1]) == "unknown"


def test_a_sidebar_that_cannot_render_does_not_take_down_the_page(monkeypatch):
    class Broken:
        def markdown(self, *args, **kwargs):
            raise RuntimeError("no sidebar here")

    monkeypatch.setattr(ui.st, "sidebar", Broken())
    ui.sidebar_health("2026-09-12")          # must not raise


def test_the_reason_is_escaped(monkeypatch, sidebar):
    markup = render(monkeypatch, sidebar, status(FAILED, "<script>alert(1)</script>"))
    assert "<script>" not in markup


# --- the navigation -----------------------------------------------------------

@pytest.fixture(scope="module")
def css():
    captured = []
    original = ui.st.markdown
    ui.st.markdown = lambda body, **kwargs: captured.append(body)
    try:
        ui.apply_global_style()
    finally:
        ui.st.markdown = original
    return captured[0]


def test_the_current_page_is_a_rail_not_a_pill(css):
    rule = re.search(r'stSidebarNav"\] a\[aria-current="page"\]\s*\{(.*?)\}', css, re.S)
    assert rule and "border-left-color: var(--blue)" in rule.group(1)
    assert "border-radius: 999px" not in rule.group(1)


def test_the_sidebar_is_a_fixed_width_and_cannot_collapse(css):
    rule = re.search(r'\[data-testid="stSidebar"\]\s*\{(.*?)\}', css, re.S).group(1)
    assert "var(--sidebar-width)" in rule
    assert re.search(r"--sidebar-width:\s*280px", css)


def test_the_sidebar_sits_one_rung_below_the_page(css):
    """Base > sidebar > card. The sidebar was darker than the base before,
    which put it outside the surface ladder rather than on it."""
    rule = re.search(r'\[data-testid="stSidebar"\]\s*\{(.*?)\}', css, re.S).group(1)
    assert "var(--bg-2)" in rule


def test_the_health_panel_is_ordered_above_the_navigation(css):
    """It rendered at the bottom of the sidebar, below the fold.

    Streamlit lays the sidebar out as header, nav, then user content whatever
    order the page calls them in, so calling `sidebar_health()` before
    `st.navigation()` put it below the nav rather than above it -- the panel
    whose entire argument is that it cannot be missed, under nine nav items.
    """
    assert re.search(r'\[data-testid="stSidebarContent"\]\s*\{[^}]*flex', css)
    order = {}
    for box in ("stSidebarHeader", "stSidebarUserContent", "stSidebarNav"):
        rule = re.search(rf'\[data-testid="{box}"\]\s*\{{(.*?)\}}', css, re.S)
        assert rule, f"no ordering rule for {box}"
        order[box] = int(re.search(r"order\s*:\s*(\d+)", rule.group(1)).group(1))
    assert order["stSidebarUserContent"] < order["stSidebarNav"]
    assert order["stSidebarHeader"] < order["stSidebarUserContent"]
