"""Three different nothings, and a projection that cannot pass for a measurement.

The Sector Liquidity page had six panels that could not be computed -- no
complete session, no strength measurement, no rotation history, no forecast, no
intraday candles, no live forecast -- and every one of them was rendered with
``empty_state``, the same dashed grey box used for a search that matched
nothing. They are not the same fact. One is a gap in the record that will
close; the other is an answer.

And it showed four tables of percentages in identical dress, two of them
measured exchange turnover and two of them projections.

Presentation only.
"""

from __future__ import annotations

import inspect
import re

import pytest

from dashboard import sector_flow, ui


@pytest.fixture
def drawn(monkeypatch):
    written = []
    monkeypatch.setattr(ui.st, "markdown", lambda body, **kwargs: written.append(body))
    return written


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


# --- the three nothings are three components ----------------------------------

def test_the_three_states_are_distinct_components(drawn):
    ui.empty_state("No results", "Change the filter.")
    ui.quiet_state("Nothing fired", "That is the design.")
    ui.unavailable_state("No complete session", "Coverage check failed.")
    classes = [re.search(r'class="(egx-\w+)"', m).group(1) for m in drawn]
    assert classes == ["egx-empty", "egx-quiet", "egx-unavailable"]
    assert len(set(classes)) == 3


def test_each_state_is_drawn_differently(css):
    seen = {}
    for name in ("egx-empty", "egx-quiet", "egx-unavailable"):
        rule = re.search(rf"\.{name}\s*\{{(.*?)\}}", css, re.S)
        assert rule, f"no rule for {name}"
        assert rule.group(1) not in seen, f"{name} renders the same as {seen[rule.group(1)]}"
        seen[rule.group(1)] = name


def test_unavailable_wears_the_unknown_violet(css):
    """A panel that could not be computed is the unknown state of a whole
    panel, and wears what a single unmeasured value wears."""
    rule = re.search(r"\.egx-unavailable\s*\{(.*?)\}", css, re.S).group(1)
    assert "--unknown" in rule


def test_unavailable_says_what_would_make_it_computable(drawn):
    """A reader who cannot see a panel needs to know whether to wait, to run
    something, or to stop expecting it."""
    ui.unavailable_state("No rotation history", "Not enough sessions.",
                         needs="10 complete sessions")
    assert "10 complete sessions" in drawn[-1]
    assert 'class="need"' in drawn[-1]


def test_the_requirement_is_optional(drawn):
    ui.unavailable_state("No forecast", "Not enough sessions.")
    assert 'class="need"' not in drawn[-1]


# --- a projection is not a measurement ----------------------------------------

def test_a_projection_is_marked_before_its_table(drawn):
    ui.projection_note("Blended from yesterday and the opening window.")
    markup = drawn[-1]
    assert "egx-projection" in markup
    assert "PROJECTION" in markup


def test_the_projection_mark_is_dashed_where_measurement_is_solid(css):
    rule = re.search(r"\.egx-projection\s*\{(.*?)\}", css, re.S).group(1)
    assert "dashed" in rule
    assert "var(--blue)" in rule


@pytest.mark.parametrize("render", [
    lambda v: ui.unavailable_state(v, v, needs=v),
    lambda v: ui.projection_note(v, label=v),
    lambda v: ui.quiet_state(v, v, count=v),
])
def test_nothing_injects_markup(drawn, render):
    render("<script>alert(1)</script>")
    assert "<script>" not in drawn[-1]


# --- the page uses them -------------------------------------------------------

PANELS = ["_strength", "_rotation", "_next_session", "_intraday_section"]


@pytest.mark.parametrize("panel", PANELS)
def test_a_panel_that_cannot_be_computed_no_longer_reads_as_empty(panel):
    source = inspect.getsource(getattr(sector_flow, panel))
    assert "unavailable_state(" in source, f"{panel} still reports a gap as empty"
    assert "empty_state(" not in source


def test_the_coverage_failure_explains_why_a_partial_session_is_refused():
    source = inspect.getsource(sector_flow.show_sector_flow)
    assert "unavailable_state(" in source
    assert "excluded whole rather than shown in" in source


# `_intraday_section` has no forecast table since the Rubix minute store was retired.
@pytest.mark.parametrize("panel", ["_next_session"])
def test_every_forecast_table_is_marked_as_a_projection(panel):
    source = inspect.getsource(getattr(sector_flow, panel))
    assert "projection_note(" in source
    assert source.index("projection_note(") < source.rindex("st.dataframe(")


def test_the_measured_tables_are_not_marked_as_projections():
    """The mark has to mean something, so only the forecasts carry it."""
    for panel in ("_latest_session", "_strength", "_rotation"):
        assert "projection_note(" not in inspect.getsource(getattr(sector_flow, panel))


def test_the_page_still_reports_a_genuinely_absent_record_as_empty():
    """`empty_state` is still right for one case here: nothing was ever built."""
    source = inspect.getsource(sector_flow.show_sector_flow)
    assert "No sector history yet" in source
    assert "empty_state(" in source
